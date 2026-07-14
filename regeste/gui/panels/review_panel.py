"""Review tab — per-field validation/correction, multi-provider OCR comparison,
confidence-sorted queue, sampling and bulk validation.

The only GUI-side writer of the pivot besides the translation tab, and only
through `regeste.review` (never mutates `Piece` fields directly).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from regeste.i18n import _
from regeste.pivot import CONTENT_FIELDS, FieldValidation, Piece, global_status, load_corpus, save_piece
from regeste.review import apply_correction, apply_field_validation, bulk_validate, ocr_events, sample, sorted_by_confidence

logger = logging.getLogger(__name__)

STATUS_CHOICES = ("draft", "to_review", "validated", "rejected")


def _status_label(status: str) -> str:
    return {
        "draft": _("Draft"),
        "to_review": _("To review"),
        "validated": _("Validated"),
        "rejected": _("Rejected"),
    }.get(status, status)


def _field_label(field: str) -> str:
    return {
        "call_number": _("Call number"),
        "date": _("Date"),
        "sender": _("Sender"),
        "recipient": _("Recipient"),
        "transcription": _("Transcription"),
    }.get(field, field)


class ReviewPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._pieces: list[Piece] = []
        self._current: Piece | None = None
        self._field_edits: dict[str, QPlainTextEdit] = {}
        self._field_status_combos: dict[str, QComboBox] = {}
        self._build_ui()
        self.on_project_changed(None)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        bulk_group = QGroupBox(_("Queue"))
        bulk_layout = QHBoxLayout(bulk_group)
        bulk_layout.addWidget(QLabel(_("Auto-validation threshold")))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(0.8)
        bulk_layout.addWidget(self.threshold_spin)
        self.bulk_validate_button = QPushButton(_("Bulk-validate above threshold"))
        self.bulk_validate_button.clicked.connect(self._on_bulk_validate_clicked)
        bulk_layout.addWidget(self.bulk_validate_button)
        bulk_layout.addWidget(QLabel(_("Sample size")))
        self.sample_size_spin = QSpinBox()
        self.sample_size_spin.setRange(1, 1000)
        self.sample_size_spin.setValue(10)
        bulk_layout.addWidget(self.sample_size_spin)
        self.sample_button = QPushButton(_("Sample"))
        self.sample_button.clicked.connect(self._on_sample_clicked)
        bulk_layout.addWidget(self.sample_button)
        self.reset_queue_button = QPushButton(_("Show all"))
        self.reset_queue_button.clicked.connect(self._reload_pieces)
        bulk_layout.addWidget(self.reset_queue_button)
        bulk_layout.addStretch()
        outer.addWidget(bulk_group)

        splitter = QSplitter()
        self.piece_list = QListWidget()
        self.piece_list.currentRowChanged.connect(self._on_piece_selected)
        splitter.addWidget(self.piece_list)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        detail_layout.addWidget(self.summary_label)

        self.image_label = QLabel("")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(200)
        detail_layout.addWidget(self.image_label)

        fields_group = QGroupBox(_("Fields"))
        fields_layout = QVBoxLayout(fields_group)
        for field in CONTENT_FIELDS:
            row = QHBoxLayout()
            row.addWidget(QLabel(_field_label(field)))
            edit = QPlainTextEdit()
            edit.setMaximumHeight(48)
            self._field_edits[field] = edit
            row.addWidget(edit)
            combo = QComboBox()
            for status in STATUS_CHOICES:
                combo.addItem(_status_label(status), status)
            self._field_status_combos[field] = combo
            row.addWidget(combo)
            fields_layout.addLayout(row)
        self.rejection_note_edit = QLineEdit()
        self.rejection_note_edit.setPlaceholderText(_("Rejection note (required if a field is rejected)"))
        fields_layout.addWidget(self.rejection_note_edit)
        self.save_button = QPushButton(_("Save"))
        self.save_button.clicked.connect(self._on_save_clicked)
        fields_layout.addWidget(self.save_button)
        detail_layout.addWidget(fields_group)

        events_group = QGroupBox(_("OCR outputs (multi-provider comparison)"))
        events_layout = QVBoxLayout(events_group)
        self.events_table = QTableWidget(0, 3)
        self.events_table.setHorizontalHeaderLabels([_("Provider"), _("Model"), _("Text")])
        events_layout.addWidget(self.events_table)
        self.promote_button = QPushButton(_("Promote selected output to transcription"))
        self.promote_button.clicked.connect(self._on_promote_clicked)
        events_layout.addWidget(self.promote_button)
        detail_layout.addWidget(events_group)

        splitter.addWidget(detail)
        outer.addWidget(splitter)

    # --- Project synchronisation --------------------------------------------------

    def on_project_changed(self, source_dir: Path | None) -> None:
        self._source_dir = source_dir
        self._reload_pieces()

    def _reload_pieces(self) -> None:
        pieces = load_corpus(self._source_dir) if self._source_dir is not None else []
        self._set_pieces(sorted_by_confidence(pieces))

    def _set_pieces(self, pieces: list[Piece]) -> None:
        self._pieces = pieces
        self._current = None
        self.piece_list.clear()
        for piece in pieces:
            label = f"{piece.call_number or piece.id} - {_status_label(global_status(piece))}"
            self.piece_list.addItem(QListWidgetItem(label))
        enabled = bool(pieces)
        self.bulk_validate_button.setEnabled(enabled)
        self.sample_button.setEnabled(enabled)

    # --- Bulk tools -----------------------------------------------------------------

    def _on_bulk_validate_clicked(self) -> None:
        if self._source_dir is None:
            return
        validated = bulk_validate(self._pieces, self.threshold_spin.value())
        for piece in validated:
            save_piece(self._source_dir, piece)
        self._set_pieces(self._pieces)
        QMessageBox.information(
            self, _("Bulk validation"), _("{count} piece(s) validated.").format(count=len(validated))
        )
        logger.info(_("{count} piece(s) validated.").format(count=len(validated)))

    def _on_sample_clicked(self) -> None:
        self._set_pieces(sample(self._pieces, self.sample_size_spin.value()))

    # --- Piece detail -----------------------------------------------------------------

    def _on_piece_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._pieces):
            self._current = None
            return
        piece = self._pieces[row]
        self._current = piece
        self.summary_label.setText(
            _("{id} - fonds: {fonds} / série: {series}").format(
                id=piece.id, fonds=piece.fonds, series=piece.series
            )
        )
        self._update_image_preview(piece)
        values = {
            "call_number": piece.call_number,
            "date": piece.date,
            "sender": piece.sender,
            "recipient": piece.recipient,
            "transcription": piece.transcription,
        }
        for field in CONTENT_FIELDS:
            self._field_edits[field].setPlainText(values[field])
            status = piece.field_validations.get(field, FieldValidation()).status
            index = self._field_status_combos[field].findData(status)
            self._field_status_combos[field].setCurrentIndex(index if index >= 0 else 0)
        self.rejection_note_edit.clear()

        self.events_table.setRowCount(0)
        for event in ocr_events(piece):
            row_index = self.events_table.rowCount()
            self.events_table.insertRow(row_index)
            self.events_table.setItem(row_index, 0, QTableWidgetItem(event.provider or ""))
            self.events_table.setItem(row_index, 1, QTableWidgetItem(event.model or ""))
            self.events_table.setItem(row_index, 2, QTableWidgetItem(event.detail))

    def _update_image_preview(self, piece: Piece) -> None:
        pixmap = QPixmap(piece.image_path) if piece.image_path else QPixmap()
        if pixmap.isNull():
            self.image_label.setText(_("No image"))
            return
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.width() or 400,
                400,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_save_clicked(self) -> None:
        if self._current is None or self._source_dir is None:
            return
        piece = self._current
        for field in CONTENT_FIELDS:
            new_value = self._field_edits[field].toPlainText()
            if getattr(piece, field) != new_value:
                apply_correction(piece, field, new_value)
            status = self._field_status_combos[field].currentData()
            current_status = piece.field_validations.get(field, FieldValidation()).status
            if status == current_status:
                continue
            note = self.rejection_note_edit.text().strip() or None
            try:
                apply_field_validation(piece, field, status, rejection_note=note)
            except ValueError as exc:
                QMessageBox.critical(self, _("Error"), str(exc))
                logger.error(str(exc))
                return
        save_piece(self._source_dir, piece)
        logger.info(f"{piece.id}: saved")
        self._reload_pieces()

    def _on_promote_clicked(self) -> None:
        if self._current is None:
            return
        row = self.events_table.currentRow()
        if row < 0:
            return
        item = self.events_table.item(row, 2)
        if item is None:
            return
        self._field_edits["transcription"].setPlainText(item.text())
