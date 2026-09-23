# Purpose: Application configuration: typed settings, validation, and crash-safe persistence.
# What the code does:
#   - Settings/SearchFilters dataclasses hold every config.json field with its type and default.
#   - Loading validates each field independently: an invalid value falls back to its default
#     without discarding the other fields; unknown keys are kept so newer versions' settings
#     survive a save by this version.
#   - Unparseable config.json is copied to config.json.corrupt-<timestamp> before any save
#     can overwrite it.
#   - Saves write a sibling temp file, fsync it, and os.replace() it over config.json, so an
#     interrupted save leaves either the old or the new file, never a partial one.
#   - Resolves the data directory (see docs/adr/0001-data-directory.md):
#     DOC_SEARCHER_DATA_DIR > ~/.doc_searcher > existing source-checkout .data > OS data dir.
# Usage notes, dependencies, or assumptions:
#   - AppConfig(config_file=None) loads immediately; property setters save immediately.
#   - Raises ConfigError when no writable data directory exists.
#   - Standard library only.

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_EXTENSIONS = [
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".txt",
    ".md",
    ".csv",
]
DEFAULT_EXCLUDE_PATTERNS = [".git", "node_modules", "temp", "__pycache__"]
LANGUAGES = ("zh-TW", "en-US")
THEMES = ("light", "dark")
THEME_MODES = ("auto", "dark", "light")
MAX_DEBOUNCE_MS = 2000
DATA_DIR_ENV = "DOC_SEARCHER_DATA_DIR"


class ConfigError(Exception):
    """The configuration cannot be stored; the message says what the user can do."""


@dataclass
class SearchFilters:
    file_type: str = "all"
    date_field: str = "mtime"
    date_mode: str = "all"
    date_from: str = ""
    date_to: str = ""
    size_mode: str = "any"
    min_size: float = 0.0
    min_unit: str = "MB"
    max_size: float = 0.0
    max_unit: str = "MB"
    include_paths: str = ""
    match_case: bool = False
    whole_word: bool = False
    regex: bool = False


DEFAULT_SEARCH_FILTERS = asdict(SearchFilters())


@dataclass
class Settings:
    directories: List[str] = field(default_factory=list)
    include_subdirectories: bool = True
    enabled_extensions: List[str] = field(default_factory=lambda: DEFAULT_EXTENSIONS.copy())
    max_snippet_chars: int = 80
    db_path: str = ""
    theme: str = "light"
    theme_mode: str = "auto"
    language: str = "zh-TW"
    search_debounce_ms: int = 300
    last_updated_at: Optional[float] = None
    exclude_patterns: List[str] = field(default_factory=lambda: DEFAULT_EXCLUDE_PATTERNS.copy())
    search_filters: SearchFilters = field(default_factory=SearchFilters)


# ------------------------------------------------------------------ validation
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _string_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _validate_extensions(value: Any) -> Optional[List[str]]:
    items = _string_list(value)
    if not items:
        return None
    return [item.lower() if item.startswith(".") else "." + item.lower() for item in items]


def _validate_filters(value: Any, problems: List[str]) -> SearchFilters:
    filters = SearchFilters()
    if not isinstance(value, dict):
        problems.append("search_filters")
        return filters
    for spec in fields(SearchFilters):
        if spec.name not in value:
            continue
        item = value[spec.name]
        if spec.type is float and _is_number(item) and item >= 0:
            setattr(filters, spec.name, float(item))
        elif spec.type is bool and isinstance(item, bool):
            setattr(filters, spec.name, item)
        elif spec.type is str and isinstance(item, str):
            setattr(filters, spec.name, item)
        else:
            problems.append(f"search_filters.{spec.name}")
    return filters


def _validate(raw: Dict[str, Any], default_db_path: str) -> Tuple[Settings, List[str]]:
    """Build Settings from raw JSON data; returns the names of fields that fell back."""
    settings = Settings(db_path=default_db_path)
    problems: List[str] = []

    def take(name: str, value: Any, valid: bool) -> None:
        if valid:
            setattr(settings, name, value)
        else:
            problems.append(name)

    if "directories" in raw:
        directories = _string_list(raw["directories"])
        take("directories", directories, directories is not None)
    if "include_subdirectories" in raw:
        value = raw["include_subdirectories"]
        take("include_subdirectories", value, isinstance(value, bool))
    if "enabled_extensions" in raw:
        extensions = _validate_extensions(raw["enabled_extensions"])
        take("enabled_extensions", extensions, extensions is not None)
    if "max_snippet_chars" in raw:
        value = raw["max_snippet_chars"]
        take(
            "max_snippet_chars",
            value,
            isinstance(value, int) and not isinstance(value, bool) and value > 0,
        )
    if "db_path" in raw:
        value = raw["db_path"]
        take("db_path", value, isinstance(value, str) and bool(value.strip()))
    if "theme" in raw:
        take("theme", raw["theme"], raw["theme"] in THEMES)
    if "theme_mode" in raw:
        take("theme_mode", raw["theme_mode"], raw["theme_mode"] in THEME_MODES)
    if "language" in raw:
        take("language", raw["language"], raw["language"] in LANGUAGES)
    if "search_debounce_ms" in raw:
        value = raw["search_debounce_ms"]
        valid = isinstance(value, int) and not isinstance(value, bool)
        take("search_debounce_ms", min(MAX_DEBOUNCE_MS, max(0, value)) if valid else None, valid)
    if "last_updated_at" in raw:
        value = raw["last_updated_at"]
        valid = value is None or (_is_number(value) and value >= 0)
        take("last_updated_at", None if value is None else float(value) if valid else None, valid)
    if "exclude_patterns" in raw:
        patterns = _string_list(raw["exclude_patterns"])
        take("exclude_patterns", patterns, patterns is not None)
    if "search_filters" in raw:
        settings.search_filters = _validate_filters(raw["search_filters"], problems)
    return settings, problems


# -------------------------------------------------------------- data directory
def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test"):
            pass
        return True
    except OSError:
        return False


def _source_checkout_data_dir() -> Optional[Path]:
    """The legacy <checkout>/.data directory, only for source runs where it already has data."""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parents[2]  # src/doc_searcher/config.py -> checkout root
    candidate = root / ".data"
    if not (root / "pyproject.toml").is_file():
        return None  # installed package: never write into site-packages
    if (candidate / "config.json").exists() or (candidate / "index.db").exists():
        return candidate
    return None


def platform_data_dir() -> Path:
    """The OS-conventional per-user data directory (used when ~/.doc_searcher is unwritable)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "DocSearcher"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DocSearcher"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "doc_searcher"


def get_default_data_dir() -> Path:
    """Resolve the data directory; raises ConfigError if nothing writable is available."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        path = Path(override)
        if not _is_writable_dir(path):
            raise ConfigError(
                f"{DATA_DIR_ENV} points to {path}, which cannot be created or written. "
                f"Fix its permissions or point {DATA_DIR_ENV} at a writable folder."
            )
        return path

    legacy = Path.home() / ".doc_searcher"
    checkout = _source_checkout_data_dir()
    platform_dir = platform_data_dir()
    for candidate in (legacy, checkout, platform_dir):
        if candidate is not None and _is_writable_dir(candidate):
            return candidate
    raise ConfigError(
        f"No writable data folder: tried {legacy} and {platform_dir}. "
        f"Set {DATA_DIR_ENV} to a writable folder."
    )


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to a sibling temp file, fsync it, and atomically replace path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------- config
class AppConfig:
    """Manages application persistent configuration."""

    def __init__(self, config_file: Optional[Path] = None):
        data_dir = get_default_data_dir()
        self.config_path = Path(config_file) if config_file else data_dir / "config.json"
        self.default_db_path = str(data_dir / "index.db")
        self.settings = Settings(db_path=self.default_db_path)
        self._extra: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load and validate settings from JSON; invalid fields fall back to defaults."""
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("top level is not a JSON object")
        except (OSError, ValueError) as e:
            backup = self._backup_unreadable_file()
            print(f"[Config] Failed to load config: {e}. Using defaults; original kept at {backup}")
            return
        known = {spec.name for spec in fields(Settings)}
        self.settings, problems = _validate(raw, self.default_db_path)
        self._extra = {key: value for key, value in raw.items() if key not in known}
        if problems:
            print(f"[Config] Ignored invalid values, using defaults for: {', '.join(problems)}")

    def _backup_unreadable_file(self) -> Optional[Path]:
        backup = self.config_path.with_name(
            f"{self.config_path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        try:
            shutil.copy2(self.config_path, backup)
            return backup
        except OSError:
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {**self._extra, **asdict(self.settings)}

    def save(self) -> None:
        """Atomically save settings; on failure the previous file is left untouched."""
        try:
            atomic_write_json(self.config_path, self.to_dict())
        except OSError as e:
            print(f"[Config] Failed to save config to {self.config_path}: {e}", file=sys.stderr)

    @property
    def directories(self) -> List[str]:
        return self.settings.directories

    @directories.setter
    def directories(self, paths: List[str]):
        self.settings.directories = [str(Path(p).resolve()) for p in paths]
        self.save()

    def add_directory(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        if resolved not in self.settings.directories:
            self.settings.directories.append(resolved)
            self.save()
            return True
        return False

    def remove_directory(self, path: str) -> bool:
        resolved = str(Path(path).resolve())
        if resolved in self.settings.directories:
            self.settings.directories.remove(resolved)
            self.save()
            return True
        return False

    @property
    def include_subdirectories(self) -> bool:
        return self.settings.include_subdirectories

    @include_subdirectories.setter
    def include_subdirectories(self, enabled: bool):
        self.settings.include_subdirectories = bool(enabled)
        self.save()

    @property
    def enabled_extensions(self) -> List[str]:
        return self.settings.enabled_extensions

    @enabled_extensions.setter
    def enabled_extensions(self, exts: List[str]):
        self.settings.enabled_extensions = _validate_extensions(exts) or DEFAULT_EXTENSIONS.copy()
        self.save()

    @property
    def db_path(self) -> str:
        return self.settings.db_path

    @db_path.setter
    def db_path(self, path: str):
        self.settings.db_path = str(path)
        self.save()

    @property
    def language(self) -> str:
        return self.settings.language

    @language.setter
    def language(self, language: str):
        self.settings.language = language if language in LANGUAGES else "zh-TW"
        self.save()

    @property
    def theme_mode(self) -> str:
        return self.settings.theme_mode

    @theme_mode.setter
    def theme_mode(self, mode: str):
        self.settings.theme_mode = mode if mode in THEME_MODES else "auto"
        self.save()

    @property
    def last_updated_at(self) -> Optional[float]:
        return self.settings.last_updated_at

    @last_updated_at.setter
    def last_updated_at(self, timestamp: Optional[float]):
        self.settings.last_updated_at = timestamp
        self.save()

    @property
    def search_debounce_ms(self) -> int:
        return self.settings.search_debounce_ms

    @property
    def exclude_patterns(self) -> List[str]:
        return list(self.settings.exclude_patterns)

    @exclude_patterns.setter
    def exclude_patterns(self, patterns: List[str]):
        self.settings.exclude_patterns = [p.strip() for p in patterns if p.strip()]
        self.save()

    @property
    def search_filters(self) -> Dict[str, Any]:
        return asdict(self.settings.search_filters)

    @search_filters.setter
    def search_filters(self, filters: Dict[str, Any]):
        merged = {**asdict(self.settings.search_filters), **filters}
        self.settings.search_filters = _validate_filters(merged, [])
        self.save()
