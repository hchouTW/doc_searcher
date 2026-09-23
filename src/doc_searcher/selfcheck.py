# Purpose: Non-interactive health check for installed and packaged (PyInstaller) builds.
# What the code does:
#   - Reports version, OS, CPU architecture, Python version, and whether the build is frozen.
#   - Verifies every parser backend and jieba (including its bundled dictionary) import and
#     work, SQLite has FTS5, PySide6 widgets import (no QApplication or window is created),
#     and bundled assets resolve.
#   - Returns 0 when every check passes and 1 otherwise; each line reads "ok|FAIL name: detail".
# Usage notes, dependencies, or assumptions:
#   - doc-searcher --self-check [--report FILE]; release CI runs it inside each built artifact.
#   - Never reads or writes config.json or index.db. jieba may write its cache to the temp dir.
#   - --report exists because the windowed Windows .exe has no usable stdout.

import functools
import importlib
import os
import platform
import sqlite3
import sys
from typing import Callable, List, Optional, Tuple

from doc_searcher.platform.os_detector import detect_os
from doc_searcher.platform.resource_path import resource_path
from doc_searcher.version import APP_VERSION

PARSER_BACKENDS = ("pymupdf", "docx", "pptx", "openpyxl", "xlrd", "olefile")
ASSETS = ("assets/app_icon.png", "assets/app_icon.ico")

CheckResult = Tuple[str, bool, str]


def _check(name: str, probe: Callable[[], str]) -> CheckResult:
    try:
        return name, True, probe()
    except Exception as exc:  # A self-check reports every failure instead of stopping.
        return name, False, f"{type(exc).__name__}: {exc}"


def _import(module: str) -> str:
    loaded = importlib.import_module(module)
    return getattr(loaded, "__version__", None) or getattr(loaded, "VERSION", None) or "imported"


def _jieba() -> str:
    from doc_searcher.search.text_helper import tokenize_for_fts

    tokens = tokenize_for_fts("中文全文檢索測試").split()
    if len(tokens) < 2:
        raise RuntimeError(f"unexpected tokenization {tokens!r}; dictionary missing?")
    return f"{len(tokens)} tokens"


def _fts5() -> str:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
        conn.execute("INSERT INTO probe VALUES ('self check')")
        if conn.execute("SELECT COUNT(*) FROM probe WHERE probe MATCH 'check'").fetchone()[0] != 1:
            raise RuntimeError("FTS5 query returned no match")
        return f"SQLite {sqlite3.sqlite_version}"
    finally:
        conn.close()


def _qt() -> str:
    import PySide6
    from PySide6 import QtGui, QtWidgets  # noqa: F401  (import only; no QApplication)

    return f"PySide6 {PySide6.__version__}"


def _asset(relative: str) -> Callable[[], str]:
    def probe() -> str:
        path = resource_path(relative)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return path

    return probe


def run_checks() -> List[CheckResult]:
    results = [
        _check(f"import {module}", functools.partial(_import, module)) for module in PARSER_BACKENDS
    ]
    results.append(_check("jieba dictionary", _jieba))
    results.append(_check("sqlite fts5", _fts5))
    results.append(_check("qt widgets", _qt))
    results += [_check(f"asset {relative}", _asset(relative)) for relative in ASSETS]
    return results


def describe_build() -> List[str]:
    os_info = detect_os()
    return [
        f"version: {APP_VERSION}",
        f"os: {os_info.os_name}",
        f"arch: {os_info.arch}",
        f"python: {platform.python_version()}",
        f"frozen: {bool(getattr(sys, 'frozen', False))}",
    ]


def run_self_check(report_path: Optional[str] = None) -> int:
    results = run_checks()
    lines = describe_build()
    lines += [f"{'ok' if passed else 'FAIL'} {name}: {detail}" for name, passed, detail in results]
    failed = [name for name, passed, _ in results if not passed]
    lines.append(f"result: {'FAIL (' + ', '.join(failed) + ')' if failed else 'ok'}")
    text = "\n".join(lines) + "\n"
    if report_path:
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    if sys.stdout is not None:  # None in the windowed Windows build
        sys.stdout.write(text)
        sys.stdout.flush()
    return 1 if failed else 0
