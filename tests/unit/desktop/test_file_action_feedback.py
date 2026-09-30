"""DEF-03: a failed open/reveal must tell the user why (missing file, no handler)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from doc_searcher.config import AppConfig
from doc_searcher.desktop import preview_panel, result_table
from doc_searcher.desktop.i18n import TRANSLATIONS, tr
from doc_searcher.desktop.main_window import MainWindow
from doc_searcher.desktop.preview_panel import PreviewPanel
from doc_searcher.desktop.result_table import ResultTable
from doc_searcher.search.searcher import SearchResultItem, SegmentMatch


def _app():
    return QApplication.instance() or QApplication([])


def _item(path, name=None):
    return SearchResultItem(
        doc_id=1,
        path=str(path),
        filename=name or os.path.basename(str(path)),
        file_type="txt",
        file_size=10,
        mtime=1.0,
        rank_score=1.0,
        total_matches=1,
        segments=[
            SegmentMatch("1", "text", ['a <mark style="background-color:#ffeb3b">hit</mark>'])
        ],
    )


@pytest.fixture
def launcher(monkeypatch):
    """Replace the OS launchers (never start a real app); tests set .result and read .calls."""

    class Launcher:
        result = True
        calls = []

    fake = Launcher()

    def opener(path):
        fake.calls.append(("open", path))
        return fake.result

    def revealer(path):
        fake.calls.append(("reveal", path))
        return fake.result

    for module in (preview_panel, result_table):
        monkeypatch.setattr(module, "open_file_with_default_app", opener)
        monkeypatch.setattr(module, "reveal_in_file_manager", revealer)
    fake.calls = []
    return fake


def test_missing_file_shows_a_named_notice_from_both_buttons(tmp_path, launcher):
    _app()
    launcher.result = False
    panel = PreviewPanel()
    panel.display_result(_item(tmp_path / "gone.txt"))
    assert panel.notice_label.isHidden()

    panel._on_open_file()
    assert not panel.notice_label.isHidden()
    assert "gone.txt" in panel.notice_label.text()
    assert panel.notice_label.text() == tr("zh-TW", "file_missing_notice", name="gone.txt")

    panel._hide_notice()
    panel._on_reveal_folder()
    assert panel.notice_label.text() == tr("zh-TW", "file_missing_notice", name="gone.txt")


def test_existing_file_that_cannot_be_opened_gets_the_other_message(tmp_path, launcher):
    _app()
    launcher.result = False
    (tmp_path / "here.txt").write_text("x")
    panel = PreviewPanel()
    panel.language = "en-US"
    panel.display_result(_item(tmp_path / "here.txt"))
    panel._on_open_file()
    assert panel.notice_label.text() == tr("en-US", "file_open_failed_notice", name="here.txt")
    panel._hide_notice()
    panel._on_reveal_folder()
    assert panel.notice_label.text() == tr("en-US", "file_reveal_failed_notice", name="here.txt")


def test_success_shows_no_notice(tmp_path, launcher):
    _app()
    (tmp_path / "here.txt").write_text("x")
    panel = PreviewPanel()
    panel.display_result(_item(tmp_path / "here.txt"))
    panel._on_open_file()
    panel._on_reveal_folder()
    assert panel.notice_label.isHidden()
    assert [kind for kind, _ in launcher.calls] == ["open", "reveal"]


def test_notice_clears_on_selecting_another_result_and_on_its_timer(tmp_path, launcher):
    _app()
    launcher.result = False
    panel = PreviewPanel()
    panel.display_result(_item(tmp_path / "gone.txt"))
    panel._on_open_file()
    assert panel._notice_timer.isActive()
    panel.display_result(_item(tmp_path / "other.txt"))
    assert panel.notice_label.isHidden() and not panel._notice_timer.isActive()

    panel._on_open_file()
    panel._notice_timer.timeout.emit()
    assert panel.notice_label.isHidden()


def test_notice_survives_a_language_switch_but_uses_the_error_color(tmp_path, launcher):
    _app()
    launcher.result = False
    panel = PreviewPanel()
    panel.display_result(_item(tmp_path / "gone.txt"))
    panel._on_open_file()
    panel.set_language("en-US")  # re-renders the item without dropping the message
    assert not panel.notice_label.isHidden()
    assert panel.theme.error in panel.notice_label.styleSheet()


def test_table_reports_failed_open_from_double_click_enter_and_context_menu(tmp_path, launcher):
    app = _app()
    launcher.result = False
    table = ResultTable()
    item = _item(tmp_path / "gone.txt")
    table.set_results([item])
    table.selectRow(0)
    app.processEvents()
    failures = []
    table.file_action_failed.connect(lambda action, it: failures.append((action, it)))

    table._on_double_clicked(None)
    table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert failures == [("open", item), ("open", item)]

    launcher.result = True
    table._on_double_clicked(None)
    assert len(failures) == 2  # a successful open reports nothing


def test_window_forwards_table_failures_to_the_preview(tmp_path, launcher):
    app = _app()
    launcher.result = False
    config = AppConfig(tmp_path / "config.json")
    config.db_path = str(tmp_path / "index.db")
    window = MainWindow(config)
    item = _item(tmp_path / "gone.txt")
    window.table.set_results([item])
    window.table.selectRow(0)
    app.processEvents()

    window.table._on_double_clicked(None)
    assert not window.preview.notice_label.isHidden()
    assert "gone.txt" in window.preview.notice_label.text()
    window.close()


def test_every_language_has_the_notice_texts():
    for language, resources in TRANSLATIONS.items():
        for key in ("file_missing_notice", "file_open_failed_notice", "file_reveal_failed_notice"):
            assert "{name}" in resources[key], (language, key)
        assert "changelog_file_action_feedback" in resources
