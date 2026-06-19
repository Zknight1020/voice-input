"""单进程内存 TTL 缓存：保存来自 Cursor / 外部的最新 context 文本。

设计：
- latest-wins：新推送直接覆盖旧的（不做合并）。
- TTL 过期自动失效，避免昨天的对话污染今天的识别。
- get() 不消费；同一段 context 在 TTL 内可被多次握手复用。
- asyncio 单线程使用，无锁。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ContextEntry:
    text: str
    source: str
    pushed_at: float
    ttl_s: float


class ContextCache:
    def __init__(self) -> None:
        self._entry: Optional[ContextEntry] = None

    def push(
        self,
        text: str,
        source: str,
        ttl_s: float = 60.0,
        *,
        now: Optional[float] = None,
    ) -> ContextEntry:
        ts = time.time() if now is None else now
        entry = ContextEntry(text=text, source=source, pushed_at=ts, ttl_s=ttl_s)
        self._entry = entry
        return entry

    def get(self, *, now: Optional[float] = None) -> Optional[ContextEntry]:
        ts = time.time() if now is None else now
        if self._entry is None:
            return None
        if ts - self._entry.pushed_at > self._entry.ttl_s:
            self._entry = None
            return None
        return self._entry

    def clear(self) -> None:
        self._entry = None
