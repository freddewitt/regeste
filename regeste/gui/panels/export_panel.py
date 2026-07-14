"""Export tab — 12 per-piece formats + the corpus-level review journal.

Reads the pivot corpus (`regeste.pivot.load_corpus`) for the current project;
writes nothing to the pivot itself (exporters are read-only, spec export_instruct.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from regeste.export import PIVOT_EXPORTERS, export_review_journal
from regeste.i18n import _
from regeste.pivot import load_corpus

from ..worker import ExportWorker, start_worker

logger = logging.getLogger(__name__)

# Labels are functions (not pre-computed strings) so they translate at
# widget-construction time, not at module-import time (spec §11: language is only
# known once the app has read the project/OS config, after this module loads).
FORMAT_LABELS: dict[str, callable] = {
    "ead": lambda: _("EAD (XML)"),
    "dc": lambda: _("Dublin Core (XML)"),
    "mets": lambda: _("METS/PREMIS"),
    "csv_light": lambda: _("CSV (light)"),
    "csv_full": lambda: _("CSV (full)"),
    "xlsx": lambda: _("XLSX"),
    "zip": lambda: _("ZIP"),
    "markdown": lambda: _("Markdown"),
    "markdown_obsidian": lambda: _("Markdown (Obsidian)"),
    "sqlite": lambda: _("SQLite"),
    "html": lambda: _("HTML"),
    "pdf": lambda: _("Consultation PDF"),
}

# key -> (label function, exporter function, output name). The exporter registry
# is shared with the CLI (regeste.export.PIVOT_EXPORTERS) so formats are defined once.
FORMAT_SPECS: dict[str, tuple[callable, callable, str]] = {
    key: (FORMAT_LABELS[key], exporter, output_name)
    for key, (exporter, output_name) in PIVOT_EXPORTERS.items()
}


class ExportPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_dir: Path | None = None
        self._thread: QThread | None = None
        self._worker: ExportWorker | None = None
        self._build_ui()
        self.on_project_changed(None)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        target_group = QGroupBox(_("Destination folder"))
        target_layout = QHBoxLayout(target_group)
        self.target_dir_edit = QLineEdit()
        browse_button = QPushButton(_("Browse..."))
        browse_button.clicked.connect(self._browse_target_dir)
        target_layout.addWidget(self.target_dir_edit)
        target_layout.addWidget(browse_button)
        layout.addWidget(target_group)

        formats_group = QGroupBox(_("Formats"))
        formats_layout = QVBoxLayout(formats_group)
        self.format_checkboxes: dict[str, QCheckBox] = {}
        for key, (label_fn, _fn, _name) in FORMAT_SPECS.items():
            checkbox = QCheckBox(label_fn())
            self.format_checkboxes[key] = checkbox
            formats_layout.addWidget(checkbox)
        self.validated_only_checkbox = QCheckBox(_("Validated pieces only"))
        formats_layout.addWidget(self.validated_only_checkbox)
        layout.addWidget(formats_group)

        buttons_row = QHBoxLayout()
        self.export_button = QPushButton(_("Export"))
        self.export_button.clicked.connect(self._on_export_clicked)
        self.journal_button = QPushButton(_("Export review journal"))
        self.journal_button.clicked.connect(self._on_journal_clicked)
        buttons_row.addWidget(self.export_button)
        buttons_row.addWidget(self.journal_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        layout.addWidget(QLabel(_("Log")))
        self.log_list = QListWidget()
        layout.addWidget(self.log_list)

    def _browse_target_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Select the destination folder"))
        if path:
            self.target_dir_edit.setText(path)

    # --- Project synchronisation --------------------------------------------------

    def on_project_changed(self, source_dir: Path | None) -> None:
        self._source_dir = source_dir
        self.export_button.setEnabled(source_dir is not None)
        self.journal_button.setEnabled(source_dir is not None)
        if source_dir is not None and not self.target_dir_edit.text():
            self.target_dir_edit.setText(str(source_dir / "exports"))

    # --- Export orchestration -------------------------------------------------------

    def _target_dir(self) -> Path:
        return Path(self.target_dir_edit.text() or str(self._source_dir / "exports"))

    def _on_export_clicked(self) -> None:
        if self._source_dir is None:
            return
        selected = [key for key, checkbox in self.format_checkboxes.items() if checkbox.isChecked()]
        if not selected:
            QMessageBox.information(self, _("Export"), _("Select at least one format."))
            return

        pieces = load_corpus(self._source_dir)
        if not pieces:
            QMessageBox.information(self, _("Export"), _("No pivot data found for this project yet."))
            return

        validated_only = self.validated_only_checkbox.isChecked()
        target_dir = self._target_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        jobs = []
        for key in selected:
            label_fn, exporter, output_name = FORMAT_SPECS[key]
            output_path = target_dir / output_name
            jobs.append(
                (label_fn(), lambda exporter=exporter, output_path=output_path: exporter(
                    pieces, output_path, validated_only=validated_only
                ))
            )
        self._run_jobs(jobs)

    def _on_journal_clicked(self) -> None:
        if self._source_dir is None:
            return
        pieces = load_corpus(self._source_dir)
        if not pieces:
            QMessageBox.information(self, _("Export"), _("No pivot data found for this project yet."))
            return
        target_dir = self._target_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / "journal_de_revue.xlsx"
        self._run_jobs([(_("Review journal"), lambda: export_review_journal(pieces, output_path))])

    def _run_jobs(self, jobs: list[tuple[str, callable]]) -> None:
        self.log_list.clear()
        self.progress_bar.setMaximum(len(jobs))
        self.progress_bar.setValue(0)
        self.export_button.setEnabled(False)
        self.journal_button.setEnabled(False)

        self._worker = ExportWorker(jobs)
        self._thread = start_worker(self._worker)
        self._worker.progress.connect(self._on_job_done)
        self._worker.finished.connect(self._on_export_finished)
        self._worker.failed.connect(self._on_export_failed)
        self._thread.start()

    def _on_job_done(self, label: str) -> None:
        self.progress_bar.setValue(self.progress_bar.value() + 1)
        self.log_list.addItem(f"{label} - OK")
        logger.info(f"{label} - OK")

    def _on_export_finished(self, written: list) -> None:
        self._finish_run()
        self.log_list.addItem(_("Export complete."))
        logger.info(_("Export complete."))
        for path in written:
            self.log_list.addItem(f"  {path}")
            logger.info(f"  {path}")

    def _on_export_failed(self, message: str) -> None:
        self._finish_run()
        self.log_list.addItem(_("Export failed: {error}").format(error=message))
        logger.error(_("Export failed: {error}").format(error=message))

    def _finish_run(self) -> None:
        if self._thread is not None:
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self.export_button.setEnabled(self._source_dir is not None)
        self.journal_button.setEnabled(self._source_dir is not None)
