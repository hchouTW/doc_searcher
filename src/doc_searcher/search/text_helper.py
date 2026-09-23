# Purpose: Text processing, jieba Chinese tokenization, and HTML snippet highlighting helper.
# What the code does:
#   - Tokenizes Chinese and English text using jieba.cut_for_search for FTS5 index.
#   - Extracts safe literal or regex snippet contexts with HTML <mark> tags.
#   - Sanitizes and escapes HTML characters safely.
# Usage notes, dependencies, or assumptions:
#   - Requires jieba.
#   - Used by indexer, searcher, and UI preview panel.

import html
import re
from typing import List, Tuple
import jieba


def tokenize_for_fts(text: str) -> str:
    """Tokenize text using jieba for search index, returning space-separated tokens."""
    if not text:
        return ""
    # jieba.cut_for_search generates finer tokens for indexing
    tokens = jieba.cut_for_search(text)
    # Filter out empty or pure whitespace tokens
    clean_tokens = [t.strip() for t in tokens if t.strip()]
    return " ".join(clean_tokens)


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
        for sub in jieba.cut(term):
            sub = sub.strip()
            if len(sub) >= 1 and sub not in {"*", "?", "-", "+"}:
                highlight_terms.add(sub)

    return sorted(list(highlight_terms), key=len, reverse=True)


def generate_highlighted_snippets(
    content: str,
    keywords: List[str],
    max_snippets: int = 3,
    context_chars: int = 50,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> List[str]:
    """Generate context snippets with HTML <mark> tags around matched keywords."""
    if not content or not keywords:
        return []

    escaped_kws = [re.escape(k) for k in keywords if k.strip()]
    if not escaped_kws:
        return []

    expression = r"(" + "|".join(escaped_kws) + r")"
    if whole_word:
        expression = rf"(?<!\w){expression}(?!\w)"
    pattern = re.compile(expression, 0 if case_sensitive else re.IGNORECASE)
    return generate_regex_highlighted_snippets(
        content, pattern, max_snippets=max_snippets, context_chars=context_chars
    )


def generate_regex_highlighted_snippets(
    content: str,
    pattern: re.Pattern,
    max_snippets: int = 3,
    context_chars: int = 50,
) -> List[str]:
    """Generate escaped snippets from an already validated regular expression."""
    if not content:
        return []
    matches = list(pattern.finditer(content))
    if not matches:
        return []

    snippets = []
    used_ranges: List[Tuple[int, int]] = []

    for match in matches[:max_snippets]:
        start_idx = max(0, match.start() - context_chars)
        end_idx = min(len(content), match.end() + context_chars)

        # Check overlap with previous snippets
        overlaps = False
        for u_start, u_end in used_ranges:
            if not (end_idx < u_start or start_idx > u_end):
                overlaps = True
                break
        if overlaps:
            continue

        used_ranges.append((start_idx, end_idx))
        chunk = content[start_idx:end_idx]

        pieces = []
        cursor = 0
        for local_match in pattern.finditer(chunk):
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
