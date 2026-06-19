"""单点探针：用最短音频 + DEBUG 帧打印，快速试一个字段名/格式。

不做 AB 对比，只看豆包对一组 extra_request 字段的"接受/拒绝/卡死"反应。

用法：
    python -m voice_input.scripts.probe_field 'corpus' 'string' '["A","B"]'
    python -m voice_input.scripts.probe_field 'hot_words' 'list' 'A,B,C'
    python -m voice_input.scripts.probe_field 'context' 'string' 'hello world'
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

os.environ.setdefault("VOICE_INPUT_DEBUG_FRAMES", "1")

from voice_input import config
from voice_input.core.doubao_asr import DoubaoASRSession
from voice_input.scripts.ab_compare import (
    aiff_to_pcm16k,
    chunk_pcm,
    synthesize_say,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("probe")


async def probe(extra: dict | None, pcm: bytes, total_timeout: float = 12.0) -> tuple[str, str]:
    """跑一次会话，返回 (text, status)。status ∈ ok / timeout / error。"""
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
            extra_request=extra,
        ) as sess:
            async def feeder() -> None:
                for c in chunks:
                    if c:
                        await sess.send_audio(c)
                        await asyncio.sleep(0.02)
                await sess.finish()

            ft = asyncio.create_task(feeder())
            try:
                async for evt in sess.events():
                    for u in evt.utterances:
                        if u.definite:
                            finalized[u.start_time] = u.text
                    nd = [u for u in evt.utterances if not u.definite]
                    current_partial = nd[-1].text if nd else ""
            finally:
                await ft
        return "".join(t for _, t in sorted(finalized.items())) + current_partial

    try:
        txt = await asyncio.wait_for(_run(), timeout=total_timeout)
        return txt, "ok"
    except asyncio.TimeoutError:
        return ("".join(t for _, t in sorted(finalized.items())) + current_partial), "timeout"
    except Exception as e:
        return f"<err: {e}>", "error"


def _parse_value(kind: str, raw: str):
    if kind == "string":
        return raw
    if kind == "list":
        return [x.strip() for x in raw.split(",") if x.strip()]
    if kind == "json":
        return json.loads(raw)
    raise SystemExit(f"unknown kind: {kind}")


async def amain(args: argparse.Namespace) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("say"):
        print("缺 ffmpeg / say")
        return 2

    text = args.text
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "x.aiff"
        pcm = Path(td) / "x.pcm"
        synthesize_say(text, aiff)
        aiff_to_pcm16k(aiff, pcm)
        pcm_bytes = pcm.read_bytes()

    print(f"\n>>> baseline (no extra) ...")
    txt0, st0 = await probe(None, pcm_bytes)
    print(f"    [{st0}] {txt0!r}")

    if args.field:
        val = _parse_value(args.kind, args.value)
        extra = {args.field: val}
        print(f"\n>>> {args.field}={args.kind}: {val!r}")
        txt1, st1 = await probe(extra, pcm_bytes)
        print(f"    [{st1}] {txt1!r}")

        if st1 == "ok" and txt1 == txt0:
            print("    => 字段被静默忽略（结果完全等同 baseline）")
        elif st1 == "ok" and txt1 != txt0:
            print("    => 字段似乎影响了识别！")
        elif st1 == "timeout":
            print("    => 服务端卡住——字段名/格式被部分识别但出错")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("field", nargs="?", help="字段名")
    p.add_argument("kind", nargs="?", choices=("string", "list", "json"))
    p.add_argument("value", nargs="?")
    p.add_argument(
        "--text",
        default="打开 MCP 看一下 Benewake 激光雷达",
        help="要 say 的中文测试句",
    )
    args = p.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
