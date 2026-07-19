"""Export/Review/Translation tabs (spec Brick 6). Headless (offscreen), providers mocked."""

from __future__ import annotations

import json
import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QMessageBox

from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import Registry
from regeste.core.transcription_mode import TranscriptionMode
from regeste.gui.main_window import MainWindow
from regeste.pivot import CONTENT_FIELDS, FieldValidation, Piece, load_corpus, load_piece, save_piece
from regeste.translation import TranslationProvider, TranslationResult


def _image(directory, name):
    Image.new("RGB", (16, 16), color="white").save(directory / name)


def _validated_piece(source_dir, piece_id="a.jpg", **overrides):
    _image(source_dir, piece_id)
    kwargs = dict(
        id=piece_id,
        call_number="3 U 794/1",
        fonds="3 U 794",
        transcription="Cher Monsieur,",
        image_path=str(source_dir / piece_id),
        confidence_score=0.9,
        field_validations={f: FieldValidation(status="validated") for f in CONTENT_FIELDS},
    )
    kwargs.update(overrides)
    piece = Piece(**kwargs)
    save_piece(source_dir, piece)
    return piece


class _FakeTranslationProvider(TranslationProvider):
    name = "fake"

    def __init__(self, text: str = "Dear Sir,") -> None:
        self._text = text
        self.received_prompt = None

    @property
    def requires_api_key(self) -> bool:
        return False

    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        self.received_prompt = prompt
        return TranslationResult(text=self._text, tokens_in=1, tokens_out=1, model=model)


def test_main_window_has_six_tabs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    tabs = window.tabs
    assert tabs.count() == 6
    assert [tabs.tabText(i) for i in range(6)] == [
        "Transcription",
        "Review",
        "Translation",
        "Output type",
        "Settings",
        "Log",
    ]


def test_export_panel_nested_collapsed_in_transcription_tab(qtbot):
    """The 12-format archival exporter stays reachable from the Transcription
    tab, hidden until "Show advanced archival export" is checked."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.export_panel.isHidden()
    assert not window.show_archival_checkbox.isChecked()

    window.show_archival_checkbox.setChecked(True)
    assert not window.export_panel.isHidden()

    window.show_archival_checkbox.setChecked(False)
    assert window.export_panel.isHidden()


def test_output_type_tab_drives_export_options_and_project_config(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)

    window.hypotheses_radio.setChecked(True)
    window.per_file_radio.setChecked(True)
    window.format_checkboxes["txt"].setChecked(True)

    options = window._current_export_options()
    assert options.transcription_mode is TranscriptionMode.HYPOTHESES
    assert options.single_file is False
    assert options.per_file is True
    assert "txt" in options.formats

    config = window._build_project_config(tmp_path)
    assert config.transcription_mode is TranscriptionMode.HYPOTHESES
    assert config.export.transcription_mode is TranscriptionMode.HYPOTHESES


def test_apply_config_restores_hypotheses_mode(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)

    config = ProjectConfig(
        project_name="p",
        source_dir=tmp_path,
        output_dir=tmp_path,
        provider=ProviderConfig(kind="claude", model="fake-model"),
        transcription_mode=TranscriptionMode.HYPOTHESES,
    )
    window._apply_config(config)
    assert window.hypotheses_radio.isChecked()
    assert not window.literal_radio.isChecked()

    config.transcription_mode = TranscriptionMode.LITERAL
    window._apply_config(config)
    assert window.literal_radio.isChecked()


def test_settings_button_removed_from_transcription_tab(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert not hasattr(window, "settings_button")
    assert not hasattr(window, "_open_settings")


def test_settings_panel_save_updates_main_window_state_and_persists(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)
    config = ProjectConfig(
        project_name="test",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model"),
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.settings_panel
    panel.provider_combo.setCurrentText("gemini")
    panel.model_combo.setCurrentText("gemini-vision")
    panel.workers_spin.setValue(9)
    panel.forced_language_edit.setText("en")

    panel.settings_saved.emit()

    assert window._provider_config.kind == "gemini"
    assert window._provider_config.model == "gemini-vision"
    assert window._workers == 9
    assert window._forced_language == "en"

    saved_meta = json.loads((source_dir / "regeste.json").read_text(encoding="utf-8"))
    assert saved_meta["meta"]["provider"]["kind"] == "gemini"


def test_settings_panel_changes_apply_without_clicking_save(qtbot, tmp_path):
    """Regression test: changing a provider in Settings and switching to another
    tab (Transcription, to launch) must apply the change even if "Save settings"
    was never clicked — the permanent tab has no OK button forcing that click,
    unlike the old modal dialog."""
    window = MainWindow()
    qtbot.addWidget(window)

    settings_index = next(
        i for i in range(window.tabs.count()) if window.tabs.tabText(i) == "Settings"
    )
    window.tabs.setCurrentIndex(settings_index)

    panel = window.settings_panel
    panel.provider_combo.setCurrentText("lm_studio")
    panel.model_combo.setCurrentText("qwen2.5-vl")
    # Note: panel.settings_saved.emit() deliberately NOT called here.

    window.tabs.setCurrentIndex(0)  # switch away from Settings to Transcription

    assert window._provider_config.kind == "lm_studio"
    assert window._provider_config.model == "qwen2.5-vl"


def test_settings_panel_changes_applied_at_launch_even_without_tab_switch(qtbot, tmp_path, monkeypatch):
    from regeste.core.providers.base import Provider, TranscriptionResult

    class _FakeProvider(Provider):
        requires_api_key = False

        def list_vision_models(self):
            return []

        def transcribe(self, image_bytes, *, model, prompt, forced_language=None):
            return TranscriptionResult(text="x", description="", tokens_in=1, tokens_out=1, model=model)

    monkeypatch.setattr("regeste.gui.main_window.create_provider", lambda cfg: _FakeProvider())
    # Cost-estimate confirmation before launch (spec §6/§8) - accept it so the run proceeds.
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.settings_panel
    panel.provider_combo.setCurrentText("lm_studio")
    panel.model_combo.setCurrentText("qwen2.5-vl")
    # settings_saved deliberately never emitted here either.

    window._on_launch_clicked()

    # The sync at the top of _on_launch_clicked runs before anything else, so
    # this is already true even though the run below is still in flight.
    assert window._provider_config.kind == "lm_studio"
    assert window._provider_config.model == "qwen2.5-vl"

    qtbot.waitUntil(lambda: window._worker is None, timeout=5000)


def test_project_changed_propagates_to_panels(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert len(window.review_panel._pieces) == 1
    assert window.export_panel.export_button.isEnabled() is True
    assert window.translation_panel.translate_button.isEnabled() is True


def test_export_panel_writes_selected_formats(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    target_dir = tmp_path / "out"
    window.export_panel.target_dir_edit.setText(str(target_dir))
    window.export_panel.format_checkboxes["csv_light"].setChecked(True)
    window.export_panel.format_checkboxes["markdown"].setChecked(True)

    window.export_panel._on_export_clicked()
    qtbot.waitUntil(lambda: window.export_panel._worker is None, timeout=5000)

    assert (target_dir / "export_light.csv").exists()
    assert (target_dir / "export.md").exists()


def test_export_panel_journal_button(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    target_dir = tmp_path / "out"
    window.export_panel.target_dir_edit.setText(str(target_dir))
    window.export_panel._on_journal_clicked()
    qtbot.waitUntil(lambda: window.export_panel._worker is None, timeout=5000)

    assert (target_dir / "journal_de_revue.xlsx").exists()


def test_review_panel_save_updates_pivot(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, transcription="brouillon")

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    window.review_panel.piece_list.setCurrentRow(0)
    window.review_panel._field_edits["transcription"].setPlainText("texte corrigé")
    combo = window.review_panel._field_status_combos["transcription"]
    combo.setCurrentIndex(combo.findData("validated"))

    window.review_panel._on_save_clicked()

    saved = load_piece(source_dir, "a.jpg")
    assert saved.transcription == "texte corrigé"
    assert saved.field_validations["transcription"].status == "validated"


def test_review_panel_bulk_validate(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(
        source_dir,
        confidence_score=0.95,
        field_validations={},
    )

    monkeypatch.setattr(
        "regeste.gui.panels.review_panel.QMessageBox.information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    window.review_panel.threshold_spin.setValue(0.5)
    window.review_panel._on_bulk_validate_clicked()

    saved = load_piece(source_dir, "a.jpg")
    assert all(v.status == "validated" for v in saved.field_validations.values())


def test_review_panel_group_validate_button(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, field_validations={})

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    window.review_panel.piece_list.setCurrentRow(0)
    window.review_panel._on_group_validate_clicked()

    saved = load_piece(source_dir, "a.jpg")
    assert all(v.status == "validated" for v in saved.field_validations.values())


def test_review_panel_simple_mode_transcription_edit_saved_on_validate(qtbot, tmp_path):
    """The transcription field in the simple view is editable directly - a typo fix
    must be saved (via the same `apply_correction` mechanism as advanced mode) when
    a group-status button is clicked, not require switching to Advanced first."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, field_validations={}, transcription="Cher Monsieur,")

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    window.review_panel.piece_list.setCurrentRow(0)
    window.review_panel.transcription_display.setPlainText("Cher Monsieur, corrigé.")
    window.review_panel._on_group_validate_clicked()

    saved = load_piece(source_dir, "a.jpg")
    assert saved.transcription == "Cher Monsieur, corrigé."
    assert all(v.status == "validated" for v in saved.field_validations.values())


def test_review_panel_group_hold_button_overwrites_heterogeneous_statuses(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    # Starts fully validated (spec's example of a heterogeneous case, worst-cased to "all validated").
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    window.review_panel.piece_list.setCurrentRow(0)
    window.review_panel._on_group_hold_clicked()

    saved = load_piece(source_dir, "a.jpg")
    assert all(v.status == "to_review" for v in saved.field_validations.values())


def test_review_panel_group_reject_requires_note(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, field_validations={})

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.review_panel.piece_list.setCurrentRow(0)

    monkeypatch.setattr(
        "regeste.gui.panels.review_panel.QInputDialog.getText",
        lambda *args, **kwargs: ("", True),
    )
    window.review_panel._on_group_reject_clicked()
    assert load_piece(source_dir, "a.jpg").field_validations == {}

    monkeypatch.setattr(
        "regeste.gui.panels.review_panel.QInputDialog.getText",
        lambda *args, **kwargs: ("illisible", True),
    )
    window.review_panel._on_group_reject_clicked()
    saved = load_piece(source_dir, "a.jpg")
    assert all(v.status == "rejected" for v in saved.field_validations.values())
    assert all(v.rejection_note == "illisible" for v in saved.field_validations.values())


def test_review_panel_list_sorted_pending_validated_rejected(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, piece_id="rejected.jpg", call_number="rejected.jpg", field_validations={
        f: FieldValidation(status="rejected", rejection_note="x") for f in CONTENT_FIELDS
    })
    _validated_piece(source_dir, piece_id="validated.jpg", call_number="validated.jpg")
    _validated_piece(source_dir, piece_id="pending.jpg", call_number="pending.jpg", field_validations={})

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    ids = [p.id for p in window.review_panel._pieces]
    assert ids == ["pending.jpg", "validated.jpg", "rejected.jpg"]


def test_review_panel_advanced_checkbox_toggles_detail_fields(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert window.review_panel.advanced_group.isHidden()
    window.review_panel.advanced_checkbox.setChecked(True)
    assert not window.review_panel.advanced_group.isHidden()
    window.review_panel.advanced_checkbox.setChecked(False)
    assert window.review_panel.advanced_group.isHidden()


def test_review_panel_view_image_opens_system_viewer(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    piece = _validated_piece(source_dir)

    opened = []
    monkeypatch.setattr(
        "regeste.gui.panels.review_panel.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.review_panel.piece_list.setCurrentRow(0)
    window.review_panel._on_view_image_clicked()

    assert opened == [piece.image_path]


def test_log_panel_verbose_checkbox_toggles_logger_level(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    app_logger = logging.getLogger("regeste")
    assert app_logger.level == logging.INFO
    assert window._log_handler.level == logging.INFO

    window.log_panel.verbose_checkbox.setChecked(True)
    assert app_logger.level == logging.DEBUG
    assert window._log_handler.level == logging.DEBUG

    window.log_panel.verbose_checkbox.setChecked(False)
    assert app_logger.level == logging.INFO
    assert window._log_handler.level == logging.INFO


def test_translation_panel_translates_and_saves(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, transcription="Cher Monsieur, je vous écris depuis Lyon.")

    fake_provider = _FakeTranslationProvider(text="Dear Sir, I am writing from Lyon.")
    monkeypatch.setattr(
        "regeste.gui.panels.translation_panel._create_translation_provider",
        lambda kind, base_url, api_key: fake_provider,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.translation_panel
    panel.set_effective_translation_provider(ProviderConfig(kind="claude", model="fake-model"))

    panel._on_translate_clicked()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)

    assert fake_provider.received_prompt is not None
    assert "Lyon" in fake_provider.received_prompt

    saved = load_piece(source_dir, "a.jpg")
    target = panel.language_combo.currentData()
    assert saved.translations[target].text == "Dear Sir, I am writing from Lyon."


def test_translation_panel_translates_low_confidence_piece_without_blocking(qtbot, tmp_path, monkeypatch):
    """Low OCR confidence is an informational warning in `check_guards`, not a hard
    block — the batch launcher must not require per-piece confirmation for it."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, confidence_score=0.1)

    fake_provider = _FakeTranslationProvider(text="Translated.")
    monkeypatch.setattr(
        "regeste.gui.panels.translation_panel._create_translation_provider",
        lambda kind, base_url, api_key: fake_provider,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.translation_panel
    panel.set_effective_translation_provider(ProviderConfig(kind="claude", model="fake-model"))
    panel._on_translate_clicked()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)

    target = panel.language_combo.currentData()
    assert load_piece(source_dir, "a.jpg").translations[target].text == "Translated."


def test_translation_panel_validated_only_scope_skips_unvalidated_pieces(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, piece_id="validated.jpg", call_number="validated.jpg")
    _validated_piece(
        source_dir, piece_id="draft.jpg", call_number="draft.jpg", field_validations={}
    )

    fake_provider = _FakeTranslationProvider(text="Translated.")
    monkeypatch.setattr(
        "regeste.gui.panels.translation_panel._create_translation_provider",
        lambda kind, base_url, api_key: fake_provider,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.translation_panel
    assert panel.validated_only_radio.isChecked()  # default scope
    panel.set_effective_translation_provider(ProviderConfig(kind="claude", model="fake-model"))
    panel._on_translate_clicked()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)

    target = panel.language_combo.currentData()
    assert load_piece(source_dir, "validated.jpg").translations[target].text == "Translated."
    assert load_piece(source_dir, "draft.jpg").translations is None


def test_translation_panel_all_pieces_scope_bypasses_validation_guard(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, piece_id="draft.jpg", call_number="draft.jpg", field_validations={})

    fake_provider = _FakeTranslationProvider(text="Translated.")
    monkeypatch.setattr(
        "regeste.gui.panels.translation_panel._create_translation_provider",
        lambda kind, base_url, api_key: fake_provider,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    panel = window.translation_panel
    panel.all_pieces_radio.setChecked(True)
    panel.set_effective_translation_provider(ProviderConfig(kind="claude", model="fake-model"))
    panel._on_translate_clicked()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)

    target = panel.language_combo.currentData()
    assert load_piece(source_dir, "draft.jpg").translations[target].text == "Translated."


def test_export_action_appears_live_in_logs_tab(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    target_dir = tmp_path / "out"
    window.export_panel.target_dir_edit.setText(str(target_dir))
    window.export_panel.format_checkboxes["markdown"].setChecked(True)
    window.export_panel._on_export_clicked()
    qtbot.waitUntil(lambda: window.export_panel._worker is None, timeout=5000)

    log_text = window.log_panel.log_view.toPlainText()
    assert "Markdown - OK" in log_text
    assert "Export complete." in log_text


def test_log_panel_append_preserves_manual_scroll_position(qtbot):
    from regeste.gui.panels import LogPanel

    panel = LogPanel()
    qtbot.addWidget(panel)
    panel.resize(300, 80)  # small viewport so the scrollbar actually has room to move

    for i in range(200):
        panel.append_message(f"line {i}")

    scrollbar = panel.log_view.verticalScrollBar()
    scrollbar.setValue(0)  # user scrolls back up to read earlier lines
    panel.append_message("a new line arrives while scrolled up")

    assert scrollbar.value() == 0  # position untouched, per user's manual scroll
    assert "a new line arrives while scrolled up" in panel.log_view.toPlainText()


def test_log_panel_copy_all_and_search(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from regeste.gui.panels import LogPanel

    panel = LogPanel()
    qtbot.addWidget(panel)
    panel.append_message("first entry")
    panel.append_message("needle in the haystack")
    panel.append_message("last entry")

    panel._on_copy_clicked()
    clipboard_text = QApplication.clipboard().text()
    assert "first entry" in clipboard_text
    assert "needle in the haystack" in clipboard_text
    assert "last entry" in clipboard_text

    panel.search_edit.setText("needle")
    panel._on_search_next()
    assert panel.log_view.textCursor().selectedText() == "needle"


def test_translation_defaults_to_ocr_provider(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._provider_config = ProviderConfig(kind="claude", model="ocr-model")

    # Default: translation reuses the OCR provider; the panel receives it.
    assert window._translation_same_as_ocr
    assert window._effective_translation_provider() == ProviderConfig(kind="claude", model="ocr-model")
    window._push_translation_context()
    assert window.translation_panel._translation_provider == ProviderConfig(
        kind="claude", model="ocr-model"
    )

    # Separate: translation uses its own provider.
    window._translation_same_as_ocr = False
    window._translation_provider_config = ProviderConfig(kind="gemini", model="trad-model")
    window._push_translation_context()
    assert window.translation_panel._translation_provider == ProviderConfig(
        kind="gemini", model="trad-model"
    )


def test_translation_provider_persists_in_registry(qtbot, tmp_path):
    source_dir = tmp_path / "imgs"
    source_dir.mkdir()
    config = ProjectConfig(
        project_name="p",
        source_dir=source_dir,
        output_dir=tmp_path / "out",
        provider=ProviderConfig(kind="claude", model="ocr-model"),
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    # No saved value -> defaults to "same as OCR".
    assert window._translation_same_as_ocr

    # Choose a separate translation provider and persist it.
    window._translation_same_as_ocr = False
    window._translation_provider_config = ProviderConfig(kind="gemini", model="trad-model")
    window._persist_meta()

    reloaded = ProjectConfig.from_meta(Registry.load(source_dir).meta)
    assert reloaded.translation_same_as_ocr is False
    assert reloaded.translation_provider == ProviderConfig(kind="gemini", model="trad-model")


def test_prompt_dialog_warns_when_placeholder_removed(qtbot):
    from regeste.gui.prompt_dialog import PromptEditDialog

    dialog = PromptEditDialog(
        title="t",
        current_text="x {glossaire} {entites_a_preserver}",
        default_text="D",
        warn_placeholders=["{glossaire}", "{entites_a_preserver}"],
        warning_message="w",
    )
    qtbot.addWidget(dialog)
    assert dialog.warning_label.isHidden()
    dialog.editor.setPlainText("x without them")
    assert not dialog.warning_label.isHidden()
    dialog._on_reset()
    assert dialog.text() == "D"


def _registry_with_costs(source_dir, costs, *, ceiling=None):
    """A registry whose files were all processed ok, each with the given cost."""
    from regeste.core.registry import FileEntry

    config = ProjectConfig(
        project_name="p",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model"),
        spend_ceiling=ceiling,
    )
    names = [f"file_{i:02d}.jpg" for i in range(len(costs))]
    registry = Registry.new(source_dir, meta=config.to_meta(), file_names=names)
    for name, cost in zip(names, costs):
        registry.files[name] = FileEntry(status="ok", cost=cost, model="fake-model")
    registry.save()
    return registry


def test_settings_panel_has_costs_subtab(qtbot):
    from regeste.gui.panels import SettingsPanel

    panel = SettingsPanel()
    qtbot.addWidget(panel)
    labels = [panel._sub_tabs.tabText(i) for i in range(panel._sub_tabs.count())]
    assert labels == ["OCR", "Translation", "General", "Costs"]


def test_costs_tab_empty_state_without_registry(qtbot):
    from regeste.gui.panels import SettingsPanel

    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.set_cost_data(None)
    assert not panel.costs_empty_label.isHidden()
    assert panel.costs_chart.isHidden()
    assert panel.spending_group.isHidden()


def test_costs_tab_populates_chart_and_gauge(qtbot, tmp_path):
    from regeste.gui.panels import SettingsPanel

    registry = _registry_with_costs(tmp_path, [0.01, 0.04, 0.02], ceiling=0.10)
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.set_cost_data(registry)

    assert panel.costs_empty_label.isHidden()
    assert not panel.costs_chart.isHidden()
    assert panel.costs_chart._data == [
        ("file_00.jpg", 0.01),
        ("file_01.jpg", 0.04),
        ("file_02.jpg", 0.02),
    ]
    # 0.07 spent out of 0.10 = 70% -> below the warning threshold, default blue.
    assert panel.spending_bar.value() == 7000
    assert "0.07" in panel.spending_label.text()
    assert "0.10" in panel.spending_label.text()
    assert "#0a84ff" in panel.spending_bar.styleSheet()
    # Chart paints without crashing (forced render offscreen).
    assert not panel.costs_chart.grab().isNull()


def test_costs_tab_gauge_warning_and_exceeded(qtbot, tmp_path):
    from regeste.gui.panels import SettingsPanel

    panel = SettingsPanel()
    qtbot.addWidget(panel)

    warning = _registry_with_costs(tmp_path / "w", [0.085], ceiling=0.10)
    panel.set_cost_data(warning)
    assert "#d29922" in panel.spending_bar.styleSheet()

    exceeded = _registry_with_costs(tmp_path / "e", [0.12], ceiling=0.10)
    panel.set_cost_data(exceeded)
    assert "#f85149" in panel.spending_bar.styleSheet()
    assert panel.spending_bar.value() == panel.spending_bar.maximum()


def test_costs_tab_no_ceiling_hides_gauge(qtbot, tmp_path):
    from regeste.gui.panels import SettingsPanel

    registry = _registry_with_costs(tmp_path, [0.05], ceiling=None)
    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.set_cost_data(registry)

    assert panel.spending_bar.isHidden()
    assert "(no ceiling)" in panel.spending_label.text()


def test_costs_tab_skips_non_ok_entries(qtbot, tmp_path):
    from regeste.core.registry import FileEntry
    from regeste.gui.panels import SettingsPanel

    registry = _registry_with_costs(tmp_path, [0.01, 0.02], ceiling=None)
    registry.files["file_01.jpg"] = FileEntry(status="error", error_message="boom")
    registry.files["pending.jpg"] = FileEntry()

    panel = SettingsPanel()
    qtbot.addWidget(panel)
    panel.set_cost_data(registry)
    assert panel.costs_chart._data == [("file_00.jpg", 0.01)]


def test_main_window_feeds_costs_tab_on_project_open(qtbot, tmp_path):
    _registry_with_costs(tmp_path, [0.01, 0.04], ceiling=0.50)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(tmp_path)

    assert window.settings_panel.costs_chart._data == [
        ("file_00.jpg", 0.01),
        ("file_01.jpg", 0.04),
    ]
    assert not window.settings_panel.spending_group.isHidden()


def test_settings_panel_keeps_translation_choice_when_same_checked(qtbot):
    # Spec: ticking "same as OCR" must never lose the last separate choice.
    from regeste.core.imaging import PreprocessOptions, ResizeOptions
    from regeste.gui.panels import SettingsPanel

    panel = SettingsPanel()
    panel.apply_config(
        provider_config=ProviderConfig(kind="claude", model="ocr"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="",
        rates={},
        spend_ceiling=None,
        workers=4,
        ui_language=None,
        translation_provider=ProviderConfig(kind="gemini", model="trad-model"),
        translation_same_as_ocr=True,
    )
    qtbot.addWidget(panel)
    # "Same" is on, yet the separate choice is still there and returned.
    assert panel.get_translation_same_as_ocr() is True
    assert panel.get_translation_provider() == ProviderConfig(kind="gemini", model="trad-model")
