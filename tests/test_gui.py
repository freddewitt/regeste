"""GUI tests (PySide6 + pytest-qt) — provider entirely mocked, no real network calls.

Runs headless: QT_QPA_PLATFORM=offscreen must be set before any QApplication is
created, hence the env var is forced here at import time (before the PySide6
imports below trigger platform plugin resolution) rather than relying solely on
the shell invocation.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from regeste import i18n
from regeste.core.costs import Rate
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.providers.base import ModelInfo, Provider, TranscriptionResult
from regeste.core.imaging import PreprocessOptions, ResizeOptions
from regeste.core.registry import Registry
from regeste.gui.main_window import MainWindow
from regeste.gui.settings_dialog import SettingsDialog


class FakeProvider(Provider):
    """No SDK, scripted responses, no network (pattern from tests/test_transcriber.py)."""

    def __init__(self, responses=None, models=None):
        self._responses = list(responses or [])
        self._models = list(models or [])
        self.calls = 0

    @property
    def requires_api_key(self) -> bool:
        return False

    def list_vision_models(self):
        return self._models

    def transcribe(self, image_bytes, *, model, prompt, forced_language=None):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _image(directory, name):
    Image.new("RGB", (16, 16), color="white").save(directory / name)


def test_main_window_creates_expected_widgets(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.project_name_edit is not None
    assert window.source_dir_edit is not None
    assert window.output_dir_edit is not None
    assert window.launch_button is not None
    assert window.stop_button is not None
    assert window.new_mode_radio is not None
    assert window.resume_mode_radio is not None
    assert window.progress_bar is not None
    assert window.stop_button.isEnabled() is False
    assert window.new_mode_radio.isChecked() is True


def test_full_run_end_to_end_writes_registry_and_export(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    _image(source_dir, "a.jpg")
    _image(source_dir, "b.jpg")

    responses = [
        TranscriptionResult(text="A", description="", tokens_in=10, tokens_out=5, model="fake-model"),
        TranscriptionResult(text="B", description="", tokens_in=10, tokens_out=5, model="fake-model"),
    ]
    provider = FakeProvider(responses=responses)
    monkeypatch.setattr("regeste.gui.main_window.create_provider", lambda config: provider)
    # Cost-estimate confirmation before launch (spec §6/§8) - accept it so the run proceeds.
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.project_name_edit.setText("my_project")
    window.output_dir_edit.setText(str(output_dir))
    window.new_mode_radio.setChecked(True)

    window._on_launch_clicked()
    assert window._worker is not None

    # waitUntil polls rather than awaiting the signal directly: the worker can complete
    # (and emit `finished`) before this test gets a chance to attach a waitSignal listener.
    qtbot.waitUntil(lambda: window._worker is None, timeout=5000)

    assert provider.calls == 2
    registry = Registry.load(source_dir)
    assert registry.files["a.jpg"].status == "ok"
    assert registry.files["b.jpg"].status == "ok"

    combined_json = output_dir / "my_project" / "combined" / "my_project.json"
    assert combined_json.exists()
    data = json.loads(combined_json.read_text())
    assert {entry["name"] for entry in data} == {"a.jpg", "b.jpg"}

    assert window.launch_button.isEnabled() is True
    assert window.stop_button.isEnabled() is False


def test_launch_declining_cost_estimate_cancels_the_run(qtbot, tmp_path, monkeypatch):
    """The cost-estimate confirmation (spec §6/§8, GUI counterpart of the CLI's
    "Start now?") must actually gate the run: declining must not start it."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    _image(source_dir, "a.jpg")

    provider = FakeProvider(responses=[])
    monkeypatch.setattr("regeste.gui.main_window.create_provider", lambda config: provider)
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.project_name_edit.setText("my_project")
    window.output_dir_edit.setText(str(output_dir))
    window.new_mode_radio.setChecked(True)

    window._on_launch_clicked()

    assert window._worker is None
    assert provider.calls == 0
    assert window.launch_button.isEnabled() is True


def test_launch_shows_files_to_process_and_cost_estimate(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    _image(source_dir, "a.jpg")
    _image(source_dir, "b.jpg")

    responses = [
        TranscriptionResult(text="A", description="", tokens_in=10, tokens_out=5, model="fake-model"),
        TranscriptionResult(text="B", description="", tokens_in=10, tokens_out=5, model="fake-model"),
    ]
    provider = FakeProvider(responses=responses)
    monkeypatch.setattr("regeste.gui.main_window.create_provider", lambda config: provider)

    captured = {}

    def fake_question(parent, title, text, *args, **kwargs):
        captured["text"] = text
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("regeste.gui.main_window.QMessageBox.question", fake_question)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.project_name_edit.setText("my_project")
    window.output_dir_edit.setText(str(output_dir))
    window.new_mode_radio.setChecked(True)

    window._on_launch_clicked()
    qtbot.waitUntil(lambda: window._worker is None, timeout=5000)

    assert "text" in captured
    # "fake-model" has no entry in DEFAULT_RATES => the heuristic estimate is 0.
    assert i18n._("Files to process: {count}").format(count=2) in captured["text"]
    assert (
        i18n._("Rough cost estimate (heuristic, not a measurement): ~{amount}").format(
            amount=i18n.format_cost(0.0)
        )
        in captured["text"]
    )


def test_launch_with_nothing_to_process_skips_cost_estimate_dialog(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()  # no images

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("cost-estimate dialog must not appear when there is nothing to process")

    monkeypatch.setattr("regeste.gui.main_window.QMessageBox.question", _should_not_be_called)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.new_mode_radio.setChecked(True)

    window._on_launch_clicked()

    assert i18n._("Nothing to process.") in window.log_panel.log_view.toPlainText()


def test_opening_existing_project_restores_settings(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="restored",
        source_dir=source_dir,
        output_dir=source_dir / "out",
        provider=ProviderConfig(kind="gemini", model="gemini-2.5-pro", api_key="fake-key"),
        workers=7,
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert window.project_name_edit.text() == "restored"
    assert window._provider_config.kind == "gemini"
    assert window._provider_config.model == "gemini-2.5-pro"
    assert window._workers == 7
    assert window.resume_mode_radio.isChecked() is True


def test_new_mode_with_existing_registry_requires_confirmation(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="untouched",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])
    before = (source_dir / "regeste.json").read_text()

    monkeypatch.setattr("regeste.gui.main_window._confirm_overwrite", lambda parent: False)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.new_mode_radio.setChecked(True)  # explicit control, overrides the Resume default

    window._on_launch_clicked()

    after = (source_dir / "regeste.json").read_text()
    assert before == after
    assert window._worker is None


def test_resume_with_no_existing_registry_shows_a_clear_error(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    errors = []
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.critical",
        lambda parent, title, text: errors.append(text),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.resume_mode_radio.setChecked(True)

    window._on_launch_clicked()

    assert len(errors) == 1
    assert window._worker is None


def test_stop_button_calls_request_stop(qtbot):
    class StubTranscriber:
        def __init__(self):
            self.stopped = False

        def request_stop(self):
            self.stopped = True

    window = MainWindow()
    qtbot.addWidget(window)
    stub = StubTranscriber()
    window._transcriber = stub
    window.stop_button.setEnabled(True)

    qtbot.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)

    assert stub.stopped is True


def test_settings_dialog_round_trips_values(qtbot):
    provider_config = ProviderConfig(kind="ollama", model="llama-vision", base_url="http://localhost:11434/v1")
    dialog = SettingsDialog(
        None,
        provider_config=provider_config,
        preprocessing=PreprocessOptions(deskew=True),
        resize=ResizeOptions(max_px_override=2048),
        forced_language="fr",
        system_prompt="custom prompt",
        rates={"m": Rate(input_per_million=1.0, output_per_million=2.0)},
        spend_ceiling=5.0,
        workers=8,
    )
    qtbot.addWidget(dialog)

    assert dialog.get_provider_config().kind == "ollama"
    assert dialog.get_preprocessing().deskew is True
    assert dialog.get_resize().max_px_override == 2048
    assert dialog.get_forced_language() == "fr"
    assert dialog.get_system_prompt() == "custom prompt"
    assert dialog.get_workers() == 8
    assert dialog.get_spend_ceiling() == 5.0
    assert "m" in dialog.get_rates()


def test_settings_dialog_resize_max_bytes_override_round_trips(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model="fake-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(max_bytes_override=1_000_000),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    assert dialog.get_resize().max_bytes_override == 1_000_000


def test_settings_dialog_resize_max_bytes_override_defaults_to_none(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model="fake-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    assert dialog.get_resize().max_bytes_override is None


def test_settings_dialog_manual_model_override_hidden_for_non_local_kinds(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model="fake-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    # `isVisible()` needs the whole ancestor chain shown on screen; `isHidden()`
    # reflects the explicit visibility flag we set, regardless of the (unshown,
    # in tests) top-level dialog.
    assert dialog.manual_model_checkbox.isHidden() is True


def test_settings_dialog_manual_model_override_used_for_lm_studio(qtbot):
    """LM Studio/llama.cpp only (spec §2.3): a checked manual override takes
    priority over whatever is currently selected in the detected-models combo."""
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="lm_studio", model=""),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    assert dialog.manual_model_checkbox.isHidden() is False
    dialog.manual_model_checkbox.setChecked(True)
    dialog.manual_model_edit.setText("my-forced-model")

    assert dialog.get_provider_config().model == "my-forced-model"


def test_settings_dialog_manual_model_override_unchecked_uses_combo(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="llama_cpp", model="from-combo"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    dialog.manual_model_edit.setText("should-be-ignored")
    # checkbox left unchecked => manual override must not apply

    assert dialog.get_provider_config().model == "from-combo"


def test_settings_dialog_ui_language_round_trips(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model="fake-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
        ui_language="ja",
    )
    qtbot.addWidget(dialog)

    assert dialog.get_ui_language() == "ja"


def test_settings_dialog_ui_language_defaults_to_automatic(qtbot):
    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model="fake-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    assert dialog.get_ui_language() is None


def test_ui_language_survives_save_and_reload(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="restored",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
        ui_language="de",
    )
    reloaded = ProjectConfig.from_meta(config.to_meta())
    assert reloaded.ui_language == "de"

    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert window._ui_language == "de"


def test_opening_settings_and_changing_language_persists_in_regeste_json(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="restored",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    # The restart notice is a blocking modal (QMessageBox.information); stub it out
    # like the existing `QMessageBox.critical` monkeypatches elsewhere in this file.
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.information", lambda parent, title, text: None
    )
    # `_apply_ui_language` calls `set_language()`, a process-wide global (spec §11.1) -
    # snapshot it so this test doesn't leak the "ru" language into tests that run after it.
    monkeypatch.setattr(i18n, "_current_language", i18n.get_current_language())
    monkeypatch.setattr(i18n, "_translation", i18n._translation)

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    assert window._ui_language is None

    window._apply_ui_language("ru")
    assert window._ui_language == "ru"

    saved_config = window._build_project_config(source_dir)
    assert saved_config.ui_language == "ru"
    reloaded = ProjectConfig.from_meta(saved_config.to_meta())
    assert reloaded.ui_language == "ru"


def test_main_window_has_visible_language_selector(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    codes = {window.language_combo.itemData(i) for i in range(window.language_combo.count())}
    assert codes == {None} | set(i18n.LANGUAGE_NAMES)


def test_language_selector_switches_live_without_restart(qtbot, monkeypatch):
    # `set_language()` mutates process-wide globals (spec §11.1) - snapshot/restore
    # them so this test doesn't leak "fr" into whichever test runs after it.
    monkeypatch.setattr(i18n, "_current_language", i18n.get_current_language())
    monkeypatch.setattr(i18n, "_translation", i18n._translation)

    window = MainWindow()
    qtbot.addWidget(window)
    old_launch_button = window.launch_button

    window.language_combo.setCurrentIndex(window.language_combo.findData("fr"))

    assert window._ui_language == "fr"
    assert i18n.get_current_language() == "fr"
    # The button text couldn't retranslate without a fresh widget being built.
    assert window.launch_button is not old_launch_button
    assert window.launch_button.text() == i18n._("Launch")


def test_language_selector_preserves_project_state_across_switch(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_current_language", i18n.get_current_language())
    monkeypatch.setattr(i18n, "_translation", i18n._translation)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.project_name_edit.setText("my_project")

    window.language_combo.setCurrentIndex(window.language_combo.findData("de"))

    assert window.source_dir_edit.text() == str(source_dir)
    assert window.project_name_edit.text() == "my_project"


def test_language_selector_blocked_while_run_in_progress(qtbot, monkeypatch):
    monkeypatch.setattr("regeste.gui.main_window.QMessageBox.information", lambda *a, **k: None)

    window = MainWindow()
    qtbot.addWidget(window)
    original_language = window._ui_language
    window._thread = object()  # sentinel: any non-None value marks a run in flight

    other_code = "es" if original_language != "es" else "de"
    window.language_combo.setCurrentIndex(window.language_combo.findData(other_code))

    assert window._ui_language == original_language
    assert window.language_combo.currentData() == original_language
    window._thread = None


def test_system_prompt_survives_save_and_reload(qtbot, tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="restored",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
        system_prompt="You are a custom transcriber.",
    )
    reloaded = ProjectConfig.from_meta(config.to_meta())
    assert reloaded.system_prompt == "You are a custom transcriber."

    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)

    assert window._system_prompt == "You are a custom transcriber."


def test_resume_provider_validation_failure_shows_error_without_launching_run(qtbot, tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="resumed",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])

    class DeadProvider(Provider):
        @property
        def requires_api_key(self) -> bool:
            return True

        def list_vision_models(self):
            raise ConnectionError("key revoked")

        def transcribe(self, image_bytes, *, model, prompt, forced_language=None):
            raise AssertionError("transcribe should never be reached: validation must fail first")

    monkeypatch.setattr("regeste.gui.worker.create_provider", lambda cfg: DeadProvider())

    errors = []
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.critical",
        lambda parent, title, text: errors.append(text),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(source_dir)
    window.resume_mode_radio.setChecked(True)

    window._on_launch_clicked()

    qtbot.waitUntil(lambda: len(errors) == 1, timeout=5000)
    assert "Provider unavailable" in errors[0]
    assert window._worker is None
    assert window.launch_button.isEnabled() is True


def test_fetch_models_runs_in_a_thread_and_populates_the_combo(qtbot, monkeypatch):
    models = [ModelInfo(id="m1", display_name="Model One", requires_api_key=False)]
    provider = FakeProvider(models=models)
    monkeypatch.setattr("regeste.gui.worker.create_provider", lambda config: provider)

    dialog = SettingsDialog(
        None,
        provider_config=ProviderConfig(kind="claude", model=""),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        forced_language=None,
        system_prompt="p",
        rates={},
        spend_ceiling=None,
        workers=4,
    )
    qtbot.addWidget(dialog)

    dialog._on_fetch_models_clicked()
    # waitUntil polls rather than awaiting the signal directly: the worker can complete
    # (and emit) before this test gets a chance to attach a waitSignal listener.
    qtbot.waitUntil(lambda: dialog.model_combo.count() == 1, timeout=5000)
