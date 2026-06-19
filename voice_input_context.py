#!/usr/bin/env python3
"""Push recent Claude Code conversation context to local voice_input."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple


URL = os.getenv("VOICE_INPUT_URL", "http://127.0.0.1:8770").rstrip("/")
MAX_CHARS = int(os.getenv("VOICE_INPUT_CONTEXT_MAX_CHARS", "1500"))
TURNS = int(os.getenv("VOICE_INPUT_CONTEXT_TURNS", "4"))
TTL_S = int(os.getenv("VOICE_INPUT_CONTEXT_TTL_S", "90"))
DEBUG = os.getenv("VOICE_INPUT_CONTEXT_DEBUG") == "1"
LOG_PATH = Path.home() / ".claude" / "voice_input_context.log"


_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]{1,200}`")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?:/[A-Za-z0-9_.\-]+){2,}")
_WS = re.compile(r"[ \t]{2,}")


def _log(msg: str) -> None:
    if not DEBUG:
        return
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def _strip_noise(text: str) -> str:
    text = _FENCE.sub(" ", text)
    text = _INLINE.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _PATH.sub(" ", text)
    text = _WS.sub(" ", text)
    return text.strip()


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            t = c.get("text") or ""
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def _read_recent_turns(transcript_path: Path, turns: int) -> List[Tuple[str, str]]:
    if not transcript_path.exists():
        return []
    try:
        lines = transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    collected: List[Tuple[str, str]] = []
    for line in reversed(lines):
        if len(collected) >= turns:
            break
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t not in ("user", "assistant"):
            continue
        msg = obj.get("message") or {}
        role = msg.get("role") or t
        if role not in ("user", "assistant"):
            continue
        cleaned = _strip_noise(_extract_text(msg.get("content")))
        if cleaned:
            collected.append((role, cleaned))
    collected.reverse()
    return collected


def _assemble(turns_data: List[Tuple[str, str]], max_chars: int) -> Optional[str]:
    if not turns_data:
        return None
    parts = []
    for role, text in turns_data:
        tag = "用户" if role == "user" else "助手"
        parts.append(f"{tag}：{text}")
    blob = "\n".join(parts)
    if len(blob) > max_chars:
        blob = "..." + blob[-(max_chars - 3):]
    return blob


def _push(text: str, source: str) -> None:
    body = json.dumps(
        {"text": text, "source": source, "ttl_s": TTL_S},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        URL + "/api/context",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            _log(f"pushed: {resp.status} len={len(text)} src={source}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        _log(f"push failed (silent): {e}")


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    blob = _assemble(_read_recent_turns(Path(transcript_path), TURNS), MAX_CHARS)
    if not blob:
        return 0

    hook_event = payload.get("hook_event_name") or os.getenv("CLAUDE_HOOK_EVENT", "?")
    _push(blob, source=f"claude:{hook_event}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
