"""Interactive CLI tests — provider and input()/getpass.getpass entirely mocked."""

from __future__ import annotations

import json
import signal

from unittest.mock import patch

import pytest
from PIL import Image

from regeste.cli.app import IO, _Aborted, _export_archival_formats, _translate_corpus, run
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.providers.base import ModelInfo, Provider, TranscriptionResult
from regeste.core.registry import Registry
from regeste.pivot import load_piece
from regeste.translation import TranslationResult


class FakeProvider(Provider):
    """No SDK, scripted responses, no network (pattern from tests/test_transcriber.py)."""

    def __init__(self, api_key=None, base_url=None, kind="claude", models=None, responses=None):
        self.api_key = api_key
        self.base_url = base_url
        self.kind = kind
        self._models = list(models or [ModelInfo(id="fake-model", display_name="Fake Model", requires_api_key=False)])
        self._responses = list(responses or [])
        self.calls = 0

    @property
    def requires_api_key(self) -> bool:
        return self.kind in ("claude", "gemini", "openai")

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


def _scripted_input(answers):
    """Returns a callable consuming answers in order; raises if exhausted (bug surfacing)."""
    queue = list(answers)

    def _input(prompt=""):
        assert queue, f"no scripted answer left for prompt: {prompt!r}"
        return queue.pop(0)

    return _input


def _new_project_answers(source_dir, output_dir):
    """Answers for the full new-project flow, up to and including "Start now? -> yes"."""
    return [
        str(source_dir),  # source folder
        "my_project",  # project name
        str(output_dir),  # output folder
        "1",  # provider choice (claude)
        "1",  # model choice
        "",  # forced language
        "y",  # use default system prompt?
        "y",  # use same model for translation?
        "y",  # use default translation prompt?
        "n",  # deskew
        "n",  # denoise
        "n",  # contrast
        "n",  # upscale
        "n",  # disable adaptive resizing
        "n",  # override max_px
        "n",  # override max_bytes
        "1",  # workers
        "",  # spend ceiling
        "md,json",  # export formats
        "y",  # single_file
        "y",  # per_file
        "y",  # start now
        "n",  # translate the transcribed pieces now?
        "n",  # export to archival formats now?
    ]


def test_new_project_end_to_end_with_export(tmp_path, monkeypatch):
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
    monkeypatch.setattr("regeste.cli.app.create_provider", lambda config: provider)

    answers = _new_project_answers(source_dir, output_dir)
    input_func = _scripted_input(answers)

    exit_code = run(input_func=input_func, getpass_func=lambda prompt="": "fake-key", print_func=lambda *a, **k: None)

    assert exit_code == 0
    assert provider.calls == 2

    registry = Registry.load(source_dir)
    assert registry.files["a.jpg"].status == "ok"
    assert registry.files["b.jpg"].status == "ok"

    combined_json = output_dir / "my_project" / "combined" / "my_project.json"
    assert combined_json.exists()
    data = json.loads(combined_json.read_text())
    assert {entry["name"] for entry in data} == {"a.jpg", "b.jpg"}

    per_file_dir = output_dir / "my_project" / "per_file"
    assert (per_file_dir / "a.md").exists()
    assert (per_file_dir / "b.json").exists()


def test_resume_does_not_reask_provider(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")
    _image(source_dir, "b.jpg")

    config = ProjectConfig(
        project_name="resumed",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    registry = Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="A", description="", tokens_in=10, tokens_out=5, cost=0.0, model="fake-model"
    )
    registry.save()

    provider = FakeProvider(
        responses=[
            TranscriptionResult(text="B", description="", tokens_in=10, tokens_out=5, model="fake-model")
        ]
    )
    create_provider_calls = []

    def fake_create_provider(cfg):
        create_provider_calls.append(cfg)
        return provider

    monkeypatch.setattr("regeste.cli.app.create_provider", fake_create_provider)

    def failing_getpass(prompt=""):
        raise AssertionError("getpass should not be called in resume mode")

    answers = [
        str(source_dir),  # source folder
        "y",  # resume this project?
        "n",  # modify the project settings?
        "y",  # start now
        "n",  # translate the transcribed pieces now?
        "n",  # export to archival formats now?
    ]
    input_func = _scripted_input(answers)

    exit_code = run(input_func=input_func, getpass_func=failing_getpass, print_func=lambda *a, **k: None)

    assert exit_code == 0
    # a.jpg already ok, only b.jpg (added between sessions) was submitted to the provider.
    assert provider.calls == 1
    reloaded = Registry.load(source_dir)
    assert reloaded.files["a.jpg"].status == "ok"
    assert reloaded.files["b.jpg"].status == "ok"
    # create_provider is called twice: once to validate the resumed provider still
    # works (Fix 2), once to actually run the transcription.
    assert len(create_provider_calls) == 2
    assert all(cfg.kind == "claude" and cfg.model == "fake-model" for cfg in create_provider_calls)


def test_resume_declined_and_overwrite_refused_leaves_registry_untouched(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="untouched",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    registry = Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="A", description="", tokens_in=10, tokens_out=5, cost=0.0, model="fake-model"
    )
    before = (source_dir / "regeste.json").read_text()

    answers = [
        str(source_dir),  # source folder
        "n",  # resume this project? -> no
        "n",  # confirm overwrite? -> no (default)
    ]
    input_func = _scripted_input(answers)

    def failing_getpass(prompt=""):
        raise AssertionError("getpass should not be called when overwrite is refused")

    exit_code = run(input_func=input_func, getpass_func=failing_getpass, print_func=lambda *a, **k: None)

    assert exit_code == 0
    after = (source_dir / "regeste.json").read_text()
    assert before == after


def test_sigint_handler_requests_stop_without_raising_keyboard_interrupt(monkeypatch):
    from regeste.cli.app import IO, _run_transcription

    source_dir_calls = []

    class StubTranscriber:
        def __init__(self):
            self.stop_requested = False

        def request_stop(self):
            self.stop_requested = True

        def run(self, registry, mode, cost_tracker, on_progress=None):
            # Simulates Ctrl+C arriving mid-run: the handler must be able to call
            # request_stop() without a KeyboardInterrupt propagating through here.
            handler = signal.getsignal(signal.SIGINT)
            handler(signal.SIGINT, None)
            assert self.stop_requested is True

    stub = StubTranscriber()
    monkeypatch.setattr("regeste.cli.app.create_provider", lambda config: object())
    monkeypatch.setattr("regeste.cli.app.Transcriber", lambda config, provider, system_prompt=None: stub)

    config = ProjectConfig(
        project_name="p",
        source_dir="/tmp/whatever",
        output_dir="/tmp/whatever",
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    registry = Registry(source_dir="/tmp/whatever")
    io = IO(input_func=lambda p="": "", getpass_func=lambda p="": "", print_func=lambda *a, **k: None)

    previous_handler = signal.getsignal(signal.SIGINT)
    try:
        _run_transcription(registry, "new", config, io)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt must not propagate through _run_transcription")
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    assert stub.stop_requested is True
    assert signal.getsignal(signal.SIGINT) is previous_handler


def test_new_project_with_custom_system_prompt_is_passed_to_transcriber(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    output_dir = tmp_path / "output"
    _image(source_dir, "a.jpg")

    responses = [
        TranscriptionResult(text="A", description="", tokens_in=10, tokens_out=5, model="fake-model"),
    ]
    provider = FakeProvider(responses=responses)
    monkeypatch.setattr("regeste.cli.app.create_provider", lambda config: provider)

    captured_transcribers = []
    from regeste.core.transcriber import Transcriber as RealTranscriber

    def spying_transcriber(config, provider, system_prompt=None):
        transcriber = RealTranscriber(config, provider, system_prompt=system_prompt)
        captured_transcribers.append(transcriber)
        return transcriber

    monkeypatch.setattr("regeste.cli.app.Transcriber", spying_transcriber)

    answers = [
        str(source_dir),  # source folder
        "my_project",  # project name
        str(output_dir),  # output folder
        "1",  # provider choice (claude)
        "1",  # model choice
        "",  # forced language
        "n",  # use default system prompt? -> no
        "You are a custom prompt.",  # custom system prompt
        "y",  # use same model for translation?
        "y",  # use default translation prompt?
        "n",  # deskew
        "n",  # denoise
        "n",  # contrast
        "n",  # upscale
        "n",  # disable adaptive resizing
        "n",  # override max_px
        "n",  # override max_bytes
        "1",  # workers
        "",  # spend ceiling
        "md,json",  # export formats
        "y",  # single_file
        "y",  # per_file
        "y",  # start now
        "n",  # translate the transcribed pieces now?
        "n",  # export to archival formats now?
    ]
    input_func = _scripted_input(answers)

    exit_code = run(input_func=input_func, getpass_func=lambda prompt="": "fake-key", print_func=lambda *a, **k: None)

    assert exit_code == 0
    assert len(captured_transcribers) == 1
    assert captured_transcribers[0].system_prompt == "You are a custom prompt."

    registry = Registry.load(source_dir)
    assert registry.meta["system_prompt"] == "You are a custom prompt."


def test_resume_provider_validation_failure_aborts_cleanly_without_crash(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _image(source_dir, "a.jpg")

    config = ProjectConfig(
        project_name="resumed",
        source_dir=source_dir,
        output_dir=source_dir,
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    registry = Registry.new(source_dir, meta=config.to_meta(), file_names=["a.jpg"])
    registry.save()

    class DeadProvider(Provider):
        @property
        def requires_api_key(self) -> bool:
            return True

        def list_vision_models(self):
            raise ConnectionError("key revoked")

        def transcribe(self, image_bytes, *, model, prompt, forced_language=None):
            raise AssertionError("transcribe should never be reached: validation must fail first")

    monkeypatch.setattr("regeste.cli.app.create_provider", lambda cfg: DeadProvider())

    messages = []
    answers = [
        str(source_dir),  # source folder
        "y",  # resume this project?
        "n",  # modify the project settings?
        "y",  # start now
        "n",  # try again? -> no
    ]
    input_func = _scripted_input(answers)

    exit_code = run(
        input_func=input_func,
        getpass_func=lambda prompt="": "fake-key",
        print_func=lambda *args, **kwargs: messages.append(args[0] if args else ""),
    )

    assert exit_code == 0
    assert any("Provider unavailable" in message for message in messages)
    reloaded = Registry.load(source_dir)
    assert reloaded.files["a.jpg"].status == "pending"


def test_configure_provider_manual_model_override_when_none_detected(monkeypatch):
    """LM Studio/llama.cpp only (spec §2.3): vision isn't always exposed cleanly,
    so a manual "force this model" override is offered as a last resort even when
    detection finds nothing.
    """
    from regeste.cli.app import IO, _configure_provider

    def _no_vision_models(cfg):
        provider = FakeProvider(kind=cfg.kind)
        provider._models = []  # FakeProvider treats `models=[]` as falsy, hence this
        return provider

    monkeypatch.setattr("regeste.cli.app.create_provider", _no_vision_models)

    answers = [
        "4",  # provider choice (lm_studio)
        "http://localhost:1234/v1",  # server URL
        "y",  # manually enter a model identifier?
        "my-custom-vision-model",  # model identifier
    ]
    input_func = _scripted_input(answers)
    io = IO(input_func=input_func, getpass_func=lambda p="": "unused", print_func=lambda *a, **k: None)

    provider_config = _configure_provider(io)

    assert provider_config.kind == "lm_studio"
    assert provider_config.model == "my-custom-vision-model"


def test_configure_provider_manual_model_override_bypasses_detected_list(monkeypatch):
    """Manual override is still offered - and takes priority - even when detection
    did find models (spec §2.3: "tenter la détection d'abord" but still allow a
    forced override)."""
    from regeste.cli.app import IO, _configure_provider

    detected = [ModelInfo(id="detected-model", display_name="Detected", requires_api_key=False)]
    monkeypatch.setattr(
        "regeste.cli.app.create_provider",
        lambda cfg: FakeProvider(kind=cfg.kind, models=detected),
    )

    answers = [
        "5",  # provider choice (llama_cpp)
        "http://localhost:8080/v1",  # server URL
        "y",  # manually enter a model identifier?
        "forced-model-id",  # model identifier
    ]
    input_func = _scripted_input(answers)
    io = IO(input_func=input_func, getpass_func=lambda p="": "unused", print_func=lambda *a, **k: None)

    provider_config = _configure_provider(io)

    assert provider_config.kind == "llama_cpp"
    assert provider_config.model == "forced-model-id"


def test_configure_provider_no_manual_override_offered_for_other_kinds(monkeypatch):
    """claude/gemini/openai/ollama keep the existing retry-only fallback (spec §2.3:
    their detection is considered reliable enough on its own)."""
    from regeste.cli.app import IO, _configure_provider

    def _no_vision_models(cfg):
        provider = FakeProvider(kind=cfg.kind)
        provider._models = []  # FakeProvider treats `models=[]` as falsy, hence this
        return provider

    monkeypatch.setattr("regeste.cli.app.create_provider", _no_vision_models)

    answers = [
        "1",  # provider choice (claude)
        "n",  # try again? -> no (no manual-override prompt should appear here)
    ]
    input_func = _scripted_input(answers)
    io = IO(input_func=input_func, getpass_func=lambda p="": "fake-key", print_func=lambda *a, **k: None)

    with pytest.raises(_Aborted):
        _configure_provider(io)


def test_configure_provider_retry_on_network_error_then_abort(monkeypatch):
    from regeste.cli.app import IO, _configure_provider

    attempts = []

    def flaky_create_provider(cfg):
        attempts.append(cfg)
        raise ConnectionError("unreachable")

    monkeypatch.setattr("regeste.cli.app.create_provider", flaky_create_provider)

    answers = [
        "1",  # provider choice (claude)
        "n",  # try again? -> no
    ]
    input_func = _scripted_input(answers)
    io = IO(input_func=input_func, getpass_func=lambda p="": "fake-key", print_func=lambda *a, **k: None)

    with pytest.raises(_Aborted):
        _configure_provider(io)

    assert len(attempts) == 1


def test_cli_translates_transcribed_pieces_without_review(tmp_path):
    source = tmp_path / "imgs"
    source.mkdir()
    config = ProjectConfig(
        project_name="p",
        source_dir=source,
        output_dir=tmp_path / "out",
        provider=ProviderConfig(kind="claude", model="ocr-model"),
    )
    registry = Registry.new(source, meta=config.to_meta(), file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="Bonjour", description="", tokens_in=1, tokens_out=1,
        cost=0.0, model="m", language="français",
    )

    class _FakeTP:
        name = "fake"

        def translate(self, prompt, *, model):
            assert "Bonjour" in prompt  # source text injected
            return TranslationResult(text="Hello", tokens_in=1, tokens_out=1, model=model)

    io = IO(
        input_func=_scripted_input(["y", "en"]),  # translate now? / target language
        getpass_func=lambda _p="": "",
        print_func=lambda *a, **k: None,
    )
    with patch("regeste.cli.app.create_translation_provider", return_value=_FakeTP()):
        _translate_corpus(registry, source, config, io)

    piece = load_piece(source, "a.jpg")
    assert piece is not None
    assert "en" in piece.translations
    assert piece.translations["en"].text == "Hello"


def test_cli_translates_to_multiple_target_languages(tmp_path):
    source = tmp_path / "imgs"
    source.mkdir()
    config = ProjectConfig(
        project_name="p",
        source_dir=source,
        output_dir=tmp_path / "out",
        provider=ProviderConfig(kind="claude", model="ocr-model"),
    )
    registry = Registry.new(source, meta=config.to_meta(), file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="Bonjour", description="", tokens_in=1, tokens_out=1,
        cost=0.0, model="m", language="français",
    )

    class _FakeTP:
        name = "fake"

        def translate(self, prompt, *, model):
            return TranslationResult(text="translated", tokens_in=1, tokens_out=1, model=model)

    io = IO(
        input_func=_scripted_input(["y", "en, de"]),  # translate now? / two targets
        getpass_func=lambda _p="": "",
        print_func=lambda *a, **k: None,
    )
    with patch("regeste.cli.app.create_translation_provider", return_value=_FakeTP()):
        _translate_corpus(registry, source, config, io)

    piece = load_piece(source, "a.jpg")
    assert set(piece.translations) == {"en", "de"}


def test_cli_exports_archival_formats(tmp_path):
    source = tmp_path / "imgs"
    source.mkdir()
    config = ProjectConfig(
        project_name="p",
        source_dir=source,
        output_dir=tmp_path / "out",
        provider=ProviderConfig(kind="claude", model="ocr-model"),
    )
    registry = Registry.new(source, meta=config.to_meta(), file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="Bonjour", description="lettre", tokens_in=1, tokens_out=1,
        cost=0.0, model="m", language="français",
    )
    out = tmp_path / "exp"
    io = IO(
        input_func=_scripted_input(["y", "dc, html", str(out)]),  # export? / formats / dir
        getpass_func=lambda _p="": "",
        print_func=lambda *a, **k: None,
    )
    _export_archival_formats(registry, source, config, io)
    assert (out / "dublin_core.xml").exists()
    assert (out / "export.html").exists()
