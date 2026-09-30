"""The test-plan dataset generator itself: complete, deterministic, and consumable."""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.parsers import ParseStatus, is_supported, parse_file
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database
from search_plan import dataset

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("td") / "dataset"
    manifest = dataset.build_dataset(root)
    return root, manifest


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(manifest, **match):
    return [e for e in manifest["files"] if all(e[k] == v for k, v in match.items())]


def parsed_text(path: Path) -> str:
    result = parse_file(str(path))
    assert result.status == ParseStatus.SUCCESS, (path, result.status, result.error)
    return "\n".join(segment.text for segment in result.segments)


def test_manifest_lists_every_file_and_every_file_is_listed(built):
    root, manifest = built
    listed = {e["path"] for e in manifest["files"]}
    on_disk = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if (p.is_file() or p.is_symlink()) and p.name != dataset.MANIFEST_NAME
    }
    assert listed == on_disk
    assert {e["group"] for e in manifest["files"]} == {
        "zh",
        "en",
        "mix",
        "fmt",
        "enc",
        "tree",
        "bad",
        "neg",
        "scan",
        "path",
    }


def test_unavailable_formats_are_reported_with_a_reason(built):
    _, manifest = built
    unavailable = {u["path"]: u["reason"] for u in manifest["unavailable"]}
    assert {"fmt/marker.doc", "fmt/marker.ppt"} <= set(unavailable)
    assert all(reason for reason in unavailable.values())
    listed = {e["path"] for e in manifest["files"]}
    assert not listed & set(unavailable)


def test_markers_are_unique_and_plain_alphanumeric(built):
    _, manifest = built
    markers = [m for e in manifest["files"] for m in e["markers"] if m.startswith("QA")]
    assert len(markers) == len(set(markers))
    assert all(m.isalnum() for m in markers)


@pytest.mark.skipif(sys.platform == "win32", reason="mtime check uses POSIX stat semantics")
def test_build_is_deterministic(built, tmp_path):
    root, manifest = built
    second = tmp_path / "again"
    assert dataset.build_dataset(second) == manifest
    for entry in entries(manifest):
        path = root / entry["path"]
        other = second / entry["path"]
        if path.is_symlink() or not os.access(path, os.R_OK):
            continue
        assert path.stat().st_mtime == dataset.FIXED_EPOCH
        if entry["hash_stable"]:
            assert digest(path) == digest(other), entry["path"]


def test_only_encrypted_pdfs_and_optional_xls_are_hash_unstable(built):
    _, manifest = built
    unstable = {e["path"] for e in entries(manifest, hash_stable=False)}
    assert unstable <= {"bad/user_password.pdf", "bad/owner_password.pdf", "fmt/marker.xls"}


def test_refuses_to_write_into_a_foreign_directory(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        dataset.build_dataset(tmp_path)
    assert (tmp_path / "precious.txt").read_text() == "keep me"


def test_rebuilding_over_a_previous_dataset_replaces_it(tmp_path):
    root = tmp_path / "td"
    dataset.build_dataset(root)
    (root / "stray.txt").write_text("leftover")
    dataset.build_dataset(root)  # includes a chmod 000 file and a symlink loop from the first run
    assert not (root / "stray.txt").exists()
    assert (root / dataset.MANIFEST_NAME).is_file()


def test_indexed_files_contain_their_markers(built):
    root, manifest = built
    checked = 0
    for entry in manifest["files"]:
        if entry["expect"] != dataset.INDEXED or not entry["markers"]:
            continue
        text = parsed_text(root / entry["path"])
        for marker in entry["markers"]:
            assert marker in text, (entry["path"], marker)
        checked += 1
    assert checked >= 20


def test_chinese_variants_are_present_in_both_scripts(built):
    root, _ = built
    assert "會議記錄" in parsed_text(root / "zh/trad_report.docx")
    assert "会议记录" in parsed_text(root / "zh/simp_report.docx")
    assert "升等" not in parsed_text(root / "zh/unrelated.txt")


def test_unsupported_scanned_and_broken_files_behave_as_recorded(built):
    root, manifest = built
    for entry in entries(manifest, expect=dataset.SKIPPED_UNSUPPORTED):
        assert not is_supported(entry["path"])
    scanned = parse_file(str(root / "scan/scanned.pdf"))
    assert scanned.status == ParseStatus.EMPTY
    for entry in entries(manifest, expect=dataset.SKIPPED_UNREADABLE):
        path = root / entry["path"]
        if not path.is_file() or (entry["path"] == "bad/locked.txt" and RUNNING_AS_ROOT):
            continue
        assert parse_file(str(path)).status != ParseStatus.SUCCESS, entry["path"]
    assert not FileScanner().is_valid_document_file("~$temp.docx")


def test_dataset_can_be_indexed_end_to_end(built, tmp_path):
    root, manifest = built
    db = Database(str(tmp_path / "index.db"))
    files = FileScanner().scan_directories([str(root)])
    stats = DocumentIndexer(db).run_batch_indexing([f[0] for f in files], [])
    assert stats["failed"] >= 5  # truncated x2, empty/garbage/protected pdf (locked when not root)
    searcher = DocumentSearcher(db)

    def hits(query):
        return {Path(r.path).relative_to(root).as_posix() for r in searcher.search(query)}

    for extension in ("pdf", "docx", "pptx", "xlsx", "txt", "md", "csv"):
        assert hits(f"QAMARK{extension}") == {f"fmt/marker.{extension}"}
    assert hits("QAMARKxls") <= {"fmt/marker.xls"}
    assert hits("QATREEdeep") == {"tree/sub/deep/c.txt"}
    assert hits("QABADgood") == {"bad/good.txt"}
    assert hits("QABADownerpdf") == {"bad/owner_password.pdf"}
    assert hits("QAENCutf8bom") == {"enc/utf8_bom.txt"}
    assert hits("QASCANkeyword") == set()  # no OCR
    assert hits("QANEGjson") == set()  # unsupported extension
