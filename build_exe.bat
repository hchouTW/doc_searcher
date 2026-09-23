@echo off
chcp 65001 >nul
title DocSearcher - Windows 11 打包編譯程式

echo =======================================================
echo        DocSearcher 獨立執行檔 (.exe) 編譯建置
echo =======================================================
echo.

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

if not exist "venv\Scripts\activate.bat" (
    echo [*] 尚未找到虛擬環境，正在先執行基礎安裝...
    call install_windows.bat
)

call "venv\Scripts\activate.bat"

echo [*] 正在確認 PyInstaller 打包工具...
pip install pyinstaller --quiet

if not exist "assets\app_icon.ico" (
    echo [*] 產生圖示...
    python scripts\generate_icon.py
)

echo.
echo [*] 開始編譯 DocSearcher.exe 獨立免安裝執行檔 (請稍候 1~2 分鐘)...
pyinstaller doc_searcher.spec --clean -y

if %errorlevel% equ 0 (
    echo.
    echo =======================================================
    echo [✓] 編譯成功！
    echo.
    echo 執行檔已輸出至：
    echo %APP_DIR%dist\DocSearcher.exe
    echo.
    echo 該檔案為完全獨立的免安裝版本，可直接複製到任何 Windows 11 電腦執行！
    echo =======================================================
) else (
    echo.
    echo [X] 編譯過程發生錯誤，請檢查輸出訊息。
)

echo.
pause
