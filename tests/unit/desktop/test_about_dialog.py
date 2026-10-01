import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QWidget

from doc_searcher.desktop.about_dialog import AboutDialog, clamp_into, fit_size
from doc_searcher.desktop.i18n import tr
from doc_searcher.version import APP_VERSION, CHANGELOG

SCREEN = QRect(0, 0, 1440, 860)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _html(language, entries):
    items = []
    for version, date, keys in entries:
        lis = "".join(f"<li>{tr(language, f'changelog_{k}')}</li>" for k in keys)
        items.append(f"<p><b>v{version}</b> — {date}</p><ul>{lis}</ul>")
    return "".join(items)


def _dialog(app, monkeypatch, parent=None, language="en-US", entries=CHANGELOG, area=SCREEN):
    monkeypatch.setattr(AboutDialog, "_available", lambda self: area)
    heading = f"<h3>{tr(language, 'about_current_version', version=APP_VERSION)}</h3>"
    return AboutDialog(parent, tr(language, "about_title"), heading, _html(language, entries))


def _long_entries(count=40):
    return [("9.%d.0" % i, "2026-01-01", list(CHANGELOG[0][2])) for i in range(count)]


def test_fit_size_is_compact_for_short_content_and_capped_by_screen():
    short = fit_size(SCREEN, 400, lambda w: 120, 80)
    assert short == QSize(400, 200)
    tall = fit_size(SCREEN, 5000, lambda w: 50_000, 80)
    assert tall.width() <= 720 and tall.height() == int(SCREEN.height() * 0.9)
    tiny = fit_size(QRect(0, 0, 280, 200), 5000, lambda w: 50_000, 80)
    assert tiny.width() <= 252 and tiny.height() <= 180


def test_clamp_into_keeps_frame_on_screen():
    frame = QRect(1300, 800, 400, 300)
    point = clamp_into(SCREEN, frame)
    assert point == QPoint(1040, 560)
    assert clamp_into(SCREEN, QRect(-50, -50, 400, 300)) == QPoint(0, 0)
    assert clamp_into(QRect(0, 0, 300, 200), QRect(10, 10, 500, 400)) == QPoint(0, 0)


def test_short_changelog_stays_compact(app, monkeypatch):
    dialog = _dialog(app, monkeypatch, entries=CHANGELOG[-1:])
    assert dialog.height() < SCREEN.height() * 0.5
    assert dialog.width() < SCREEN.width() * 0.6
    dialog.show()
    app.processEvents()
    assert dialog.changelog.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize("language", ["en-US", "zh-TW"])
def test_long_changelog_scrolls_only_the_changelog(app, monkeypatch, language):
    dialog = _dialog(app, monkeypatch, language=language, entries=_long_entries())
    dialog.show()
    app.processEvents()
    assert dialog.height() <= SCREEN.height() * 0.9
    assert dialog.width() <= 720
    assert dialog.changelog.verticalScrollBar().maximum() > 0
    assert dialog.changelog.horizontalScrollBar().maximum() == 0
    bounds = dialog.rect()
    assert bounds.contains(dialog.buttons.geometry())
    assert bounds.contains(dialog.heading.geometry())
    assert dialog.buttons.isVisible() and dialog.heading.isVisible()
    assert dialog.heading.geometry().bottom() < dialog.changelog.geometry().top()
    assert dialog.changelog.geometry().bottom() < dialog.buttons.geometry().top()


@pytest.mark.parametrize("language", ["en-US", "zh-TW"])
@pytest.mark.parametrize("area", [QRect(0, 0, 360, 420), QRect(0, 0, 800, 480)])
def test_small_screens_neither_clip_nor_scroll_sideways(app, monkeypatch, language, area):
    dialog = _dialog(app, monkeypatch, language=language, area=area)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= area.width() and dialog.height() <= area.height()
    assert dialog.changelog.horizontalScrollBar().maximum() == 0
    assert dialog.rect().contains(dialog.buttons.geometry())
    assert dialog.buttons.geometry().height() >= dialog.buttons.sizeHint().height()


def test_resizing_rewraps_and_updates_scroll_range(app, monkeypatch):
    dialog = _dialog(app, monkeypatch, entries=_long_entries())
    dialog.show()
    app.processEvents()
    bar = dialog.changelog.verticalScrollBar()
    dialog.resize(640, 500)
    app.processEvents()
    wide = bar.maximum()
    dialog.resize(340, 500)
    app.processEvents()
    assert dialog.changelog.horizontalScrollBar().maximum() == 0
    assert bar.maximum() > wide
    dialog.resize(640, 1200)
    app.processEvents()
    assert dialog.height() <= SCREEN.height()  # maximum size keeps it on screen


def test_centers_over_parent_and_stays_on_screen(app, monkeypatch):
    parent = QWidget()
    parent.setGeometry(300, 200, 800, 500)
    parent.show()
    dialog = _dialog(app, monkeypatch, parent=parent)
    dialog.show()
    app.processEvents()
    assert abs(dialog.frameGeometry().center().x() - parent.frameGeometry().center().x()) <= 2
    assert abs(dialog.frameGeometry().center().y() - parent.frameGeometry().center().y()) <= 2
    dialog.close()
    parent.setGeometry(1300, 780, 300, 200)  # near the bottom-right corner
    edge = _dialog(app, monkeypatch, parent=parent)
    edge.show()
    app.processEvents()
    frame = edge.frameGeometry()
    assert frame.right() <= SCREEN.right() and frame.bottom() <= SCREEN.bottom()
    assert frame.left() >= 0 and frame.top() >= 0
    edge.close()
    parent.close()
