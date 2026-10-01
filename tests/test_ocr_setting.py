"""Tesseract OCR is off by default; enabling it re-extracts files indexed without OCR."""

import json
from io import BytesIO

import pymupdf
from PIL import Image

from doc_searcher.config import AppConfig
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.parsers import ParseStatus, parse_file
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (300, 100), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def scanned_pdf(path):
    with pymupdf.open() as document:
        document.new_page().insert_image(pymupdf.Rect(20, 50, 400, 200), stream=png_bytes())
        document.save(path)
    return path


def fake_tesseract(monkeypatch):
    calls = []

    def recognize(pixmap):
        calls.append(pixmap)
        return "會議 scanned"

    monkeypatch.setattr("doc_searcher.parsers.ocr.recognize", recognize)
    return calls


def test_parsers_skip_ocr_by_default_and_report_it(tmp_path, monkeypatch):
    calls = fake_tesseract(monkeypatch)
    image = tmp_path / "photo.png"
    image.write_bytes(png_bytes())

    pdf = parse_file(str(scanned_pdf(tmp_path / "scan.pdf")))
    picture = parse_file(str(image))

    assert calls == []
    assert pdf.status is ParseStatus.PARTIAL
    assert [w["code"] for w in pdf.warnings] == ["ocr_disabled"]
    assert pdf.omitted_locations == ["page 1"]
    assert picture.status is ParseStatus.EMPTY
    assert [w["code"] for w in picture.warnings] == ["ocr_disabled"]


def test_enabling_ocr_reextracts_unchanged_files_indexed_without_it(tmp_path, monkeypatch):
    calls = fake_tesseract(monkeypatch)
    root = tmp_path / "docs"
    root.mkdir()
    scanned_pdf(root / "scan.pdf")
    (root / "photo.png").write_bytes(png_bytes())
    (root / "notes.txt").write_text("plain")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)

    off = service.run(IndexRequest([str(root)]))
    assert off["indexed"] == 3 and calls == []
    assert len(db.paths_with_warning("ocr_disabled")) == 2
    assert DocumentSearcher(db).search("會議") == []

    on = service.run(IndexRequest([str(root)], ocr=True))
    assert on["indexed"] == 2  # Only the files that skipped OCR; notes.txt is unchanged.
    assert len(calls) == 2
    assert db.paths_with_warning("ocr_disabled") == []
    assert len(DocumentSearcher(db).search("會議")) == 2

    assert service.run(IndexRequest([str(root)], ocr=True))["indexed"] == 0
    db.close()


def test_ocr_reextraction_respects_request_scope(tmp_path, monkeypatch):
    fake_tesseract(monkeypatch)
    first, second = tmp_path / "a", tmp_path / "b"
    for root in (first, second):
        root.mkdir()
        scanned_pdf(root / "scan.pdf")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    service.run(IndexRequest([str(first), str(second)]))

    service.run(IndexRequest([str(first)], scope=[str(first)], ocr=True))

    assert db.paths_with_warning("ocr_disabled") == [
        db.get_document_by_path(str(second / "scan.pdf"))["path"]
    ]
    db.close()


def test_ocr_setting_defaults_off_and_persists(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(path)
    assert config.ocr_enabled is False

    config.ocr_enabled = True
    assert json.loads(path.read_text())["ocr_enabled"] is True
    assert AppConfig(path).ocr_enabled is True

    path.write_text(json.dumps({"ocr_enabled": "yes"}))
    assert AppConfig(path).ocr_enabled is False


def test_cli_ocr_flag_enables_ocr_for_the_run(tmp_path, monkeypatch, capsys):
    from doc_searcher.cli import main

    calls = fake_tesseract(monkeypatch)
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "docs"
    root.mkdir()
    scanned_pdf(root / "scan.pdf")

    assert main(["--dir", str(root), "--search", "會議", "--json"]) == 0
    assert calls == [] and json.loads(capsys.readouterr().out)["items"] == []

    assert main(["--dir", str(root), "--search", "會議", "--json", "--ocr"]) == 0
    assert len(calls) == 1 and len(json.loads(capsys.readouterr().out)["items"]) == 1
