"""aiohttp HTTP + WebSocket 服务，给浏览器界面用。

路由：
  GET  /                  → web/index.html
  GET  /static/{path}     → web/* 静态资源
  GET  /ws                → 状态/识别事件推送 + 用户触发 toggle
  GET  /api/history       → 历史列表 JSON
  PATCH /api/history/{id} → 更新文本
  DELETE /api/history/{id}→ 删除
  POST /api/copy          → {"text": "..."} 写剪贴板（不粘贴）
  GET  /api/audio/input-devices → 可用输入设备
  POST /api/audio/input-device  → {"device_id": 4 | null} 选择输入设备
  POST /api/context       → {"text": "...", "source": "...", "ttl_s": 60} 推送语境
  GET  /api/context       → 当前缓存的 context（调试用）
  DELETE /api/context     → 立刻清空 context
"""
from __future__ import annotations

import asyncio
import json
import logging
import weakref
from pathlib import Path
from typing import Optional, Set

from aiohttp import WSMsgType, web

from voice_input.core.context_cache import ContextCache
from voice_input.core.history import HistoryStore
from voice_input.core.history_context import HistoryContextManager
from voice_input.core.paster import copy_to_clipboard
from voice_input.core.recorder import Recorder
from voice_input.core.session import SessionEvent, VoiceInputSession


log = logging.getLogger(__name__)


def build_app(
    session: VoiceInputSession,
    history: HistoryStore,
    web_dir: Path,
    context_cache: Optional[ContextCache] = None,
    history_context: Optional[HistoryContextManager] = None,
    recorder: Optional[Recorder] = None,
) -> web.Application:
    app = web.Application()
    app["session"] = session
    app["history"] = history
    app["context_cache"] = context_cache or ContextCache()
    app["history_context"] = history_context
    app["recorder"] = recorder
    app["ws_clients"] = weakref.WeakSet()

    async def _broadcast(evt: SessionEvent) -> None:
        msg = json.dumps({"kind": evt.kind, "payload": evt.payload}, ensure_ascii=False)
        dead = []
        for ws in list(app["ws_clients"]):
            try:
                await ws.send_str(msg)
            except ConnectionResetError:
                dead.append(ws)
        for ws in dead:
            app["ws_clients"].discard(ws)

    session.add_listener(_broadcast)

    # ── 静态首页 ─────────────────────────────────
    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(web_dir / "index.html")

    app.router.add_get("/", index)
    app.router.add_static("/static", path=str(web_dir), show_index=False)

    # ── WebSocket ───────────────────────────────
    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        app["ws_clients"].add(ws)

        # 上线先推一次状态
        await ws.send_str(
            json.dumps({"kind": "state", "payload": {"state": session.state}})
        )

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    obj = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                action = obj.get("action")
                if action == "toggle":
                    await session.toggle()
        finally:
            app["ws_clients"].discard(ws)
        return ws

    app.router.add_get("/ws", ws_handler)

    # ── 历史 REST ───────────────────────────────
    async def list_history(_request: web.Request) -> web.Response:
        items = [r.to_dict() for r in history.list(limit=200)]
        return web.json_response(items)

    async def update_history(request: web.Request) -> web.Response:
        try:
            item_id = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        body = await request.json()
        text = body.get("text", "")
        if not isinstance(text, str):
            return web.json_response({"error": "text must be string"}, status=400)
        rec = history.update_text(item_id, text)
        if rec is None:
            return web.json_response({"error": "not found"}, status=404)
        manager: Optional[HistoryContextManager] = app["history_context"]
        if manager is not None:
            manager.refresh()
        return web.json_response(rec.to_dict())

    async def delete_history(request: web.Request) -> web.Response:
        try:
            item_id = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        ok = history.delete(item_id)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        manager: Optional[HistoryContextManager] = app["history_context"]
        if manager is not None:
            manager.refresh()
        return web.json_response({"ok": True})

    async def copy_text(request: web.Request) -> web.Response:
        body = await request.json()
        text = body.get("text", "")
        if not isinstance(text, str) or not text:
            return web.json_response({"error": "text required"}, status=400)
        copy_to_clipboard(text)
        return web.json_response({"ok": True})

    app.router.add_get("/api/history", list_history)
    app.router.add_patch("/api/history/{id}", update_history)
    app.router.add_delete("/api/history/{id}", delete_history)
    app.router.add_post("/api/copy", copy_text)

    # ── 音频输入设备 ─────────────────────────────
    async def list_input_devices(_request: web.Request) -> web.Response:
        rec = app["recorder"]
        if rec is None:
            return web.json_response({"error": "recorder unavailable"}, status=503)
        try:
            devices = rec.list_input_devices()
        except Exception as e:
            log.warning("list input devices failed: %s", e)
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response(
            {
                "selected": rec.selected_input_device,
                "devices": devices,
            }
        )

    async def select_input_device(request: web.Request) -> web.Response:
        rec = app["recorder"]
        if rec is None:
            return web.json_response({"error": "recorder unavailable"}, status=503)
        if session.state != "idle":
            return web.json_response(
                {"error": "input device can only be changed while idle"},
                status=409,
            )
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)
        device_id = body.get("device_id")
        if device_id is not None:
            try:
                device_id = int(device_id)
            except (TypeError, ValueError):
                return web.json_response(
                    {"error": "device_id must be int or null"},
                    status=400,
                )
            available_ids = {d["id"] for d in rec.list_input_devices()}
            if device_id not in available_ids:
                return web.json_response({"error": "input device not found"}, status=404)
        rec.set_input_device(device_id)
        return web.json_response(
            {
                "ok": True,
                "selected": rec.selected_input_device,
                "devices": rec.list_input_devices(),
            }
        )

    app.router.add_get("/api/audio/input-devices", list_input_devices)
    app.router.add_post("/api/audio/input-device", select_input_device)

    # ── Context 推送（cursor hook 等外部工具用） ──────────────
    async def push_context(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)
        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return web.json_response({"error": "text required"}, status=400)
        source = body.get("source", "external")
        if not isinstance(source, str):
            return web.json_response({"error": "source must be string"}, status=400)
        try:
            ttl = float(body.get("ttl_s", 60.0))
        except (TypeError, ValueError):
            return web.json_response({"error": "ttl_s must be number"}, status=400)
        if ttl <= 0 or ttl > 3600:
            return web.json_response({"error": "ttl_s out of range (0, 3600]"}, status=400)
        # 限制单次推送大小（防止失控字符串）
        if len(text) > 8000:
            return web.json_response({"error": "text too long (>8KB)"}, status=413)
        cache: ContextCache = app["context_cache"]
        entry = cache.push(text, source=source, ttl_s=ttl)
        log.info("context pushed: source=%s len=%d ttl=%.0fs", source, len(text), ttl)
        return web.json_response(
            {
                "ok": True,
                "source": entry.source,
                "ttl_s": entry.ttl_s,
                "length": len(entry.text),
            }
        )

    async def get_context(_request: web.Request) -> web.Response:
        cache: ContextCache = app["context_cache"]
        entry = cache.get()
        if entry is None:
            return web.json_response({"present": False})
        return web.json_response(
            {
                "present": True,
                "source": entry.source,
                "pushed_at": entry.pushed_at,
                "ttl_s": entry.ttl_s,
                "length": len(entry.text),
                "preview": entry.text[:200],
            }
        )

    async def delete_context(_request: web.Request) -> web.Response:
        cache: ContextCache = app["context_cache"]
        cache.clear()
        return web.json_response({"ok": True})

    async def get_context_settings(_request: web.Request) -> web.Response:
        manager: Optional[HistoryContextManager] = app["history_context"]
        if manager is None:
            return web.json_response({"history_context": {"available": False}})
        return web.json_response(
            {"history_context": {"available": True, **manager.to_dict()}}
        )

    async def update_context_settings(request: web.Request) -> web.Response:
        manager: Optional[HistoryContextManager] = app["history_context"]
        if manager is None:
            return web.json_response({"error": "history context unavailable"}, status=503)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)
        enabled = body.get("history_context_enabled")
        if not isinstance(enabled, bool):
            return web.json_response(
                {"error": "history_context_enabled must be boolean"},
                status=400,
            )
        manager.set_enabled(enabled)
        if enabled:
            manager.refresh()
        return web.json_response(
            {"history_context": {"available": True, **manager.to_dict()}}
        )

    app.router.add_post("/api/context", push_context)
    app.router.add_get("/api/context", get_context)
    app.router.add_delete("/api/context", delete_context)
    app.router.add_get("/api/context/settings", get_context_settings)
    app.router.add_patch("/api/context/settings", update_context_settings)

    return app


async def run_server(
    app: web.Application,
    host: str,
    port: int,
) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("voice_input server on http://%s:%d", host, port)
    return runner
