"""历史记录持久化（SQLite）。

线程安全：sqlite3 默认在多线程使用同一连接是不安全的，因此每次调用
都新建短连接。读写量很低（每次录音一条），不会成为瓶颈。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    text        TEXT    NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transcripts_created_at
    ON transcripts (created_at DESC);
"""


@dataclass(frozen=True)
class Transcript:
    id: int
    created_at: float
    text: str
    duration_ms: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "text": self.text,
            "duration_ms": self.duration_ms,
        }


class HistoryStore:
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def insert(self, text: str, duration_ms: int = 0) -> Transcript:
        created = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO transcripts (created_at, text, duration_ms) VALUES (?, ?, ?)",
                (created, text, duration_ms),
            )
            new_id = cur.lastrowid
            conn.commit()
        assert new_id is not None
        return Transcript(id=new_id, created_at=created, text=text, duration_ms=duration_ms)

    def list(self, limit: int = 100, offset: int = 0) -> List[Transcript]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, text, duration_ms FROM transcripts "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [Transcript(**dict(r)) for r in rows]

    def get(self, item_id: int) -> Optional[Transcript]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, created_at, text, duration_ms FROM transcripts WHERE id = ?",
                (item_id,),
            ).fetchone()
        return Transcript(**dict(row)) if row else None

    def update_text(self, item_id: int, text: str) -> Optional[Transcript]:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE transcripts SET text = ? WHERE id = ?",
                (text, item_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
        return self.get(item_id)

    def delete(self, item_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM transcripts WHERE id = ?", (item_id,))
            conn.commit()
        return cur.rowcount > 0

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM transcripts")
            conn.commit()
