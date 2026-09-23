@echo off
chcp 65001 >nul
title DocSearcher - Windows 10 安裝精靈

echo =======================================================
echo     DocSearcher 文件內文關鍵字檢索系統 - Windows 10 安裝
echo =======================================================
echo.

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

:: 1. 檢查 Python 執行環境
echo [*] 正在檢查 Python 執行環境...
set "PY_CMD="

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :PYTHON_FOUND
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_FOUND
)

:: 2. Windows 10 自動安裝 Python (優先 winget，次選 PowerShell 下載官方安裝包)
echo [!] 未檢測到 Python 執行環境，正在為 Windows 10 自動準備...

where winget >nul 2>nul
if %errorlevel% equ 0 (
    echo [*] 檢測到 winget，正在透過 winget 安裝 Python 3.12...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% equ 0 goto :AFTER_PY_INSTALL
)

echo [*] 未安裝 winget，正在透過 PowerShell 自動下載官方 Python 3.12 安裝程式...
set "PY_INSTALLER=%TEMP%\python_installer_win10.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
  "Write-Host '正在下載 Python 3.12.8 官方安裝程式...'; " ^
  "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%PY_INSTALLER%'"

if exist "%PY_INSTALLER%" (
    echo [*] 正在靜默安裝 Python 3.12 (自動加入 PATH 環境變數)...
    "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
    del /f /q "%PY_INSTALLER%" >nul 2>nul
) else (
    echo [X] 下載 Python 安裝程式失敗。
    echo     請手動前往 https://www.python.org/ 下載安裝 Python，
    echo     並在安裝畫面勾選「Add python.exe to PATH」後重新執行本腳本。
    pause
    exit /b 1
)

:AFTER_PY_INSTALL
:: 重新讀取 PATH 環境變數
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
where py >nul 2>nul && set "PY_CMD=py -3" || set "PY_CMD=python"

:PYTHON_FOUND
for /f "tokens=*" %%i in ('%PY_CMD% --version') do set "PY_VER=%%i"
echo [✓] 檢測到 Python: %PY_VER%
echo.

:: 3. 檢查 Visual C++ 2015-2022 運行庫 (Windows 10 依賴)
echo [*] 檢查 Visual C++ 運行庫...
if not exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [!] 檢測到缺少 VC++ 運行庫，正在為 Windows 10 下載安裝...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
      "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%TEMP%\vc_redist.x64.exe'; " ^
      "Start-Process -FilePath '%TEMP%\vc_redist.x64.exe' -ArgumentList '/quiet /norestart' -Wait"
    echo [✓] VC++ 運行庫安裝完成。
) else (
    echo [✓] VC++ 運行庫已就緒。
)
echo.

:: 4. 建立獨立虛擬環境 (venv)
if not exist "venv" (
    echo [*] 正在建立 Python 專屬虛擬環境 (venv)...
    %PY_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo [X] 建立虛擬環境失敗。
        pause
        exit /b 1
    )
    echo [✓] 虛擬環境建立完成。
) else (
    echo [✓] 虛擬環境已存在。
)
echo.

:: 5. 安裝必要套件
echo [*] 正在安裝必要套件 (PySide6, PyMuPDF, python-docx, openpyxl, jieba 等)...
call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [X] 套件安裝失敗，請檢查網路連線。
    pause
    exit /b 1
)
echo [✓] 套件安裝成功。
echo.

:: 6. 生成圖示資源
if not exist "assets\app_icon.ico" (
    echo [*] 正在產生應用程式圖示...
    python scripts\generate_icon.py
)

:: 7. 建立 Windows 10 桌面捷徑 (使用 pythonw.exe 無黑視窗啟動)
echo [*] 正在建立 Windows 10 桌面捷徑...

set "TARGET_EXE=%APP_DIR%venv\Scripts\pythonw.exe"
set "SCRIPT_PATH=%APP_DIR%main.py"
set "ICON_PATH=%APP_DIR%assets\app_icon.ico"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\DocSearcher.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$WshShell = New-Object -ComObject WScript.Shell; " ^
  "$Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); " ^
  "$Shortcut.TargetPath = '%TARGET_EXE%'; " ^
  "$Shortcut.Arguments = '\"%SCRIPT_PATH%\"'; " ^
  "$Shortcut.WorkingDirectory = '%APP_DIR%'; " ^
  "$Shortcut.IconLocation = '%ICON_PATH%'; " ^
  "$Shortcut.Description = 'DocSearcher 本機多格式文件內文檢索系統'; " ^
  "$Shortcut.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [✓] 成功在您的 Windows 10 桌面上建立「DocSearcher」捷徑！
)

echo.
echo =======================================================
echo             DocSearcher Windows 10 安裝完成！
echo =======================================================
echo.
echo 您可以隨時：
echo  1. 雙擊桌面上的「DocSearcher」捷徑圖示啟動
echo  2. 或在此視窗按 Enter 立即為您啟動程式
echo.
set /p START_NOW="是否立即啟動 DocSearcher？(Y/N) [預設: Y]: "
if /i "%START_NOW%"=="N" goto :END

start "" "%TARGET_EXE%" "%SCRIPT_PATH%"

:END
exit /b 0
