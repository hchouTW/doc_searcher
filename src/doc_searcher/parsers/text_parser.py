# Purpose: Plain text (.txt, .md, .csv) document parser.
# What the code does:
#   - Decodes by byte-order mark first (UTF-8, UTF-16 LE/BE, UTF-32 LE/BE), then detects
#     BOM-less UTF-16 from its NUL-byte pattern, then tries UTF-8, Big5, GBK, and finally
#     Latin-1 (which accepts any bytes, so invalid input never fails).
#   - Returns a single PageSegment, or EMPTY for whitespace-only files.
# Usage notes, dependencies, or assumptions:
#   - Standard library only.

import codecs
import os
from typing import Optional

from .base import BaseParser, ExtractedDoc, PageSegment

# Longest BOMs first so UTF-32 LE is not mistaken for UTF-16 LE.
_BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
]


def _bomless_utf16(raw: bytes) -> Optional[str]:
    """Guess UTF-16 without a BOM: mostly-ASCII text has NULs in every other byte."""
    sample = raw[:4096]
    if len(sample) < 4 or len(sample) % 2:
        return None
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    half = len(sample) // 2
    if odd_nuls > 0.3 * half and even_nuls < 0.05 * half:
        return "utf-16-le"
    if even_nuls > 0.3 * half and odd_nuls < 0.05 * half:
        return "utf-16-be"
    return None


def decode_text(raw: bytes) -> str:
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return raw[len(bom):].decode(encoding, errors="replace")
    utf16 = _bomless_utf16(raw)
    if utf16:
        try:
            return raw.decode(utf16)
        except UnicodeDecodeError:
            pass
    for encoding in TextParser.ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")  # unreachable in practice: latin-1 is in ENCODINGS


class TextParser(BaseParser):
    """Parser for plain text, Markdown, and CSV files."""

    ENCODINGS = ["utf-8", "big5", "gbk", "cp950", "cp936", "latin-1"]

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        ext = os.path.splitext(abs_path)[1].lower().lstrip(".")

        try:
            with open(abs_path, "rb") as f:
                raw_bytes = f.read()
        except OSError as e:
            return ExtractedDoc.from_exception(abs_path, ext, "Cannot read file", e)

        decoded_text = decode_text(raw_bytes).strip()
        segments = []
        if decoded_text:
            segments.append(
                PageSegment(
                    segment_id="1",
                    segment_type="section",
                    text=decoded_text
                )
            )

        return ExtractedDoc(
            file_path=abs_path,
            file_type=ext,
            total_segments=len(segments),
            segments=segments
        )
