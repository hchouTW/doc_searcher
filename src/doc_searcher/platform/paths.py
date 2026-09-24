# Purpose: One canonical spelling for file-system paths DocSearcher stores and compares.
# What the code does:
#   - canonical_path() returns the absolute path, Unicode-normalized to NFC on macOS. APFS and
#     HFS+ find a name by either normalization form but report it in whichever form it was
#     created (older Macs, zip archives, and network shares often use decomposed NFD), so the
#     same folder could otherwise be indexed twice or missed by a typed (NFC) filter.
#   - canonical_text() applies the same normalization to user-entered filter text.
# Usage notes, dependencies, or assumptions:
#   - Linux and Windows file systems treat NFC and NFD names as different files, so paths are
#     left unchanged there. Standard library only.

import os
import sys
import unicodedata

NORMALIZE_NAMES = sys.platform == "darwin"


def canonical_text(text: str) -> str:
    return unicodedata.normalize("NFC", text) if NORMALIZE_NAMES else text


def canonical_path(path: str) -> str:
    return canonical_text(os.path.abspath(path))
