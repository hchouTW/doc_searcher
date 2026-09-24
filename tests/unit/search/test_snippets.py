"""Snippet generation correctness and bounds (Task 5.2)."""

import re

from doc_searcher.search.regex_engine import compile_user_regex
from doc_searcher.search.text_helper import (
    MAX_MATCH_CHARS,
    generate_highlighted_snippets,
    generate_regex_highlighted_snippets,
)

MARK = "<mark"


def test_highlights_every_match_inside_a_snippet():
    snippets = generate_highlighted_snippets("the budget and the Budget plan", ["budget"])
    assert len(snippets) == 1
    assert snippets[0].count(MARK) == 2


def test_html_is_escaped():
    snippets = generate_regex_highlighted_snippets(
        "<b>x</b> 2026-01-02", compile_user_regex(r"\d{4}")
    )
    assert "&lt;b&gt;" in snippets[0] and "<b>" not in snippets[0]


def _v120_snippets(content, pattern, max_snippets=3, context_chars=50):
    """Verbatim v1.2.0 algorithm (eager list of matches), the reference for unchanged results."""
    import html

    matches = list(pattern.finditer(content))
    snippets, used_ranges = [], []
    for match in matches[:max_snippets]:
        start_idx = max(0, match.start() - context_chars)
        end_idx = min(len(content), match.end() + context_chars)
        if any(not (end_idx < u_start or start_idx > u_end) for u_start, u_end in used_ranges):
            continue
        used_ranges.append((start_idx, end_idx))
        chunk = content[start_idx:end_idx]
        pieces, cursor = [], 0
        for local_match in pattern.finditer(chunk):
            pieces.append(html.escape(chunk[cursor : local_match.start()]))
            pieces.append(
                '<mark style="background-color: #ffeb3b; color: #000; '
                'font-weight: bold; padding: 1px 3px; border-radius: 2px;">'
                f"{html.escape(local_match.group(0))}</mark>"
            )
            cursor = local_match.end()
        pieces.append(html.escape(chunk[cursor:]))
        prefix = "..." if start_idx > 0 else ""
        suffix = "..." if end_idx < len(content) else ""
        snippets.append(f"{prefix}{''.join(pieces)}{suffix}")
    return snippets


def test_results_match_v120_for_ordinary_patterns():
    import random

    rng = random.Random(7)
    words = ["budget", "預算", "2026-03-01", "<tag>", "&", "plan", "x" * 40, "\n"]
    patterns = [r"\d{4}-\d{2}-\d{2}", r"budget|plan", r"預算", r"&|<tag>", r"x+"]
    for _ in range(300):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(1, 120)))
        for expression in patterns:
            for context in (20, 60):
                expected = _v120_snippets(text, re.compile(expression, re.I), 3, context)
                actual = generate_regex_highlighted_snippets(
                    text, compile_user_regex(expression), 3, context
                )
                assert actual == expected, (expression, text)


def test_scanning_stops_after_enough_snippets():
    consumed = []

    class CountingPattern:
        def __init__(self, pattern):
            self.pattern = pattern

        def finditer(self, text, **kwargs):
            for match in self.pattern.finditer(text):
                consumed.append(1)
                yield match

    content = ("x" * 200 + "hit") * 100_000
    generate_regex_highlighted_snippets(
        content, CountingPattern(re.compile("hit")), context_chars=60
    )
    assert len(consumed) < 50  # 3 snippets need a handful of matches, not 100,000


def test_huge_match_is_truncated():
    content = "a" * 1_000_000
    snippets = generate_regex_highlighted_snippets(content, re.compile(".*"), context_chars=60)
    # One snippet for the 1 MB match (truncated) plus one for ".*"'s empty match at the end.
    assert len(snippets) == 2
    assert all(len(snippet) < MAX_MATCH_CHARS + 500 for snippet in snippets)
    assert snippets[0].endswith("...")


def test_zero_width_matches_produce_no_empty_marks():
    snippets = generate_regex_highlighted_snippets("foo bar", compile_user_regex(r"(?=bar)"))
    assert snippets == ["foo bar"]  # the lookahead matches, but there is no text to highlight
