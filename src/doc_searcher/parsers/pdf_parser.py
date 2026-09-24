# Purpose: PDF document text extraction parser.
# What the code does:
#   - Uses pymupdf (fitz) to extract text page-by-page.
#   - Reports ENCRYPTED only when a user password is required to read the text.
#   - Catches corrupted or invalid PDFs without crashing.
# Usage notes, dependencies, or assumptions:
#   - Requires pymupdf.
#   - Returns PageSegment per page with 1-based page numbers.

import logging
import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus


_messages_routed = False


def _route_mupdf_messages(pymupdf) -> None:
    """MuPDF prints warnings such as "MuPDF error: ..." to stdout by default, which would mix
    with CLI results and corrupt the MCP stdio stream; send them to Python logging instead."""
    global _messages_routed
    if not _messages_routed:
        pymupdf.set_messages(pylogging_name=__name__, pylogging_level=logging.WARNING)
        _messages_routed = True


class PdfParser(BaseParser):
    """Parser for PDF (.pdf) documents using PyMuPDF."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import pymupdf

            _route_mupdf_messages(pymupdf)
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "pdf", ParseStatus.DEPENDENCY_MISSING, "pymupdf is not installed."
            )

        try:
            doc = pymupdf.open(abs_path)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "pdf", "Cannot open PDF", e)

        try:
            # Owner-password-only PDFs (permissions) open without a password and stay readable.
            if doc.needs_pass:
                return ExtractedDoc.failed(
                    abs_path, "pdf", ParseStatus.ENCRYPTED, "Document is password protected."
                )

            total_pages = len(doc)
            for page_idx in range(total_pages):
                try:
                    page = doc.load_page(page_idx)
                    text = page.get_text("text").strip()
                    if text:
                        segments.append(
                            PageSegment(
                                segment_id=str(page_idx + 1), segment_type="page", text=text
                            )
                        )
                except Exception:
                    # Skip problematic individual page but continue
                    continue

            return ExtractedDoc(
                file_path=abs_path, file_type="pdf", total_segments=total_pages, segments=segments
            )
        finally:
            doc.close()
