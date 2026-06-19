from __future__ import annotations

import wave
from io import BytesIO

import pytest

from voice_input.core.doubao_asr import TranscriptEvent, Utterance
from voice_input.core.gateway_stt import (
    GatewaySTTConfig,
    GatewayTranscriptionSession,
    _pcm_to_wav,
)


def test_pcm_to_wav_wraps_pcm_bytes() -> None:
    wav = _pcm_to_wav(
        b"\x01\x00\x02\x00",
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    with wave.open(BytesIO(wav), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.readframes(2) == b"\x01\x00\x02\x00"


@pytest.mark.asyncio
async def test_gateway_session_buffers_audio_and_emits_final(monkeypatch) -> None:
    captured = {}

    async def fake_transcribe(self, wav_bytes: bytes) -> str:
        captured["wav"] = wav_bytes
        return "你好乔博"

    monkeypatch.setattr(GatewayTranscriptionSession, "_transcribe", fake_transcribe)

    sess = GatewayTranscriptionSession(
        GatewaySTTConfig(
            base_url="https://staging.song-ai-api.com/v1",
            api_key="test-key",
        )
    )
    async with sess:
        await sess.send_audio(b"\x01\x00")
        await sess.send_audio(b"\x02\x00")
        await sess.finish()
        events = [evt async for evt in sess.events()]

    assert captured["wav"].startswith(b"RIFF")
    assert events == [
        TranscriptEvent(
            utterances=(Utterance(text="你好乔博", definite=True, start_time=0),)
        )
    ]


@pytest.mark.asyncio
async def test_gateway_session_requires_api_key() -> None:
    sess = GatewayTranscriptionSession(
        GatewaySTTConfig(base_url="https://staging.song-ai-api.com/v1", api_key="")
    )
    with pytest.raises(RuntimeError, match="AI_API_KEY"):
        async with sess:
            pass
