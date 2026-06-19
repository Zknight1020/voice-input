@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   语音输入工具 — 首次安装
echo ================================================
echo.

REM ── 1. 检查 Python ─────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python。
    echo.
    echo 请先到 https://www.python.org/downloads/ 下载 Python 3.10 及以上版本，
    echo 安装时 *务必* 勾选 "Add Python to PATH"。
    echo 装完后重新运行本脚本。
    echo.
    pause
    exit /b 1
)

REM ── 2. 检查版本 ────────────────────────────────────
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.10 及以上。
    python --version
    echo.
    echo 请到 https://www.python.org/downloads/ 升级。
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo 检测到 %%v

REM ── 3. 创建虚拟环境 ────────────────────────────────
if not exist venv (
    echo.
    echo 正在创建虚拟环境（约需 30 秒）...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
) else (
    echo 虚拟环境已存在，跳过。
)

REM ── 4. 创建本地配置模板 ─────────────────────────────
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo.
        echo 已创建 .env。请先打开 .env 填入 AI Gateway 或豆包 ASR key。
    )
) else (
    echo .env 已存在，跳过配置模板。
)

REM ── 5. 升级 pip + 装依赖 ───────────────────────────
echo.
echo 正在安装依赖（约需 1-2 分钟）...
venv\Scripts\python -m pip install --upgrade pip --quiet
venv\Scripts\python -m pip install -r voice_input\requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络后重试。
    pause
    exit /b 1
)

echo.
echo ================================================
echo   安装完成！双击 start.bat 启动。
echo ================================================
pause
