"""Server REST/WS 集成测试（用 fake session）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import ClientSession, WSMsgType

from voice_input.core.context_cache import ContextCache
from voice_input.core.history import HistoryStore
from voice_input.core.history_context import HistoryContextManager, HistoryContextSettings
from voice_input.core.session import SessionEvent
from voice_input.server import build_app


class FakeSession:
    def __init__(self) -> None:
        self.state = "idle"
        self._listeners = []
        self.toggled = 0

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    async def toggle(self) -> None:
        self.toggled += 1

    async def emit(self, evt: SessionEvent) -> None:
        for fn in self._listeners:
            await fn(evt)


class FakeRecorder:
    def __init__(self) -> None:
        self.selected_input_device = None
        self.set_calls = []

    def list_input_devices(self):
        selected = self.selected_input_device
        return [
            {
                "id": 1,
                "name": "MacBook Pro麦克风",
                "channels": 1,
                "default": True,
                "selected": selected is None or selected == 1,
            },
            {
                "id": 4,
                "name": "REDMI Buds 8",
                "channels": 1,
                "default": False,
                "selected": selected == 4,
            },
        ]

    def set_input_device(self, device_id):
        self.set_calls.append(device_id)
        self.selected_input_device = device_id


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    d = tmp_path / "web"
    d.mkdir()
    (d / "index.html").write_text("<html><body>OK</body></html>")
    return d


@pytest.fixture
def history(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.sqlite3")


@pytest.fixture
async def client(aiohttp_client, history: HistoryStore, web_dir: Path):
    session = FakeSession()
    recorder = FakeRecorder()
    context_cache = ContextCache()
    history_context = HistoryContextManager(
        history,
        context_cache,
        HistoryContextSettings(max_turns=3, max_chars=80, ttl_s=120),
    )
    app = build_app(
        session,
        history,
        web_dir,
        context_cache=context_cache,
        history_context=history_context,
        recorder=recorder,
    )
    app["fake_session"] = session
    app["fake_recorder"] = recorder
    app["fake_context_cache"] = context_cache
    app["fake_history_context"] = history_context
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_index_served(client) -> None:
    resp = await client.get("/")
    assert resp.status == 200
    body = await resp.text()
    assert "OK" in body


@pytest.mark.asyncio
async def test_history_list_returns_records(client, history: HistoryStore) -> None:
    history.insert("第一条")
    history.insert("第二条")
    resp = await client.get("/api/history")
    assert resp.status == 200
    items = await resp.json()
    assert [i["text"] for i in items] == ["第二条", "第一条"]


@pytest.mark.asyncio
async def test_history_update_text(client, history: HistoryStore) -> None:
    rec = history.insert("旧")
    resp = await client.patch(f"/api/history/{rec.id}", json={"text": "新"})
    assert resp.status == 200
    data = await resp.json()
    assert data["text"] == "新"
    entry = client.server.app["fake_context_cache"].get()
    assert entry is not None
    assert entry.source == "voice_history"
    assert entry.text == "新"


@pytest.mark.asyncio
async def test_history_update_404(client) -> None:
    resp = await client.patch("/api/history/9999", json={"text": "x"})
    assert resp.status == 404


@pytest.mark.asyncio
async def test_history_delete(client, history: HistoryStore) -> None:
    rec = history.insert("待删")
    resp = await client.delete(f"/api/history/{rec.id}")
    assert resp.status == 200
    assert history.get(rec.id) is None
    assert client.server.app["fake_context_cache"].get() is None


@pytest.mark.asyncio
async def test_copy_endpoint_writes_clipboard(client) -> None:
    with patch("voice_input.server.copy_to_clipboard") as mock_copy:
        resp = await client.post("/api/copy", json={"text": "你好"})
        assert resp.status == 200
        mock_copy.assert_called_once_with("你好")


@pytest.mark.asyncio
async def test_copy_rejects_empty(client) -> None:
    resp = await client.post("/api/copy", json={"text": ""})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_audio_input_devices_returns_inputs(client) -> None:
    resp = await client.get("/api/audio/input-devices")
    assert resp.status == 200
    data = await resp.json()
    assert data["selected"] is None
    assert [d["name"] for d in data["devices"]] == [
        "MacBook Pro麦克风",
        "REDMI Buds 8",
    ]
    assert data["devices"][0]["default"] is True
    assert data["devices"][1]["selected"] is False


@pytest.mark.asyncio
async def test_audio_input_device_can_be_selected(client) -> None:
    resp = await client.post("/api/audio/input-device", json={"device_id": 4})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["selected"] == 4
    assert client.server.app["fake_recorder"].set_calls == [4]


@pytest.mark.asyncio
async def test_audio_input_device_can_reset_to_default(client) -> None:
    await client.post("/api/audio/input-device", json={"device_id": 4})
    resp = await client.post("/api/audio/input-device", json={"device_id": None})
    assert resp.status == 200
    assert (await resp.json())["selected"] is None
    assert client.server.app["fake_recorder"].set_calls == [4, None]


@pytest.mark.asyncio
async def test_audio_input_device_rejects_bad_id(client) -> None:
    resp = await client.post("/api/audio/input-device", json={"device_id": "bad"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_ws_receives_state_on_connect(client) -> None:
    async with client.ws_connect("/ws") as ws:
        msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
        assert msg.type == WSMsgType.TEXT
        data = json.loads(msg.data)
        assert data["kind"] == "state"
        assert data["payload"]["state"] == "idle"


@pytest.mark.asyncio
async def test_ws_toggle_action_calls_session(client) -> None:
    async with client.ws_connect("/ws") as ws:
        # 跳过 initial state
        await ws.receive()
        await ws.send_str(json.dumps({"action": "toggle"}))
        # 给点时间让 server 处理
        await asyncio.sleep(0.05)

    # 通过应用上下文取出 fake session
    app = client.server.app
    assert app["fake_session"].toggled == 1


@pytest.mark.asyncio
async def test_ws_broadcasts_session_events(client) -> None:
    fake = client.server.app["fake_session"]
    async with client.ws_connect("/ws") as ws:
        await ws.receive()  # initial state
        await fake.emit(SessionEvent(kind="partial", payload={"text": "你好"}))
        msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
        data = json.loads(msg.data)
        assert data == {"kind": "partial", "payload": {"text": "你好"}}


# ── /api/context 推送 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_push_and_get(client) -> None:
    resp = await client.post(
        "/api/context",
        json={"text": "用户在讨论激光雷达点云", "source": "cursor", "ttl_s": 30},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["source"] == "cursor"
    assert body["ttl_s"] == 30.0

    g = await client.get("/api/context")
    assert g.status == 200
    data = await g.json()
    assert data["present"] is True
    assert data["source"] == "cursor"
    assert "激光雷达" in data["preview"]


@pytest.mark.asyncio
async def test_context_get_when_empty(client) -> None:
    resp = await client.get("/api/context")
    assert resp.status == 200
    assert (await resp.json()) == {"present": False}


@pytest.mark.asyncio
async def test_context_delete(client) -> None:
    await client.post("/api/context", json={"text": "x", "source": "test"})
    resp = await client.delete("/api/context")
    assert resp.status == 200
    g = await client.get("/api/context")
    assert (await g.json())["present"] is False


@pytest.mark.asyncio
async def test_context_rejects_empty_text(client) -> None:
    resp = await client.post("/api/context", json={"text": ""})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_context_rejects_bad_ttl(client) -> None:
    resp = await client.post(
        "/api/context", json={"text": "x", "ttl_s": -1}
    )
    assert resp.status == 400
    resp = await client.post(
        "/api/context", json={"text": "x", "ttl_s": 999999}
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_context_rejects_oversize(client) -> None:
    resp = await client.post("/api/context", json={"text": "A" * 10000})
    assert resp.status == 413


@pytest.mark.asyncio
async def test_context_latest_wins(client) -> None:
    await client.post("/api/context", json={"text": "first", "source": "a"})
    await client.post("/api/context", json={"text": "second", "source": "b"})
    g = await client.get("/api/context")
    data = await g.json()
    assert data["source"] == "b"
    assert data["preview"].startswith("second")


@pytest.mark.asyncio
async def test_context_settings_can_disable_history_context(client, history: HistoryStore) -> None:
    history.insert("上一段")
    resp = await client.patch(
        "/api/context/settings", json={"history_context_enabled": False}
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["history_context"]["enabled"] is False

    rec = history.insert("新一段")
    edit = await client.patch(f"/api/history/{rec.id}", json={"text": "编辑后"})
    assert edit.status == 200
    assert client.server.app["fake_context_cache"].get() is None


@pytest.mark.asyncio
async def test_context_settings_enable_refreshes_recent_history(
    client, history: HistoryStore
) -> None:
    history.insert("第一段")
    history.insert("第二段")
    await client.patch(
        "/api/context/settings", json={"history_context_enabled": False}
    )
    resp = await client.patch(
        "/api/context/settings", json={"history_context_enabled": True}
    )
    assert resp.status == 200

    entry = client.server.app["fake_context_cache"].get()
    assert entry is not None
    assert entry.source == "voice_history"
    assert entry.text == "第一段\n第二段"
