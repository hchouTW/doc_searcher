# Purpose: "Version Info" dialog that sizes itself to its content and the available screen.
# What the code does:
#   - Shows a fixed heading, a changelog browser and an OK button; only the changelog scrolls.
#   - Chooses width from the changelog's natural width (bounded by a readable maximum and the
#     screen) and height from the wrapped content, capped at a fraction of the usable area.
#   - After first show, grows by any leftover scroll range (style padding the estimate missed).
#   - Wraps text to the dialog width (no horizontal scrolling), stays resizable, and re-wraps live.
#   - Centers over its parent window and shifts back inside the screen's available geometry.
# Usage notes, dependencies, or assumptions:
#   - PySide6.QtWidgets; relies on the application stylesheet (theme.py) for light/dark colors.
#   - Construct with already-localized strings, then call exec().

import math

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

SCREEN_FRACTION = 0.9  # share of the usable screen area the dialog may occupy
MAX_READABLE_WIDTH = 720  # beyond this, lines get too long to read comfortably
MIN_WIDTH = 320


def fit_size(available: QRect, natural_width: int, content_height_at, chrome_height: int) -> QSize:
    """Pick a dialog size: natural width within [MIN_WIDTH, readable/screen cap], then height.

    ``content_height_at(width)`` returns the wrapped content height for a text width; the
    result is capped so the dialog always fits the usable screen area.
    """
    max_width = int(available.width() * SCREEN_FRACTION)
    max_height = int(available.height() * SCREEN_FRACTION)
    width = max(min(MIN_WIDTH, max_width), min(natural_width, MAX_READABLE_WIDTH, max_width))
    height = min(content_height_at(width) + chrome_height, max_height)
    return QSize(width, height)


def clamp_into(available: QRect, frame: QRect) -> QPoint:
    """Top-left that keeps ``frame`` inside ``available`` (top-left wins if it cannot fit)."""
    x = min(frame.x(), available.right() + 1 - frame.width())
    y = min(frame.y(), available.bottom() + 1 - frame.height())
    return QPoint(max(x, available.x()), max(y, available.y()))


class AboutDialog(QDialog):
    def __init__(self, parent, title: str, heading_html: str, changelog_html: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 16)
        self.heading = QLabel(heading_html, self)
        self.heading.setTextFormat(Qt.TextFormat.RichText)
        self.heading.setWordWrap(True)
        self._layout.addWidget(self.heading)
        self.changelog = QTextBrowser(self)
        self.changelog.setHtml(changelog_html)
        self.changelog.setOpenExternalLinks(False)
        self.changelog.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.changelog.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.changelog.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._layout.addWidget(self.changelog, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=self)
        self.buttons.accepted.connect(self.accept)
        self._layout.addWidget(self.buttons)
        self._fit_to_content()

    def _available(self) -> QRect:
        parent = self.parentWidget()
        screen = (parent.screen() if parent is not None else None) or self.screen()
        screen = screen or QGuiApplication.primaryScreen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_to_content(self) -> None:
        available = self._available()
        margins = self._layout.contentsMargins()
        horizontal = margins.left() + margins.right() + self.changelog.frameWidth() * 2
        measure = QTextDocument()
        measure.setDefaultFont(self.changelog.font())
        measure.setHtml(self.changelog.toHtml())
        measure.setTextWidth(-1)
        document_margins = measure.documentMargin() * 2
        # Reserve the vertical scrollbar so wrapping does not change when it appears.
        scrollbar = self.changelog.verticalScrollBar().sizeHint().width()
        natural = math.ceil(measure.idealWidth() + document_margins + horizontal + scrollbar)
        natural = max(natural, self.buttons.sizeHint().width() + horizontal)

        def content_height(width: int) -> int:
            text_width = width - horizontal - scrollbar
            measure.setTextWidth(text_width)
            heading = self.heading.heightForWidth(width - margins.left() - margins.right())
            return math.ceil(measure.size().height() + self.changelog.frameWidth() * 2 + heading)

        chrome = (
            margins.top()
            + margins.bottom()
            + self.buttons.sizeHint().height()
            + self._layout.spacing() * 2
        )
        size = fit_size(available, natural, content_height, chrome)
        self.setMaximumSize(available.size())
        self.setMinimumSize(min(MIN_WIDTH, available.width()), 0)
        self.resize(size)

    def _center_over_parent(self) -> None:
        available = self._available()
        parent = self.parentWidget()
        frame = self.frameGeometry()
        frame.setSize(frame.size().boundedTo(available.size()))
        if parent is not None:
            frame.moveCenter(parent.window().frameGeometry().center())
        else:
            frame.moveCenter(available.center())
        self.move(clamp_into(available, frame))

    def _absorb_overflow(self) -> None:
        """Grow by whatever the estimate missed (e.g. stylesheet padding) when there is room."""
        room = int(self._available().height() * SCREEN_FRACTION) - self.height()
        overflow = min(self.changelog.verticalScrollBar().maximum(), room)
        if overflow > 0:
            self.resize(self.width(), self.height() + overflow)
            self._center_over_parent()

    def showEvent(self, event):
        super().showEvent(event)
        self._center_over_parent()
        QTimer.singleShot(0, self._absorb_overflow)
