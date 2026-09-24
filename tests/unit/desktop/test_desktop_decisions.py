"""Qt-free rules extracted from MainWindow (Task 4.2)."""

from datetime import date, datetime

import pytest

from doc_searcher.desktop import search_filters as sf
from doc_searcher.desktop.coordination import WorkScheduler, summarize_indexing

NOW = 1_800_000_000.0


def state(**overrides):
    values = dict(
        date_mode="all",
        date_field="mtime",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        size_mode="any",
        min_size=0.0,
        min_unit="MB",
        max_size=0.0,
        max_unit="MB",
        include_paths="",
        match_case=False,
        whole_word=False,
        regex=False,
    )
    values.update(overrides)
    return sf.FilterState(**values)


def kwargs(**overrides):
    return sf.to_search_kwargs(state(**overrides), ["node_modules"], ["/root"], NOW)


# ------------------------------------------------------------ search filters
def test_defaults_pass_through_options_only():
    assert kwargs(include_paths=" a ; ;b ", regex=True) == {
        "date_field": "mtime",
        "include_paths": ["a", "b"],
        "exclude_patterns": ["node_modules"],
        "search_roots": ["/root"],
        "match_case": False,
        "whole_word": False,
        "regex": True,
    }


@pytest.mark.parametrize("mode, days", [("day", 1), ("week", 7), ("month", 30), ("year", 365)])
def test_relative_date_presets(mode, days):
    result = kwargs(date_mode=mode)
    assert result["modified_after"] == NOW - days * 86400
    assert "modified_before" not in result


def test_custom_date_range_covers_whole_days_in_local_time():
    result = kwargs(date_mode="custom", date_from=date(2026, 3, 1), date_to=date(2026, 3, 2))
    assert result["modified_after"] == datetime(2026, 3, 1).timestamp()
    assert result["modified_before"] == datetime(2026, 3, 3).timestamp()


@pytest.mark.parametrize(
    "mode, minimum, maximum",
    [
        ("small", None, sf.MB - 1),
        ("medium", sf.MB, 10 * sf.MB - 1),
        ("large", 10 * sf.MB, 100 * sf.MB),
        ("huge", 100 * sf.MB + 1, None),
    ],
)
def test_size_presets(mode, minimum, maximum):
    result = kwargs(size_mode=mode)
    assert result.get("min_size") == minimum
    assert result.get("max_size") == maximum


def test_custom_size_units_and_zero_means_unbounded():
    result = kwargs(size_mode="custom", min_size=1.5, min_unit="KB", max_size=2, max_unit="GB")
    assert result["min_size"] == 1536 and result["max_size"] == 2 * 1024**3
    assert "min_size" not in kwargs(size_mode="custom", max_size=1)


def test_validation_order_and_messages():
    bad_dates = state(date_mode="custom", date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))
    assert sf.validate(bad_dates, "(", kwargs())[0] == "invalid_date_range"
    kind, detail = sf.validate(state(regex=True), "(", kwargs())
    assert kind == "regex" and "missing" in detail
    assert sf.validate(state(regex=True), "", kwargs()) is None  # empty query is not compiled
    sizes = {"min_size": 10, "max_size": 5}
    assert sf.validate(state(), "x", sizes) == ("invalid_size_range", "")
    assert sf.validate(state(), "x", kwargs()) is None


def test_signature_changes_with_every_result_affecting_input():
    base = sf.signature(state(), "", ["/a"], True)
    assert sf.signature(state(), "", ["/a"], True) == base
    for changed in (
        sf.signature(state(regex=True), "", ["/a"], True),
        sf.signature(state(date_to=date(2026, 2, 1)), "", ["/a"], True),
        sf.signature(state(), "tmp", ["/a"], True),
        sf.signature(state(), "", ["/a", "/b"], True),
        sf.signature(state(), "", ["/a"], False),
    ):
        assert changed != base


def test_query_error_keys():
    assert sf.query_error_key("repeated_operator") == "error_repeated_operator"
    assert sf.query_error_key("something else") is None


# --------------------------------------------------------- indexing outcome
def docs():
    return 42


@pytest.mark.parametrize(
    "stats, expected",
    [
        ({"cancelled": True}, ("idle", "index_stopped", {"docs": 42}, False)),
        ({"error": "boom"}, ("error", "index_failed", {"message": "boom"}, False)),
        ({"skipped": True, "deleted": 2}, ("completed", "index_unavailable", {"docs": 42}, False)),
        (
            {
                "indexed": 1,
                "deleted": 0,
                "unavailable_directories": ["/x"],
                "scan_error_paths": ["/y"],
            },
            (
                "completed",
                "index_partial",
                {"indexed": 1, "deleted": 0, "skipped": 2, "docs": 42},
                True,
            ),
        ),
        ({"indexed": 0, "deleted": 0, "failed": 0}, ("completed", None, {}, True)),
        (
            {"indexed": 3, "deleted": 1, "failed": 2},
            (
                "completed",
                "index_complete",
                {"indexed": 3, "deleted": 1, "failed": 2, "docs": 42},
                True,
            ),
        ),
    ],
)
def test_indexing_outcomes(stats, expected):
    outcome = summarize_indexing(stats, docs)
    assert (outcome.state, outcome.message_key, outcome.params, outcome.mark_updated) == expected


def test_error_outcome_does_not_query_the_database():
    def fail():
        raise AssertionError("total_docs must not be queried")

    assert summarize_indexing({"error": "x"}, fail).state == "error"


# ---------------------------------------------------------------- scheduler
def test_index_waits_for_running_work():
    scheduler = WorkScheduler()
    assert scheduler.request_index(False, False) == "start"
    assert scheduler.request_index(True, False) == "queued"
    assert scheduler.after_index() == "index"
    assert scheduler.after_index() is None
    assert scheduler.request_index(False, True) == "queued_behind_search"
    assert scheduler.after_search() == ("index", None)


def test_search_waits_and_latest_request_wins():
    scheduler = WorkScheduler()
    first, second = ("a", "all", {}), ("b", "pdf", {})
    assert scheduler.request_search(first, index_running=True, search_running=False) == "queued"
    assert scheduler.request_search(second, index_running=True, search_running=False) == "queued"
    assert scheduler.after_index() == "search"
    assert scheduler.request_search(second, False, False) == "start"
    scheduler.search_started()
    assert scheduler.pending_search is None


def test_empty_query_clears_and_invalidates_a_running_search():
    scheduler = WorkScheduler()
    assert scheduler.request_search(("", "all", {}), False, False) == "clear"
    assert scheduler.pending_search is None
    assert scheduler.request_search(("", "all", {}), False, True) == "clear"
    assert not scheduler.accepts_results(True, ("q",), ("q",))  # stale while a request is queued
    assert scheduler.after_search() == (None, None)


def test_results_accepted_only_for_current_unchanged_search():
    scheduler = WorkScheduler()
    assert scheduler.accepts_results(True, ("q", "all", 1), ("q", "all", 1))
    assert not scheduler.accepts_results(False, ("q", "all", 1), ("q", "all", 1))
    assert not scheduler.accepts_results(True, ("q", "all", 1), ("q", "pdf", 1))
