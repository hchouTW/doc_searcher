"""Validation and loading rules for AppConfig (Task 2.1)."""

import json

import pytest

from doc_searcher.config import DEFAULT_SEARCH_FILTERS, AppConfig
from fixtures.legacy_data import write_legacy_config


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(tmp_path / "data"))


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_legacy_config_loads_without_visible_changes(tmp_path):
    path = tmp_path / "config.json"
    legacy = write_legacy_config(path, ["/legacy/root_a", "/legacy/root_b"])

    config = AppConfig(path)

    assert config.directories == legacy["directories"]
    assert config.include_subdirectories is True
    assert config.enabled_extensions == legacy["enabled_extensions"]
    assert config.db_path == legacy["db_path"]
    assert config.language == "en-US"
    assert config.search_debounce_ms == 450
    assert config.last_updated_at == legacy["last_updated_at"]
    assert config.exclude_patterns == legacy["exclude_patterns"]
    assert config.search_filters == {**DEFAULT_SEARCH_FILTERS, **legacy["search_filters"]}

    config.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    for key, value in legacy.items():
        if key != "search_filters":
            assert saved[key] == value, key
    assert saved["search_filters"] == {**DEFAULT_SEARCH_FILTERS, **legacy["search_filters"]}


def test_invalid_fields_fall_back_individually(tmp_path, capsys):
    path = write(tmp_path / "config.json", {
        "directories": ["/kept", 3, ""],
        "include_subdirectories": "yes",
        "language": "fr-FR",
        "search_debounce_ms": "fast",
        "theme_mode": "dark",
        "last_updated_at": "yesterday",
        "exclude_patterns": "node_modules",
        "enabled_extensions": [],
        "max_snippet_chars": True,
        "search_filters": {"regex": "true", "min_size": 2, "file_type": "pdf", "max_size": -1},
    })

    config = AppConfig(path)

    assert config.directories == ["/kept"]
    assert config.include_subdirectories is True
    assert config.language == "zh-TW"
    assert config.search_debounce_ms == 300
    assert config.theme_mode == "dark"
    assert config.last_updated_at is None
    assert config.exclude_patterns == [".git", "node_modules", "temp", "__pycache__"]
    assert config.enabled_extensions[0] == ".pdf"
    assert config.settings.max_snippet_chars == 80
    filters = config.search_filters
    assert filters["regex"] is False
    assert filters["min_size"] == 2.0
    assert filters["file_type"] == "pdf"
    assert filters["max_size"] == 0.0
    reported = capsys.readouterr().out
    for name in ("include_subdirectories", "language", "search_filters.regex", "search_filters.max_size"):
        assert name in reported


def test_debounce_is_clamped(tmp_path):
    assert AppConfig(write(tmp_path / "a.json", {"search_debounce_ms": 99999})).search_debounce_ms == 2000
    assert AppConfig(write(tmp_path / "b.json", {"search_debounce_ms": -5})).search_debounce_ms == 0


def test_unknown_keys_survive_a_save(tmp_path):
    path = write(tmp_path / "config.json", {"language": "en-US", "future_setting": {"x": 1}})
    config = AppConfig(path)
    config.language = "zh-TW"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_setting"] == {"x": 1}
    assert saved["language"] == "zh-TW"


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", ""])
def test_unreadable_file_is_backed_up_before_being_replaced(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")

    config = AppConfig(path)
    assert config.directories == []
    config.language = "en-US"  # triggers a save over the unreadable file

    backups = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == "en-US"


def test_setters_normalize_values(tmp_path):
    config = AppConfig(tmp_path / "config.json")
    config.enabled_extensions = ["PDF", ".TXT", " "]
    assert config.enabled_extensions == [".pdf", ".txt"]
    config.theme_mode = "neon"
    assert config.theme_mode == "auto"
    config.search_filters = {"regex": True, "bogus": 1}
    assert config.search_filters["regex"] is True
    assert "bogus" not in config.search_filters
