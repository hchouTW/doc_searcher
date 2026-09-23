@echo off
cd /d "%~dp0"
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" "main.py"
) else (
    echo [!] 尚未完成安裝，正在啟動安裝程序...
    call install.bat
)
