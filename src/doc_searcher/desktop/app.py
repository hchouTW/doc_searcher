# Purpose: PySide6 application initialization, theme setup, and runner.
# What the code does:
#   - Configures QApplication with high-DPI scaling and platform attributes.
#   - Applies typography and unified theme style sheets.
#   - Launches MainWindow and centers its decorated frame on the pointer's screen.
#   - Shows a dialog and exits with code 3 when the data folder or index.db is unusable
#     (ConfigError / StorageError).
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets (QApplication), doc_searcher.desktop.theme.

import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from doc_searcher.config import AppConfig, ConfigError
from doc_searcher.diagnostics import configure_logging
from doc_searcher.storage.errors import StorageError
from doc_searcher.desktop.theme import get_active_theme, apply_application_theme
from .main_window import MainWindow


def centered_frame_top_left(available: QRect, frame: QRect) -> QPoint:
    """Center a frame in the work area while keeping oversized frames reachable."""
    x = available.x() + max(0, (available.width() - frame.width()) // 2)
    y = available.y() + max(0, (available.height() - frame.height()) // 2)
    return QPoint(x, y)


def place_window_on_screen(window, pointer_position: QPoint) -> None:
    """Place the shown window using its native frame and the launch pointer."""
    screen = QGuiApplication.screenAt(pointer_position) or QGuiApplication.primaryScreen()
    if screen is None or window.isMaximized() or window.isFullScreen():
        return
    window.move(centered_frame_top_left(screen.availableGeometry(), window.frameGeometry()))


def run_app():
    """Start the PySide6 Desktop Application with theme adaptation."""
    configure_logging()
    # Support high-DPI displays
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("DocSearcher")
    app.setOrganizationName("Antigravity")

    # Set default clean system font
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    try:
        config = AppConfig()
    except ConfigError as exc:
        QMessageBox.critical(None, "DocSearcher", str(exc))
        sys.exit(3)
    theme_mode = config.theme_mode
    theme = get_active_theme(theme_mode)
    apply_application_theme(theme)

    try:
        window = MainWindow(config)
    except StorageError as exc:
        QMessageBox.critical(None, "DocSearcher", str(exc))
        sys.exit(3)
    pointer_position = QCursor.pos()
    window.show()
    QTimer.singleShot(0, lambda: place_window_on_screen(window, pointer_position))

    sys.exit(app.exec())
