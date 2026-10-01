# Purpose: Token-boundary-independent Chinese candidates for SQLite FTS5.
# Behavior: Encode folded CJK characters and adjacent pairs as ASCII tokens.
# Usage: Candidates must be verified against original text; no offsets are folded.
from doc_searcher.search.script_fold import fold


def is_cjk(char: str) -> bool:
    n = ord(char)
    return 0x3400 <= n <= 0x9FFF or 0xF900 <= n <= 0xFAFF or 0x20000 <= n <= 0x323AF


def cjk_tokens(text: str) -> str:
    text = fold(text)
    tokens = set()
    previous = None
    for char in text:
        if is_cjk(char):
            tokens.add(f"u{ord(char):x}")
            if previous is not None:
                tokens.add(f"b{ord(previous):x}x{ord(char):x}")
            previous = char
        else:
            previous = None
    return " ".join(sorted(tokens))


def candidate_expression(text: str) -> str:
    tokens = cjk_tokens(text).split()
    pairs = [t for t in tokens if t.startswith("b")]
    return " AND ".join(pairs or tokens)
