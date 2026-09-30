# Purpose: "Display Columns" button and menu for choosing which result-table columns are shown.
# What the code does:
#   - Shows a checkbox per column (menu stays open while toggling) plus "Reset to Default".
#   - File Name is checked and disabled, so it can never be hidden.
#   - Emits columns_changed(list of column ids) in fixed display order; holds no other state.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets; column ids come from doc_searcher.config.COLUMN_IDS.
#   - The owner (MainWindow) applies the ids to the table and persists them.

from typing import Dict, List

from PySide6.QtCore import Signal
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
            """
        )

    def _on_toggled(self, _checked: bool) -> None:
        self.columns_changed.emit(self.selected_columns())

    def _reset(self) -> None:
        self.set_columns(COLUMN_IDS)
        self.columns_changed.emit(self.selected_columns())
