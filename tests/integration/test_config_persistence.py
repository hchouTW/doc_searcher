"""Crash safety and data-directory resolution for AppConfig (Task 2.1)."""

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from doc_searcher import config as config_module
from doc_searcher.config import AppConfig, ConfigError, get_default_data_dir

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(path))
    return path


def test_failed_write_keeps_previous_file(data_dir, monkeypatch):
    config = AppConfig()
    config.language = "en-US"
    before = config.config_path.read_bytes()

    def exploding_dump(data, handle, **kwargs):
        handle.write('{"language": "zh-')  # partial output, then failure
        raise OSError("disk full")

    monkeypatch.setattr(config_module.json, "dump", exploding_dump)
    config.language = "zh-TW"

    assert config.config_path.read_bytes() == before
    assert [p.name for p in data_dir.iterdir() if p.suffix == ".tmp"] == []


def test_failed_replace_keeps_previous_file(data_dir, monkeypatch):
    config = AppConfig()
    config.language = "en-US"
    before = config.config_path.read_bytes()
    monkeypatch.setattr(config_module.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("busy")))

    config.language = "zh-TW"

    assert config.config_path.read_bytes() == before
    assert [p.name for p in data_dir.iterdir() if p.suffix == ".tmp"] == []


def test_killed_writer_never_leaves_partial_json(tmp_path):
    """SIGKILL a process that saves continuously; the file must always parse."""
    path = tmp_path / "config.json"
    writer = (
        "import sys\n"
        "from doc_searcher.config import AppConfig\n"
        "config = AppConfig(sys.argv[1])\n"
        "config.settings.exclude_patterns = ['x' * 200] * 500\n"
        "config.save()\n"
        "print('ready', flush=True)\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    config.settings.search_debounce_ms = i % 2000\n"
        "    config.save()\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR), "DOC_SEARCHER_DATA_DIR": str(tmp_path / "data")}
    rng = random.Random(1234)
    for _ in range(8):
        proc = subprocess.Popen(
            [sys.executable, "-c", writer, str(path)], env=env, stdout=subprocess.PIPE, text=True
        )
        assert proc.stdout.readline().strip() == "ready"
        time.sleep(rng.uniform(0.01, 0.15))  # land the kill somewhere inside the save loop
        proc.kill()
        proc.wait()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["exclude_patterns"]) == 500


def test_env_override_that_cannot_be_written_is_an_actionable_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(blocker / "data"))
    with pytest.raises(ConfigError, match="DOC_SEARCHER_DATA_DIR"):
        get_default_data_dir()


def _writable_except(monkeypatch, blocked):
    real = config_module._is_writable_dir
    monkeypatch.setattr(
        config_module, "_is_writable_dir",
        lambda path: Path(path) not in blocked and real(path),
    )


def test_legacy_home_location_is_preferred(tmp_path, monkeypatch):
    monkeypatch.delenv("DOC_SEARCHER_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert get_default_data_dir() == tmp_path / "home" / ".doc_searcher"


def test_unwritable_home_falls_back_to_platform_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DOC_SEARCHER_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(config_module, "_source_checkout_data_dir", lambda: None)
    _writable_except(monkeypatch, {tmp_path / "home" / ".doc_searcher"})

    chosen = get_default_data_dir()

    assert chosen == config_module.platform_data_dir()
    assert tmp_path in chosen.parents


def test_nothing_writable_is_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DOC_SEARCHER_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(config_module, "_source_checkout_data_dir", lambda: None)
    monkeypatch.setattr(config_module, "_is_writable_dir", lambda path: False)
    with pytest.raises(ConfigError, match="DOC_SEARCHER_DATA_DIR"):
        get_default_data_dir()


def test_frozen_app_never_uses_a_bundle_relative_fallback(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config_module._source_checkout_data_dir() is None


def test_existing_checkout_data_dir_is_kept_for_source_runs(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    module = checkout / "src" / "doc_searcher" / "config.py"
    module.parent.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(module))
    assert config_module._source_checkout_data_dir() is None  # no data there yet

    (checkout / ".data").mkdir()
    (checkout / ".data" / "index.db").write_bytes(b"")
    assert config_module._source_checkout_data_dir() == checkout / ".data"

    (checkout / "pyproject.toml").unlink()  # looks like site-packages, not a checkout
    assert config_module._source_checkout_data_dir() is None


def test_cli_reports_missing_data_dir(tmp_path, monkeypatch, capsys):
    from doc_searcher import cli

    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(blocker / "data"))
    assert cli.main(["--dir", str(tmp_path), "--search", "x"]) == 3
    assert "DOC_SEARCHER_DATA_DIR" in capsys.readouterr().err
