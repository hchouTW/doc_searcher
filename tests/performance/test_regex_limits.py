"""Regex search limits: memory, pathological patterns, cancellation, oversized text (Task 5.2)."""

import re
import time
import tracemalloc

import pytest

from doc_searcher.search import regex_engine
from doc_searcher.search.searcher import DocumentSearcher, SearchQueryError
from doc_searcher.search.text_helper import generate_regex_highlighted_snippets
from doc_searcher.storage.database import Database


def _peak_bytes(function):
    tracemalloc.start()
    try:
        function()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_snippet_memory_does_not_scale_with_match_count():
    pattern = re.compile(r"\d")
    few = "1" * 2_000
    many = "1" * 200_000  # 100x more matches in a single segment
    peak_few = _peak_bytes(lambda: generate_regex_highlighted_snippets(few, pattern))
    peak_many = _peak_bytes(lambda: generate_regex_highlighted_snippets(many, pattern))
    assert peak_many < peak_few * 3 + 64_000


@pytest.fixture
def searcher(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    for index, content in enumerate(["a" * 35 + "b", "x" * 28, "budget 2026-03-01 plan"]):
        db.save_document_index(
            str(tmp_path / f"doc{index}.txt"),
            "txt",
            len(content),
            1.0,
            [
                {
                    "segment_id": "1",
                    "segment_type": "section",
                    "content": content,
                    "tokenized_content": content,
                }
            ],
        )
    yield DocumentSearcher(db)
    db.close()


def test_regex_results_are_unchanged(searcher):
    results = searcher.search(r"\d{4}-\d{2}-\d{2}", regex=True)
    assert [r.filename for r in results] == ["doc2.txt"]
    assert "<mark" in results[0].segments[0].snippets[0]


def test_catastrophic_backtracking_in_stdlib_re_is_harmless(searcher):
    started = time.monotonic()
    assert searcher.search(r"(x+x+)+y", regex=True) == []  # hangs for minutes with stdlib re
    assert time.monotonic() - started < 2


def test_over_budget_regex_stops_with_actionable_error(searcher, monkeypatch):
    monkeypatch.setattr(regex_engine, "REGEX_TIME_BUDGET_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises(SearchQueryError) as exc:
        searcher.search(r"(a|aa)+$", regex=True)
    assert exc.value.code == "regex_timeout"
    assert "(a+)+" in str(exc.value)  # tells the user what to change
    assert time.monotonic() - started < 3


def test_cancelled_regex_search_stops_at_the_next_segment(searcher):
    calls = []

    def cancel_after_first():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(SearchQueryError) as exc:
        searcher.search(r"\w+", regex=True, cancel_check=cancel_after_first)
    assert exc.value.code == "cancelled"
    assert len(calls) == 2


def test_oversized_document_is_scanned_within_budget(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    content = "lorem ipsum dolor " * 300_000  # ~5 MB in one segment, no match
    db.save_document_index(
        str(tmp_path / "big.txt"),
        "txt",
        len(content),
        1.0,
        [
            {
                "segment_id": "1",
                "segment_type": "section",
                "content": content,
                "tokenized_content": "",
            }
        ],
    )
    started = time.monotonic()
    assert DocumentSearcher(db).search(r"\d{4}-\d{2}", regex=True) == []
    assert time.monotonic() - started < regex_engine.REGEX_TIME_BUDGET_SECONDS
    db.close()
