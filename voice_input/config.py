"""voice_input 全局配置。所有可调参数集中在此。

打包后用 sys._MEIPASS 找资源；开发时用 __file__ 相对路径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


PROJECT_ROOT: Path = _project_root()

try:
    from dotenv import load_dotenv

    for env_path in (
        Path.cwd() / ".env",
        PROJECT_ROOT / ".env",
        PROJECT_ROOT.parent / ".env",
    ):
        load_dotenv(env_path, override=False)
except ImportError:
    pass


# ── 用户数据目录 ─────────────────────────────────────────────────
def user_data_dir() -> Path:
    """跨平台用户数据目录（历史 SQLite 落在这里）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home())))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    target = base / "VoiceInput"
    target.mkdir(parents=True, exist_ok=True)
    return target


HISTORY_DB_PATH: Path = Path(os.getenv("VOICE_INPUT_DB", str(user_data_dir() / "history.sqlite3")))


# ── 服务端口 ─────────────────────────────────────────────────────
SERVER_HOST: str = os.getenv("VOICE_INPUT_HOST", "127.0.0.1")
SERVER_PORT: int = int(os.getenv("VOICE_INPUT_PORT", "8770"))
WEB_DIR: Path = PROJECT_ROOT / "web"


# ── 录音参数 ────────────────────────────────────────────────────
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
SAMPLE_WIDTH: int = 2  # int16
FRAME_MS: int = 100  # 录音器每 100 ms 产出一帧 PCM
INPUT_DEVICE: int | None = (
    int(os.environ["VOICE_INPUT_INPUT_DEVICE"])
    if os.getenv("VOICE_INPUT_INPUT_DEVICE")
    else None
)


# ── 热键 ────────────────────────────────────────────────────────
HOTKEY: str = os.getenv("VOICE_INPUT_HOTKEY", "<f2>")


# ── 自动粘贴 ─────────────────────────────────────────────────────
AUTO_PASTE: bool = os.getenv("VOICE_INPUT_AUTO_PASTE", "1") == "1"


# ── STT provider ────────────────────────────────────────────────
# gateway: OpenAI-compatible /audio/transcriptions, recommended for shared release.
# doubao: Volcengine streaming ASR, used by the original development path.
STT_PROVIDER: str = os.getenv("VOICE_INPUT_STT_PROVIDER", "gateway").strip().lower()


# ── AI Gateway STT（OpenAI-compatible）───────────────────────────
AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://staging.song-ai-api.com/v1")
AI_API_KEY: str = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
AI_STT_MODEL: str = os.getenv("AI_STT_MODEL", "whisper-1")
AI_STT_LANGUAGE: str = os.getenv("AI_STT_LANGUAGE", "zh")


# ── 豆包 ASR（仅 STT_PROVIDER=doubao 时使用）──────────────────────
DOUBAO_APP_ID: str = os.getenv("DOUBAO_APP_ID", "")
DOUBAO_ACCESS_KEY: str = os.getenv("DOUBAO_ACCESS_KEY", "")
DOUBAO_RESOURCE_ID: str = os.getenv("DOUBAO_RESOURCE_ID", "volc.bigasr.sauc.duration")
DOUBAO_ASR_URL: str = os.getenv(
    "DOUBAO_ASR_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
)

# ── Context 注入 ─────────────────────────────────────────────────
# 豆包 v3 sauc bigmodel 的 request.corpus.context 字段（嵌套两层 + JSON-string，
# 详见 voice_input/docs/豆包语音-大模型流式ASR-API.pdf 第 28-29 页）。
# 字段名/嵌套结构由代码固定，不再可配；context_builder.build_doubao_corpus
# 负责生成符合 schema 的对象。
HOTWORDS_PATH: Path = Path(
    os.getenv("VOICE_INPUT_HOTWORDS", str(PROJECT_ROOT / "hotwords.txt"))
)
# 旧 deprecated（仅 build_context_blob 用，给 probe 工具兜底）
CONTEXT_MAX_CHARS: int = int(os.getenv("VOICE_INPUT_CONTEXT_MAX_CHARS", "1000"))

# 语音工具自身的连续输入上下文：最终识别/历史编辑后，把最近历史写入 context cache。
HISTORY_CONTEXT_ENABLED: bool = os.getenv("VOICE_INPUT_HISTORY_CONTEXT", "1") not in {
    "0",
    "false",
    "False",
}
HISTORY_CONTEXT_TURNS: int = int(os.getenv("VOICE_INPUT_HISTORY_CONTEXT_TURNS", "5"))
HISTORY_CONTEXT_MAX_CHARS: int = int(
    os.getenv("VOICE_INPUT_HISTORY_CONTEXT_MAX_CHARS", "1500")
)
HISTORY_CONTEXT_TTL_S: float = float(
    os.getenv("VOICE_INPUT_HISTORY_CONTEXT_TTL_S", "900")
)
