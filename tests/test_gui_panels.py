"""Export/Review/Translation tabs (spec Brick 6). Headless (offscreen), providers mocked."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QMessageBox

from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import Registry
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


def test_main_window_has_five_tabs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    tabs = window.tabs
    assert tabs.count() == 5
    assert [tabs.tabText(i) for i in range(5)] == [
        "Transcription",
        "Export",
        "Review",
        "Translation",
        "Logs",
    ]


def test_project_changed_propagates_to_panels(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert len(window.review_panel._pieces) == 1
    assert window.export_panel.export_button.isEnabled() is True
    assert window.translation_panel.piece_combo.count() == 1


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
    assert panel.piece_combo.count() == 1
    panel.set_effective_translation_provider(ProviderConfig(kind="claude", model="fake-model"))

    panel._on_translate_clicked()
    qtbot.waitUntil(lambda: panel._worker is None, timeout=5000)

    assert fake_provider.received_prompt is not None
    assert "Lyon" in fake_provider.received_prompt

    saved = load_piece(source_dir, "a.jpg")
    target = panel.language_combo.currentData()
    assert saved.translations[target].text == "Dear Sir, I am writing from Lyon."


def test_translation_panel_blocks_on_low_confidence_warning(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _validated_piece(source_dir, confidence_score=0.1)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert "basse" in window.translation_panel.guard_label.text()


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


def test_settings_dialog_keeps_translation_choice_when_same_checked(qtbot):
    # Spec: ticking "same as OCR" must never lose the last separate choice.
    from regeste.core.imaging import PreprocessOptions, ResizeOptions
    from regeste.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(
        provider_config=ProviderConfig(kind="claude", model="ocr"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="",
        rates={},
        spend_ceiling=None,
        workers=4,
        translation_provider=ProviderConfig(kind="gemini", model="trad-model"),
        translation_same_as_ocr=True,
    )
    qtbot.addWidget(dialog)
    # "Same" is on, yet the separate choice is still there and returned.
    assert dialog.get_translation_same_as_ocr() is True
    assert dialog.get_translation_provider() == ProviderConfig(kind="gemini", model="trad-model")
