# Purpose: Make supported standalone images searchable using local OCR.
# Behavior: Process every TIFF frame or single PNG/JPEG with bounded rendering and provenance.
# Usage: PyMuPDF, Pillow (TIFF frame decoding), and Tesseract with configured language data.
#   With OCR off the image is recorded without text and an "ocr_disabled" warning.
import os
from .base import BaseParser, ExtractedDoc, ParseStatus, SourceTextBuilder
from . import ocr


class ImageParser(BaseParser):
    def parse(self, file_path: str) -> ExtractedDoc:
        import pymupdf

        path = os.path.abspath(file_path)
        extension = os.path.splitext(path)[1][1:].lower()
        segments, warnings, omitted = [], [], []
        if not ocr.is_enabled():
            return ExtractedDoc(
                path,
                extension,
                warnings=[
                    dict(
                        code="ocr_disabled",
                        location="image",
                        message="Tesseract OCR is off; image text was not recognized.",
                    )
                ],
                omitted_locations=["image"],
            )
        try:
            from PIL import Image, ImageSequence

            with Image.open(path) as image:
                frames = getattr(image, "n_frames", 1)
                for number, frame in enumerate(ImageSequence.Iterator(image), 1):
                    ocr.check_cancelled()
                    location = f"image {number}"
                    try:
                        rgb = frame.convert("RGB")
                        rgb.thumbnail((3500, 3500))
                        pixmap = pymupdf.Pixmap(
                            pymupdf.csRGB, rgb.width, rgb.height, rgb.tobytes(), False
                        )
                        text = ocr.recognize(pixmap)
                        builder = SourceTextBuilder()
                        builder.append(text, dict(kind="ocr", location=location, frame=number))
                        if text:
                            segments.append(builder.segment(str(number), "image"))
                        else:
                            warnings.append(
                                dict(
                                    code="ocr_no_text",
                                    location=location,
                                    message="OCR found no recognizable text.",
                                )
                            )
                    except Exception as exc:
                        warnings.append(
                            dict(code="ocr_failed", location=location, message=str(exc))
                        )
                        omitted.append(location)
            return ExtractedDoc(
                path,
                extension,
                frames,
                segments,
                warnings=warnings,
                omitted_locations=omitted,
                status=ParseStatus.PARTIAL if omitted else None,
            )
        except Exception as exc:
            return ExtractedDoc.from_exception(path, extension, "Cannot read image", exc)
