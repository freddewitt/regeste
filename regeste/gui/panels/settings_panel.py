"""Settings tab — provider/model (OCR + translation), images, transcription,
costs, advanced. Positioned just before Log.

Moved out of the old `SettingsDialog` modal (opened on demand from a button in
Transcription) into a permanent tab: same widgets/wiring, just regrouped into
sub-tabs (OCR / Translation / General) instead of the old 5 dialog tabs, and
"OK" replaced by an explicit Save button since there is no modal to cancel out
of anymore. `apply_config()` is the persistent-tab equivalent of the old
constructor's value-seeding — called once at startup with the app defaults and
again whenever a project is opened/resumed.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from regeste.core.costs import Rate
from regeste.core.imaging import DEFAULT_LIMITS, PreprocessOptions, ResizeOptions
from regeste.core.project import ProviderConfig
from regeste.core.providers import DEFAULT_BASE_URLS, PROVIDER_KINDS, REQUIRES_API_KEY_KINDS
from regeste.core.registry import Registry
from regeste.core.transcriber import DEFAULT_SYSTEM_PROMPT
from regeste.i18n import LANGUAGE_NAMES, _, format_cost

from ..prompt_dialog import PromptEditDialog
from ..worker import ModelFetchWorker, start_worker

# Vision capability isn't always exposed cleanly by these two local backends (spec
# §2.3) - offer a manual "force this model" override as a last resort, after auto
# detection has been attempted. Not offered for claude/gemini/openai/ollama, whose
# detection the spec considers reliable enough on its own.
MANUAL_MODEL_KINDS = ("lm_studio", "llama_cpp")


class CostsChartWidget(QWidget):
    """Per-file cost bars + cumulative line, painted with QPainter.

    Deliberately no charting dependency (no pyqtgraph/QtCharts): a run is ~15
    files in practice, so a hand-rolled paintEvent is cheap and keeps the package
    lean. Left Y axis = per-file cost (bars), right Y axis = cumulative (red line).
    Bars go from green (cheapest file) to red (most expensive) through orange.
    """

    _MARGIN_LEFT = 64
    _MARGIN_RIGHT = 64
    _MARGIN_TOP = 30
    _MARGIN_BOTTOM = 46

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, float]] = []
        self._hit_rects: list[QRectF] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(240)

    def set_data(self, data: list[tuple[str, float]]) -> None:
        self._data = data
        self._hit_rects = []
        self.update()

    @staticmethod
    def _bar_color(cost: float, min_cost: float, max_cost: float) -> QColor:
        span = max_cost - min_cost
        ratio = (cost - min_cost) / span if span > 0 else 0.5
        # Hue 120 (green, cheapest) -> 0 (red, most expensive), through orange.
        return QColor.fromHsv(int(120 * (1 - ratio)), 200, 200)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._hit_rects = []
            if not self._data:
                return
            text_color = self.palette().color(QPalette.ColorRole.Text)
            grid_color = QColor(text_color)
            grid_color.setAlpha(60)

            width = self.width() - self._MARGIN_LEFT - self._MARGIN_RIGHT
            height = self.height() - self._MARGIN_TOP - self._MARGIN_BOTTOM
            if width <= 0 or height <= 0:
                return
            left = self._MARGIN_LEFT
            top = self._MARGIN_TOP
            bottom = top + height
            metrics = painter.fontMetrics()

            costs = [cost for _, cost in self._data]
            max_cost = max(costs) or 1.0
            min_cost = min(costs)
            total = sum(costs)

            # Horizontal grid + left axis labels (per-file cost) + right axis
            # labels (cumulative).
            for i in range(5):
                fraction = i / 4
                y = bottom - fraction * height
                painter.setPen(grid_color)
                painter.drawLine(left, int(y), left + width, int(y))
                painter.setPen(text_color)
                left_rect = QRect(
                    0, int(y - metrics.height() / 2), self._MARGIN_LEFT - 6, metrics.height()
                )
                painter.drawText(
                    left_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{fraction * max_cost:.4f}",
                )
                right_rect = QRect(
                    left + width + 6,
                    int(y - metrics.height() / 2),
                    self._MARGIN_RIGHT - 6,
                    metrics.height(),
                )
                painter.drawText(
                    right_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{fraction * total:.4f}",
                )

            count = len(self._data)
            slot = width / count
            bar_width = slot * 0.7
            cumulative = 0.0
            line_points: list[QPointF] = []
            for index, (name, cost) in enumerate(self._data):
                x = left + index * slot
                bar_height = (cost / max_cost) * height
                bar_rect = QRectF(
                    x + (slot - bar_width) / 2, bottom - bar_height, bar_width, bar_height
                )
                painter.fillRect(bar_rect, self._bar_color(cost, min_cost, max_cost))
                # Hover hit zone: the whole column, so even a zero-cost bar stays reachable.
                self._hit_rects.append(QRectF(x, top, slot, height))
                elided = metrics.elidedText(
                    name, Qt.TextElideMode.ElideMiddle, max(int(slot) - 4, 8)
                )
                painter.setPen(text_color)
                painter.drawText(
                    QRectF(x, bottom + 4, slot, self._MARGIN_BOTTOM - 8),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    elided,
                )
                cumulative += cost
                if total > 0:
                    line_points.append(
                        QPointF(x + slot / 2, bottom - (cumulative / total) * height)
                    )

            if line_points:
                painter.setPen(QPen(QColor("#f85149"), 2))
                painter.drawPolyline(line_points)

            # Legend: green square = per-file cost, red line = cumulative.
            legend_y = 6
            painter.fillRect(
                QRectF(left, legend_y, 10, 10), self._bar_color(min_cost, min_cost, max_cost)
            )
            painter.setPen(text_color)
            per_file_label = _("Per-file cost")
            painter.drawText(
                QRectF(left + 14, legend_y - 2, 200, 14),
                Qt.AlignmentFlag.AlignLeft,
                per_file_label,
            )
            line_legend_x = left + 20 + metrics.horizontalAdvance(per_file_label) + 16
            painter.setPen(QPen(QColor("#f85149"), 2))
            painter.drawLine(
                QPointF(line_legend_x, legend_y + 5), QPointF(line_legend_x + 12, legend_y + 5)
            )
            painter.setPen(text_color)
            painter.drawText(
                QRectF(line_legend_x + 16, legend_y - 2, 200, 14),
                Qt.AlignmentFlag.AlignLeft,
                _("Cumulative"),
            )
        finally:
            painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        for (name, cost), rect in zip(self._data, self._hit_rects):
            if rect.contains(position):
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    _("{file} - cost: {cost}").format(file=name, cost=f"{cost:.4f}"),
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)


class SettingsPanel(QWidget):
    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fetch_thread: QThread | None = None
        self._fetch_worker: ModelFetchWorker | None = None
        # OCR prompt is edited in a separate dialog (button in the OCR sub-tab).
        self._system_prompt_value = DEFAULT_SYSTEM_PROMPT
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_ocr_tab(), _("OCR"))
        self._sub_tabs.addTab(self._build_translation_tab(), _("Translation"))
        self._sub_tabs.addTab(self._build_general_tab(), _("General"))
        self._sub_tabs.addTab(self._build_costs_tab(), _("Costs"))
        outer.addWidget(self._sub_tabs)

        self.save_button = QPushButton(_("Save settings"))
        self.save_button.clicked.connect(lambda: self.settings_saved.emit())
        outer.addWidget(self.save_button)

    # --- OCR sub-tab (provider/model, images, forced language, costs, workers) -------

    def _build_ocr_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self._build_ocr_provider_group())
        layout.addWidget(self._build_images_limits_group())
        layout.addWidget(self._build_resize_group())
        layout.addWidget(self._build_preprocess_group())
        layout.addWidget(self._build_forced_language_group())
        layout.addWidget(self._build_costs_group())
        layout.addWidget(self._build_workers_group())
        layout.addStretch()
        return widget

    def _build_ocr_provider_group(self) -> QGroupBox:
        group = QGroupBox(_("Provider"))
        layout = QGridLayout(group)
        row = 0

        layout.addWidget(QLabel(_("Provider")), row, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(PROVIDER_KINDS)
        self.provider_combo.currentTextChanged.connect(self._set_provider_kind)
        layout.addWidget(self.provider_combo, row, 1)
        row += 1

        layout.addWidget(QLabel(_("Server URL")), row, 0)
        self.base_url_edit = QLineEdit()
        layout.addWidget(self.base_url_edit, row, 1)
        row += 1

        layout.addWidget(QLabel(_("API key")), row, 0)
        self.api_key_edit = QLineEdit()
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

        self.fetch_status_label = QLabel("")
        self.fetch_status_label.setWordWrap(True)
        layout.addWidget(self.fetch_status_label, row, 0, 1, 2)
        return group

    def _on_edit_ocr_prompt(self) -> None:
        dialog = PromptEditDialog(
            self,
            title=_("OCR prompt"),
            current_text=self._system_prompt_value,
            default_text=DEFAULT_SYSTEM_PROMPT,
        )
        if dialog.exec() == PromptEditDialog.DialogCode.Accepted:
            self._system_prompt_value = dialog.text()

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

    def _build_images_limits_group(self) -> QGroupBox:
        group = QGroupBox(_("Provider size limits (informative)"))
        layout = QGridLayout(group)
        for i, (name, limit) in enumerate(DEFAULT_LIMITS.items()):
            layout.addWidget(QLabel(name), i, 0)
            layout.addWidget(
                QLabel(
                    _("max {px}px / {mb:.0f} MB").format(px=limit.max_px, mb=limit.max_bytes / (1024 * 1024))
                ),
                i,
                1,
            )
        return group

    def _build_resize_group(self) -> QGroupBox:
        group = QGroupBox(_("Resizing"))
        layout = QVBoxLayout(group)
        self.disable_resize_checkbox = QCheckBox(_("Disable adaptive resizing"))
        layout.addWidget(self.disable_resize_checkbox)

        override_row = QHBoxLayout()
        self.override_max_px_checkbox = QCheckBox(_("Override maximum pixel dimension"))
        self.max_px_spin = QSpinBox()
        self.max_px_spin.setRange(100, 20000)
        self.max_px_spin.setEnabled(False)
        self.override_max_px_checkbox.toggled.connect(self.max_px_spin.setEnabled)
        override_row.addWidget(self.override_max_px_checkbox)
        override_row.addWidget(self.max_px_spin)
        layout.addLayout(override_row)

        override_bytes_row = QHBoxLayout()
        self.override_max_bytes_checkbox = QCheckBox(_("Override maximum file size (bytes)"))
        self.max_bytes_spin = QSpinBox()
        self.max_bytes_spin.setRange(1024, 500_000_000)
        self.max_bytes_spin.setEnabled(False)
        self.override_max_bytes_checkbox.toggled.connect(self.max_bytes_spin.setEnabled)
        override_bytes_row.addWidget(self.override_max_bytes_checkbox)
        override_bytes_row.addWidget(self.max_bytes_spin)
        layout.addLayout(override_bytes_row)
        return group

    def _build_preprocess_group(self) -> QGroupBox:
        group = QGroupBox(_("Preprocessing chain"))
        layout = QVBoxLayout(group)
        self.deskew_checkbox = QCheckBox(_("Deskew"))
        self.denoise_checkbox = QCheckBox(_("Denoise"))
        self.contrast_checkbox = QCheckBox(_("Contrast enhancement"))
        self.upscale_checkbox = QCheckBox(_("Upscale"))
        self.upscale_quality_checkbox = QCheckBox(_("Quality upscaling (Real-ESRGAN if available)"))
        self.upscale_quality_checkbox.setVisible(False)
        self.upscale_checkbox.toggled.connect(self.upscale_quality_checkbox.setVisible)
        for checkbox in (
            self.deskew_checkbox,
            self.denoise_checkbox,
            self.contrast_checkbox,
            self.upscale_checkbox,
            self.upscale_quality_checkbox,
        ):
            layout.addWidget(checkbox)
        return group

    def _build_forced_language_group(self) -> QGroupBox:
        group = QGroupBox(_("Transcription"))
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel(_("Force document language (optional, auto if empty)")))
        self.forced_language_edit = QLineEdit()
        layout.addWidget(self.forced_language_edit)
        return group

    def _build_costs_group(self) -> QGroupBox:
        group = QGroupBox(_("Costs"))
        layout = QVBoxLayout(group)

        self.rates_table = QTableWidget(0, 3)
        self.rates_table.setHorizontalHeaderLabels(
            [_("Model"), _("Input $/M tokens"), _("Output $/M tokens")]
        )
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
        self.spend_ceiling_spin = QDoubleSpinBox()
        self.spend_ceiling_spin.setRange(0, 1_000_000)
        self.spend_ceiling_spin.setDecimals(2)
        self.spend_ceiling_spin.setEnabled(False)
        self.spend_ceiling_checkbox.toggled.connect(self.spend_ceiling_spin.setEnabled)
        ceiling_row.addWidget(self.spend_ceiling_checkbox)
        ceiling_row.addWidget(self.spend_ceiling_spin)
        layout.addLayout(ceiling_row)
        return group

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

    def _build_workers_group(self) -> QGroupBox:
        group = QGroupBox(_("Advanced"))
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel(_("Number of parallel workers")))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 64)
        layout.addWidget(self.workers_spin)
        return group

    # --- Translation sub-tab -----------------------------------------------------------

    def _build_translation_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self._build_translation_provider_group())
        layout.addStretch()
        return widget

    def _build_translation_provider_group(self) -> QGroupBox:
        group = QGroupBox(_("Translation model"))
        layout = QGridLayout(group)
        self.translation_same_checkbox = QCheckBox(_("Use the same model for translation"))
        self.translation_same_checkbox.toggled.connect(self._on_translation_same_toggled)
        layout.addWidget(self.translation_same_checkbox, 0, 0, 1, 2)
        layout.addWidget(QLabel(_("Provider")), 1, 0)
        self.translation_provider_combo = QComboBox()
        self.translation_provider_combo.addItems(PROVIDER_KINDS)
        layout.addWidget(self.translation_provider_combo, 1, 1)
        layout.addWidget(QLabel(_("Server URL")), 2, 0)
        self.translation_base_url_edit = QLineEdit()
        layout.addWidget(self.translation_base_url_edit, 2, 1)
        layout.addWidget(QLabel(_("API key")), 3, 0)
        self.translation_api_key_edit = QLineEdit()
        self.translation_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.translation_api_key_edit, 3, 1)
        layout.addWidget(QLabel(_("Model")), 4, 0)
        self.translation_model_edit = QLineEdit()
        layout.addWidget(self.translation_model_edit, 4, 1)
        return group

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

    # --- General sub-tab ---------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        language_group = QGroupBox(_("Interface language"))
        language_layout = QVBoxLayout(language_group)
        self.ui_language_combo = QComboBox()
        # First entry: auto-detect from the OS locale (LANG/LC_ALL), stored as `None`.
        self.ui_language_combo.addItem(_("Automatic (system language)"), None)
        for code, native_name in LANGUAGE_NAMES.items():
            self.ui_language_combo.addItem(native_name, code)
        language_layout.addWidget(self.ui_language_combo)
        language_note = QLabel(_("Applies immediately when you click Save settings - no restart needed."))
        language_note.setWordWrap(True)
        language_layout.addWidget(language_note)
        layout.addWidget(language_group)

        layout.addStretch()
        return widget

    # --- Costs sub-tab (per-file chart + spending-vs-ceiling gauge) -------------------

    def _build_costs_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.costs_empty_label = QLabel(
            _("No cost data available yet - run a transcription or open a project with processed files.")
        )
        self.costs_empty_label.setWordWrap(True)
        layout.addWidget(self.costs_empty_label)

        self.costs_chart = CostsChartWidget()
        layout.addWidget(self.costs_chart, stretch=1)

        self.spending_group = QGroupBox(_("Spending"))
        spending_layout = QVBoxLayout(self.spending_group)
        self.spending_label = QLabel("-")
        spending_layout.addWidget(self.spending_label)
        self.spending_bar = QProgressBar()
        self.spending_bar.setRange(0, 10_000)
        # The exact figures live in the label above; the bar is the visual ratio only.
        self.spending_bar.setTextVisible(False)
        spending_layout.addWidget(self.spending_bar)
        layout.addWidget(self.spending_group)

        self.set_cost_data(None)
        return widget

    def set_cost_data(self, registry: Registry | None) -> None:
        """Rebuild the Costs tab from the registry's per-file entries.

        Rebuilt from `Registry.files` (each `FileEntry` keeps its `cost`) rather
        than from the run's transient `CostTracker`, so the tab also reflects a
        project just opened from disk - no flying tracker state to keep alive.
        """
        data: list[tuple[str, float]] = []
        ceiling: float | None = None
        provider_kind = ""
        if registry is not None:
            data = [
                (name, entry.cost)
                for name, entry in sorted(registry.files.items())
                if entry.status == "ok"
            ]
            ceiling = registry.meta.get("spend_ceiling")
            provider = registry.meta.get("provider") or {}
            if isinstance(provider, dict):
                provider_kind = provider.get("kind", "")

        self.costs_chart.set_data(data)
        has_data = bool(data)
        self.costs_empty_label.setVisible(not has_data)
        self.costs_chart.setVisible(has_data)
        self.spending_group.setVisible(has_data)
        if not has_data:
            return

        self.spending_group.setTitle(
            _("Spending ({provider})").format(provider=provider_kind)
            if provider_kind
            else _("Spending")
        )
        spent = sum(cost for _, cost in data)
        if ceiling is None:
            self.spending_bar.setVisible(False)
            self.spending_label.setText(
                _("Spent: {spent} (no ceiling)").format(spent=format_cost(spent))
            )
            return

        self.spending_bar.setVisible(True)
        ratio = spent / ceiling if ceiling > 0 else 1.0
        self.spending_bar.setValue(min(int(ratio * 10_000), 10_000))
        self.spending_label.setText(
            _("Spent: {spent} / {ceiling} (ceiling)").format(
                spent=format_cost(spent), ceiling=format_cost(ceiling)
            )
        )
        if ratio >= 1.0:
            chunk_color = "#f85149"  # over the ceiling: red
        elif ratio >= 0.8:
            chunk_color = "#d29922"  # 80%+ of the ceiling: warning
        else:
            chunk_color = "#0a84ff"  # matches the theme's default chunk blue
        self.spending_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {chunk_color}; border-radius: 5px; }}"
        )

    # --- Config in/out --------------------------------------------------------------

    def apply_config(
        self,
        *,
        provider_config: ProviderConfig,
        preprocessing: PreprocessOptions,
        resize: ResizeOptions,
        forced_language: str | None,
        system_prompt: str,
        rates: dict[str, Rate],
        spend_ceiling: float | None,
        workers: int,
        ui_language: str | None,
        translation_provider: ProviderConfig | None,
        translation_same_as_ocr: bool,
    ) -> None:
        """Repopulate every widget from the current config. Called once at startup
        with the app defaults, and again whenever a project is opened/resumed
        (the persistent-tab equivalent of the old `SettingsDialog.__init__`'s
        one-shot value seeding)."""
        self._system_prompt_value = system_prompt

        self.provider_combo.setCurrentText(provider_config.kind)
        self._set_provider_kind(provider_config.kind)
        self.base_url_edit.setText(provider_config.base_url or "")
        self.api_key_edit.setText(provider_config.api_key or "")
        if provider_config.model:
            self.model_combo.setCurrentText(provider_config.model)

        self.translation_same_checkbox.setChecked(translation_same_as_ocr)
        tp = translation_provider
        self.translation_provider_combo.setCurrentText(tp.kind if tp else "claude")
        self.translation_base_url_edit.setText(tp.base_url if tp and tp.base_url else "")
        self.translation_api_key_edit.setText(tp.api_key if tp and tp.api_key else "")
        self.translation_model_edit.setText(tp.model if tp else "")
        self._on_translation_same_toggled(self.translation_same_checkbox.isChecked())

        self.disable_resize_checkbox.setChecked(resize.disabled)
        self.override_max_px_checkbox.setChecked(resize.max_px_override is not None)
        self.max_px_spin.setValue(resize.max_px_override or 4096)
        self.max_px_spin.setEnabled(resize.max_px_override is not None)
        self.override_max_bytes_checkbox.setChecked(resize.max_bytes_override is not None)
        self.max_bytes_spin.setValue(resize.max_bytes_override or 20 * 1024 * 1024)
        self.max_bytes_spin.setEnabled(resize.max_bytes_override is not None)

        self.deskew_checkbox.setChecked(preprocessing.deskew)
        self.denoise_checkbox.setChecked(preprocessing.denoise)
        self.contrast_checkbox.setChecked(preprocessing.contrast)
        self.upscale_checkbox.setChecked(preprocessing.upscale)
        self.upscale_quality_checkbox.setChecked(preprocessing.upscale_quality)
        self.upscale_quality_checkbox.setVisible(preprocessing.upscale)

        self.forced_language_edit.setText(forced_language or "")

        self.rates_table.setRowCount(0)
        for model, rate in rates.items():
            self._add_rate_row(model, rate.input_per_million, rate.output_per_million)

        self.spend_ceiling_checkbox.setChecked(spend_ceiling is not None)
        self.spend_ceiling_spin.setValue(spend_ceiling or 0.0)
        self.spend_ceiling_spin.setEnabled(spend_ceiling is not None)

        self.workers_spin.setValue(workers)

        index = self.ui_language_combo.findData(ui_language)
        self.ui_language_combo.setCurrentIndex(index if index >= 0 else 0)

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
