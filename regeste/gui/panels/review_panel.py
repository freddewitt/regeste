"""Review tab — per-field validation/correction, multi-provider OCR comparison,
confidence-sorted queue, sampling and bulk validation.

Built for non-specialists: a progress bar, the original image next to its
transcription, one big "It's correct - next" button, and the expert tools
(bulk validation, sampling) tucked behind "Tools".

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
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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


FILTER_TO_CHECK = "to_check"
FILTER_DONE = "done"
FILTER_ALL = "all"


class _ImageViewer(QGraphicsView):
    """Zoomable image: mouse wheel to zoom, double-click to fit the window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumWidth(320)
        self.setToolTip(_("Scroll to zoom, double-click to fit the window"))
        self._has_image = False
        self._fitted = True

    def set_image(self, path: str | None) -> None:
        self._scene.clear()
        pixmap = QPixmap(path) if path else QPixmap()
        self._has_image = not pixmap.isNull()
        if not self._has_image:
            self._scene.addText(_("No image"))
        else:
            self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._scene.itemsBoundingRect())
        self.fit()

    def fit(self) -> None:
        self.resetTransform()
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._has_image:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._fitted = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._fitted and self._has_image:
            self.fit()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._fitted and self._has_image:
            self.fit()


def _is_pending(piece: Piece) -> bool:
    return global_status(piece) not in ("validated", "rejected")


class ReviewPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._pieces: list[Piece] = []
        self._current: Piece | None = None
        self._field_edits: dict[str, QPlainTextEdit] = {}
        self._field_status_combos: dict[str, QComboBox] = {}
        self._corpus: list[Piece] | None = None
        self._base: list[Piece] = []
        self._build_ui()
        self.on_project_changed(None)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # --- Top bar: progress, filter, Tools -------------------------------------------
        top = QHBoxLayout()
        self.progress_label = QLabel("")
        top.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(12)
        top.addWidget(self.progress_bar, 1)
        top.addWidget(QLabel(_("Show")))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem(_("All"), FILTER_ALL)
        self.filter_combo.addItem(_("To check"), FILTER_TO_CHECK)
        self.filter_combo.addItem(_("Done"), FILTER_DONE)
        self.filter_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        top.addWidget(self.filter_combo)
        self.tools_button = QToolButton()
        self.tools_button.setText(_("Tools"))
        self.tools_button.setCheckable(True)
        self.tools_button.setArrowType(Qt.ArrowType.DownArrow)
        self.tools_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools_button.toggled.connect(lambda checked: self.bulk_group.setVisible(checked))
        top.addWidget(self.tools_button)
        outer.addLayout(top)

        # --- Expert tools (hidden until "Tools" is opened) -----------------------------
        self.bulk_group = QGroupBox(_("Queue"))
        bulk_layout = QHBoxLayout(self.bulk_group)
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
        self.hypothesis_checkbox = QCheckBox(_("Hypothetical only"))
        self.hypothesis_checkbox.toggled.connect(self._reload_pieces)
        bulk_layout.addWidget(self.hypothesis_checkbox)
        bulk_layout.addStretch()
        outer.addWidget(self.bulk_group)
        self.bulk_group.setVisible(False)

        # --- Main area: list | image | text and actions --------------------------------
        splitter = QSplitter()
        self.piece_list = QListWidget()
        self.piece_list.setMinimumWidth(160)
        self.piece_list.currentRowChanged.connect(self._on_piece_selected)
        splitter.addWidget(self.piece_list)

        self.image_viewer = _ImageViewer()
        splitter.addWidget(self.image_viewer)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        detail_layout.addWidget(self.summary_label)

        # --- Simple view (always visible) ------------------------------------------------

        question = QLabel(_("Does the text match the image? Correct it below if needed."))
        question.setWordWrap(True)
        detail_layout.addWidget(question)
        self.transcription_display = QPlainTextEdit()
        self.transcription_display.setMinimumHeight(160)
        self.transcription_display.textChanged.connect(self._update_validate_label)
        detail_layout.addWidget(self.transcription_display, 1)

        detail_layout.addWidget(QLabel(_("Image description")))
        self.description_display = QPlainTextEdit()
        self.description_display.setReadOnly(True)
        self.description_display.setMaximumHeight(80)
        detail_layout.addWidget(self.description_display)

        actions_row = QHBoxLayout()
        style = self.style()
        self.validate_button = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton), _("It's correct - next")
        )
        self.validate_button.setDefault(True)
        self.validate_button.setMinimumHeight(40)
        self.validate_button.setToolTip(_("Shortcut: {keys}").format(keys="Ctrl+Enter"))
        self.validate_button.clicked.connect(self._on_group_validate_clicked)
        actions_row.addWidget(self.validate_button, 2)
        self.hold_button = QPushButton("⏸ " + _("Review later"))
        self.hold_button.setMinimumHeight(40)
        self.hold_button.setToolTip(_("Shortcut: {keys}").format(keys="Ctrl+L"))
        self.hold_button.clicked.connect(self._on_group_hold_clicked)
        actions_row.addWidget(self.hold_button, 1)
        self.reject_button = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton), _("Reject")
        )
        self.reject_button.setMinimumHeight(40)
        self.reject_button.clicked.connect(self._on_group_reject_clicked)
        actions_row.addWidget(self.reject_button, 1)
        detail_layout.addLayout(actions_row)

        self.view_image_button = QPushButton(_("View image"))
        self.view_image_button.clicked.connect(self._on_view_image_clicked)
        detail_layout.addWidget(self.view_image_button, 0, Qt.AlignmentFlag.AlignLeft)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._on_group_validate_clicked)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._on_group_validate_clicked)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._on_group_hold_clicked)

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
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([180, 520, 420])
        outer.addWidget(splitter, 1)

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
        # Apply the "hypothetical only" filter before anything else.
        if self.hypothesis_checkbox.isChecked():
            pieces = [p for p in pieces if p.hypothesis_mode]
        self._base = pieces
        self._refresh()

    def _refresh(self, select_id: str | None = None) -> None:
        """Rebuild the list from `self._base` (progress counts the whole base, the
        "Show" filter only narrows what the list displays)."""
        base = self._base
        mode = self.filter_combo.currentData()
        if mode == FILTER_TO_CHECK:
            shown = [p for p in base if _is_pending(p)]
        elif mode == FILTER_DONE:
            shown = [p for p in base if not _is_pending(p)]
        else:
            shown = base
        ordered = sorted_for_review(shown)
        previous_id = select_id or (self._current.id if self._current is not None else None)
        self._pieces = ordered
        self._current = None
        self.piece_list.blockSignals(True)
        self.piece_list.clear()
        for piece in ordered:
            status = global_status(piece)
            prefix = "H " if piece.hypothesis_mode else ""
            label = f"{prefix}{piece.call_number or piece.id} - {_status_label(status)}"
            item = QListWidgetItem(_status_dot_icon(status), label)
            self.piece_list.addItem(item)
        self.piece_list.blockSignals(False)
        enabled = bool(ordered)
        self.bulk_validate_button.setEnabled(enabled)
        self.sample_button.setEnabled(enabled)
        for button in (self.validate_button, self.hold_button, self.reject_button):
            button.setEnabled(enabled)
        self._update_progress()
        row = next((r for r, p in enumerate(ordered) if p.id == previous_id), -1)
        if row >= 0:
            self.piece_list.setCurrentRow(row)
        else:
            self._on_piece_selected(-1)

    def _update_progress(self) -> None:
        total = len(self._base)
        done = sum(1 for p in self._base if not _is_pending(p))
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        if total and done == total:
            self.progress_label.setText(_("Everything has been checked."))
        else:
            self.progress_label.setText(_("{done} of {total} checked").format(done=done, total=total))

    def _next_pending_id(self) -> str | None:
        """The next piece still to check after the current one (wrapping around)."""
        if self._current is None:
            return None
        ids = [p.id for p in self._pieces]
        start = ids.index(self._current.id) if self._current.id in ids else -1
        for piece in self._pieces[start + 1 :] + self._pieces[: start + 1]:
            if piece.id != self._current.id and _is_pending(piece):
                return piece.id
        return None

    def _update_validate_label(self) -> None:
        edited = self._current is not None and self.transcription_display.toPlainText() != self._current.transcription
        self.validate_button.setText(_("Save my corrections - next") if edited else _("It's correct - next"))

    # --- Bulk tools -----------------------------------------------------------------

    def _on_bulk_validate_clicked(self) -> None:
        if self._source_dir is None:
            return
        validated = bulk_validate(self._pieces, self.threshold_spin.value())
        for piece in validated:
            save_piece(self._source_dir, piece)
        self._refresh()
        QMessageBox.information(
            self, _("Bulk validation"), _("{count} piece(s) validated.").format(count=len(validated))
        )
        logger.info(_("{count} piece(s) validated.").format(count=len(validated)))

    def _on_sample_clicked(self) -> None:
        self._set_pieces(sample(self._base, self.sample_size_spin.value()))

    # --- Piece detail -----------------------------------------------------------------

    def _on_piece_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._pieces):
            self._current = None
            self.summary_label.setText("")
            self.transcription_display.clear()
            self.description_display.clear()
            self.image_viewer.set_image(None)
            self._update_validate_label()
            return
        piece = self._pieces[row]
        self._current = piece
        self.summary_label.setText(
            _("{id} - fonds: {fonds} / série: {series}").format(
                id=piece.id, fonds=piece.fonds, series=piece.series
            )
        )
        self.image_viewer.set_image(piece.image_path)
        self.transcription_display.setPlainText(piece.transcription)
        self._update_validate_label()
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
        # Move on to the next piece to check; stay put when nothing is left.
        self._refresh(select_id=self._next_pending_id() or piece.id)

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
