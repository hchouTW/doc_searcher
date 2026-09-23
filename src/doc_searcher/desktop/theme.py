# Purpose: Unified theme management supporting macOS/Windows Dark & Light modes with OS self-adaptation.
# What the code does:
#   - Detects system dark mode via Qt styleHints.
#   - Adapts typography and corner radius automatically to Windows 11, Windows 10, or macOS.
#   - Defines complete, high-contrast color palettes for both Dark and Light modes.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtGui (QGuiApplication, QColor, QPalette), PySide6.QtCore (Qt).

from dataclasses import dataclass
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from doc_searcher.platform.os_detector import CURRENT_OS


@dataclass
class ThemeColors:
    is_dark: bool
    bg_window: str
    bg_card: str
    bg_subtle: str
    bg_input: str
    bg_table: str
    bg_table_alt: str
    bg_selected: str
    text_primary: str
    text_secondary: str
    text_muted: str
    text_selected: str
    border: str
    border_focus: str
    accent: str
    accent_hover: str
    accent_text: str
    btn_bg: str
    btn_hover: str
    btn_text: str
    btn_border: str
    snippet_bg: str
    snippet_border: str
    mark_bg: str
    mark_text: str
    error: str
    # Adaptive metrics
    border_radius: int = 8
    font_family: str = ""


DARK_PALETTE = ThemeColors(
    is_dark=True,
    bg_window="#18181b",  # Deep zinc
    bg_card="#27272a",  # Slightly lighter zinc
    bg_subtle="#202023",
    bg_input="#1e1e22",
    bg_table="#27272a",
    bg_table_alt="#222225",
    bg_selected="#1d4ed8",  # Blue
    text_primary="#f4f4f5",  # Crisp light gray / near white
    text_secondary="#d4d4d8",  # Readable secondary
    text_muted="#a1a1aa",  # Muted metadata
    text_selected="#ffffff",
    border="#3f3f46",  # Subtle border
    border_focus="#3b82f6",  # Bright blue focus
    accent="#2563eb",
    accent_hover="#1d4ed8",
    accent_text="#ffffff",
    btn_bg="#3f3f46",
    btn_hover="#52525b",
    btn_text="#f4f4f5",
    btn_border="#52525b",
    snippet_bg="#202023",
    snippet_border="#3b82f6",
    mark_bg="#fbbf24",  # Amber / warm yellow highlight
    mark_text="#18181b",  # Dark text inside highlight
    error="#f87171",  # WCAG AA (>=4.5:1) against bg_window and bg_card
    border_radius=CURRENT_OS.border_radius,
    font_family=CURRENT_OS.font_family,
)

LIGHT_PALETTE = ThemeColors(
    is_dark=False,
    bg_window="#f4f5f7",
    bg_card="#ffffff",
    bg_subtle="#f1f3f5",
    bg_input="#ffffff",
    bg_table="#ffffff",
    bg_table_alt="#f8f9fa",
    bg_selected="#dbeafe",
    text_primary="#18181b",  # Deep dark
    text_secondary="#3f3f46",
    text_muted="#6b6b73",  # Darkened from #71717a to clear WCAG AA (4.5:1) on bg_window
    text_selected="#1d4ed8",
    border="#e4e4e7",
    border_focus="#2563eb",
    accent="#2563eb",
    accent_hover="#1d4ed8",
    accent_text="#ffffff",
    btn_bg="#ffffff",
    btn_hover="#f1f3f5",
    btn_text="#18181b",
    btn_border="#ced4da",
    snippet_bg="#f8f9fa",
    snippet_border="#2563eb",
    mark_bg="#fde047",  # Light yellow
    mark_text="#000000",
    error="#c81e1e",  # WCAG AA (>=4.5:1) against bg_window and bg_card
    border_radius=CURRENT_OS.border_radius,
    font_family=CURRENT_OS.font_family,
)


def is_system_dark() -> bool:
    """Detect if macOS or Windows is currently in Dark Mode."""
    app = QApplication.instance()
    if app and hasattr(app, "styleHints"):
        try:
            return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            pass
    return False


def get_active_theme(force_mode: str = "auto") -> ThemeColors:
    """Get active theme palette based on setting or system state."""
    palette = (
        DARK_PALETTE
        if (force_mode == "dark" or (force_mode == "auto" and is_system_dark()))
        else LIGHT_PALETTE
    )
    palette.border_radius = CURRENT_OS.border_radius
    palette.font_family = CURRENT_OS.font_family
    return palette


def generate_qss(c: ThemeColors) -> str:
    """Generate global application QSS stylesheet ensuring zero color conflicts with OS adaptation."""
    r = c.border_radius
    return f"""
        * {{
            font-family: {c.font_family};
        }}

        QMainWindow, QWidget#central {{
            background-color: {c.bg_window};
            color: {c.text_primary};
        }}

        /* Tooltips */
        QToolTip {{
            background-color: {c.bg_card};
            color: {c.text_primary};
            border: 1px solid {c.border};
            padding: 4px 8px;
            border-radius: {max(2, r - 4)}px;
        }}

        /* Scrollbars */
        QScrollBar:vertical {{
            border: none;
            background: {c.bg_window};
            width: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {c.border};
            min-height: 20px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c.text_muted};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}

        /* Status Bar */
        QStatusBar {{
            background-color: {c.bg_window};
            color: {c.text_muted};
            border-top: 1px solid {c.border};
        }}
    """
