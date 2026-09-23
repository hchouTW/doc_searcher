# Purpose: Parser registry and dispatcher module.
# What the code does:
#   - Maps file extensions to corresponding parser instances.
#   - Exposes get_parser(path) and supported extension lists.
#   - parse_file(path) is the checked entry point: it reports UNSUPPORTED and UNREADABLE
#     itself (libraries often misreport missing/permission-denied files as corrupt) and
#     converts unexpected parser exceptions into CORRUPT results.
# Usage notes, dependencies, or assumptions:
#   - Caches parser instances for reuse.

import os
from typing import Optional, Dict
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus
from .pdf_parser import PdfParser
from .docx_parser import DocxParser
from .doc_parser import DocParser
from .pptx_parser import PptxParser
from .ppt_parser import PptParser
from .xlsx_parser import XlsxParser
from .xls_parser import XlsParser
from .text_parser import TextParser

__all__ = [
    "BaseParser",
    "ExtractedDoc",
    "PageSegment",
    "ParseStatus",
    "PdfParser",
    "DocxParser",
    "DocParser",
    "PptxParser",
    "PptParser",
    "XlsxParser",
    "XlsParser",
    "TextParser",
    "SUPPORTED_EXTENSIONS",
    "get_parser",
    "is_supported",
    "parse_file",
]

_PARSERS: Dict[str, BaseParser] = {
    ".pdf": PdfParser(),
    ".docx": DocxParser(),
    ".doc": DocParser(),
    ".pptx": PptxParser(),
    ".ppt": PptParser(),
    ".xlsx": XlsxParser(),
    ".xls": XlsParser(),
    ".txt": TextParser(),
    ".md": TextParser(),
    ".csv": TextParser(),
}

SUPPORTED_EXTENSIONS = set(_PARSERS.keys())


def get_parser(file_path: str) -> Optional[BaseParser]:
    """Retrieve the appropriate parser for the given file based on its extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return _PARSERS.get(ext)


def is_supported(file_path: str) -> bool:
    """Check if the file format is supported."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def parse_file(file_path: str) -> ExtractedDoc:
    """Parse any file and always return an ExtractedDoc with a ParseStatus; never raises
    for document problems."""
    abs_path = os.path.abspath(file_path)
    ext = os.path.splitext(abs_path)[1].lower().lstrip(".")
    parser = get_parser(abs_path)
    if parser is None:
        return ExtractedDoc.failed(
            abs_path, ext, ParseStatus.UNSUPPORTED, f"Unsupported file type: .{ext}"
        )
    try:
        with open(abs_path, "rb") as handle:
            handle.read(1)
    except OSError as exc:
        return ExtractedDoc.failed(
            abs_path, ext, ParseStatus.UNREADABLE, f"Cannot read file: {exc}"
        )
    try:
        return parser.parse(abs_path)
    except Exception as exc:  # A parser bug or library crash must not stop batch indexing.
        return ExtractedDoc.from_exception(abs_path, ext, "Parser exception", exc)
