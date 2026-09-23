# Purpose: Application configuration management and persistence.
# What the code does:
#   - Loads and saves locale, directories, theme, exclusion rules, and active search filters.
#   - Dynamically determines storage directory (home dir ~/.doc_searcher or project local fallback).
# Usage notes, dependencies, or assumptions:
#   - Uses standard library json and pathlib.

import json
import os
from pathlib import Path
from typing import List, Dict, Any

DEFAULT_EXTENSIONS = [".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".txt", ".md", ".csv"]
DEFAULT_EXCLUDE_PATTERNS = [".git", "node_modules", "temp", "__pycache__"]
DEFAULT_SEARCH_FILTERS = {
    "file_type": "all",
    "date_field": "mtime",
    "date_mode": "all",
    "date_from": "",
    "date_to": "",
    "size_mode": "any",
    "min_size": 0.0,
    "min_unit": "MB",
    "max_size": 0.0,
    "max_unit": "MB",
    "include_paths": "",
    "match_case": False,
    "whole_word": False,
    "regex": False,
}


def get_default_data_dir() -> Path:
    """Determine data directory with automatic sandbox/permission fallback."""
    if "DOC_SEARCHER_DATA_DIR" in os.environ:
        p = Path(os.environ["DOC_SEARCHER_DATA_DIR"])
        p.mkdir(parents=True, exist_ok=True)
        return p

    home_candidate = Path.home() / ".doc_searcher"
    try:
        home_candidate.mkdir(parents=True, exist_ok=True)
        # Verify writability
        test_file = home_candidate / ".write_test"
        test_file.touch()
        test_file.unlink()
        return home_candidate
    except (PermissionError, OSError):
        # Fallback to project-local data directory
        local_candidate = Path(__file__).resolve().parent.parent / ".data"
        local_candidate.mkdir(parents=True, exist_ok=True)
        return local_candidate


class AppConfig:
    """Manages application persistent configuration."""

    def __init__(self, config_file: Path = None):
        data_dir = get_default_data_dir()
        self.config_path = config_file or (data_dir / "config.json")
        self.default_db_path = str(data_dir / "index.db")

        self.data: Dict[str, Any] = {
            "directories": [],
            "include_subdirectories": True,
            "enabled_extensions": DEFAULT_EXTENSIONS.copy(),
            "max_snippet_chars": 80,
            "db_path": self.default_db_path,
            "theme": "light",
            "language": "zh-TW",
            "search_debounce_ms": 300,
            "exclude_patterns": DEFAULT_EXCLUDE_PATTERNS.copy(),
            "search_filters": DEFAULT_SEARCH_FILTERS.copy(),
        }
        self.load()

    def load(self) -> None:
        """Load settings from JSON file if exists."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.data.update(saved)
            except Exception as e:
                print(f"[Config] Failed to load config: {e}")

    def save(self) -> None:
        """Save settings to JSON file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Config] Failed to save config: {e}")

    @property
    def directories(self) -> List[str]:
        return self.data.get("directories", [])

    @directories.setter
    def directories(self, paths: List[str]):
        self.data["directories"] = [str(Path(p).resolve()) for p in paths]
        self.save()

    def add_directory(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        if resolved not in self.data["directories"]:
            self.data["directories"].append(resolved)
            self.save()
            return True
        return False

    def remove_directory(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        if resolved in self.data["directories"]:
            self.data["directories"].remove(resolved)
            self.save()
            return True
        return False

    @property
    def include_subdirectories(self) -> bool:
        return self.data.get("include_subdirectories", True)

    @include_subdirectories.setter
    def include_subdirectories(self, enabled: bool):
        self.data["include_subdirectories"] = enabled
        self.save()

    @property
    def enabled_extensions(self) -> List[str]:
        return self.data.get("enabled_extensions", DEFAULT_EXTENSIONS)

    @enabled_extensions.setter
    def enabled_extensions(self, exts: List[str]):
        self.data["enabled_extensions"] = exts
        self.save()

    @property
    def db_path(self) -> str:
        return self.data.get("db_path", self.default_db_path)

    @property
    def language(self) -> str:
        language = self.data.get("language", "zh-TW")
        return language if language in {"zh-TW", "en-US"} else "zh-TW"

    @language.setter
    def language(self, language: str):
        self.data["language"] = language if language in {"zh-TW", "en-US"} else "zh-TW"
        self.save()

    @property
    def search_debounce_ms(self) -> int:
        try:
            return min(2000, max(0, int(self.data.get("search_debounce_ms", 300))))
        except (TypeError, ValueError):
            return 300

    @property
    def exclude_patterns(self) -> List[str]:
        value = self.data.get("exclude_patterns", DEFAULT_EXCLUDE_PATTERNS)
        return [str(item).strip() for item in value if str(item).strip()]

    @exclude_patterns.setter
    def exclude_patterns(self, patterns: List[str]):
        self.data["exclude_patterns"] = [p.strip() for p in patterns if p.strip()]
        self.save()

    @property
    def search_filters(self) -> Dict[str, Any]:
        filters = DEFAULT_SEARCH_FILTERS.copy()
        saved = self.data.get("search_filters", {})
        if isinstance(saved, dict):
            filters.update(saved)
        return filters

    @search_filters.setter
    def search_filters(self, filters: Dict[str, Any]):
        merged = DEFAULT_SEARCH_FILTERS.copy()
        merged.update(filters)
        self.data["search_filters"] = merged
        self.save()
