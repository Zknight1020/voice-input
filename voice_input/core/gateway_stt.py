"""OpenAI-compatible audio transcription client for voice_input.

This provider is intentionally non-streaming: the session buffers PCM chunks
locally, sends one WAV file to /audio/transcriptions on finish(), then emits a
single final transcript event. It fits the browser-button release mode well.
"""
from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import dataclass
from typing import Optional

import httpx

from voice_input.core.doubao_asr import TranscriptEvent, Utterance


@dataclass(frozen=True)
class GatewaySTTConfig:
    base_url: str
    api_key: str
    model: str = "whisper-1"
    language: str = "zh"
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    prompt: Optional[str] = None
    timeout_s: float = 60.0


class GatewayTranscriptionSession:
    """One-shot /audio/transcriptions adapter matching VoiceInputSession ASR API."""

    _SENTINEL = object()

    def __init__(self, config: GatewaySTTConfig) -> None:
        self._config = config
        self._chunks: list[bytes] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "GatewayTranscriptionSession":
        if not self._config.api_key.strip():
            raise RuntimeError("缺少 AI_API_KEY / OPENAI_API_KEY，无法调用语音识别网关")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._queue.put(self._SENTINEL)

    async def send_audio(self, pcm: bytes) -> None:
        if pcm:
            self._chunks.append(pcm)

    async def finish(self) -> None:
        wav_bytes = _pcm_to_wav(
            b"".join(self._chunks),
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            sample_width=self._config.sample_width,
        )
        text = await self._transcribe(wav_bytes)
        if text:
            await self._queue.put(
                TranscriptEvent(
                    utterances=(Utterance(text=text, definite=True, start_time=0),)
                )
            )
        await self._queue.put(self._SENTINEL)

    async def events(self):
        while True:
            item = await self._queue.get()
            if item is self._SENTINEL:
                return
            yield item

    async def _transcribe(self, wav_bytes: bytes) -> str:
        data = {
            "model": self._config.model,
            "language": self._config.language,
            "response_format": "json",
        }
        if self._config.prompt:
            data["prompt"] = self._config.prompt

        async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
            resp = await client.post(
                f"{self._config.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                files={"file": ("voice-input.wav", wav_bytes, "audio/wav")},
                data=data,
            )
            resp.raise_for_status()

        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            payload = resp.json()
            if isinstance(payload, dict):
                return str(payload.get("text", "")).strip()
        return resp.text.strip()


def _pcm_to_wav(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()
