#!/usr/bin/env python3
# Purpose: Gate a packaged DocSearcher build before it is uploaded or published.
# What the code does:
#   - Runs `<executable> --self-check --report <tmp>` (a report file, because the windowed
#     Windows .exe has no stdout; Python's subprocess waits even for GUI-subsystem programs).
#   - Fails unless the self-check passed, the build is frozen, the version equals
#     src/doc_searcher/version.py, and the CPU architecture equals --arch.
# Usage notes, dependencies, or assumptions:
#   - python scripts/verify_build.py dist/DocSearcher.app/Contents/MacOS/DocSearcher --arch arm64
#   - python scripts/verify_build.py dist/DocSearcher.exe --arch x86_64
#   - Standard library only; reads the expected version as text so nothing heavy is imported.

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expected_version() -> str:
    text = (ROOT / "src" / "doc_searcher" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("APP_VERSION not found in src/doc_searcher/version.py")
    return match.group(1)


def verify(executable: str, arch: str, version: str, timeout: float) -> list:
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "self-check.txt"
        completed = subprocess.run(
            [executable, "--self-check", "--report", str(report_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    print(report or completed.stdout or "(no self-check report written)")
    fields = dict(
        line.split(": ", 1)
        for line in report.splitlines()
        if ": " in line and " " not in line.split(": ", 1)[0]
    )
    problems = []
    if completed.returncode != 0:
        problems.append(f"self-check exited with {completed.returncode}")
    if fields.get("result") != "ok":
        problems.append(f"self-check result: {fields.get('result', 'missing')}")
    if fields.get("frozen") != "True":
        problems.append("executable is not a frozen (PyInstaller) build")
    if fields.get("version") != version:
        problems.append(f"version {fields.get('version')!r} != expected {version!r}")
    if fields.get("arch") != arch:
        problems.append(f"architecture {fields.get('arch')!r} != expected {arch!r}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable")
    parser.add_argument("--arch", required=True, choices=["arm64", "x86_64"])
    parser.add_argument("--version", default=None, help="defaults to src/doc_searcher/version.py")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    problems = verify(args.executable, args.arch, args.version or expected_version(), args.timeout)
    for problem in problems:
        print(f"[verify_build] FAIL: {problem}", file=sys.stderr)
    if not problems:
        print(f"[verify_build] ok: {args.executable}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
