"""Import-contract tests: light modules stay light, and missing dependencies are named."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def run_python(code, *flags):
    return subprocess.run(
        [sys.executable, *flags, "-c", code], cwd=ROOT, capture_output=True, text=True
    )


def test_version_imports_without_site_packages():
    result = run_python("from core.version import APP_VERSION; print(APP_VERSION)", "-S")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_version_import_does_not_load_heavy_modules():
    code = (
        "import sys, core.version; "
        "heavy = {'PySide6', 'jieba', 'sqlite3', 'pymupdf', 'parsers', 'core.database'}; "
        "print(sorted(heavy & set(sys.modules)))"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize("module, missing", [
    ("core.searcher", "jieba"),
    ("core.indexer", "jieba"),
    ("ui.worker", "PySide6"),
])
def test_missing_dependency_is_reported_by_name(module, missing):
    code = (
        f"import sys; sys.modules[{missing!r}] = None\n"
        "try:\n"
        f"    import {module}\n"
        "except ImportError as exc:\n"
        "    print(type(exc).__name__, exc.name)\n"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    kind, name = result.stdout.split()
    assert kind == "ModuleNotFoundError"
    assert name.split(".")[0] == missing


def test_production_code_does_not_modify_sys_path():
    offenders = [
        str(path.relative_to(ROOT))
        for pattern in ("core/*.py", "parsers/*.py", "ui/*.py", "utils/*.py", "*.py")
        for path in ROOT.glob(pattern)
        if "sys.path" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
