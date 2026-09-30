# Purpose: Enumerate original-text occurrences independently of preview rendering.
# Behavior: Merge overlapping positive intervals, retaining adjacent and zero-width hits.
# Usage: Streaming iterators keep memory proportional to query terms, with regex deadlines.
import heapq
import html
import itertools
import re
from dataclasses import dataclass
from typing import Optional

from doc_searcher.search.regex_engine import Deadline
from doc_searcher.search.script_fold import script_regex
from doc_searcher.search.stemming import stem_spec
from doc_searcher.search.text_helper import _highlight_pattern, MAX_MATCH_CHARS


@dataclass
class Occurrence:
    start: int
    end: int
    matched_terms: list[str]


@dataclass
class MatchLocation(Occurrence):
    doc_id: int = 0
    segment_row_id: int = 0
    segment_id: str = ""
    segment_type: str = ""
    source: Optional[dict] = None
    revision: int = 0


@dataclass
class MatchPage:
    locations: list[MatchLocation]
    next_offset: Optional[int]
    total_matches: int
    complete: bool
    revision: int


def iter_locations(content, positive_terms, *, regex_pattern=None, deadline=None,
                   cancel_check=None, match_case=False, whole_word=False, stemming=True):
    """Yield merged intervals in original Python character coordinates, without buffering hits."""
    def checked(iterator, term, group=0):
        for i, match in enumerate(iterator):
            if i % 256 == 0:
                if cancel_check and cancel_check():
                    # Imported lazily: searcher uses this module too.
                    from doc_searcher.search.searcher import SearchQueryError
                    raise SearchQueryError("搜尋已取消。", "cancelled")
                if deadline:
                    deadline.remaining()
            yield Occurrence(match.start(group), match.end(group), [term])
    streams = []
    if regex_pattern is not None:
        streams.append(checked(regex_pattern.finditer(content, **Deadline.timeout_kwargs(deadline)),
                               regex_pattern.pattern))
    else:
        for term in dict.fromkeys(positive_terms):
            if not term:
                continue
            use_stem = stemming and not (match_case or whole_word) and stem_spec(term) is not None
            if use_stem:
                pattern = _highlight_pattern((term,), match_case, whole_word, True)
                streams.append(checked(pattern.finditer(content), term))
            else:
                expression = script_regex(term)
                if whole_word:
                    expression = rf"(?<!\w){expression}(?!\w)"
                pattern = re.compile(f"(?=({expression}))", 0 if match_case else re.IGNORECASE)
                streams.append(checked(pattern.finditer(content), term, 1))
    pending = None
    for hit in heapq.merge(*streams, key=lambda x: (x.start, x.end)):
        if pending and (hit.start < pending.end or (hit.start, hit.end) == (pending.start, pending.end)):
            pending.end = max(pending.end, hit.end)
            pending.matched_terms = list(dict.fromkeys(pending.matched_terms + hit.matched_terms))
        else:
            if pending:
                yield pending
            pending = hit
    if pending:
        yield pending


def render_context(content, occurrences, *, start=0, end=None, active=None):
    """Escape original text and highlight occurrences, with a caret for zero-width hits."""
    end = len(content) if end is None else end
    pieces, cursor = [], start
    for hit in occurrences:
        if hit.end < start or hit.start > end:
            continue
        a, b = max(start, hit.start), min(end, hit.end)
        if a < cursor:
            continue
        pieces.append(html.escape(content[cursor:a]))
        css = ' style="background-color: #ffeb3b; color: #000;"'
        if active == (hit.start, hit.end):
            css = ' class="active-occurrence" style="background-color: #ff9800; color: #000;"'
        if a == b:
            pieces.append(f'<span{css} title="zero-width">│</span>')
        else:
            pieces.append(f'<mark{css}>{html.escape(content[a:b])}</mark>')
        cursor = b
    pieces.append(html.escape(content[cursor:end]))
    return ("..." if start else "") + "".join(pieces) + ("..." if end < len(content) else "")


def occurrence_snippets(content, terms, *, max_snippets=3, context_chars=60, **options):
    """Render a bounded number of contexts; skip overlap without limiting examined hits."""
    snippets, last_end = [], -1
    for hit in iter_locations(content, terms, **options):
        start = max(0, hit.start - context_chars)
        end = min(len(content), min(hit.end, hit.start + MAX_MATCH_CHARS) + context_chars)
        if start <= last_end:
            continue
        # Match against the full original text so context edges do not alter lookarounds.
        locations = itertools.takewhile(lambda x, bound=end: x.start <= bound,
                                         iter_locations(content, terms, **options))
        snippets.append(render_context(content, locations, start=start, end=end))
        last_end = end
        if len(snippets) >= max_snippets:
            break
    return snippets
