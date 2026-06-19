"""从语音输入历史生成下一次 ASR 可用的动态上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from voice_input.core.context_cache import ContextCache, ContextEntry
from voice_input.core.history import HistoryStore


VOICE_HISTORY_SOURCE = "voice_history"


@dataclass
class HistoryContextSettings:
    enabled: bool = True
    max_turns: int = 5
    max_chars: int = 1500
    ttl_s: float = 900.0


class HistoryContextManager:
    def __init__(
        self,
        history: HistoryStore,
        cache: ContextCache,
        settings: Optional[HistoryContextSettings] = None,
    ) -> None:
        self._history = history
        self._cache = cache
        self._settings = settings or HistoryContextSettings()

    @property
    def settings(self) -> HistoryContextSettings:
        return self._settings

    def set_enabled(self, enabled: bool) -> None:
        self._settings.enabled = enabled
        if not enabled:
            self._clear_voice_history_context()

    def refresh(self) -> Optional[ContextEntry]:
        """把最近历史写入 context cache。

        HistoryStore.list() 返回从新到旧；context_builder 会把动态文本按行倒序，
        所以这里先恢复成从旧到新的行序，最终喂给豆包时仍是从新到旧。
        """
        if not self._settings.enabled:
            self._clear_voice_history_context()
            return None

        records = self._history.list(limit=self._settings.max_turns)
        lines = [r.text.strip() for r in reversed(records) if r.text.strip()]
        if not lines:
            self._clear_voice_history_context()
            return None

        text = "\n".join(lines)
        if len(text) > self._settings.max_chars:
            text = "…" + text[-(self._settings.max_chars - 1):]

        return self._cache.push(
            text,
            source=VOICE_HISTORY_SOURCE,
            ttl_s=self._settings.ttl_s,
        )

    def to_dict(self) -> dict:
        s = self._settings
        return {
            "enabled": s.enabled,
            "max_turns": s.max_turns,
            "max_chars": s.max_chars,
            "ttl_s": s.ttl_s,
        }

    def _clear_voice_history_context(self) -> None:
        entry = self._cache.get()
        if entry is not None and entry.source == VOICE_HISTORY_SOURCE:
            self._cache.clear()
