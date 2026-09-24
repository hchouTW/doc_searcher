"""Native path compatibility (Task 6.1).

Shared tests run everywhere; tests marked macos/windows need native filesystem semantics and run
only on those CI runners (the marker's skip reason says so elsewhere).
"""

import os
import unicodedata
from pathlib import Path

import pytest

from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.platform import platform_helper
from doc_searcher.platform.os_detector import detect_os
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database

# Characters that are legal in file names on every supported OS but special somewhere:
# SQL LIKE wildcards (% _), glob wildcards ([ ]), and non-ASCII scripts.
SPECIAL_NAMES = [
    "報告 資料夾",  # CJK + space
    "emoji 📁 folder",
    "100% done",
    "snake_case_dir",
    "[draft] notes",
    "a%b_c",
]


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "index.db"))
    yield database
    database.close()


def build_tree(root: Path):
    files = {}
    for name in SPECIAL_NAMES:
        folder = root / name
        folder.mkdir(parents=True)
        path = folder / f"{name} 文件.txt"
        path.write_text(f"shared keyword {name}", encoding="utf-8")
        files[name] = path
    return files


def index(db, root):
    return IndexingService(db).run(IndexRequest(roots=[str(root)]))


def test_special_character_paths_are_indexed_and_found(tmp_path, db):
    files = build_tree(tmp_path / "root")
    stats = index(db, tmp_path / "root")
    assert stats["indexed"] == len(SPECIAL_NAMES) and stats["failed"] == 0
    found = {r.path for r in DocumentSearcher(db).search("keyword")}
    assert found == {str(p) for p in files.values()}


@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_include_path_filter_matches_only_that_folder(tmp_path, db, name):
    files = build_tree(tmp_path / "root")
    index(db, tmp_path / "root")
    results = DocumentSearcher(db).search("keyword", include_paths=[str(files[name].parent)])
    assert [r.path for r in results] == [str(files[name])]


def test_like_wildcards_in_include_path_are_literal(tmp_path, db):
    root = tmp_path / "root"
    (root / "a%b").mkdir(parents=True)
    (root / "axxb").mkdir()
    (root / "a_c").mkdir()
    (root / "abc").mkdir()
    for folder in ("a%b", "axxb", "a_c", "abc"):
        (root / folder / "f.txt").write_text("keyword", encoding="utf-8")
    index(db, root)
    engine = DocumentSearcher(db)
    assert [
        Path(r.path).parent.name
        for r in engine.search("keyword", include_paths=[str(root / "a%b")])
    ] == ["a%b"]
    assert [
        Path(r.path).parent.name
        for r in engine.search("keyword", include_paths=[str(root / "a_c")])
    ] == ["a_c"]


@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_exclusion_of_special_folder(tmp_path, db, name):
    files = build_tree(tmp_path / "root")
    index(db, tmp_path / "root")
    results = DocumentSearcher(db).search(
        "keyword", exclude_patterns=[name], search_roots=[str(tmp_path / "root")]
    )
    assert str(files[name]) not in {r.path for r in results}
    assert len(results) == len(SPECIAL_NAMES) - 1
    assert FileScanner.matches_exclusion(str(files[name]), [name], str(tmp_path / "root"))


def test_filename_search_treats_wildcards_literally(tmp_path, db):
    files = build_tree(tmp_path / "root")
    index(db, tmp_path / "root")
    engine = DocumentSearcher(db)
    assert [r.path for r in engine.search("filename:100%")] == [str(files["100% done"])]
    assert [r.path for r in engine.search("filename:a%b_c")] == [str(files["a%b_c"])]
    assert {r.path for r in engine.search("filename:_")} == {
        str(files["snake_case_dir"]),
        str(files["a%b_c"]),
    }


def test_like_fallback_treats_wildcards_literally(tmp_path, db):
    for name, text in (("one.txt", "rate 100% sure"), ("two.txt", "rate 1000 units")):
        (tmp_path / name).write_text(text, encoding="utf-8")
    index(db, tmp_path)
    rows = DocumentSearcher(db)._fallback_like_search(["100%"], "", [], 10)
    assert [Path(row["path"]).name for row in rows] == ["one.txt"]


@pytest.mark.posix
def test_backslash_in_posix_names(tmp_path, db):
    folder = tmp_path / "back\\slash"
    folder.mkdir()
    (folder / "f.txt").write_text("keyword", encoding="utf-8")
    (tmp_path / "other.txt").write_text("keyword", encoding="utf-8")
    index(db, tmp_path)
    results = DocumentSearcher(db).search("keyword", include_paths=[str(folder)])
    assert [r.path for r in results] == [str(folder / "f.txt")]


@pytest.mark.parametrize("family", ["macos", "windows", "linux"])
def test_open_and_reveal_pass_special_paths_unchanged(tmp_path, monkeypatch, family):
    files = build_tree(tmp_path / "root")
    info = {
        "macos": detect_os(
            system_name="Darwin", release="24.0.0", machine="arm64", mac_version="15.0"
        ),
        "windows": detect_os(
            system_name="Windows", release="11", machine="AMD64", windows_build=22631
        ),
        "linux": detect_os(system_name="Linux", release="6.8", machine="x86_64"),
    }[family]
    calls = []
    monkeypatch.setattr(platform_helper.subprocess, "run", lambda args, check: calls.append(args))
    monkeypatch.setattr(
        platform_helper.os, "startfile", lambda path: calls.append([path]), raising=False
    )
    for path in files.values():
        assert platform_helper.open_file_with_default_app(str(path), info)
        assert platform_helper.reveal_in_file_manager(str(path), info)
    flattened = " ".join(" ".join(call) for call in calls)
    for path in files.values():
        assert str(path) in flattened or str(path.parent) in flattened


# ------------------------------------------------------------------- macOS
@pytest.mark.macos
def test_nfc_and_nfd_spellings_select_the_same_folder(tmp_path, db):
    """APFS keeps the spelling a name was created with but finds it by either form."""
    nfd_name = unicodedata.normalize("NFD", "café résumé")
    folder = tmp_path / nfd_name
    folder.mkdir()
    (folder / "doc.txt").write_text("keyword", encoding="utf-8")
    index(db, tmp_path)
    nfc_folder = str(tmp_path / unicodedata.normalize("NFC", "café résumé"))
    assert os.path.isdir(nfc_folder)
    engine = DocumentSearcher(db)
    assert len(engine.search("keyword", include_paths=[nfc_folder])) == 1
    assert (
        engine.search("keyword", exclude_patterns=["café résumé"], search_roots=[str(tmp_path)])
        == []
    )
    assert len(engine.search("filename:doc", include_paths=[nfc_folder])) == 1


@pytest.mark.macos
def test_roots_differing_only_in_normalization_are_one_folder(tmp_path, db):
    nfd = tmp_path / unicodedata.normalize("NFD", "é")
    nfd.mkdir()
    (nfd / "a.txt").write_text("keyword", encoding="utf-8")
    nfc = tmp_path / unicodedata.normalize("NFC", "é")
    IndexingService(db).run(IndexRequest(roots=[str(nfd), str(nfc)]))
    assert len(db.get_all_indexed_paths()) == 1

    # A later rescan through the other spelling changes nothing.
    stats = IndexingService(db).run(IndexRequest(roots=[str(nfc)]))
    assert (stats["indexed"], stats["deleted"]) == (0, 0)
    assert len(db.get_all_indexed_paths()) == 1


@pytest.mark.macos
def test_legacy_nfd_entry_is_replaced_once(tmp_path, db):
    nfd = tmp_path / unicodedata.normalize("NFD", "é")
    nfd.mkdir()
    (nfd / "a.txt").write_text("keyword", encoding="utf-8")
    legacy_path = str(nfd / "a.txt")
    conn = db.get_connection()
    with conn:  # as written by v1.2.0: the path exactly as os.walk returned it
        conn.execute(
            "INSERT INTO documents (path, filename, file_type, file_size, mtime, ctime, indexed_at)"
            " VALUES (?, 'a.txt', 'txt', 7, 0, 0, 0)",
            (legacy_path,),
        )
    first = IndexingService(db).run(IndexRequest(roots=[str(tmp_path)]))
    second = IndexingService(db).run(IndexRequest(roots=[str(tmp_path)]))
    assert list(db.get_all_indexed_paths()) == [unicodedata.normalize("NFC", legacy_path)]
    assert (second["indexed"], second["deleted"]) == (0, 0)
    assert first["indexed"] == 1


# ----------------------------------------------------------------- Windows
@pytest.mark.windows
def test_mixed_case_and_separator_variants_select_the_same_folder(tmp_path, db):
    folder = tmp_path / "Reports"
    folder.mkdir()
    (folder / "q1.txt").write_text("keyword", encoding="utf-8")
    index(db, tmp_path)
    engine = DocumentSearcher(db)
    for variant in (str(folder).lower(), str(folder).upper(), str(folder).replace("\\", "/")):
        assert len(engine.search("keyword", include_paths=[variant])) == 1, variant


@pytest.mark.windows
def test_unc_paths_are_contained_correctly():
    assert FileScanner.is_path_within_directory(r"\\server\share\a\b.txt", r"\\server\share\a")
    assert not FileScanner.is_path_within_directory(r"\\server\share\ab\b.txt", r"\\server\share\a")
    assert not FileScanner.is_path_within_directory(r"\\other\share\a\b.txt", r"\\server\share\a")
    assert not FileScanner.is_path_within_directory(r"D:\a\b.txt", r"C:\a")


def _long_paths_enabled() -> bool:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        )
        return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


@pytest.mark.windows
def test_paths_longer_than_max_path(tmp_path, db):
    if not _long_paths_enabled():
        pytest.skip(
            "LongPathsEnabled is off on this Windows machine; >260-char paths cannot be created"
        )
    folder = tmp_path
    while len(str(folder)) < 300:
        folder = folder / ("long_segment_" + "x" * 20)
    folder.mkdir(parents=True)
    (folder / "deep.txt").write_text("keyword", encoding="utf-8")
    stats = index(db, tmp_path)
    assert stats["indexed"] == 1 and stats["failed"] == 0
    assert len(DocumentSearcher(db).search("keyword", include_paths=[str(folder)])) == 1


@pytest.mark.macos
def test_stale_legacy_row_is_removed_without_touching_the_canonical_row(tmp_path, db):
    nfd = tmp_path / unicodedata.normalize("NFD", "é")
    nfd.mkdir()
    (nfd / "a.txt").write_text("keyword", encoding="utf-8")
    IndexingService(db).run(IndexRequest(roots=[str(tmp_path)]))  # canonical (NFC) row
    legacy = str(nfd / "a.txt")
    conn = db.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO documents (path, filename, file_type, file_size, mtime, ctime, indexed_at)"
            " VALUES (?, 'a.txt', 'txt', 7, 0, 0, 0)",
            (legacy,),
        )
    IndexingService(db).run(IndexRequest(roots=[str(tmp_path)]))
    assert list(db.get_all_indexed_paths()) == [unicodedata.normalize("NFC", legacy)]
    assert len(DocumentSearcher(db).search("keyword")) == 1  # canonical row still searchable
