"""Main workflow screen (spec §7.1) — a single run: source/output, mode, live progress/costs.

Everything provider/model/preprocessing/costs-table/workers related lives in the
Settings tab (`panels.SettingsPanel`) instead — this screen only drives one run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QThread, Qt, QTimer, Signal
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
from regeste.core.imaging import IMAGE_EXTENSIONS, PreprocessOptions, ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import FileEntry, Registry
from regeste.core.transcriber import DEFAULT_SYSTEM_PROMPT, ProgressState, Transcriber, create_provider
from regeste.core.transcription_mode import TranscriptionMode
from regeste.i18n import LANGUAGE_NAMES, _, format_cost, is_rtl, set_language
from regeste.pivot import build_pieces_from_registry, load_corpus, load_piece as load_pivot_piece, save_piece as save_pivot_piece

from .panels import ExportPanel, LogPanel, QtLogHandler, ReviewPanel, SettingsPanel, TranslationPanel
from .panels.log_panel import LOGGER_NAME
from .worker import ModelFetchWorker, TranscriptionWorker, start_worker

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

        # Settings-owned state (spec §8), defaulted here and edited via the Settings tab.
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
        # Created before the Transcription tab: the panel now lives inside it
        # (collapsible "Archival formats" section) instead of its own tab.
        self.export_panel = ExportPanel()
        self.tabs.addTab(self._scrollable(self._build_transcription_tab()), _("Transcription"))

        self.review_panel = ReviewPanel()
        self.tabs.addTab(self._scrollable(self.review_panel), _("Review"))
        self.translation_panel = TranslationPanel()
        self.translation_panel.translation_prompt_changed.connect(self._on_translation_prompt_changed)
        self.tabs.addTab(self._scrollable(self.translation_panel), _("Translation"))
        self._push_translation_context()
        self.tabs.addTab(self._scrollable(self._build_output_type_tab()), _("Output type"))
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_saved.connect(self._on_settings_saved)
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

        # The 12-format archival exporter (EAD/DC/METS/CSV/...) moved here from the
        # old "Export" tab - advanced usage, hidden behind an explicit checkbox so
        # the Transcription tab stays focused on the run.
        archival_group = QGroupBox(_("Archival formats"))
        archival_layout = QVBoxLayout(archival_group)
        self.show_archival_checkbox = QCheckBox(_("Show advanced archival export"))
        self.show_archival_checkbox.setChecked(False)
        archival_layout.addWidget(self.show_archival_checkbox)
        self.export_panel.setVisible(False)
        self.show_archival_checkbox.toggled.connect(self.export_panel.setVisible)
        archival_layout.addWidget(self.export_panel)
        layout.addWidget(archival_group)
        layout.addStretch()

        return central

    def _build_output_type_tab(self) -> QWidget:
        """OCR output choices: combined/per-file, formats, transcription mode."""
        central = QWidget()
        layout = QVBoxLayout(central)

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
        output_mode_layout.addStretch()
        layout.addWidget(output_mode_group)

        formats_group = QGroupBox(_("Formats"))
        formats_layout = QHBoxLayout(formats_group)
        self.format_checkboxes: dict[str, QCheckBox] = {}
        for fmt in KNOWN_FORMATS:
            checkbox = QCheckBox(fmt)
            checkbox.setChecked(fmt in ("md", "json"))
            self.format_checkboxes[fmt] = checkbox
            formats_layout.addWidget(checkbox)
        formats_layout.addStretch()
        layout.addWidget(formats_group)

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
        layout.addWidget(transcription_mode_group)
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

    def _apply_config(self, config: ProjectConfig) -> None:
        """Pushes a restored `ProjectConfig` into every field, main screen and Settings."""
        self.project_name_edit.setText(config.project_name)
        self.output_dir_edit.setText(str(config.output_dir))
        # Exclusive radios: combined wins if both were set (older configs allowed both).
        self.combined_radio.setChecked(config.export.single_file)
        self.per_file_radio.setChecked(not config.export.single_file)
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
        self._push_translation_context()
        self._push_settings_context()

    # --- Settings tab ------------------------------------------------------------------

    def _push_settings_context(self) -> None:
        self.settings_panel.apply_config(
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
            "show_archival": self.show_archival_checkbox.isChecked(),
            "formats": {fmt: cb.isChecked() for fmt, cb in self.format_checkboxes.items()},
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
        self.show_archival_checkbox.setChecked(state["show_archival"])
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

    def _current_transcription_mode(self) -> TranscriptionMode:
        return (
            TranscriptionMode.HYPOTHESES
            if self.hypotheses_radio.isChecked()
            else TranscriptionMode.LITERAL
        )

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
        self.translation_panel.set_translation_prompt(self._translation_prompt)

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
        if status == "ok" and entry:
            message = _(
                "{file} - done (model={model}, tokens_in={tin}, tokens_out={tout}, cost={cost})"
            ).format(
                file=state.file_name,
                model=entry.model,
                tin=entry.tokens_in,
                tout=entry.tokens_out,
                cost=format_cost(entry.cost),
            )
            logger.info(message)
        else:
            message = f"{state.file_name} - {status}"
            if entry and entry.error_message:
                message += f" - {entry.error_message}"
            logger.error(message)

        self._in_flight_files.discard(state.file_name)
        self.progress_bar.setMaximum(max(state.total, 1))
        self.progress_bar.setValue(state.processed)
        self._update_progress_label()

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
        self._push_cost_data()
        self._sync_pivot_and_notify(Path(self.source_dir_edit.text()))

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

    def _advance_spinner(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self.spinner_label.setText(_SPINNER_FRAMES[self._spinner_frame])

    def _update_progress_label(self) -> None:
        total = self.progress_bar.maximum()
        processed = self.progress_bar.value()
        if self._in_flight_files:
            current = ", ".join(sorted(self._in_flight_files))
            self.progress_label.setText(
                _("{done} / {total} - processing: {current}").format(
                    done=processed, total=total, current=current
                )
            )
        else:
            self.progress_label.setText(f"{processed} / {total}")

    def _on_file_started(self, name: str) -> None:
        self._in_flight_files.add(name)
        logger.info(_("{file} - starting").format(file=name))
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
