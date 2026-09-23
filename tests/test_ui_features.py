# Purpose: Regression tests for localization, result sorting, and preview navigation.
# What the code does:
#   - Confirms locale persistence and immediate UI translation.
#   - Confirms sorted rows retain their SearchResultItem association.
#   - Confirms the active preview match is distinct and CSS text zoom changes rendering.
#   - Confirms combo-box popups expand beyond narrow controls for long translations.
# Usage notes, dependencies, or assumptions:
#   - Uses Qt's offscreen platform so no desktop session is required.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.i18n import tr
from core.searcher import SearchResultItem, SegmentMatch
from ui.main_window import MainWindow, ContentWidthComboBox
from ui.preview_panel import PreviewPanel
from ui.result_table import ResultTable
from ui.search_input import SyntaxSearchInput
from ui.theme import DARK_PALETTE, LIGHT_PALETTE
from core.version import APP_VERSION


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
        segments=[SegmentMatch(
            segment_id="1",
            segment_type="text",
            snippets=[
                'before <mark style="background-color: #ffeb3b">keyword</mark> after '
                '<mark style="background-color: #ffeb3b">keyword</mark>'
            ],
        )],
    )


def test_language_preference_persists_and_updates_window(tmp_path):
    app = _app()
    config_path = tmp_path / "config.json"
    config = AppConfig(config_path)
    config.data["db_path"] = str(tmp_path / "index.db")
    window = MainWindow(config)

    window.btn_lang_en.click()
    app.processEvents()

    assert window.windowTitle() == tr("en-US", "app_title")
    assert window.btn_search.text() == "Search"
    assert AppConfig(config_path).language == "en-US"
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
    config.data["db_path"] = str(tmp_path / "index.db")
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
        (item.start, item.length): item.format.foreground().color().name()
        for item in formats
    }
    assert colored[(7, 3)] == "#fb923c"
    assert colored[(11, 15)] == "#86efac"


def test_custom_size_units_and_reset_filters(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.data["db_path"] = str(tmp_path / "index.db")
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
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

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
    config.data["db_path"] = str(tmp_path / "index.db")
    window = MainWindow(config)

    # No documents indexed yet: version shown, no duplicate document count.
    assert window.status_label.text() == tr(window.language, "status_bar_never", version=APP_VERSION)

    stats = {"total_docs": 6429, "last_indexed_at": 1758585600.0}
    idle_text = window._idle_status_text(stats)
    assert idle_text.startswith(f"v{APP_VERSION}")
    assert "6,429" in idle_text
    # The old duplicate "index up to date | N documents" keys must be retired.
    from core import i18n
    assert "index_current" not in i18n.TRANSLATIONS["zh-TW"]
    assert "status_ready" not in i18n.TRANSLATIONS["zh-TW"]
    window.close()


def test_os_badge_follows_active_theme(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.data["db_path"] = str(tmp_path / "index.db")
    window = MainWindow(config)

    window.apply_theme(LIGHT_PALETTE)
    assert LIGHT_PALETTE.bg_subtle in window.os_label.styleSheet()
    window.apply_theme(DARK_PALETTE)
    assert DARK_PALETTE.bg_subtle in window.os_label.styleSheet()
    window.close()


def test_format_chips_live_inside_advanced_filters_panel(tmp_path):
    _app()
    config = AppConfig(tmp_path / "config.json")
    config.data["db_path"] = str(tmp_path / "index.db")
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
        (item.start, item.length): item.format.foreground().color().name()
        for item in formats
    }
    assert colored[(0, len("filename:"))] == "#38bdf8"


def test_theme_colors_meet_wcag_aa_for_muted_text_and_errors():
    # Regression guard for the light-mode contrast audit fix.
    assert _contrast_ratio(LIGHT_PALETTE.text_muted, LIGHT_PALETTE.bg_window) >= 4.5
    assert _contrast_ratio(LIGHT_PALETTE.error, LIGHT_PALETTE.bg_window) >= 4.5
    assert _contrast_ratio(DARK_PALETTE.error, DARK_PALETTE.bg_window) >= 4.5
