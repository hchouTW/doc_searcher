# Purpose: Localized, filterable document-search workspace with background indexing controls.
# What the code does:
#   - Keeps directory selection, clearing, indexing, and search controls in a scrollable sidebar.
#   - Provides metadata filters, query helpers, sortable results, and live index progress.
#   - Shows a persisted last-update time in the bottom status bar.
#   - Uses content-aware combo boxes whose popups escape card clipping and remain readable.
#   - Listens to macOS/Windows system colorScheme changes and updates theme dynamically.
#   - Provides a manual theme toggle button (Auto / Dark / Light).
#   - Ensures high-contrast readability with no system-color mismatches.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets, doc_searcher.desktop.theme.

import os
import re
import time
import math
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple, Any

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSplitter, QFileDialog, QProgressBar, QStatusBar,
    QButtonGroup, QCheckBox, QFrame, QMessageBox, QApplication, QComboBox,
    QToolButton, QDateEdit, QDoubleSpinBox, QStyle, QDialog, QScrollArea,
    QSizePolicy, QTextBrowser, QDialogButtonBox
)
from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextDocument

from doc_searcher.config import AppConfig
from doc_searcher.storage.database import Database
from doc_searcher.search.searcher import SearchResultItem
from doc_searcher.indexing.scanner import FileScanner
from doc_searcher.desktop.i18n import tr
from doc_searcher.version import APP_VERSION, CHANGELOG
from doc_searcher.desktop.theme import ThemeColors, get_active_theme
from doc_searcher.platform.resource_path import resource_path
from .result_table import ResultTable
from .preview_panel import PreviewPanel
from .worker import IndexWorker, SearchWorker
from .search_input import SyntaxSearchInput


class ContentWidthComboBox(QComboBox):
    """Combo box with a popup wide enough for its longest translated option."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(9)
        self.view().setTextElideMode(Qt.ElideNone)
        self.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def content_popup_width(self) -> int:
        """Calculate a screen-bounded popup width for all current labels."""
        margins = 44 + self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        content_width = max(
            (self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())),
            default=0,
        ) + margins
        screen = self.screen().availableGeometry()
        return min(max(self.width(), content_width), max(180, screen.width() - 32))

    def showPopup(self):
        screen = self.screen().availableGeometry()
        popup_width = self.content_popup_width()
        self.view().setMinimumWidth(popup_width)
        super().showPopup()

        # QComboBox creates a top-level popup; resize and raise that window so a
        # narrow parent card cannot crop translated labels or cover the menu.
        popup = self.view().window()
        popup.resize(max(popup.width(), popup_width), popup.height())
        popup.raise_()
        if popup.geometry().right() > screen.right():
            popup.move(screen.right() - popup.width(), popup.y())


class MainWindow(QMainWindow):
    """Main window of the document search application."""

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.db = Database(self.config.db_path)
        self.language = self.config.language
        
        self.theme_mode = self.config.data.get("theme_mode", "auto")  # 'auto', 'dark', 'light'
        self.theme: ThemeColors = get_active_theme(self.theme_mode)
        
        self.active_type_filter = "all"
        self.index_worker: Optional[IndexWorker] = None
        self.search_worker: Optional[SearchWorker] = None
        self.pending_search: Optional[Tuple[str, str, Dict[str, Any]]] = None
        self.pending_index_start = False
        self._folder_dialog: Optional[QFileDialog] = None
        self.index_state = "idle"
        self.last_index_progress: Tuple[int, int, str] = (0, 0, "")
        self._restoring_filters = False

        # Debounce timer for search typing
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(self.config.search_debounce_ms)
        self.search_timer.timeout.connect(self._trigger_search)
        self.exclude_reindex_timer = QTimer(self)
        self.exclude_reindex_timer.setSingleShot(True)
        self.exclude_reindex_timer.setInterval(900)
        self.exclude_reindex_timer.timeout.connect(self._start_indexing)

        self._init_ui()
        self._apply_language()
        self._restore_filter_settings()
        self.apply_theme(self.theme)
        self._update_db_status()

        # Listen for system theme changes (e.g. macOS switching between Light & Dark)
        app = QApplication.instance()
        if app and hasattr(app, "styleHints"):
            try:
                app.styleHints().colorSchemeChanged.connect(self._on_system_color_scheme_changed)
            except Exception:
                pass

        # If directories exist, trigger initial incremental check
        if self.config.directories:
            self._start_indexing()

    def _init_ui(self):
        self.setWindowTitle("文件內文關鍵字檢索系統 (PDF / Word / PPT / Excel)")
        self.resize(1180, 780)
        self.setMinimumSize(880, 560)

        # Set application icon
        icon_path = resource_path(os.path.join("assets", "app_icon.png"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # Central widget
        self.central_widget = QWidget()
        self.central_widget.setObjectName("central")
        self.setCentralWidget(self.central_widget)
        
        main_layout = QHBoxLayout(self.central_widget)
        main_layout.setContentsMargins(16, 16, 16, 12)
        main_layout.setSpacing(12)

        self.splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.splitter)
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setMinimumWidth(280)
        self.sidebar_scroll.setMaximumWidth(360)
        self.sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 4, 0)
        sidebar_layout.setSpacing(10)
        self.sidebar_scroll.setWidget(self.sidebar)
        self.splitter.addWidget(self.sidebar_scroll)

        # 1. Compact status/navigation bar and collapsible settings
        self.top_bar = QFrame()
        top_layout = QVBoxLayout(self.top_bar)
        top_layout.setContentsMargins(10, 6, 10, 6)
        top_layout.setSpacing(8)
        self.index_summary_label = QLabel()
        top_layout.addWidget(self.index_summary_label)
        navigation_row = QHBoxLayout()
        navigation_row.addStretch()

        # Segmented 繁中/EN control replaces the old dropdown (no standalone label;
        # the frame's tooltip carries the "Language" hint for accessibility instead).
        self.language_frame = QFrame()
        self.language_frame.setObjectName("languageSegment")
        self.language_frame.setFixedHeight(32)
        language_seg_layout = QHBoxLayout(self.language_frame)
        language_seg_layout.setContentsMargins(2, 2, 2, 2)
        language_seg_layout.setSpacing(2)
        self.language_group = QButtonGroup(self)
        self.language_group.setExclusive(True)
        self.btn_lang_zh = QPushButton("繁中")
        self.btn_lang_en = QPushButton("EN")
        for button, code in ((self.btn_lang_zh, "zh-TW"), (self.btn_lang_en, "en-US")):
            button.setCheckable(True)
            button.setProperty("lang_code", code)
            button.setChecked(code == self.language)
            self.language_group.addButton(button)
            language_seg_layout.addWidget(button)
        self.language_group.buttonClicked.connect(self._on_language_changed)
        navigation_row.addWidget(self.language_frame)

        self.btn_settings = QToolButton()
        self.btn_settings.setCheckable(True)
        self.btn_settings.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_settings.toggled.connect(self._toggle_settings)
        navigation_row.addWidget(self.btn_settings)
        top_layout.addLayout(navigation_row)
        sidebar_layout.addWidget(self.top_bar)

        self.settings_panel = QFrame()
        settings_layout = QVBoxLayout(self.settings_panel)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(12)

        self.dir_frame = QFrame()
        dir_layout = QVBoxLayout(self.dir_frame)
        dir_layout.setContentsMargins(10, 8, 10, 8)
        dir_layout.setSpacing(6)

        dir_header_layout = QVBoxLayout()
        dir_header_layout.setSpacing(10)

        self.dir_icon = QLabel("📁 檢索目錄：")
        self.dir_icon.setStyleSheet("font-weight: bold;")
        dir_header_layout.addWidget(self.dir_icon)

        self.dir_label = QLabel()
        self.dir_label.setStyleSheet("font-size: 13px;")
        self._refresh_dir_label()
        self.dir_label.setWordWrap(True)
        dir_header_layout.addWidget(self.dir_label)

        self.btn_add_dir = QPushButton("➕ 選擇資料夾...")
        self.btn_add_dir.clicked.connect(self._on_choose_directory)
        dir_header_layout.addWidget(self.btn_add_dir)

        self.btn_reset_dirs = QPushButton("↺ 重設目錄...")
        self.btn_reset_dirs.setToolTip("重新選取一個資料夾並取代目前所有檢索目錄")
        self.btn_reset_dirs.clicked.connect(self._on_reset_directories)
        dir_header_layout.addWidget(self.btn_reset_dirs)

        self.btn_clear_dirs = QPushButton()
        self.btn_clear_dirs.clicked.connect(self._clear_directories)
        dir_header_layout.addWidget(self.btn_clear_dirs)

        dir_layout.addLayout(dir_header_layout)

        self.chk_include_subdirectories = QCheckBox("包含所有下級資料夾")
        self.chk_include_subdirectories.setChecked(self.config.include_subdirectories)
        self.chk_include_subdirectories.setToolTip("開啟時會檢索所選目錄下的所有層級資料夾")
        self.chk_include_subdirectories.toggled.connect(
            self._on_include_subdirectories_toggled
        )
        dir_layout.addWidget(self.chk_include_subdirectories)
        settings_layout.addWidget(self.dir_frame)

        self.appearance_frame = QFrame()
        appearance_layout = QVBoxLayout(self.appearance_frame)
        appearance_layout.setContentsMargins(10, 8, 10, 8)
        appearance_layout.setSpacing(6)

        self.btn_theme_toggle = QPushButton(self._get_theme_btn_text())
        self.btn_theme_toggle.setToolTip("點選切換深色 / 淺色 / 自動跟隨系統外觀")
        self.btn_theme_toggle.clicked.connect(self._toggle_theme_mode)
        appearance_layout.addWidget(self.btn_theme_toggle)

        self.btn_about = QPushButton()
        self.btn_about.clicked.connect(self._show_about_dialog)
        appearance_layout.addWidget(self.btn_about)
        settings_layout.addWidget(self.appearance_frame)
        sidebar_layout.addWidget(self.settings_panel)
        self.settings_panel.setVisible(False)

        # 2. Index controls
        self.index_frame = QFrame()
        index_layout = QVBoxLayout(self.index_frame)
        index_layout.setContentsMargins(10, 8, 10, 8)
        index_layout.setSpacing(10)

        self.index_label = QLabel()
        self.index_label.setStyleSheet("font-weight: bold;")
        index_layout.addWidget(self.index_label)
        self.index_state_label = QLabel()
        index_layout.addWidget(self.index_state_label)

        self.progress_text_label = QLabel()
        self.progress_text_label.setVisible(False)
        index_layout.addWidget(self.progress_text_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        index_layout.addWidget(self.progress_bar)

        self.btn_reindex = QPushButton()
        self.btn_reindex.clicked.connect(self._on_manual_refresh)
        index_layout.addWidget(self.btn_reindex)

        self.btn_pause_index = QPushButton()
        self.btn_pause_index.setEnabled(False)
        self.btn_pause_index.clicked.connect(self._toggle_indexing_pause)
        self.btn_stop_index = QPushButton()
        self.btn_stop_index.setEnabled(False)
        self.btn_stop_index.clicked.connect(self._stop_indexing)
        actions_row = QHBoxLayout()
        actions_row.addWidget(self.btn_pause_index)
        actions_row.addWidget(self.btn_stop_index)
        index_layout.addLayout(actions_row)
        sidebar_layout.addWidget(self.index_frame)

        # 3. Search & Filter Bar
        self.search_card = QFrame()
        search_layout = QVBoxLayout(self.search_card)
        search_layout.setContentsMargins(12, 12, 12, 10)
        search_layout.setSpacing(10)

        # Search Input
        self.search_input = SyntaxSearchInput()
        self.search_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChangedWithText.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self._trigger_search)
        search_layout.addWidget(self.search_input)
        input_row = QHBoxLayout()

        self.btn_clear_search = QToolButton()
        self.btn_clear_search.setText("✕")
        self.btn_clear_search.clicked.connect(self._clear_search)
        self.btn_clear_search.setVisible(False)
        input_row.addWidget(self.btn_clear_search)

        self.btn_search = QPushButton()
        self.btn_search.clicked.connect(self._trigger_search)
        input_row.addWidget(self.btn_search)

        self.btn_search_help = QPushButton()
        self.btn_search_help.clicked.connect(self._show_search_help)
        input_row.addWidget(self.btn_search_help)

        search_layout.addLayout(input_row)

        self.shortcut_focus_search = QShortcut(QKeySequence.Find, self)
        self.shortcut_focus_search.activated.connect(self.search_input.setFocus)
        self.shortcut_clear_search = QShortcut(QKeySequence("Esc"), self.search_input)
        self.shortcut_clear_search.activated.connect(self._clear_search)

        # Advanced metadata filters (format chips now live inside this collapsible
        # panel alongside date/size/path filters instead of a separate row below
        # the search bar, per the compact-layout requirement).
        self.btn_advanced_filters = QToolButton()
        self.btn_advanced_filters.setCheckable(True)
        self.btn_advanced_filters.setArrowType(Qt.RightArrow)
        self.btn_advanced_filters.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_advanced_filters.toggled.connect(self._toggle_advanced_filters)
        search_layout.addWidget(self.btn_advanced_filters, alignment=Qt.AlignLeft)

        self.advanced_dialog = QDialog(self, Qt.Tool)
        self.advanced_dialog.setModal(False)
        self.advanced_dialog.finished.connect(
            lambda _result: self.btn_advanced_filters.setChecked(False)
        )
        dialog_layout = QVBoxLayout(self.advanced_dialog)
        dialog_layout.setContentsMargins(8, 8, 8, 8)
        self.advanced_scroll = QScrollArea()
        self.advanced_scroll.setWidgetResizable(True)
        dialog_layout.addWidget(self.advanced_scroll)
        self.advanced_filter_frame = QFrame()
        advanced_layout = QVBoxLayout(self.advanced_filter_frame)
        advanced_layout.setContentsMargins(8, 6, 8, 6)
        advanced_layout.setSpacing(6)

        # Format chips (multi-selectable via QButtonGroup exclusivity)
        format_row = QHBoxLayout()
        format_row.setSpacing(8)
        self.filter_label = QLabel()
        self.filter_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        format_row.addWidget(self.filter_label)

        self.filter_group = QButtonGroup(self)
        filters = [
            ("filter_all", "all"),
            ("filter_pdf", "pdf"),
            ("filter_word", "word"),
            ("filter_excel", "excel"),
            ("filter_ppt", "ppt"),
            ("filter_text", "text")
        ]

        self.filter_buttons = []
        for idx, (label_key, tag) in enumerate(filters):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setProperty("filter_tag", tag)
            btn.setProperty("label_key", label_key)
            if idx == 0:
                btn.setChecked(True)
            self.filter_group.addButton(btn, idx)
            format_row.addWidget(btn)
            self.filter_buttons.append(btn)

        self.filter_group.buttonClicked.connect(self._on_filter_changed)
        format_row.addStretch()
        advanced_layout.addLayout(format_row)

        date_row = QHBoxLayout()
        self.date_filter_label = QLabel()
        date_row.addWidget(self.date_filter_label)
        self.date_field_combo = ContentWidthComboBox()
        self.date_field_combo.setMinimumWidth(156)
        for key, value in (("date_modified", "mtime"), ("date_created", "ctime")):
            self.date_field_combo.addItem(key, value)
        self.date_field_combo.currentIndexChanged.connect(self._on_metadata_filter_changed)
        date_row.addWidget(self.date_field_combo)
        self.date_filter_combo = ContentWidthComboBox()
        self.date_filter_combo.setMinimumWidth(140)
        for key, value in (("date_all", "all"), ("date_day", "day"),
                           ("date_week", "week"), ("date_month", "month"),
                           ("date_year", "year"),
                           ("date_custom", "custom")):
            self.date_filter_combo.addItem(key, value)
        self.date_filter_combo.currentIndexChanged.connect(self._on_metadata_filter_changed)
        date_row.addWidget(self.date_filter_combo)
        self.date_from_label = QLabel()
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_from.setMinimumWidth(120)
        self.date_from.setMinimumHeight(28)
        self.date_from.setCalendarPopup(True)
        self.date_to_label = QLabel()
        self.date_to = QDateEdit(QDate.currentDate())
        self.date_to.setMinimumWidth(120)
        self.date_to.setMinimumHeight(28)
        self.date_to.setCalendarPopup(True)
        for widget in (self.date_from_label, self.date_from, self.date_to_label, self.date_to):
            widget.setVisible(False)
            date_row.addWidget(widget)
        self.date_from.dateChanged.connect(self._on_metadata_filter_changed)
        self.date_to.dateChanged.connect(self._on_metadata_filter_changed)
        date_row.addStretch()
        advanced_layout.addLayout(date_row)

        size_row = QHBoxLayout()
        self.size_filter_label = QLabel()
        size_row.addWidget(self.size_filter_label)
        self.size_preset_combo = ContentWidthComboBox()
        self.size_preset_combo.setMinimumWidth(180)
        for key, value in (("size_any", "any"), ("size_small", "small"),
                           ("size_medium", "medium"), ("size_large", "large"),
                           ("size_huge", "huge"), ("size_custom", "custom")):
            self.size_preset_combo.addItem(key, value)
        self.size_preset_combo.currentIndexChanged.connect(self._on_metadata_filter_changed)
        size_row.addWidget(self.size_preset_combo)
        self.min_size = QDoubleSpinBox()
        self.max_size = QDoubleSpinBox()
        self.min_unit = ContentWidthComboBox()
        self.max_unit = ContentWidthComboBox()
        for spin, unit in ((self.min_size, self.min_unit), (self.max_size, self.max_unit)):
            spin.setRange(0, 999999)
            spin.setDecimals(1)
            spin.setMinimumWidth(115)
            spin.setMinimumHeight(28)
            spin.setSpecialValueText("—")
            spin.valueChanged.connect(self._on_metadata_filter_changed)
            size_row.addWidget(spin)
            for name in ("KB", "MB", "GB"):
                unit.addItem(name, name)
            unit.setMinimumWidth(72)
            unit.currentIndexChanged.connect(self._on_metadata_filter_changed)
            size_row.addWidget(unit)
        size_row.addStretch()
        advanced_layout.addLayout(size_row)

        path_row = QHBoxLayout()
        self.include_path_label = QLabel()
        path_row.addWidget(self.include_path_label)
        self.include_path_input = QLineEdit()
        self.include_path_input.textChanged.connect(self._on_metadata_filter_changed)
        path_row.addWidget(self.include_path_input, stretch=1)
        self.btn_choose_scope = QPushButton("…")
        self.btn_choose_scope.clicked.connect(self._choose_scope_folder)
        path_row.addWidget(self.btn_choose_scope)
        self.exclude_label = QLabel()
        path_row.addWidget(self.exclude_label)
        self.exclude_input = QLineEdit()
        self.exclude_input.textChanged.connect(self._on_exclude_changed)
        path_row.addWidget(self.exclude_input, stretch=1)
        advanced_layout.addLayout(path_row)

        mode_row = QHBoxLayout()
        self.match_case = QCheckBox()
        self.whole_word = QCheckBox()
        self.regex_mode = QCheckBox()
        for checkbox in (self.match_case, self.whole_word, self.regex_mode):
            checkbox.toggled.connect(self._on_metadata_filter_changed)
            mode_row.addWidget(checkbox)
        mode_row.addStretch()
        advanced_layout.addLayout(mode_row)

        operator_row = QHBoxLayout()
        self.operator_label = QLabel()
        operator_row.addWidget(self.operator_label)
        self.operator_buttons = []
        for label, insertion in (("AND", " AND "), ("OR", " OR "),
                                 ("NOT", " NOT "), ('"…"', '""')):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, text=insertion: self._insert_query_syntax(text))
            operator_row.addWidget(button)
            self.operator_buttons.append(button)
        operator_row.addStretch()
        advanced_layout.addLayout(operator_row)
        self.advanced_filter_frame.setMinimumHeight(212)
        self.advanced_scroll.setWidget(self.advanced_filter_frame)

        self.active_filter_row = QVBoxLayout()
        self.active_filter_row.setSpacing(6)
        self.btn_reset_filters = QPushButton()
        self.btn_reset_filters.clicked.connect(self._reset_all_filters)
        self.active_filter_row.addWidget(self.btn_reset_filters)
        search_layout.addLayout(self.active_filter_row)

        self.results_count_label = QLabel()
        self.results_count_label.setStyleSheet("font-size: 12px;")
        results_status_row = QHBoxLayout()
        results_status_row.addStretch()
        results_status_row.addWidget(self.results_count_label)

        search_layout.addLayout(results_status_row)
        sidebar_layout.addWidget(self.search_card)
        sidebar_layout.addStretch()

        # 3. Main Splitter: Results Table (Left) + Preview Panel (Right)
        # Left Container
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.table = ResultTable()
        self.table.item_selected.connect(self._on_table_item_selected)
        left_layout.addWidget(self.table)
        self.splitter.addWidget(left_widget)

        # Right Container
        self.preview = PreviewPanel()
        self.splitter.addWidget(self.preview)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 6)
        self.splitter.setStretchFactor(2, 4)
        self.splitter.setSizes([300, 500, 380])

        # 4. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel()
        self.status_bar.addWidget(self.status_label, 1)
        self.last_updated_label = QLabel()
        self.last_updated_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_bar.addPermanentWidget(self.last_updated_label)

    def apply_theme(self, theme: ThemeColors):
        """Apply colors across the entire window and child widgets."""
        self.theme = theme
        radius = theme.border_radius
        control_radius = max(4, radius - 2)

        # Main window & central widget
        self.setStyleSheet(f"""
            QMainWindow, QWidget#central {{
                background-color: {theme.bg_window};
                color: {theme.text_primary};
            }}
            QStatusBar {{
                background-color: {theme.bg_window};
                color: {theme.text_muted};
                border-top: 1px solid {theme.border};
            }}
        """)

        # Navigation, settings, and index blocks
        control_card_style = f"""
            QFrame {{
                background-color: {theme.bg_card};
                border: 1px solid {theme.border};
                border-radius: {radius}px;
            }}
        """
        self.dir_frame.setStyleSheet(control_card_style)
        self.appearance_frame.setStyleSheet(control_card_style)
        self.index_frame.setStyleSheet(control_card_style)
        self.top_bar.setStyleSheet(control_card_style)
        self.dir_icon.setStyleSheet(f"font-weight: bold; color: {theme.text_secondary};")
        self.index_label.setStyleSheet(
            f"font-weight: bold; color: {theme.text_secondary};"
        )
        self._refresh_dir_label()

        btn_style = f"""
            QPushButton {{
                background-color: {theme.btn_bg};
                color: {theme.btn_text};
                border: 1px solid {theme.btn_border};
                border-radius: {control_radius}px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {theme.btn_hover};
            }}
        """
        for button in (
            self.btn_add_dir, self.btn_reset_dirs, self.btn_clear_dirs, self.btn_reindex,
            self.btn_pause_index, self.btn_stop_index, self.btn_theme_toggle,
            self.btn_about, self.btn_search_help, self.btn_choose_scope, *self.operator_buttons,
        ):
            button.setStyleSheet(btn_style)
        self.btn_settings.setFixedHeight(32)
        self.btn_settings.setStyleSheet(f"""
            QToolButton {{ background: {theme.btn_bg}; color: {theme.btn_text};
                border: 1px solid {theme.btn_border}; border-radius: {control_radius}px;
                padding: 5px 12px; }}
            QToolButton:hover, QToolButton:checked {{ background: {theme.btn_hover}; }}
        """)
        self.btn_advanced_filters.setStyleSheet(self.btn_settings.styleSheet())
        self.btn_clear_search.setStyleSheet(self.btn_settings.styleSheet())

        # Language segmented control: same height/radius family as the Settings button.
        self.language_frame.setStyleSheet(f"""
            QFrame#languageSegment {{ background: {theme.bg_subtle};
                border: 1px solid {theme.btn_border}; border-radius: {control_radius}px; }}
        """)
        language_seg_btn_style = f"""
            QPushButton {{ background: transparent; color: {theme.text_secondary};
                border: none; border-radius: {max(2, control_radius - 3)}px;
                padding: 4px 14px; font-size: 12px; font-weight: 600; }}
            QPushButton:hover {{ background: {theme.btn_hover}; }}
            QPushButton:checked {{ background: {theme.accent}; color: {theme.accent_text}; }}
        """
        self.btn_lang_zh.setStyleSheet(language_seg_btn_style)
        self.btn_lang_en.setStyleSheet(language_seg_btn_style)

        combo_style = f"""
            QComboBox {{ background-color: {theme.bg_input}; color: {theme.text_primary};
                border: 1px solid {theme.btn_border}; border-radius: {control_radius}px;
                padding: 5px 9px; min-height: 18px; combobox-popup: 0; }}
            QComboBox:hover {{ border-color: {theme.border_focus}; }}
            QComboBox QAbstractItemView {{ background-color: {theme.bg_card};
                color: {theme.text_primary}; border: 1px solid {theme.border_focus};
                padding: 4px; outline: none; selection-background-color: {theme.bg_selected};
                selection-color: {theme.text_selected}; }}
            QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 4px 10px; }}
        """
        for combo in (self.date_field_combo, self.date_filter_combo,
                      self.size_preset_combo, self.min_unit, self.max_unit):
            combo.setStyleSheet(combo_style)
        self.chk_include_subdirectories.setStyleSheet(
            f"font-size: 12px; color: {theme.text_secondary};"
        )
        self.btn_theme_toggle.setText(self._get_theme_btn_text())

        # Search card
        self.search_card.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.bg_card};
                border: 1px solid {theme.border};
                border-radius: {radius}px;
            }}
        """)
        self.search_input.setStyleSheet(f"""
            QPlainTextEdit {{
                border: 2px solid {theme.border};
                border-radius: {control_radius}px;
                padding: 8px 12px;
                font-size: 14px;
                background-color: {theme.bg_input};
                color: {theme.text_primary};
            }}
            QPlainTextEdit:focus {{
                border-color: {theme.border_focus};
            }}
        """)
        self.search_input.set_theme(theme)
        self.btn_search.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.accent};
                color: {theme.accent_text};
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: {control_radius}px;
                padding: 8px 20px;
            }}
            QPushButton:hover {{
                background-color: {theme.accent_hover};
            }}
        """)

        self.filter_label.setStyleSheet(f"font-size: 12px; color: {theme.text_secondary}; font-weight: bold;")
        chip_style = f"""
            QPushButton {{ background: {theme.bg_subtle}; color: {theme.text_secondary};
                border: 1px solid {theme.border}; border-radius: 12px;
                padding: 4px 10px; font-size: 12px; }}
            QPushButton:hover {{ background: {theme.btn_hover}; }}
            QPushButton:checked {{ background: {theme.accent}; color: {theme.accent_text};
                border-color: {theme.accent}; font-weight: bold; }}
        """
        for button in self.filter_buttons:
            button.setStyleSheet(chip_style)
        self.advanced_filter_frame.setStyleSheet(f"""
            QFrame {{ background: {theme.bg_subtle}; border: 1px solid {theme.border};
                border-radius: {control_radius}px; }}
            QLabel {{ border: none; color: {theme.text_secondary}; }}
            QComboBox, QDateEdit, QDoubleSpinBox, QLineEdit {{ background: {theme.bg_input};
                color: {theme.text_primary}; border: 1px solid {theme.border};
                border-radius: 4px; padding: 4px 6px; }}
        """)
        for checkbox in (self.match_case, self.whole_word, self.regex_mode):
            checkbox.setStyleSheet(f"color: {theme.text_secondary};")
        self.btn_reset_filters.setStyleSheet(btn_style)
        self._refresh_filter_badges()
        self.results_count_label.setStyleSheet(f"font-size: 12px; color: {theme.text_muted};")

        # Splitter handle
        self.splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {theme.border};
                width: 3px;
            }}
        """)

        # Status
        self.status_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 12px;")
        self.last_updated_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 12px;")
        self.index_state_label.setStyleSheet(
            f"color: {theme.text_secondary}; background: {theme.bg_subtle}; "
            f"border: 1px solid {theme.border}; border-radius: {control_radius}px; "
            "padding: 3px 8px;"
        )
        self.progress_text_label.setStyleSheet(f"color: {theme.text_muted}; font-size: 11px;")
        self.index_summary_label.setStyleSheet(f"color: {theme.text_secondary}; font-weight: 600;")
        # Update child components
        self.table.apply_theme(theme)
        self.preview.apply_theme(theme)

    def _get_theme_btn_text(self) -> str:
        if self.theme_mode == "auto":
            return tr(self.language, "theme_auto")
        elif self.theme_mode == "dark":
            return tr(self.language, "theme_dark")
        else:
            return tr(self.language, "theme_light")

    def _apply_language(self):
        """Refresh every persistent visible label after a locale change."""
        self.setWindowTitle(tr(self.language, "app_title"))
        self.btn_settings.setText(
            tr(self.language, "hide_settings") if self.settings_panel.isVisible()
            else tr(self.language, "settings")
        )
        self.language_frame.setToolTip(tr(self.language, "language"))
        self.btn_lang_zh.setChecked(self.language == "zh-TW")
        self.btn_lang_en.setChecked(self.language == "en-US")
        self.dir_icon.setText(tr(self.language, "directory_title"))
        self.btn_add_dir.setText(tr(self.language, "choose_folder"))
        self.btn_reset_dirs.setText(tr(self.language, "reset_folders"))
        self.btn_reset_dirs.setToolTip(tr(self.language, "reset_folders_tip"))
        self.btn_clear_dirs.setText(tr(self.language, "clear_folders"))
        self.chk_include_subdirectories.setText(tr(self.language, "include_subdirectories"))
        self.chk_include_subdirectories.setToolTip(tr(self.language, "include_subdirectories_tip"))
        self.btn_theme_toggle.setText(self._get_theme_btn_text())
        self.btn_theme_toggle.setToolTip(tr(self.language, "theme_tip"))
        self.btn_about.setText(tr(self.language, "about_version"))
        self.index_label.setText(tr(self.language, "index_controls"))
        self.index_state_label.setText(tr(self.language, f"state_{self.index_state}"))
        self.btn_reindex.setText(tr(self.language, "scan_index"))
        self.btn_pause_index.setText(
            tr(self.language, "resume_index")
            if self.index_worker and self.index_worker.isRunning() and self.index_worker.is_paused
            else tr(self.language, "pause_index")
        )
        self.btn_stop_index.setText(tr(self.language, "stop_index"))
        self.btn_stop_index.setToolTip(tr(self.language, "stop_index_tip"))
        self.search_input.setPlaceholderText(tr(self.language, "search_placeholder"))
        self.btn_search.setText(tr(self.language, "search"))
        self.btn_clear_search.setToolTip(tr(self.language, "clear_search"))
        self.btn_search_help.setText(tr(self.language, "search_help"))
        self.filter_label.setText(tr(self.language, "format_filter"))
        for button in self.filter_buttons:
            button.setText(tr(self.language, button.property("label_key")))
        self.btn_advanced_filters.setText(tr(self.language, "advanced_filters"))
        self.advanced_dialog.setWindowTitle(tr(self.language, "advanced_filters"))
        self.date_filter_label.setText(tr(self.language, "date_range"))
        for index in range(self.date_field_combo.count()):
            key = "date_created" if self.date_field_combo.itemData(index) == "ctime" else "date_modified"
            self.date_field_combo.setItemText(index, tr(self.language, key))
        date_key_by_value = {
            "all": "date_all", "day": "date_day", "week": "date_week", "month": "date_month",
            "year": "date_year", "custom": "date_custom",
        }
        for index in range(self.date_filter_combo.count()):
            self.date_filter_combo.setItemText(
                index, tr(self.language, date_key_by_value[self.date_filter_combo.itemData(index)])
            )
        self.date_from_label.setText(tr(self.language, "date_from"))
        self.date_to_label.setText(tr(self.language, "date_to"))
        self.size_filter_label.setText(tr(self.language, "size_label"))
        for index in range(self.size_preset_combo.count()):
            self.size_preset_combo.setItemText(
                index, tr(self.language, f"size_{self.size_preset_combo.itemData(index)}")
            )
        self.min_size.setPrefix(f"{tr(self.language, 'size_min')} ")
        self.max_size.setPrefix(f"{tr(self.language, 'size_max')} ")
        self.min_size.setSpecialValueText(f"{tr(self.language, 'size_min')} —")
        self.max_size.setSpecialValueText(f"{tr(self.language, 'size_max')} —")
        self.include_path_label.setText(tr(self.language, "include_path"))
        self.include_path_input.setPlaceholderText(tr(self.language, "include_path_tip"))
        self.btn_choose_scope.setText(tr(self.language, "choose_scope"))
        self.exclude_label.setText(tr(self.language, "exclude_rules"))
        self.exclude_input.setPlaceholderText(tr(self.language, "exclude_rules_tip"))
        self.match_case.setText(tr(self.language, "match_case"))
        self.whole_word.setText(tr(self.language, "whole_word"))
        self.regex_mode.setText(tr(self.language, "regex_mode"))
        self.regex_mode.setToolTip(tr(self.language, "regex_tip"))
        self.btn_reset_filters.setText(tr(self.language, "reset_filters"))
        self.operator_label.setText(tr(self.language, "insert_operator"))
        for button, key in zip(self.operator_buttons, (
            "operator_and_tip", "operator_or_tip", "operator_not_tip", "operator_exact_tip",
        )):
            button.setToolTip(tr(self.language, key))
        if not self.search_input.text().strip() and not self.search_worker:
            self.results_count_label.setText(tr(self.language, "ready_to_search"))
        self.table.set_language(self.language)
        self.preview.set_language(self.language)
        self._refresh_dir_label()
        self._update_db_status(update_main_status=False)
        self._refresh_filter_badges()
        if self.index_worker and self.index_worker.isRunning():
            current, total, filename = self.last_index_progress
            if self.index_state == "indexing" and total:
                self._on_indexing_progress(current, total, filename)
            else:
                self.status_label.setText(tr(self.language, f"state_{self.index_state}"))
                if self.index_state == "scanning":
                    self.progress_text_label.setText(tr(self.language, "progress_scanning"))
        elif not self.search_worker:
            self.status_label.setText(self._idle_status_text())

    def _on_language_changed(self, button):
        language = button.property("lang_code")
        if language and language != self.language:
            self.language = language
            self.config.language = language
            self._apply_language()

    def _toggle_settings(self, visible: bool):
        self.settings_panel.setVisible(visible)
        self.btn_settings.setText(
            tr(self.language, "hide_settings") if visible else tr(self.language, "settings")
        )

    ADVANCED_DIALOG_HEIGHT = 296

    def _toggle_advanced_filters(self, visible: bool):
        if visible:
            screen = self.screen().availableGeometry()
            width = min(900, screen.width() - 24)
            self.advanced_dialog.resize(width, self.ADVANCED_DIALOG_HEIGHT)
            position = self.btn_advanced_filters.mapToGlobal(
                self.btn_advanced_filters.rect().bottomLeft()
            )
            x = max(screen.left() + 8, min(position.x(), screen.right() - width - 8))
            y = max(
                screen.top() + 8,
                min(position.y() + 4, screen.bottom() - self.ADVANCED_DIALOG_HEIGHT - 8),
            )
            self.advanced_dialog.move(x, y)
            self.advanced_dialog.show()
            self.advanced_dialog.raise_()
        else:
            self.advanced_dialog.hide()
        self.btn_advanced_filters.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    def _toggle_theme_mode(self):
        modes = ["auto", "dark", "light"]
        curr_idx = modes.index(self.theme_mode) if self.theme_mode in modes else 0
        self.theme_mode = modes[(curr_idx + 1) % len(modes)]
        self.config.data["theme_mode"] = self.theme_mode
        self.config.save()

        self.theme = get_active_theme(self.theme_mode)
        self.apply_theme(self.theme)

    def _on_system_color_scheme_changed(self):
        """Called automatically when macOS switches Light/Dark mode."""
        if self.theme_mode == "auto":
            self.theme = get_active_theme("auto")
            self.apply_theme(self.theme)

    def _refresh_dir_label(self):
        dirs = self.config.directories
        theme = self.theme if hasattr(self, "theme") else get_active_theme()
        if not dirs:
            self.dir_label.setText(tr(self.language, "directory_empty"))
            self.dir_label.setStyleSheet(f"color: {theme.error}; font-style: italic;")
        else:
            names = [os.path.basename(p) or p for p in dirs]
            self.dir_label.setText(
                tr(self.language, "directory_count", names=" | ".join(names), count=len(dirs))
            )
            self.dir_label.setStyleSheet(f"color: {theme.text_primary}; font-weight: 500;")

    def _open_folder_dialog(
        self, title: str, on_selected: Callable[[List[str]], None], start_directory: str = ""
    ):
        """Open a native, window-modal picker without a nested Qt event loop."""
        if self._folder_dialog is not None:
            return
        if self.btn_advanced_filters.isChecked():
            self.btn_advanced_filters.setChecked(False)

        dialog = QFileDialog(self)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModal)
        if start_directory:
            dialog.setDirectory(start_directory)
        self._folder_dialog = dialog

        def finish(result: int):
            selected = dialog.selectedFiles() if result == QDialog.Accepted else []
            self._folder_dialog = None
            dialog.deleteLater()

            def apply_selection():
                if not selected:
                    self._restore_focus_after_folder_dialog()
                    return
                valid = []
                for path in selected:
                    try:
                        with os.scandir(path):
                            pass
                        valid.append(path)
                    except (OSError, PermissionError):
                        pass
                if len(valid) != len(selected):
                    QMessageBox.warning(
                        self, tr(self.language, "notice"),
                        tr(self.language, "directory_unavailable"),
                    )
                if valid:
                    on_selected(valid)
                self._restore_focus_after_folder_dialog()

            # Let the native sheet close before another modal warning or index work begins.
            QTimer.singleShot(0, apply_selection)

        dialog.finished.connect(finish)
        dialog.open()

    def _restore_focus_after_folder_dialog(self):
        if self.isVisible() and self._folder_dialog is None:
            self.raise_()
            self.activateWindow()

    def _on_choose_directory(self):
        self._open_folder_dialog(
            tr(self.language, "choose_folder_title"), self._add_directories
        )

    def _add_directories(self, folders: List[str]):
        changed = False
        for folder in folders:
            changed = self.config.add_directory(folder) or changed
        if changed:
            self._refresh_dir_label()
            self._invalidate_directory_results()
            self._mark_last_updated()
            self._start_indexing()

    def _invalidate_directory_results(self):
        self.table.set_results([])
        self.preview.display_result(None)
        self.results_count_label.setText(
            tr(self.language, "search_waiting") if self.search_input.text().strip()
            else tr(self.language, "ready_to_search")
        )

    def _choose_scope_folder(self):
        starting_directory = self.config.directories[0] if self.config.directories else ""
        self._open_folder_dialog(
            tr(self.language, "choose_scope"),
            lambda folders: self._set_scope_folder(folders[0]), starting_directory
        )

    def _set_scope_folder(self, folder: str):
        if not any(
            FileScanner.is_path_within_directory(folder, root)
            for root in self.config.directories
        ):
            QMessageBox.warning(
                self, tr(self.language, "notice"), tr(self.language, "scope_outside")
            )
            return
        existing = [path.strip() for path in self.include_path_input.text().split(";") if path.strip()]
        if folder not in existing:
            existing.append(folder)
            self.include_path_input.setText("; ".join(existing))

    def _on_reset_directories(self):
        self._open_folder_dialog(
            tr(self.language, "reset_folder_title"),
            lambda folders: self._reset_directories(folders[0])
        )

    def _reset_directories(self, folder: str):
        if self.config.directories == [os.path.abspath(folder)]:
            return
        self.config.directories = [folder]
        self._refresh_dir_label()
        self._invalidate_directory_results()
        self._mark_last_updated()
        self._start_indexing()

    def _clear_directories(self):
        if not self.config.directories and not self.db.get_all_indexed_paths():
            return
        self.config.directories = []
        self._refresh_dir_label()
        self._mark_last_updated()
        self._invalidate_directory_results()
        if self.search_worker and self.search_worker.isRunning():
            self.pending_search = (
                self.search_input.text().strip(), self.active_type_filter,
                self._current_search_filters(),
            )
        self._start_indexing(clear_index=True)

    def _on_include_subdirectories_toggled(self, enabled: bool):
        self.config.include_subdirectories = enabled
        if self.config.directories:
            self._invalidate_directory_results()
            self._start_indexing()

    def _on_manual_refresh(self):
        if self.config.directories:
            self._mark_last_updated()
        self._start_indexing()

    def _start_indexing(self, clear_index: bool = False):
        if not self.config.directories and not clear_index:
            QMessageBox.information(
                self, tr(self.language, "notice"), tr(self.language, "choose_folder_first")
            )
            return

        if self.index_worker and self.index_worker.isRunning():
            self.pending_index_start = True
            return
        if self.search_worker and self.search_worker.isRunning():
            self.pending_index_start = True
            self.status_label.setText(tr(self.language, "search_waiting"))
            return

        self.btn_reindex.setEnabled(False)
        self.btn_add_dir.setEnabled(False)
        self.btn_reset_dirs.setEnabled(False)
        self.btn_clear_dirs.setEnabled(False)
        self.chk_include_subdirectories.setEnabled(False)
        self.btn_pause_index.setEnabled(True)
        self.btn_pause_index.setText(tr(self.language, "pause_index"))
        self.btn_stop_index.setEnabled(True)
        self.btn_stop_index.setText(tr(self.language, "stop_index"))
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_text_label.setVisible(True)
        self.progress_text_label.setText(tr(self.language, "progress_scanning"))
        self._set_index_state("scanning")

        self.index_worker = IndexWorker(
            self.db,
            self.config.directories,
            self.config.include_subdirectories,
            self.config.exclude_patterns,
            clear_index=clear_index,
        )
        self.index_worker.progress.connect(self._on_indexing_progress)
        self.index_worker.state_changed.connect(self._set_index_state)
        self.index_worker.indexing_finished.connect(self._on_indexing_finished)
        self.index_worker.finished.connect(self._after_index_worker_finished)
        self.index_worker.start()

    def _toggle_indexing_pause(self):
        if not self.index_worker or not self.index_worker.isRunning():
            return

        if self.index_worker.is_paused:
            self.index_worker.resume()
            self.btn_pause_index.setText(tr(self.language, "pause_index"))
        else:
            self.index_worker.pause()
            self.btn_pause_index.setText(tr(self.language, "resume_index"))

    def _stop_indexing(self):
        if not self.index_worker or not self.index_worker.isRunning():
            return

        self.index_worker.cancel()
        self.btn_pause_index.setEnabled(False)
        self.btn_stop_index.setEnabled(False)
        self.btn_stop_index.setText(tr(self.language, "stopping_index"))
        self._set_index_state("stopping")

    def _on_indexing_progress(self, current: int, total: int, filename: str):
        self.last_index_progress = (current, total, filename)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        percent = round((current / total) * 100) if total else 0
        message = f"{current:,} / {total:,} · {percent}%"
        self.progress_text_label.setText(message)

    def _set_index_state(self, state: str):
        self.index_state = state
        self.index_state_label.setText(tr(self.language, f"state_{state}"))
        if state == "scanning":
            self.progress_text_label.setText(tr(self.language, "progress_scanning"))
        if state in ("scanning", "indexing", "paused", "stopping"):
            self.status_label.setText(tr(self.language, f"state_{state}"))

    def _on_indexing_finished(self, stats: dict):
        self.progress_bar.setVisible(False)
        self.progress_text_label.setVisible(False)
        self.btn_reindex.setEnabled(True)
        self.btn_add_dir.setEnabled(True)
        self.btn_reset_dirs.setEnabled(True)
        self.btn_clear_dirs.setEnabled(True)
        self.chk_include_subdirectories.setEnabled(True)
        self.btn_pause_index.setEnabled(False)
        self.btn_pause_index.setText(tr(self.language, "pause_index"))
        self.btn_stop_index.setEnabled(False)
        self.btn_stop_index.setText(tr(self.language, "stop_index"))
        if stats.get("cancelled"):
            self._set_index_state("idle")
            docs = self.db.get_stats().get("total_docs", 0)
            self.status_label.setText(tr(self.language, "index_stopped", docs=docs))
        elif stats.get("error"):
            self._set_index_state("error")
            self.status_label.setText(
                tr(self.language, "index_failed", message=stats["error"])
            )
        else:
            if not stats.get("skipped"):
                self._mark_last_updated()
            self._set_index_state("completed")
            docs = self.db.get_stats().get("total_docs", 0)
            unavailable_count = len(stats.get("unavailable_directories", []))
            scan_error_count = len(stats.get("scan_error_paths", []))
            if stats.get("skipped"):
                self.status_label.setText(tr(self.language, "index_unavailable", docs=docs))
            elif unavailable_count or scan_error_count:
                self.status_label.setText(
                    tr(self.language, "index_partial", indexed=stats.get("indexed", 0),
                       deleted=stats.get("deleted", 0),
                       skipped=unavailable_count + scan_error_count, docs=docs)
                )
            elif not any(stats.get(key, 0) for key in ("indexed", "deleted", "failed")):
                self.status_label.setText(self._idle_status_text())
            else:
                self.status_label.setText(
                    tr(self.language, "index_complete", indexed=stats.get("indexed", 0),
                       deleted=stats.get("deleted", 0), failed=stats.get("failed", 0), docs=docs)
                )

        self._update_db_status(update_main_status=False)
        if self.search_input.text().strip():
            self._trigger_search()

    def _after_index_worker_finished(self):
        """Resume queued index/search work after the index thread actually exits."""
        if self.pending_index_start:
            self.pending_index_start = False
            self._start_indexing(clear_index=not self.config.directories)
            return
        if self.pending_search and self.pending_search[0]:
            self._trigger_search()

    def _idle_status_text(self, stats: Optional[dict] = None) -> str:
        """Compose the lower-left status text: version and indexed count.

        Replaces the old "index up to date | N documents" message, which duplicated
        the top bar's "N documents indexed" summary.
        """
        stats = stats or self.db.get_stats()
        docs = stats.get("total_docs", 0)
        if not self._last_updated_at(stats):
            return tr(self.language, "status_bar_never", version=APP_VERSION)
        return tr(
            self.language, "status_bar_format",
            version=APP_VERSION, docs=docs,
        )

    def _last_updated_at(self, stats: Optional[dict] = None) -> Optional[float]:
        value = self.config.data.get("last_updated_at") or (stats or self.db.get_stats()).get("last_indexed_at")
        try:
            return float(value) if value else None
        except (TypeError, ValueError):
            return None

    def _refresh_last_updated_label(self, stats: Optional[dict] = None):
        stamp = self._last_updated_at(stats)
        if stamp:
            updated = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
            self.last_updated_label.setText(tr(self.language, "last_updated", updated=updated))
        else:
            self.last_updated_label.setText(tr(self.language, "last_updated_never"))
        font = self.last_updated_label.fontMetrics()
        self.last_updated_label.setMinimumWidth(max(
            font.horizontalAdvance("最後更新：0000-00-00 00:00"),
            font.horizontalAdvance("Last Updated: 0000-00-00 00:00"),
        ))

    def _mark_last_updated(self):
        self.config.data["last_updated_at"] = time.time()
        self.config.save()
        self._refresh_last_updated_label()

    def _update_db_status(self, update_main_status: bool = True):
        stats = self.db.get_stats()
        docs = stats.get("total_docs", 0)
        self.index_summary_label.setText(tr(self.language, "indexed_summary", count=docs))
        self._refresh_last_updated_label(stats)
        if update_main_status:
            self.status_label.setText(self._idle_status_text(stats))

    def _on_search_text_changed(self, text: str):
        self.btn_clear_search.setVisible(bool(text))
        self.search_timer.start()

    def _clear_search(self):
        self.search_input.clear()
        self.search_input.setFocus()

    def _on_metadata_filter_changed(self, *_args):
        is_custom = self.date_filter_combo.currentData() == "custom"
        for widget in (self.date_from_label, self.date_from, self.date_to_label, self.date_to):
            widget.setVisible(is_custom)
        size_custom = self.size_preset_combo.currentData() == "custom"
        for widget in (self.min_size, self.min_unit, self.max_size, self.max_unit):
            widget.setVisible(size_custom)
        if self._restoring_filters:
            return
        self._persist_filter_settings()
        self._refresh_filter_badges()
        if self.search_input.text().strip():
            self.search_timer.start()

    def _on_exclude_changed(self):
        if self._restoring_filters:
            return
        self.config.exclude_patterns = [
            token.strip() for token in self.exclude_input.text().split(",") if token.strip()
        ]
        self._on_metadata_filter_changed()
        if self.config.directories:
            self.exclude_reindex_timer.start()

    def _persist_filter_settings(self):
        self.config.search_filters = {
            "file_type": self.active_type_filter,
            "date_field": self.date_field_combo.currentData(),
            "date_mode": self.date_filter_combo.currentData(),
            "date_from": self.date_from.date().toString(Qt.ISODate),
            "date_to": self.date_to.date().toString(Qt.ISODate),
            "size_mode": self.size_preset_combo.currentData(),
            "min_size": self.min_size.value(),
            "min_unit": self.min_unit.currentData(),
            "max_size": self.max_size.value(),
            "max_unit": self.max_unit.currentData(),
            "include_paths": self.include_path_input.text().strip(),
            "match_case": self.match_case.isChecked(),
            "whole_word": self.whole_word.isChecked(),
            "regex": self.regex_mode.isChecked(),
        }

    def _restore_filter_settings(self):
        self._restoring_filters = True
        filters = self.config.search_filters
        stored_type = filters.get("file_type", "all")
        for button in self.filter_buttons:
            if button.property("filter_tag") == stored_type:
                button.setChecked(True)
                self.active_type_filter = stored_type
                break
        for combo, value in (
            (self.date_field_combo, filters["date_field"]),
            (self.date_filter_combo, filters["date_mode"]),
            (self.size_preset_combo, filters["size_mode"]),
            (self.min_unit, filters["min_unit"]),
            (self.max_unit, filters["max_unit"]),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(max(0, index))
        for widget, key in ((self.date_from, "date_from"), (self.date_to, "date_to")):
            date = QDate.fromString(filters[key], Qt.ISODate)
            if date.isValid():
                widget.setDate(date)
        self.min_size.setValue(float(filters["min_size"]))
        self.max_size.setValue(float(filters["max_size"]))
        self.include_path_input.setText(str(filters["include_paths"]))
        self.exclude_input.setText(", ".join(self.config.exclude_patterns))
        self.match_case.setChecked(bool(filters["match_case"]))
        self.whole_word.setChecked(bool(filters["whole_word"]))
        self.regex_mode.setChecked(bool(filters["regex"]))
        self._restoring_filters = False
        self._on_metadata_filter_changed()

    def _refresh_filter_badges(self):
        while self.active_filter_row.count() > 1:
            item = self.active_filter_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        badges = []
        if self.active_type_filter != "all":
            active_button = next(
                (button for button in self.filter_buttons
                 if button.property("filter_tag") == self.active_type_filter), None
            )
            if active_button:
                badges.append(("format", tr(self.language, "badge_format", value=active_button.text())))
        date_mode = self.date_filter_combo.currentData()
        if date_mode != "all":
            badges.append(("date", tr(self.language, "badge_date", value=self.date_filter_combo.currentText())))
        size_mode = self.size_preset_combo.currentData()
        if size_mode != "any":
            badges.append(("size", tr(self.language, "badge_size", value=self.size_preset_combo.currentText())))
        if self.include_path_input.text().strip():
            badges.append(("path", tr(self.language, "badge_path", value=self.include_path_input.text().strip())))
        if self.exclude_input.text().strip():
            badges.append(("exclude", tr(self.language, "badge_exclude", value=self.exclude_input.text().strip())))
        for key, control in (("case", self.match_case), ("word", self.whole_word), ("regex", self.regex_mode)):
            if control.isChecked():
                badges.append((key, control.text() + " ✕"))
        for key, label in badges:
            badge = QPushButton()
            badge.setText(badge.fontMetrics().elidedText(label, Qt.ElideRight, 230))
            badge.setToolTip(label)
            badge.setStyleSheet(
                f"background: {self.theme.bg_subtle}; color: {self.theme.text_secondary}; "
                f"border: 1px solid {self.theme.border}; border-radius: 10px; padding: 3px 8px;"
            )
            badge.clicked.connect(lambda _=False, target=key: self._clear_filter(target))
            self.active_filter_row.insertWidget(self.active_filter_row.count() - 1, badge)
        self.btn_reset_filters.setVisible(bool(badges) or self.active_type_filter != "all")

    def _clear_filter(self, target: str):
        if target == "format":
            self.filter_buttons[0].setChecked(True)
            self.active_type_filter = "all"
            self._on_metadata_filter_changed()
        elif target == "date":
            self.date_filter_combo.setCurrentIndex(self.date_filter_combo.findData("all"))
        elif target == "size":
            self.size_preset_combo.setCurrentIndex(self.size_preset_combo.findData("any"))
        elif target == "path":
            self.include_path_input.clear()
        elif target == "exclude":
            self.exclude_input.clear()
        elif target == "case":
            self.match_case.setChecked(False)
        elif target == "word":
            self.whole_word.setChecked(False)
        elif target == "regex":
            self.regex_mode.setChecked(False)

    def _reset_all_filters(self):
        had_exclusions = bool(self.config.exclude_patterns)
        self._restoring_filters = True
        self.date_field_combo.setCurrentIndex(0)
        self.date_filter_combo.setCurrentIndex(0)
        self.size_preset_combo.setCurrentIndex(0)
        self.min_size.setValue(0)
        self.max_size.setValue(0)
        self.min_unit.setCurrentIndex(1)
        self.max_unit.setCurrentIndex(1)
        self.include_path_input.clear()
        self.exclude_input.clear()
        for checkbox in (self.match_case, self.whole_word, self.regex_mode):
            checkbox.setChecked(False)
        self.filter_buttons[0].setChecked(True)
        self.active_type_filter = "all"
        self._restoring_filters = False
        self.config.exclude_patterns = []
        self._on_metadata_filter_changed()
        if had_exclusions and self.config.directories:
            self.exclude_reindex_timer.start()

    def _current_search_filters(self) -> Dict[str, Any]:
        """Convert UI metadata choices into worker-safe numeric boundaries."""
        values: Dict[str, Any] = {
            "date_field": self.date_field_combo.currentData(),
            "include_paths": [
                path.strip() for path in self.include_path_input.text().split(";") if path.strip()
            ],
            "exclude_patterns": self.config.exclude_patterns,
            "search_roots": self.config.directories,
            "match_case": self.match_case.isChecked(),
            "whole_word": self.whole_word.isChecked(),
            "regex": self.regex_mode.isChecked(),
        }
        now = time.time()
        date_mode = self.date_filter_combo.currentData()
        days_by_mode = {"day": 1, "week": 7, "month": 30, "year": 365}
        if date_mode in days_by_mode:
            values["modified_after"] = now - days_by_mode[date_mode] * 86400
        elif date_mode == "custom":
            start = self.date_from.date()
            end = self.date_to.date().addDays(1)
            values["modified_after"] = datetime(
                start.year(), start.month(), start.day()
            ).timestamp()
            values["modified_before"] = datetime(
                end.year(), end.month(), end.day()
            ).timestamp()

        mb = 1024 * 1024
        size_mode = self.size_preset_combo.currentData()
        size_bounds = {
            "small": (None, mb - 1),
            "medium": (mb, 10 * mb - 1),
            "large": (10 * mb, 100 * mb),
            "huge": (100 * mb + 1, None),
        }
        if size_mode in size_bounds:
            minimum, maximum = size_bounds[size_mode]
            if minimum is not None:
                values["min_size"] = minimum
            if maximum is not None:
                values["max_size"] = maximum
        elif size_mode == "custom":
            units = {"KB": 1024, "MB": mb, "GB": mb * 1024}
            if self.min_size.value() > 0:
                values["min_size"] = round(self.min_size.value() * units[self.min_unit.currentData()])
            if self.max_size.value() > 0:
                values["max_size"] = round(self.max_size.value() * units[self.max_unit.currentData()])
        return values

    def _current_filter_signature(self) -> Tuple[Any, ...]:
        """Return stable UI values for rejecting results from superseded searches."""
        return (
            self.date_filter_combo.currentData(),
            self.date_field_combo.currentData(),
            self.date_from.date().toJulianDay(),
            self.date_to.date().toJulianDay(),
            self.size_preset_combo.currentData(),
            self.min_size.value(),
            self.max_size.value(),
            self.min_unit.currentData(),
            self.max_unit.currentData(),
            self.include_path_input.text(),
            self.exclude_input.text(),
            self.match_case.isChecked(),
            self.whole_word.isChecked(),
            self.regex_mode.isChecked(),
            tuple(self.config.directories),
            self.config.include_subdirectories,
        )

    def _insert_query_syntax(self, insertion: str):
        """Insert a boolean operator or quote pair at the current cursor."""
        cursor = self.search_input.cursorPosition()
        if insertion == '""':
            self.search_input.insert(insertion)
            self.search_input.setCursorPosition(cursor + 1)
        else:
            current = self.search_input.text()
            operator = insertion.strip()
            prefix = " " if cursor > 0 and not current[cursor - 1].isspace() else ""
            suffix = " " if cursor == len(current) or not current[cursor].isspace() else ""
            self.search_input.insert(f"{prefix}{operator}{suffix}")
        self.search_input.setFocus()

    def _show_about_dialog(self):
        about_box = QMessageBox(self)
        about_box.setWindowTitle(tr(self.language, "about_title"))
        about_box.setIcon(QMessageBox.Information)
        about_box.setTextFormat(Qt.RichText)
        lines = [
            f"<h3>{tr(self.language, 'about_current_version', version=APP_VERSION)}</h3>",
            f"<h4>{tr(self.language, 'about_changelog_heading')}</h4>",
        ]
        for version, date, highlight_keys in CHANGELOG:
            lines.append(f"<p><b>v{version}</b> — {date}</p><ul>")
            lines.extend(
                f"<li>{tr(self.language, f'changelog_{key}')}</li>" for key in highlight_keys
            )
            lines.append("</ul>")
        about_box.setText("".join(lines))
        about_box.exec()

    def _show_search_help(self):
        help_box = QDialog(self)
        help_box.setWindowTitle(tr(self.language, "help_title"))
        if self.language == "zh-TW":
            help_html = r"""
            <h3>搜尋語法</h3>
            <ul>
              <li><b>多個關鍵字：</b><code>年度 預算</code>（預設為 AND）</li>
              <li><b>任一關鍵字：</b><code>預算 OR 決算</code></li>
              <li><b>排除關鍵字：</b><code>預算 NOT 草案</code></li>
              <li><b>精確片語：</b><code>&quot;保密協定條款&quot;</code></li>
              <li><b>搜尋檔名：</b><code>filename:年度報告</code> 或
                  <code>檔名:&quot;會議記錄&quot;</code></li>
            </ul>
            <h3>輔助操作</h3>
            <ul>
              <li>使用格式篩選，可只查看 PDF、Word、Excel、PPT 或文字檔。</li>
              <li><b>Enter</b>：立即搜尋；<b>Cmd/Ctrl+F</b>：聚焦搜尋框；
                  <b>Esc</b>：清除搜尋。</li>
              <li>可依修改／建立日期、大小與子資料夾路徑縮小範圍；排除規則會重新掃描索引。</li>
              <li>區分大小寫、完整單字及正規表示式可獨立切換；正規表示式模式使用 Python 語法。</li>
              <li>點擊表頭排序；篩選徽章可單獨移除，或一鍵重設所有篩選。</li>
              <li>為避免資料庫競爭，索引期間輸入的搜尋會在索引結束後執行。</li>
            </ul>
            <h3>正規表示式模式（Python Regex 語法）</h3>
            <p>支援 Python <code>re</code> 語法；預設不區分大小寫。啟用「正規表示式」後可使用：</p>
            <ul>
              <li><b>文字與選項：</b><code>report</code> 搜尋文字；
                  <code>cat|dog|bird</code> 匹配任一詞；
                  <code>(?i)error</code> 忽略大小寫。</li>
              <li><b>數字與格式：</b><code>\d+</code> 匹配連續數字，
                  <code>\d{4}</code> 匹配四位數（如 2026）；
                  <code>\d{4}-\d{2}-\d{2}</code> 匹配日期；
                  <code>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}</code> 匹配 IPv4 形式。</li>
              <li><b>邊界與位置：</b><code>^IMPORT</code> 匹配索引文字段落開頭；
                  <code>END$</code> 匹配段落結尾；
                  <code>\btest\b</code> 匹配完整單字。</li>
              <li><b>靈活組合：</b><code>LOG.*ERROR</code> 匹配同一行內的前後文字；
                  <code>\b\w+\.(pdf|docx|txt)\b</code> 匹配副檔名引用。</li>
            </ul>
            """
        else:
            help_html = r"""
            <h3>Query syntax</h3>
            <ul>
              <li><b>All words:</b> <code>annual budget</code> (implicit AND)</li>
              <li><b>Either word:</b> <code>budget OR forecast</code></li>
              <li><b>Exclude:</b> <code>budget NOT draft</code></li>
              <li><b>Exact phrase:</b> <code>&quot;confidentiality clause&quot;</code></li>
              <li><b>File name:</b> <code>filename:annual report</code></li>
            </ul>
            <h3>Tips</h3>
            <ul>
              <li>Filter by format, modified or created date, size, and subfolder path. Exclusion changes rescan the index.</li>
              <li>Match case, whole word, and Python regular expression modes can be toggled independently.</li>
              <li>Click a column header to sort. Remove individual filter badges or reset all filters.</li>
              <li><b>Enter</b> searches; <b>Cmd/Ctrl+F</b> focuses search; <b>Esc</b> clears it.</li>
              <li>Searches entered during indexing run automatically when indexing finishes.</li>
            </ul>
            <h3>Python regular expression examples</h3>
            <p>Enable Regex mode to use Python <code>re</code> syntax. Matching is case insensitive by default:</p>
            <ul>
              <li><b>Text:</b> <code>report</code>; alternatives <code>cat|dog|bird</code>;
                  ignore case <code>(?i)error</code>.</li>
              <li><b>Numbers and formats:</b> <code>\d+</code>, <code>\d{4}</code>,
                  date <code>\d{4}-\d{2}-\d{2}</code>, IPv4 form
                  <code>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}</code>.</li>
              <li><b>Boundaries:</b> <code>^IMPORT</code> at the start of an indexed text segment,
                  <code>END$</code> at its end, <code>\btest\b</code> as a whole word.</li>
              <li><b>Combinations:</b> <code>LOG.*ERROR</code> on one line;
                  <code>\b\w+\.(pdf|docx|txt)\b</code> for file extension references.</li>
            </ul>
            """
        layout = QVBoxLayout(help_box)
        layout.setContentsMargins(20, 16, 20, 16)
        content = QTextBrowser(help_box)
        content.setHtml(help_html)
        content.setOpenExternalLinks(False)
        content.setLineWrapMode(QTextBrowser.WidgetWidth)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=help_box)
        buttons.accepted.connect(help_box.accept)
        layout.addWidget(buttons)

        screen = help_box.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        margins = layout.contentsMargins()
        minimum_width = buttons.sizeHint().width() + margins.left() + margins.right()
        outer_width = min(int(available.width() * 0.9),
                          max(minimum_width, int(self.width() * 0.9)))
        measure = QTextDocument()
        measure.setDefaultFont(content.font())
        measure.setHtml(content.toHtml())
        measure.setTextWidth(-1)
        browser_chrome = content.frameWidth() * 2 + measure.documentMargin() * 2
        natural_width = math.ceil(measure.idealWidth() + browser_chrome)
        dialog_width = min(outer_width, max(minimum_width,
                                            natural_width + margins.left() + margins.right()))
        content_width = dialog_width - margins.left() - margins.right() - content.frameWidth() * 2
        measure.setTextWidth(content_width)
        controls_height = (margins.top() + margins.bottom() + buttons.sizeHint().height()
                           + layout.spacing())
        minimum_height = controls_height + content.fontMetrics().lineSpacing() * 3
        outer_height = min(int(available.height() * 0.8),
                           max(minimum_height, int(self.height() * 0.8)))
        dialog_height = min(outer_height,
                            math.ceil(measure.size().height()
                                      + content.frameWidth() * 2 + controls_height))
        help_box.resize(dialog_width, dialog_height)
        help_box.exec()

    def _on_filter_changed(self, button):
        self.active_type_filter = button.property("filter_tag")
        self._persist_filter_settings()
        self._refresh_filter_badges()
        self._trigger_search()

    def _trigger_search(self):
        query = self.search_input.text().strip()
        if self.date_filter_combo.currentData() == "custom" and self.date_from.date() > self.date_to.date():
            self.results_count_label.setText(tr(self.language, "invalid_date_range"))
            return
        if self.regex_mode.isChecked() and query:
            try:
                re.compile(query)
            except re.error as exc:
                self.search_input.setToolTip(f"Regex: {exc}")
                self.results_count_label.setText(f"Regex: {exc}")
                return
        self.search_input.setToolTip("")
        search_filters = self._current_search_filters()
        if search_filters.get("min_size", 0) > search_filters.get("max_size", float("inf")):
            self.results_count_label.setText(tr(self.language, "invalid_size_range"))
            return
        if not query:
            self.table.set_results([])
            self.preview.display_result(None)
            self.results_count_label.setText(tr(self.language, "ready_to_search"))
            if self.search_worker and self.search_worker.isRunning():
                self.pending_search = ("", self.active_type_filter, search_filters)
            return

        if self.index_worker and self.index_worker.isRunning():
            self.pending_search = (query, self.active_type_filter, search_filters)
            self.results_count_label.setText(tr(self.language, "search_waiting"))
            return

        if self.search_worker and self.search_worker.isRunning():
            self.pending_search = (query, self.active_type_filter, search_filters)
            self.results_count_label.setText(tr(self.language, "search_waiting"))
            return

        self._start_search(query, self.active_type_filter, search_filters)

    def _start_search(self, query: str, type_filter: str, search_filters: Dict[str, Any]):
        self.pending_search = None
        self.results_count_label.setText(tr(self.language, "searching"))
        worker = SearchWorker(self.db, query, type_filter, search_filters)
        worker.filter_signature = self._current_filter_signature()
        self.search_worker = worker
        worker.search_finished.connect(
            lambda results, completed_query, elapsed_ms, source=worker:
                self._on_search_finished(
                    source, results, completed_query, elapsed_ms
                )
        )
        worker.search_failed.connect(
            lambda message, source=worker: self._on_search_failed(source, message)
        )
        worker.finished.connect(
            lambda source=worker: self._on_search_worker_finished(source)
        )
        worker.start()

    def _on_search_finished(
        self,
        worker: SearchWorker,
        results: List[SearchResultItem],
        query: str,
        elapsed_ms: float,
    ):
        if (
            worker is not self.search_worker
            or self.pending_search is not None
            or query != self.search_input.text().strip()
            or worker.type_filter != self.active_type_filter
            or worker.filter_signature != self._current_filter_signature()
        ):
            return

        self.table.set_results(results)
        count = len(results)
        if count == 0:
            self.results_count_label.setText(
                tr(self.language, "search_none", elapsed=elapsed_ms)
            )
        else:
            self.results_count_label.setText(
                tr(self.language, "search_found", count=count, elapsed=elapsed_ms)
            )

    def _on_search_failed(self, worker: SearchWorker, message: str):
        if worker is self.search_worker and self.pending_search is None:
            error_keys = {
                "精確片語的雙引號未成對。": "error_unpaired_phrase",
                "AND、OR、NOT 前後都必須有搜尋詞。": "error_operator_position",
                "AND、OR、NOT 不可連續使用。": "error_repeated_operator",
                "請在 filename: 或 檔名: 後輸入檔名關鍵字。": "error_filename_empty",
                "檔名搜尋的雙引號未成對。": "error_filename_quotes",
            }
            if message in error_keys:
                message = tr(self.language, error_keys[message])
            self.results_count_label.setText(
                tr(self.language, "search_failed", message=message)
            )

    def _on_search_worker_finished(self, worker: SearchWorker):
        worker.deleteLater()
        if worker is not self.search_worker:
            return

        self.search_worker = None
        if self.pending_index_start:
            self.pending_index_start = False
            self._start_indexing(clear_index=not self.config.directories)
            return
        pending = self.pending_search
        self.pending_search = None
        if pending and pending[0]:
            query, type_filter, _search_filters = pending
            if (
                query == self.search_input.text().strip()
                and type_filter == self.active_type_filter
            ):
                self._start_search(query, type_filter, self._current_search_filters())

    def _on_table_item_selected(self, item: Optional[SearchResultItem]):
        self.preview.display_result(item)

    def closeEvent(self, event):
        self.advanced_dialog.close()
        self.pending_search = None
        if self.index_worker and self.index_worker.isRunning():
            self.index_worker.cancel()
            self.index_worker.wait(1000)
        if self.search_worker and self.search_worker.isRunning():
            self.search_worker.wait(5000)
        self.db.close()
        event.accept()
