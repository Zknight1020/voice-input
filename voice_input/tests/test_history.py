"""HistoryStore 单测：CRUD + 排序。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from voice_input.core.history import HistoryStore, Transcript


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


class TestInsert:
    def test_returns_transcript_with_id(self, store: HistoryStore) -> None:
        rec = store.insert("你好世界", duration_ms=1234)
        assert rec.id > 0
        assert rec.text == "你好世界"
        assert rec.duration_ms == 1234
        assert rec.created_at > 0

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        a = HistoryStore(tmp_path / "h.sqlite3")
        rec = a.insert("hello")
        b = HistoryStore(tmp_path / "h.sqlite3")
        got = b.get(rec.id)
        assert got is not None
        assert got.text == "hello"


class TestList:
    def test_orders_by_created_desc(self, store: HistoryStore) -> None:
        first = store.insert("first")
        time.sleep(0.01)
        second = store.insert("second")
        items = store.list()
        assert [i.id for i in items] == [second.id, first.id]

    def test_respects_limit_and_offset(self, store: HistoryStore) -> None:
        ids = [store.insert(f"t{i}").id for i in range(5)]
        ids.reverse()  # 最新在前
        page = store.list(limit=2, offset=1)
        assert [r.id for r in page] == ids[1:3]


class TestUpdate:
    def test_update_text_returns_new_record(self, store: HistoryStore) -> None:
        rec = store.insert("旧文本")
        updated = store.update_text(rec.id, "新文本")
        assert updated is not None
        assert updated.text == "新文本"
        assert updated.id == rec.id

    def test_update_missing_returns_none(self, store: HistoryStore) -> None:
        assert store.update_text(99999, "x") is None


class TestDelete:
    def test_delete_existing_returns_true(self, store: HistoryStore) -> None:
        rec = store.insert("doomed")
        assert store.delete(rec.id) is True
        assert store.get(rec.id) is None

    def test_delete_missing_returns_false(self, store: HistoryStore) -> None:
        assert store.delete(99999) is False


class TestClear:
    def test_clear_removes_all(self, store: HistoryStore) -> None:
        for i in range(3):
            store.insert(f"t{i}")
        store.clear()
        assert store.list() == []
