"""录音→识别→粘贴 协调状态机。

状态：
  idle       → 等待用户触发
  preparing  → 正在初始化 ASR / 麦克风输入
  recording  → 麦克风采集中，PCM 实时流给豆包
  finalizing → 已发结束帧，等豆包返回最终 definite 文本

外部接口：
  - toggle()：按一次切换 idle ↔ recording；preparing/finalizing 时忽略
  - add_listener(fn)：注册事件订阅（async function；server 用它推 WS）
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, List, Protocol


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionEvent:
    kind: str  # "state" | "partial" | "final" | "error"
    payload: dict


Listener = Callable[[SessionEvent], Awaitable[None]]


class _Recorder(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def chunks(self) -> AsyncIterator[bytes]: ...


class _ASRSession(Protocol):
    async def __aenter__(self) -> "_ASRSession": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def send_audio(self, pcm: bytes) -> None: ...
    async def finish(self) -> None: ...
    def events(self) -> AsyncIterator[Any]: ...


ASRFactory = Callable[[], _ASRSession]


class VoiceInputSession:
    def __init__(
        self,
        recorder: _Recorder,
        asr_factory: ASRFactory,
        on_final: Callable[[str, int], Awaitable[None]],
    ) -> None:
        """
        recorder      — 16k PCM 录音器（可注入 fake）
        asr_factory   — 调用即返回新 ASR 会话
        on_final      — 收到最终文本时执行的副作用（粘贴 + 入库）
                        签名：on_final(text, duration_ms)
        """
        self._recorder = recorder
        self._asr_factory = asr_factory
        self._on_final = on_final
        self._state = "idle"
        self._task: asyncio.Task | None = None
        self._listeners: List[Listener] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    async def _emit(self, event: SessionEvent) -> None:
        for fn in list(self._listeners):
            with contextlib.suppress(Exception):
                await fn(event)

    async def _set_state(self, state: str) -> None:
        self._state = state
        await self._emit(SessionEvent(kind="state", payload={"state": state}))

    async def toggle(self) -> None:
        async with self._lock:
            if self._state == "idle":
                self._task = asyncio.create_task(self._run_session())
            elif self._state == "recording":
                # 让 chunks() 退出 → _run_session 走到 finish 流程
                await self._recorder.stop()
            # finalizing：忽略

    async def _run_session(self) -> None:
        await self._set_state("preparing")
        started = time.time()
        last_final_text = ""
        try:
            async with self._asr_factory() as asr:
                await self._recorder.start()
                await self._set_state("recording")
                consumer = asyncio.create_task(self._consume_asr_events(asr))
                try:
                    async for chunk in self._recorder.chunks():
                        await asr.send_audio(chunk)
                    await self._set_state("finalizing")
                    await asr.finish()
                    last_final_text = await consumer
                finally:
                    if not consumer.done():
                        consumer.cancel()
                        with contextlib.suppress(Exception):
                            await consumer
        except Exception as e:  # 上游断开 / 网络问题
            await self._emit(SessionEvent(kind="error", payload={"message": str(e)}))
        else:
            duration_ms = int((time.time() - started) * 1000)
            if last_final_text:
                try:
                    await self._on_final(last_final_text, duration_ms)
                except Exception as e:
                    log.error("on_final 失败: %s", e, exc_info=True)
        finally:
            await self._set_state("idle")

    async def _consume_asr_events(self, asr: _ASRSession) -> str:
        """消费 ASR 流，广播累积文本，返回最终入库文本。

        豆包流式 ASR 在长停顿时把已识别的句子标 definite=true 并开启新 utterance；
        切分帧之后已 definite 的 utterance 不会再随后续帧重发。所以必须以 utterance
        粒度累积，用 start_time 作为稳定标识。

        策略：
        - finalized[start_time] = text  收集所有 definite=True 的 utterance
        - current_partial 取这一帧最后一个 definite=False utterance 的 text
        - 每帧广播 sorted(finalized) 拼接 + current_partial

        "final" 事件只在整段会话结束（events() 流终止）发一次。
        """
        finalized: dict[int, str] = {}
        current_partial = ""
        last_emitted = ""

        async for evt in asr.events():
            for u in evt.utterances:
                if u.definite:
                    finalized[u.start_time] = u.text
            non_definite = [u for u in evt.utterances if not u.definite]
            current_partial = non_definite[-1].text if non_definite else ""

            full = "".join(t for _, t in sorted(finalized.items())) + current_partial
            if full and full != last_emitted:
                last_emitted = full
                await self._emit(
                    SessionEvent(kind="partial", payload={"text": full})
                )

        full_text = "".join(t for _, t in sorted(finalized.items())) + current_partial
        if full_text:
            await self._emit(
                SessionEvent(kind="final", payload={"text": full_text})
            )
        return full_text
