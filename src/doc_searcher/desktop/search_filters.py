# Purpose: Translate the advanced-filter panel's values into search parameters, without Qt.
# What the code does:
#   - FilterState is a plain snapshot of the panel (MainWindow reads the widgets into it).
#   - to_search_kwargs() turns date presets/custom ranges and size presets/custom limits into
#     the numeric boundaries DocumentSearcher.search() accepts.
#   - validate() reports an invalid date range, regex syntax error, or invalid size range.
#   - signature() gives a comparable value for discarding results of superseded searches.
#   - query_error_key() maps SearchQueryError.code values to i18n keys.
# Usage notes, dependencies, or assumptions:
#   - No Qt, so every rule is unit-testable without a QApplication.

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from doc_searcher.search.regex_engine import RegexError, compile_user_regex

DAYS_BY_DATE_MODE = {"day": 1, "week": 7, "month": 30, "year": 365}
MB = 1024 * 1024
SIZE_PRESETS = {
    "small": (None, MB - 1),
    "medium": (MB, 10 * MB - 1),
    "large": (10 * MB, 100 * MB),
    "huge": (100 * MB + 1, None),
}
SIZE_UNITS = {"KB": 1024, "MB": MB, "GB": MB * 1024}

# SearchQueryError.code -> i18n key (regex_syntax keeps the re.error text as its message).
QUERY_ERROR_KEYS = {
    "unpaired_phrase": "error_unpaired_phrase",
    "operator_position": "error_operator_position",
    "repeated_operator": "error_repeated_operator",
    "filename_empty": "error_filename_empty",
    "filename_quotes": "error_filename_quotes",
    "regex_timeout": "error_regex_timeout",
}


@dataclass(frozen=True)
class FilterState:
    date_mode: str
    date_field: str
    date_from: date
    date_to: date
    size_mode: str
    min_size: float
    min_unit: str
    max_size: float
    max_unit: str
    include_paths: str
    match_case: bool
    whole_word: bool
    regex: bool


def _midnight(day: date) -> float:
    return datetime.combine(day, time()).timestamp()


def to_search_kwargs(
    state: FilterState, exclude_patterns: List[str], search_roots: List[str], now: float
) -> Dict[str, Any]:
    """Convert UI choices into DocumentSearcher.search() keyword arguments."""
    values: Dict[str, Any] = {
        "date_field": state.date_field,
        "include_paths": [path.strip() for path in state.include_paths.split(";") if path.strip()],
        "exclude_patterns": exclude_patterns,
        "search_roots": search_roots,
        "match_case": state.match_case,
        "whole_word": state.whole_word,
        "regex": state.regex,
    }
    if state.date_mode in DAYS_BY_DATE_MODE:
        values["modified_after"] = now - DAYS_BY_DATE_MODE[state.date_mode] * 86400
    elif state.date_mode == "custom":
        values["modified_after"] = _midnight(state.date_from)
        values["modified_before"] = _midnight(state.date_to + timedelta(days=1))

    if state.size_mode in SIZE_PRESETS:
        minimum, maximum = SIZE_PRESETS[state.size_mode]
        if minimum is not None:
            values["min_size"] = minimum
        if maximum is not None:
            values["max_size"] = maximum
    elif state.size_mode == "custom":
        if state.min_size > 0:
            values["min_size"] = round(state.min_size * SIZE_UNITS[state.min_unit])
        if state.max_size > 0:
            values["max_size"] = round(state.max_size * SIZE_UNITS[state.max_unit])
    return values


def validate(
    state: FilterState, query: str, search_kwargs: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """Return (kind, detail) for the first invalid input, or None.

    kind is an i18n key ("invalid_date_range", "invalid_size_range") or "regex" with the
    re.error message as detail.
    """
    if state.date_mode == "custom" and state.date_from > state.date_to:
        return "invalid_date_range", ""
    if state.regex and query:
        try:
            compile_user_regex(query)  # the same engine the search uses
        except RegexError as exc:
            return "regex", str(exc)
    if search_kwargs.get("min_size", 0) > search_kwargs.get("max_size", float("inf")):
        return "invalid_size_range", ""
    return None


def signature(
    state: FilterState, exclude_text: str, directories: List[str], include_subdirectories: bool
) -> Tuple[Any, ...]:
    """Stable value that changes whenever a filter that affects results changes."""
    return (
        state.date_mode,
        state.date_field,
        state.date_from.toordinal(),
        state.date_to.toordinal(),
        state.size_mode,
        state.min_size,
        state.max_size,
        state.min_unit,
        state.max_unit,
        state.include_paths,
        exclude_text,
        state.match_case,
        state.whole_word,
        state.regex,
        tuple(directories),
        include_subdirectories,
    )


def query_error_key(code: str) -> Optional[str]:
    return QUERY_ERROR_KEYS.get(code)
