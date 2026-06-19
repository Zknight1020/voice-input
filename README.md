# 语音输入工具

这是一个本地运行的中文语音转文字工具。启动后会打开浏览器页面，点按钮录音，识别结果会出现在历史记录里，可以复制到 Codex、Claude Code、Cursor、微信、文档或任何输入框。

这份 public 版本不包含任何 key。默认推荐使用 **AI Gateway** 的 OpenAI-compatible `/audio/transcriptions` 接口；如果你有豆包官方 ASR key，也可以切到豆包流式模式。

## 快速开始

### Windows

1. 解压 `VoiceInput.zip`。
2. 双击 `setup.bat`，等待依赖安装完成。
3. 打开 `.env`，填入你的 AI Gateway key 或豆包官方 key。
4. 双击 `start.bat`。
5. 浏览器会打开 `http://127.0.0.1:8770/`。
6. 在页面里选麦克风，点“开始录音”，说完再点“结束录音”。
7. 在历史记录里点“复制”，粘贴到你要输入的地方。

第一次运行前需要安装 Python 3.10 或更新版本。安装 Python 时请勾选 `Add Python to PATH`。

### macOS / Linux

```bash
pip install -r voice_input/requirements.txt
python -m voice_input.main --no-hotkey --no-paste
```

macOS 如果缺少 PortAudio：

```bash
brew install portaudio
```

## 配置

先复制配置模板：

```bash
cp .env.example .env
```

Windows 下运行 `setup.bat` 时会自动复制一份 `.env.example` 到 `.env`。不要把 `.env` 提交到 git 或发到公开群。

### 方式 A：AI Gateway / OpenAI-compatible STT

```bash
VOICE_INPUT_STT_PROVIDER=gateway
AI_BASE_URL=https://staging.song-ai-api.com/v1
AI_API_KEY=...
AI_STT_MODEL=whisper-1
AI_STT_LANGUAGE=zh
VOICE_INPUT_HISTORY_CONTEXT=1
```

字段说明：

| 字段 | 说明 |
|---|---|
| `VOICE_INPUT_STT_PROVIDER` | `gateway` |
| `AI_BASE_URL` | OpenAI-compatible 网关地址 |
| `AI_API_KEY` | 你的网关 key |
| `AI_STT_MODEL` | 语音转文字模型，默认 `whisper-1` |
| `AI_STT_LANGUAGE` | 默认 `zh` |
| `VOICE_INPUT_HISTORY_CONTEXT` | 是否把最近语音历史作为下一次识别的上下文 |

如果 `staging.song-ai-api.com` 临时不可用，可以把 `AI_BASE_URL` 改成：

```bash
AI_BASE_URL=https://song-ai-api.com/v1
```

### 方式 B：豆包官方流式 ASR

如果你有火山引擎/豆包官方 ASR key，可以这样配置：

```bash
VOICE_INPUT_STT_PROVIDER=doubao
DOUBAO_APP_ID=...
DOUBAO_ACCESS_KEY=...
DOUBAO_RESOURCE_ID=volc.bigasr.sauc.duration
DOUBAO_ASR_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
VOICE_INPUT_HISTORY_CONTEXT=1
```

豆包模式会边录边出 partial 文本；网关模式是在结束录音后一次性识别，更适合轻量分享版。

## 热词

热词在 `voice_input/hotwords.txt`，一行一个词。建议把你常说但 ASR 容易听错的内容放进去：

- 人名、公司名、项目名
- 仓库名、文件名、函数名
- 常用英文缩写
- 论文、模型、产品名

修改后不需要重启；下一次录音会重新读取。

例子：

```text
乔子椋
乔博
Codex
Claude Code
MCP
OpenAI-compatible
```

## 上下文能力

这个工具有三种上下文来源。

**1. 热词上下文**  
来自 `voice_input/hotwords.txt`，适合纠正专名。

**2. 最近语音历史**  
页面里的“使用最近历史作为上下文”默认开启。上一段识别结果、以及你在历史区编辑保存后的文本，会作为下一次识别的参考。

历史记录存在本机：

| 系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\\VoiceInput\\history.sqlite3` |
| macOS | `~/Library/Application Support/VoiceInput/history.sqlite3` |
| Linux | `~/.local/share/VoiceInput/history.sqlite3` |

编辑历史会覆盖数据库里的 `transcripts.text` 字段；也就是说，保存后的版本会成为后续上下文。

**3. Claude Code hook 上下文（可选）**  
如果你用 Claude Code / Codex 类工具写代码，可以把最近几轮对话推给语音工具。这样你刚刚在聊的模块、人名、函数名，会影响下一次语音识别。

发布包里带了 `voice_input_context.py`。安装方式：

1. 把 `voice_input_context.py` 放到：

```bash
~/.claude/hooks/voice_input_context.py
```

2. 给它执行权限：

```bash
chmod +x ~/.claude/hooks/voice_input_context.py
```

3. 在 `~/.claude/settings.json` 里加入：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/env python3 ~/.claude/hooks/voice_input_context.py >/dev/null 2>&1 || true",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/env python3 ~/.claude/hooks/voice_input_context.py >/dev/null 2>&1 || true",
            "async": true
          }
        ]
      }
    ]
  }
}
```

hook 的行为：

- `UserPromptSubmit`：你发出消息后推一次上下文。
- `Stop`：AI 回复结束后再推一次上下文。
- 只推最近几轮纯文本，会过滤代码块、tool 结果、URL、长路径。
- voice_input 没启动时静默失败，不影响 Claude Code。
- 推送到本机 `http://127.0.0.1:8770/api/context`，默认缓存 90 秒。

可选环境变量：

```bash
VOICE_INPUT_URL=http://127.0.0.1:8770
VOICE_INPUT_CONTEXT_MAX_CHARS=1500
VOICE_INPUT_CONTEXT_TURNS=4
VOICE_INPUT_CONTEXT_TTL_S=90
VOICE_INPUT_CONTEXT_DEBUG=1
```

## Airloop 调试工具（macOS）

发布包里带了 `voice_input/scripts/air_loop.py`。它会走真实空气回路：

```text
say 播放测试句 -> 扬声器 -> 麦克风录音 -> 当前 STT provider 识别
```

这个工具适合排查“网关能用，但真实麦克风/扬声器链路是否正常”。它只支持 macOS，并要求安装 `ffmpeg`：

```bash
brew install ffmpeg
```

先查看 avfoundation 音频输入设备编号：

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

然后运行：

```bash
python -m voice_input.scripts.air_loop --audio-device 11
```

常用参数：

```bash
python -m voice_input.scripts.air_loop \
  --audio-device 11 \
  --text "你好乔博，这是语音输入工具测试" \
  --duration 5
```

它会打印两次识别结果：无上下文和带上下文。airloop 使用当前 `.env` 里的 `VOICE_INPUT_STT_PROVIDER`，所以能测同一条实际识别链路。

## 常见问题

**浏览器打不开 / 提示无法连接**  
等 5 秒刷新，或确认 `start.bat` 的黑色窗口还开着。

**录音结束后提示缺少 key**  
打开 `.env`，确认已填 `AI_API_KEY`，或已切换到 `VOICE_INPUT_STT_PROVIDER=doubao` 并填好 `DOUBAO_APP_ID` / `DOUBAO_ACCESS_KEY`。

**点“开始录音”没反应**  
检查浏览器是否允许麦克风权限。

**识别不准**  
先改 `voice_input/hotwords.txt`，把常用专名放进去；再打开“使用最近历史作为上下文”。

**没有声音 / 麦克风不对**  
在页面顶部选择输入设备。如果列表不对，点“刷新”。

**如何退出**  
关闭 `start.bat` 打开的命令行窗口，或按 `Ctrl+C`。

## 隐私边界

- 音频会发送到你在 `.env` 配置的 STT provider：AI Gateway 或豆包官方 ASR。
- 历史文本存在本机 SQLite。
- `.env` 里的 key 是个人凭据，不要提交到 git；public GitHub 版本只提供 `.env.example`。
- 不建议录入密码、token、客户敏感信息或未脱敏商业机密。

## 验证

开发机上可以跑：

```bash
.venv/bin/pytest voice_input/tests/ -q
```
