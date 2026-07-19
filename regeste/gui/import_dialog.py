"""Batch import dialog — pick an external photo folder, name the batch, choose copy mode.

The dialog resolves batch identifier collisions itself: if the requested id is
already used in the registry, the first free ``<id>-N`` suffix (N starting at 2)
is picked silently and reported to the user — the user is never asked twice.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from regeste.core.registry import Registry
from regeste.i18n import _


def resolve_batch_id(requested: str, registry: Registry) -> str:
    """Return `requested`, or the first free ``<requested>-N`` suffix on collision."""
    used = {entry.source.batch_id for entry in registry.files.values()}
    if requested not in used:
        return requested
    suffix = 2
    while f"{requested}-{suffix}" in used:
        suffix += 1
    return f"{requested}-{suffix}"


class BatchImportDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, project_dir: Path, registry: Registry) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Import batch"))
        self._project_dir = project_dir
        self._registry = registry
        self._resolved_batch_id = ""

        layout = QVBoxLayout(self)
        form = QGridLayout()

        form.addWidget(QLabel(_("Source folder")), 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        browse_button = QPushButton(_("Browse..."))
        browse_button.clicked.connect(self._browse_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(browse_button)
        form.addLayout(folder_row, 0, 1)

        form.addWidget(QLabel(_("Batch identifier")), 1, 0)
        self.batch_id_edit = QLineEdit()
        form.addWidget(self.batch_id_edit, 1, 1)
        layout.addLayout(form)

        self.keep_in_place_checkbox = QCheckBox(_("Process in place"))
        layout.addWidget(self.keep_in_place_checkbox)
        keep_in_place_hint = QLabel(_("(do not copy the images)"))
        keep_in_place_hint.setIndent(20)
        layout.addWidget(keep_in_place_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = buttons.addButton(
            _("Import"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.batch_id_edit.textChanged.connect(self._update_import_enabled)
        self.folder_edit.textChanged.connect(self._update_import_enabled)
        self._update_import_enabled()

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Select the folder to import"))
        if path:
            self.folder_edit.setText(path)

    def _update_import_enabled(self) -> None:
        # The batch identifier is mandatory; a source folder must be picked too.
        enabled = bool(self.batch_id_edit.text().strip()) and bool(self.folder_edit.text().strip())
        self.import_button.setEnabled(enabled)

    def accept(self) -> None:
        requested = self.batch_id_edit.text().strip()
        self._resolved_batch_id = resolve_batch_id(requested, self._registry)
        if self._resolved_batch_id != requested:
            QMessageBox.information(
                self,
                _("Import batch"),
                _("The identifier \"{requested}\" already exists. Batch imported as \"{resolved}\".").format(
                    requested=requested, resolved=self._resolved_batch_id
                ),
            )
        super().accept()

    @property
    def source_folder(self) -> Path:
        return Path(self.folder_edit.text())

    @property
    def batch_id(self) -> str:
        """The collision-resolved identifier (valid after `accept()`)."""
        return self._resolved_batch_id or self.batch_id_edit.text().strip()

    @property
    def keep_in_place(self) -> bool:
        return self.keep_in_place_checkbox.isChecked()
