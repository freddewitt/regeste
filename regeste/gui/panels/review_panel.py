"""Review tab — per-field validation/correction, multi-provider OCR comparison,
confidence-sorted queue, sampling and bulk validation.

Single view with two densities, toggled by "Advanced": the simple view (image,
transcription, image description, 3 group-status buttons) is always visible;
checking "Advanced" additionally reveals the per-field editors (status combo,
rejection note, OCR-provider comparison) — same widgets as before, shown
conditionally rather than duplicated.

The only GUI-side writer of the pivot besides the translation tab, and only
through `regeste.review` (never mutates `Piece` fields directly).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from regeste.i18n import _
from regeste.pivot import CONTENT_FIELDS, FieldValidation, Piece, global_status, load_corpus, save_piece
from regeste.review import (
    apply_correction,
    apply_field_validation,
    apply_group_status,
    bulk_validate,
    ocr_events,
    sample,
    sorted_for_review,
)

logger = logging.getLogger(__name__)

STATUS_CHOICES = ("draft", "to_review", "validated", "rejected")

_BUCKET_COLORS = {
    "validated": QColor("#3fb950"),
    "rejected": QColor("#f85149"),
}
_PENDING_COLOR = QColor("#d29922")


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


def _status_dot_icon(status: str) -> QIcon:
    color = _BUCKET_COLORS.get(status, _PENDING_COLOR)
    pixmap = QPixmap(12, 12)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, 12, 12)
    painter.end()
    return QIcon(pixmap)


class ReviewPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._pieces: list[Piece] = []
        self._current: Piece | None = None
        self._field_edits: dict[str, QPlainTextEdit] = {}
        self._field_status_combos: dict[str, QComboBox] = {}
        self._corpus: list[Piece] | None = None
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

        # --- Simple view (always visible) ------------------------------------------------

        self.image_label = QLabel("")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(320)
        detail_layout.addWidget(self.image_label)

        detail_layout.addWidget(QLabel(_field_label("transcription")))
        self.transcription_display = QPlainTextEdit()
        self.transcription_display.setMinimumHeight(160)
        detail_layout.addWidget(self.transcription_display)

        detail_layout.addWidget(QLabel(_("Image description")))
        self.description_display = QPlainTextEdit()
        self.description_display.setReadOnly(True)
        self.description_display.setMaximumHeight(80)
        detail_layout.addWidget(self.description_display)

        actions_row = QHBoxLayout()
        style = self.style()
        self.validate_button = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton), _("Validate")
        )
        self.validate_button.clicked.connect(self._on_group_validate_clicked)
        actions_row.addWidget(self.validate_button)
        self.reject_button = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton), _("Reject")
        )
        self.reject_button.clicked.connect(self._on_group_reject_clicked)
        actions_row.addWidget(self.reject_button)
        self.hold_button = QPushButton("⏸ " + _("Hold"))
        self.hold_button.clicked.connect(self._on_group_hold_clicked)
        actions_row.addWidget(self.hold_button)
        actions_row.addStretch()
        self.view_image_button = QPushButton(_("View image"))
        self.view_image_button.clicked.connect(self._on_view_image_clicked)
        actions_row.addWidget(self.view_image_button)
        detail_layout.addLayout(actions_row)

        self.advanced_checkbox = QCheckBox(_("Advanced"))
        self.advanced_checkbox.toggled.connect(self._on_advanced_toggled)
        detail_layout.addWidget(self.advanced_checkbox)

        # --- Advanced view (hidden unless "Advanced" is checked) --------------------------

        self.advanced_group = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_group)
        advanced_layout.setContentsMargins(0, 0, 0, 0)

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
        advanced_layout.addWidget(fields_group)

        events_group = QGroupBox(_("OCR outputs (multi-provider comparison)"))
        events_layout = QVBoxLayout(events_group)
        self.events_table = QTableWidget(0, 3)
        self.events_table.setHorizontalHeaderLabels([_("Provider"), _("Model"), _("Text")])
        events_layout.addWidget(self.events_table)
        self.promote_button = QPushButton(_("Promote selected output to transcription"))
        self.promote_button.clicked.connect(self._on_promote_clicked)
        events_layout.addWidget(self.promote_button)
        advanced_layout.addWidget(events_group)

        detail_layout.addWidget(self.advanced_group)
        self.advanced_group.setVisible(False)

        splitter.addWidget(detail)
        outer.addWidget(splitter)

    def _on_advanced_toggled(self, checked: bool) -> None:
        self.advanced_group.setVisible(checked)

    # --- Project synchronisation --------------------------------------------------

    def set_corpus(self, corpus: list[Piece] | None) -> None:
        """Receive a pre-loaded corpus from the main window cache."""
        self._corpus = corpus

    def on_project_changed(self, source_dir: Path | None) -> None:
        self._source_dir = source_dir
        self._reload_pieces()

    def _reload_pieces(self) -> None:
        if self._corpus is not None and self._source_dir is not None:
            pieces = self._corpus
        elif self._source_dir is not None:
            pieces = load_corpus(self._source_dir)
        else:
            pieces = []
        self._set_pieces(pieces)

    def _set_pieces(self, pieces: list[Piece]) -> None:
        ordered = sorted_for_review(pieces)
        previous_id = self._current.id if self._current is not None else None
        self._pieces = ordered
        self._current = None
        self.piece_list.clear()
        for piece in ordered:
            status = global_status(piece)
            label = f"{piece.call_number or piece.id} - {_status_label(status)}"
            item = QListWidgetItem(_status_dot_icon(status), label)
            self.piece_list.addItem(item)
        enabled = bool(ordered)
        self.bulk_validate_button.setEnabled(enabled)
        self.sample_button.setEnabled(enabled)
        if previous_id is not None:
            for row, piece in enumerate(ordered):
                if piece.id == previous_id:
                    self.piece_list.setCurrentRow(row)
                    break

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
        self.transcription_display.setPlainText(piece.transcription)
        self.description_display.setPlainText(piece.summary)
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
                self.image_label.width() or 480,
                520,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # --- Simple-mode group actions ---------------------------------------------------

    def _apply_group_status(self, status: str, *, rejection_note: str | None = None) -> None:
        if self._current is None or self._source_dir is None:
            return
        piece = self._current
        edited_transcription = self.transcription_display.toPlainText()
        if edited_transcription != piece.transcription:
            apply_correction(piece, "transcription", edited_transcription)
        try:
            apply_group_status(piece, status, rejection_note=rejection_note)
        except ValueError as exc:
            QMessageBox.critical(self, _("Error"), str(exc))
            logger.error(str(exc))
            return
        save_piece(self._source_dir, piece)
        logger.info(f"{piece.id}: {status}")
        self._set_pieces(self._pieces)

    def _on_group_validate_clicked(self) -> None:
        self._apply_group_status("validated")

    def _on_group_hold_clicked(self) -> None:
        self._apply_group_status("to_review")

    def _on_group_reject_clicked(self) -> None:
        if self._current is None:
            return
        note, confirmed = QInputDialog.getText(self, _("Reject"), _("Rejection note (required)"))
        note = note.strip()
        if not confirmed or not note:
            return
        self._apply_group_status("rejected", rejection_note=note)

    def _on_view_image_clicked(self) -> None:
        if self._current is None or not self._current.image_path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._current.image_path))

    # --- Advanced-mode per-field editing ----------------------------------------------

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
