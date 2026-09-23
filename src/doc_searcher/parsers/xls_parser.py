# Purpose: Legacy Excel 97-2003 (.xls) spreadsheet text extraction parser.
# What the code does:
#   - Uses xlrd to open and extract content from BIFF8 .xls workbooks.
#   - Iterates through sheets, rows, and cells.
#   - Returns PageSegment per worksheet.
# Usage notes, dependencies, or assumptions:
#   - Requires xlrd.
#   - Cross-platform for Windows and macOS.

import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus


class XlsParser(BaseParser):
    """Parser for legacy Microsoft Excel 97-2003 (.xls) spreadsheets."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import xlrd
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "xls", ParseStatus.DEPENDENCY_MISSING, "xlrd is not installed."
            )

        book = None
        try:
            book = xlrd.open_workbook(abs_path, on_demand=True)
            sheet_names = book.sheet_names()

            for sheet_name in sheet_names:
                sheet = book.sheet_by_name(sheet_name)
                row_lines = []

                for row_idx in range(sheet.nrows):
                    row_vals = []
                    for col_idx in range(sheet.ncols):
                        val = sheet.cell_value(row_idx, col_idx)
                        if val is not None and str(val).strip():
                            row_vals.append(str(val).strip())
                    if row_vals:
                        row_lines.append(" | ".join(row_vals))

                sheet_text = "\n".join(row_lines).strip()
                if sheet_text:
                    segments.append(
                        PageSegment(segment_id=sheet_name, segment_type="sheet", text=sheet_text)
                    )

            return ExtractedDoc(
                file_path=abs_path,
                file_type="xls",
                total_segments=len(sheet_names),
                segments=segments,
            )
        except Exception as e:
            if "encrypted" in str(e).lower():
                return ExtractedDoc.failed(
                    abs_path, "xls", ParseStatus.ENCRYPTED, f"Workbook is encrypted: {e}"
                )
            return ExtractedDoc.from_exception(abs_path, "xls", "Error reading xls file", e)
        finally:
            if book is not None:
                book.release_resources()  # on_demand keeps the file open until released
