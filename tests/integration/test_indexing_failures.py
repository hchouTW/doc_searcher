"""A bad document, page, or parser never stops batch indexing (Task 2.3)."""

import os

import pytest

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.parsers import get_parser
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database
from fixtures.platform import RUNNING_AS_ROOT


def make_pdf(path, pages, **save_kwargs):
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(str(path), **save_kwargs)
    doc.close()
    return path


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "index.db"))
    yield database
    database.close()


def test_mixed_batch_indexes_good_files_and_records_failures(tmp_path, db, monkeypatch):
    import pymupdf

    docs = tmp_path / "docs"
    docs.mkdir()
    good_txt = docs / "good.txt"
    good_txt.write_text("alpha budget", encoding="utf-8")
    good_pdf = make_pdf(docs / "good.pdf", ["beta budget"])
    corrupt = docs / "corrupt.docx"
    corrupt.write_bytes(b"not a zip")
    locked = make_pdf(
        docs / "locked.pdf",
        ["secret"],
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="u",
        owner_pw="o",
    )
    crashing = docs / "crash.xlsx"
    crashing.write_bytes(b"")
    monkeypatch.setattr(get_parser(str(crashing)), "parse", lambda path: 1 / 0)
    vanished = docs / "vanished.txt"  # listed by the scan, deleted before parsing

    files = [str(p) for p in (good_txt, corrupt, locked, crashing, vanished, good_pdf)]
    stats = DocumentIndexer(db).run_batch_indexing(files, [])

    assert stats["indexed"] == 2
    assert stats["failed"] == 4
    assert stats["cancelled"] is False
    assert {r.path for r in DocumentSearcher(db).search("budget")} == {str(good_txt), str(good_pdf)}
    assert db.get_document_by_path(str(corrupt))["error"].startswith("Cannot open docx file")
    assert "password" in db.get_document_by_path(str(locked))["error"]
    assert "Parser exception" in db.get_document_by_path(str(crashing))["error"]
    assert db.get_document_by_path(str(vanished)) is None


@pytest.mark.posix
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root ignores file permissions")
def test_permission_denied_file_does_not_stop_batch(tmp_path, db):
    readable = tmp_path / "ok.txt"
    readable.write_text("gamma budget", encoding="utf-8")
    denied = tmp_path / "denied.txt"
    denied.write_text("hidden", encoding="utf-8")
    os.chmod(denied, 0)
    try:
        stats = DocumentIndexer(db).run_batch_indexing([str(denied), str(readable)], [])
    finally:
        os.chmod(denied, 0o644)
    assert stats["indexed"] == 1 and stats["failed"] == 1
    assert "Cannot read file" in db.get_document_by_path(str(denied))["error"]


def test_one_bad_pdf_page_keeps_the_other_pages(tmp_path, db, monkeypatch):
    import pymupdf

    path = make_pdf(
        tmp_path / "pages.pdf", ["first page delta", "second page broken", "third page delta"]
    )
    real_get_text = pymupdf.Page.get_text

    def flaky_get_text(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError("damaged content stream")
        return real_get_text(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", flaky_get_text)
    stats = DocumentIndexer(db).run_batch_indexing([str(path)], [])

    assert stats["indexed"] == 1
    results = DocumentSearcher(db).search("delta")
    assert [seg.segment_id for seg in results[0].segments] == ["1", "3"]
