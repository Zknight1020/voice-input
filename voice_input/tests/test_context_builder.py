import json
from pathlib import Path

from voice_input.core.context_builder import (
    build_context_blob,
    build_doubao_corpus,
    load_hotwords,
)


def test_load_hotwords_strips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "hot.txt"
    p.write_text(
        "# header\n"
        "Benewake\n"
        "\n"
        "  MCP  \n"
        "# 中间注释\n"
        "Three.js\n",
        encoding="utf-8",
    )
    assert load_hotwords(p) == ["Benewake", "MCP", "Three.js"]


def test_load_hotwords_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "hot.txt"
    p.write_text("A\nB\nA\nC\nB\n", encoding="utf-8")
    assert load_hotwords(p) == ["A", "B", "C"]


def test_load_hotwords_missing_file(tmp_path: Path) -> None:
    assert load_hotwords(tmp_path / "no.txt") == []


def test_build_context_blob_dynamic_only() -> None:
    blob = build_context_blob("用户在讨论激光雷达", [])
    assert blob == "用户在讨论激光雷达"


def test_build_context_blob_hotwords_only() -> None:
    blob = build_context_blob(None, ["Benewake", "MCP", "Three.js"])
    assert blob == "本项目常用术语：Benewake、MCP、Three.js。"


def test_build_context_blob_both() -> None:
    blob = build_context_blob("用户在讨论激光雷达", ["Benewake", "MCP"])
    assert blob is not None
    assert "用户在讨论激光雷达" in blob
    assert "本项目常用术语：Benewake、MCP。" in blob
    assert blob.index("用户") < blob.index("本项目")


def test_build_context_blob_empty() -> None:
    assert build_context_blob(None, []) is None
    assert build_context_blob("", []) is None
    assert build_context_blob("   \n  ", ["  ", ""]) is None


def test_build_context_blob_truncates_keeping_tail() -> None:
    long = "A" * 5000
    blob = build_context_blob(long, ["X"], max_chars=200)
    assert blob is not None
    assert len(blob) <= 200
    assert blob.startswith("…")
    # 热词在末尾，要保留
    assert "X" in blob


# ── build_doubao_corpus（生产路径）───────────────────────────────


def test_doubao_corpus_empty_inputs_returns_none() -> None:
    assert build_doubao_corpus(None, []) is None
    assert build_doubao_corpus("", []) is None
    assert build_doubao_corpus("   ", ["  ", ""]) is None


def test_doubao_corpus_hotwords_only_uses_hotwords_shape() -> None:
    out = build_doubao_corpus(None, ["Benewake", "MCP", "Three.js"])
    assert out is not None
    # 顶层必须是 {"corpus": {"context": <json-string>}}
    assert set(out.keys()) == {"corpus"}
    assert set(out["corpus"].keys()) == {"context"}
    inner = json.loads(out["corpus"]["context"])
    # hotwords shape：{"hotwords": [{"word": "..."}, ...]}
    assert "hotwords" in inner
    assert "context_data" not in inner
    words = [h["word"] for h in inner["hotwords"]]
    assert words == ["Benewake", "MCP", "Three.js"]


def test_doubao_corpus_dynamic_only_uses_dialog_ctx_shape() -> None:
    out = build_doubao_corpus("用户：打开 MCP\n助手：好的", [])
    assert out is not None
    inner = json.loads(out["corpus"]["context"])
    assert inner["context_type"] == "dialog_ctx"
    assert "hotwords" not in inner
    # context_data 是从新到旧
    texts = [d["text"] for d in inner["context_data"]]
    assert texts == ["助手：好的", "用户：打开 MCP"]


def test_doubao_corpus_both_dialog_ctx_with_hotwords_tail() -> None:
    out = build_doubao_corpus(
        "用户：打开 MCP\n助手：好的",
        ["Benewake", "MCP", "激光雷达"],
    )
    assert out is not None
    inner = json.loads(out["corpus"]["context"])
    assert inner["context_type"] == "dialog_ctx"
    texts = [d["text"] for d in inner["context_data"]]
    # 最旧那条（索引 -1）是项目术语背景；前面是 dynamic 的 turns（从新到旧）
    assert texts[-1].startswith("项目常用术语：")
    assert "Benewake" in texts[-1]
    assert "MCP" in texts[-1]
    assert texts[0] == "助手：好的"
    assert texts[1] == "用户：打开 MCP"


def test_doubao_corpus_dialog_ctx_caps_at_20_turns() -> None:
    big = "\n".join(f"turn {i}" for i in range(50))
    out = build_doubao_corpus(big, ["X"])
    assert out is not None
    inner = json.loads(out["corpus"]["context"])
    # 20 轮含 hotwords 背景条目
    assert len(inner["context_data"]) == 20
    # 最后一条是热词
    assert inner["context_data"][-1]["text"].startswith("项目常用术语：")


def test_doubao_corpus_hotwords_only_caps_at_pure_limit() -> None:
    many = [f"w{i}" for i in range(200)]
    out = build_doubao_corpus(None, many, max_hotwords_pure=50)
    assert out is not None
    inner = json.loads(out["corpus"]["context"])
    assert len(inner["hotwords"]) == 50


def test_doubao_corpus_context_is_string_not_dict() -> None:
    """schema 关键：corpus.context 必须是 string，里面是 json-stringified 对象。"""
    out = build_doubao_corpus("hello", ["A"])
    assert out is not None
    assert isinstance(out["corpus"]["context"], str)
    # 能被 json.loads 解开
    parsed = json.loads(out["corpus"]["context"])
    assert isinstance(parsed, dict)
