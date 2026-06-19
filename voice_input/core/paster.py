"""跨平台剪贴板写入 + 模拟粘贴。

策略：
1. 把文本写到系统剪贴板（pyperclip）
2. 模拟 Ctrl+V (Win/Linux) 或 ⌘+V (macOS)
"""
from __future__ import annotations

import sys
import time
from typing import Optional, Protocol

import pyperclip


class _KeyboardController(Protocol):
    def pressed(self, *args): ...
    def press(self, key) -> None: ...
    def release(self, key) -> None: ...


def _build_keyboard():
    from pynput.keyboard import Controller, Key

    return Controller(), Key


def copy_to_clipboard(text: str) -> None:
    """无副作用：仅写剪贴板。"""
    pyperclip.copy(text)


def paste_to_focused(
    text: str,
    *,
    auto_paste: bool = True,
    delay_before_paste: float = 0.05,
    keyboard_factory=_build_keyboard,
) -> None:
    """写剪贴板，可选模拟粘贴到当前焦点窗口。

    keyboard_factory 仅用于测试注入，默认用 pynput。
    """
    copy_to_clipboard(text)
    if not auto_paste:
        return

    controller, Key = keyboard_factory()
    time.sleep(delay_before_paste)
    modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
    with controller.pressed(modifier):
        controller.press("v")
        controller.release("v")
