"""voice_input 主入口：装配所有部件并启动。

启动后：
  1. aiohttp 服务监听 127.0.0.1:SERVER_PORT
  2. 全局 F2 热键监听
  3. 自动打开默认浏览器到 http://127.0.0.1:SERVER_PORT/
  4. Ctrl+C 退出

启动参数：
  --port PORT     覆盖端口
  --no-paste      只写剪贴板，不模拟粘贴（macOS 未授权时建议）
  --no-hotkey     不绑全局热键（仅靠浏览器按钮触发）
  --no-browser    不自动打开浏览器
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import webbrowser
from pathlib import Path
from typing import NoReturn

# 同时支持 `python main.py` 和 `python -m voice_input.main`
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_input import config
from voice_input.core.context_builder import build_doubao_corpus, load_hotwords
from voice_input.core.context_builder import build_context_blob
from voice_input.core.context_cache import ContextCache
from voice_input.core.doubao_asr import DoubaoASRSession
from voice_input.core.gateway_stt import GatewaySTTConfig, GatewayTranscriptionSession
from voice_input.core.history import HistoryStore
from voice_input.core.history_context import HistoryContextManager, HistoryContextSettings
from voice_input.core.hotkey import HotkeyListener
from voice_input.core.paster import paste_to_focused
from voice_input.core.recorder import Recorder
from voice_input.core.session import VoiceInputSession
from voice_input.server import build_app, run_server


log = logging.getLogger("voice_input")


def _make_asr_factory(context_cache: ContextCache, hotwords: list[str]):
    """每次启动一个 ASR session 时，从 cache 读最新 dynamic + 叠加热词。

    gateway 模式把 context 作为 OpenAI-compatible transcriptions prompt；
    doubao 模式生成 corpus.context 嵌套结构注入握手帧。
    cache 不消费——同一段 context 在 TTL 内被多次复用是预期行为。
    """

    def _factory():
        entry = context_cache.get()
        dynamic = entry.text if entry else None
        if config.STT_PROVIDER == "gateway":
            prompt = build_context_blob(
                dynamic,
                hotwords,
                max_chars=config.CONTEXT_MAX_CHARS,
            )
            if prompt:
                log.info(
                    "Gateway STT prompt: source=%s, hotwords=%d, len=%d",
                    entry.source if entry else "hotwords-only",
                    len(hotwords),
                    len(prompt),
                )
            return GatewayTranscriptionSession(
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
            )

        if config.STT_PROVIDER != "doubao":
            raise RuntimeError(
                "未知 VOICE_INPUT_STT_PROVIDER="
                f"{config.STT_PROVIDER!r}，可选 gateway 或 doubao"
            )
        extra = build_doubao_corpus(dynamic, hotwords)
        if extra is not None:
            mode = "dialog_ctx" if dynamic else "hotwords"
            log.info(
                "ASR corpus.context: mode=%s, source=%s, hotwords=%d",
                mode,
                entry.source if entry else "hotwords-only",
                len(hotwords),
            )
        return DoubaoASRSession(
            app_id=config.DOUBAO_APP_ID,
            access_key=config.DOUBAO_ACCESS_KEY,
            resource_id=config.DOUBAO_RESOURCE_ID,
            url=config.DOUBAO_ASR_URL,
            extra_request=extra,
        )

    return _factory


async def _amain(args: argparse.Namespace) -> NoReturn:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    port = args.port or config.SERVER_PORT
    auto_paste = config.AUTO_PASTE and not args.no_paste

    history = HistoryStore(config.HISTORY_DB_PATH)
    recorder = Recorder(
        sample_rate=config.SAMPLE_RATE,
        channels=config.CHANNELS,
        frame_ms=config.FRAME_MS,
        input_device=config.INPUT_DEVICE,
    )

    context_cache = ContextCache()
    history_context = HistoryContextManager(
        history,
        context_cache,
        HistoryContextSettings(
            enabled=config.HISTORY_CONTEXT_ENABLED,
            max_turns=config.HISTORY_CONTEXT_TURNS,
            max_chars=config.HISTORY_CONTEXT_MAX_CHARS,
            ttl_s=config.HISTORY_CONTEXT_TTL_S,
        ),
    )
    hotwords = load_hotwords(config.HOTWORDS_PATH)
    log.info(
        "loaded %d hotwords from %s",
        len(hotwords),
        config.HOTWORDS_PATH,
    )

    async def on_final(text: str, duration_ms: int) -> None:
        history.insert(text, duration_ms=duration_ms)
        history_context.refresh()
        try:
            paste_to_focused(text, auto_paste=auto_paste)
        except Exception as e:
            log.warning("paste failed: %s", e)

    session = VoiceInputSession(
        recorder=recorder,
        asr_factory=_make_asr_factory(context_cache, hotwords),
        on_final=on_final,
    )

    app = build_app(
        session,
        history,
        config.WEB_DIR,
        context_cache=context_cache,
        history_context=history_context,
        recorder=recorder,
    )
    runner = await run_server(app, config.SERVER_HOST, port)

    loop = asyncio.get_running_loop()
    hotkey: HotkeyListener | None = None
    if not args.no_hotkey:
        hotkey = HotkeyListener(config.HOTKEY, session.toggle, loop)
        try:
            hotkey.start()
            log.info("hotkey %s 已绑定", config.HOTKEY)
        except Exception as e:
            log.warning("热键注册失败（可能缺少辅助功能权限）: %s", e)
            hotkey = None

    url = f"http://{config.SERVER_HOST}:{port}/"
    log.info("界面：%s", url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    stop = asyncio.Event()

    def _on_signal() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda *_: stop.set())

    try:
        await stop.wait()
    finally:
        log.info("正在退出 …")
        if hotkey is not None:
            hotkey.stop()
        await runner.cleanup()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="voice_input", description="豆包语音输入工具")
    p.add_argument("--port", type=int, default=None, help="HTTP/WS 端口")
    p.add_argument("--no-paste", action="store_true", help="只复制不模拟粘贴")
    p.add_argument("--no-hotkey", action="store_true", help="不绑全局热键")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
