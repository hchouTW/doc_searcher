# Purpose: Plain text (.txt, .md, .csv, .log) document parser.
# What the code does:
#   - Detects encoding (UTF-8, UTF-16, Big5, GBK, Latin-1) and extracts text.
#   - Returns single PageSegment.
# Usage notes, dependencies, or assumptions:
#   - Standard library and fallback encodings.

import os
from .base import BaseParser, ExtractedDoc, PageSegment


class TextParser(BaseParser):
    """Parser for plain text, Markdown, and CSV files."""

    ENCODINGS = ["utf-8", "utf-8-sig", "big5", "gbk", "cp950", "cp936", "latin-1"]

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        ext = os.path.splitext(abs_path)[1].lower().lstrip(".")

        raw_bytes = b""
        try:
            with open(abs_path, "rb") as f:
                raw_bytes = f.read()
        except Exception as e:
            return ExtractedDoc(
                file_path=abs_path,
                file_type=ext,
                error=f"Cannot read file: {str(e)}"
            )

        decoded_text = ""
        for enc in self.ENCODINGS:
            try:
                decoded_text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if not decoded_text:
            decoded_text = raw_bytes.decode("utf-8", errors="ignore")

        decoded_text = decoded_text.strip()
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
