# Purpose: Document preview and highlighted snippet display panel with full theme support.
# What the code does:
#   - Displays document metadata and rendered HTML snippets.
#   - Adapts snippet background, border, text color, and highlight marks to dark/light theme.
#   - Re-renders the active match with a distinct focus color and applies reliable CSS zoom.
#   - Shows an inline notice when opening a file or revealing its folder fails (missing file,
#     no handler), from its own buttons or from the results table via show_file_action_failure.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets, doc_searcher.desktop.theme.ThemeColors.

import os
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
from PySide6.QtCore import QTimer, Signal
from doc_searcher.search.searcher import SearchResultItem
from doc_searcher.platform.platform_helper import (
    open_file_with_default_app,
    reveal_in_file_manager,
    format_file_size,
    format_timestamp,
)
from doc_searcher.desktop.theme import ThemeColors, get_active_theme
from doc_searcher.desktop.i18n import tr


NOTICE_MILLISECONDS = 8000


class PreviewPanel(QWidget):
    reprocess_requested = Signal(str)
    """Panel displaying file details, matched snippets, and action buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_item: SearchResultItem = None
        self.language = "zh-TW"
        self.match_count = 0
        self.current_match = -1
        self._search_context = None
        self._context_workers = []
        self._context_generation = 0
        self._active_context = None
        self.zoom_steps = 0
        self.theme: ThemeColors = get_active_theme()
        self._init_ui()
        from PySide6.QtWidgets import QPushButton

        self.btn_context = QPushButton(tr(self.language, "more_context"))
        self.btn_context.setEnabled(False)
        self.layout().addWidget(self.btn_context)
        self.btn_context.clicked.connect(
            lambda: self._request_context(
                full_segment=True,
                text_offset=self._active_context.get("next_offset") or 0
                if self._active_context
                else 0,
            )
        )
        self.btn_reprocess = QPushButton(tr(self.language, "reprocess_selected"))
        self.layout().addWidget(self.btn_reprocess)
        self.btn_reprocess.setEnabled(False)
        self.btn_reprocess.clicked.connect(
            lambda: (
                self.reprocess_requested.emit(self.current_item.path) if self.current_item else None
            )
        )
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

        # Explains a failed open/reveal; hidden until something goes wrong.
        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        self.notice_label.setHidden(True)
        header_layout.addWidget(self.notice_label)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._hide_notice)

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
        if hasattr(self, "btn_context"):
            self.btn_context.setText(tr(language, "more_context"))
        if hasattr(self, "btn_reprocess"):
            self.btn_reprocess.setText(tr(language, "reprocess_selected"))
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
        self.notice_label.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {theme.error};"
        )
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
        if hasattr(self, "btn_reprocess"):
            self.btn_reprocess.setEnabled(False)
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

    def set_search_context(self, db, query, options, revision):
        self._search_context = (db, query, dict(options), revision)
        self._context_generation += 1
        self._active_context = None
        for worker in self._context_workers:
            worker.cancel()

    def shutdown(self):
        for worker in list(self._context_workers):
            worker.cancel()
            worker.wait()
        self._context_workers.clear()

    def _request_context(self, *, full_segment=False, text_offset=0):
        from doc_searcher.desktop.worker import ContextWorker

        if not self._search_context or not self.current_item or self.current_match < 0:
            return
        self._context_generation += 1
        generation = self._context_generation
        for worker in self._context_workers:
            worker.cancel()
        db, query, options, revision = self._search_context
        worker = ContextWorker(
            db,
            query,
            self.current_item.doc_id,
            revision,
            self.current_match,
            options,
            full_segment=full_segment,
            text_offset=text_offset,
        )
        self._context_workers.append(worker)

        def ready(context):
            if generation != self._context_generation or not self.current_item:
                return
            self._active_context = context
            self._render_preview()

        def failed(message):
            if generation == self._context_generation:
                self.notice_label.setText(tr(self.language, "locations_stale") + " " + message)
                self.notice_label.show()

        worker.ready.connect(ready)
        worker.failed.connect(failed)

        def finished():
            if worker in self._context_workers:
                self._context_workers.remove(worker)
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()

    def display_result(self, item: SearchResultItem, reset_match: bool = True):
        """Display a result, optionally retaining its current highlighted match."""
        if reset_match:
            self._hide_notice()
            self._context_generation += 1
            self._active_context = None
            for worker in self._context_workers:
                worker.cancel()
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
        quality = tr(self.language, "quality_" + item.parse_status)
        self.meta_label.setText(self.meta_label.text() + " | " + quality)
        if item.warnings:
            self.meta_label.setToolTip(str(item.warnings))

        self.btn_reprocess.setEnabled(True)
        self.btn_open.setEnabled(True)
        self.btn_reveal.setEnabled(True)
        self.btn_copy_path.setEnabled(True)

        # Occurrence count is independent of the number of initial snippets.
        self.match_count = item.total_matches
        if reset_match or not 0 <= self.current_match < self.match_count:
            self.current_match = 0 if self.match_count else -1
        self._render_preview()
        self._update_match_controls()
        if self._search_context and reset_match:
            self._request_context()

    def _render_preview(self):
        """Render snippets at the selected scale with one visibly active match."""
        if not self.current_item:
            return

        if self._search_context and self.match_count:
            context = self._active_context
            if context is not None:
                import html

                location = str(context.get("source") or context["segment_id"])
                font_px = 13 + self.zoom_steps * 2
                self.browser.setHtml(
                    f'<body style="font-size:{font_px}px; color:{self.theme.text_primary};">'
                    f"<b>{html.escape(location)}</b><p>{context['html']}</p></body>"
                )
                self.browser.setProperty("active_match_index", self.current_match)
                self.browser.setProperty("preview_font_px", font_px)
            else:
                self.browser.setHtml(tr(self.language, "loading_context"))
                self.browser.setProperty("active_match_index", -1)
            return
        if self.current_item.passages and not self.current_item.total_matches:
            import html

            label = tr(self.language, "semantic_passage")
            blocks = [f"<b>{html.escape(label)}</b>"]
            for passage in self.current_item.passages:
                location = (
                    f"{passage['segment_type']} {passage['segment_id']} ({passage['matched_by']})"
                )
                blocks.append(
                    f"<p><b>{html.escape(location)}</b></p><p>{html.escape(passage['text'])}</p>"
                )
            self.browser.setHtml("".join(blocks))
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
                is_active = match_index == self.current_match
                background = "#f97316" if is_active else c.mark_bg
                border = (
                    f"2px solid {'#ffffff' if c.is_dark else '#1d4ed8'}"
                    if is_active
                    else "1px solid transparent"
                )
                first_mark = True

                def style_mark(match, background=background, border=border):
                    # Only the snippet's first mark shows the active state.
                    nonlocal first_mark
                    active_style = (
                        f"background-color: {background}; border: {border};"
                        if first_mark
                        else f"background-color: {c.mark_bg}; border: 1px solid transparent;"
                    )
                    first_mark = False
                    return (
                        f'<mark style="{active_style} color: {c.mark_text}; '
                        f'font-weight: bold; padding: 1px 3px; border-radius: 3px;">'
                    )

                styled = re.sub(r"<mark(?:\s[^>]*)?>", style_mark, snip, flags=re.IGNORECASE)
                localized_snippets.append(f'<a name="match-{match_index}"></a>{styled}')
                match_index += 1

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
        if hasattr(self, "btn_context"):
            self.btn_context.setEnabled(bool(self._search_context and has_matches))
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
        if self._search_context:
            self._active_context = None
            self._request_context()
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
        if self.current_item and not open_file_with_default_app(self.current_item.path):
            self.show_file_action_failure("open", self.current_item)

    def _on_reveal_folder(self):
        if self.current_item and not reveal_in_file_manager(self.current_item.path):
            self.show_file_action_failure("reveal", self.current_item)

    def show_file_action_failure(self, action: str, item: SearchResultItem):
        """Explain why opening ("open") or revealing ("reveal") item's file did not work."""
        if not os.path.exists(item.path):
            key = "file_missing_notice"
        else:
            key = "file_open_failed_notice" if action == "open" else "file_reveal_failed_notice"
        self.notice_label.setText(tr(self.language, key, name=item.filename))
        self.notice_label.setHidden(False)
        self._notice_timer.start(NOTICE_MILLISECONDS)

    def _hide_notice(self):
        self._notice_timer.stop()
        self.notice_label.setHidden(True)
        self.notice_label.setText("")

    def _on_copy_path(self):
        if self.current_item:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_item.path)
            self.btn_copy_path.setText("✓")
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                1500, lambda: self.btn_copy_path.setText(tr(self.language, "copy_path"))
            )
