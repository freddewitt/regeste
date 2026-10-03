"""Chat tab — ask questions about the transcribed corpus in natural language.

Retrieval (BM25) and prompt building live in `regeste.chat` (no Qt); this panel
only drives them. The model is chosen in Settings > Chat (default: the OCR one).
The conversation is kept in memory for the session and is not saved to disk.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from regeste.chat import ChatAnswer, ChatEngine
from regeste.core.project import ProviderConfig
from regeste.i18n import _
from regeste.pivot import Piece
from regeste.translation import create_translation_provider

from ..worker import ChatWorker, start_worker

logger = logging.getLogger(__name__)


def reveal_in_folder(path: Path) -> None:
    """Show `path` selected in Finder / Explorer (Linux: open its folder)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class ChatPanel(QWidget):
    def __init__(self, corpus_getter: Callable[..., list[Piece]] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._corpus_getter = corpus_getter
        self._pieces: list[Piece] = []
        self._provider: ProviderConfig | None = None
        self._prompt: str | None = None
        self._top_k = 8
        self._engine: ChatEngine | None = None
        self._history: list[tuple[str, str]] = []
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._pending_question = ""
        self._conversation_pieces: set[str] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        body = QHBoxLayout()
        self.transcript = QTextBrowser()
        self.transcript.setMinimumHeight(320)
        self.transcript.setPlaceholderText(
            _("Ask a question about your documents: find a letter, compare sources, "
              "list who wrote to whom...")
        )
        body.addWidget(self.transcript, 3)

        sources_group = QGroupBox(_("Documents of this conversation"))
        sources_layout = QVBoxLayout(sources_group)
        self.sources_list = QListWidget()
        self.sources_list.itemClicked.connect(self._open_item)
        self.sources_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources_list.customContextMenuRequested.connect(self._on_sources_menu)
        sources_layout.addWidget(self.sources_list)
        body.addWidget(sources_group, 1)
        layout.addLayout(body)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(_("Your question"))
        self.input_edit.returnPressed.connect(self._on_send)
        row.addWidget(self.input_edit, 1)
        self.send_button = QPushButton(_("Send"))
        self.send_button.clicked.connect(self._on_send)
        row.addWidget(self.send_button)
        self.new_button = QPushButton(_("New conversation"))
        self.new_button.clicked.connect(self._on_new_conversation)
        row.addWidget(self.new_button)
        layout.addLayout(row)
        self._update_enabled()

    # --- Context pushed by the main window -----------------------------------------

    def set_chat_config(self, provider: ProviderConfig | None, prompt: str | None, top_k: int) -> None:
        if (provider, prompt, top_k) != (self._provider, self._prompt, self._top_k):
            self._engine = None
        self._provider, self._prompt, self._top_k = provider, prompt, top_k

    def refresh(self, pieces: list[Piece]) -> None:
        """Called when the tab is shown: pick up pieces transcribed/corrected since."""
        self._pieces = pieces
        self._engine = None
        self._update_enabled()

    def on_project_changed(self, source_dir) -> None:
        self._pieces = []
        self._engine = None
        self._on_new_conversation()
        self._update_enabled()

    def is_busy(self) -> bool:
        return self._thread is not None

    # --- Conversation -----------------------------------------------------------------

    def _update_enabled(self) -> None:
        ready = bool(self._pieces) and not self.is_busy()
        self.send_button.setEnabled(ready)
        self.input_edit.setEnabled(ready)
        if not self._pieces and not self.is_busy():
            self.status_label.setText(_("No transcribed document yet: run a transcription first."))

    def _on_new_conversation(self) -> None:
        if self.is_busy():
            return
        self._history.clear()
        self.transcript.clear()
        self.sources_list.clear()
        self._conversation_pieces.clear()

    # --- Documents cited in this conversation ----------------------------------------

    def _add_sources(self, sources) -> None:
        for source in sources:
            piece = source.piece
            if piece.id in self._conversation_pieces:
                continue
            self._conversation_pieces.add(piece.id)
            date = f" — {piece.date}" if piece.date else ""
            item = QListWidgetItem(f"{piece.call_number or piece.id}{date}")
            item.setData(Qt.ItemDataRole.UserRole, piece.image_path)
            item.setToolTip(piece.image_path)
            self.sources_list.addItem(item)

    @staticmethod
    def _item_path(item: QListWidgetItem | None) -> Path | None:
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return Path(path) if path else None

    def _open_item(self, item: QListWidgetItem) -> None:
        path = self._item_path(item)
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_sources_menu(self, position) -> None:
        item = self.sources_list.itemAt(position)
        path = self._item_path(item)
        if path is None:
            return
        menu = QMenu(self)
        menu.addAction(_("Open")).triggered.connect(lambda: self._open_item(item))
        menu.addAction(_("Show in folder")).triggered.connect(lambda: reveal_in_folder(path))
        menu.exec(self.sources_list.viewport().mapToGlobal(position))

    def _render(self) -> None:
        parts = []
        for role, text in self._history:
            who = _("You") if role == "user" else _("Assistant")
            parts.append(f"**{who}**\n\n{text}")
        self.transcript.setMarkdown("\n\n---\n\n".join(parts))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _get_engine(self) -> ChatEngine:
        if self._engine is None:
            cfg = self._provider
            if cfg is None or not cfg.model.strip():
                raise ValueError(_("Choose a chat model in Settings > Chat (or in Settings > OCR)."))
            provider = create_translation_provider(cfg.kind, cfg.base_url, cfg.api_key)
            self._engine = ChatEngine(
                self._pieces, provider, cfg.model.strip(), instruction=self._prompt, top_k=self._top_k
            )
        return self._engine

    def _on_send(self) -> None:
        question = self.input_edit.text().strip()
        if not question or self.is_busy():
            return
        try:
            engine = self._get_engine()
        except Exception as exc:  # noqa: BLE001 - bad config: tell the user, don't crash
            self.status_label.setText(str(exc))
            return
        self.input_edit.clear()
        self._pending_question = question
        history = list(self._history)
        self._history.append(("user", question))
        self._render()
        self.status_label.setText(_("Searching and answering..."))
        self._worker = ChatWorker(engine, question, history)
        self._thread = start_worker(self._worker)
        self._worker.succeeded.connect(self._on_answer)
        self._worker.failed.connect(self._on_failed)
        self._thread.finished.connect(self._on_thread_finished)
        self._update_enabled()
        self._thread.start()

    def _on_answer(self, answer: ChatAnswer) -> None:
        self._history.append(("assistant", answer.text))
        self._render()
        self._add_sources(answer.sources)
        self.status_label.setText(
            _("{n} extracts consulted - {tin} tokens in, {tout} tokens out").format(
                n=len(answer.sources), tin=answer.tokens_in, tout=answer.tokens_out
            )
        )

    def _on_failed(self, message: str) -> None:
        self._history.pop()  # the unanswered question
        self._render()
        self.input_edit.setText(self._pending_question)
        self.status_label.setText(_("Error: {message}").format(message=message))

    def _on_thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._update_enabled()
