# Purpose: Parser registry and dispatcher module.
# What the code does:
#   - Maps file extensions to corresponding parser instances.
#   - Exposes get_parser(path) and supported extension lists.
# Usage notes, dependencies, or assumptions:
#   - Caches parser instances for reuse.

import os
from typing import Optional, Dict
from .base import BaseParser, ExtractedDoc, PageSegment
from .pdf_parser import PdfParser
from .docx_parser import DocxParser
from .doc_parser import DocParser
from .pptx_parser import PptxParser
from .ppt_parser import PptParser
from .xlsx_parser import XlsxParser
from .xls_parser import XlsParser
from .text_parser import TextParser

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
