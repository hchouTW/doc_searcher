# Purpose: Document preview and highlighted snippet display panel with full theme support.
# What the code does:
#   - Displays document metadata and rendered HTML snippets.
#   - Adapts snippet background, border, text color, and highlight marks to dark/light theme.
#   - Re-renders the active match with a distinct focus color and applies reliable CSS zoom.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets, doc_searcher.desktop.theme.ThemeColors.

import re
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QPushButton,
    QFrame,
    QApplication,
)
from PySide6.QtCore import QTimer
from doc_searcher.search.searcher import SearchResultItem
from doc_searcher.platform.platform_helper import (
    open_file_with_default_app,
    reveal_in_file_manager,
    format_file_size,
    format_timestamp,
)
from doc_searcher.desktop.theme import ThemeColors, get_active_theme
from doc_searcher.desktop.i18n import tr


class PreviewPanel(QWidget):
    """Panel displaying file details, matched snippets, and action buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_item: SearchResultItem = None
        self.language = "zh-TW"
        self.match_count = 0
        self.current_match = -1
        self.zoom_steps = 0
        self.theme: ThemeColors = get_active_theme()
        self._init_ui()
        self.apply_theme(self.theme)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Header card
        self.header_card = QFrame()
        header_layout = QVBoxLayout(self.header_card)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(8)

        # File title
        self.title_label = QLabel("請選擇搜尋結果")
        self.title_label.setWordWrap(True)
        header_layout.addWidget(self.title_label)

        # Metadata info line
        self.meta_label = QLabel("尚未選擇文件")
        header_layout.addWidget(self.meta_label)

        # Path label
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        header_layout.addWidget(self.path_label)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_open = QPushButton("開啟原檔 ↗")
        self.btn_open.clicked.connect(self._on_open_file)
        btn_row.addWidget(self.btn_open)

        self.btn_reveal = QPushButton("開啟所在資料夾")
        self.btn_reveal.clicked.connect(self._on_reveal_folder)
        btn_row.addWidget(self.btn_reveal)

        self.btn_copy_path = QPushButton("複製完整路徑")
        self.btn_copy_path.clicked.connect(self._on_copy_path)
        btn_row.addWidget(self.btn_copy_path)

        btn_row.addStretch()
        header_layout.addLayout(btn_row)

        layout.addWidget(self.header_card)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.btn_prev_match = QPushButton("↑")
        self.btn_prev_match.clicked.connect(self._previous_match)
        self.match_counter_label = QLabel("0 / 0")
        self.btn_next_match = QPushButton("↓")
        self.btn_next_match.clicked.connect(self._next_match)
        self.btn_zoom_out = QPushButton("A−")
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        self.btn_zoom_in = QPushButton("A+")
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        nav_row.addWidget(self.btn_prev_match)
        nav_row.addWidget(self.match_counter_label)
        nav_row.addWidget(self.btn_next_match)
        nav_row.addStretch()
        nav_row.addWidget(self.btn_zoom_out)
        nav_row.addWidget(self.btn_zoom_in)
        layout.addLayout(nav_row)

        # Text snippet browser
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        layout.addWidget(self.browser, stretch=1)
        self.set_language(self.language)

    def set_language(self, language: str):
        """Apply translated labels and re-render current content."""
        self.language = language
        self.btn_open.setText(tr(language, "open_file"))
        self.btn_reveal.setText(tr(language, "reveal_file"))
        self.btn_copy_path.setText(tr(language, "copy_path"))
        self.btn_prev_match.setToolTip(tr(language, "previous_match"))
        self.btn_next_match.setToolTip(tr(language, "next_match"))
        if self.current_item:
            self.display_result(self.current_item, reset_match=False)
        else:
            self._set_empty_state()

    def apply_theme(self, theme: ThemeColors):
        """Update styling to match theme colors."""
        self.theme = theme

        self.header_card.setObjectName("previewHeader")
        # Header card style
        self.header_card.setStyleSheet(f"""
            QFrame#previewHeader {{
                background-color: {theme.bg_card};
                border: 1px solid {theme.border};
                border-radius: 8px;
            }}
        """)
        self.title_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {theme.text_primary};"
        )
        self.meta_label.setStyleSheet(f"font-size: 12px; color: {theme.text_muted};")
        self.path_label.setStyleSheet(f"""
            font-size: 11px;
            color: {theme.text_secondary};
            font-family: Menlo, Monaco, "Courier New", monospace;
            background-color: {theme.bg_subtle};
            padding: 4px 6px;
            border-radius: 4px;
        """)

        # Buttons style
        self.btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.accent};
                color: {theme.accent_text};
                font-weight: bold;
                border: none;
                border-radius: 5px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {theme.accent_hover};
            }}
            QPushButton:disabled {{
                background-color: {theme.border};
                color: {theme.text_muted};
            }}
        """)

        btn_secondary_style = f"""
            QPushButton {{
                background-color: {theme.btn_bg};
                color: {theme.btn_text};
                border: 1px solid {theme.btn_border};
                border-radius: 5px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {theme.btn_hover};
            }}
            QPushButton:disabled {{
                color: {theme.text_muted};
                border-color: {theme.border};
            }}
        """
        self.btn_reveal.setStyleSheet(btn_secondary_style)
        self.btn_copy_path.setStyleSheet(btn_secondary_style)
        self.btn_prev_match.setStyleSheet(btn_secondary_style)
        self.btn_next_match.setStyleSheet(btn_secondary_style)
        self.btn_zoom_out.setStyleSheet(btn_secondary_style)
        self.btn_zoom_in.setStyleSheet(btn_secondary_style)
        self.match_counter_label.setStyleSheet(f"color: {theme.text_secondary}; padding: 0 6px;")

        # Browser style
        self.browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {theme.bg_card};
                color: {theme.text_primary};
                border: 1px solid {theme.border};
                border-radius: 8px;
                font-size: 13px;
                line-height: 1.6;
                padding: 12px;
            }}
        """)

        # Re-render content
        if self.current_item:
            self.display_result(self.current_item, reset_match=False)
        else:
            self._set_empty_state()

    def _set_empty_state(self):
        self.current_item = None
        self.title_label.setText(tr(self.language, "preview_empty_title"))
        self.meta_label.setText(tr(self.language, "preview_empty_body"))
        self.path_label.setText("")
        self.btn_open.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        self.btn_copy_path.setEnabled(False)
        self.match_count = 0
        self.current_match = -1
        self._update_match_controls()

        c = self.theme
        self.browser.setHtml(f"""
            <div style="text-align: center; margin-top: 60px; color: {c.text_muted};">
                <p style="font-size: 15px; font-weight: bold;">🔍 {tr(self.language, "preview_empty_title")}</p>
                <p style="font-size: 12px;">{tr(self.language, "preview_empty_body")}</p>
            </div>
        """)

    def display_result(self, item: SearchResultItem, reset_match: bool = True):
        """Display a result, optionally retaining its current highlighted match."""
        self.current_item = item
        if not item:
            self._set_empty_state()
            return

        self.title_label.setText(item.filename)
        size_str = format_file_size(item.file_size)
        time_str = format_timestamp(item.mtime)
        self.meta_label.setText(
            tr(
                self.language,
                "preview_meta",
                type=item.file_type.upper(),
                size=size_str,
                modified=time_str,
                segments=len(item.segments),
            )
        )
        self.path_label.setText(item.path)

        self.btn_open.setEnabled(True)
        self.btn_reveal.setEnabled(True)
        self.btn_copy_path.setEnabled(True)

        self.match_count = sum(
            len(re.findall(r"<mark(?:\s[^>]*)?>", snippet, flags=re.IGNORECASE))
            for segment in item.segments
            for snippet in segment.snippets
        )
        if reset_match or not 0 <= self.current_match < self.match_count:
            self.current_match = 0 if self.match_count else -1
        self._render_preview()
        self._update_match_controls()

    def _render_preview(self):
        """Render snippets at the selected scale with one visibly active match."""
        if not self.current_item:
            return

        # Build rich HTML snippets using theme colors
        item = self.current_item
        c = self.theme
        font_px = 13 + (self.zoom_steps * 2)
        html_blocks = []
        match_index = 0
        for seg in item.segments:
            seg_type_label = {
                "page": tr(self.language, "segment_page", id=seg.segment_id),
                "sheet": tr(self.language, "segment_sheet", id=seg.segment_id),
                "slide": tr(self.language, "segment_slide", id=seg.segment_id),
                "section": tr(self.language, "segment_section", id=seg.segment_id),
                "檔名": tr(self.language, "segment_filename_hit"),
            }.get(
                seg.segment_type,
                tr(self.language, "segment_block", id=seg.segment_id),
            )

            localized_snippets = []
            for snip in seg.snippets:

                def add_anchor(match):
                    nonlocal match_index
                    is_active = match_index == self.current_match
                    background = "#f97316" if is_active else c.mark_bg
                    border = (
                        f"2px solid {'#ffffff' if c.is_dark else '#1d4ed8'}"
                        if is_active
                        else "1px solid transparent"
                    )
                    anchor = (
                        f'<a name="match-{match_index}"></a>'
                        f'<mark style="background-color: {background}; color: {c.mark_text}; '
                        f"font-weight: bold; padding: 1px 3px; border: {border}; "
                        f'border-radius: 3px;">'
                    )
                    match_index += 1
                    return anchor

                localized_snippets.append(
                    re.sub(r"<mark(?:\s[^>]*)?>", add_anchor, snip, flags=re.IGNORECASE)
                )

            snippets_html = "".join(
                f"""
                <div style="margin: 6px 0; padding: 8px 12px; background-color: {c.snippet_bg}; border-left: 3px solid {c.snippet_border}; border-radius: 4px; font-size: {font_px}px; line-height: 1.5; color: {c.text_primary};">
                    {snip}
                </div>
            """
                for snip in localized_snippets
            )

            block = f"""
                <div style="margin-bottom: 16px;">
                    <div style="font-size: {font_px}px; font-weight: bold; color: {c.text_secondary}; margin-bottom: 4px;">
                        {seg_type_label}
                    </div>
                    {snippets_html}
                </div>
            """
            html_blocks.append(block)

        full_html = f"""
            <html>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; font-size: {font_px}px; background-color: {c.bg_card}; color: {c.text_primary};">
                {"".join(html_blocks)}
            </body>
            </html>
        """
        self.browser.setHtml(full_html)
        self.browser.setProperty("preview_font_px", font_px)
        self.browser.setProperty("active_match_index", self.current_match)
        if self.current_match >= 0:
            anchor = f"match-{self.current_match}"
            self.browser.scrollToAnchor(anchor)
            QTimer.singleShot(0, lambda: self.browser.scrollToAnchor(anchor))

    def _update_match_controls(self):
        has_matches = self.match_count > 0
        self.btn_prev_match.setEnabled(has_matches)
        self.btn_next_match.setEnabled(has_matches)
        self.btn_zoom_out.setEnabled(self.zoom_steps > -3)
        self.btn_zoom_in.setEnabled(self.zoom_steps < 6)
        if has_matches:
            self.match_counter_label.setText(
                tr(
                    self.language,
                    "match_counter",
                    current=self.current_match + 1,
                    total=self.match_count,
                )
            )
        else:
            self.match_counter_label.setText(tr(self.language, "no_matches"))

    def _move_match(self, offset: int):
        if not self.match_count:
            return
        self.current_match = (self.current_match + offset) % self.match_count
        self._render_preview()
        self._update_match_controls()

    def _previous_match(self):
        self._move_match(-1)

    def _next_match(self):
        self._move_match(1)

    def _zoom_in(self):
        if self.zoom_steps < 6:
            self.zoom_steps += 1
            self._render_preview()
            self._update_match_controls()

    def _zoom_out(self):
        if self.zoom_steps > -3:
            self.zoom_steps -= 1
            self._render_preview()
            self._update_match_controls()

    def _on_open_file(self):
        if self.current_item:
            open_file_with_default_app(self.current_item.path)

    def _on_reveal_folder(self):
        if self.current_item:
            reveal_in_file_manager(self.current_item.path)

    def _on_copy_path(self):
        if self.current_item:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_item.path)
            self.btn_copy_path.setText("✓")
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                1500, lambda: self.btn_copy_path.setText(tr(self.language, "copy_path"))
            )
