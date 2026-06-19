@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo 还没装好 — 请先双击 setup.bat 完成首次安装。
    echo.
    pause
    exit /b 1
)

echo ================================================
echo   语音输入工具
echo   浏览器会自动打开 http://127.0.0.1:8770/
echo   关闭本窗口或按 Ctrl+C 即可退出
echo ================================================
echo.

venv\Scripts\python -m voice_input.main --no-hotkey --no-paste
pause
