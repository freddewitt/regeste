"""Logs tab — live view of everything logged under the "regeste" logger.

`QtLogHandler` re-emits every log record as a Qt signal so it can cross from a
worker thread to the GUI thread safely; `LogPanel` appends it, preserving the
user's scroll position unless they were already pinned to the bottom.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from regeste.i18n import _

LOGGER_NAME = "regeste"


class _LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.emitter = _LogEmitter()
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.emitter.message.emit(self.format(record))


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_("Search in logs..."))
        self.search_edit.returnPressed.connect(self._on_search_next)
        search_row.addWidget(self.search_edit)
        search_button = QPushButton(_("Search"))
        search_button.clicked.connect(self._on_search_next)
        search_row.addWidget(search_button)
        copy_button = QPushButton(_("Copy all"))
        copy_button.clicked.connect(self._on_copy_clicked)
        search_row.addWidget(copy_button)
        layout.addLayout(search_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(20000)
        font = self.log_view.font()
        font.setFamily("Monospace")
        self.log_view.setFont(font)
        layout.addWidget(self.log_view)

    def append_message(self, text: str) -> None:
        scrollbar = self.log_view.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        self.log_view.appendPlainText(text)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _on_copy_clicked(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())

    def _on_search_next(self) -> None:
        term = self.search_edit.text()
        if not term:
            return
        if self.log_view.find(term):
            return
        # Not found from the current position onward - wrap and retry from the top.
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.log_view.setTextCursor(cursor)
        self.log_view.find(term)
