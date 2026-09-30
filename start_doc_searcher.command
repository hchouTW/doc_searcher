#!/bin/bash
# macOS launcher for DocSearcher: double-click in Finder to start the GUI.
# Installs into ./venv on first run (or when the package is missing), like run_windows.bat.
cd "$(dirname "$0")" || exit 1

PY="venv/bin/python"

if [ ! -x "$PY" ] || ! "$PY" -c "import doc_searcher" >/dev/null 2>&1; then
    echo "[!] 尚未完成安裝，正在建立虛擬環境並安裝套件..."
    if [ ! -x "$PY" ]; then
        # Prefer the newest supported interpreter (3.10 ~ 3.14).
        BASE=""
        for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
            if command -v "$cand" >/dev/null 2>&1; then BASE="$cand"; break; fi
        done
        if [ -z "$BASE" ]; then
            echo "找不到 Python 3.10 以上版本，請先安裝：https://www.python.org/downloads/macos/"
            read -r -p "按 Enter 鍵關閉..." _
            exit 1
        fi
        "$BASE" -m venv venv || { read -r -p "建立虛擬環境失敗，按 Enter 鍵關閉..." _; exit 1; }
    fi
    "$PY" -m pip install --upgrade pip >/dev/null
    "$PY" -m pip install . || { read -r -p "安裝失敗，按 Enter 鍵關閉..." _; exit 1; }
fi

# Detach so the Terminal window can be closed without quitting the app.
nohup "$PY" -m doc_searcher >/dev/null 2>&1 &
disown
exit 0
