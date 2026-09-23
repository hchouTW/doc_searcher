# Purpose: Single-line, code-editor-style search input with live query highlighting.
# What the code does:
#   - Colors boolean operators, quoted phrases, regex/syntax symbols, and the
#     filename:/檔名: field restrictor as users type, each with its own high-contrast
#     color distinct from plain search keywords.
#   - Preserves QLineEdit-like helper methods used by the main window.
# Usage notes, dependencies, or assumptions:
#   - Built on QPlainTextEdit because QLineEdit cannot render per-token formatting.

import re

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit

from ui.theme import ThemeColors


class QuerySyntaxHighlighter(QSyntaxHighlighter):
    """Apply lightweight search-query token colors to a text document."""

    def __init__(self, document):
        super().__init__(document)
        self.operator_format = QTextCharFormat()
        self.phrase_format = QTextCharFormat()
        self.symbol_format = QTextCharFormat()
        self.field_format = QTextCharFormat()

    def set_theme(self, theme: ThemeColors):
        self.operator_format.setForeground(QColor("#fb923c" if theme.is_dark else "#c2410c"))
        self.operator_format.setFontWeight(QFont.Bold)
        self.phrase_format.setForeground(QColor("#86efac" if theme.is_dark else "#15803d"))
        self.symbol_format.setForeground(QColor("#c4b5fd" if theme.is_dark else "#7c3aed"))
        self.field_format.setForeground(QColor("#38bdf8" if theme.is_dark else "#0369a1"))
        self.field_format.setFontWeight(QFont.Bold)
        self.rehighlight()

    def highlightBlock(self, text: str):
        for match in re.finditer(r"\b(?:AND|OR|NOT)\b", text, re.IGNORECASE):
            self.setFormat(match.start(), match.end() - match.start(), self.operator_format)
        for match in re.finditer(r"[()\[\]{}*?+\\^$|.]", text):
            self.setFormat(match.start(), 1, self.symbol_format)
        for match in re.finditer(r'"(?:[^"\\]|\\.)*(?:"|$)', text):
            self.setFormat(match.start(), match.end() - match.start(), self.phrase_format)
        field_match = re.match(r"^\s*(?:filename|檔名)\s*:", text, re.IGNORECASE)
        if field_match:
            self.setFormat(0, field_match.end(), self.field_format)


class SyntaxSearchInput(QPlainTextEdit):
    """Single-line editor compatible with the search window's existing API."""

    returnPressed = Signal()
    textChangedWithText = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTabChangesFocus(True)
        self.setFixedHeight(42)
        self.highlighter = QuerySyntaxHighlighter(self.document())
        self.textChanged.connect(lambda: self.textChangedWithText.emit(self.text()))

    def sizeHint(self):
        return QSize(280, 42)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str):
        self.setPlainText(text)

    def insert(self, text: str):
        self.insertPlainText(text)

    def cursorPosition(self) -> int:
        return self.textCursor().position()

    def setCursorPosition(self, position: int):
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(position, len(self.toPlainText()))))
        self.setTextCursor(cursor)

    def setClearButtonEnabled(self, _enabled: bool):
        """Compatibility no-op; MainWindow owns the adjacent clear button."""

    def set_theme(self, theme: ThemeColors):
        self.highlighter.set_theme(theme)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)
