"""VoiceInputSession 状态机集成测试。

用 fake recorder + fake ASR 完整跑一遍 toggle → recording → finalizing → idle。
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from voice_input.core.doubao_asr import TranscriptEvent, Utterance
from voice_input.core.session import SessionEvent, VoiceInputSession


def _frame(*pairs: tuple[str, bool, int]) -> TranscriptEvent:
    """便捷构造：[(text, definite, start_time), ...]。"""
    utts = tuple(Utterance(text=t, definite=d, start_time=s) for t, d, s in pairs)
    return TranscriptEvent(utterances=utts)


class FakeRecorder:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.started = False
        self.sent_chunks: list[bytes] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        await self._queue.put(b"")  # sentinel

    async def chunks(self):
        while True:
            chunk = await self._queue.get()
            if chunk == b"":
                return
            yield chunk

    async def push(self, data: bytes) -> None:
        await self._queue.put(data)


class FakeASRSession:
    def __init__(self) -> None:
        self.audio_in: list[bytes] = []
        self.finished = False
        self._events: asyncio.Queue = asyncio.Queue()
        self._finish_emit: list[TranscriptEvent] = [_frame(("你好世界", True, 100))]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_in.append(pcm)

    async def finish(self) -> None:
        self.finished = True
        for evt in self._finish_emit:
            await self._events.put(evt)
        await self._events.put(None)

    async def emit(self, evt: TranscriptEvent) -> None:
        await self._events.put(evt)

    async def events(self):
        while True:
            evt = await self._events.get()
            if evt is None:
                return
            yield evt


@pytest.mark.asyncio
async def test_toggle_runs_full_lifecycle() -> None:
    rec = FakeRecorder()
    asr = FakeASRSession()
    finals: list[tuple[str, int]] = []

    async def on_final(text: str, duration_ms: int) -> None:
        finals.append((text, duration_ms))

    sess = VoiceInputSession(rec, lambda: asr, on_final)

    states: List[str] = []

    async def listener(evt: SessionEvent) -> None:
        if evt.kind == "state":
            states.append(evt.payload["state"])

    sess.add_listener(listener)

    await sess.toggle()
    await asyncio.sleep(0.01)
    assert sess.state == "recording"
    assert rec.started

    await rec.push(b"audio1")
    await rec.push(b"audio2")
    await asyncio.sleep(0.01)
    assert asr.audio_in == [b"audio1", b"audio2"]

    await asr.emit(_frame(("你好", False, 100)))
    await asyncio.sleep(0.01)

    await sess.toggle()
    await asyncio.wait_for(sess._task, timeout=1.0)

    assert asr.finished
    assert sess.state == "idle"
    assert states == ["preparing", "recording", "finalizing", "idle"]
    assert finals == [("你好世界", finals[0][1])]


@pytest.mark.asyncio
async def test_listener_receives_partial_and_final() -> None:
    rec = FakeRecorder()
    asr = FakeASRSession()

    async def on_final(text: str, duration_ms: int) -> None:
        pass

    sess = VoiceInputSession(rec, lambda: asr, on_final)

    received: list[SessionEvent] = []

    async def listener(evt: SessionEvent) -> None:
        received.append(evt)

    sess.add_listener(listener)

    await sess.toggle()
    await asyncio.sleep(0.01)
    await asr.emit(_frame(("你", False, 100)))
    await asr.emit(_frame(("你好", False, 100)))
    await asyncio.sleep(0.01)
    await sess.toggle()
    await asyncio.wait_for(sess._task, timeout=1.0)

    kinds = [e.kind for e in received]
    assert "partial" in kinds
    assert "final" in kinds
    final_event = next(e for e in received if e.kind == "final")
    assert final_event.payload["text"] == "你好世界"


@pytest.mark.asyncio
async def test_pause_in_middle_does_not_drop_earlier_text() -> None:
    """录音中停顿，豆包发 (definite_utt, empty_partial_placeholder) 切分帧后开新句。

    回归 bug：原解析取 utterances[-1] 是空 placeholder（definite=False），导致前面
    已 definite 的句子永远不入 finalized，被新句覆盖。

    新逻辑用 start_time 作 utterance 唯一标识，按 utterance 粒度累积，必须做到累积
    文本长度全程单调不缩水。
    """

    class PauseASR(FakeASRSession):
        async def finish(self) -> None:
            self.finished = True
            await self._events.put(_frame(("我们去吃饭吧", True, 5000)))
            await self._events.put(None)

    rec = FakeRecorder()
    asr = PauseASR()
    finals: list[tuple[str, int]] = []

    async def on_final(text: str, duration_ms: int) -> None:
        finals.append((text, duration_ms))

    sess = VoiceInputSession(rec, lambda: asr, on_final)

    texts: list[str] = []

    async def listener(evt: SessionEvent) -> None:
        if evt.kind in ("partial", "final"):
            texts.append(evt.payload["text"])

    sess.add_listener(listener)

    await sess.toggle()
    await asyncio.sleep(0.01)

    # 第一句：partial 累积
    await asr.emit(_frame(("今", False, 1000)))
    await asr.emit(_frame(("今天", False, 1000)))
    await asr.emit(_frame(("今天天气", False, 1000)))
    await asr.emit(_frame(("今天天气真好", False, 1000)))
    # 切分帧：第一句 definite=True，新一句空 partial placeholder（被解析层过滤）
    await asr.emit(_frame(("今天天气真好", True, 1000)))
    # 第二句开始（新 start_time）
    await asr.emit(_frame(("我们", False, 5000)))
    await asr.emit(_frame(("我们去吃饭", False, 5000)))
    await asyncio.sleep(0.01)

    await sess.toggle()
    await asyncio.wait_for(sess._task, timeout=1.0)

    lengths = [len(t) for t in texts]
    assert lengths == sorted(lengths), f"文本长度倒退: {texts}"

    assert finals == [("今天天气真好我们去吃饭吧", finals[0][1])]
    assert texts[-1] == "今天天气真好我们去吃饭吧"


@pytest.mark.asyncio
async def test_falls_back_to_last_partial_when_no_definite() -> None:
    """ASR 全程没给 definite=True；应入库最后一次 partial 文本。"""

    class PartialOnlyASR(FakeASRSession):
        async def finish(self) -> None:
            self.finished = True
            await self._events.put(_frame(("最终的 partial", False, 1000)))
            await self._events.put(None)

    rec = FakeRecorder()
    asr = PartialOnlyASR()
    finals: list[tuple[str, int]] = []

    async def on_final(text: str, duration_ms: int) -> None:
        finals.append((text, duration_ms))

    sess = VoiceInputSession(rec, lambda: asr, on_final)

    received: list[SessionEvent] = []

    async def listener(evt: SessionEvent) -> None:
        received.append(evt)

    sess.add_listener(listener)

    await sess.toggle()
    await asyncio.sleep(0.01)
    await asr.emit(_frame(("中间", False, 1000)))
    await asyncio.sleep(0.01)
    await sess.toggle()
    await asyncio.wait_for(sess._task, timeout=1.0)

    assert len(finals) == 1
    assert finals[0][0] == "最终的 partial"
    final_events = [e for e in received if e.kind == "final"]
    assert final_events
    assert final_events[-1].payload["text"] == "最终的 partial"


@pytest.mark.asyncio
async def test_on_final_exception_is_logged_not_swallowed(caplog) -> None:
    rec = FakeRecorder()
    asr = FakeASRSession()

    async def boom(text: str, duration_ms: int) -> None:
        raise RuntimeError("disk full")

    sess = VoiceInputSession(rec, lambda: asr, boom)

    await sess.toggle()
    await asyncio.sleep(0.01)
    await sess.toggle()
    with caplog.at_level("ERROR", logger="voice_input.core.session"):
        await asyncio.wait_for(sess._task, timeout=1.0)

    assert sess.state == "idle"
    assert any("on_final 失败" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_toggle_during_finalizing_is_noop() -> None:
    rec = FakeRecorder()
    asr = FakeASRSession()

    async def on_final(text: str, duration_ms: int) -> None:
        pass

    sess = VoiceInputSession(rec, lambda: asr, on_final)

    sess._state = "finalizing"
    await sess.toggle()
    assert sess._task is None


@pytest.mark.asyncio
async def test_emits_preparing_before_recorder_start() -> None:
    rec = FakeRecorder()
    asr = FakeASRSession()

    async def on_final(text: str, duration_ms: int) -> None:
        pass

    sess = VoiceInputSession(rec, lambda: asr, on_final)
    states: list[str] = []

    async def listener(evt: SessionEvent) -> None:
        if evt.kind == "state":
            states.append(evt.payload["state"])

    sess.add_listener(listener)

    await sess.toggle()
    await asyncio.sleep(0.01)

    assert states[:2] == ["preparing", "recording"]

    await sess.toggle()
    await asyncio.wait_for(sess._task, timeout=1.0)
