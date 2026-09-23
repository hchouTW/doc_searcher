# Purpose: PySide6 application initialization, theme setup, and runner.
# What the code does:
#   - Configures QApplication with high-DPI scaling and platform attributes.
#   - Applies typography and unified theme style sheets.
#   - Launches MainWindow.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets (QApplication), doc_searcher.desktop.theme.

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from doc_searcher.config import AppConfig
from doc_searcher.desktop.theme import get_active_theme, generate_qss
from .main_window import MainWindow


def run_app():
    """Start the PySide6 Desktop Application with theme adaptation."""
    # Support high-DPI displays
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("DocSearcher")
    app.setOrganizationName("Antigravity")

    # Set default clean system font
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    config = AppConfig()
    theme_mode = config.data.get("theme_mode", "auto")
    theme = get_active_theme(theme_mode)
    app.setStyleSheet(generate_qss(theme))

    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())
