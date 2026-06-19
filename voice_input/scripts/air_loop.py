"""物理空气回路烟雾测试：
   say -> 内置扬声器 -> 空气 -> 内置麦克风 -> ffmpeg 录 PCM -> 喂 voice_input ASR

目的：在真实硬件上跑通"say 一句话 → 出来什么"，证明完整 pipeline 工作。
不做严格识别准确度评估（空气回路噪声大），只看能否识别成像样的中文。

要求：
- 终端有麦克风权限（系统设置 → 隐私 → 麦克风）
- 默认输入设备是内置麦克风（或任何能听到扬声器声音的输入设备）
- 房间不太吵

用法：
    python -m voice_input.scripts.air_loop
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from voice_input import config
from voice_input.core.context_builder import (
    build_context_blob,
    build_doubao_corpus,
    load_hotwords,
)
from voice_input.core.doubao_asr import DoubaoASRSession
from voice_input.core.gateway_stt import GatewaySTTConfig, GatewayTranscriptionSession
from voice_input.scripts.ab_compare import chunk_pcm


DEFAULT_TEXT = "打开 MCP 看一下 Benewake 激光雷达的点云"
DEFAULT_CONTEXT = (
    "用户在调试 Benewake Horn X2 激光雷达，通过 MCP 工具控制 lidar_bridge 显示点云。"
)


async def transcribe_with_provider(
    pcm: bytes,
    *,
    context: str | None,
    hotwords: list[str],
) -> str:
    """Use the configured STT provider so air_loop tests the release path."""
    if config.STT_PROVIDER == "gateway":
        prompt = build_context_blob(
            context,
            hotwords,
            max_chars=config.CONTEXT_MAX_CHARS,
        )
        async with GatewayTranscriptionSession(
            GatewaySTTConfig(
                base_url=config.AI_BASE_URL,
                api_key=config.AI_API_KEY,
                model=config.AI_STT_MODEL,
                language=config.AI_STT_LANGUAGE,
                sample_rate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                sample_width=config.SAMPLE_WIDTH,
                prompt=prompt,
            )
        ) as sess:
            await sess.send_audio(pcm)
            await sess.finish()
            texts = []
            async for evt in sess.events():
                texts.extend(u.text for u in evt.utterances)
            return "".join(texts).strip()

    if config.STT_PROVIDER == "doubao":
        extra = build_doubao_corpus(context, hotwords) if context else None
        chunks = chunk_pcm(pcm)
        finalized: dict[int, str] = {}
        current_partial = ""
        async with DoubaoASRSession(
            app_id=config.DOUBAO_APP_ID,
            access_key=config.DOUBAO_ACCESS_KEY,
            resource_id=config.DOUBAO_RESOURCE_ID,
            url=config.DOUBAO_ASR_URL,
            extra_request=extra,
        ) as sess:
            for chunk in chunks:
                if chunk:
                    await sess.send_audio(chunk)
                    await asyncio.sleep(0.02)
            await sess.finish()
            async for evt in sess.events():
                for u in evt.utterances:
                    if u.definite:
                        finalized[u.start_time] = u.text
                non_def = [u for u in evt.utterances if not u.definite]
                current_partial = non_def[-1].text if non_def else ""
        return "".join(t for _, t in sorted(finalized.items())) + current_partial

    raise RuntimeError(
        f"未知 VOICE_INPUT_STT_PROVIDER={config.STT_PROVIDER!r}，可选 gateway 或 doubao"
    )


async def record_through_mic(seconds: float, out_pcm: Path, audio_idx: int) -> None:
    """用 ffmpeg 录指定 avfoundation 输入设备 N 秒，转 16k mono int16 PCM 裸字节。

    audio_idx 是 `ffmpeg -f avfoundation -list_devices true -i ""` 输出里
    "AVFoundation audio devices" 段的 [N] 编号。在这台机子上 11 = MacBook Pro 麦克风。
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-i",
        f":{audio_idx}",
        "-t",
        f"{seconds:.2f}",
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
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 录音失败 (rc={proc.returncode}): {err.decode(errors='ignore')}"
        )


async def speak(text: str, voice: str, rate: int = 175) -> None:
    proc = await asyncio.create_subprocess_exec(
        "say", "-v", voice, "-r", str(rate), text
    )
    await proc.wait()


async def amain(args: argparse.Namespace) -> int:
    if not shutil.which("ffmpeg"):
        print("✗ 缺 ffmpeg：brew install ffmpeg", file=sys.stderr)
        return 2
    if not shutil.which("say"):
        print("✗ 仅 macOS（缺 say）", file=sys.stderr)
        return 2

    text = args.text
    print(f"原文: {text!r}")
    print(f"voice: {args.voice}, 录音 {args.duration}s")

    with tempfile.TemporaryDirectory() as td:
        pcm_path = Path(td) / "rec.pcm"
        # 先开录音 task；等 ~0.5s 让 ffmpeg ready 再说话
        rec = asyncio.create_task(record_through_mic(args.duration, pcm_path, args.audio_device))
        await asyncio.sleep(0.6)
        await speak(text, args.voice)
        await rec
        pcm = pcm_path.read_bytes()
        print(f"录到 {len(pcm)} bytes ({len(pcm)/32000:.2f}s @16k mono)")

        hotwords = load_hotwords(config.HOTWORDS_PATH)
        print(f"provider: {config.STT_PROVIDER}, hotwords: {len(hotwords)}")

        # 跑两次：无 context vs 有 context
        txt_a = await transcribe_with_provider(pcm, context=None, hotwords=[])
        txt_b = await transcribe_with_provider(
            pcm,
            context=args.context,
            hotwords=hotwords,
        )

    print(f"\n  [A] 无 context : {txt_a!r}")
    print(f"  [B] 带 context : {txt_b!r}")

    if not txt_a and not txt_b:
        print("\n✗ 两边都空——可能麦克风没权限 / 扬声器静音 / 房间太吵")
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--text", default=DEFAULT_TEXT, help="要播放的中文测试句")
    p.add_argument("--context", default=DEFAULT_CONTEXT, help="带 context 实验组的前文")
    p.add_argument("--voice", default="Tingting")
    p.add_argument("--duration", type=float, default=6.0, help="录音秒数")
    p.add_argument(
        "--audio-device",
        type=int,
        default=11,
        help="avfoundation 音频输入设备编号（默认 11 = MacBook Pro 麦克风；"
        "用 `ffmpeg -f avfoundation -list_devices true -i \"\"` 查询）",
    )
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
