# Purpose: Modern PowerPoint (.pptx) presentation text extraction parser.
# What the code does:
#   - Uses python-pptx to iterate through slides, text frames, shapes, tables, and notes.
#   - Extracts slide-by-slide text with slide numbers.
#   - Protects against corrupted or password-encrypted presentations.
# Usage notes, dependencies, or assumptions:
#   - Requires python-pptx.
#   - Returns PageSegment per slide.

import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus


class PptxParser(BaseParser):
    """Parser for Microsoft PowerPoint (.pptx) presentations."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import pptx
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "pptx", ParseStatus.DEPENDENCY_MISSING, "python-pptx is not installed."
            )

        try:
            prs = pptx.Presentation(abs_path)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "pptx", "Cannot open pptx file", e)

        try:
            total_slides = len(prs.slides)
            for idx, slide in enumerate(prs.slides, start=1):
                slide_texts = []

                # Extract text from shapes and text frames
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for paragraph in shape.text_frame.paragraphs:
                            t = paragraph.text.strip()
                            if t:
                                slide_texts.append(t)
                    elif shape.has_table:
                        for row in shape.table.rows:
                            row_vals = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                            if row_vals:
                                slide_texts.append(" | ".join(row_vals))

                # Extract notes if present
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    note_text = slide.notes_slide.notes_text_frame.text.strip()
                    if note_text:
                        slide_texts.append(f"[Note] {note_text}")

                combined_slide_text = "\n".join(slide_texts).strip()
                if combined_slide_text:
                    segments.append(
                        PageSegment(
                            segment_id=str(idx),
                            segment_type="slide",
                            text=combined_slide_text
                        )
                    )

            return ExtractedDoc(
                file_path=abs_path,
                file_type="pptx",
                total_segments=total_slides,
                segments=segments
            )
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "pptx", "Error reading pptx content", e)
