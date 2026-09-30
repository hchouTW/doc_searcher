# Purpose: Build the synthetic test dataset (TD-*) that docs/test-plan.md runs its cases against.
# What the code does:
#   - build_dataset(target) writes small, deterministic documents for every group in the plan:
#     zh (Traditional/Simplified), en, mix (special characters), fmt (one marker per format),
#     enc (text encodings), tree (subfolder scope), bad (corrupt/locked/protected), neg
#     (unsupported extensions), scan (image-only PDF) and path (awkward file names).
#   - Writes manifest.json listing every file with its group, expected outcome and search markers,
#     plus the formats this machine could not produce (with the reason), so tests can skip on them.
#   - OOXML zips get fixed timestamps, document properties are pinned and mtimes are set to a
#     constant, so two runs produce byte-identical files (manifest field hash_stable marks the
#     exceptions, e.g. encrypted PDFs, whose encryption is randomized).
# Usage notes, dependencies, or assumptions:
#   - CLI: python tests/search_plan/dataset.py OUTDIR   (OUTDIR must be new, empty, or hold a
#     manifest.json from an earlier run; nothing outside OUTDIR is touched).
#   - Needs pymupdf, python-docx, python-pptx, openpyxl. xlwt is optional (.xls); legacy .doc and
#     .ppt cannot be authored without Microsoft Office/LibreOffice and are reported as unavailable.
#   - All content is synthetic; markers look like QAMARKpdf (letters and digits only, on purpose:
#     see docs/test-plan.md, Known defects) and are unique across the dataset.
#   - Permission-based cases (chmod 000, symlink loop) are created on POSIX only, and
#     "locked.txt" is only unreadable when not running as root.

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

FIXED_TIME = datetime(2026, 1, 1, 0, 0, 0)
FIXED_EPOCH = FIXED_TIME.timestamp()
MANIFEST_NAME = "manifest.json"

# Expected outcomes recorded in the manifest (what a correct DocSearcher does with the file).
INDEXED = "indexed"  # parsed, searchable by its markers
NO_TEXT = "no_text"  # valid file, nothing to search (image-only PDF)
SKIPPED_UNSUPPORTED = "skipped_unsupported"  # extension not supported, must be ignored
SKIPPED_UNREADABLE = "skipped_unreadable"  # corrupt/locked/encrypted: recorded, never fatal
SKIPPED_TEMP = "skipped_temp"  # Office lock file (~$...), ignored by the scanner


@dataclass
class Entry:
    path: str  # relative to the dataset root, "/" separated
    group: str
    expect: str
    markers: List[str] = field(default_factory=list)
    hash_stable: bool = True
    note: str = ""


@dataclass
class Unavailable:
    path: str
    group: str
    reason: str


class _Builder:
    def __init__(self, root: Path):
        self.root = root
        self.entries: List[Entry] = []
        self.unavailable: List[Unavailable] = []

    # -- helpers ---------------------------------------------------------------------------
    def target(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def record(self, relative: str, group: str, expect: str, markers=(), **kwargs) -> None:
        self.entries.append(Entry(relative, group, expect, list(markers), **kwargs))

    def skip(self, relative: str, group: str, reason: str) -> None:
        self.unavailable.append(Unavailable(relative, group, reason))

    def text(self, relative, group, content, markers=(), encoding="utf-8", expect=INDEXED, **kw):
        path = self.target(relative)
        path.write_bytes(content.encode(encoding))
        self.record(relative, group, expect, markers, **kw)
        return path


# -- format writers ------------------------------------------------------------------------
_MODIFIED_RE = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")
_PDF_STRING = rb"(?:<[0-9a-fA-F]*>|\((?:\\.|[^\\)])*\))"  # MuPDF writes /ID parts as hex or literal
_PDF_ID_RE = re.compile(
    rb"/ID\s*\[\s*" + _PDF_STRING + rb"\s*" + _PDF_STRING + rb"\s*\]", re.DOTALL
)
_FIXED_PDF_ID = b"/ID[<" + b"0" * 32 + b"><" + b"0" * 32 + b">]"


def _pin_pdf_id(path: Path) -> None:
    """Replace the random trailer /ID; it sits after the xref table, so no offset moves."""
    data = path.read_bytes()
    path.write_bytes(_PDF_ID_RE.sub(lambda match: _FIXED_PDF_ID, data, count=1))


def _normalise_zip(path: Path) -> None:
    """Rewrite an OOXML zip with fixed timestamps so identical content gives identical bytes."""
    with zipfile.ZipFile(path) as source:
        items = [(info.filename, source.read(info.filename)) for info in source.infolist()]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in items:
            if name == "docProps/core.xml":  # openpyxl stamps "now" into dcterms:modified on save
                data = _MODIFIED_RE.sub(rb"\g<1>2026-01-01T00:00:00Z\g<2>", data)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, data)
    os.replace(temporary, path)


def write_docx(path: Path, paragraphs: List[str]) -> None:
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    props = document.core_properties
    props.author = props.last_modified_by = "qa"
    props.created = props.modified = props.last_printed = FIXED_TIME
    document.save(str(path))
    _normalise_zip(path)


def write_pptx(path: Path, slides: List[str]) -> None:
    import pptx

    presentation = pptx.Presentation()
    for text in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = text
    props = presentation.core_properties
    props.author = props.last_modified_by = "qa"
    props.created = props.modified = props.last_printed = FIXED_TIME
    presentation.save(str(path))
    _normalise_zip(path)


def write_xlsx(path: Path, sheets: Dict[str, List[List[str]]]) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    workbook.properties.creator = workbook.properties.lastModifiedBy = "qa"
    workbook.properties.created = workbook.properties.modified = FIXED_TIME
    workbook.save(str(path))
    _normalise_zip(path)


def write_pdf(path: Path, pages: List[str], **save_options) -> None:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((50, 72), text, fontname="china-t", fontsize=12)
    document.set_metadata({})
    document.save(str(path), **save_options)
    document.close()
    if "encryption" not in save_options:  # encryption salts are random; those files stay unstable
        _pin_pdf_id(path)


def write_image_only_pdf(path: Path, text: str) -> None:
    """A PDF whose only content is a rendered picture of text (no text layer)."""
    import pymupdf

    source = pymupdf.open()
    source.new_page().insert_text((50, 72), text, fontname="china-t", fontsize=24)
    pixmap = source[0].get_pixmap(dpi=100)
    source.close()
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(page.rect, pixmap=pixmap)
    document.set_metadata({})
    document.save(str(path))
    document.close()
    _pin_pdf_id(path)


# -- dataset groups ------------------------------------------------------------------------
def _zh(b: _Builder) -> None:
    b.text(
        "zh/trad_meeting.txt",
        "zh",
        "本次會議記錄：升等審查辦法已通過。\n升等名單於下週公布。\n"
        "升起國旗，等待通知。計算機與軟體課程。\n",
        ["會議記錄", "升等", "升等審查辦法"],
    )
    b.text(
        "zh/simp_meeting.txt",
        "zh",
        "本次会议记录：升等审查办法已通过。\n升等名单于下周公布。\n计算机与软件课程。\n",
        ["会议记录", "升等", "计算机", "软件"],
    )
    write_docx(b.target("zh/trad_report.docx"), ["會議記錄摘要", "升等 案件 三件"])
    b.record("zh/trad_report.docx", "zh", INDEXED, ["會議記錄", "升等"])
    write_docx(b.target("zh/simp_report.docx"), ["会议记录摘要", "升等 案件 三件"])
    b.record("zh/simp_report.docx", "zh", INDEXED, ["会议记录", "升等"])
    b.text(
        "zh/會議記錄範本.txt",
        "zh",
        "template body without the keyword\n",
        [],
        note="keyword only in the file name",
    )
    b.text(
        "zh/会议记录模板.txt",
        "zh",
        "template body without the keyword\n",
        [],
        note="keyword only in the file name",
    )
    b.text(
        "zh/unrelated.txt",
        "zh",
        "升 and 等 appear only inside other words: 上升 等於 提升 平等\n",
        [],
        note="single characters only; must not satisfy a search for 升等 (no 升等 token)",
    )


def _en(b: _Builder) -> None:
    b.text(
        "en/cases.txt",
        "en",
        "The outstanding result.\nOUTSTANDING effort.\nOutstanding work.\n"
        "Several outstandings remain; they outstand others.\n",
        ["outstanding", "OUTSTANDING", "Outstanding"],
    )
    b.text(
        "en/section.txt",
        "en",
        "Section 1 (Paragraphs)\nThis part follows Section 2 (Tables).\n",
        ["Section 1 (Paragraphs)"],
    )


def _mix(b: _Builder) -> None:
    b.text(
        "mix/1111223pi retreat.txt",
        "mix",
        "Agenda for the 1111223pi retreat.\nNothing else here.\n",
        ["1111223pi retreat"],
        note="marker is in both the file name and the body",
    )
    b.text(
        "mix/symbols.txt",
        "mix",
        "Date 2023-01-03. Grades A- and A+. snake_case name. Path a/b. Language C++. Rate 100%.\n"
        "升等 outstanding 會議記錄，「引號」。\n",
        ["2023-01-03", "A-", "A+", "snake_case", "a/b", "C++", "100%"],
    )


def _fmt(b: _Builder) -> None:
    write_pdf(
        b.target("fmt/marker.pdf"),
        ["PDF page one. Nothing to find here.", "PDF page two. 專案預算 QAMARKpdf"],
    )
    b.record("fmt/marker.pdf", "fmt", INDEXED, ["QAMARKpdf"], note="marker on page 2")
    write_docx(b.target("fmt/marker.docx"), ["Word body.", "QAMARKdocx 專案預算"])
    b.record("fmt/marker.docx", "fmt", INDEXED, ["QAMARKdocx"])
    write_pptx(b.target("fmt/marker.pptx"), ["Slide one", "QAMARKpptx 專案預算"])
    b.record("fmt/marker.pptx", "fmt", INDEXED, ["QAMARKpptx"], note="marker on slide 2")
    write_xlsx(
        b.target("fmt/marker.xlsx"),
        {"Data": [["item", "value"], ["a", "1"]], "Summary": [["QAMARKxlsx", "專案預算"]]},
    )
    b.record("fmt/marker.xlsx", "fmt", INDEXED, ["QAMARKxlsx"], note="marker on sheet 'Summary'")
    b.text("fmt/marker.txt", "fmt", "QAMARKtxt 專案預算\n", ["QAMARKtxt"])
    b.text("fmt/marker.md", "fmt", "# Title\n\nQAMARKmd 專案預算\n", ["QAMARKmd"])
    b.text("fmt/marker.csv", "fmt", "name,note\nrow,QAMARKcsv 專案預算\n", ["QAMARKcsv"])

    try:
        import xlwt
    except ImportError:
        b.skip("fmt/marker.xls", "fmt", "xlwt is not installed (pip install xlwt to enable)")
    else:
        book = xlwt.Workbook(encoding="utf-8")
        book.add_sheet("Data").write(0, 0, "QAMARKxls 專案預算")
        book.save(str(b.target("fmt/marker.xls")))
        b.record(
            "fmt/marker.xls",
            "fmt",
            INDEXED,
            ["QAMARKxls"],
            hash_stable=False,
            note="xlwt embeds no timestamps but its output is not guaranteed stable across versions",
        )
    for extension in ("doc", "ppt"):
        b.skip(
            f"fmt/marker.{extension}",
            "fmt",
            "no pure-Python writer; supply a real Office-authored file (test plan OQ-6)",
        )


def _enc(b: _Builder) -> None:
    traditional = "會議記錄 專案預算"
    simplified = "会议记录 项目预算"
    cases = [
        ("utf8_bom", "utf-8-sig", traditional),
        ("utf16", "utf-16", traditional),
        ("utf32", "utf-32", traditional),
        ("big5", "big5", traditional),
        ("gbk", "gbk", simplified),
    ]
    for name, encoding, body in cases:
        marker = "QAENC" + name.replace("_", "")
        note = "no BOM; may be misread as Big5 (known limitation)" if name == "gbk" else ""
        b.text(f"enc/{name}.txt", "enc", f"{marker} {body}\n", [marker], encoding, note=note)


def _tree(b: _Builder) -> None:
    b.text("tree/a.txt", "tree", "QATREEroot at the top level\n", ["QATREEroot"])
    b.text("tree/sub/b.txt", "tree", "QATREEsub one level down\n", ["QATREEsub"])
    b.text("tree/sub/deep/c.txt", "tree", "QATREEdeep two levels down\n", ["QATREEdeep"])


def _bad(b: _Builder) -> None:
    b.text("bad/good.txt", "bad", "QABADgood is still indexed\n", ["QABADgood"])

    good_docx = b.root / "bad" / "_source.docx"
    good_docx.parent.mkdir(parents=True, exist_ok=True)
    write_docx(good_docx, ["QABADtruncated docx body " * 20])
    b.target("bad/truncated.docx").write_bytes(good_docx.read_bytes()[:400])
    good_docx.unlink()
    b.record("bad/truncated.docx", "bad", SKIPPED_UNREADABLE)

    good_xlsx = b.root / "bad" / "_source.xlsx"
    write_xlsx(good_xlsx, {"Sheet": [["QABADtruncated xlsx"] * 5] * 20})
    b.target("bad/truncated.xlsx").write_bytes(good_xlsx.read_bytes()[:400])
    good_xlsx.unlink()
    b.record("bad/truncated.xlsx", "bad", SKIPPED_UNREADABLE)

    b.target("bad/empty.pdf").write_bytes(b"")
    b.record("bad/empty.pdf", "bad", SKIPPED_UNREADABLE, note="zero bytes")
    b.target("bad/garbage.pdf").write_bytes(b"not a pdf at all " * 50)
    b.record("bad/garbage.pdf", "bad", SKIPPED_UNREADABLE)

    import pymupdf

    write_pdf(
        b.target("bad/user_password.pdf"),
        ["QABADprotected"],
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="userpw",
        owner_pw="ownerpw",
    )
    b.record(
        "bad/user_password.pdf",
        "bad",
        SKIPPED_UNREADABLE,
        hash_stable=False,
        note="password 'userpw' required to open",
    )
    write_pdf(
        b.target("bad/owner_password.pdf"),
        ["QABADownerpdf text stays extractable"],
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="",
        owner_pw="ownerpw",
        permissions=int(pymupdf.PDF_PERM_ACCESSIBILITY),
    )
    b.record(
        "bad/owner_password.pdf",
        "bad",
        INDEXED,
        ["QABADownerpdf"],
        hash_stable=False,
        note="restricts copying/printing only; README says text is still extracted",
    )

    b.text("bad/~$temp.docx", "bad", "Office lock file", [], expect=SKIPPED_TEMP)

    if os.name != "nt":
        locked = b.text(
            "bad/locked.txt", "bad", "QABADlocked cannot be read\n", [], expect=SKIPPED_UNREADABLE
        )
        locked.chmod(0o000)
        b.entries[-1].note = "chmod 000; readable (and therefore indexed) when running as root"
        try:
            os.symlink("..", b.target("bad/loop/up"), target_is_directory=True)
            b.record("bad/loop/up", "bad", SKIPPED_UNREADABLE, note="symlink to a parent directory")
        except OSError as exc:
            b.skip("bad/loop/up", "bad", f"cannot create symlink: {exc}")
    else:
        b.skip("bad/locked.txt", "bad", "permission cases are POSIX only")
        b.skip("bad/loop/up", "bad", "symlink loop is POSIX only")


def _neg(b: _Builder) -> None:
    for name, content in (
        ("data.json", b'{"note": "QANEGjson"}'),
        ("run.log", b"2026-01-01 QANEGlog line\n"),
        ("pixel.png", b"\x89PNG\r\n\x1a\nQANEGpng"),
        ("tool.exe", b"MZ QANEGexe"),
    ):
        b.target(f"neg/{name}").write_bytes(content)
        b.record(f"neg/{name}", "neg", SKIPPED_UNSUPPORTED, [f"QANEG{name.split('.')[1]}"])


def _scan(b: _Builder) -> None:
    write_image_only_pdf(b.target("scan/scanned.pdf"), "QASCANkeyword 掃描")
    b.record(
        "scan/scanned.pdf",
        "scan",
        NO_TEXT,
        ["QASCANkeyword"],
        note="text exists only as pixels; no OCR, so the marker must NOT be found",
    )


def _path(b: _Builder) -> None:
    names = {
        "cjk": "會議記錄_2026.txt",
        "emoji": "party 🎉 notes.txt",
        "spaces": "file with  spaces.txt",
        "percent": "100% done.txt",
        "underscore": "a_b_c.txt",
        "brackets": "[draft] (v1).txt",
        "nfd": unicodedata.normalize("NFD", "café_résumé.txt"),
    }
    for key, name in names.items():
        note = "name stored in NFD form" if key == "nfd" else ""
        marker = f"QAPATH{key}"
        b.text(f"path/{name}", "path", f"{marker} body\n", [marker], note=note)


GROUPS = (_zh, _en, _mix, _fmt, _enc, _tree, _bad, _neg, _scan, _path)


def _prepare_target(target: Path) -> None:
    if target.exists():
        contents = list(target.iterdir())
        if contents and not (target / MANIFEST_NAME).is_file():
            raise FileExistsError(
                f"{target} is not empty and has no {MANIFEST_NAME}; refusing to write into it"
            )
        if contents:
            _remove_previous(target)
    target.mkdir(parents=True, exist_ok=True)


def _remove_previous(target: Path) -> None:
    for path in target.rglob("*"):
        try:
            path.chmod(0o700) if not path.is_symlink() else None
        except OSError:
            pass
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def build_dataset(target) -> Dict[str, object]:
    """Write the dataset under target and return the manifest as a dict."""
    root = Path(target)
    _prepare_target(root)
    builder = _Builder(root)
    for group in GROUPS:
        group(builder)

    for entry in builder.entries:
        path = root / entry.path
        if not path.is_symlink():
            os.utime(path, (FIXED_EPOCH, FIXED_EPOCH))
    manifest = {
        "generator": "tests/search_plan/dataset.py",
        "files": [asdict(entry) for entry in sorted(builder.entries, key=lambda e: e.path)],
        "unavailable": [asdict(item) for item in builder.unavailable],
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def load_manifest(root) -> Dict[str, object]:
    return json.loads((Path(root) / MANIFEST_NAME).read_text(encoding="utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the docs/test-plan.md test dataset.")
    parser.add_argument("outdir", help="new or empty directory (or a previous dataset to rebuild)")
    args = parser.parse_args(argv)
    manifest = build_dataset(args.outdir)
    print(f"wrote {len(manifest['files'])} files to {args.outdir}")
    for item in manifest["unavailable"]:
        print(f"unavailable: {item['path']}: {item['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
