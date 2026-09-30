# Purpose: "Display Columns" button and menu for choosing which result-table columns are shown.
# What the code does:
#   - Shows a checkbox per column (menu stays open while toggling) plus "Reset to Default".
#   - File Name is checked and disabled, so it can never be hidden.
#   - Emits columns_changed(list of column ids) in fixed display order; holds no other state.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets; column ids come from doc_searcher.config.COLUMN_IDS.
#   - The owner (MainWindow) applies the ids to the table and persists them.

import tempfile
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QMenu, QToolButton, QWidgetAction

from doc_searcher.config import COLUMN_IDS, REQUIRED_COLUMN
from doc_searcher.desktop.i18n import tr
from doc_searcher.desktop.theme import ThemeColors

COLUMN_LABEL_KEYS = {
    "type": "table_type",
    "filename": "table_filename",
    "hits": "table_hits",
    "size": "table_size",
    "modified": "table_modified",
    "path": "table_path",
}

INDICATOR_SIZE = 18


def _check_icon(color: str) -> str:
    """Render a check mark PNG (Qt style sheets cannot draw one) and return its path."""
    folder = Path(tempfile.gettempdir()) / "doc_searcher_icons"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"check_{color.lstrip('#')}.png"
    pixmap = QPixmap(INDICATOR_SIZE * 2, INDICATOR_SIZE * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    n = INDICATOR_SIZE * 2
    painter.drawPolyline(
        [QPointF(n * 0.25, n * 0.52), QPointF(n * 0.43, n * 0.70), QPointF(n * 0.76, n * 0.32)]
    )
    painter.end()
    pixmap.save(str(path), "PNG")
    return path.as_posix()


class ColumnMenuButton(QToolButton):
    columns_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.language = "zh-TW"
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setObjectName("columnMenuButton")
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._checks: Dict[str, QCheckBox] = {}
        for column_id in COLUMN_IDS:
            check = QCheckBox()
            check.setChecked(True)
            check.setEnabled(column_id != REQUIRED_COLUMN)
            check.toggled.connect(self._on_toggled)
            action = QWidgetAction(self._menu)
            action.setDefaultWidget(check)
            self._menu.addAction(action)
            self._checks[column_id] = check
        self._menu.addSeparator()
        self._reset_action = self._menu.addAction("")
        self._reset_action.triggered.connect(self._reset)
        self.set_language(self.language)

    def selected_columns(self) -> List[str]:
        return [c for c in COLUMN_IDS if self._checks[c].isChecked()]

    def set_columns(self, column_ids) -> None:
        """Reflect the given columns in the checkboxes without emitting a change."""
        wanted = set(column_ids) | {REQUIRED_COLUMN}
        for column_id, check in self._checks.items():
            check.blockSignals(True)
            check.setChecked(column_id in wanted)
            check.blockSignals(False)

    def set_language(self, language: str) -> None:
        self.language = language
        self.setText(f"⚙ {tr(language, 'table_columns_button')}")
        self._reset_action.setText(tr(language, "table_columns_reset"))
        for column_id, check in self._checks.items():
            check.setText(tr(language, COLUMN_LABEL_KEYS[column_id]))

    def apply_theme(self, theme: ThemeColors) -> None:
        radius = max(4, theme.border_radius - 2)
        self.setStyleSheet(
            f"""
            QToolButton#columnMenuButton {{
                background: {theme.btn_bg}; color: {theme.btn_text};
                border: 1px solid {theme.btn_border}; border-radius: {radius}px;
                padding: 4px 10px;
            }}
            QToolButton#columnMenuButton:hover {{ background: {theme.btn_hover}; }}
            QToolButton#columnMenuButton::menu-indicator {{ image: none; }}
            """
        )
        check = _check_icon(theme.accent_text)
        check_disabled = _check_icon(theme.bg_card)
        self._menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: {theme.bg_card}; color: {theme.text_primary};
                border: 1px solid {theme.border}; padding: 4px; border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {theme.bg_selected}; color: {theme.text_selected};
                border-radius: 4px;
            }}
            QCheckBox {{ color: {theme.text_primary}; padding: 4px 8px; background: transparent; }}
            QCheckBox:disabled {{ color: {theme.text_muted}; }}
            /* Non-text contrast >= 3:1 (WCAG 1.4.11): unchecked outline uses text_muted,
               checked state is a filled accent box with a check mark. */
            QCheckBox::indicator {{
                width: {INDICATOR_SIZE - 4}px; height: {INDICATOR_SIZE - 4}px;
                border: 2px solid {theme.text_muted}; border-radius: 4px;
                background: {theme.bg_input};
            }}
            QCheckBox::indicator:hover {{ border-color: {theme.border_focus}; }}
            QCheckBox::indicator:checked {{
                background: {theme.accent}; border-color: {theme.selected_bar};
                image: url({check});
            }}
            QCheckBox::indicator:checked:hover {{ background: {theme.accent_hover}; }}
            QCheckBox::indicator:disabled {{
                background: {theme.bg_subtle}; border-color: {theme.text_muted};
            }}
            QCheckBox::indicator:checked:disabled {{
                background: {theme.text_muted}; border-color: {theme.text_muted};
                image: url({check_disabled});
            }}
            """
        )

    def _on_toggled(self, _checked: bool) -> None:
        self.columns_changed.emit(self.selected_columns())

    def _reset(self) -> None:
        self.set_columns(COLUMN_IDS)
        self.columns_changed.emit(self.selected_columns())
