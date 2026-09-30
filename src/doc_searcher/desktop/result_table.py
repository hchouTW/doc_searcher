# Purpose: High-performance search result table widget with complete theme support.
# What the code does:
#   - Displays sortable metadata and highlighted matching excerpts in Dark and Light modes.
#   - Synchronizes selection with preview panel.
#   - Supports Enter / double-click to open file and right-click context menu.
#     A failed open or reveal is reported through file_action_failed(action, item), which the
#     window forwards to the preview panel's notice.
#   - Columns can be hidden (File Name is always visible); widths adapt so the visible columns
#     always fill the viewport, File Name has priority, and long names/paths elide with tooltips.
#   - The selected row gets a 4 px left bar (theme.selected_bar) painted by a delegate.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets (QTableWidget, QHeaderView, QMenu), doc_searcher.desktop.theme.ThemeColors.
#   - Column ids come from doc_searcher.config.COLUMN_IDS; the table never touches AppConfig,
#     MainWindow feeds it through set_visible_columns() and listens to visible_columns_changed.

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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QModelIndex, QEvent, QPersistentModelIndex
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter

from doc_searcher.config import COLUMN_IDS, REQUIRED_COLUMN

from doc_searcher.search.searcher import SearchResultItem
from doc_searcher.platform.platform_helper import (
    open_file_with_default_app,
    reveal_in_file_manager,
    format_file_size,
    format_timestamp,
)
from doc_searcher.desktop.theme import ThemeColors, get_active_theme
from doc_searcher.desktop.i18n import tr


SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class SortableTableItem(QTableWidgetItem):
    """Table item that sorts by a raw value while showing formatted text."""

    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


SELECTION_BAR_WIDTH = 4
FILENAME_COLUMN = COLUMN_IDS.index(REQUIRED_COLUMN)
PATH_COLUMN = COLUMN_IDS.index("path")
PATH_SHARE = 0.28  # fraction of the viewport offered to the Path column
PATH_MIN_WIDTH = 100
FILENAME_MIN_WIDTH = 160


class ElidedLabel(QLabel):
    """Single-line label that shows the full text, elided with an ellipsis when too narrow."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        width = max(0, self.width() - 2)
        super().setText(
            QFontMetrics(self.font()).elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, width
            )
            if width
            else self._full_text
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class SelectionBarDelegate(QStyledItemDelegate):
    """Paints the theme's selection bar at the left edge of the selected row."""

    def __init__(self, table: "ResultTable"):
        super().__init__(table)
        self._table = table

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ):
        super().paint(painter, option, index)
        table = self._table
        if index.column() == table.first_visible_column() and table.isRowSelected(index.row()):
            rect = option.rect
            painter.fillRect(
                rect.left(),
                rect.top(),
                SELECTION_BAR_WIDTH,
                rect.height(),
                QColor(table.theme.selected_bar),
            )


class ResultTable(QTableWidget):
    """Table widget presenting search matches with adaptive high-contrast styling."""

    item_selected = Signal(object)  # Emits SearchResultItem or None
    file_action_failed = Signal(str, object)  # ("open" | "reveal", SearchResultItem)
    visible_columns_changed = Signal(list)  # Emits the visible column ids after a change

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

        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setItemDelegate(SelectionBarDelegate(self))

        header = self.horizontalHeader()
        for column in range(len(COLUMN_IDS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(FILENAME_COLUMN, QHeaderView.Fixed)
        header.setSectionResizeMode(PATH_COLUMN, QHeaderView.Fixed)
        header.setMinimumSectionSize(40)
        header.setStretchLastSection(False)
        self.viewport().installEventFilter(self)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSortIndicatorShown(True)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    # ------------------------------------------------------------ column visibility
    def visible_columns(self) -> List[str]:
        return [
            column_id
            for index, column_id in enumerate(COLUMN_IDS)
            if not self.isColumnHidden(index)
        ]

    def first_visible_column(self) -> int:
        for index in range(len(COLUMN_IDS)):
            if not self.isColumnHidden(index):
                return index
        return FILENAME_COLUMN

    def isRowSelected(self, row: int) -> bool:
        return self.selectionModel().isRowSelected(row, QModelIndex())

    def set_visible_columns(self, column_ids) -> None:
        """Show exactly the given columns; File Name is always kept visible."""
        wanted = set(column_ids) | {REQUIRED_COLUMN}
        before = self.visible_columns()
        for index, column_id in enumerate(COLUMN_IDS):
            self.setColumnHidden(index, column_id not in wanted)
        self._apply_column_widths()
        self.viewport().update()
        if self.visible_columns() != before:
            self.visible_columns_changed.emit(self.visible_columns())

    def _apply_column_widths(self) -> None:
        """Size File Name and Path adaptively so the visible columns fill the viewport.

        File Name has priority: it takes the room left after the content-sized columns and
        Path, never drops below FILENAME_MIN_WIDTH (the table scrolls sideways instead), and
        Path only receives a share of the space that File Name can spare.
        """
        header = self.horizontalHeader()
        show_path = not self.isColumnHidden(PATH_COLUMN)
        other = sum(
            header.sectionSize(i)
            for i in range(len(COLUMN_IDS))
            if i not in (FILENAME_COLUMN, PATH_COLUMN) and not self.isColumnHidden(i)
        )
        room = self.viewport().width() - other
        path_width = 0
        if show_path:
            path_width = max(PATH_MIN_WIDTH, int(room * PATH_SHARE))
            path_width = max(PATH_MIN_WIDTH // 2, min(path_width, room - FILENAME_MIN_WIDTH))
            header.resizeSection(PATH_COLUMN, path_width)
        header.resizeSection(FILENAME_COLUMN, max(FILENAME_MIN_WIDTH, room - path_width))

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_column_widths()
        return super().eventFilter(obj, event)

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
                border-bottom: 1px solid {theme.border};
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
            it_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
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
            it_matches.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            it_matches.setForeground(QColor("#60a5fa" if c.is_dark else "#0a58ca"))

            # 4. File size
            it_size = SortableTableItem(format_file_size(item.file_size))
            it_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it_size.setForeground(QColor(c.text_muted))

            # 5. Modified time
            it_time = SortableTableItem(format_timestamp(item.mtime))
            it_time.setForeground(QColor(c.text_muted))

            # 6. Path
            it_path = SortableTableItem(item.path)
            it_path.setToolTip(item.path)
            it_path.setForeground(QColor(c.text_muted))

            for table_item, sort_value in (
                (it_type, item.file_type.lower()),
                (it_name, item.filename.casefold()),
                (it_matches, item.total_matches),
                (it_size, item.file_size),
                (it_time, item.mtime),
                (it_path, item.path.casefold()),
            ):
                table_item.setData(Qt.ItemDataRole.UserRole, item)
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
            name_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name_container.setProperty("snippet_html", snippet)
            name_container.setProperty("result_filename", item.filename)
            name_container.setStyleSheet("background: transparent;")
            name_layout = QVBoxLayout(name_container)
            name_layout.setContentsMargins(8, 3, 6, 3)
            name_layout.setSpacing(1)
            name_label = ElidedLabel(item.filename)
            name_label.setObjectName("resultNameLabel")
            name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name_label.setFixedHeight(18)
            name_label.setStyleSheet(
                f"color: {c.text_primary}; font-weight: bold; background: transparent;"
            )
            snippet_label = QLabel(snippet)
            snippet_label.setObjectName("resultSnippetLabel")
            snippet_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            snippet_label.setWordWrap(True)
            snippet_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            snippet_label.setFixedHeight(38)
            snippet_label.setTextFormat(Qt.TextFormat.RichText)
            snippet_label.setToolTip(re.sub(r"<[^>]+>", "", snippet))
            snippet_label.setStyleSheet(
                f"color: {c.text_secondary}; font-size: 11px; background: transparent;"
            )
            name_layout.addWidget(name_label)
            name_layout.addWidget(snippet_label)
            self.setCellWidget(row, 1, name_container)
            self.setRowHeight(row, 65)

        self.setSortingEnabled(True)
        self._apply_column_widths()
        if 0 <= previous_sort_column < len(COLUMN_IDS):
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
            selected = bool(it_name and it_name.isSelected())
            name_container = self.cellWidget(row, 1)
            if name_container:
                name_label = name_container.findChild(QLabel, "resultNameLabel")
                snippet_label = name_container.findChild(QLabel, "resultSnippetLabel")
                if name_label:
                    name_label.setStyleSheet(
                        f"color: {c.text_selected if selected else c.text_primary}; "
                        "font-weight: bold; background: transparent;"
                    )
                if snippet_label:
                    snippet = name_container.property("snippet_html") or ""
                    snippet = re.sub(
                        r"<mark\b[^>]*>",
                        f'<span style="background-color: {c.mark_bg}; color: {c.mark_text}; font-weight: bold;">',
                        snippet,
                    ).replace("</mark>", "</span>")
                    snippet_label.setText(snippet)
                    snippet_label.setStyleSheet(
                        f"color: {c.text_selected if selected else c.text_secondary}; "
                        "font-size: 11px; background: transparent;"
                    )

    def get_selected_item(self) -> Optional[SearchResultItem]:
        selected = self.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        first_item = self.item(row, 0)
        return first_item.data(Qt.ItemDataRole.UserRole) if first_item else None

    def _on_selection_changed(self):
        self._refresh_row_colors()
        item = self.get_selected_item()
        self.item_selected.emit(item)

    def _open_item(self, item: SearchResultItem):
        if not open_file_with_default_app(item.path):
            self.file_action_failed.emit("open", item)

    def _on_double_clicked(self, table_item):
        item = self.get_selected_item()
        if item:
            self._open_item(item)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            item = self.get_selected_item()
            if item:
                self._open_item(item)
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
            self._open_item(item)
        elif action == act_reveal:
            if not reveal_in_file_manager(item.path):
                self.file_action_failed.emit("reveal", item)
        elif action == act_copy_path:
            QApplication.clipboard().setText(item.path)
