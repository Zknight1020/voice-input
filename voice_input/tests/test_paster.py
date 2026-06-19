"""Paster 单测：注入 fake 键盘，断言剪贴板和按键序列。"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from voice_input.core import paster


class FakeController:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    @contextmanager
    def pressed(self, modifier):
        self.events.append(("hold", str(modifier)))
        try:
            yield
        finally:
            self.events.append(("release_modifier", str(modifier)))

    def press(self, key) -> None:
        self.events.append(("press", str(key)))

    def release(self, key) -> None:
        self.events.append(("release", str(key)))


class FakeKey:
    cmd = "Key.cmd"
    ctrl = "Key.ctrl"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paster.time, "sleep", lambda _s: None)


@pytest.fixture
def fake_keyboard():
    fake = FakeController()
    return fake, lambda: (fake, FakeKey)


@pytest.fixture(autouse=True)
def _stub_clipboard(monkeypatch: pytest.MonkeyPatch):
    fake = MagicMock()
    monkeypatch.setattr(paster.pyperclip, "copy", fake)
    return fake


def test_copy_only_writes_clipboard(_stub_clipboard, fake_keyboard) -> None:
    fake, factory = fake_keyboard
    paster.paste_to_focused("hello", auto_paste=False, keyboard_factory=factory)
    _stub_clipboard.assert_called_once_with("hello")
    assert fake.events == []


def test_auto_paste_uses_cmd_on_mac(_stub_clipboard, fake_keyboard, monkeypatch) -> None:
    monkeypatch.setattr(paster.sys, "platform", "darwin")
    fake, factory = fake_keyboard
    paster.paste_to_focused("hi", auto_paste=True, keyboard_factory=factory)
    assert fake.events[0] == ("hold", "Key.cmd")
    assert ("press", "v") in fake.events


def test_auto_paste_uses_ctrl_on_windows(_stub_clipboard, fake_keyboard, monkeypatch) -> None:
    monkeypatch.setattr(paster.sys, "platform", "win32")
    fake, factory = fake_keyboard
    paster.paste_to_focused("hi", auto_paste=True, keyboard_factory=factory)
    assert fake.events[0] == ("hold", "Key.ctrl")


def test_copy_to_clipboard_writes_clipboard(_stub_clipboard) -> None:
    paster.copy_to_clipboard("世界")
    _stub_clipboard.assert_called_once_with("世界")
