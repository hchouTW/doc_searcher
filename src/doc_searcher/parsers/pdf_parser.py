# Purpose: PDF document text extraction parser.
# What the code does:
#   - Extracts sorted native text and OCRs scanned/image regions through bounded Tesseract subprocesses.
#   - With OCR off, those regions are omitted and reported as "ocr_disabled" warnings.
#   - Reports ENCRYPTED only when a user password is required to read the text.
#   - Preserves readable pages and reports failed/no-native-text page locations.
# Usage notes, dependencies, or assumptions:
#   - Requires pymupdf.
#   - Returns PageSegment per page with 1-based page numbers.

import logging
import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus, SourceTextBuilder
from . import ocr


_messages_routed = False


def _route_mupdf_messages(pymupdf) -> None:
    """MuPDF prints warnings such as "MuPDF error: ..." to stdout by default, which would mix
    with CLI results and corrupt the MCP stdio stream; send them to Python logging instead."""
    global _messages_routed
    if not _messages_routed:
        pymupdf.set_messages(pylogging_name=__name__, pylogging_level=logging.WARNING)
        _messages_routed = True


class PdfParser(BaseParser):
    """Parser for PDF (.pdf) documents using PyMuPDF."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import pymupdf

            _route_mupdf_messages(pymupdf)
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "pdf", ParseStatus.DEPENDENCY_MISSING, "pymupdf is not installed."
            )

        try:
            doc = pymupdf.open(abs_path)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "pdf", "Cannot open PDF", e)

        try:
            # Owner-password-only PDFs (permissions) open without a password and stay readable.
            if doc.needs_pass:
                return ExtractedDoc.failed(
                    abs_path, "pdf", ParseStatus.ENCRYPTED, "Document is password protected."
                )

            warnings, omitted = [], []
            total_pages = len(doc)
            for page_idx in range(total_pages):
                ocr.check_cancelled()
                try:
                    page = doc.load_page(page_idx)
                    text = page.get_text("text", sort=True).strip()
                    builder = SourceTextBuilder()
                    location = f"page {page_idx + 1}"
                    if text:
                        builder.append(
                            text, dict(kind="native", page=page_idx + 1, location=location)
                        )
                    images = page.get_image_info()
                    threshold = int(os.environ.get("DOC_SEARCHER_OCR_MIN_CHARS", "40"))
                    sparse = len("".join(text.split())) < threshold
                    # Native-only sparse/blank pages do not need recognition. Mixed pages
                    # OCR image regions independently, keeping original text and its offsets.
                    clips = []
                    if images:
                        if not text and sparse:
                            clips = [(location, None, None)]
                        else:
                            seen = set()
                            # OCR original image pixels, excluding native text overlaid on them.
                            image_blocks = [
                                block
                                for block in page.get_text(
                                    "dict", flags=pymupdf.TEXT_PRESERVE_IMAGES
                                )["blocks"]
                                if block.get("type") == 1
                            ]
                            for image in images:
                                bounds = tuple(image["bbox"])
                                if bounds not in seen:
                                    seen.add(bounds)
                                    rect = pymupdf.Rect(bounds) & page.rect
                                    if rect.width >= 20 and rect.height >= 20:
                                        image_block = next(
                                            (
                                                block
                                                for block in image_blocks
                                                if tuple(block["bbox"]) == bounds
                                            ),
                                            None,
                                        )
                                        if image_block is not None:
                                            clips.append(
                                                (
                                                    f"{location} image {len(clips) + 1}",
                                                    image_block["image"],
                                                    image.get("transform"),
                                                )
                                            )
                                        else:
                                            warnings.append(
                                                dict(
                                                    code="image_pixels_unavailable",
                                                    location=location,
                                                    message="Original image pixels unavailable; mixed-page OCR skipped to avoid duplicate native text.",
                                                )
                                            )
                                            omitted.append(location)
                    elif not text and page.get_drawings():
                        clips = [
                            (location, None, None)
                        ]  # text sometimes consists of vector outlines
                    if clips and not ocr.is_enabled():
                        warnings.append(
                            dict(
                                code="ocr_disabled",
                                location=location,
                                message="Tesseract OCR is off; image text was not recognized.",
                            )
                        )
                        omitted.append(location)
                    for ocr_location, clip, transform in clips if ocr.is_enabled() else []:
                        try:
                            recognized = ocr.recognize(
                                ocr.render_image(clip)
                                if isinstance(clip, bytes)
                                else ocr.render(page, clip)
                            )
                            already_represented = bool(recognized)
                            if isinstance(recognized, ocr.OCRText) and transform and text:
                                recognized = _remove_native_duplicates(recognized, page, transform)
                            if recognized:
                                builder.append(
                                    recognized,
                                    dict(kind="ocr", page=page_idx + 1, location=ocr_location),
                                    "\n",
                                )
                            elif not already_represented:
                                warnings.append(
                                    dict(
                                        code="ocr_no_text",
                                        location=ocr_location,
                                        message="OCR found no recognizable text.",
                                    )
                                )
                        except Exception as exc:
                            warnings.append(
                                dict(code="ocr_failed", location=ocr_location, message=str(exc))
                            )
                            omitted.append(ocr_location)
                    segment = builder.segment(str(page_idx + 1), "page")
                    if segment.text.strip():
                        segments.append(segment)
                    elif not clips:
                        warnings.append(
                            dict(
                                code="no_native_text",
                                location=location,
                                message="No native text extracted; blank page.",
                            )
                        )

                except Exception as exc:
                    location = f"page {page_idx + 1}"
                    warnings.append(dict(code="page_failed", location=location, message=str(exc)))
                    omitted.append(location)

            return ExtractedDoc(
                file_path=abs_path,
                file_type="pdf",
                total_segments=total_pages,
                segments=segments,
                warnings=warnings,
                omitted_locations=omitted,
                status=ParseStatus.PARTIAL if omitted else None,
            )
        finally:
            doc.close()


def _remove_native_duplicates(recognized, page, transform):
    """Remove only OCR words already represented by native text at the same position."""
    import pymupdf
    from doc_searcher.search.script_fold import fold

    native = page.get_text("words")
    remaining = []
    matrix = pymupdf.Matrix(transform)
    for word in recognized.words:
        bounds = (
            pymupdf.Rect(
                word["left"] / recognized.width,
                word["top"] / recognized.height,
                (word["left"] + word["width"]) / recognized.width,
                (word["top"] + word["height"]) / recognized.height,
            )
            * matrix
        )
        token = fold(word["text"]).casefold()
        duplicate = False
        for existing in native:
            native_token = fold(existing[4]).casefold()
            overlap = bounds & pymupdf.Rect(existing[:4])
            if token and token in native_token and overlap.get_area() > bounds.get_area() * 0.25:
                duplicate = True
                break
        if not duplicate:
            remaining.append(word)
    return ocr.word_text(remaining)
