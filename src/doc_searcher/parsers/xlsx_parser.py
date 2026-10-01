# Purpose: Modern Excel (.xlsx) workbook text extraction parser.
# What the code does:
#   - Streams source formulas and cached values separately without evaluating formulas.
#   - Iterates through worksheets and non-empty rows.
#   - Preserves worksheets (including hidden sheets) and cell-level original-text spans.
#   - Reads cell comments including empty cells through ZIP relationships.
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
            from .xlsx_comments import read_comments

            try:
                comments, warnings = read_comments(abs_path)
            except Exception as exc:
                comments = {}
                warnings = [
                    dict(code="comments_failed", location="workbook comments", message=str(exc))
                ]
            omitted = [warning["location"] for warning in warnings]
            for sheet_name in source_wb.sheetnames:
                builder = SourceTextBuilder()
                source_sheet, cached_sheet = source_wb[sheet_name], cached_wb[sheet_name]
                for source_row, cached_row in zip(
                    source_sheet.iter_rows(), cached_sheet.iter_rows(), strict=True
                ):
                    for cell, cached in zip(source_row, cached_row, strict=True):
                        value = cell.value
                        if value is None:
                            continue  # includes merged-cell followers
                        source = dict(
                            worksheet=sheet_name,
                            cell=cell.coordinate,
                            location=f"{sheet_name}!{cell.coordinate}",
                            kind="value",
                        )
                        if cell.data_type == "f":
                            formula = (
                                value if isinstance(value, str) else getattr(value, "text", None)
                            )
                            if not isinstance(value, str):
                                source = {
                                    **source,
                                    "range": getattr(value, "ref", None),
                                    "formula_type": getattr(value, "t", "unknown"),
                                }
                            if formula:
                                builder.append(
                                    formula,
                                    {**source, "kind": "formula", "cached_value": cached.value},
                                    " | ",
                                )
                            else:
                                warnings.append(
                                    dict(
                                        code="unsupported_formula",
                                        location=source["location"],
                                        message="Formula representation has no readable source text; not evaluated.",
                                    )
                                )
                                omitted.append(source["location"])
                            if cached.value is None:
                                warnings.append(
                                    dict(
                                        code="missing_cached_result",
                                        location=source["location"],
                                        message=(
                                            "Formula source indexed; cached result missing; formula not evaluated."
                                            if formula
                                            else "Cached result missing; formula source unavailable; formula not evaluated."
                                        ),
                                    )
                                )
                            elif (
                                str(cached.value).strip()
                                and formula != '="' + str(cached.value).replace('"', '""') + '"'
                            ):
                                builder.append(str(cached.value), source, " | ")
                        elif str(value).strip():
                            builder.append(str(value), source, " | ")
                for coordinate, comment in comments.get(sheet_name, []):
                    builder.append(
                        comment,
                        dict(
                            worksheet=sheet_name,
                            cell=coordinate,
                            location=f"{sheet_name}!{coordinate}",
                            kind="comment",
                        ),
                        "\n",
                    )
                segment = builder.segment(sheet_name, "sheet")
                if segment.text.strip():
                    segments.append(segment)
            return ExtractedDoc(
                abs_path,
                "xlsx",
                len(source_wb.sheetnames),
                segments,
                warnings=warnings,
                omitted_locations=omitted,
            )
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "xlsx", "Error reading xlsx file", e)
        finally:
            for workbook in (source_wb, cached_wb):
                if workbook is not None:
                    workbook.close()
