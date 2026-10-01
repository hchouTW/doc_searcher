"""File format and PDF cases (FMT-*, PDF-*) from docs/test-plan.md."""

import os
import time
from pathlib import Path

import pytest

from search_plan.helpers import make_scratch


def locations(item):
    return {(seg.segment_type, seg.segment_id) for seg in item.segments}


@pytest.mark.parametrize(
    "extension,location",
    [
        ("pdf", ("page", "2")),
        ("pptx", ("slide", "2")),
        ("xlsx", ("sheet", "Summary")),
        ("docx", None),
        ("txt", None),
        ("md", None),
        ("csv", None),
    ],
)
def test_fmt_01_07_each_format_is_indexed_and_reports_where_the_hit_is(env, extension, location):
    results = env.results(f"QAMARK{extension}")
    assert set(results) == {f"fmt/marker.{extension}"}
    item = results[f"fmt/marker.{extension}"]
    assert item.file_type == extension
    if location:
        assert location in locations(item)
    assert "<mark" in item.segments[0].snippets[0]


def test_fmt_01_07_shared_keyword_is_found_in_every_generated_format(env):
    hits = env.search("專案預算")
    expected = {f"fmt/marker.{e}" for e in ("pdf", "docx", "pptx", "xlsx", "txt", "md", "csv")}
    assert expected <= hits


def test_fmt_xls_is_covered_when_xlwt_can_write_it(env):
    if not (env.root / "fmt/marker.xls").exists():
        pytest.skip("xlwt not installed; see manifest 'unavailable'")
    assert env.search("QAMARKxls") == {"fmt/marker.xls"}


def test_fmt_08_unsupported_extensions_are_never_indexed(env):
    for marker in ("QANEGjson", "QANEGlog", "QANEGpng", "QANEGexe"):
        assert env.search(marker) == set()
    indexed = {row[0] for row in env.db.get_connection().execute("SELECT path FROM documents")}
    names = {Path(path).name for path in indexed if Path(path).parent.name == "neg"}
    assert names == {"pixel.png"}


ENCODING_MARKERS = {
    "enc/utf8_bom.txt": "QAENCutf8bom",
    "enc/utf16.txt": "QAENCutf16",
    "enc/utf32.txt": "QAENCutf32",
    "enc/big5.txt": "QAENCbig5",
    "enc/gbk.txt": "QAENCgbk",
}


@pytest.mark.parametrize("path,marker", ENCODING_MARKERS.items())
def test_fmt_09_every_encoding_is_readable_at_least_for_ascii(env, path, marker):
    assert env.search(marker) == {path}


def test_fmt_09_chinese_body_decodes_for_bom_and_big5_files(env):
    found = env.search("會議記錄")
    assert {"enc/utf8_bom.txt", "enc/utf16.txt", "enc/utf32.txt", "enc/big5.txt"} <= found


@pytest.mark.xfail(
    strict=True,
    reason="README known limitation: GBK text without a BOM is read as Big5 (mojibake)",
)
def test_fmt_09_gbk_without_bom_body_decodes(env):
    assert "会议记录" in env.results("QAENCgbk")["enc/gbk.txt"].segments[0].snippets[0]


def test_fmt_11_multi_sheet_workbook_indexes_every_sheet(env):
    item = env.results("QAMARKxlsx")["fmt/marker.xlsx"]
    assert ("sheet", "Summary") in locations(item)
    data_sheet = env.results("item value")  # header row of sheet "Data"
    assert "fmt/marker.xlsx" in data_sheet
    assert ("sheet", "Data") in locations(data_sheet["fmt/marker.xlsx"])


def test_pdf_01_text_pdf_reports_page_numbers(env):
    assert ("page", "2") in locations(env.results("QAMARKpdf")["fmt/marker.pdf"])
    assert ("page", "1") in locations(env.results("Nothing to find")["fmt/marker.pdf"])


def test_pdf_02_owner_password_pdf_text_is_extracted(env):
    assert env.search("QABADownerpdf") == {"bad/owner_password.pdf"}


def test_pdf_03_scanned_pdf_ocr_or_actionable_runtime_failure(env):
    from doc_searcher.parsers import parse_file

    scanned = parse_file(str(env.root / "scan/scanned.pdf"))
    if scanned.omitted_locations:
        assert env.search("QASCANkeyword") == set()
        assert any(w["code"] == "ocr_failed" for w in scanned.warnings)
    else:
        assert env.search("QASCANkeyword") == {"scan/scanned.pdf"}
    row = (
        env.db.get_connection()
        .execute("SELECT total_segments, error FROM documents WHERE filename = 'scanned.pdf'")
        .fetchone()
    )
    assert row is not None and row["error"] is None


def test_pdf_04_user_password_pdf_is_recorded_as_unreadable(env):
    row = (
        env.db.get_connection()
        .execute("SELECT error FROM documents WHERE filename = 'user_password.pdf'")
        .fetchone()
    )
    assert row is not None and "password" in row["error"].lower()


# ---- FMT-12: changes between scans ------------------------------------------------------------
def test_fmt_12_modified_and_deleted_files_follow_a_rescan(scratch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "keep.txt").write_text("QAKEEP stays", encoding="utf-8")
    changing = docs / "change.txt"
    changing.write_text("QAOLDTEXT here", encoding="utf-8")
    (docs / "gone.txt").write_text("QAGONE soon", encoding="utf-8")
    scratch.run([docs])
    assert scratch.names("QAOLDTEXT") == {"change.txt"}

    changing.write_text("QANEWTEXT here", encoding="utf-8")
    os.utime(changing, (time.time() + 10, time.time() + 10))
    (docs / "gone.txt").unlink()
    stats = scratch.run([docs])
    assert stats["deleted"] == 1 and stats["indexed"] == 1
    assert scratch.names("QAOLDTEXT") == set()
    assert scratch.names("QANEWTEXT") == {"change.txt"}
    assert scratch.names("QAGONE") == set()
    assert scratch.names("QAKEEP") == {"keep.txt"}


def test_fmt_12_unavailable_folder_keeps_its_index(scratch, tmp_path):
    docs = tmp_path / "usb"
    docs.mkdir()
    (docs / "file.txt").write_text("QAREMOVABLE data", encoding="utf-8")
    scratch.run([docs])
    parked = tmp_path / "usb-unplugged"
    docs.rename(parked)  # the folder disappears, like an unplugged drive
    stats = scratch.run([docs])
    assert stats["deleted"] == 0 and stats["skipped"]
    assert scratch.names("QAREMOVABLE") == {"file.txt"}
    parked.rename(docs)
    scratch.run([docs])
    assert scratch.names("QAREMOVABLE") == {"file.txt"}


# ---- legacy Office files (.doc, .ppt, .xls) from tests/sample_files -----------------------------
SAMPLE_FILES = Path(__file__).resolve().parents[1] / "sample_files"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    """tests/sample_files indexed: Word/PowerPoint-authored .doc/.ppt next to their modern twins."""
    scratch = make_scratch(tmp_path_factory.mktemp("legacy"))
    stats = scratch.run([SAMPLE_FILES])
    assert stats["indexed"] >= 8 and stats["failed"] == 0
    yield scratch
    scratch.db.close()


def _hits(legacy, query):
    return {item.filename: item for item in legacy.searcher.search(query, limit=50)}


def test_fmt_doc_word_authored_file_is_indexed_and_searchable(legacy):
    hits = _hits(legacy, "保密協定條款")
    assert {"sample_contract.doc", "sample_contract.docx"} <= set(hits)
    item = hits["sample_contract.doc"]
    assert item.file_type == "doc"
    assert "<mark" in item.segments[0].snippets[0]
    assert "sample_contract.doc" in _hits(legacy, "專案預算")


def test_fmt_ppt_powerpoint_authored_file_is_indexed_and_searchable(legacy):
    hits = _hits(legacy, "組織願景")
    assert {"sample_presentation.ppt", "sample_presentation.pptx"} <= set(hits)
    item = hits["sample_presentation.ppt"]
    assert item.file_type == "ppt"
    assert ("slide", "1") in locations(item)  # .ppt text arrives as one slide-1 block (README)
    assert "sample_presentation.ppt" in _hits(legacy, "季度營運業務報告")


def test_fmt_legacy_simplified_query_finds_traditional_text(legacy):
    assert "sample_contract.doc" in _hits(legacy, "保密协定条款")
    assert "sample_presentation.ppt" in _hits(legacy, "组织愿景")


def test_fmt_xls_legacy_workbook_is_indexed_and_searchable(legacy):
    hits = _hits(legacy, "預算")
    assert "sample_legacy_financial.xls" in hits
    assert hits["sample_legacy_financial.xls"].file_type == "xls"
