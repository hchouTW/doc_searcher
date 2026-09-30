# Purpose: Let English word forms match each other (outstand, outstanding, outstandings) in
#   highlighting and in the literal re-checks, using the same stemmer as the search index.
# What the code does:
#   - stem(word) returns SQLite FTS5's own Porter stem (the doc_fts table is built with
#     tokenize='porter unicode61'), so highlights agree with what the index matched.
#   - stem_spec(term) describes a stemmable term (plain ASCII letters, 3+ characters) by the
#     prefix all its inflections share; stem_regex_source() turns it into a regex for the words
#     that start with that prefix, and matches_stem() keeps only those whose stem is the same
#     (so "run" highlights "running" but not "runway").
# Usage notes, dependencies, or assumptions:
#   - Needs FTS5 (already required by the app). The stem is looked up in a private in-memory
#     FTS5 table guarded by a lock and cached; Porter over-stems some words (news/new,
#     universe/university), exactly as the index does.
#   - Only ASCII letters are stemmed; Chinese and other text is untouched.

import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None
_MIN_LENGTH = 3


def _probe() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute(
            "CREATE VIRTUAL TABLE stem_probe USING fts5(word, tokenize='porter unicode61')"
        )
        conn.execute("CREATE VIRTUAL TABLE stem_terms USING fts5vocab(stem_probe, 'row')")
        _connection = conn
    return _connection


@lru_cache(maxsize=16384)
def stem(word: str) -> str:
    """The stem SQLite's porter tokenizer indexes word under (lower case)."""
    lowered = word.lower()
    with _lock:
        conn = _probe()
        conn.execute("DELETE FROM stem_probe")
        conn.execute("INSERT INTO stem_probe VALUES (?)", (lowered,))
        row = conn.execute("SELECT term FROM stem_terms").fetchone()
    return row[0] if row else lowered


@dataclass(frozen=True)
class StemSpec:
    prefix: str  # lower-case start shared by the term and its stem
    stem: str


def stem_spec(term: str) -> Optional[StemSpec]:
    """Describe term when it is a plain English word worth stemming, else None."""
    if len(term) < _MIN_LENGTH or not (term.isascii() and term.isalpha()):
        return None
    word_stem = stem(term)
    prefix = os.path.commonprefix([term.lower(), word_stem])
    return StemSpec(prefix, word_stem) if len(prefix) >= 2 else None


def stem_regex_source(spec: StemSpec) -> str:
    """Regex source for words starting with spec.prefix (filter with matches_stem)."""
    return rf"(?<![A-Za-z]){re.escape(spec.prefix)}[A-Za-z]*"


def matches_stem(spec: StemSpec, word: str) -> bool:
    return stem(word) == spec.stem
