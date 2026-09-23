"""doc-searcher --self-check (Task 3.3)."""

import os
import subprocess
import sys
from pathlib import Path

from doc_searcher import cli, selfcheck
from doc_searcher.version import APP_VERSION

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def test_self_check_passes_and_writes_report(tmp_path, capsys):
    report = tmp_path / "self-check.txt"
    assert cli.main(["--self-check", "--report", str(report)]) == 0

    text = report.read_text(encoding="utf-8")
    assert text == capsys.readouterr().out
    assert f"version: {APP_VERSION}" in text
    assert "arch: " in text
    assert text.rstrip().endswith("result: ok")
    for backend in selfcheck.PARSER_BACKENDS:
        assert f"ok import {backend}" in text


def test_missing_asset_fails(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck, "resource_path", lambda relative: "/nonexistent/" + relative)
    assert selfcheck.run_self_check() == 1
    out = capsys.readouterr().out
    assert "FAIL asset assets/app_icon.png" in out
    assert "result: FAIL (asset assets/app_icon.png, asset assets/app_icon.ico)" in out


def test_missing_backend_fails(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "xlrd", None)
    assert selfcheck.run_self_check() == 1
    assert "FAIL import xlrd" in capsys.readouterr().out


def test_missing_fts5_fails(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck, "_fts5", lambda: (_ for _ in ()).throw(RuntimeError("no fts5")))
    assert selfcheck.run_self_check() == 1
    assert "FAIL sqlite fts5: RuntimeError: no fts5" in capsys.readouterr().out


def test_report_requires_self_check(capsys):
    try:
        cli.main(["--report", "x.txt"])
    except SystemExit as exc:
        assert exc.code == 2
    assert "--self-check" in capsys.readouterr().err


def test_self_check_creates_no_window_and_touches_no_user_data(tmp_path):
    data_dir = tmp_path / "data"
    code = (
        "import sys\n"
        "from doc_searcher.cli import main\n"
        "status = main(['--self-check'])\n"
        "from PySide6.QtWidgets import QApplication\n"
        "assert QApplication.instance() is None, 'self-check created a QApplication'\n"
        "sys.exit(status)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR), "DOC_SEARCHER_DATA_DIR": str(data_dir)}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not data_dir.exists()


def _load_verify_build():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_build.py"
    spec = importlib.util.spec_from_file_location("verify_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_build_reads_version_without_importing_the_package():
    assert _load_verify_build().expected_version() == APP_VERSION


def test_verify_build_rejects_unfrozen_and_wrong_arch(tmp_path, monkeypatch):
    verify_build = _load_verify_build()
    wrapper = tmp_path / "fake_app.py"
    wrapper.write_text(
        "import sys\nfrom doc_searcher.cli import main\nsys.exit(main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    runner = [sys.executable, str(wrapper)]
    real_run = subprocess.run

    def run_with_python(cmd, **kwargs):
        env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
        return real_run(runner + list(cmd[1:]), env=env, **kwargs)

    monkeypatch.setattr(verify_build.subprocess, "run", run_with_python)
    other_arch = "x86_64" if "arm64" in selfcheck.describe_build()[2] else "arm64"
    problems = verify_build.verify("fake", other_arch, APP_VERSION, timeout=120)
    assert any("not a frozen" in p for p in problems)
    assert any("architecture" in p for p in problems)
    assert not any("version" in p for p in problems)


def test_release_tag_check_runs_without_site_packages():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_release_tag.py"
    ok = subprocess.run(
        [sys.executable, "-S", str(script), f"v{APP_VERSION}"], capture_output=True, text=True
    )
    bad = subprocess.run(
        [sys.executable, "-S", str(script), "v0.0.0-mismatch"], capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr
    assert bad.returncode == 1
    assert f"expected tag 'v{APP_VERSION}'" in bad.stderr
