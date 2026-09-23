#!/usr/bin/env bash
# Purpose: Regenerate constraints/ci.txt, the hashed, universal lock used by CI and release builds.
# What the code does:
#   - Compiles pyproject.toml (extras dev, mcp, package) + constraints/build.in +
#     constraints/platform.in with `uv pip compile --universal --generate-hashes`.
#   - Checks that every pinned package has a wheel for each CI target (Linux x86_64,
#     Windows x64, macOS arm64/x86_64 on Python 3.10, 3.12, 3.14); jieba is sdist-only.
# Usage notes, dependencies, or assumptions:
#   - scripts/update_constraints.sh            keep existing pins where still valid
#   - scripts/update_constraints.sh --upgrade  move every pin to the newest allowed version
#   - scripts/update_constraints.sh --check    only run the wheel-availability check
#   - Needs uv (pip install uv). Review and commit the resulting diff like any code change.

set -euo pipefail
cd "$(dirname "$0")/.."

UV="${UV:-uv}"
mode="${1:-}"
lock="constraints/ci.txt"

if [[ "$mode" != "--check" ]]; then
    upgrade=()
    [[ "$mode" == "--upgrade" ]] && upgrade=(--upgrade)
    "$UV" pip compile pyproject.toml constraints/build.in constraints/platform.in \
        --extra dev --extra mcp --extra package \
        --universal --python-version 3.10 --generate-hashes \
        --no-emit-package doc-searcher \
        --custom-compile-command "scripts/update_constraints.sh" \
        ${upgrade[@]+"${upgrade[@]}"} --quiet -o "$lock"
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
status=0
# macOS 13 is the oldest macOS on GitHub-hosted runners; ubuntu-latest has glibc 2.39.
for target in x86_64-manylinux_2_39 x86_64-pc-windows-msvc aarch64-apple-darwin x86_64-apple-darwin; do
    for python in 3.10 3.12 3.14; do
        if MACOSX_DEPLOYMENT_TARGET=13.0 "$UV" pip compile "$lock" --python-platform "$target" \
            --python-version "$python" --only-binary :all: --no-binary jieba --quiet \
            -o "$scratch/out.txt" 2> "$scratch/err.txt"; then
            echo "[ok]   $target python $python"
        else
            echo "[FAIL] $target python $python"
            sed 's/^/       /' "$scratch/err.txt"
            status=1
        fi
    done
done
exit $status
