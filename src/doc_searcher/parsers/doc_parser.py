# Purpose: Legacy Word (.doc) document text extraction parser.
# What the code does:
#   - Uses olefile to read OLE2 Compound Document streams (e.g. WordDocument).
#   - Decodes 16-bit Unicode (UTF-16LE) and multi-byte text runs.
#   - Provides pure-Python extraction without requiring Microsoft Word or external binaries.
# Usage notes, dependencies, or assumptions:
#   - Requires olefile.
#   - Cross-platform for Windows and macOS.

import os
import re
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment


class DocParser(BaseParser):
    """Parser for legacy Word 97-2003 (.doc) binary documents."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import olefile
        except ImportError:
            return ExtractedDoc(
                file_path=abs_path,
                file_type="doc",
                error="olefile is not installed."
            )

        if not olefile.isOleFile(abs_path):
            # Sometimes a .doc file is actually an RTF or plain text file named .doc
            try:
                with open(abs_path, "rb") as f:
                    raw_data = f.read()
                text = self._extract_raw_strings(raw_data)
                if text:
                    return ExtractedDoc(
                        file_path=abs_path,
                        file_type="doc",
                        total_segments=1,
                        segments=[PageSegment(segment_id="1", segment_type="section", text=text)]
                    )
            except Exception as ex:
                return ExtractedDoc(
                    file_path=abs_path,
                    file_type="doc",
                    error=f"Not a valid OLE file or readable document: {str(ex)}"
                )

        try:
            with olefile.OleFileIO(abs_path) as ole:
                raw_bytes = b""
                if ole.exists("WordDocument"):
                    with ole.openstream("WordDocument") as stream:
                        raw_bytes += stream.read()
                
                # Check for 1Table or 0Table if present
                if ole.exists("1Table"):
                    with ole.openstream("1Table") as stream:
                        raw_bytes += stream.read()
                elif ole.exists("0Table"):
                    with ole.openstream("0Table") as stream:
                        raw_bytes += stream.read()

                extracted_text = self._extract_raw_strings(raw_bytes)
                if not extracted_text:
                    # Fallback: scan all streams
                    for entry in ole.listdir():
                        if ole.get_size(entry) > 0:
                            with ole.openstream(entry) as s:
                                extracted_text += "\n" + self._extract_raw_strings(s.read())

                extracted_text = extracted_text.strip()
                if extracted_text:
                    segments.append(
                        PageSegment(
                            segment_id="1",
                            segment_type="section",
                            text=extracted_text
                        )
                    )

            return ExtractedDoc(
                file_path=abs_path,
                file_type="doc",
                total_segments=len(segments),
                segments=segments
            )
        except Exception as e:
            return ExtractedDoc(
                file_path=abs_path,
                file_type="doc",
                error=f"Error parsing .doc file: {str(e)}"
            )

    def _extract_raw_strings(self, data: bytes, min_len: int = 3) -> str:
        """Extract continuous UTF-16LE and UTF-8/Latin-1 readable strings from binary stream."""
        results = []

        # 1. Look for UTF-16LE strings (typical in Word 97-2003)
        try:
            # Word uses UTF-16LE for Unicode text
            # Find runs of 2-byte characters where the high byte is often null (ASCII range) or valid CJK
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

        # 2. Look for ASCII/UTF-8 strings
        ascii_pattern = re.compile(rb'[\x20-\x7e\x09\x0a\x0d]{%d,}' % min_len)
        for match in ascii_pattern.finditer(data):
            try:
                s = match.group().decode("utf-8", errors="ignore").strip()
                if len(s) >= min_len:
                    results.append(s)
            except Exception:
                pass

        # Deduplicate while preserving order
        seen = set()
        cleaned = []
        for line in results:
            # Filter out obvious binary noise
            cleaned_line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', line).strip()
            if len(cleaned_line) >= min_len and cleaned_line not in seen:
                seen.add(cleaned_line)
                cleaned.append(cleaned_line)

        return "\n".join(cleaned)
