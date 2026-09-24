# Purpose: Regression tests for localization, result sorting, and preview navigation.
# What the code does:
#   - Confirms locale persistence and immediate UI translation.
#   - Confirms sorted rows retain their SearchResultItem association.
#   - Confirms the active preview match is distinct and CSS text zoom changes rendering.
#   - Confirms combo-box popups expand beyond narrow controls for long translations.
# Usage notes, dependencies, or assumptions:
#   - Uses Qt's offscreen platform so no desktop session is required.

import os
import time
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QTextBrowser,
)

import pytest

from doc_searcher.config import AppConfig
from doc_searcher.desktop.i18n import tr
from doc_searcher.search.searcher import SearchResultItem, SegmentMatch
from doc_searcher.desktop.main_window import MainWindow, ContentWidthComboBox
from doc_searcher.desktop.preview_panel import PreviewPanel
from doc_searcher.desktop.result_table import ResultTable
from doc_searcher.desktop.search_input import SyntaxSearchInput
from doc_searcher.desktop.theme import DARK_PALETTE, LIGHT_PALETTE
from doc_searcher.version import APP_VERSION


def _app():
    return QApplication.instance() or QApplication([])


def _result(name: str, matches: int) -> SearchResultItem:
    return SearchResultItem(
        doc_id=matches,
        path=f"/tmp/{name}",
        filename=name,
        file_type="txt",
        file_size=matches * 100,
        mtime=float(matches),
        rank_score=float(matches),
        total_matches=matches,
        segments=[
            SegmentMatch(
                segment_id="1",
                segment_type="text",
                snippets=[
                    'before <mark style="background-color: #ffeb3b">keyword</mark> after '
                    '<mark style="background-color: #ffeb3b">keyword</mark>'
                ],
            )
        ],
    )


def test_language_preference_persists_and_updates_window(tmp_path):
    app = _app()
    config_path = tmp_path / "config.json"
    config = AppConfig(config_path)
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)

    window.btn_lang_en.click()
    app.processEvents()

    assert window.windowTitle() == tr("en-US", "app_title")
    assert window.btn_search.text() == "Search"
    assert AppConfig(config_path).language == "en-US"
    window.close()


def test_sidebar_layout_and_compact_index_progress(tmp_path):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    window.show()
    app.processEvents()

    assert window.splitter.count() == 3
    assert isinstance(window.splitter.widget(0), QScrollArea)
    assert window.sidebar_scroll.horizontalScrollBar().maximum() == 0
    assert window.splitter.widget(1).layout().itemAt(0).widget() is window.table
    assert window.splitter.widget(2) is window.preview
    assert window.progress_bar.parent() is window.index_frame
    assert window.search_input.width() <= window.search_card.width()
    window.resize(880, 560)
    window.splitter.setSizes([280, 300, 300])
    window.date_filter_combo.setCurrentIndex(window.date_filter_combo.findData("day"))
    window.size_preset_combo.setCurrentIndex(window.size_preset_combo.findData("medium"))
    window.match_case.setChecked(True)
    app.processEvents()
    assert window.sidebar_scroll.width() == 280
    assert window.sidebar_scroll.horizontalScrollBar().maximum() == 0
    window._set_index_state("scanning")
    assert window.progress_text_label.text() == tr(window.language, "progress_scanning")
    window._on_indexing_progress(172, 265, "/some/very/long/path/report.pdf")
    assert window.progress_text_label.text() == "172 / 265 · 65%"
    assert "/some/very/long/path" not in window.status_label.text()
    window._set_index_state("paused")
    window._on_indexing_progress(173, 265, "/another/long/path.pdf")
    assert window.index_state_label.text() == tr(window.language, "state_paused")
    assert window.status_label.text() == tr(window.language, "state_paused")
    window.close()


def test_search_help_shows_python_regex_examples(tmp_path, monkeypatch):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    shown = []

    def capture_help(dialog):
        browser = dialog.findChild(QTextBrowser)
        shown.append(browser.toPlainText())
        available = dialog.screen().availableGeometry()
        assert dialog.width() <= min(
            int(available.width() * 0.9), max(320, int(window.width() * 0.9))
        )
        assert dialog.height() <= int(available.height() * 0.8)
        assert browser.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        assert browser.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        assert browser.lineWrapMode() == QTextBrowser.WidgetWidth

    monkeypatch.setattr(QDialog, "exec", capture_help)

    window._show_search_help()
    assert "Python Regex 語法" in shown[-1]
    for expression in (r"(?i)error", r"\d{4}-\d{2}-\d{2}", r"\btest\b", r"\b\w+\.(pdf|docx|txt)\b"):
        assert expression in shown[-1]
    window.btn_lang_en.click()
    window._show_search_help()
    assert "Python regular expression examples" in shown[-1]
    window.close()


def test_search_help_sizing_adapts_to_window_and_content(tmp_path, monkeypatch):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    window.show()
    app.processEvents()
    sizes = []

    def capture_help(dialog):
        dialog.show()
        app.processEvents()
        browser = dialog.findChild(QTextBrowser)
        buttons = dialog.findChild(QDialogButtonBox)
        sizes.append((dialog.width(), dialog.height(), browser.verticalScrollBar().maximum()))
        assert buttons.isVisible()
        assert buttons.geometry().bottom() <= dialog.contentsRect().bottom()
        assert browser.horizontalScrollBar().maximum() == 0
        dialog.close()

    monkeypatch.setattr(QDialog, "exec", capture_help)
    window.resize(1000, 700)
    app.processEvents()
    window._show_search_help()
    wide = sizes[-1]

    monkeypatch.setattr(window, "width", lambda: 420)
    monkeypatch.setattr(window, "height", lambda: 320)
    app.processEvents()
    window._show_search_help()
    narrow = sizes[-1]
    assert narrow[0] < wide[0]
    assert narrow[2] > 0

    original_set_html = QTextBrowser.setHtml
    monkeypatch.setattr(
        QTextBrowser,
        "setHtml",
        lambda browser, _html: original_set_html(browser, "<h3>Help</h3><p>Short text.</p>"),
    )
    monkeypatch.setattr(window, "width", lambda: 1000)
    monkeypatch.setattr(window, "height", lambda: 700)
    window.resize(1000, 700)
    app.processEvents()
    window._show_search_help()
    short = sizes[-1]
    assert short[0] < wide[0]
    assert short[1] < wide[1]
    window.close()


@pytest.mark.parametrize("action", ["_on_choose_directory", "_on_reset_directories"])
def test_folder_picker_selection_and_cancel(tmp_path, monkeypatch, action):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    old_folder = tmp_path / "old"
    old_folder.mkdir()
    config.directories = [str(old_folder)]
    window._refresh_dir_label()
    window.show()
    app.processEvents()
    started = []
    opened = []
    restored = []
    monkeypatch.setattr(window, "_start_indexing", lambda: started.append(True))
    monkeypatch.setattr(QFileDialog, "open", lambda dialog: opened.append(dialog))
    monkeypatch.setattr(window, "_restore_focus_after_folder_dialog", lambda: restored.append(True))

    getattr(window, action)()
    dialog = opened[-1]
    assert dialog.parent() is window
    assert dialog.windowModality() == Qt.WindowModal
    assert dialog.fileMode() == QFileDialog.Directory
    assert dialog.testOption(QFileDialog.ShowDirsOnly)
    getattr(window, action)()
    assert len(opened) == 1
    dialog.done(QDialog.Rejected)
    app.processEvents()
    assert window._folder_dialog is None
    assert config.directories == [str(old_folder)]
    assert started == []
    assert restored == [True]

    folder = tmp_path / "documents"
    folder.mkdir()
    getattr(window, action)()
    dialog = opened[-1]
    dialog.selectFile(str(folder))
    dialog.done(QDialog.Accepted)
    app.processEvents()
    assert window._folder_dialog is None
    expected = [str(old_folder), str(folder)] if action == "_on_choose_directory" else [str(folder)]
    assert config.directories == expected
    assert folder.name in window.dir_label.text()
    assert started == [True]
    assert restored == [True, True]
    window.close()


def test_folder_picker_batches_multiple_paths_and_skips_unavailable(tmp_path, monkeypatch):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    window.show()
    app.processEvents()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    opened = []
    started = []
    warnings = []
    monkeypatch.setattr(QFileDialog, "open", lambda dialog: opened.append(dialog))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(window, "_start_indexing", lambda: started.append(True))

    window._on_choose_directory()
    dialog = opened[-1]
    monkeypatch.setattr(
        dialog, "selectedFiles", lambda: [str(first), str(second), str(tmp_path / "gone")]
    )
    dialog.done(QDialog.Accepted)
    app.processEvents()

    assert config.directories == [str(first), str(second)]
    assert "2" in window.dir_label.text()
    assert started == [True]
    assert len(warnings) == 1
    assert AppConfig(config.config_path).directories == config.directories

    window._on_choose_directory()
    dialog = opened[-1]
    monkeypatch.setattr(dialog, "selectedFiles", lambda: [str(tmp_path / "gone")])
    dialog.done(QDialog.Accepted)
    app.processEvents()
    assert config.directories == [str(first), str(second)]
    assert started == [True]
    window.close()


def test_folder_picker_rejects_unreadable_selection_without_changing_state(tmp_path, monkeypatch):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    opened = []
    warnings = []
    monkeypatch.setattr(QFileDialog, "open", lambda dialog: opened.append(dialog))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    real_scandir = os.scandir

    def scandir(path):
        if str(path) == str(restricted):
            raise PermissionError("read denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    window._on_choose_directory()
    dialog = opened[-1]
    monkeypatch.setattr(dialog, "selectedFiles", lambda: [str(restricted)])
    dialog.done(QDialog.Accepted)
    app.processEvents()

    assert config.directories == []
    assert window.dir_label.text() == tr(window.language, "directory_empty")
    assert len(warnings) == 1
    window.close()


def test_clear_directories_updates_count_and_requests_index_clear(tmp_path, monkeypatch):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    folder = tmp_path / "docs"
    folder.mkdir()
    config.directories = [str(folder)]
    window = MainWindow(config)
    requested = []
    monkeypatch.setattr(
        window, "_start_indexing", lambda clear_index=False: requested.append(clear_index)
    )

    window._clear_directories()
    assert config.directories == []
    assert window.dir_label.text() == tr(window.language, "directory_empty")
    assert AppConfig(config.config_path).directories == []
    assert requested == [True]
    window._clear_directories()
    assert requested == [True]
    window.close()


def test_directory_controls_sync_ui_config_and_index(tmp_path):
    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    first = tmp_path / "first"
    nested = first / "nested"
    second = tmp_path / "second"
    nested.mkdir(parents=True)
    second.mkdir()
    top_file = first / "top.txt"
    deep_file = nested / "deep.txt"
    replacement = second / "replacement.txt"
    for path in (top_file, deep_file, replacement):
        path.write_text("searchable", encoding="utf-8")
    window = MainWindow(config)

    def wait_for_index():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if window.index_worker and not window.index_worker.isRunning():
                app.processEvents()
                return
            time.sleep(0.01)
        pytest.fail("index worker did not finish")

    window._add_directories([str(first)])
    wait_for_index()
    assert set(window.db.get_all_indexed_paths()) == {str(top_file), str(deep_file)}
    assert "2" in window.index_summary_label.text()

    window.chk_include_subdirectories.setChecked(False)
    wait_for_index()
    assert config.include_subdirectories is False
    assert set(window.db.get_all_indexed_paths()) == {str(top_file)}
    assert "1" in window.index_summary_label.text()

    window._reset_directories(str(second))
    wait_for_index()
    assert config.directories == [str(second)]
    assert set(window.db.get_all_indexed_paths()) == {str(replacement)}
    assert "1" in window.dir_label.text()

    window._clear_directories()
    wait_for_index()
    assert config.directories == []
    assert window.db.get_all_indexed_paths() == {}
    assert window.dir_label.text() == tr(window.language, "directory_empty")
    assert "0" in window.index_summary_label.text()
    window.close()


def test_directory_change_clears_results_and_rejects_superseded_search(tmp_path, monkeypatch):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    window = MainWindow(config)
    config.directories = [str(old)]
    window._refresh_dir_label()
    window.search_input.setText("searchable")
    window.search_timer.stop()
    window.table.set_results([_result("stale.txt", 1)])
    old_signature = window._current_filter_signature()
    worker = SimpleNamespace(type_filter=window.active_type_filter, filter_signature=old_signature)
    window.search_worker = worker
    monkeypatch.setattr(window, "_start_indexing", lambda *args, **kwargs: None)

    window._reset_directories(str(new))
    assert window.table.rowCount() == 0
    assert window.results_count_label.text() == tr(window.language, "search_waiting")
    window._on_search_finished(worker, [_result("stale.txt", 1)], "searchable", 1.0)
    assert window.table.rowCount() == 0
    window.search_worker = None
    window.close()


def test_result_sort_keeps_item_mapping_and_displays_snippet():
    app = _app()
    table = ResultTable()
    low = _result("low.txt", 1)
    high = _result("high.txt", 8)
    table.set_results([low, high])
    table.sortItems(2, Qt.DescendingOrder)
    table.selectRow(0)
    app.processEvents()

    assert table.get_selected_item() is high
    assert table.cellWidget(0, 1).property("result_filename") == "high.txt"
    assert "keyword" in table.cellWidget(0, 1).property("snippet_html")


def test_preview_counts_and_navigates_highlighted_matches():
    _app()
    preview = PreviewPanel()
    preview.display_result(_result("preview.txt", 2))

    assert preview.match_count == 2
    assert preview.match_counter_label.text() == "1 / 2"
    first_html = preview.browser.toHtml()
    assert "#f97316" in first_html.lower()
    assert preview.browser.property("active_match_index") == 0
    preview._next_match()
    assert preview.match_counter_label.text() == "2 / 2"
    assert preview.browser.property("active_match_index") == 1
    assert preview.browser.toHtml() != first_html
    preview._previous_match()
    assert preview.match_counter_label.text() == "1 / 2"


def test_preview_zoom_changes_rendered_css_font_size():
    _app()
    preview = PreviewPanel()
    preview.display_result(_result("zoom.txt", 2))

    assert preview.browser.property("preview_font_px") == 13
    preview._zoom_in()
    assert preview.browser.property("preview_font_px") == 15
    assert "font-size:15px" in preview.browser.toHtml().replace(" ", "").lower()
    preview._zoom_out()
    assert preview.browser.property("preview_font_px") == 13


def test_combo_popup_expands_for_long_labels():
    app = _app()
    combo = ContentWidthComboBox()
    combo.addItems(["Short", "A translated option that must remain fully visible"])
    combo.resize(72, 28)
    combo.show()
    combo.showPopup()
    app.processEvents()

    assert combo.content_popup_width() > combo.width()
    assert combo.view().minimumWidth() == combo.content_popup_width()
    assert combo.view().textElideMode() == Qt.ElideNone
    combo.hidePopup()


def test_filter_preferences_and_syntax_input(tmp_path):
    app = _app()
    config_path = tmp_path / "config.json"
    config = AppConfig(config_path)
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    assert isinstance(window.search_input, SyntaxSearchInput)
    window.search_input.setText('budget AND "annual report"')
    window.search_input.setCursorPosition(6)
    window._insert_query_syntax(" OR ")
    assert window.search_input.text() == 'budget OR AND "annual report"'

    window.date_field_combo.setCurrentIndex(window.date_field_combo.findData("ctime"))
    window.filter_buttons[1].click()
    window.date_filter_combo.setCurrentIndex(window.date_filter_combo.findData("day"))
    window.size_preset_combo.setCurrentIndex(window.size_preset_combo.findData("medium"))
    window.include_path_input.setText(str(tmp_path / "team"))
    window.match_case.setChecked(True)
    app.processEvents()
    window.close()

    saved = AppConfig(config_path)
    assert saved.search_filters["date_field"] == "ctime"
    assert saved.search_filters["file_type"] == "pdf"
    assert saved.search_filters["size_mode"] == "medium"
    assert saved.search_filters["match_case"] is True
    assert saved.search_filters["include_paths"] == str(tmp_path / "team")


def test_search_syntax_colors_operators_and_exact_phrases():
    _app()
    editor = SyntaxSearchInput()
    editor.set_theme(DARK_PALETTE)
    editor.setText('budget AND "annual report"')
    editor.highlighter.rehighlight()

    formats = editor.document().firstBlock().layout().formats()
    colored = {
        (item.start, item.length): item.format.foreground().color().name() for item in formats
    }
    assert colored[(7, 3)] == "#fb923c"
    assert colored[(11, 15)] == "#86efac"


def test_custom_size_units_and_reset_filters(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    window.size_preset_combo.setCurrentIndex(window.size_preset_combo.findData("custom"))
    window.min_size.setValue(50)
    window.min_unit.setCurrentIndex(window.min_unit.findData("KB"))
    window.include_path_input.setText(str(tmp_path / "team"))

    assert window._current_search_filters()["min_size"] == 50 * 1024
    assert window.btn_reset_filters.isVisibleTo(window.search_card)
    window.max_size.setValue(20)
    window.max_unit.setCurrentIndex(window.max_unit.findData("KB"))
    window._trigger_search()
    assert window.results_count_label.text() == tr(window.language, "invalid_size_range")
    window._reset_all_filters()
    assert window._current_search_filters().get("min_size") is None
    assert window.include_path_input.text() == ""
    assert window.size_preset_combo.currentData() == "any"
    assert config.exclude_patterns == []
    window.close()


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def adjust(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = adjust(r), adjust(g), adjust(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    l1, l2 = max(l1, l2), min(l1, l2)
    return (l1 + 0.05) / (l2 + 0.05)


def test_status_bar_shows_version_and_no_longer_duplicates_top_bar(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)

    # No documents indexed yet: version shown, no duplicate document count.
    assert window.status_label.text() == tr(
        window.language, "status_bar_never", version=APP_VERSION
    )
    assert window.last_updated_label.text() == tr(window.language, "last_updated_never")

    stats = {"total_docs": 6429, "last_indexed_at": 1758585600.0}
    idle_text = window._idle_status_text(stats)
    assert idle_text.startswith(f"v{APP_VERSION}")
    assert "6,429" in idle_text
    # The old duplicate "index up to date | N documents" keys must be retired.
    from doc_searcher.desktop import i18n

    assert "index_current" not in i18n.TRANSLATIONS["zh-TW"]
    assert "status_ready" not in i18n.TRANSLATIONS["zh-TW"]
    window.close()


def test_status_bar_timestamp_tracks_directory_changes_refresh_and_scan(tmp_path, monkeypatch):
    _app()
    config_path = tmp_path / "config.json"
    config = AppConfig(config_path)
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    folder = tmp_path / "docs"
    replacement = tmp_path / "replacement"
    folder.mkdir()
    replacement.mkdir()
    monkeypatch.setattr(window, "_start_indexing", lambda *args, **kwargs: None)

    window._add_directories([str(folder)])
    first = config.last_updated_at
    assert first > 0
    assert AppConfig(config_path).last_updated_at == first
    assert (
        datetime.fromtimestamp(first).strftime("%Y-%m-%d %H:%M") in window.last_updated_label.text()
    )

    config.last_updated_at = 1.0
    window._reset_directories(str(replacement))
    assert config.last_updated_at > 1.0
    config.last_updated_at = 1.0
    window._on_manual_refresh()
    assert config.last_updated_at > 1.0
    config.last_updated_at = 1.0
    window._on_indexing_finished({"indexed": 0, "deleted": 0, "failed": 0})
    assert config.last_updated_at > 1.0
    assert AppConfig(config_path).last_updated_at == config.last_updated_at
    assert "最後更新：" in window.last_updated_label.text()
    window._set_index_state("scanning")
    assert "最後更新：" in window.last_updated_label.text()
    window.btn_lang_en.click()
    assert "Last Updated:" in window.last_updated_label.text()
    assert (
        window.last_updated_label.minimumWidth()
        >= window.last_updated_label.fontMetrics().horizontalAdvance(
            window.last_updated_label.text()
        )
    )
    window.close()

    reloaded = AppConfig(config_path)
    reloaded.settings.directories = []
    reopened = MainWindow(reloaded)
    assert (
        datetime.fromtimestamp(reloaded.last_updated_at).strftime("%Y-%m-%d %H:%M")
        in reopened.last_updated_label.text()
    )
    reopened.close()


def test_status_bar_omits_os_badge(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)

    assert not hasattr(window, "os_label")
    assert window.status_label.parent() is window.status_bar
    assert window.last_updated_label.parent() is window.status_bar
    window.close()


def test_format_chips_live_inside_advanced_filters_panel(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)

    for button in window.filter_buttons:
        assert button.window() is window.advanced_dialog
    # No standalone format row remains directly under the search input.
    assert window.filter_label.window() is window.advanced_dialog
    window.close()


def test_search_field_restrictor_is_highlighted_distinctly():
    _app()
    editor = SyntaxSearchInput()
    editor.set_theme(DARK_PALETTE)
    editor.setText("filename:annual report")
    editor.highlighter.rehighlight()

    formats = editor.document().firstBlock().layout().formats()
    colored = {
        (item.start, item.length): item.format.foreground().color().name() for item in formats
    }
    assert colored[(0, len("filename:"))] == "#38bdf8"


def test_theme_colors_meet_wcag_aa_for_muted_text_and_errors():
    # Regression guard for the light-mode contrast audit fix.
    assert _contrast_ratio(LIGHT_PALETTE.text_muted, LIGHT_PALETTE.bg_window) >= 4.5
    assert _contrast_ratio(LIGHT_PALETTE.error, LIGHT_PALETTE.bg_window) >= 4.5
    assert _contrast_ratio(DARK_PALETTE.error, DARK_PALETTE.bg_window) >= 4.5


def test_theme_switch_updates_palette_sidebar_and_preserves_results(tmp_path):
    from PySide6.QtGui import QColor, QPalette

    app = _app()
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    window.show()
    window.table.set_results([_result("first.txt", 2), _result("second.txt", 3)])
    window.table.selectRow(1)
    selected = window.table.get_selected_item()
    window.preview._next_match()
    active_match = window.preview.current_match
    for theme in (DARK_PALETTE, LIGHT_PALETTE, DARK_PALETTE):
        window.apply_theme(theme)
        app.processEvents()
        assert app.palette().color(QPalette.Window) == QColor(theme.bg_window)
        assert app.palette().color(QPalette.PlaceholderText) == QColor(theme.text_muted)
        assert window.advanced_dialog.palette().color(QPalette.Window) == QColor(theme.bg_window)
        # The empty sidebar area must not fall back to the OS's light background.
        sidebar_image = window.sidebar.grab().toImage()
        assert sidebar_image.pixelColor(5, sidebar_image.height() - 5) == QColor(theme.bg_window)
        assert window.table.get_selected_item() is selected
        assert window.preview.current_match == active_match
        assert theme.bg_card in app.styleSheet()  # Tooltips change with the window.
    window.close()


@pytest.mark.parametrize("theme", [LIGHT_PALETTE, DARK_PALETTE])
def test_selected_result_labels_and_highlights_follow_theme(theme):
    from PySide6.QtWidgets import QLabel

    app = _app()
    table = ResultTable()
    table.apply_theme(theme)
    table.set_results([_result("first.txt", 2), _result("second.txt", 3)])
    table.selectRow(1)
    app.processEvents()
    for row in range(2):
        container = table.cellWidget(row, 1)
        label = container.findChild(QLabel, "resultNameLabel")
        snippet = container.findChild(QLabel, "resultSnippetLabel")
        expected = theme.text_selected if row == 1 else theme.text_primary
        assert expected in label.styleSheet()
        assert theme.mark_text in snippet.text()
        assert theme.mark_bg in snippet.text()
        assert "<mark" not in snippet.text()
    assert _contrast_ratio(theme.text_selected, theme.bg_selected) >= 4.5
    assert _contrast_ratio(theme.mark_text, theme.mark_bg) >= 4.5
    table.close()
