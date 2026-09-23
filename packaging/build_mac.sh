#!/usr/bin/env bash
# Purpose: Build the standalone macOS DocSearcher.app (no Python required on the target Mac).
# What the code does:
#   - Creates/reuses ./venv, installs the hashed lock (constraints/ci.txt, which includes
#     PyInstaller + Pillow), generates the icon if missing, runs doc_searcher_mac.spec, and zips
#     the bundle with ditto for distribution.
# Usage notes, dependencies, or assumptions:
#   - packaging/build_mac.sh   (needs python3 on the build machine only)
#   - Output: dist/DocSearcher.app and dist/DocSearcher-macOS-<arch>.zip
#   - The build is ad-hoc signed, not notarized; recipients must clear the quarantine flag
#     (see README "macOS Gatekeeper").

set -euo pipefail

# Run from the project root so venv/, dist/ and build/ live there.
cd "$(dirname "$0")/.."

if [[ ! -x venv/bin/python ]]; then
    echo "[*] Creating virtual environment..."
    python3 -m venv venv
fi

echo "[*] Installing dependencies..."
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet --require-hashes -r constraints/ci.txt

if [[ ! -f src/doc_searcher/assets/app_icon.png ]]; then
    echo "[*] Generating icon..."
    venv/bin/python scripts/generate_icon.py
fi

echo "[*] Building DocSearcher.app..."
venv/bin/python -m PyInstaller packaging/doc_searcher_mac.spec --clean -y

arch="$(uname -m)"
zip_path="dist/DocSearcher-macOS-${arch}.zip"
rm -f "$zip_path"
ditto -c -k --sequesterRsrc --keepParent dist/DocSearcher.app "$zip_path"

cat <<EOF

[✓] Build succeeded
    App: $(pwd)/dist/DocSearcher.app
    Zip: $(pwd)/${zip_path}

Unsigned build: on another Mac, clear the quarantine flag before first launch:
    xattr -cr /Applications/DocSearcher.app
EOF
