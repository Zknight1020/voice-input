"""豆包流式 ASR 客户端。

帧结构来自 demo_server/asr_bridge.py（已联调通过）。
将协议层（纯函数）和会话层（WebSocket）拆开，方便单测。
"""
from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import os
import struct
import uuid
from dataclasses import dataclass
from typing import Optional

from websockets.asyncio.client import connect as ws_connect


log = logging.getLogger(__name__)
_DEBUG_FRAMES = os.getenv("VOICE_INPUT_DEBUG_FRAMES") == "1"


# ── 协议层（纯函数，可单测） ──────────────────────────────────────


def build_full_client_request(payload_obj: dict) -> bytes:
    """握手帧：full client request + JSON + gzip。"""
    payload = gzip.compress(json.dumps(payload_obj, ensure_ascii=False).encode("utf-8"))
    header = bytes([0x11, 0x10, 0x11, 0x00])
    return header + struct.pack(">I", len(payload)) + payload


def build_audio_only_request(audio_bytes: bytes, is_last: bool = False) -> bytes:
    """音频帧。is_last=True 表示最后一帧。"""
    gz = gzip.compress(audio_bytes)
    flags = 0x02 if is_last else 0x00
    header = bytes([0x11, 0x20 | flags, 0x01, 0x00])
    return header + struct.pack(">I", len(gz)) + gz


@dataclass(frozen=True)
class Utterance:
    """豆包帧里的单个 utterance。

    start_time 用 ms，作为同一句话跨帧的稳定标识（同一 utterance 多帧
    更新时 start_time 不变；新一句话有新的 start_time）。
    """

    text: str
    definite: bool
    start_time: int


@dataclass(frozen=True)
class TranscriptEvent:
    """一帧豆包响应解析后的快照（含所有 utterances）。"""

    utterances: tuple[Utterance, ...]


def parse_server_frame(frame: bytes) -> Optional[TranscriptEvent]:
    """解析豆包响应帧，返回识别快照；心跳/无内容返回 None。"""
    if len(frame) < 8:
        return None

    b1, b2 = frame[1], frame[2]
    flags = b1 & 0x0F
    ser = (b2 & 0xF0) >> 4
    comp = b2 & 0x0F

    idx = 4
    if flags in (0x1, 0x3):
        idx += 4  # skip sequence number

    if len(frame) < idx + 4:
        return None

    psize = struct.unpack(">I", frame[idx : idx + 4])[0]
    idx += 4
    payload = frame[idx : idx + psize]

    if comp == 1:
        with contextlib.suppress(Exception):
            payload = gzip.decompress(payload)

    if ser != 1:
        return None

    try:
        obj = json.loads(payload.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    if _DEBUG_FRAMES:
        log.info("DOUBAO_FRAME %s", json.dumps(obj, ensure_ascii=False))

    result = obj.get("result", {})
    if not isinstance(result, dict):
        return None

    raw_utterances = result.get("utterances", []) or []
    parsed: list[Utterance] = []
    if isinstance(raw_utterances, list):
        for u in raw_utterances:
            if not isinstance(u, dict):
                continue
            text = u.get("text", "") or ""
            definite = bool(u.get("definite", False))
            if not text and not definite:
                # 空 partial placeholder（停顿后切分时豆包会塞这种）
                continue
            try:
                start_time = int(u.get("start_time", 0))
            except (TypeError, ValueError):
                start_time = 0
            parsed.append(Utterance(text=text, definite=definite, start_time=start_time))

    if not parsed:
        # 兜底：utterances 为空但 result.text 有内容
        fallback_text = result.get("text", "") or ""
        if not fallback_text:
            return None
        parsed.append(Utterance(text=fallback_text, definite=False, start_time=0))

    return TranscriptEvent(utterances=tuple(parsed))


# ── 会话层 ───────────────────────────────────────────────────────


def _handshake_payload(extra_request: Optional[dict] = None) -> dict:
    """构建握手 payload。

    extra_request 会浅合并进 request 子对象，用来塞 context、hot_words 等
    可选字段。字段名按豆包 v3 sauc bigmodel 文档配——具体字段名运行时由
    上层决定（例如 {"context": "前文..."}），这里不写死。
    """
    request: dict = {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        "show_utterances": True,
        "result_type": "single",
    }
    if extra_request:
        request.update(extra_request)
    return {
        "user": {"uid": "voice-input"},
        "audio": {
            "format": "pcm",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "language": "zh-CN",
        },
        "request": request,
    }


class DoubaoASRSession:
    """单次识别会话。

    extra_request: 握手时合并到 request 子对象的额外字段（context / hot_words 等）。

    使用方式::

        async with DoubaoASRSession(..., extra_request={"context": "前文..."}) as sess:
            await sess.send_audio(pcm_chunk)
            ...
            await sess.finish()
            async for evt in sess.events():
                ...
    """

    _SENTINEL: object = object()

    def __init__(
        self,
        app_id: str,
        access_key: str,
        resource_id: str,
        url: str,
        extra_request: Optional[dict] = None,
    ) -> None:
        self._app_id = app_id
        self._access_key = access_key
        self._resource_id = resource_id
        self._url = url
        self._extra_request = extra_request
        self._ws = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "DoubaoASRSession":
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self._ws = await ws_connect(
            self._url,
            additional_headers=headers,
            max_size=10_000_000,
        )
        await self._ws.send(
            build_full_client_request(_handshake_payload(self._extra_request))
        )
        if _DEBUG_FRAMES and self._extra_request:
            log.info(
                "DOUBAO_HANDSHAKE_EXTRA %s",
                json.dumps(self._extra_request, ensure_ascii=False),
            )
        self._reader_task = asyncio.create_task(self._reader_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(Exception):
                await self._reader_task
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if isinstance(msg, bytes):
                    if _DEBUG_FRAMES:
                        b1 = msg[1] if len(msg) >= 2 else 0
                        msg_type = (b1 & 0xF0) >> 4
                        log.info(
                            "DOUBAO_RAW msg_type=0x%X len=%d head=%s",
                            msg_type,
                            len(msg),
                            msg[:8].hex(),
                        )
                    evt = parse_server_frame(msg)
                    if evt is not None:
                        await self._queue.put(evt)
        finally:
            await self._queue.put(self._SENTINEL)

    async def send_audio(self, pcm: bytes) -> None:
        assert self._ws is not None
        await self._ws.send(build_audio_only_request(pcm, is_last=False))

    async def finish(self) -> None:
        """发送终止帧，提示豆包返回 definite 终态。"""
        assert self._ws is not None
        await self._ws.send(build_audio_only_request(b"", is_last=True))

    async def events(self):
        """异步迭代识别事件，直到上游关闭。"""
        while True:
            item = await self._queue.get()
            if item is self._SENTINEL:
                return
            yield item
