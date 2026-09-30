# Purpose: Modern Word (.docx) document text extraction parser.
# What the code does:
#   - Traverses explicit Word text nodes in XML order, with body/header/footer/text-box sources.
#   - Shared header/footer parts and physical merged cells are extracted once.
#   - Handles protected or malformed files gracefully without unhandled exceptions.
# Usage notes, dependencies, or assumptions:
#   - Requires python-docx.
#   - Returns PageSegment grouped by structural sections or blocks.

import os
from typing import List
from .base import BaseParser, ExtractedDoc, PageSegment, ParseStatus


class DocxParser(BaseParser):
    """Parser for Microsoft Word (.docx) documents."""

    def parse(self, file_path: str) -> ExtractedDoc:
        abs_path = os.path.abspath(file_path)
        segments: List[PageSegment] = []

        try:
            import docx
        except ImportError:
            return ExtractedDoc.failed(
                abs_path, "docx", ParseStatus.DEPENDENCY_MISSING, "python-docx is not installed."
            )

        try:
            doc = docx.Document(abs_path)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "docx", "Cannot open docx file", e)

        try:
            from docx.oxml.ns import qn
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
            from .base import SourceTextBuilder
            warnings, omitted = [], []
            parts = [(str(doc.part.partname), "body", doc.element.body)]
            seen = set()
            for relation in doc.part.rels.values():
                if relation.reltype in (RT.HEADER, RT.FOOTER):
                    part = relation.target_part
                    name = str(part.partname)
                    if name not in seen:
                        seen.add(name)
                        parts.append((name, "header" if relation.reltype == RT.HEADER else "footer", part.element))
            for part_name, part_kind, root in parts:
                builder, previous = SourceTextBuilder(), None
                paragraphs = {node: i for i, node in enumerate(root.iter(qn("w:p")), 1)}
                for node in root.iter():
                    if node.tag in (qn("w:altChunk"), qn("w:object")):
                        location = f"{part_name}:{node.getroottree().getpath(node)}"
                        warnings.append(dict(code="unsupported_object", location=location,
                                             message="Embedded Office objects are not extracted."))
                        omitted.append(location)
                    if node.tag not in (qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
                        continue
                    ancestors = list(node.iterancestors())
                    paragraph = next((a for a in ancestors if a.tag == qn("w:p")), None)
                    if paragraph is None:
                        continue
                    kind = part_kind
                    if any(a.tag == qn("w:txbxContent") for a in ancestors):
                        kind = "text_box"
                    elif part_kind == "body":
                        kind = "table_cell" if any(a.tag == qn("w:tc") for a in ancestors) else "paragraph"
                    source = dict(kind=kind, part=part_name, paragraph=paragraphs[paragraph],
                                  location=paragraph.getroottree().getpath(paragraph))
                    text = node.text or "" if node.tag == qn("w:t") else "\t" if node.tag == qn("w:tab") else "\n"
                    builder.append(text, source, "" if source == previous else "\n\n")
                    previous = source
                segment = builder.segment(part_name, "section")
                if segment.text.strip():
                    segments.append(segment)
            return ExtractedDoc(abs_path, "docx", len(segments), segments,
                                warnings=warnings, omitted_locations=omitted)
        except Exception as e:
            return ExtractedDoc.from_exception(abs_path, "docx", "Error reading docx content", e)
