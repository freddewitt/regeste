"""Main workflow screen (spec §7.1) — a single run: source/output, mode, live progress/costs.

Everything provider/model/preprocessing/costs-table/workers related lives in the
Settings tab (`panels.SettingsPanel`) instead — this screen only drives one run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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
from regeste.core.imaging import IMAGE_EXTENSIONS, PreprocessOptions, ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import FileEntry, Registry, SourceInfo
from regeste.core.transcriber import ProgressState, Transcriber, create_provider
from regeste.core.transcription_mode import TranscriptionMode
from regeste.i18n import LANGUAGE_NAMES, _, format_cost, is_rtl, set_language
from regeste.pivot import build_pieces_from_registry, load_corpus, load_piece as load_pivot_piece, save_piece as save_pivot_piece

from .import_dialog import BatchImportDialog
from .import_worker import BatchImportWorker, list_importable_images
from .panels import ExportPanel, LogPanel, QtLogHandler, ReviewPanel, SettingsPanel, TranslationPanel
from .panels.export_panel import FORMAT_SPECS
from .panels.log_panel import LOGGER_NAME
from .worker import ExportWorker, ModelFetchWorker, ProjectArchiveWorker, TranscriptionWorker, start_worker

logger = logging.getLogger(__name__)

# Busy indicator next to the progress bar while a run is active - independent of
# per-file progress, so the UI never looks idle during the (sometimes long) wait
# for the first provider response.
_SPINNER_FRAMES = "◐◓◑◒"


def _list_images(source_dir: Path) -> list[str]:
    return sorted(
        p.name for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _sync_new_files(registry: Registry, source_dir: Path) -> None:
    """Add images that showed up in `source_dir` since the last session (spec §9)."""
    existing_displays = {registry.display_name(k) for k in registry.files}
    batch_id = source_dir.name
    for name in _list_images(source_dir):
        if name not in existing_displays:
            prefixed_name = f"{batch_id}_{name}"
            registry.files[prefixed_name] = FileEntry(
                source=SourceInfo(
                    batch_id=batch_id,
                    physical_path=str(source_dir / name),
                    imported=False,
                ),
            )


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

        # Settings-owned state (spec §8), defaulted here and edited via the Settings tab.
        self._provider_config = ProviderConfig(kind="claude", model="")
        # Separate translation provider kept even while "same as OCR" is on.
        self._translation_provider_config: ProviderConfig | None = None
        self._translation_same_as_ocr = True
        self._translation_prompt: str | None = None
        self._translation_parallel = False
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
        self._pending_resume_registry: Registry | None = None
        self._pending_resume_config: ProjectConfig | None = None
        self._import_thread: QThread | None = None
        self._import_worker: BatchImportWorker | None = None
        self._import_batch_id = ""
        self._archive_thread: QThread | None = None
        self._archive_worker: ProjectArchiveWorker | None = None
        self._menu_export_thread: QThread | None = None
        self._menu_export_worker: ExportWorker | None = None
        self._menu_export_written: list = []
        self._in_flight_files: set[str] = set()
        self._spinner_frame = 0
        self._corpus_cache: list | None = None

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
        self.log_panel.verbose_toggled.connect(self._on_verbose_toggled)

    def _on_verbose_toggled(self, verbose: bool) -> None:
        # Verbose reveals the exhaustive DEBUG-level diagnostic logs added throughout
        # core/ (providers, transcriber, imaging, registry) for troubleshooting — the
        # logger/handler level is the actual gate, not a display-side filter, so DEBUG
        # records aren't even formatted/collected while unchecked.
        level = logging.DEBUG if verbose else logging.INFO
        logging.getLogger(LOGGER_NAME).setLevel(level)
        self._log_handler.setLevel(level)

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
        self.export_panel = ExportPanel()
        self.tabs.addTab(self._scrollable(self._build_transcription_tab()), _("Transcription"))

        self.review_panel = ReviewPanel()
        self._review_tab_index = self.tabs.addTab(self._scrollable(self.review_panel), _("Review"))

        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_saved.connect(self._on_settings_saved)

        self.translation_panel = TranslationPanel(settings_panel=self.settings_panel)
        self.translation_panel.translation_prompt_changed.connect(self._on_translation_prompt_changed)
        self.tabs.addTab(self._scrollable(self.translation_panel), _("Translation"))
        self._push_translation_context()
        self.tabs.addTab(self._scrollable(self.export_panel), _("Export archive"))
        self._settings_tab_widget = self._scrollable(self.settings_panel)
        self.tabs.addTab(self._settings_tab_widget, _("Settings"))
        self._push_settings_context()
        self.log_panel = LogPanel()
        self.tabs.addTab(self._scrollable(self.log_panel), _("Log"))

        self._previous_tab_index = 0
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.project_changed.connect(self.export_panel.on_project_changed)
        self.project_changed.connect(self.review_panel.on_project_changed)
        self.project_changed.connect(self.translation_panel.on_project_changed)

        outer_layout.addWidget(self.tabs)
        self.setCentralWidget(central)
        self._setup_menu()

    def _setup_menu(self) -> None:
        # Clear first: `_build_ui()` also runs on language change (`_rebuild_ui`),
        # and `menuBar()` persists across central-widget swaps - without this each
        # rebuild would stack another "File" menu.
        menu_bar = self.menuBar()
        menu_bar.clear()
        file_menu = menu_bar.addMenu(_("File"))

        new_action = file_menu.addAction(_("New project…"))
        new_action.triggered.connect(self._on_menu_new_project)

        open_action = file_menu.addAction(_("Open project…"))
        open_action.triggered.connect(self._on_menu_open_project)

        self.import_batch_action = file_menu.addAction(_("Import batch..."))
        self.import_batch_action.triggered.connect(self._on_import_batch_clicked)

        self.export_archive_action = file_menu.addAction(_("Export project archive..."))
        self.export_archive_action.triggered.connect(self._on_export_archive_clicked)

        file_menu.addSeparator()

        save_action = file_menu.addAction(_("Save"))
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_menu_save)

        file_menu.addSeparator()

        export_action = file_menu.addAction(_("Export project…"))
        export_action.triggered.connect(self._on_menu_export)

        file_menu.addSeparator()

        quit_action = file_menu.addAction(_("Quit Regeste"))
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(QApplication.instance().quit)

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

        # Two-column layout: left (Mode + Output mode) / right (Formats + Transcription mode).
        config_columns = QHBoxLayout()

        left_column = QVBoxLayout()
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
        left_column.addWidget(mode_group)

        output_mode_group = QGroupBox(_("Output mode"))
        output_mode_layout = QHBoxLayout(output_mode_group)
        self.combined_radio = QRadioButton(_("Combined (single file)"))
        self.per_file_radio = QRadioButton(_("Per file"))
        self.combined_radio.setChecked(True)
        self._output_mode_group = QButtonGroup(self)
        self._output_mode_group.addButton(self.combined_radio)
        self._output_mode_group.addButton(self.per_file_radio)
        output_mode_layout.addWidget(self.combined_radio)
        output_mode_layout.addWidget(self.per_file_radio)
        self.no_review_checkbox = QCheckBox(_("Without review"))
        self.no_review_checkbox.setToolTip(
            _(
                "When checked, each file is written to the output folder as soon as its "
                "transcription is done, without waiting for manual review. When unchecked "
                "(default), the app switches to the Review tab once transcription finishes."
            )
        )
        output_mode_layout.addWidget(self.no_review_checkbox)
        output_mode_layout.addStretch()
        left_column.addWidget(output_mode_group)
        left_column.addStretch()
        config_columns.addLayout(left_column)

        right_column = QVBoxLayout()
        formats_group = QGroupBox(_("Formats"))
        formats_layout = QHBoxLayout(formats_group)
        self.format_checkboxes: dict[str, QCheckBox] = {}
        for fmt in KNOWN_FORMATS:
            checkbox = QCheckBox(fmt)
            checkbox.setChecked(fmt in ("md", "json"))
            self.format_checkboxes[fmt] = checkbox
            formats_layout.addWidget(checkbox)
        formats_layout.addStretch()
        right_column.addWidget(formats_group)

        transcription_mode_group = QGroupBox(_("Transcription mode"))
        transcription_mode_layout = QVBoxLayout(transcription_mode_group)
        radios_row = QHBoxLayout()
        self.literal_radio = QRadioButton(_("Literal"))
        self.hypotheses_radio = QRadioButton(_("Hypotheses"))
        self.literal_radio.setChecked(True)
        self._transcription_mode_group = QButtonGroup(self)
        self._transcription_mode_group.addButton(self.literal_radio)
        self._transcription_mode_group.addButton(self.hypotheses_radio)
        radios_row.addWidget(self.literal_radio)
        radios_row.addWidget(self.hypotheses_radio)
        radios_row.addStretch()
        self.hypotheses_radio.toggled.connect(self._on_transcription_mode_changed)
        transcription_mode_layout.addLayout(radios_row)
        explanation = QLabel(
            _(
                "Literal: raw transcription. Hypotheses: illegible or ambiguous passages "
                "are marked with contextual [[hypotheses]], and the notation legend is "
                "included in the exports."
            )
        )
        explanation.setWordWrap(True)
        transcription_mode_layout.addWidget(explanation)
        right_column.addWidget(transcription_mode_group)
        right_column.addStretch()
        config_columns.addLayout(right_column)

        layout.addLayout(config_columns)

        controls_row = QHBoxLayout()
        self.launch_button = QPushButton(_("Launch"))
        self.launch_button.setDefault(True)
        self.launch_button.clicked.connect(self._on_launch_clicked)
        self.stop_button = QPushButton(_("Stop"))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        controls_row.addWidget(self.launch_button)
        controls_row.addWidget(self.stop_button)
        controls_row.addStretch()
        layout.addLayout(controls_row)

        progress_row = QHBoxLayout()
        self.spinner_label = QLabel("")
        self.spinner_label.setFixedWidth(20)
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("0 / 0")
        progress_row.addWidget(self.spinner_label)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(150)
        self._spinner_timer.timeout.connect(self._advance_spinner)

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

    # Output type controls are now embedded in _build_transcription_tab()

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
        self._push_cost_data()
        self._sync_pivot_and_notify(path)

    def _sync_pivot_and_notify(self, source_dir: Path) -> None:
        """Seeds pivot pieces for any newly-transcribed file, then tells the
        Export/Review/Translation tabs to reload - never touches a piece that
        already has a pivot file, so review/translation progress is preserved.
        """
        self._corpus_cache = None  # invalidate: corpus may have changed
        if self._registry is not None:
            for piece in build_pieces_from_registry(self._registry, source_dir):
                if load_pivot_piece(source_dir, piece.id) is None:
                    save_pivot_piece(source_dir, piece)
        # Push the fresh corpus to panels so they don't each reload from disk.
        corpus = self.get_corpus(force_reload=True)
        self.export_panel.set_corpus(corpus)
        self.review_panel.set_corpus(corpus)
        self.translation_panel.set_corpus(corpus)
        self._push_translation_context()
        self.project_changed.emit(source_dir)

    # --- File menu ---------------------------------------------------------------------

    def _on_menu_new_project(self) -> None:
        """Creates an empty project (no files) in a user-chosen folder."""
        if self._is_busy():
            QMessageBox.information(
                self,
                _("New project"),
                _("Please wait for the current operation to finish before changing project."),
            )
            return
        path = QFileDialog.getExistingDirectory(self, _("Select the project folder"))
        if not path:
            return
        source_dir = Path(path)
        # `Registry.new()` overwrites an existing regeste.json - never silently (AGENTS.md).
        if Registry.load(source_dir) is not None and not _confirm_overwrite(self):
            return
        config = ProjectConfig(
            project_name=source_dir.name,
            source_dir=source_dir,
            output_dir=source_dir,
            provider=self._provider_config,
        )
        self._registry = Registry.new(source_dir, meta=config.to_meta(), file_names=[])
        self._config = config
        self.source_dir_edit.setText(str(source_dir))
        self.new_mode_radio.setChecked(True)
        self._apply_config(config)
        self._push_cost_data()
        self._sync_pivot_and_notify(source_dir)
        logger.info(_("New project created in {path}").format(path=source_dir))

    def _on_menu_open_project(self) -> None:
        """Loads an existing project (folder containing a regeste.json)."""
        if self._is_busy():
            QMessageBox.information(
                self,
                _("Open project"),
                _("Please wait for the current operation to finish before changing project."),
            )
            return
        path = QFileDialog.getExistingDirectory(self, _("Select the project folder"))
        if not path:
            return
        source_dir = Path(path)
        registry = Registry.load(source_dir)
        if registry is None:
            QMessageBox.critical(
                self, _("Error"), _("No existing project found in this folder to resume.")
            )
            return
        config = ProjectConfig.from_meta(registry.meta)
        self._registry = registry
        self._config = config
        self.source_dir_edit.setText(str(source_dir))
        self.resume_mode_radio.setChecked(True)
        self._apply_config(config)
        self._push_cost_data()
        self._sync_pivot_and_notify(source_dir)
        logger.info(_("Project loaded from {path}").format(path=source_dir))

    def _on_menu_save(self) -> None:
        if self._registry is None:
            return
        # Capture any pending Settings edits too, so Ctrl+S never loses them.
        self._sync_settings_from_panel()
        self._persist_meta()
        logger.info(_("Project saved."))

    def _on_menu_export(self) -> None:
        """Manual equivalent of the automatic end-of-run export: OCR formats per the
        Transcription tab's checkboxes, plus the Export archive tab's selection."""
        if self._registry is None or self._config is None:
            QMessageBox.information(
                self, _("Export"), _("Open a project folder before exporting.")
            )
            return
        if self._is_busy():
            QMessageBox.information(
                self,
                _("Export"),
                _("Please wait for the current operation to finish before exporting."),
            )
            return
        self._sync_settings_from_panel()
        source_dir = Path(self.source_dir_edit.text())
        # Rebuild the config from the live checkboxes so the export honors the
        # current Transcription tab options, then persist it like a tab switch would.
        self._config = self._build_project_config(source_dir)
        self._persist_meta()

        ocr_written = export_registry(
            self._registry,
            source_dir=source_dir,
            output_dir=self._config.output_dir,
            project_name=self._config.project_name,
            options=self._config.export,
        )
        logger.info(_("Exported files:"))
        for path in ocr_written:
            logger.info(f"  {path}")
        self._menu_export_written = list(ocr_written)

        panel = self.export_panel
        selected = [key for key, checkbox in panel.format_checkboxes.items() if checkbox.isChecked()]
        if not selected:
            self._show_menu_export_summary(self._menu_export_written)
            return
        pieces = self.get_corpus()
        if not pieces:
            logger.info(_("No pivot data found for this project yet."))
            self._show_menu_export_summary(self._menu_export_written)
            return

        # Same job construction as `ExportPanel._on_export_clicked()`.
        validated_only = panel.validated_only_checkbox.isChecked()
        target_language = panel.language_combo.currentData()
        target_dir = panel._target_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for key in selected:
            label_fn, exporter, output_name = FORMAT_SPECS[key]
            output_path = target_dir / output_name
            jobs.append(
                (label_fn(), lambda exporter=exporter, output_path=output_path: exporter(
                    pieces, output_path, validated_only=validated_only, target_language=target_language
                ))
            )

        self.progress_bar.setMaximum(len(jobs))
        self.progress_bar.setValue(0)
        self._menu_export_worker = ExportWorker(jobs)
        self._menu_export_thread = start_worker(self._menu_export_worker)
        self._menu_export_worker.progress.connect(self._on_menu_export_progress)
        self._menu_export_worker.finished.connect(self._on_menu_export_finished)
        self._menu_export_worker.failed.connect(self._on_menu_export_failed)
        self._menu_export_thread.start()

    def _on_menu_export_progress(self, label: str) -> None:
        self.progress_bar.setValue(self.progress_bar.value() + 1)
        logger.info(f"{label} - OK")

    def _on_menu_export_finished(self, written: list) -> None:
        self._finish_menu_export()
        logger.info(_("Export complete."))
        self._show_menu_export_summary(self._menu_export_written, list(written))

    def _on_menu_export_failed(self, message: str) -> None:
        self._finish_menu_export()
        logger.error(_("Export failed: {error}").format(error=message))
        QMessageBox.critical(
            self, _("Error"), _("Export failed: {error}").format(error=message)
        )

    def _finish_menu_export(self) -> None:
        if self._menu_export_thread is not None:
            self._menu_export_thread.wait(5000)
        self._menu_export_thread = None
        self._menu_export_worker = None

    def _show_menu_export_summary(self, ocr_written: list, archive_written: list | None = None) -> None:
        # Labels reuse the tab names so the summary maps directly to their source
        # tab (Transcription's OCR checkboxes vs Export archive's pivot formats).
        sections = []
        if ocr_written:
            sections.append(_("Transcription") + " :\n" + "\n".join(f"  {path}" for path in ocr_written))
        if archive_written:
            sections.append(_("Export archive") + " :\n" + "\n".join(f"  {path}" for path in archive_written))
        QMessageBox.information(
            self,
            _("Export"),
            _("Export complete. Files written:\n{files}").format(files="\n".join(sections)),
        )

    # --- Batch import ----------------------------------------------------------------

    def _on_import_batch_clicked(self) -> None:
        if self._registry is None:
            QMessageBox.information(
                self, _("Import batch"), _("Open a project folder before importing a batch.")
            )
            return
        project_dir = Path(self.source_dir_edit.text())
        dialog = BatchImportDialog(self, project_dir=project_dir, registry=self._registry)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source_files = list_importable_images(dialog.source_folder)
        if not source_files:
            QMessageBox.information(
                self, _("Import batch"), _("No image files found in this folder.")
            )
            return
        batch_id = dialog.batch_id
        logger.info(
            _("Import: {count} image(s) from {folder} as batch \"{batch_id}\"").format(
                count=len(source_files), folder=dialog.source_folder, batch_id=batch_id
            )
        )
        self.progress_bar.setMaximum(max(len(source_files), 1))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {len(source_files)}")

        self._import_worker = BatchImportWorker(
            source_files, batch_id, project_dir, self._registry, dialog.keep_in_place
        )
        self._import_batch_id = batch_id
        self._import_thread = start_worker(self._import_worker)
        self._import_worker.progress.connect(self._on_import_progress)
        # Bound methods of MainWindow only: Qt queues them to the GUI thread. A
        # plain lambda would run inside the worker thread and `_finish_import`'s
        # `thread.wait()` would deadlock-abort (QThread waited on from itself).
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_thread.start()
        self.import_batch_action.setEnabled(False)
        self.launch_button.setEnabled(False)

    def _on_import_progress(self, done: int, total: int) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)
        self.progress_label.setText(f"{done} / {total}")

    def _on_import_finished(self, count: int) -> None:
        batch_id = self._import_batch_id
        self._finish_import()
        logger.info(
            _("Import complete: {count} new file(s) in batch \"{batch_id}\".").format(
                count=count, batch_id=batch_id
            )
        )
        QMessageBox.information(
            self,
            _("Import batch"),
            _("{count} new file(s) imported as batch \"{batch_id}\".").format(
                count=count, batch_id=batch_id
            ),
        )
        source_text = self.source_dir_edit.text().strip()
        if source_text:
            self._sync_pivot_and_notify(Path(source_text))
        self._push_cost_data()

    def _on_import_failed(self, message: str) -> None:
        self._finish_import()
        logger.error(_("Import failed: {error}").format(error=message))
        QMessageBox.critical(
            self, _("Error"), _("Import failed: {error}").format(error=message)
        )

    def _finish_import(self) -> None:
        if self._import_thread is not None:
            self._import_thread.wait(5000)
        self._import_thread = None
        self._import_worker = None
        self.import_batch_action.setEnabled(True)
        self.launch_button.setEnabled(True)

    # --- Project archive export -------------------------------------------------------

    def _on_export_archive_clicked(self) -> None:
        if self._registry is None:
            QMessageBox.information(
                self, _("Export project archive"), _("Open a project folder before exporting an archive.")
            )
            return
        default_name = f"{self.project_name_edit.text().strip() or 'regeste'}.zip"
        output_file, _filter = QFileDialog.getSaveFileName(
            self, _("Export project archive"), default_name, "Zip (*.zip)"
        )
        if not output_file:
            return
        output_path = Path(output_file)
        self.progress_bar.setMaximum(max(len(self._registry.files), 1))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0 / {len(self._registry.files)}")

        self._archive_worker = ProjectArchiveWorker(self._registry, output_path)
        self._archive_thread = start_worker(self._archive_worker)
        self._archive_worker.progress.connect(self._on_import_progress)
        self._archive_worker.finished.connect(self._on_export_archive_finished)
        self._archive_worker.failed.connect(self._on_export_archive_failed)
        self._archive_thread.start()
        self.export_archive_action.setEnabled(False)
        self.launch_button.setEnabled(False)

    def _on_export_archive_finished(self, output_path: Path) -> None:
        self._finish_export_archive()
        logger.info(_("Project archive written to {path}").format(path=output_path))
        QMessageBox.information(
            self,
            _("Export project archive"),
            _("Project archive written to {path}").format(path=output_path),
        )

    def _on_export_archive_failed(self, message: str) -> None:
        self._finish_export_archive()
        logger.error(_("Project archive export failed: {error}").format(error=message))
        QMessageBox.critical(
            self, _("Error"), _("Project archive export failed: {error}").format(error=message)
        )

    def _finish_export_archive(self) -> None:
        if self._archive_thread is not None:
            self._archive_thread.wait(5000)
        self._archive_thread = None
        self._archive_worker = None
        self.export_archive_action.setEnabled(True)
        self.launch_button.setEnabled(True)

    def _apply_config(self, config: ProjectConfig) -> None:
        """Pushes a restored `ProjectConfig` into every field, main screen and Settings."""
        self.project_name_edit.setText(config.project_name)
        self.output_dir_edit.setText(str(config.output_dir))
        # Exclusive radios: combined wins if both were set (older configs allowed both).
        self.combined_radio.setChecked(config.export.single_file)
        self.per_file_radio.setChecked(not config.export.single_file)
        self.no_review_checkbox.setChecked(config.no_review)
        for fmt, checkbox in self.format_checkboxes.items():
            checkbox.setChecked(fmt in config.export.formats)
        self.hypotheses_radio.setChecked(
            config.transcription_mode is TranscriptionMode.HYPOTHESES
        )
        self.literal_radio.setChecked(
            config.transcription_mode is not TranscriptionMode.HYPOTHESES
        )
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
        self._translation_parallel = config.translation_parallel
        self._push_translation_context()
        self._push_settings_context()

    # --- Settings tab ------------------------------------------------------------------

    def _push_settings_context(self) -> None:
        self.settings_panel.apply_config(
            provider_config=self._provider_config,
            preprocessing=self._preprocessing,
            resize=self._resize_options,
            forced_language=self._forced_language,
            system_prompt=self._system_prompt,
            transcription_mode=self._current_transcription_mode(),
            rates=self._rates,
            spend_ceiling=self._spend_ceiling,
            workers=self._workers,
            ui_language=self._ui_language,
            translation_provider=self._translation_provider_config,
            translation_same_as_ocr=self._translation_same_as_ocr,
            translation_parallel=self._translation_parallel,
        )

    def _sync_settings_from_panel(self) -> None:
        """Pull the Settings tab's current widget values into the live config.

        Called defensively (tab switch away from Settings, launch, translate)
        so a change is never silently lost just because "Save settings" wasn't
        clicked — unlike the old modal dialog, nothing forces that click in a
        permanent tab.
        """
        panel = self.settings_panel
        self._provider_config = panel.get_provider_config()
        self._preprocessing = panel.get_preprocessing()
        self._resize_options = panel.get_resize()
        self._forced_language = panel.get_forced_language()
        self._system_prompt = panel.get_system_prompt()
        self._rates = panel.get_rates()
        self._spend_ceiling = panel.get_spend_ceiling()
        self._workers = panel.get_workers()
        self._translation_provider_config = panel.get_translation_provider()
        self._translation_same_as_ocr = panel.get_translation_same_as_ocr()
        self._translation_prompt = panel.get_translation_prompt()
        self._translation_parallel = panel.get_translation_parallel()

    def _on_settings_saved(self) -> None:
        self._sync_settings_from_panel()
        self._push_translation_context()
        self._persist_meta()
        self._apply_ui_language(self.settings_panel.get_ui_language())

    def _on_tab_changed(self, index: int) -> None:
        # Leaving Settings for any other tab must apply pending changes - Launch
        # and Translate both read cached `self._provider_config`/etc, not the
        # panel's widgets directly.
        if self.tabs.widget(self._previous_tab_index) is self._settings_tab_widget and index != self._previous_tab_index:
            self._sync_settings_from_panel()
            self._push_translation_context()
            self._persist_meta()
        self._previous_tab_index = index

    def _on_language_selector_changed(self, index: int) -> None:
        self._apply_ui_language(self.language_combo.itemData(index))

    def _is_busy(self) -> bool:
        """True while a run/export/translation is in flight - rebuilding the UI
        underneath a live QThread's signal connections would crash it."""
        return (
            self._thread is not None
            or self._import_thread is not None
            or self._menu_export_thread is not None
            or self.export_panel._thread is not None
            or self.translation_panel._thread is not None
        )

    def get_corpus(self, *, force_reload: bool = False) -> list:
        """Return the pivot corpus for the current project, cached after first load.

        Panels should call this instead of ``load_corpus()`` individually.
        Pass ``force_reload=True`` after a run finishes or a project changes.
        """
        source_text = self.source_dir_edit.text().strip()
        if not source_text:
            return []
        if self._corpus_cache is not None and not force_reload:
            return self._corpus_cache
        self._corpus_cache = load_corpus(Path(source_text))
        return self._corpus_cache

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
            "combined": self.combined_radio.isChecked(),
            "hypotheses": self.hypotheses_radio.isChecked(),
            "formats": {fmt: cb.isChecked() for fmt, cb in self.format_checkboxes.items()},
            "no_review": self.no_review_checkbox.isChecked(),
            "resume_mode": self.resume_mode_radio.isChecked(),
        }
        log_text = self.log_panel.log_view.toPlainText()

        self._build_ui()
        self._setup_logging()
        # The fresh Settings panel starts empty - re-feed the Costs tab from the
        # registry (state survives the rebuild in ivars, widgets don't).
        self._push_cost_data()

        self.project_name_edit.setText(state["project_name"])
        self.source_dir_edit.setText(state["source_dir"])
        self.output_dir_edit.setText(state["output_dir"])
        self.combined_radio.setChecked(state["combined"])
        self.per_file_radio.setChecked(not state["combined"])
        self.hypotheses_radio.setChecked(state["hypotheses"])
        self.literal_radio.setChecked(not state["hypotheses"])
        for fmt, checked in state["formats"].items():
            if fmt in self.format_checkboxes:
                self.format_checkboxes[fmt].setChecked(checked)
        self.no_review_checkbox.setChecked(state["no_review"])
        if state["resume_mode"]:
            self.resume_mode_radio.setChecked(True)
        else:
            self.new_mode_radio.setChecked(True)
        if log_text:
            self.log_panel.log_view.setPlainText(log_text)

        if state["source_dir"]:
            self.project_changed.emit(Path(state["source_dir"]))

    # --- Run orchestration -----------------------------------------------------------

    def _current_transcription_mode(self) -> TranscriptionMode:
        return (
            TranscriptionMode.HYPOTHESES
            if self.hypotheses_radio.isChecked()
            else TranscriptionMode.LITERAL
        )

    def _on_transcription_mode_changed(self) -> None:
        """Keep the Settings tab's OCR prompt (dialog default + fallback when
        uncustomized) in sync with the selected mode."""
        self.settings_panel.set_transcription_mode(self._current_transcription_mode())

    def _current_export_options(self) -> ExportOptions:
        formats = frozenset(fmt for fmt, checkbox in self.format_checkboxes.items() if checkbox.isChecked())
        return ExportOptions(
            formats=formats,
            single_file=self.combined_radio.isChecked(),
            per_file=self.per_file_radio.isChecked(),
            transcription_mode=self._current_transcription_mode(),
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
            transcription_mode=self._current_transcription_mode(),
            rates=self._rates,
            spend_ceiling=self._spend_ceiling,
            workers=self._workers,
            ui_language=self._ui_language,
            translation_provider=self._translation_provider_config,
            translation_same_as_ocr=self._translation_same_as_ocr,
            translation_prompt=self._translation_prompt,
            translation_parallel=self._translation_parallel,
            no_review=self.no_review_checkbox.isChecked(),
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

    def _push_cost_data(self) -> None:
        """Refresh the Settings > Costs tab from the registry's recorded per-file
        costs (None-safe: the tab shows its empty state when no project is open)."""
        self.settings_panel.set_cost_data(self._registry)

    def _push_translation_context(self) -> None:
        self.translation_panel.set_effective_translation_provider(
            self._effective_translation_provider()
        )
        self.settings_panel.set_translation_prompt(self._translation_prompt)
        self.translation_panel.set_translation_workers(
            self._workers if self._translation_parallel else 1
        )

    def _on_translation_prompt_changed(self, prompt) -> None:
        self._translation_prompt = prompt
        self._persist_meta()

    def _on_launch_clicked(self) -> None:
        # Belt-and-braces alongside the tab-switch sync: Launch must never run
        # against a stale provider config just because Settings wasn't left first.
        self._sync_settings_from_panel()
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
        # Stashed on self (not captured in a lambda) so the `succeeded` connection
        # below stays a plain bound-method connection: Qt then resolves receiver
        # thread affinity from `self` and queues the call onto the GUI thread. A
        # lambda receiver has no such affinity, so Qt would invoke it directly on
        # the worker thread - fatal here since it leads to a QMessageBox further
        # down (`_start_run` -> `_confirm_run`), and NSWindow must be created on
        # the main thread on macOS.
        self._pending_resume_registry = registry
        self._pending_resume_config = config
        self._validate_worker = ModelFetchWorker(config.provider)
        self._validate_thread = start_worker(self._validate_worker)
        self._validate_worker.succeeded.connect(self._on_provider_validated)
        self._validate_worker.failed.connect(self._on_provider_validation_failed)
        self._validate_thread.start()

    def _on_provider_validated(self, models: list) -> None:
        self.launch_button.setEnabled(True)
        registry = self._pending_resume_registry
        config = self._pending_resume_config
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

        logger.info(
            _("Starting: {count} file(s), provider={provider}, model={model}").format(
                count=len(file_list), provider=config.provider.kind, model=config.provider.model
            )
        )
        self._in_flight_files.clear()

        self._worker = TranscriptionWorker(self._transcriber, registry, mode, cost_tracker)
        self._thread = start_worker(self._worker)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self._thread.start()
        self._spinner_timer.start()

        self.launch_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        # No registry mutation from another worker while a run is in flight.
        self.import_batch_action.setEnabled(False)

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
        status = entry.transcription.status if entry else "error"
        display = self._registry.display_name(state.file_name) if self._registry else state.file_name
        if status == "ok" and entry:
            message = _(
                "{file} - done (model={model}, tokens_in={tin}, tokens_out={tout}, cost={cost})"
            ).format(
                file=display,
                model=entry.transcription.model,
                tin=entry.transcription.tokens_in,
                tout=entry.transcription.tokens_out,
                cost=format_cost(entry.transcription.cost),
            )
            logger.info(message)
        else:
            message = f"{display} - {status}"
            if entry and entry.transcription.error_message:
                message += f" - {entry.transcription.error_message}"
            logger.error(message)

        if status == "ok" and self.no_review_checkbox.isChecked():
            self._export_and_log()

        self._in_flight_files.discard(state.file_name)
        self.progress_bar.setMaximum(max(state.total, 1))
        self.progress_bar.setValue(state.processed)
        self._update_progress_label()

        self.file_cost_label.setText(format_cost(entry.transcription.cost) if entry else "-")
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
        self._push_cost_data()
        self._sync_pivot_and_notify(Path(self.source_dir_edit.text()))
        if not self.no_review_checkbox.isChecked():
            self.tabs.setCurrentIndex(self._review_tab_index)

    def _on_run_failed(self, message: str) -> None:
        self._finish_run()
        # Partial costs are already recorded in the registry - refresh the Costs
        # tab even on failure so they stay visible.
        self._push_cost_data()
        logger.error(_("Run failed: {error}").format(error=message))

    def _finish_run(self) -> None:
        if self._thread is not None:
            self._thread.wait(5000)
        self._thread = None
        self._worker = None
        self._transcriber = None
        self._in_flight_files.clear()
        self._spinner_timer.stop()
        self.spinner_label.setText("")
        self.launch_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.import_batch_action.setEnabled(True)

    def _advance_spinner(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self.spinner_label.setText(_SPINNER_FRAMES[self._spinner_frame])

    def _update_progress_label(self) -> None:
        total = self.progress_bar.maximum()
        processed = self.progress_bar.value()
        if self._in_flight_files:
            current = ", ".join(
                sorted(self._registry.display_name(f) if self._registry else f for f in self._in_flight_files)
            )
            self.progress_label.setText(
                _("{done} / {total} - processing: {current}").format(
                    done=processed, total=total, current=current
                )
            )
        else:
            self.progress_label.setText(f"{processed} / {total}")

    def _on_file_started(self, name: str) -> None:
        self._in_flight_files.add(name)
        display = self._registry.display_name(name) if self._registry else name
        logger.info(_("{file} - starting").format(file=display))
        self._update_progress_label()

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
