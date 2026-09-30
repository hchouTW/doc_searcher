# Purpose: Text processing, jieba Chinese tokenization, and HTML snippet highlighting helper.
# What the code does:
#   - Tokenizes Chinese and English text using jieba.cut_for_search for FTS5 index, after folding
#     Traditional characters to Simplified so both scripts share the same tokens.
#   - has_word_char() tells indexable tokens from punctuation-only ones, which SQLite's unicode61
#     tokenizer never indexes; query building and highlighting drop the latter.
#   - Extracts safe literal or regex snippet contexts with HTML <mark> tags, pulling only the
#     first few matches lazily.
#   - Sanitizes and escapes HTML characters safely.
# Usage notes, dependencies, or assumptions:
#   - Requires jieba; script folding lives in search.script_fold, English stemming in
#     search.stemming (highlights follow the index: outstand marks outstanding).
#   - Used by indexer, searcher, and UI preview panel.

import html
import itertools
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import jieba

from doc_searcher.search.regex_engine import Deadline
from doc_searcher.search.script_fold import fold, script_regex
from doc_searcher.search.stemming import StemSpec, matches_stem, stem_regex_source, stem_spec

# Longest part of a single match shown in a snippet (e.g. ".*" over a whole 5 MB segment).
MAX_MATCH_CHARS = 300


def tokenize_for_fts(text: str) -> str:
    """Tokenize text using jieba for search index, returning space-separated tokens."""
    if not text:
        return ""
    # jieba.cut_for_search generates finer tokens for indexing
    tokens = jieba.cut_for_search(fold(text))
    # Filter out empty or pure whitespace tokens
    clean_tokens = [t.strip() for t in tokens if t.strip()]
    return " ".join(clean_tokens)


def has_word_char(token: str) -> bool:
    """True when token contains a letter or digit, i.e. something the FTS index can hold."""
    return any(char.isalnum() for char in token)


def extract_keywords_from_query(query: str) -> List[str]:
    """Parse user query string into individual match keywords."""
    # Remove operators like AND, OR, NOT
    cleaned = re.sub(r"\b(AND|OR|NOT)\b", " ", query, flags=re.IGNORECASE)
    # Extract words and quoted phrases
    phrases = re.findall(r'"([^"]+)"', cleaned)
    remainder = re.sub(r'"[^"]+"', " ", cleaned)

    words = remainder.split()
    all_terms = phrases + words

    # Also tokenize Chinese terms so each component word can be highlighted
    highlight_terms = set()
    for term in all_terms:
        term = term.strip()
        if not term:
            continue
        highlight_terms.add(term)
        # Add sub-tokens from jieba
        for sub in jieba.cut(fold(term)):
            sub = sub.strip()
            if not has_word_char(sub):
                continue  # "-", "(", "_": the whole term is highlighted, not every dash
            if len(sub) == 1 and sub.isascii() and len(term) > 1:
                continue  # the "A" of "A-" would light up every letter a
            highlight_terms.add(sub)

    return sorted(list(highlight_terms), key=len, reverse=True)


def generate_highlighted_snippets(
    content: str,
    keywords: List[str],
    max_snippets: int = 3,
    context_chars: int = 50,
    case_sensitive: bool = False,
    whole_word: bool = False,
    stemming: bool = True,
) -> List[str]:
    """Generate context snippets with HTML <mark> tags around matched keywords.

    Unless case_sensitive or whole_word is set (both ask for the exact word), plain English
    keywords also mark their other forms, as the stemmed index matched them.
    """
    if not content or not keywords:
        return []
    pattern = _highlight_pattern(
        tuple(keywords), case_sensitive, whole_word, stemming and not (case_sensitive or whole_word)
    )
    if pattern is None:
        return []
    return generate_regex_highlighted_snippets(
        content, pattern, max_snippets=max_snippets, context_chars=context_chars
    )


@lru_cache(maxsize=256)
def _highlight_pattern(
    keywords: Tuple[str, ...], case_sensitive: bool, whole_word: bool, stemmed: bool
) -> Optional[Any]:
    """Compile (once per query) the highlight pattern for keywords; None when nothing to mark."""
    specs: Dict[str, StemSpec] = {}
    alternatives = []
    literals = []
    for keyword in dict.fromkeys(k for k in keywords if k.strip()):
        spec = stem_spec(keyword) if stemmed else None
        if spec is None:
            literals.append(script_regex(keyword))
        elif spec not in specs.values():
            name = f"s{len(specs)}"
            specs[name] = spec
            alternatives.append(f"(?P<{name}>{stem_regex_source(spec)})")
    alternatives += literals
    if not alternatives:
        return None

    expression = "(?:" + "|".join(alternatives) + ")"
    if whole_word:
        expression = rf"(?<!\w){expression}(?!\w)"
    pattern: Any = re.compile(expression, 0 if case_sensitive else re.IGNORECASE)
    return _StemFilter(pattern, specs) if specs else pattern


class _StemFilter:
    """Wraps a compiled pattern and drops stem-branch matches whose word has another stem."""

    def __init__(self, pattern: Any, specs: Dict[str, StemSpec]):
        self._pattern = pattern
        self._specs = specs

    def finditer(self, string: str, **kwargs: Any):
        specs = self._specs
        for match in self._pattern.finditer(string, **kwargs):
            name = match.lastgroup
            if name is None or matches_stem(specs[name], match.group()):
                yield match


def generate_regex_highlighted_snippets(
    content: str,
    pattern: Any,
    max_snippets: int = 3,
    context_chars: int = 50,
    deadline: Optional[Deadline] = None,
) -> List[str]:
    """Generate escaped snippets from an already validated regular expression.

    Only the first max_snippets matches are examined, consumed lazily, so memory does not grow
    with the number of matches. A match longer than MAX_MATCH_CHARS is shown truncated. With a
    deadline, pattern must come from regex_engine (it accepts timeout=).
    """
    if not content:
        return []

    snippets: List[str] = []
    used_ranges: List[Tuple[int, int]] = []
    matches = pattern.finditer(content, **Deadline.timeout_kwargs(deadline))
    # Like the original eager version, only the first max_snippets matches are considered
    # (overlapping ones are skipped), but they are pulled lazily: memory stays constant.
    for match in itertools.islice(matches, max_snippets):
        shown_end = min(match.end(), match.start() + MAX_MATCH_CHARS)
        start_idx = max(0, match.start() - context_chars)
        end_idx = min(len(content), shown_end + context_chars)
        if any(not (end_idx < u_start or start_idx > u_end) for u_start, u_end in used_ranges):
            continue

        used_ranges.append((start_idx, end_idx))
        chunk = content[start_idx:end_idx]

        pieces = []
        cursor = 0
        for local_match in pattern.finditer(chunk, **Deadline.timeout_kwargs(deadline)):
            if not local_match.group(0):
                continue  # zero-width matches (e.g. lookarounds) have nothing to highlight
            pieces.append(html.escape(chunk[cursor : local_match.start()]))
            pieces.append(
                '<mark style="background-color: #ffeb3b; color: #000; '
                'font-weight: bold; padding: 1px 3px; border-radius: 2px;">'
                f"{html.escape(local_match.group(0))}</mark>"
            )
            cursor = local_match.end()
        pieces.append(html.escape(chunk[cursor:]))
        highlighted_chunk = "".join(pieces)

        prefix = "..." if start_idx > 0 else ""
        suffix = "..." if end_idx < len(content) else ""
        snippets.append(f"{prefix}{highlighted_chunk}{suffix}")

    return snippets
