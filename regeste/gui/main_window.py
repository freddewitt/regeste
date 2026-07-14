"""Main workflow screen (spec §7.1) — a single run: source/output, mode, live progress/costs.

Everything provider/model/preprocessing/costs-table/workers related lives in
`SettingsDialog` instead (spec §8) — this screen only drives one run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from regeste.core.costs import CostTracker, DEFAULT_RATES, Rate, estimate_before_run
from regeste.core.export import ExportOptions, KNOWN_FORMATS, export_registry
from regeste.core.imaging import PreprocessOptions, ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import FileEntry, Registry
from regeste.core.transcriber import DEFAULT_SYSTEM_PROMPT, ProgressState, Transcriber, create_provider
from regeste.i18n import LANGUAGE_NAMES, _, format_cost, is_rtl, set_language
from regeste.pivot import build_pieces_from_registry, load_piece as load_pivot_piece, save_piece as save_pivot_piece

from .panels import ExportPanel, LogPanel, QtLogHandler, ReviewPanel, TranslationPanel
from .panels.log_panel import LOGGER_NAME

logger = logging.getLogger(__name__)
from .settings_dialog import SettingsDialog
from .worker import ModelFetchWorker, TranscriptionWorker, start_worker

# Same list/sync logic as regeste/cli/app.py — kept in sync deliberately (spec: opening
# an existing project must resync new files identically regardless of the front-end).
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".gif",
}


def _list_images(source_dir: Path) -> list[str]:
    return sorted(
        p.name for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _sync_new_files(registry: Registry, source_dir: Path) -> None:
    for name in _list_images(source_dir):
        if name not in registry.files:
            registry.files[name] = FileEntry()


def _confirm_overwrite(parent: QWidget) -> bool:
    """Asks before `Registry.new()` erases an existing project (AGENTS.md: never silent)."""
    answer = QMessageBox.question(
        parent,
        _("Confirm"),
        _("A project already exists in this folder - starting a new one will erase its progress. Continue?"),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


class MainWindow(QMainWindow):
    # Emitted with the current source_dir (or None) whenever the pivot corpus
    # for a project may have changed - opened, resumed, or a run just finished
    # seeding new pieces. The Export/Review/Translation tabs resync from this.
    project_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_("Regeste"))
        # Clamp the default size to the available screen so the window never
        # opens taller/wider than the display (small laptops).
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        width = 920 if available is None else min(920, available.width())
        height = 720 if available is None else min(720, available.height())
        self.resize(width, height)

        self._registry: Registry | None = None
        self._config: ProjectConfig | None = None

        # Settings-owned state (spec §8), defaulted here and edited via SettingsDialog.
        self._provider_config = ProviderConfig(kind="claude", model="")
        # Separate translation provider kept even while "same as OCR" is on.
        self._translation_provider_config: ProviderConfig | None = None
        self._translation_same_as_ocr = True
        self._translation_prompt: str | None = None
        self._preprocessing = PreprocessOptions()
        self._resize_options = ResizeOptions()
        self._forced_language: str | None = None
        self._system_prompt: str | None = None
        self._rates: dict[str, Rate] = dict(DEFAULT_RATES)
        self._spend_ceiling: float | None = None
        self._workers = 4
        self._ui_language: str | None = None

        self._transcriber: Transcriber | None = None
        self._thread: QThread | None = None
        self._worker: TranscriptionWorker | None = None
        self._validate_thread: QThread | None = None
        self._validate_worker: ModelFetchWorker | None = None

        self._build_ui()
        self._setup_logging()

    def _setup_logging(self) -> None:
        app_logger = logging.getLogger(LOGGER_NAME)
        for handler in [h for h in app_logger.handlers if isinstance(h, QtLogHandler)]:
            app_logger.removeHandler(handler)
        self._log_handler = QtLogHandler()
        self._log_handler.setLevel(logging.INFO)
        self._log_handler.emitter.message.connect(self.log_panel.append_message)
        app_logger.addHandler(self._log_handler)
        app_logger.setLevel(logging.INFO)

    # --- UI construction -----------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(4, 4, 4, 4)

        language_row = QHBoxLayout()
        language_row.addStretch()
        language_row.addWidget(QLabel(_("Language")))
        self.language_combo = QComboBox()
        self.language_combo.addItem(_("Automatic (system language)"), None)
        for code, native_name in LANGUAGE_NAMES.items():
            self.language_combo.addItem(native_name, code)
        index = self.language_combo.findData(self._ui_language)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)
        self.language_combo.currentIndexChanged.connect(self._on_language_selector_changed)
        language_row.addWidget(self.language_combo)
        outer_layout.addLayout(language_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._scrollable(self._build_transcription_tab()), _("Transcription"))

        self.export_panel = ExportPanel()
        self.tabs.addTab(self._scrollable(self.export_panel), _("Export"))
        self.review_panel = ReviewPanel()
        self.tabs.addTab(self._scrollable(self.review_panel), _("Review"))
        self.translation_panel = TranslationPanel()
        self.translation_panel.translation_prompt_changed.connect(self._on_translation_prompt_changed)
        self.tabs.addTab(self._scrollable(self.translation_panel), _("Translation"))
        self._push_translation_context()
        self.log_panel = LogPanel()
        self.tabs.addTab(self._scrollable(self.log_panel), _("Logs"))

        self.project_changed.connect(self.export_panel.on_project_changed)
        self.project_changed.connect(self.review_panel.on_project_changed)
        self.project_changed.connect(self.translation_panel.on_project_changed)

        outer_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

    @staticmethod
    def _scrollable(widget: QWidget) -> QScrollArea:
        # Wrap a tab page so the window can be smaller than the page's natural
        # height (the content scrolls instead of forcing the window taller than
        # the screen).
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(widget)
        return area

    def _build_transcription_tab(self) -> QWidget:
        central = QWidget()
        layout = QVBoxLayout(central)

        identity_group = QGroupBox(_("Project"))
        form = QGridLayout(identity_group)
        row = 0

        form.addWidget(QLabel(_("Project name")), row, 0)
        self.project_name_edit = QLineEdit()
        form.addWidget(self.project_name_edit, row, 1)
        row += 1

        form.addWidget(QLabel(_("Source folder")), row, 0)
        self.source_dir_edit = QLineEdit()
        self.source_dir_edit.setReadOnly(True)
        browse_source_button = QPushButton(_("Browse..."))
        browse_source_button.clicked.connect(self._browse_source_dir)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_dir_edit)
        source_row.addWidget(browse_source_button)
        form.addLayout(source_row, row, 1)
        row += 1

        form.addWidget(QLabel(_("Output folder")), row, 0)
        self.output_dir_edit = QLineEdit()
        browse_output_button = QPushButton(_("Browse..."))
        browse_output_button.clicked.connect(self._browse_output_dir)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(browse_output_button)
        form.addLayout(output_row, row, 1)
        layout.addWidget(identity_group)

        export_group = QGroupBox(_("Export"))
        export_layout = QHBoxLayout(export_group)
        self.combined_checkbox = QCheckBox(_("Combined (single file)"))
        self.combined_checkbox.setChecked(True)
        self.per_file_checkbox = QCheckBox(_("Per file"))
        self.per_file_checkbox.setChecked(True)
        export_layout.addWidget(self.combined_checkbox)
        export_layout.addWidget(self.per_file_checkbox)
        export_layout.addSpacing(24)
        self.format_checkboxes: dict[str, QCheckBox] = {}
        for fmt in KNOWN_FORMATS:
            checkbox = QCheckBox(fmt)
            checkbox.setChecked(fmt in ("md", "json"))
            self.format_checkboxes[fmt] = checkbox
            export_layout.addWidget(checkbox)
        export_layout.addStretch()
        layout.addWidget(export_group)

        mode_group = QGroupBox(_("Mode"))
        mode_layout = QHBoxLayout(mode_group)
        self.new_mode_radio = QRadioButton(_("New"))
        self.resume_mode_radio = QRadioButton(_("Resume"))
        self.new_mode_radio.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.new_mode_radio)
        self._mode_group.addButton(self.resume_mode_radio)
        mode_layout.addWidget(self.new_mode_radio)
        mode_layout.addWidget(self.resume_mode_radio)
        mode_layout.addStretch()
        layout.addWidget(mode_group)

        controls_row = QHBoxLayout()
        self.launch_button = QPushButton(_("Launch"))
        self.launch_button.setDefault(True)
        self.launch_button.clicked.connect(self._on_launch_clicked)
        self.stop_button = QPushButton(_("Stop"))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        self.settings_button = QPushButton(_("Settings..."))
        self.settings_button.clicked.connect(self._open_settings)
        controls_row.addWidget(self.launch_button)
        controls_row.addWidget(self.stop_button)
        controls_row.addStretch()
        controls_row.addWidget(self.settings_button)
        layout.addLayout(controls_row)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("0 / 0")
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        costs_group = QGroupBox(_("Costs"))
        costs_layout = QGridLayout(costs_group)
        costs_layout.addWidget(QLabel(_("Current file")), 0, 0)
        self.file_cost_label = QLabel("-")
        costs_layout.addWidget(self.file_cost_label, 0, 1)
        costs_layout.addWidget(QLabel(_("Total")), 0, 2)
        self.total_cost_label = QLabel("0.00")
        costs_layout.addWidget(self.total_cost_label, 0, 3)
        costs_layout.addWidget(QLabel(_("Projected")), 1, 0)
        self.projected_cost_label = QLabel(_("not enough data yet"))
        costs_layout.addWidget(self.projected_cost_label, 1, 1)
        costs_layout.addWidget(QLabel(_("Range (min/max per file)")), 1, 2)
        self.projected_range_label = QLabel("-")
        costs_layout.addWidget(self.projected_range_label, 1, 3)
        layout.addWidget(costs_group)
        layout.addStretch()

        return central

    # --- Folder selection / project loading -----------------------------------------

    def _browse_source_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Select the source folder"))
        if path:
            self.set_source_dir(Path(path))

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Select the output folder"))
        if path:
            self.output_dir_edit.setText(path)

    def set_source_dir(self, path: Path) -> None:
        """Selects the source folder; restores an existing project's state if found."""
        self.source_dir_edit.setText(str(path))
        registry = Registry.load(path)
        self._registry = registry
        if registry is not None:
            self.resume_mode_radio.setChecked(True)
            self._apply_config(ProjectConfig.from_meta(registry.meta))
        else:
            self.new_mode_radio.setChecked(True)
        self._sync_pivot_and_notify(path)

    def _sync_pivot_and_notify(self, source_dir: Path) -> None:
        """Seeds pivot pieces for any newly-transcribed file, then tells the
        Export/Review/Translation tabs to reload - never touches a piece that
        already has a pivot file, so review/translation progress is preserved.
        """
        if self._registry is not None:
            for piece in build_pieces_from_registry(self._registry, source_dir):
                if load_pivot_piece(source_dir, piece.id) is None:
                    save_pivot_piece(source_dir, piece)
        self._push_translation_context()
        self.project_changed.emit(source_dir)

    def _apply_config(self, config: ProjectConfig) -> None:
        """Pushes a restored `ProjectConfig` into every field, main screen and Settings."""
        self.project_name_edit.setText(config.project_name)
        self.output_dir_edit.setText(str(config.output_dir))
        self.combined_checkbox.setChecked(config.export.single_file)
        self.per_file_checkbox.setChecked(config.export.per_file)
        for fmt, checkbox in self.format_checkboxes.items():
            checkbox.setChecked(fmt in config.export.formats)
        self._provider_config = config.provider
        self._preprocessing = config.preprocessing
        self._resize_options = config.resize
        self._forced_language = config.forced_language
        self._system_prompt = config.system_prompt
        self._rates = config.rates
        self._spend_ceiling = config.spend_ceiling
        self._workers = config.workers
        self._ui_language = config.ui_language
        self._translation_provider_config = config.translation_provider
        self._translation_same_as_ocr = config.translation_same_as_ocr
        self._translation_prompt = config.translation_prompt
        self._push_translation_context()

    # --- Settings dialog -------------------------------------------------------------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self,
            provider_config=self._provider_config,
            preprocessing=self._preprocessing,
            resize=self._resize_options,
            forced_language=self._forced_language,
            system_prompt=self._system_prompt or DEFAULT_SYSTEM_PROMPT,
            rates=self._rates,
            spend_ceiling=self._spend_ceiling,
            workers=self._workers,
            ui_language=self._ui_language,
            translation_provider=self._translation_provider_config,
            translation_same_as_ocr=self._translation_same_as_ocr,
        )
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self._provider_config = dialog.get_provider_config()
            self._preprocessing = dialog.get_preprocessing()
            self._resize_options = dialog.get_resize()
            self._forced_language = dialog.get_forced_language()
            self._system_prompt = dialog.get_system_prompt()
            self._rates = dialog.get_rates()
            self._spend_ceiling = dialog.get_spend_ceiling()
            self._workers = dialog.get_workers()
            self._translation_provider_config = dialog.get_translation_provider()
            self._translation_same_as_ocr = dialog.get_translation_same_as_ocr()
            self._push_translation_context()
            self._persist_meta()
            self._apply_ui_language(dialog.get_ui_language())

    def _on_language_selector_changed(self, index: int) -> None:
        self._apply_ui_language(self.language_combo.itemData(index))

    def _is_busy(self) -> bool:
        """True while a run/export/translation is in flight - rebuilding the UI
        underneath a live QThread's signal connections would crash it."""
        return (
            self._thread is not None
            or self.export_panel._thread is not None
            or self.translation_panel._thread is not None
        )

    def _apply_ui_language(self, new_language: str | None) -> None:
        """Switches the gettext catalog, layout direction and rebuilds the UI so
        every already-built widget picks up the new language immediately - no
        restart required (spec §11.2/§11.3)."""
        if new_language == self._ui_language:
            return
        if self._is_busy():
            QMessageBox.information(
                self,
                _("Interface language"),
                _("Please wait for the current operation to finish before switching language."),
            )
            index = self.language_combo.findData(self._ui_language)
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(index if index >= 0 else 0)
            self.language_combo.blockSignals(False)
            return
        self._ui_language = new_language
        set_language(new_language)
        QApplication.instance().setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        )
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Reconstructs every tab so its widget text picks up the new language.

        Only the widgets are torn down - project/settings state lives in ivars
        (`self._registry`, `self._provider_config`, ...) untouched by this, so
        it's just re-read into the fresh widgets plus a `project_changed` replay
        for the Export/Review/Translation tabs.
        """
        state = {
            "project_name": self.project_name_edit.text(),
            "source_dir": self.source_dir_edit.text(),
            "output_dir": self.output_dir_edit.text(),
            "combined": self.combined_checkbox.isChecked(),
            "per_file": self.per_file_checkbox.isChecked(),
            "formats": {fmt: cb.isChecked() for fmt, cb in self.format_checkboxes.items()},
            "resume_mode": self.resume_mode_radio.isChecked(),
        }
        log_text = self.log_panel.log_view.toPlainText()

        self._build_ui()
        self._setup_logging()

        self.project_name_edit.setText(state["project_name"])
        self.source_dir_edit.setText(state["source_dir"])
        self.output_dir_edit.setText(state["output_dir"])
        self.combined_checkbox.setChecked(state["combined"])
        self.per_file_checkbox.setChecked(state["per_file"])
        for fmt, checked in state["formats"].items():
            if fmt in self.format_checkboxes:
                self.format_checkboxes[fmt].setChecked(checked)
        if state["resume_mode"]:
            self.resume_mode_radio.setChecked(True)
        else:
            self.new_mode_radio.setChecked(True)
        if log_text:
            self.log_panel.log_view.setPlainText(log_text)

        if state["source_dir"]:
            self.project_changed.emit(Path(state["source_dir"]))

    # --- Run orchestration -----------------------------------------------------------

    def _current_export_options(self) -> ExportOptions:
        formats = frozenset(fmt for fmt, checkbox in self.format_checkboxes.items() if checkbox.isChecked())
        return ExportOptions(
            formats=formats,
            single_file=self.combined_checkbox.isChecked(),
            per_file=self.per_file_checkbox.isChecked(),
        )

    def _build_project_config(self, source_dir: Path) -> ProjectConfig:
        return ProjectConfig(
            project_name=self.project_name_edit.text() or source_dir.name,
            source_dir=source_dir,
            output_dir=Path(self.output_dir_edit.text() or str(source_dir)),
            provider=self._provider_config,
            preprocessing=self._preprocessing,
            resize=self._resize_options,
            forced_language=self._forced_language,
            system_prompt=self._system_prompt,
            export=self._current_export_options(),
            rates=self._rates,
            spend_ceiling=self._spend_ceiling,
            workers=self._workers,
            ui_language=self._ui_language,
            translation_provider=self._translation_provider_config,
            translation_same_as_ocr=self._translation_same_as_ocr,
            translation_prompt=self._translation_prompt,
        )

    def _persist_meta(self) -> None:
        """Re-save the project config into regeste.json (when a project is open)."""
        if self._registry is not None:
            self._registry.meta = self._build_project_config(self._registry.source_dir).to_meta()
            self._registry.save()

    def _effective_translation_provider(self) -> ProviderConfig | None:
        if self._translation_same_as_ocr:
            return self._provider_config
        return self._translation_provider_config

    def _push_translation_context(self) -> None:
        self.translation_panel.set_effective_translation_provider(
            self._effective_translation_provider()
        )
        self.translation_panel.set_translation_prompt(self._translation_prompt)

    def _on_translation_prompt_changed(self, prompt) -> None:
        self._translation_prompt = prompt
        self._persist_meta()

    def _on_launch_clicked(self) -> None:
        source_text = self.source_dir_edit.text().strip()
        if not source_text:
            QMessageBox.critical(self, _("Error"), _("Choose a source folder first."))
            return
        source_dir = Path(source_text)
        if not source_dir.is_dir():
            QMessageBox.critical(self, _("Error"), _("Not a folder: {path}").format(path=source_dir))
            return

        mode: Literal["new", "resume"] = "resume" if self.resume_mode_radio.isChecked() else "new"
        existing = Registry.load(source_dir)

        if mode == "resume":
            if existing is None:
                QMessageBox.critical(
                    self, _("Error"), _("No existing project found in this folder to resume.")
                )
                return
            registry = existing
            config = ProjectConfig.from_meta(registry.meta)
            self._apply_config(config)
            _sync_new_files(registry, source_dir)
            registry.save()
            self._registry = registry
            self._config = config
            # Resume mode only: the provider persisted in regeste.json may no longer
            # work (revoked key, local server down) - "new" mode already validates it
            # implicitly via the provider/model pickers in Settings, so this would be
            # redundant there. Runs off the GUI thread, same mechanism as fetching
            # models in Settings, to avoid blocking the UI on a network call.
            self._validate_provider_then_resume(registry, config)
            return

        if existing is not None and not _confirm_overwrite(self):
            return
        config = self._build_project_config(source_dir)
        registry = Registry.new(source_dir, meta=config.to_meta(), file_names=_list_images(source_dir))

        self._registry = registry
        self._config = config
        self._start_run(registry, mode, config)

    def _validate_provider_then_resume(self, registry: Registry, config: ProjectConfig) -> None:
        self.launch_button.setEnabled(False)
        self._validate_worker = ModelFetchWorker(config.provider)
        self._validate_thread = start_worker(self._validate_worker)
        self._validate_worker.succeeded.connect(
            lambda models: self._on_provider_validated(registry, config, models)
        )
        self._validate_worker.failed.connect(self._on_provider_validation_failed)
        self._validate_thread.start()

    def _on_provider_validated(self, registry: Registry, config: ProjectConfig, models: list) -> None:
        self.launch_button.setEnabled(True)
        if not models:
            QMessageBox.critical(self, _("Error"), _("No vision model found for this provider."))
            return
        self._start_run(registry, "resume", config)

    def _on_provider_validation_failed(self, message: str) -> None:
        self.launch_button.setEnabled(True)
        QMessageBox.critical(self, _("Error"), _("Provider unavailable: {error}").format(error=message))

    def _start_run(self, registry: Registry, mode: Literal["new", "resume"], config: ProjectConfig) -> None:
        file_list = registry.files_to_process(mode)
        self.progress_bar.setMaximum(max(len(file_list), 1))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {len(file_list)}")

        if not file_list:
            logger.info(_("Nothing to process."))
            self._export_and_log()
            self._sync_pivot_and_notify(Path(self.source_dir_edit.text()))
            return

        if not self._confirm_run(file_list, config):
            self.launch_button.setEnabled(True)
            return

        provider = create_provider(config.provider)
        self._transcriber = Transcriber(config, provider, system_prompt=config.system_prompt)
        cost_tracker = CostTracker(rates=config.rates)

        self._worker = TranscriptionWorker(self._transcriber, registry, mode, cost_tracker)
        self._thread = start_worker(self._worker)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self._thread.start()

        self.launch_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _confirm_run(self, file_list: list[str], config: ProjectConfig) -> bool:
        """Rough cost estimate before launch (spec §6/§8), GUI counterpart of the
        CLI's "Files to process / Rough cost estimate / Start now?" prompt.
        """
        estimate_tracker = CostTracker(rates=config.rates)
        # Same heuristic as the CLI (spec §6): a plausible "average" file, not a
        # measurement - real costs are only known once the run is under way.
        average_cost = estimate_tracker.file_cost(config.provider.model, 1500, 500)
        estimate = estimate_before_run(len(file_list), average_cost)
        message = "{files}\n{cost}".format(
            files=_("Files to process: {count}").format(count=len(file_list)),
            cost=_("Rough cost estimate (heuristic, not a measurement): ~{amount}").format(
                amount=format_cost(estimate)
            ),
        )
        answer = QMessageBox.question(
            self,
            _("Confirm"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_progress(self, state: ProgressState) -> None:
        entry = self._registry.files.get(state.file_name) if self._registry else None
        status = entry.status if entry else "error"
        message = f"{state.file_name} - {status}"
        if entry and entry.error_message:
            message += f" - {entry.error_message}"
        if status == "ok":
            logger.info(message)
        else:
            logger.error(message)

        self.progress_bar.setMaximum(max(state.total, 1))
        self.progress_bar.setValue(state.processed)
        self.progress_label.setText(f"{state.processed} / {state.total}")

        self.file_cost_label.setText(format_cost(entry.cost) if entry else "-")
        self.total_cost_label.setText(format_cost(state.total_cost))
        if state.projection is not None:
            self.projected_cost_label.setText(f"~{format_cost(state.projection.projected_cost)}")
            self.projected_range_label.setText(
                f"{format_cost(state.projection.min_cost_per_file)} - {format_cost(state.projection.max_cost_per_file)}"
            )
        else:
            self.projected_cost_label.setText(_("not enough data yet"))
            self.projected_range_label.setText("-")

    def _on_run_finished(self) -> None:
        self._finish_run()
        self._export_and_log()
        self._sync_pivot_and_notify(Path(self.source_dir_edit.text()))

    def _on_run_failed(self, message: str) -> None:
        self._finish_run()
        logger.error(_("Run failed: {error}").format(error=message))

    def _finish_run(self) -> None:
        if self._thread is not None:
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._transcriber = None
        self.launch_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _export_and_log(self) -> None:
        if self._config is None or self._registry is None:
            return
        source_dir = Path(self.source_dir_edit.text())
        written = export_registry(
            self._registry,
            source_dir=source_dir,
            output_dir=self._config.output_dir,
            project_name=self._config.project_name,
            options=self._config.export,
        )
        logger.info(_("Exported files:"))
        for path in written:
            logger.info(f"  {path}")

    def _on_stop_clicked(self) -> None:
        # Thread-safe: `request_stop()` only sets a `threading.Event` (spec §10).
        if self._transcriber is not None:
            self._transcriber.request_stop()
