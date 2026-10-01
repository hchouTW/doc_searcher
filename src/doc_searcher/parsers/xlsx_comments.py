# Purpose: Read XLSX comments without loading whole worksheets into memory.
# Behavior: Resolve workbook/sheet relationships and retain ordinary comment text/cells.
# Usage: Local ZIP parts only; threaded comments are reported as unsupported.
import posixpath
from zipfile import ZipFile
from lxml import etree

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _xml(archive, name):
    return etree.fromstring(
        archive.read(name), etree.XMLParser(resolve_entities=False, no_network=True)
    )


def _relationships(archive, part):
    directory, name = posixpath.split(part)
    rels = posixpath.join(directory, "_rels", name + ".rels")
    if rels not in archive.namelist():
        return {}
    result = {}
    for rel in _xml(archive, rels):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        result[rel.get("Id")] = (
            posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else posixpath.join(directory, target)
            ),
            rel.get("Type", ""),
        )
    return result


def read_comments(path):
    """Return worksheet comments and coverage warnings; never follow external links."""
    comments, warnings = {}, []
    with ZipFile(path) as archive:
        workbook = _xml(archive, "xl/workbook.xml")
        relations = _relationships(archive, "xl/workbook.xml")
        for sheet in workbook.iter(_S + "sheet"):
            sheet_name = sheet.get("name")
            part, _ = relations[sheet.get(_R + "id")]
            values = []
            for target, kind in _relationships(archive, part).values():
                if kind.endswith("/comments"):
                    for comment in _xml(archive, target).iter(_S + "comment"):
                        text = "".join(node.text or "" for node in comment.iter(_S + "t"))
                        if text.strip():
                            values.append((comment.get("ref"), text))
                elif kind.endswith(("/oleObject", "/package")):
                    warnings.append(
                        dict(
                            code="unsupported_embedded_object",
                            location=f"{sheet_name}:{target}",
                            message="Embedded Office object content is not extracted.",
                        )
                    )
                elif "threadedComment" in kind:
                    warnings.append(
                        dict(
                            code="unsupported_threaded_comments",
                            location=sheet_name,
                            message="Threaded Excel comments are not extracted.",
                        )
                    )
            comments[sheet_name] = values
    return comments, warnings
