# Purpose: Modern Excel (.xlsx) workbook text extraction parser.
# What the code does:
#   - Streams source formulas and cached values separately without evaluating formulas.
#   - Iterates through worksheets and non-empty rows.
#   - Preserves worksheets (including hidden sheets) and cell-level original-text spans.
#   - Reports missing cached formula results instead of silently treating them as empty.
# Usage notes, dependencies, or assumptions:
#   - Requires openpyxl.
#   - Returns PageSegment per worksheet.

import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus


class XlsxParser(BaseParser):
    """Parser for Microsoft Excel (.xlsx) spreadsheets using openpyxl."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import openpyxl
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "xlsx", ParseStatus.DEPENDENCY_MISSING, "openpyxl is not installed."
            )

        source_wb = cached_wb = None
        try:
            from .base import SourceTextBuilder
            source_wb = openpyxl.load_workbook(abs_path, read_only=True, data_only=False)
            cached_wb = openpyxl.load_workbook(abs_path, read_only=True, data_only=True)
            warnings = []
            for sheet_name in source_wb.sheetnames:
                builder = SourceTextBuilder()
                source_sheet, cached_sheet = source_wb[sheet_name], cached_wb[sheet_name]
                for source_row, cached_row in zip(source_sheet.iter_rows(), cached_sheet.iter_rows(), strict=True):
                    for cell, cached in zip(source_row, cached_row, strict=True):
                        value = cell.value
                        if value is None:
                            continue  # includes merged-cell followers
                        source = dict(worksheet=sheet_name, cell=cell.coordinate,
                                      location=f"{sheet_name}!{cell.coordinate}", kind="value")
                        if cell.data_type == "f":
                            builder.append(str(value), {**source, "kind": "formula", "cached_value": cached.value}, " | ")
                            if cached.value is None:
                                warnings.append(dict(code="missing_cached_result", location=source["location"],
                                    message="Formula source indexed; cached result missing; formula not evaluated."))
                            elif str(cached.value).strip() and str(value) != '="' + str(cached.value).replace('"', '""') + '"':
                                builder.append(str(cached.value), source, " | ")
                        elif str(value).strip():
                            builder.append(str(value), source, " | ")
                segment = builder.segment(sheet_name, "sheet")
                if segment.text.strip():
                    segments.append(segment)
            return ExtractedDoc(abs_path, "xlsx", len(source_wb.sheetnames), segments, warnings=warnings)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "xlsx", "Error reading xlsx file", e)
        finally:
            for workbook in (source_wb, cached_wb):
                if workbook is not None:
                    workbook.close()
