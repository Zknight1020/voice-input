"""全局热键监听（pynput）。

按一下 F2（或配置的热键）→ 触发 callback。callback 在 pynput 的
监听线程被调用，需要把消息转发回 asyncio loop。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable, Optional


AsyncCallback = Callable[[], Awaitable[None]]


class HotkeyListener:
    """注册全局热键，触发时把 async callback 投递到指定 loop。"""

    def __init__(
        self,
        hotkey: str,
        callback: AsyncCallback,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._hotkey = hotkey
        self._callback = callback
        self._loop = loop
        self._listener = None

    def start(self) -> None:
        from pynput.keyboard import GlobalHotKeys

        self._listener = GlobalHotKeys({self._hotkey: self._on_trigger})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_trigger(self) -> None:
        """pynput 监听线程回调。安全派发到 asyncio loop。"""
        asyncio.run_coroutine_threadsafe(self._callback(), self._loop)
