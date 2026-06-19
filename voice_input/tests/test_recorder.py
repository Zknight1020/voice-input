"""Recorder 行为单测：不开真音频设备，把入队替换为同步。"""
from __future__ import annotations

import asyncio

import pytest

from voice_input.core.recorder import Recorder


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(r, "_enqueue", lambda data: r._queue.put_nowait(data))
    return r


@pytest.mark.asyncio
async def test_chunks_yields_callback_data(rec: Recorder) -> None:
    rec._loop = asyncio.get_running_loop()
    rec._on_audio(b"\x01\x02", frames=1, time_info=None, status=None)
    rec._on_audio(b"\x03\x04", frames=1, time_info=None, status=None)
    await rec.stop()

    out = [chunk async for chunk in rec.chunks()]
    assert out == [b"\x01\x02", b"\x03\x04"]


@pytest.mark.asyncio
async def test_callback_after_stop_is_dropped(rec: Recorder) -> None:
    rec._loop = asyncio.get_running_loop()
    rec._on_audio(b"before", frames=1, time_info=None, status=None)
    await rec.stop()
    rec._on_audio(b"after", frames=1, time_info=None, status=None)

    out = [chunk async for chunk in rec.chunks()]
    assert out == [b"before"]


def test_list_input_devices_marks_default_and_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDefault:
        device = [1, 2]

    class FakeSD:
        default = FakeDefault()

        @staticmethod
        def query_devices():
            return [
                {"name": "Output Only", "max_input_channels": 0},
                {"name": "MacBook Pro麦克风", "max_input_channels": 1},
                {"name": "Speaker", "max_output_channels": 2},
                {"name": "REDMI Buds 8", "max_input_channels": 1},
            ]

    monkeypatch.setattr("voice_input.core.recorder.sd", FakeSD)
    r = Recorder(input_device=3)

    assert r.list_input_devices() == [
        {
            "id": 1,
            "name": "MacBook Pro麦克风",
            "channels": 1,
            "default": True,
            "selected": False,
        },
        {
            "id": 3,
            "name": "REDMI Buds 8",
            "channels": 1,
            "default": False,
            "selected": True,
        },
    ]


def test_set_input_device_accepts_none_or_int() -> None:
    r = Recorder(input_device=4)
    r.set_input_device(None)
    assert r.selected_input_device is None
    r.set_input_device(1)
    assert r.selected_input_device == 1


@pytest.mark.asyncio
async def test_start_passes_selected_device_to_sounddevice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = {}

    class FakeStream:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)

        def start(self) -> None:
            pass

    class FakeSD:
        RawInputStream = FakeStream

    monkeypatch.setattr("voice_input.core.recorder.sd", FakeSD)
    r = Recorder(input_device=4)

    await r.start()

    assert created["device"] == 4
