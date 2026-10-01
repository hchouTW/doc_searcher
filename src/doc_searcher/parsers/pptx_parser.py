# Purpose: Modern PowerPoint (.pptx) presentation text extraction parser.
# What the code does:
#   - Uses python-pptx to iterate through slides, text frames, nested groups, inherited decorations, tables, and notes with source spans.
#   - Extracts slide-by-slide text with slide numbers.
#   - Protects against corrupted or password-encrypted presentations.
# Usage notes, dependencies, or assumptions:
#   - Requires python-pptx.
#   - Returns PageSegment per slide.

import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus, SourceTextBuilder


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
            warnings: list[dict] = []
            omitted: list[str] = []
            for idx, slide in enumerate(prs.slides, start=1):
                builder = SourceTextBuilder()
                self._extract_shapes(
                    slide.shapes, builder, idx, "slide", warnings=warnings, omitted=omitted
                )
                # Non-placeholder layout/master decorations are inherited by the slide.
                for origin, shapes in (
                    ("layout", slide.slide_layout.shapes),
                    ("master", slide.slide_layout.slide_master.shapes),
                ):
                    if origin == "master" and (
                        slide._element.get("showMasterSp") in {"0", "false"}
                        or slide.slide_layout._element.get("showMasterSp") in {"0", "false"}
                    ):
                        continue
                    self._extract_shapes(
                        shapes,
                        builder,
                        idx,
                        origin,
                        inherited=True,
                        warnings=warnings,
                        omitted=omitted,
                    )
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    text = slide.notes_slide.notes_text_frame.text.strip()
                    if text:
                        builder.append(
                            "[Note] " + text,
                            dict(kind="notes", slide=idx, location=f"slide {idx} notes"),
                            "\n",
                        )
                segment = builder.segment(str(idx), "slide")
                if segment.text.strip():
                    segments.append(segment)

            return ExtractedDoc(
                file_path=abs_path,
                file_type="pptx",
                total_segments=total_slides,
                segments=segments,
                warnings=warnings,
                omitted_locations=omitted,
            )
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "pptx", "Error reading pptx content", e)

    @classmethod
    def _extract_shapes(
        cls, shapes, builder, slide, origin, inherited=False, prefix="", warnings=None, omitted=None
    ):
        for shape in shapes:
            name = prefix + str(shape.shape_id)
            if inherited and shape.is_placeholder:
                continue  # inherited placeholder prompts are not rendered slide text
            if hasattr(shape, "shapes"):
                cls._extract_shapes(
                    shape.shapes, builder, slide, origin, inherited, name + "/", warnings, omitted
                )
            source = dict(
                kind="shape",
                slide=slide,
                shape=name,
                origin=origin,
                location=f"slide {slide} {origin} shape {name}",
            )
            if warnings is not None and (
                shape.has_chart
                or any(
                    node.tag.endswith("}oleObj")
                    or (
                        node.tag.endswith("}graphicData")
                        and node.get("uri", "").endswith("/diagram")
                    )
                    for node in shape._element.iter()
                )
            ):
                warnings.append(
                    dict(
                        code="unsupported_embedded_object",
                        location=source["location"],
                        message="Chart/embedded object content is not extracted.",
                    )
                )
                omitted.append(source["location"])
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    if paragraph.text.strip():
                        builder.append(paragraph.text, source, "\n")
            elif shape.has_table:
                for row_idx, row in enumerate(shape.table.rows, 1):
                    for col_idx, cell in enumerate(row.cells, 1):
                        if not cell.is_spanned and cell.text.strip():
                            builder.append(
                                cell.text,
                                {**source, "kind": "table_cell", "row": row_idx, "column": col_idx},
                                " | ",
                            )
