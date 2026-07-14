"""Settings window (spec §8) — provider/model, images, transcription, costs, advanced.

Kept strictly separate from the main workflow screen (own dialog, opened
on demand), per spec §8 ("never mixed with the main screen").
"""

from __future__ import annotations

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from regeste.core.costs import Rate
from regeste.core.imaging import DEFAULT_LIMITS, PreprocessOptions, ResizeOptions
from regeste.core.project import ProviderConfig
from regeste.core.providers import DEFAULT_BASE_URLS
from regeste.core.transcriber import DEFAULT_SYSTEM_PROMPT
from regeste.i18n import LANGUAGE_NAMES, _

from .prompt_dialog import PromptEditDialog
from .worker import ModelFetchWorker, start_worker

PROVIDER_KINDS = ("claude", "gemini", "openai", "lm_studio", "llama_cpp", "ollama")
REQUIRES_API_KEY_KINDS = ("claude", "gemini", "openai")
# Vision capability isn't always exposed cleanly by these two local backends (spec
# §2.3) - offer a manual "force this model" override as a last resort, after auto
# detection has been attempted. Not offered for claude/gemini/openai/ollama, whose
# detection the spec considers reliable enough on its own.
MANUAL_MODEL_KINDS = ("lm_studio", "llama_cpp")


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        provider_config: ProviderConfig,
        preprocessing: PreprocessOptions,
        resize: ResizeOptions,
        forced_language: str | None,
        system_prompt: str,
        rates: dict[str, Rate],
        spend_ceiling: float | None,
        workers: int,
        ui_language: str | None = None,
        translation_provider: ProviderConfig | None = None,
        translation_same_as_ocr: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Settings"))
        self.resize(640, 560)
        self._fetch_thread: QThread | None = None
        self._fetch_worker: ModelFetchWorker | None = None
        # OCR prompt is edited in a separate dialog (button in the provider tab).
        self._system_prompt_value = system_prompt
        # Separate translation model, kept even while "same as OCR" is on.
        self._translation_provider_initial = translation_provider
        self._translation_same_as_ocr_initial = translation_same_as_ocr

        tabs = QTabWidget()
        tabs.addTab(self._build_provider_tab(provider_config), _("Providers and models"))
        tabs.addTab(self._build_images_tab(preprocessing, resize), _("Images"))
        tabs.addTab(self._build_transcription_tab(forced_language, system_prompt), _("Transcription"))
        tabs.addTab(self._build_costs_tab(rates, spend_ceiling), _("Costs"))
        tabs.addTab(self._build_advanced_tab(workers, ui_language), _("Advanced"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self._set_provider_kind(provider_config.kind)
        if provider_config.model:
            self.model_combo.setCurrentText(provider_config.model)

    # --- Providers & models ---------------------------------------------------

    def _build_provider_tab(self, provider_config: ProviderConfig) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        row = 0

        layout.addWidget(QLabel(_("Provider")), row, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(PROVIDER_KINDS)
        self.provider_combo.setCurrentText(provider_config.kind)
        self.provider_combo.currentTextChanged.connect(self._set_provider_kind)
        layout.addWidget(self.provider_combo, row, 1)
        row += 1

        layout.addWidget(QLabel(_("Server URL")), row, 0)
        self.base_url_edit = QLineEdit(provider_config.base_url or "")
        layout.addWidget(self.base_url_edit, row, 1)
        row += 1

        layout.addWidget(QLabel(_("API key")), row, 0)
        self.api_key_edit = QLineEdit(provider_config.api_key or "")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.api_key_edit, row, 1)
        row += 1

        self.fetch_models_button = QPushButton(_("Fetch models"))
        self.fetch_models_button.clicked.connect(self._on_fetch_models_clicked)
        layout.addWidget(self.fetch_models_button, row, 0, 1, 2)
        row += 1

        layout.addWidget(QLabel(_("Model")), row, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        layout.addWidget(self.model_combo, row, 1)
        row += 1

        # LM Studio/llama.cpp only (spec §2.3): manual "force this model" override,
        # usable whether or not detection found anything.
        self.manual_model_checkbox = QCheckBox(_("Force this model identifier (if not auto-detected)"))
        self.manual_model_edit = QLineEdit()
        self.manual_model_edit.setEnabled(False)
        self.manual_model_checkbox.toggled.connect(self.manual_model_edit.setEnabled)
        layout.addWidget(self.manual_model_checkbox, row, 0)
        layout.addWidget(self.manual_model_edit, row, 1)
        row += 1

        self.edit_ocr_prompt_button = QPushButton(_("Edit OCR prompt..."))
        self.edit_ocr_prompt_button.clicked.connect(self._on_edit_ocr_prompt)
        layout.addWidget(self.edit_ocr_prompt_button, row, 0, 1, 2)
        row += 1

        translation_group = QGroupBox(_("Translation model"))
        tg = QGridLayout(translation_group)
        self.translation_same_checkbox = QCheckBox(_("Use the same model for translation"))
        self.translation_same_checkbox.setChecked(self._translation_same_as_ocr_initial)
        self.translation_same_checkbox.toggled.connect(self._on_translation_same_toggled)
        tg.addWidget(self.translation_same_checkbox, 0, 0, 1, 2)
        tp = self._translation_provider_initial
        tg.addWidget(QLabel(_("Provider")), 1, 0)
        self.translation_provider_combo = QComboBox()
        self.translation_provider_combo.addItems(PROVIDER_KINDS)
        if tp:
            self.translation_provider_combo.setCurrentText(tp.kind)
        tg.addWidget(self.translation_provider_combo, 1, 1)
        tg.addWidget(QLabel(_("Server URL")), 2, 0)
        self.translation_base_url_edit = QLineEdit(tp.base_url if tp and tp.base_url else "")
        tg.addWidget(self.translation_base_url_edit, 2, 1)
        tg.addWidget(QLabel(_("API key")), 3, 0)
        self.translation_api_key_edit = QLineEdit(tp.api_key if tp and tp.api_key else "")
        self.translation_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        tg.addWidget(self.translation_api_key_edit, 3, 1)
        tg.addWidget(QLabel(_("Model")), 4, 0)
        self.translation_model_edit = QLineEdit(tp.model if tp else "")
        tg.addWidget(self.translation_model_edit, 4, 1)
        layout.addWidget(translation_group, row, 0, 1, 2)
        row += 1
        self._on_translation_same_toggled(self.translation_same_checkbox.isChecked())

        self.fetch_status_label = QLabel("")
        self.fetch_status_label.setWordWrap(True)
        layout.addWidget(self.fetch_status_label, row, 0, 1, 2)
        return widget

    def _on_edit_ocr_prompt(self) -> None:
        dialog = PromptEditDialog(
            self,
            title=_("OCR prompt"),
            current_text=self._system_prompt_value,
            default_text=DEFAULT_SYSTEM_PROMPT,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._system_prompt_value = dialog.text()

    def _on_translation_same_toggled(self, checked: bool) -> None:
        for widget in (
            self.translation_provider_combo,
            self.translation_base_url_edit,
            self.translation_api_key_edit,
            self.translation_model_edit,
        ):
            widget.setEnabled(not checked)

    def get_translation_same_as_ocr(self) -> bool:
        return self.translation_same_checkbox.isChecked()

    def get_translation_provider(self) -> ProviderConfig | None:
        """The separate translation provider, kept regardless of the checkbox so
        toggling 'same as OCR' never loses the last choice. None if never set."""
        model = self.translation_model_edit.text().strip()
        base_url = self.translation_base_url_edit.text().strip() or None
        api_key = self.translation_api_key_edit.text().strip() or None
        if not (model or base_url or api_key):
            return None
        return ProviderConfig(
            kind=self.translation_provider_combo.currentText(),
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

    def _set_provider_kind(self, kind: str) -> None:
        self.base_url_edit.setVisible(kind in DEFAULT_BASE_URLS)
        self.api_key_edit.setVisible(kind in REQUIRES_API_KEY_KINDS)
        self.manual_model_checkbox.setVisible(kind in MANUAL_MODEL_KINDS)
        self.manual_model_edit.setVisible(kind in MANUAL_MODEL_KINDS)
        if kind in DEFAULT_BASE_URLS and not self.base_url_edit.text():
            self.base_url_edit.setText(DEFAULT_BASE_URLS[kind])

    def _on_fetch_models_clicked(self) -> None:
        provider_config = ProviderConfig(
            kind=self.provider_combo.currentText(),
            model="",
            base_url=self.base_url_edit.text() or None,
            api_key=self.api_key_edit.text() or None,
        )
        self.fetch_status_label.setText(_("Fetching models..."))
        self.fetch_models_button.setEnabled(False)

        self._fetch_worker = ModelFetchWorker(provider_config)
        self._fetch_thread = start_worker(self._fetch_worker)
        self._fetch_worker.succeeded.connect(self._on_models_fetched)
        self._fetch_worker.failed.connect(self._on_models_fetch_failed)
        self._fetch_thread.start()

    def _on_models_fetched(self, models: list) -> None:
        self.fetch_models_button.setEnabled(True)
        if not models:
            self.fetch_status_label.setText(_("No vision model found for this provider."))
            return
        self.fetch_status_label.setText("")
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(f"{model.display_name} ({model.id})", model.id)

    def _on_models_fetch_failed(self, message: str) -> None:
        self.fetch_models_button.setEnabled(True)
        self.fetch_status_label.setText(_("Could not reach the provider: {error}").format(error=message))

    # --- Images ----------------------------------------------------------------

    def _build_images_tab(self, preprocessing: PreprocessOptions, resize: ResizeOptions) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        limits_group = QGroupBox(_("Provider size limits (informative)"))
        limits_layout = QGridLayout(limits_group)
        for i, (name, limit) in enumerate(DEFAULT_LIMITS.items()):
            limits_layout.addWidget(QLabel(name), i, 0)
            limits_layout.addWidget(
                QLabel(
                    _("max {px}px / {mb:.0f} MB").format(px=limit.max_px, mb=limit.max_bytes / (1024 * 1024))
                ),
                i,
                1,
            )
        layout.addWidget(limits_group)

        resize_group = QGroupBox(_("Resizing"))
        resize_layout = QVBoxLayout(resize_group)
        self.disable_resize_checkbox = QCheckBox(_("Disable adaptive resizing"))
        self.disable_resize_checkbox.setChecked(resize.disabled)
        resize_layout.addWidget(self.disable_resize_checkbox)

        override_row = QHBoxLayout()
        self.override_max_px_checkbox = QCheckBox(_("Override maximum pixel dimension"))
        self.override_max_px_checkbox.setChecked(resize.max_px_override is not None)
        self.max_px_spin = QSpinBox()
        self.max_px_spin.setRange(100, 20000)
        self.max_px_spin.setValue(resize.max_px_override or 4096)
        self.max_px_spin.setEnabled(resize.max_px_override is not None)
        self.override_max_px_checkbox.toggled.connect(self.max_px_spin.setEnabled)
        override_row.addWidget(self.override_max_px_checkbox)
        override_row.addWidget(self.max_px_spin)
        resize_layout.addLayout(override_row)

        override_bytes_row = QHBoxLayout()
        self.override_max_bytes_checkbox = QCheckBox(_("Override maximum file size (bytes)"))
        self.override_max_bytes_checkbox.setChecked(resize.max_bytes_override is not None)
        self.max_bytes_spin = QSpinBox()
        self.max_bytes_spin.setRange(1024, 500_000_000)
        self.max_bytes_spin.setValue(resize.max_bytes_override or 20 * 1024 * 1024)
        self.max_bytes_spin.setEnabled(resize.max_bytes_override is not None)
        self.override_max_bytes_checkbox.toggled.connect(self.max_bytes_spin.setEnabled)
        override_bytes_row.addWidget(self.override_max_bytes_checkbox)
        override_bytes_row.addWidget(self.max_bytes_spin)
        resize_layout.addLayout(override_bytes_row)
        layout.addWidget(resize_group)

        preprocess_group = QGroupBox(_("Preprocessing chain"))
        preprocess_layout = QVBoxLayout(preprocess_group)
        self.deskew_checkbox = QCheckBox(_("Deskew"))
        self.deskew_checkbox.setChecked(preprocessing.deskew)
        self.denoise_checkbox = QCheckBox(_("Denoise"))
        self.denoise_checkbox.setChecked(preprocessing.denoise)
        self.contrast_checkbox = QCheckBox(_("Contrast enhancement"))
        self.contrast_checkbox.setChecked(preprocessing.contrast)
        self.upscale_checkbox = QCheckBox(_("Upscale"))
        self.upscale_checkbox.setChecked(preprocessing.upscale)
        self.upscale_quality_checkbox = QCheckBox(_("Quality upscaling (Real-ESRGAN if available)"))
        self.upscale_quality_checkbox.setChecked(preprocessing.upscale_quality)
        self.upscale_quality_checkbox.setVisible(preprocessing.upscale)
        self.upscale_checkbox.toggled.connect(self.upscale_quality_checkbox.setVisible)
        for checkbox in (
            self.deskew_checkbox,
            self.denoise_checkbox,
            self.contrast_checkbox,
            self.upscale_checkbox,
            self.upscale_quality_checkbox,
        ):
            preprocess_layout.addWidget(checkbox)
        layout.addWidget(preprocess_group)
        layout.addStretch()
        return widget

    # --- Transcription -----------------------------------------------------------

    def _build_transcription_tab(self, forced_language: str | None, system_prompt: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(_("Force document language (optional, auto if empty)")))
        self.forced_language_edit = QLineEdit(forced_language or "")
        layout.addWidget(self.forced_language_edit)
        layout.addStretch()
        return widget

    # --- Costs -------------------------------------------------------------------

    def _build_costs_tab(self, rates: dict[str, Rate], spend_ceiling: float | None) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.rates_table = QTableWidget(0, 3)
        self.rates_table.setHorizontalHeaderLabels(
            [_("Model"), _("Input $/M tokens"), _("Output $/M tokens")]
        )
        for model, rate in rates.items():
            self._add_rate_row(model, rate.input_per_million, rate.output_per_million)
        layout.addWidget(self.rates_table)

        rate_buttons = QHBoxLayout()
        add_rate_button = QPushButton(_("Add row"))
        add_rate_button.clicked.connect(lambda: self._add_rate_row("", 0.0, 0.0))
        remove_rate_button = QPushButton(_("Remove selected row"))
        remove_rate_button.clicked.connect(self._remove_selected_rate_row)
        rate_buttons.addWidget(add_rate_button)
        rate_buttons.addWidget(remove_rate_button)
        layout.addLayout(rate_buttons)

        ceiling_row = QHBoxLayout()
        self.spend_ceiling_checkbox = QCheckBox(_("Enable spend ceiling"))
        self.spend_ceiling_checkbox.setChecked(spend_ceiling is not None)
        self.spend_ceiling_spin = QDoubleSpinBox()
        self.spend_ceiling_spin.setRange(0, 1_000_000)
        self.spend_ceiling_spin.setDecimals(2)
        self.spend_ceiling_spin.setValue(spend_ceiling or 0.0)
        self.spend_ceiling_spin.setEnabled(spend_ceiling is not None)
        self.spend_ceiling_checkbox.toggled.connect(self.spend_ceiling_spin.setEnabled)
        ceiling_row.addWidget(self.spend_ceiling_checkbox)
        ceiling_row.addWidget(self.spend_ceiling_spin)
        layout.addLayout(ceiling_row)
        return widget

    def _add_rate_row(self, model: str, input_rate: float, output_rate: float) -> None:
        row = self.rates_table.rowCount()
        self.rates_table.insertRow(row)
        self.rates_table.setItem(row, 0, QTableWidgetItem(model))
        self.rates_table.setItem(row, 1, QTableWidgetItem(str(input_rate)))
        self.rates_table.setItem(row, 2, QTableWidgetItem(str(output_rate)))

    def _remove_selected_rate_row(self) -> None:
        row = self.rates_table.currentRow()
        if row >= 0:
            self.rates_table.removeRow(row)

    # --- Advanced ------------------------------------------------------------------

    def _build_advanced_tab(self, workers: int, ui_language: str | None) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(_("Number of parallel workers")))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 64)
        self.workers_spin.setValue(workers)
        layout.addWidget(self.workers_spin)

        language_group = QGroupBox(_("Interface language"))
        language_layout = QVBoxLayout(language_group)
        self.ui_language_combo = QComboBox()
        # First entry: auto-detect from the OS locale (LANG/LC_ALL), stored as `None`.
        self.ui_language_combo.addItem(_("Automatic (system language)"), None)
        for code, native_name in LANGUAGE_NAMES.items():
            self.ui_language_combo.addItem(native_name, code)
        index = self.ui_language_combo.findData(ui_language)
        self.ui_language_combo.setCurrentIndex(index if index >= 0 else 0)
        language_layout.addWidget(self.ui_language_combo)
        language_note = QLabel(_("Applies immediately when you click OK - no restart needed."))
        language_note.setWordWrap(True)
        language_layout.addWidget(language_note)
        layout.addWidget(language_group)

        layout.addStretch()
        return widget

    # --- Getters (read back after `exec() == Accepted`) -----------------------------

    def get_provider_config(self) -> ProviderConfig:
        kind = self.provider_combo.currentText()
        if (
            kind in MANUAL_MODEL_KINDS
            and self.manual_model_checkbox.isChecked()
            and self.manual_model_edit.text().strip()
        ):
            model = self.manual_model_edit.text().strip()
        else:
            model_data = self.model_combo.currentData()
            model = model_data if model_data else self.model_combo.currentText()
        return ProviderConfig(
            kind=kind,
            model=model,
            base_url=self.base_url_edit.text() or None,
            api_key=self.api_key_edit.text() or None,
        )

    def get_preprocessing(self) -> PreprocessOptions:
        return PreprocessOptions(
            deskew=self.deskew_checkbox.isChecked(),
            denoise=self.denoise_checkbox.isChecked(),
            contrast=self.contrast_checkbox.isChecked(),
            upscale=self.upscale_checkbox.isChecked(),
            upscale_quality=self.upscale_quality_checkbox.isChecked(),
        )

    def get_resize(self) -> ResizeOptions:
        return ResizeOptions(
            disabled=self.disable_resize_checkbox.isChecked(),
            max_px_override=(
                self.max_px_spin.value() if self.override_max_px_checkbox.isChecked() else None
            ),
            max_bytes_override=(
                self.max_bytes_spin.value() if self.override_max_bytes_checkbox.isChecked() else None
            ),
        )

    def get_forced_language(self) -> str | None:
        return self.forced_language_edit.text().strip() or None

    def get_system_prompt(self) -> str:
        return self._system_prompt_value

    def get_rates(self) -> dict[str, Rate]:
        rates: dict[str, Rate] = {}
        for row in range(self.rates_table.rowCount()):
            name_item = self.rates_table.item(row, 0)
            name = name_item.text().strip() if name_item else ""
            if not name:
                continue
            input_item = self.rates_table.item(row, 1)
            output_item = self.rates_table.item(row, 2)
            try:
                input_rate = float(input_item.text()) if input_item else 0.0
                output_rate = float(output_item.text()) if output_item else 0.0
            except ValueError:
                continue
            rates[name] = Rate(input_per_million=input_rate, output_per_million=output_rate)
        return rates

    def get_spend_ceiling(self) -> float | None:
        return self.spend_ceiling_spin.value() if self.spend_ceiling_checkbox.isChecked() else None

    def get_workers(self) -> int:
        return self.workers_spin.value()

    def get_ui_language(self) -> str | None:
        return self.ui_language_combo.currentData()
