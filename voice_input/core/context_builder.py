"""把动态 context（cursor 推送）+ 静态热词（hotwords.txt）拼成
喂给豆包 ASR 握手帧的 corpus 子对象。

豆包 v3 sauc bigmodel 的 request.corpus.context 字段是 string，
但内容必须是 JSON-stringified 对象，有两种合法 shape：

  1. 热词偏置（双向流式 ≤ 100 tokens）：
     {"hotwords": [{"word": "Benewake"}, {"word": "MCP"}]}

  2. 对话上下文（≤ 800 tokens & 20 轮，从新到旧）：
     {"context_type": "dialog_ctx",
      "context_data": [{"text": "用户说..."}, {"text": "更早..."}]}

策略：
  - 有 dynamic（cursor 推送）→ dialog_ctx 模式，hotwords 作为最旧条目塞进 context_data
  - 只有 hotwords → 纯 hotwords 模式（词级偏置，权重更高）
  - 都没有 → None
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# 豆包文档限制：双向流式 hotwords ≤ 100 tokens；dialog_ctx ≤ 800 tokens & 20 轮
MAX_HOTWORDS_PURE_MODE = 80      # 留 buffer，每词约 1-3 tokens
MAX_HOTWORDS_IN_DIALOG = 30      # 在 dialog_ctx 里只塞最重要的几十个
MAX_DIALOG_TURNS = 20


def load_hotwords(path: Path) -> List[str]:
    """读 hotwords.txt：一行一个词，# 开头注释，空行跳过，去重保序。"""
    if not path.exists():
        return []
    out: List[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out


def _split_dynamic_into_turns(dynamic: str, max_turns: int) -> List[str]:
    """把动态 context 字符串拆成 turns 列表，从新到旧。

    Cursor hook 拼好的 blob 形如：
        用户：xxx
        助手：yyy
        用户：zzz
    我们按行倒序拆，每个非空行是一条 turn。
    """
    lines = [ln.strip() for ln in dynamic.splitlines() if ln.strip()]
    # 倒序——豆包文档要求 context_data "从新到旧"
    lines.reverse()
    return lines[:max_turns]


def build_doubao_corpus(
    dynamic: Optional[str],
    hotwords: Iterable[str],
    *,
    max_hotwords_pure: int = MAX_HOTWORDS_PURE_MODE,
    max_hotwords_in_dialog: int = MAX_HOTWORDS_IN_DIALOG,
    max_dialog_turns: int = MAX_DIALOG_TURNS,
) -> Optional[Dict[str, Any]]:
    """生成豆包 ASR 握手帧 request 子对象里 corpus 字段的完整结构。

    返回 ``{"corpus": {"context": <json-string>}}``，或 ``None`` 表示无内容。
    上层把它整体 merge 到 ``request`` 即可。
    """
    dyn = (dynamic or "").strip()
    hot_list = [w.strip() for w in hotwords if w and w.strip()]

    if not dyn and not hot_list:
        return None

    if dyn:
        # dialog_ctx 模式：把 dynamic 拆成多 turns（从新到旧），
        # 末尾再追加一条"项目常用术语"作为最旧的背景条目（如果有 hotwords）。
        turns = _split_dynamic_into_turns(dyn, max_dialog_turns)
        # 留至少 1 个 slot 给 hotwords 背景条目
        if hot_list:
            turns = turns[: max_dialog_turns - 1]
            turns.append(
                "项目常用术语："
                + "、".join(hot_list[:max_hotwords_in_dialog])
                + "。"
            )
        ctx_obj: Dict[str, Any] = {
            "context_type": "dialog_ctx",
            "context_data": [{"text": t} for t in turns],
        }
    else:
        # 纯 hotwords 模式（词级偏置）
        ctx_obj = {
            "hotwords": [{"word": w} for w in hot_list[:max_hotwords_pure]]
        }

    return {
        "corpus": {
            "context": json.dumps(ctx_obj, ensure_ascii=False)
        }
    }


# ── 旧版 API（保留以避免破坏外部调用方）─────────────────────────


def build_context_blob(
    dynamic: Optional[str],
    hotwords: Iterable[str],
    *,
    max_chars: int = 1000,
) -> Optional[str]:
    """[deprecated] 旧的"拼成单 string"接口，只用于诊断/probe 工具。

    生产路径请用 ``build_doubao_corpus``。
    """
    dyn = (dynamic or "").strip()
    hot_list = [w.strip() for w in hotwords if w and w.strip()]
    parts: List[str] = []
    if dyn:
        parts.append(dyn)
    if hot_list:
        parts.append("本项目常用术语：" + "、".join(hot_list) + "。")
    if not parts:
        return None
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = "…" + blob[-(max_chars - 1):]
    return blob
