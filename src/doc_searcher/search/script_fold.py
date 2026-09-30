# Purpose: Make Simplified and Traditional Chinese spellings of the same text search-equivalent.
# What the code does:
#   - fold(text) maps every Traditional character to its Simplified form, one character to one
#     character, so string length and offsets never change (index and query text go through it).
#   - variants(char) lists every character that folds to the same form (发 -> 发, 發, 髮), and
#     script_regex(term) turns a literal term into a regex that accepts any of those spellings, so
#     matching and highlighting run on the document's original text.
# Usage notes, dependencies, or assumptions:
#   - Data: assets/opencc_TSCharacters.txt (OpenCC, Apache-2.0; see assets/opencc_NOTICE.txt), read
#     once on first use. Character level only: regional vocabulary (軟體/软件) is not converted.
#   - One-to-many mappings (干 <- 幹, 乾) over-match by design; recall is preferred over precision.

import re
from functools import lru_cache
from typing import Dict, FrozenSet, Tuple

from doc_searcher.platform.resource_path import resource_path

_TABLE_FILE = "assets/opencc_TSCharacters.txt"


@lru_cache(maxsize=1)
def _tables() -> Tuple[Dict[int, str], Dict[str, FrozenSet[str]]]:
    """Return (str.translate table, simplified char -> all chars folding to it)."""
    to_simplified: Dict[str, str] = {}
    with open(resource_path(_TABLE_FILE), encoding="utf-8") as handle:
        for line in handle:
            traditional, _, candidates = line.rstrip("\n").partition("\t")
            simplified = candidates.split(" ", 1)[0]
            if len(traditional) == 1 and len(simplified) == 1 and traditional != simplified:
                to_simplified[traditional] = simplified
    groups: Dict[str, set] = {}
    for traditional, simplified in to_simplified.items():
        groups.setdefault(simplified, {simplified}).add(traditional)
    return (
        {ord(t): s for t, s in to_simplified.items()},
        {simplified: frozenset(chars) for simplified, chars in groups.items()},
    )


def fold(text: str) -> str:
    """Map Traditional characters to Simplified; same length, other characters untouched."""
    return text.translate(_tables()[0]) if text else text


def variants(char: str) -> FrozenSet[str]:
    """All characters that fold to the same form as char (just {char} when it has none)."""
    folded = fold(char)
    return _tables()[1].get(folded, frozenset({char}))


def script_regex(term: str) -> str:
    """Regex source matching term literally, accepting Simplified/Traditional spellings."""
    parts = []
    for char in term:
        chars = variants(char)
        if len(chars) > 1:
            parts.append("[" + "".join(re.escape(c) for c in sorted(chars)) + "]")
        else:
            parts.append(re.escape(char))
    return "".join(parts)
