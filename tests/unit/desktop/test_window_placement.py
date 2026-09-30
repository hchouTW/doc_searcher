import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from doc_searcher.desktop.app import centered_frame_top_left, place_window_on_screen


def test_centered_frame_handles_negative_screen_origin_and_odd_dimensions():
    area = QRect(-1920, 30, 1919, 1031)
    frame = QRect(0, 0, 1180, 781)

    point = centered_frame_top_left(area, frame)

    assert abs((point.x() + frame.width() / 2) - area.center().x() - 0.5) <= 1
    assert abs((point.y() + frame.height() / 2) - area.center().y() - 0.5) <= 1


def test_oversized_frame_keeps_top_left_and_size():
    area = QRect(100, 40, 900, 600)
    frame = QRect(0, 0, 1200, 800)

    assert centered_frame_top_left(area, frame) == QPoint(100, 40)


def test_placement_falls_back_to_primary_screen(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.resize(300, 200)
    window.show()
    app.processEvents()
    primary = app.primaryScreen()
    assert primary is not None

    monkeypatch.setattr(QGuiApplication, "screenAt", lambda _point: None)
    place_window_on_screen(window, QPoint(-99999, -99999))

    assert window.frameGeometry().center() == primary.availableGeometry().center()
    window.close()
