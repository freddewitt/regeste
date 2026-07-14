"""Translation tab — provider/model/target-language selection, glossary editor,
source/translation side-by-side view.

Guards (`translation.check_guards`) block translation on a non-validated piece
and warn on low/unknown OCR confidence, per export_instruct.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from regeste.core.project import ProviderConfig
from regeste.i18n import LANGUAGE_NAMES, _
from regeste.pivot import Piece, global_status, load_corpus, save_piece
from regeste.translation import (
    ClaudeTranslationProvider,
    DEFAULT_TRANSLATION_PROMPT,
    GeminiTranslationProvider,
    OpenAICompatTranslationProvider,
    TranslationProvider,
    check_guards,
    load_glossary,
    save_glossary,
)

from ..prompt_dialog import PromptEditDialog
from ..worker import TranslationWorker, start_worker

logger = logging.getLogger(__name__)

PROVIDER_KINDS = ("claude", "gemini", "openai", "lm_studio", "llama_cpp", "ollama")
REQUIRES_API_KEY_KINDS = ("claude", "gemini", "openai")
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "lm_studio": "http://localhost:1234/v1",
    "llama_cpp": "http://localhost:8080/v1",
    "ollama": "http://localhost:11434/v1",
}


def _create_translation_provider(kind: str, base_url: str | None, api_key: str | None) -> TranslationProvider:
    if kind == "claude":
        return ClaudeTranslationProvider(api_key=api_key or "")
    if kind == "gemini":
        return GeminiTranslationProvider(api_key=api_key or "")
    return OpenAICompatTranslationProvider(base_url=base_url or "", api_key=api_key, kind=kind)


class TranslationPanel(QWidget):
    # Emitted when the user saves an edited translation prompt, so the main
    # window can persist it into the project registry.
    translation_prompt_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._pieces: list[Piece] = []
        self._glossary: dict[str, str] = {}
        self._thread: QThread | None = None
        self._worker: TranslationWorker | None = None
        # Effective translation provider (OCR or separate), resolved and pushed
        # by the main window; the provider choice UI lives in Settings.
        self._translation_provider: ProviderConfig | None = None
        # None means "use the default translation prompt".
        self._translation_prompt: str | None = None
        self._build_ui()
        self.on_project_changed(None)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        selection_group = QGroupBox(_("Piece and target language"))
        selection_layout = QHBoxLayout(selection_group)
        selection_layout.addWidget(QLabel(_("Validated piece")))
        self.piece_combo = QComboBox()
        selection_layout.addWidget(self.piece_combo)
        selection_layout.addWidget(QLabel(_("Source language")))
        self.source_language_edit = QLineEdit()
        self.source_language_edit.setPlaceholderText(_("auto-detected"))
        selection_layout.addWidget(self.source_language_edit)
        selection_layout.addWidget(QLabel(_("Target language")))
        self.language_combo = QComboBox()
        for code, native_name in LANGUAGE_NAMES.items():
            self.language_combo.addItem(native_name, code)
        selection_layout.addWidget(self.language_combo)
        self.edit_prompt_button = QPushButton(_("Edit translation prompt..."))
        self.edit_prompt_button.clicked.connect(self._on_edit_prompt_clicked)
        selection_layout.addWidget(self.edit_prompt_button)
        self.translate_button = QPushButton(_("Translate"))
        self.translate_button.clicked.connect(self._on_translate_clicked)
        selection_layout.addWidget(self.translate_button)
        layout.addWidget(selection_group)

        self.guard_label = QLabel("")
        self.guard_label.setWordWrap(True)
        layout.addWidget(self.guard_label)

        glossary_group = QGroupBox(_("Corpus glossary"))
        glossary_layout = QVBoxLayout(glossary_group)
        self.glossary_table = QTableWidget(0, 2)
        self.glossary_table.setHorizontalHeaderLabels([_("Term"), _("Translation")])
        glossary_layout.addWidget(self.glossary_table)
        glossary_buttons = QHBoxLayout()
        add_term_button = QPushButton(_("Add row"))
        add_term_button.clicked.connect(lambda: self._add_glossary_row("", ""))
        remove_term_button = QPushButton(_("Remove selected row"))
        remove_term_button.clicked.connect(self._remove_selected_glossary_row)
        save_glossary_button = QPushButton(_("Save glossary"))
        save_glossary_button.clicked.connect(self._on_save_glossary_clicked)
        glossary_buttons.addWidget(add_term_button)
        glossary_buttons.addWidget(remove_term_button)
        glossary_buttons.addWidget(save_glossary_button)
        glossary_layout.addLayout(glossary_buttons)
        layout.addWidget(glossary_group)

        splitter = QSplitter()
        self.source_view = QPlainTextEdit()
        self.source_view.setReadOnly(True)
        self.translation_view = QPlainTextEdit()
        self.translation_view.setReadOnly(True)
        splitter.addWidget(self.source_view)
        splitter.addWidget(self.translation_view)
        layout.addWidget(splitter)

        self.piece_combo.currentIndexChanged.connect(self._on_piece_selected)

    # --- Translation provider and prompt (configured in Settings / dialog) --------

    def set_effective_translation_provider(self, config: ProviderConfig | None) -> None:
        """Store the resolved translation provider (same as OCR or separate),
        pushed by the main window; used when the user clicks Translate."""
        self._translation_provider = config

    def set_translation_prompt(self, prompt: str | None) -> None:
        """Restore the saved translation prompt (None = use the default)."""
        self._translation_prompt = prompt

    def _on_edit_prompt_clicked(self) -> None:
        current = (
            self._translation_prompt
            if self._translation_prompt is not None
            else DEFAULT_TRANSLATION_PROMPT
        )
        dialog = PromptEditDialog(
            self,
            title=_("Translation prompt"),
            current_text=current,
            default_text=DEFAULT_TRANSLATION_PROMPT,
            warn_placeholders=["{entites_a_preserver}", "{glossaire}"],
            warning_message=_(
                "Removing {entites_a_preserver} or {glossaire} disables the injection "
                "of named entities and the glossary into the prompt."
            ),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = dialog.text()
            self._translation_prompt = None if text == DEFAULT_TRANSLATION_PROMPT else text
            self.translation_prompt_changed.emit(self._translation_prompt)

    # --- Project synchronisation --------------------------------------------------

    def on_project_changed(self, source_dir: Path | None) -> None:
        self._source_dir = source_dir
        self._glossary = load_glossary(source_dir) if source_dir is not None else {}
        self._populate_glossary_table()

        pieces = load_corpus(source_dir) if source_dir is not None else []
        self._pieces = [p for p in pieces if global_status(p) == "validated"]
        self.piece_combo.clear()
        for piece in self._pieces:
            self.piece_combo.addItem(piece.call_number or piece.id, piece.id)

        enabled = source_dir is not None
        self.translate_button.setEnabled(enabled and bool(self._pieces))
        self._on_piece_selected(self.piece_combo.currentIndex())

    def _current_piece(self) -> Piece | None:
        piece_id = self.piece_combo.currentData()
        return next((p for p in self._pieces if p.id == piece_id), None)

    def _on_piece_selected(self, _index: int) -> None:
        piece = self._current_piece()
        if piece is None:
            self.source_view.setPlainText("")
            self.translation_view.setPlainText("")
            self.source_language_edit.setText("")
            self.guard_label.setText("")
            return
        self.source_view.setPlainText(piece.transcription)
        # Pre-fill the source language from the OCR-detected language; editable.
        self.source_language_edit.setText(piece.language_detected)
        target = self.language_combo.currentData()
        translation = (piece.translations or {}).get(target)
        self.translation_view.setPlainText(translation.text if translation else "")
        guard = check_guards(piece)
        self.guard_label.setText(" ".join(guard.warnings))

    # --- Glossary ---------------------------------------------------------------------

    def _populate_glossary_table(self) -> None:
        self.glossary_table.setRowCount(0)
        for term, translation in self._glossary.items():
            self._add_glossary_row(term, translation)

    def _add_glossary_row(self, term: str, translation: str) -> None:
        row = self.glossary_table.rowCount()
        self.glossary_table.insertRow(row)
        self.glossary_table.setItem(row, 0, QTableWidgetItem(term))
        self.glossary_table.setItem(row, 1, QTableWidgetItem(translation))

    def _remove_selected_glossary_row(self) -> None:
        row = self.glossary_table.currentRow()
        if row >= 0:
            self.glossary_table.removeRow(row)

    def _current_glossary(self) -> dict[str, str]:
        glossary: dict[str, str] = {}
        for row in range(self.glossary_table.rowCount()):
            term_item = self.glossary_table.item(row, 0)
            translation_item = self.glossary_table.item(row, 1)
            term = term_item.text().strip() if term_item else ""
            if not term:
                continue
            glossary[term] = translation_item.text().strip() if translation_item else ""
        return glossary

    def _on_save_glossary_clicked(self) -> None:
        if self._source_dir is None:
            return
        self._glossary = self._current_glossary()
        save_glossary(self._source_dir, self._glossary)
        QMessageBox.information(self, _("Glossary"), _("Glossary saved."))

    # --- Translation ---------------------------------------------------------------------

    def _on_translate_clicked(self) -> None:
        piece = self._current_piece()
        if piece is None or self._source_dir is None:
            return

        guard = check_guards(piece)
        if not guard.allowed:
            QMessageBox.critical(self, _("Translation blocked"), guard.blocked_reason or "")
            logger.error(guard.blocked_reason or "")
            return
        if guard.warnings:
            answer = QMessageBox.question(
                self,
                _("Warning"),
                "\n".join(guard.warnings) + "\n\n" + _("Continue anyway?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        config = self._translation_provider
        if config is None or not config.model.strip():
            QMessageBox.critical(
                self,
                _("Translation failed"),
                _("No translation model is configured."),
            )
            return

        provider = _create_translation_provider(
            config.kind,
            config.base_url or None,
            config.api_key or None,
        )
        target_language = self.language_combo.currentData()
        glossary = self._current_glossary()

        self.translate_button.setEnabled(False)
        self._worker = TranslationWorker(
            piece,
            target_language,
            provider,
            config.model.strip(),
            glossary=glossary,
            source_language=self.source_language_edit.text().strip(),
            template=self._translation_prompt,
        )
        self._thread = start_worker(self._worker)
        self._worker.succeeded.connect(self._on_translation_succeeded)
        self._worker.failed.connect(self._on_translation_failed)
        self._thread.start()

    def _on_translation_succeeded(self, piece: Piece) -> None:
        self._finish_run()
        if self._source_dir is not None:
            save_piece(self._source_dir, piece)
        target = self.language_combo.currentData()
        translation = (piece.translations or {}).get(target)
        self.translation_view.setPlainText(translation.text if translation else "")
        logger.info(f"{piece.id} -> {target}: OK")

    def _on_translation_failed(self, message: str) -> None:
        self._finish_run()
        QMessageBox.critical(self, _("Translation failed"), message)
        logger.error(_("Translation failed: {error}").format(error=message))

    def _finish_run(self) -> None:
        if self._thread is not None:
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self.translate_button.setEnabled(bool(self._pieces))
