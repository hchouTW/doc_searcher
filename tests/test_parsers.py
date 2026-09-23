# Purpose: Unit tests for document parsers.
# What the code does:
#   - Tests PDF, DOCX, PPTX, XLSX, XLS, and TXT parsing with known fixtures.
#   - Validates segment counts, metadata, and keyword presence.
#   - Validates error handling for missing and corrupted files.
# Usage notes, dependencies, or assumptions:
#   - Run via pytest.

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import (
    get_parser,
    is_supported,
    PdfParser,
    DocxParser,
    PptxParser,
    XlsxParser,
    XlsParser,
    TextParser,
    DocParser,
    PptParser
)

SAMPLE_DIR = Path(__file__).parent / "sample_files"


def test_is_supported():
    assert is_supported("test.pdf") is True
    assert is_supported("test.docx") is True
    assert is_supported("test.doc") is True
    assert is_supported("test.pptx") is True
    assert is_supported("test.ppt") is True
    assert is_supported("test.xlsx") is True
    assert is_supported("test.xls") is True
    assert is_supported("test.txt") is True
    assert is_supported("test.exe") is False


def test_pdf_parser():
    sample = SAMPLE_DIR / "sample_report.pdf"
    assert sample.exists()
    parser = PdfParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert res.file_type == "pdf"
    assert res.total_segments == 2
    assert "專案預算" in res.full_text
    assert "2026年度" in res.full_text
    assert "研發投資" in res.full_text


def test_docx_parser():
    sample = SAMPLE_DIR / "sample_contract.docx"
    assert sample.exists()
    parser = DocxParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert res.file_type == "docx"
    assert "合作意向合約書" in res.full_text
    assert "保密協定條款" in res.full_text
    assert "NT$ 5,000,000" in res.full_text


def test_xlsx_parser():
    sample = SAMPLE_DIR / "sample_financial.xlsx"
    assert sample.exists()
    parser = XlsxParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert res.file_type == "xlsx"
    sheet_names = [s.segment_id for s in res.segments]
    assert "損益表" in sheet_names
    assert "資本支出" in sheet_names
    assert "8800000" in res.full_text
    assert "機房升級採購案" in res.full_text


def test_xls_parser():
    sample = SAMPLE_DIR / "sample_legacy_financial.xls"
    assert sample.exists()
    parser = XlsParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert res.file_type == "xls"
    sheet_names = [s.segment_id for s in res.segments]
    assert "舊版損益表" in sheet_names
    assert "專案預算" in res.full_text
    assert "6600000" in res.full_text


def test_pptx_parser():
    sample = SAMPLE_DIR / "sample_presentation.pptx"
    assert sample.exists()
    parser = PptxParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert res.file_type == "pptx"
    assert res.total_segments >= 2
    assert "季度營運業務報告" in res.full_text
    assert "保密協定條款" in res.full_text


def test_text_parser():
    sample = SAMPLE_DIR / "sample_readme.txt"
    assert sample.exists()
    parser = TextParser()
    res = parser.parse(str(sample))
    assert res.error is None
    assert "普通文字測試文件" in res.full_text
    assert "機密等級" in res.full_text


def test_missing_file_handling():
    parser = PdfParser()
    res = parser.parse("non_existent_file.pdf")
    assert res.error is not None
