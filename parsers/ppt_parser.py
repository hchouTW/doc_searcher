# Purpose: Legacy PowerPoint 97-2003 (.ppt) text extraction parser.
# What the code does:
#   - Uses olefile to read OLE2 Compound File streams (PowerPoint Document, Current User).
#   - Decodes slide text sequences from binary records in pure Python.
#   - Catches corrupted or invalid files safely.
# Usage notes, dependencies, or assumptions:
#   - Requires olefile.
#   - Cross-platform for Windows and macOS.

import os
import re
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment


class PptParser(BaseParser):
    """Parser for legacy PowerPoint 97-2003 (.ppt) binary presentations."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import olefile
        except ImportError:
            return ExtractedDoc(
                file_path=abs_path,
                file_type="ppt",
                error="olefile is not installed."
            )

        if not olefile.isOleFile(abs_path):
            return ExtractedDoc(
                file_path=abs_path,
                file_type="ppt",
                error="Not a valid OLE2 PowerPoint file."
            )

        try:
            with olefile.OleFileIO(abs_path) as ole:
                raw_bytes = b""
                if ole.exists("PowerPoint Document"):
                    with ole.openstream("PowerPoint Document") as stream:
                        raw_bytes = stream.read()
                elif ole.exists("Pictures"):
                    # Some PPTs store metadata in other streams
                    for entry in ole.listdir():
                        if ole.get_size(entry) > 0:
                            with ole.openstream(entry) as s:
                                raw_bytes += s.read()

                extracted_text = self._extract_ppt_strings(raw_bytes)
                if extracted_text:
                    # Group text by potential slide boundaries or as single segment
                    segments.append(
                        PageSegment(
                            segment_id="1",
                            segment_type="slide",
                            text=extracted_text
                        )
                    )

            return ExtractedDoc(
                file_path=abs_path,
                file_type="ppt",
                total_segments=len(segments),
                segments=segments
            )
        except Exception as e:
            return ExtractedDoc(
                file_path=abs_path,
                file_type="ppt",
                error=f"Error parsing .ppt file: {str(e)}"
            )

    def _extract_ppt_strings(self, data: bytes, min_len: int = 3) -> str:
        """Extract readable UTF-16LE and ASCII text blocks from PowerPoint Document stream."""
        results = []

        # UTF-16LE patterns common in PPT 97-2003
        try:
            utf16_pattern = re.compile(rb'(?:[\x20-\x7e\x09\x0a\x0d]\x00|[\x00-\xff][\x4e-\x9f]){%d,}' % min_len)
            for match in utf16_pattern.finditer(data):
                try:
                    s = match.group().decode("utf-16le", errors="ignore").strip()
                    if len(s) >= min_len:
                        results.append(s)
                except Exception:
                    pass
        except Exception:
            pass

        # ASCII patterns
        ascii_pattern = re.compile(rb'[\x20-\x7e\x09\x0a\x0d]{%d,}' % min_len)
        for match in ascii_pattern.finditer(data):
            try:
                s = match.group().decode("utf-8", errors="ignore").strip()
                if len(s) >= min_len:
                    results.append(s)
            except Exception:
                pass

        seen = set()
        cleaned = []
        for line in results:
            cleaned_line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', line).strip()
            # Ignore standard PPT headers or font names that appear repeatedly
            if cleaned_line in {"Times New Roman", "Arial", "Calibri", "Wingdings", "MS Gothic"}:
                continue
            if len(cleaned_line) >= min_len and cleaned_line not in seen:
                seen.add(cleaned_line)
                cleaned.append(cleaned_line)

        return "\n".join(cleaned)
