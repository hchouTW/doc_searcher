@echo off
chcp 65001 >nul
title DocSearcher - Windows 自動識別自適應安裝程式

echo ===================================================================
echo       DocSearcher 文件內文關鍵字檢索系統 - 全自動自適應安裝精靈
echo ===================================================================
echo.

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

:: ==========================================
:: 1. 自動檢測作業系統版本 (Windows 10 / 11)
:: ==========================================
echo [*] 正在識別 Windows 作業系統版本與硬體架構...

set "WIN_BUILD=0"
for /f "usebackq tokens=*" %%b in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "[System.Environment]::OSVersion.Version.Build" 2^>nul`) do (
    set "WIN_BUILD=%%b"
)

if "%WIN_BUILD%"=="0" (
    :: 備用註冊表查詢
    for /f "tokens=3" %%i in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuildNumber 2^>nul') do set "WIN_BUILD=%%i"
)

set "OS_TAG=Windows"
set "IS_WIN11=0"
set "IS_WIN10=0"

if %WIN_BUILD% geq 22000 (
    set "OS_TAG=Windows 11"
    set "IS_WIN11=1"
    echo [✓] 成功識別作業系統：Windows 11 (Build %WIN_BUILD%)
    echo     啟用 Windows 11 現代 Fluent 介面適配與自動依賴策略。
) else if %WIN_BUILD% geq 10240 (
    set "OS_TAG=Windows 10"
    set "IS_WIN10=1"
    echo [✓] 成功識別作業系統：Windows 10 (Build %WIN_BUILD%)
    echo     啟用 Windows 10 相容性模式 (含 VC++ 運行庫自動檢測與備用安裝管道)。
) else (
    echo [!] 警告：檢測到較舊的 Windows 版本 (Build %WIN_BUILD%)。
    echo     本軟體建議運行於 Windows 10 1809 (Build 17763) 或更高版本。
)
echo.

:: ==========================================
:: 2. 檢測並自適應安裝 Python 環境
:: ==========================================
echo [*] 正在檢查 Python 執行環境...
set "PY_CMD="

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :PYTHON_READY
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_READY
)

echo [!] 未檢測到 Python 執行環境，正在自動執行自適應安裝...

:: 策略 A: 優先嘗試 winget (Windows 11 標配，部分 Win 10 支援)
where winget >nul 2>nul
if %errorlevel% equ 0 (
    echo [*] 檢測到系統支援 winget，正在安裝 Python 3.12...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% equ 0 goto :AFTER_PY_DOWNLOAD
)

:: 策略 B: Windows 10 / 無 winget 環境，透過 PowerShell 自動下載官方安裝包
echo [*] 透過 PowerShell TLS 1.2 自動下載官方 Python 3.12 安裝包...
set "PY_SETUP=%TEMP%\python_auto_setup.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
  "Write-Host '正在自 python.org 下載 Python 3.12.8...'; " ^
  "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%PY_SETUP%'"

if exist "%PY_SETUP%" (
    echo [*] 正在靜默安裝 Python 3.12 (自動配置 PATH 環境變數)...
    "%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
    del /f /q "%PY_SETUP%" >nul 2>nul
) else (
    echo [X] 下載 Python 安裝包失敗。
    echo     請手動至 https://www.python.org/ 下載安裝，並勾選「Add python.exe to PATH」。
    pause
    exit /b 1
)

:AFTER_PY_DOWNLOAD
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
where py >nul 2>nul && set "PY_CMD=py -3" || set "PY_CMD=python"

:PYTHON_READY
for /f "tokens=*" %%i in ('%PY_CMD% --version') do set "PY_VER=%%i"
echo [✓] Python 執行環境已就緒：%PY_VER%
echo.

:: ==========================================
:: 3. 自適應檢測 Visual C++ 運行庫 (Windows 10/11)
:: ==========================================
if not exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [*] 檢測到缺少微軟 Visual C++ 運行庫，正在自適應補齊...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
      "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%TEMP%\vc_redist.x64.exe'; " ^
      "Start-Process -FilePath '%TEMP%\vc_redist.x64.exe' -ArgumentList '/quiet /norestart' -Wait"
    echo [✓] Visual C++ 運行庫安裝完成。
) else (
    echo [✓] Visual C++ 運行庫狀態良好。
)
echo.

:: ==========================================
:: 4. 建立專屬虛擬環境 (venv)
:: ==========================================
if not exist "venv" (
    echo [*] 正在為 %OS_TAG% 建立獨立虛擬環境 (venv)...
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

:: ==========================================
:: 5. 安裝應用程式套件
:: ==========================================
echo [*] 正在安裝跨平台依賴套件 (PySide6, PyMuPDF, python-docx, openpyxl, jieba 等)...
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

:: ==========================================
:: 6. 生成 %OS_TAG% 專屬高解析度圖示
:: ==========================================
if not exist "assets\app_icon.ico" (
    echo [*] 正在產生應用程式圖示...
    python scripts\generate_icon.py
)

:: ==========================================
:: 7. 建立桌面捷徑 (使用 pythonw.exe，零黑視窗)
:: ==========================================
echo [*] 正在為 %OS_TAG% 建立桌面捷徑...

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
  "$Shortcut.Description = 'DocSearcher 本機多格式文件內文檢索系統 (%OS_TAG%)'; " ^
  "$Shortcut.Save()"

if exist "%SHORTCUT_PATH%" (
    echo [✓] 成功在您的 %OS_TAG% 桌面上建立「DocSearcher」捷徑！
)

echo.
echo ===================================================================
echo     恭喜！DocSearcher 已成功在您的 %OS_TAG% 上安裝完成！
echo ===================================================================
echo.
echo 您可以隨時：
echo  1. 雙擊桌面上的「DocSearcher」圖示啟動 (完全無 CMD 黑色終端彈出)
echo  2. 或在此視窗按 Enter 鍵立即啟動程式
echo.
set /p LAUNCH_NOW="是否立即啟動 DocSearcher？(Y/N) [預設: Y]: "
if /i "%LAUNCH_NOW%"=="N" goto :END

start "" "%TARGET_EXE%" "%SCRIPT_PATH%"

:END
exit /b 0
