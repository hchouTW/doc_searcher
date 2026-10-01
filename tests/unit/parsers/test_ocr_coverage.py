"""Scanned and mixed pages must OCR image content and retain native text on failure."""

from io import BytesIO
from PIL import Image
import pymupdf
import pytest
from doc_searcher.parsers import ocr, parse_file
from doc_searcher.parsers.pdf_parser import PdfParser


@pytest.fixture(autouse=True)
def ocr_enabled():
    """These tests cover OCR behavior, which is off unless a run enables it."""
    with ocr.enabled_scope(True):
        yield


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (300, 100), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def scanned_pdf(tmp_path, caption=None):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(pymupdf.Rect(20, 50, 400, 200), stream=image_bytes())
        if caption:
            page.insert_text((20, 30), caption)
        document.save(path)
    return path


def test_scanned_page_uses_ocr_with_provenance(tmp_path, monkeypatch):
    path = scanned_pdf(tmp_path)
    monkeypatch.setattr(
        "doc_searcher.parsers.pdf_parser.ocr.recognize", lambda pixmap: "會議 scanned", raising=True
    )
    result = PdfParser().parse(str(path))
    assert result.full_text == "會議 scanned"
    assert result.segments[0].sources[0]["source"]["kind"] == "ocr"


def test_mixed_page_keeps_native_text_on_ocr_failure(tmp_path, monkeypatch):
    path = scanned_pdf(tmp_path, "Native caption")

    def fail(pixmap):
        raise RuntimeError("OCR language data missing")

    monkeypatch.setattr("doc_searcher.parsers.pdf_parser.ocr.recognize", fail, raising=True)
    result = PdfParser().parse(str(path))
    assert "Native caption" in result.full_text
    assert result.status.value == "partial"
    assert result.omitted_locations == ["page 1 image 1"]
    assert any(w["code"] == "ocr_failed" for w in result.warnings)


def test_standalone_image_is_supported(tmp_path, monkeypatch):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes())
    monkeypatch.setattr("doc_searcher.parsers.ocr.recognize", lambda pixmap: "image keyword")
    result = parse_file(str(path))
    assert result.full_text == "image keyword"
    assert result.segments[0].sources[0]["source"]["kind"] == "ocr"


def test_real_ocr_scanned_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_SEARCHER_OCR_LANGUAGES", "eng")
    import shutil
    from PIL import ImageDraw, ImageFont

    if not shutil.which("tesseract"):
        pytest.skip("Tesseract runtime not installed")
    image = Image.new("RGB", (1500, 300), "white")
    fonts = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    import os

    font_path = next((path for path in fonts if os.path.isfile(path)), None)
    if font_path is None:
        pytest.skip("No suitable fixture font installed")
    font = ImageFont.truetype(font_path, 90)
    ImageDraw.Draw(image).text((40, 70), "MEETING COVERAGE 7429", font=font, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "real.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=750, height=150)
        page.insert_image(page.rect, stream=buffer.getvalue())
        document.save(path)
    result = PdfParser().parse(str(path))
    assert "MEETING" in result.full_text and "7429" in result.full_text


def test_ocr_cancellation_prevents_subprocess_work():
    from doc_searcher.parsers import ocr

    with ocr.cancellation_scope(lambda: True):
        with pytest.raises(ocr.OCRCancelled):
            ocr.recognize(None)


def test_native_overlay_on_image_does_not_inflate_ocr_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_SEARCHER_OCR_LANGUAGES", "eng")
    import shutil

    if not shutil.which("tesseract"):
        pytest.skip("Tesseract runtime not installed")
    path = tmp_path / "overlay.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image_bytes(), keep_proportion=False)
        page.insert_text((40, 80), "MEETING coverage", fontsize=24)
        document.save(path)
    result = PdfParser().parse(str(path))
    assert result.full_text.count("MEETING") == 1


@pytest.mark.posix
def test_running_ocr_process_is_cancelled_promptly(tmp_path, monkeypatch):
    import sys
    import time
    from doc_searcher.parsers import ocr

    executable = tmp_path / "fake-tesseract"
    executable.write_text(
        "#!"
        + sys.executable
        + '\nimport sys,time\nif "--list-langs" in sys.argv:\n print("languages:\\neng")\nelse:\n time.sleep(30)\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("DOC_SEARCHER_TESSERACT", str(executable))
    monkeypatch.setenv("DOC_SEARCHER_OCR_LANGUAGES", "eng")
    pixmap = pymupdf.Pixmap(image_bytes())
    started = time.monotonic()
    with ocr.cancellation_scope(lambda: time.monotonic() - started > 0.3):
        with pytest.raises(ocr.OCRCancelled):
            ocr.recognize(pixmap)
    assert time.monotonic() - started < 2


def test_existing_invisible_ocr_layer_does_not_duplicate_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_SEARCHER_OCR_LANGUAGES", "eng")
    import shutil
    from PIL import ImageDraw, ImageFont

    if not shutil.which("tesseract"):
        pytest.skip("Tesseract runtime not installed")
    import os

    fonts = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    font_path = next((p for p in fonts if os.path.isfile(p)), None)
    if not font_path:
        pytest.skip("Fixture font unavailable")
    image = Image.new("RGB", (1500, 300), "white")
    ImageDraw.Draw(image).text(
        (40, 70), "MEETING COVERAGE", font=ImageFont.truetype(font_path, 90), fill="black"
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "searchable-scan.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=750, height=150)
        page.insert_image(page.rect, stream=buffer.getvalue())
        page.insert_text((20, 77), "MEETING COVERAGE", fontsize=45, render_mode=3)
        document.save(path)
    result = PdfParser().parse(str(path))
    assert result.full_text.count("MEETING") == 1


def test_partial_native_word_never_discards_unrepresented_ocr_text():
    from doc_searcher.parsers.ocr import OCRText
    from doc_searcher.parsers.pdf_parser import _remove_native_duplicates
    from unittest.mock import Mock

    words = [
        dict(
            text="MEETINGCOVERAGE",
            left=20,
            top=20,
            width=300,
            height=40,
            block_num="1",
            par_num="1",
            line_num="1",
        )
    ]
    recognized = OCRText(words, 400, 100)
    page = Mock()
    page.get_text.return_value = [(20, 20, 156, 60, "MEETING", 0, 0, 0)]
    assert _remove_native_duplicates(recognized, page, (400, 0, 0, 100, 0, 0)) == "MEETINGCOVERAGE"


def test_repeated_raster_word_at_other_position_is_retained():
    from doc_searcher.parsers.ocr import OCRText
    from doc_searcher.parsers.pdf_parser import _remove_native_duplicates
    from unittest.mock import Mock

    words = [
        dict(
            text="MEETING",
            left=20,
            top=y,
            width=136,
            height=40,
            block_num="1",
            par_num="1",
            line_num=str(y),
        )
        for y in (20, 120)
    ]
    page = Mock()
    page.get_text.return_value = [(20, 20, 156, 60, "MEETING", 0, 0, 0)]
    assert (
        _remove_native_duplicates(OCRText(words, 400, 200), page, (400, 0, 0, 200, 0, 0))
        == "MEETING"
    )


def test_native_multicolumn_multipage_text_preserves_all_keywords(tmp_path):
    path = tmp_path / "columns.pdf"
    with pymupdf.open() as document:
        for number in range(3):
            page = document.new_page(width=600, height=800)
            page.insert_textbox(
                pymupdf.Rect(20, 20, 270, 700), f"LEFTMEETING{number}\n" * 12, fontsize=12
            )
            page.insert_textbox(
                pymupdf.Rect(310, 20, 580, 700), f"RIGHTMEETING{number}\n" * 12, fontsize=12
            )
        document.save(path)
    result = PdfParser().parse(str(path))
    assert len(result.segments) == 3
    for number, segment in enumerate(result.segments):
        assert segment.text.count(f"LEFTMEETING{number}") == 12
        assert segment.text.count(f"RIGHTMEETING{number}") == 12
