"""wav 直灌 AB 对比：验证 context 注入是否真的提升识别准确度。

流程：
  1. 用 macOS say 合成中文测试句（含项目术语）
  2. ffmpeg 转 16k mono int16 PCM
  3. 跑两次 DoubaoASRSession：A) 不带 context  B) 带 context+hotwords
  4. 打印两边识别结果与差异

用法：
    python -m voice_input.scripts.ab_compare

依赖：macOS say + ffmpeg + voice_input venv（websockets / pytest 等）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

# 同时支持 `python -m voice_input.scripts.ab_compare` 和 `python ab_compare.py`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from voice_input import config
from voice_input.core.context_builder import build_doubao_corpus, load_hotwords
from voice_input.core.doubao_asr import DoubaoASRSession


log = logging.getLogger("ab_compare")


TEST_CASES = [
    {
        "id": "lidar-mcp",
        "text": "打开 MCP 看一下 Benewake 激光雷达的点云",
        "context": "用户在调试 Benewake Horn X2 激光雷达上位机，想通过 MCP 工具控制 lidar_bridge 显示点云。",
        "want_terms": ["MCP", "Benewake", "激光雷达", "点云"],
    },
    {
        "id": "threejs-orbit",
        "text": "把 Three.js 的 OrbitControls 配一下",
        "context": "用户在写浏览器端 3D 点云可视化，使用 Three.js 框架，需要设置 OrbitControls 鼠标交互。",
        "want_terms": ["Three.js", "OrbitControls"],
    },
    {
        "id": "lidar-bridge",
        "text": "lidar_bridge 是用 WebSocket 把 PCD 推到浏览器的",
        "context": "用户在解释 Phase 2 架构：C++ lidar_bridge 进程通过 WebSocket 把点云数据推到浏览器，前端用 Three.js 渲染。",
        "want_terms": ["lidar_bridge", "WebSocket", "PCD"],
    },
]


# ── 音频生成 ───────────────────────────────────────────────────


def synthesize_say(text: str, out_aiff: Path, voice: str = "Tingting", rate: int = 175) -> None:
    """用 macOS say 合成 aiff。"""
    cmd = ["say", "-v", voice, "-r", str(rate), "-o", str(out_aiff), text]
    subprocess.run(cmd, check=True)


def aiff_to_pcm16k(in_path: Path, out_pcm: Path) -> None:
    """ffmpeg 转 16k mono int16 PCM（裸字节，无 wav header）。"""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(in_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        str(out_pcm),
    ]
    subprocess.run(cmd, check=True)


def chunk_pcm(pcm: bytes, chunk_ms: int = 100, sample_rate: int = 16000) -> List[bytes]:
    bytes_per_chunk = int(sample_rate * 2 * chunk_ms / 1000)  # int16 = 2 字节
    return [pcm[i : i + bytes_per_chunk] for i in range(0, len(pcm), bytes_per_chunk)]


# ── ASR 会话 ───────────────────────────────────────────────────


async def transcribe(pcm: bytes, extra_request: Optional[dict], *, total_timeout: float = 25.0) -> str:
    """跑一次 ASR session，返回最终累积文本。

    total_timeout 兜底——若豆包因字段名等问题卡住不回 final，超过此值就放弃。
    """
    chunks = chunk_pcm(pcm)
    finalized: dict[int, str] = {}
    current_partial = ""

    async def _run() -> str:
        nonlocal current_partial
        async with DoubaoASRSession(
            app_id=config.DOUBAO_APP_ID,
            access_key=config.DOUBAO_ACCESS_KEY,
            resource_id=config.DOUBAO_RESOURCE_ID,
            url=config.DOUBAO_ASR_URL,
            extra_request=extra_request,
        ) as sess:

            async def feeder() -> None:
                for c in chunks:
                    if c:
                        await sess.send_audio(c)
                        await asyncio.sleep(0.02)
                await sess.finish()

            feed_task = asyncio.create_task(feeder())
            try:
                async for evt in sess.events():
                    for u in evt.utterances:
                        if u.definite:
                            finalized[u.start_time] = u.text
                    non_def = [u for u in evt.utterances if not u.definite]
                    current_partial = non_def[-1].text if non_def else ""
            finally:
                await feed_task
        return "".join(t for _, t in sorted(finalized.items())) + current_partial

    try:
        return await asyncio.wait_for(_run(), timeout=total_timeout)
    except asyncio.TimeoutError:
        log.warning("transcribe timeout after %.1fs (extra=%s)", total_timeout, extra_request)
        return "".join(t for _, t in sorted(finalized.items())) + current_partial


# ── 对比 ───────────────────────────────────────────────────────


def score(transcript: str, want_terms: List[str]) -> dict:
    hits = [t for t in want_terms if t.lower() in transcript.lower()]
    return {
        "hits": hits,
        "misses": [t for t in want_terms if t not in hits],
        "rate": f"{len(hits)}/{len(want_terms)}",
    }


async def run_case(case: dict, hotwords: List[str], voice: str) -> dict:
    print(f"\n━━━ {case['id']} ━━━")
    print(f"原文: {case['text']}")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        aiff = tmp / "x.aiff"
        pcm = tmp / "x.pcm"
        synthesize_say(case["text"], aiff, voice=voice)
        aiff_to_pcm16k(aiff, pcm)
        pcm_bytes = pcm.read_bytes()
        print(f"音频: {len(pcm_bytes)} bytes ({len(pcm_bytes)/32000:.2f}s @16k mono int16)")

        # A) 不带 context（baseline）
        txt_a = await transcribe(pcm_bytes, extra_request=None)

        # B) 带 corpus.context（dialog_ctx + hotwords 背景条目）
        extra = build_doubao_corpus(case["context"], hotwords)
        txt_b = await transcribe(pcm_bytes, extra_request=extra)

    sa = score(txt_a, case["want_terms"])
    sb = score(txt_b, case["want_terms"])
    print(f"\n  [A]  无 context : {txt_a!r}")
    print(f"        命中 {sa['rate']} {sa['hits']}; 漏 {sa['misses']}")
    print(f"  [B]  带 context : {txt_b!r}")
    print(f"        命中 {sb['rate']} {sb['hits']}; 漏 {sb['misses']}")
    if sb['hits'] != sa['hits']:
        delta = set(sb['hits']) - set(sa['hits'])
        rev = set(sa['hits']) - set(sb['hits'])
        if delta:
            print(f"  ✓ context 修复: {sorted(delta)}")
        if rev:
            print(f"  ✗ context 反而丢失: {sorted(rev)}")
    return {"id": case["id"], "a": {**sa, "text": txt_a}, "b": {**sb, "text": txt_b}}


async def amain(args: argparse.Namespace) -> int:
    if not shutil.which("ffmpeg"):
        print("✗ 缺 ffmpeg：brew install ffmpeg", file=sys.stderr)
        return 2
    if not shutil.which("say"):
        print("✗ 缺 say（仅 macOS）", file=sys.stderr)
        return 2

    hotwords = load_hotwords(config.HOTWORDS_PATH)
    print(f"hotwords: {len(hotwords)} 词，从 {config.HOTWORDS_PATH}")
    print(f"voice: {args.voice}")

    cases = TEST_CASES
    if args.case:
        cases = [c for c in TEST_CASES if c["id"] == args.case]
        if not cases:
            print(f"✗ 未知 case: {args.case}", file=sys.stderr)
            return 2

    results = []
    for c in cases:
        try:
            r = await run_case(c, hotwords, args.voice)
            results.append(r)
        except Exception as e:
            print(f"  ✗ {c['id']} 失败: {e}")

    # 总结
    total_a = sum(len(r["a"]["hits"]) for r in results)
    total_b = sum(len(r["b"]["hits"]) for r in results)
    total = sum(len(r["a"]["hits"]) + len(r["a"]["misses"]) for r in results)
    print(f"\n━━━ 总结 ━━━")
    print(f"  无 context 命中: {total_a}/{total}")
    print(f"  带 context 命中: {total_b}/{total}")
    print(f"  净提升: {total_b - total_a:+d}")
    return 0 if total_b >= total_a else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--voice", default="Tingting", help="say 中文人声")
    p.add_argument("--case", default=None, help="只跑指定 id（lidar-mcp / threejs-orbit / lidar-bridge）")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
