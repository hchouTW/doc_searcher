# Purpose: High-performance search result table widget with complete theme support.
# What the code does:
#   - Displays sortable metadata and highlighted matching excerpts in Dark and Light modes.
#   - Synchronizes selection with preview panel.
#   - Supports Enter / double-click to open file and right-click context menu.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets (QTableWidget, QHeaderView, QMenu), doc_searcher.desktop.theme.ThemeColors.

from typing import List, Optional
import re

from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMenu,
    QApplication,
    QLabel,
    QWidget,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from doc_searcher.search.searcher import SearchResultItem
from doc_searcher.platform.platform_helper import (
    open_file_with_default_app,
    reveal_in_file_manager,
    format_file_size,
    format_timestamp,
)
from doc_searcher.desktop.theme import ThemeColors, get_active_theme
from doc_searcher.desktop.i18n import tr


SORT_ROLE = Qt.UserRole + 1


class SortableTableItem(QTableWidgetItem):
    """Table item that sorts by a raw value while showing formatted text."""

    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class ResultTable(QTableWidget):
    """Table widget presenting search matches with adaptive high-contrast styling."""

    item_selected = Signal(object)  # Emits SearchResultItem or None

    HEADER_KEYS = [
        "table_type",
        "table_filename",
        "table_hits",
        "table_size",
        "table_modified",
        "table_path",
    ]

    TYPE_ICONS = {
        "pdf": "📕 PDF",
        "docx": "📘 DOCX",
        "doc": "📘 DOC",
        "pptx": "📙 PPTX",
        "ppt": "📙 PPT",
        "xlsx": "📗 XLSX",
        "xls": "📗 XLS",
        "txt": "📄 TXT",
        "md": "📄 MD",
        "csv": "📊 CSV",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results_data: List[SearchResultItem] = []
        self.language = "zh-TW"
        self.theme: ThemeColors = get_active_theme()
        self._init_ui()
        self.apply_theme(self.theme)

    def _init_ui(self):
        self.setColumnCount(len(self.HEADER_KEYS))
        self.setHorizontalHeaderLabels([tr(self.language, key) for key in self.HEADER_KEYS])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSortingEnabled(True)

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSortIndicatorShown(True)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def set_language(self, language: str):
        """Update table strings without rebuilding the application."""
        self.language = language
        self.setHorizontalHeaderLabels([tr(language, key) for key in self.HEADER_KEYS])
        if self.results_data:
            self.set_results(self.results_data)

    def apply_theme(self, theme: ThemeColors):
        """Apply theme colors explicitly across all table elements."""
        self.theme = theme
        self.setStyleSheet(f"""
            QTableWidget {{
                background-color: {theme.bg_table};
                alternate-background-color: {theme.bg_table_alt};
                color: {theme.text_primary};
                border: 1px solid {theme.border};
                border-radius: 8px;
                gridline-color: transparent;
                selection-background-color: {theme.bg_selected};
                selection-color: {theme.text_selected};
                outline: none;
            }}
            QTableWidget::item {{
                padding: 7px 10px;
                border-bottom: 1px solid {theme.border if theme.is_dark else "transparent"};
            }}
            QTableWidget::item:selected {{
                background-color: {theme.bg_selected};
                color: {theme.text_selected};
            }}
            QHeaderView::section {{
                background-color: {theme.bg_subtle};
                color: {theme.text_secondary};
                font-weight: bold;
                border: none;
                border-bottom: 2px solid {theme.border};
                padding: 8px 10px;
            }}
        """)
        # Refresh row item colors
        self._refresh_row_colors()

    def set_results(self, items: List[SearchResultItem]):
        """Populate table with new search results."""
        self.results_data = items
        previous_sort_column = self.horizontalHeader().sortIndicatorSection()
        previous_sort_order = self.horizontalHeader().sortIndicatorOrder()
        self.setSortingEnabled(False)
        self.clearContents()
        self.setRowCount(len(items))

        c = self.theme
        for row, item in enumerate(items):
            # 1. Type
            type_tag = self.TYPE_ICONS.get(item.file_type.lower(), f"📁 {item.file_type.upper()}")
            it_type = SortableTableItem(type_tag)
            it_type.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            it_type.setForeground(QColor(c.text_secondary))

            # 2. Filename with a highlighted one-to-two-line excerpt underneath.
            # The visible file name lives in the two-line cell widget below; keep
            # the backing item text empty to avoid painting duplicate text.
            it_name = SortableTableItem("")
            it_name.setToolTip(item.filename)
            font = QFont()
            font.setBold(True)
            it_name.setFont(font)
            it_name.setForeground(QColor(c.text_primary))

            # 3. Matches count (light-mode blue darkened to #0a58ca for WCAG AA on alternating rows)
            it_matches = SortableTableItem(f"{item.total_matches} {tr(self.language, 'hit_unit')}")
            it_matches.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            it_matches.setForeground(QColor("#60a5fa" if c.is_dark else "#0a58ca"))

            # 4. File size
            it_size = SortableTableItem(format_file_size(item.file_size))
            it_size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            it_size.setForeground(QColor(c.text_muted))

            # 5. Modified time
            it_time = SortableTableItem(format_timestamp(item.mtime))
            it_time.setForeground(QColor(c.text_muted))

            # 6. Path
            it_path = SortableTableItem(item.path)
            it_path.setForeground(QColor(c.text_muted))

            for table_item, sort_value in (
                (it_type, item.file_type.lower()),
                (it_name, item.filename.casefold()),
                (it_matches, item.total_matches),
                (it_size, item.file_size),
                (it_time, item.mtime),
                (it_path, item.path.casefold()),
            ):
                table_item.setData(Qt.UserRole, item)
                table_item.setData(SORT_ROLE, sort_value)

            self.setItem(row, 0, it_type)
            self.setItem(row, 1, it_name)
            self.setItem(row, 2, it_matches)
            self.setItem(row, 3, it_size)
            self.setItem(row, 4, it_time)
            self.setItem(row, 5, it_path)

            snippet = (
                item.segments[0].snippets[0] if item.segments and item.segments[0].snippets else ""
            )
            snippet = re.sub(r"\s+", " ", snippet).strip()
            name_container = QWidget()
            name_container.setAttribute(Qt.WA_TransparentForMouseEvents)
            name_container.setProperty("snippet_html", snippet)
            name_container.setProperty("result_filename", item.filename)
            name_container.setStyleSheet("background: transparent;")
            name_layout = QVBoxLayout(name_container)
            name_layout.setContentsMargins(8, 3, 6, 3)
            name_layout.setSpacing(1)
            name_label = QLabel(item.filename)
            name_label.setObjectName("resultNameLabel")
            name_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            name_label.setFixedHeight(18)
            name_label.setStyleSheet(
                f"color: {c.text_primary}; font-weight: bold; background: transparent;"
            )
            snippet_label = QLabel(snippet)
            snippet_label.setObjectName("resultSnippetLabel")
            snippet_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            snippet_label.setWordWrap(True)
            snippet_label.setFixedHeight(38)
            snippet_label.setTextFormat(Qt.RichText)
            snippet_label.setToolTip(re.sub(r"<[^>]+>", "", snippet))
            snippet_label.setStyleSheet(
                f"color: {c.text_secondary}; font-size: 11px; background: transparent;"
            )
            name_layout.addWidget(name_label)
            name_layout.addWidget(snippet_label)
            self.setCellWidget(row, 1, name_container)
            self.setRowHeight(row, 65)

        self.setSortingEnabled(True)
        if 0 <= previous_sort_column < 6:
            self.sortItems(previous_sort_column, previous_sort_order)

        if items:
            self.selectRow(0)
        else:
            self.item_selected.emit(None)

    def _refresh_row_colors(self):
        c = self.theme
        for row in range(self.rowCount()):
            it_type = self.item(row, 0)
            it_name = self.item(row, 1)
            it_matches = self.item(row, 2)
            it_size = self.item(row, 3)
            it_time = self.item(row, 4)
            it_path = self.item(row, 5)

            if it_type:
                it_type.setForeground(QColor(c.text_secondary))
            if it_name:
                it_name.setForeground(QColor(c.text_primary))
            if it_matches:
                it_matches.setForeground(QColor("#60a5fa" if c.is_dark else "#0a58ca"))
            if it_size:
                it_size.setForeground(QColor(c.text_muted))
            if it_time:
                it_time.setForeground(QColor(c.text_muted))
            if it_path:
                it_path.setForeground(QColor(c.text_muted))
            name_container = self.cellWidget(row, 1)
            if name_container:
                name_label = name_container.findChild(QLabel, "resultNameLabel")
                snippet_label = name_container.findChild(QLabel, "resultSnippetLabel")
                if name_label:
                    name_label.setStyleSheet(
                        f"color: {c.text_primary}; font-weight: bold; background: transparent;"
                    )
                if snippet_label:
                    snippet_label.setStyleSheet(
                        f"color: {c.text_secondary}; font-size: 11px; background: transparent;"
                    )

    def get_selected_item(self) -> Optional[SearchResultItem]:
        selected = self.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        first_item = self.item(row, 0)
        return first_item.data(Qt.UserRole) if first_item else None

    def _on_selection_changed(self):
        item = self.get_selected_item()
        self.item_selected.emit(item)

    def _on_double_clicked(self, table_item):
        item = self.get_selected_item()
        if item:
            open_file_with_default_app(item.path)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            item = self.get_selected_item()
            if item:
                open_file_with_default_app(item.path)
                event.accept()
                return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        item = self.get_selected_item()
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {self.theme.bg_card};
                color: {self.theme.text_primary};
                border: 1px solid {self.theme.border};
                padding: 4px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {self.theme.bg_selected};
                color: {self.theme.text_selected};
                border-radius: 4px;
            }}
        """)
        act_open = menu.addAction(f"{tr(self.language, 'open_file')} (Enter)")
        act_reveal = menu.addAction(tr(self.language, "reveal_file"))
        menu.addSeparator()
        act_copy_path = menu.addAction(tr(self.language, "copy_path"))

        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == act_open:
            open_file_with_default_app(item.path)
        elif action == act_reveal:
            reveal_in_file_manager(item.path)
        elif action == act_copy_path:
            QApplication.clipboard().setText(item.path)
