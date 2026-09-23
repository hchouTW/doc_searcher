"""Import-contract tests: light modules stay light, and missing dependencies are named."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SRC_DIR = ROOT / "src"


def run_python(code, *flags):
    """Run code in a fresh interpreter that can see only src/ (plus site-packages unless -S)."""
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    return subprocess.run(
        [sys.executable, *flags, "-c", code],
        cwd=SRC_DIR.parent / "tests",
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.skipif(
    not SRC_DIR.is_dir(), reason="needs the source checkout (-S hides site-packages)"
)
def test_version_imports_without_site_packages():
    result = run_python("from doc_searcher.version import APP_VERSION; print(APP_VERSION)", "-S")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_version_import_does_not_load_heavy_modules():
    code = (
        "import sys, doc_searcher.version; "
        "heavy = {'PySide6', 'jieba', 'sqlite3', 'pymupdf', 'doc_searcher.parsers', 'doc_searcher.storage.database'}; "
        "print(sorted(heavy & set(sys.modules)))"
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize(
    "module, missing",
    [
        ("doc_searcher.search.searcher", "jieba"),
        ("doc_searcher.indexing.indexer", "jieba"),
        ("doc_searcher.desktop.worker", "PySide6"),
    ],
)
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
        str(path.relative_to(SRC_DIR))
        for path in SRC_DIR.rglob("*.py")
        if "sys.path" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_package_imports_without_repository_root_on_sys_path():
    """python -I ignores PYTHONPATH and cwd; the package must come from an install."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import doc_searcher, doc_searcher.cli; print(doc_searcher.__file__)",
        ],
        cwd=SRC_DIR.parent.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "No module named 'doc_searcher'" in result.stderr:
        pytest.skip("doc_searcher is not installed in this interpreter (pip install -e .)")
    assert result.returncode == 0, result.stderr
    assert "doc_searcher" in result.stdout
