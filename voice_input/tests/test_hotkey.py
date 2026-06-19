"""HotkeyListener 单测：直接触发 _on_trigger，断言 callback 被调度。"""
from __future__ import annotations

import asyncio

import pytest

from voice_input.core.hotkey import HotkeyListener


@pytest.mark.asyncio
async def test_trigger_dispatches_callback_to_loop() -> None:
    fired = asyncio.Event()

    async def cb() -> None:
        fired.set()

    listener = HotkeyListener("<f2>", cb, loop=asyncio.get_running_loop())
    # 不调 start()（会绑定真实键盘），直接走触发路径
    listener._on_trigger()

    await asyncio.wait_for(fired.wait(), timeout=1.0)
