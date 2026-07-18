"""Translation tab — corpus-level batch translation launcher.

Positioned right after Review, before Export (spec update: Translation is now a
batch job over the whole corpus, not a single-piece workbench). Scope is either
"validated pieces only" (aligned with the existing `global_status(piece) ==
"validated"` definition already used by `check_guards`/the old single-piece
picker — not redefined here) or "all pieces", which bypasses the validation
guard the same way the headless CLI already does (`enforce_guard=False`) while
keeping the low-confidence warning informational only.

Guards (`translation.check_guards`) still gate the "validated only" scope;
`translate_piece()` writes into `Piece.translations[target_language]` and
`save_piece()` persists it — the only other writer of the pivot besides
`review/`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from regeste.core.project import ProviderConfig
from regeste.i18n import LANGUAGE_NAMES, _
from regeste.pivot import Piece, global_status, load_corpus
from regeste.translation import (
    ClaudeTranslationProvider,
    DEFAULT_TRANSLATION_PROMPT,
    GeminiTranslationProvider,
    OpenAICompatTranslationProvider,
    TranslationProvider,
    load_glossary,
    save_glossary,
)

from ..worker import TranslationBatchWorker, start_worker

logger = logging.getLogger(__name__)

# Prompt placeholders the guard warning below refers to, if the user strips them
# from the (now inline, not dialog-gated) prompt text area.
_GUARDED_PLACEHOLDERS = ("{entites_a_preserver}", "{glossaire}")


def _create_translation_provider(kind: str, base_url: str | None, api_key: str | None) -> TranslationProvider:
    if kind == "claude":
        return ClaudeTranslationProvider(api_key=api_key or "")
    if kind == "gemini":
        return GeminiTranslationProvider(api_key=api_key or "")
    return OpenAICompatTranslationProvider(base_url=base_url or "", api_key=api_key, kind=kind)


class TranslationPanel(QWidget):
    # Emitted whenever the user edits the (now inline) translation prompt, so the
    # main window can persist it into the project registry.
    translation_prompt_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._pieces: list[Piece] = []
        self._glossary: dict[str, str] = {}
        self._thread: QThread | None = None
        self._worker: TranslationBatchWorker | None = None
        # Effective translation provider (OCR or separate), resolved and pushed
        # by the main window; the provider choice UI lives in Settings.
        self._translation_provider: ProviderConfig | None = None
        self._corpus: list[Piece] | None = None
        self._build_ui()
        self.on_project_changed(None)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        selection_group = QGroupBox(_("Corpus translation"))
        selection_layout = QHBoxLayout(selection_group)
        selection_layout.addWidget(QLabel(_("Target language")))
        self.language_combo = QComboBox()
        for code, native_name in LANGUAGE_NAMES.items():
            self.language_combo.addItem(native_name, code)
        selection_layout.addWidget(self.language_combo)

        self.validated_only_radio = QRadioButton(_("Validated pieces only"))
        self.validated_only_radio.setChecked(True)
        self.all_pieces_radio = QRadioButton(_("All pieces"))
        selection_layout.addWidget(self.validated_only_radio)
        selection_layout.addWidget(self.all_pieces_radio)

        self.translate_button = QPushButton(_("Launch translation"))
        self.translate_button.clicked.connect(self._on_translate_clicked)
        selection_layout.addWidget(self.translate_button)
        layout.addWidget(selection_group)

        prompt_group = QGroupBox(_("Translation prompt"))
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlainText(DEFAULT_TRANSLATION_PROMPT)
        self.prompt_edit.setMinimumHeight(160)
        prompt_layout.addWidget(self.prompt_edit)
        layout.addWidget(prompt_group)

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

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("0 / 0")
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        layout.addWidget(QLabel(_("Log")))
        self.log_list = QListWidget()
        layout.addWidget(self.log_list)

    # --- Translation provider (configured in Settings) -----------------------------

    def set_effective_translation_provider(self, config: ProviderConfig | None) -> None:
        """Store the resolved translation provider (same as OCR or separate),
        pushed by the main window; used when the user launches a batch."""
        self._translation_provider = config

    def set_translation_prompt(self, prompt: str | None) -> None:
        """Restore the saved translation prompt (None = use the default)."""
        self.prompt_edit.setPlainText(prompt if prompt is not None else DEFAULT_TRANSLATION_PROMPT)

    def _current_prompt(self) -> str | None:
        text = self.prompt_edit.toPlainText()
        return None if text == DEFAULT_TRANSLATION_PROMPT else text

    # --- Project synchronisation --------------------------------------------------

    def set_corpus(self, corpus: list[Piece] | None) -> None:
        """Receive a pre-loaded corpus from the main window cache."""
        self._corpus = corpus

    def on_project_changed(self, source_dir: Path | None) -> None:
        self._source_dir = source_dir
        self._glossary = load_glossary(source_dir) if source_dir is not None else {}
        self._populate_glossary_table()
        self._pieces = self._corpus if self._corpus is not None else (load_corpus(source_dir) if source_dir is not None else [])
        self.translate_button.setEnabled(source_dir is not None and bool(self._pieces))

    # --- Glossary (unchanged, out of scope for this task) --------------------------

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

    # --- Batch translation ----------------------------------------------------------

    def _scoped_pieces(self) -> list[Piece]:
        if self.validated_only_radio.isChecked():
            return [p for p in self._pieces if global_status(p) == "validated"]
        return list(self._pieces)

    def _on_translate_clicked(self) -> None:
        if self._source_dir is None:
            return

        prompt_text = self.prompt_edit.toPlainText()
        removed = [p for p in _GUARDED_PLACEHOLDERS if p not in prompt_text]
        if removed:
            answer = QMessageBox.question(
                self,
                _("Warning"),
                _(
                    "Removing {entites_a_preserver} or {glossaire} disables the injection "
                    "of named entities and the glossary into the prompt."
                )
                + "\n\n"
                + _("Continue anyway?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.translation_prompt_changed.emit(self._current_prompt())

        pieces = self._scoped_pieces()
        if not pieces:
            QMessageBox.information(self, _("Translation"), _("No piece matches the selected scope."))
            return

        config = self._translation_provider
        if config is None or not config.model.strip():
            QMessageBox.critical(self, _("Translation failed"), _("No translation model is configured."))
            return

        provider = _create_translation_provider(config.kind, config.base_url or None, config.api_key or None)
        target_language = self.language_combo.currentData()
        glossary = self._current_glossary()
        enforce_guard = self.validated_only_radio.isChecked()

        self.translate_button.setEnabled(False)
        self.log_list.clear()
        self.progress_bar.setMaximum(len(pieces))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {len(pieces)}")

        self._worker = TranslationBatchWorker(
            self._source_dir,
            pieces,
            target_language,
            provider,
            config.model.strip(),
            glossary=glossary,
            template=self._current_prompt(),
            enforce_guard=enforce_guard,
        )
        self._thread = start_worker(self._worker)
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.finished.connect(self._on_batch_finished)
        self._thread.start()

    def _on_batch_progress(self, done: int, total: int, piece_id: str) -> None:
        self.progress_bar.setValue(done)
        self.progress_label.setText(f"{done} / {total}")
        self.log_list.addItem(f"{piece_id} - OK")
        logger.info(f"{piece_id}: translated")

    def _on_batch_finished(self, succeeded: list, errors: list) -> None:
        self._finish_run()
        for piece_id, message in errors:
            self.log_list.addItem(f"{piece_id} - {_('Error')}: {message}")
            logger.error(f"{piece_id}: {message}")
        self.log_list.addItem(
            _("Translation complete: {ok} succeeded, {failed} failed.").format(
                ok=len(succeeded), failed=len(errors)
            )
        )
        logger.info(f"Batch translation done: {len(succeeded)} ok, {len(errors)} failed")

    def _finish_run(self) -> None:
        if self._thread is not None:
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self.translate_button.setEnabled(bool(self._pieces))
