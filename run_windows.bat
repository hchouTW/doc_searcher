@echo off
cd /d "%~dp0"
:: Checkouts installed before the src/ layout lack the doc_searcher package; reinstall them.
"venv\Scripts\python.exe" -c "import doc_searcher" >nul 2>&1
if %errorlevel% equ 0 (
    start "" "venv\Scripts\pythonw.exe" -m doc_searcher
) else (
    echo [!] 尚未完成安裝，正在啟動安裝程序...
    call install.bat
)
