"""Filter and preview-panel cases (FLT-*, PRV-*) from docs/test-plan.md.

The preview runs on Qt's offscreen platform; real results come from the indexed dataset.
Cases that need a person (opening a file in the OS, Finder/Explorer, theme legibility) are in
docs/test-execution-log.md as manual checks.
"""

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from doc_searcher.desktop.preview_panel import PreviewPanel
from doc_searcher.platform import platform_helper
from doc_searcher.platform.platform_helper import format_file_size, format_timestamp
from search_plan import dataset


def _app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def preview():
    _app()
    return PreviewPanel()


# ---- FLT ----------------------------------------------------------------------------------------
GROUPS = {
    "pdf": {"pdf"},
    "word": {"docx", "doc"},
    "excel": {"xlsx", "xls"},
    "ppt": {"pptx", "ppt"},
    "text": {"txt", "md", "csv"},
}


def test_flt_01_word_filter_shows_only_word_documents(env):
    everything = env.results("專案預算")
    word = env.results("專案預算", type_filter="word")
    assert set(word) == {"fmt/marker.docx"}
    assert set(word) < set(everything)


@pytest.mark.parametrize("group", GROUPS)
def test_flt_02_every_group_returns_only_its_extensions(env, group):
    hits = env.results("專案預算", type_filter=group)
    assert hits, group
    assert {item.file_type for item in hits.values()} <= GROUPS[group]


def test_flt_02_groups_partition_the_unfiltered_results(env):
    total = set(env.results("專案預算"))
    combined = set()
    for group in GROUPS:
        combined |= set(env.results("專案預算", type_filter=group))
    assert combined == total


def test_flt_03_date_bounds_include_after_and_exclude_before(env):
    stamp = dataset.FIXED_EPOCH
    everything = env.search("QATREEroot")
    assert env.search("QATREEroot", modified_after=stamp) == everything  # inclusive
    assert env.search("QATREEroot", modified_before=stamp) == set()  # exclusive
    assert env.search("QATREEroot", modified_before=stamp + 1) == everything
    assert env.search("QATREEroot", modified_after=stamp + 1) == set()


def test_flt_03_size_bounds_are_inclusive(env):
    size = (env.root / "tree/a.txt").stat().st_size
    assert env.search("QATREEroot", min_size=size, max_size=size) == {"tree/a.txt"}
    assert env.search("QATREEroot", min_size=size + 1) == set()
    assert env.search("QATREEroot", max_size=size - 1) == set()


def test_flt_03_include_path_limits_results_to_a_subfolder(env):
    hits = env.search(
        "QATREEroot OR QATREEsub OR QATREEdeep", include_paths=[str(env.root / "tree/sub")]
    )
    assert hits == {"tree/sub/b.txt", "tree/sub/deep/c.txt"}


# ---- PRV ----------------------------------------------------------------------------------------
def test_prv_01_metadata_matches_the_file(env, preview):
    item = env.results("QAMARKtxt")["fmt/marker.txt"]
    preview.display_result(item)
    stat = os.stat(env.root / "fmt/marker.txt")
    assert preview.title_label.text() == "marker.txt"
    assert preview.path_label.text() == str(env.root / "fmt/marker.txt")
    assert format_file_size(stat.st_size) in preview.meta_label.text()
    assert "TXT" in preview.meta_label.text()
    assert format_timestamp(stat.st_mtime) in preview.meta_label.text()
    assert item.file_size == stat.st_size
    assert item.mtime == stat.st_mtime


def test_prv_02_zoom_is_clamped_and_reversible(env, preview):
    preview.display_result(env.results("QAMARKtxt")["fmt/marker.txt"])
    for _ in range(20):
        preview._zoom_in()
    top = preview.browser.property("preview_font_px")
    assert top == 13 + 6 * 2 and not preview.btn_zoom_in.isEnabled()
    for _ in range(40):
        preview._zoom_out()
    bottom = preview.browser.property("preview_font_px")
    assert bottom == 13 - 3 * 2 and not preview.btn_zoom_out.isEnabled()
    for _ in range(3):
        preview._zoom_in()
    assert preview.browser.property("preview_font_px") == 13 and preview.btn_zoom_in.isEnabled()


def test_prv_03_match_navigation_wraps_and_counts(scratch, tmp_path, preview):
    # Distant hits give separate snippets; a hit is one snippet, as in the results list.
    filler = "filler words go here. " * 30
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "far.txt").write_text(
        f"outstanding {filler} outstanding {filler} outstanding", encoding="utf-8"
    )
    scratch.run([tmp_path / "docs"])
    item = scratch.searcher.search("outstanding")[0]
    preview.display_result(item)
    total = preview.match_count
    assert total == item.total_matches  # same count the results list shows
    assert total >= 2 and preview.match_counter_label.text() == f"1 / {total}"
    preview._previous_match()  # from the first match, wraps to the last
    assert preview.match_counter_label.text() == f"{total} / {total}"
    preview._next_match()  # and forward again to the first
    assert preview.match_counter_label.text() == f"1 / {total}"
    for _ in range(total - 1):
        preview._next_match()
    assert preview.match_counter_label.text() == f"{total} / {total}"


@pytest.mark.parametrize(
    "query,path,expected",
    [
        ("升等", "zh/trad_meeting.txt", "升等"),
        ('"升等"', "zh/simp_report.docx", "升等"),
        ("会议记录", "zh/trad_meeting.txt", "會議記錄"),
        ("會議記錄", "zh/simp_meeting.txt", "会议记录"),
        ("Section 1 (Paragraphs)", "en/section.txt", "(Paragraphs)"),
        ("C++", "mix/symbols.txt", "C++"),
        ("a/b", "mix/symbols.txt", "a/b"),
        ("100%", "mix/symbols.txt", "100%"),
    ],
)
def test_prv_04_highlight_covers_exactly_the_text_in_the_document(env, query, path, expected):
    item = env.results(query)[path]
    marked = re.findall(r">([^<]+)</mark>", " ".join(s for g in item.segments for s in g.snippets))
    assert expected in marked
    stored = " ".join(
        row[0]
        for row in env.db.get_connection().execute(
            "SELECT s.content FROM doc_segments s JOIN documents d ON d.id = s.doc_id "
            "WHERE d.path = ?",
            (str(env.root / path),),
        )
    )
    assert all(m in stored for m in marked)  # every highlight is text that is really there


def test_prv_04_highlight_stays_aligned_after_emoji_and_english(scratch, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "e.txt").write_text(
        "😀😀 hello 會議記錄 world 😀 会议记录", encoding="utf-8"
    )
    scratch.run([tmp_path / "docs"])
    (item,) = scratch.searcher.search("会议记录")
    marked = re.findall(r">([^<]+)</mark>", item.segments[0].snippets[0])
    assert marked == ["會議記錄", "会议记录"]


def test_prv_05_opening_a_missing_file_reports_failure_instead_of_crashing(tmp_path):
    assert platform_helper.open_file_with_default_app(str(tmp_path / "missing.txt")) is False
    assert platform_helper.reveal_in_file_manager(str(tmp_path / "missing.txt")) is False


def test_prv_05_open_button_passes_the_result_path_to_the_os(env, preview, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "doc_searcher.desktop.preview_panel.open_file_with_default_app", lambda p: opened.append(p)
    )
    preview.display_result(env.results("QAMARKtxt")["fmt/marker.txt"])
    preview._on_open_file()
    assert opened == [str(env.root / "fmt/marker.txt")]


@pytest.mark.parametrize(
    "query,path",
    [
        ("QAPATHcjk", "path/會議記錄_2026.txt"),
        ("QAPATHspaces", "path/file with  spaces.txt"),
        ("QAPATHemoji", "path/party 🎉 notes.txt"),
    ],
)
def test_prv_06_copy_path_puts_the_exact_absolute_path_on_the_clipboard(env, preview, query, path):
    preview.display_result(env.results(query)[path])
    preview._on_copy_path()
    assert QApplication.clipboard().text() == str(env.root / path)
