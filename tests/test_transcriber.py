"""Run orchestrator tests — provider is entirely mocked, no real network calls."""

from PIL import Image

from regeste.core.costs import CostTracker, Rate
from regeste.core.imaging import PreprocessOptions, ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.providers import ClaudeProvider, GeminiProvider, OpenAICompatProvider
from regeste.core.providers.base import Provider, TranscriptionResult
from regeste.core.registry import Registry
from regeste.core.transcriber import Transcriber, _is_retryable, create_provider


class FakeProvider(Provider):
    """Test provider: no SDK, scripted responses, no network."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = 0

    @property
    def requires_api_key(self) -> bool:
        return False

    def list_vision_models(self):
        return []

    def transcribe(self, image_bytes, *, model, prompt, forced_language=None):
        self.calls += 1
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _config(tmp_path, *, workers=1, spend_ceiling=None):
    return ProjectConfig(
        project_name="test",
        source_dir=tmp_path,
        output_dir=tmp_path / "output",
        provider=ProviderConfig(kind="claude", model="test-model"),
        preprocessing=PreprocessOptions(),
        resize=ResizeOptions(),
        workers=workers,
        spend_ceiling=spend_ceiling,
    )


def _image(tmp_path, name):
    Image.new("RGB", (40, 40), color="white").save(tmp_path / name)


def test_create_provider_claude_and_gemini():
    assert isinstance(create_provider(ProviderConfig(kind="claude", model="m", api_key="k")), ClaudeProvider)
    assert isinstance(create_provider(ProviderConfig(kind="gemini", model="m", api_key="k")), GeminiProvider)


def test_create_provider_openai_compat_uses_default_base_url():
    provider = create_provider(ProviderConfig(kind="ollama", model="qwen2.5vl"))
    assert isinstance(provider, OpenAICompatProvider)
    assert provider._base_url == "http://localhost:11434/v1"


def test_create_provider_openai_compat_respects_custom_base_url():
    provider = create_provider(ProviderConfig(kind="lm_studio", model="m", base_url="http://other:9999/v1"))
    assert provider._base_url == "http://other:9999/v1"


def test_is_retryable_on_rate_limit_and_server_error():
    error_429 = Exception("too many requests")
    error_429.status_code = 429
    error_500 = Exception("server error")
    error_500.status_code = 503
    assert _is_retryable(error_429) is True
    assert _is_retryable(error_500) is True


def test_is_retryable_false_on_client_error():
    error_400 = Exception("bad request")
    error_400.status_code = 400
    assert _is_retryable(error_400) is False


def test_transcriber_processes_all_files_and_saves_the_registry(tmp_path):
    _image(tmp_path, "a.jpg")
    _image(tmp_path, "b.jpg")
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])

    provider = FakeProvider(
        responses=[
            TranscriptionResult(text="A", description="", tokens_in=1_000_000, tokens_out=0, model="test-model"),
            TranscriptionResult(text="B", description="", tokens_in=1_000_000, tokens_out=0, model="test-model"),
        ]
    )
    tracker = CostTracker(rates={"test-model": Rate(input_per_million=2.0, output_per_million=0.0)})
    transcriber = Transcriber(_config(tmp_path), provider)

    transcriber.run(registry, "new", tracker)

    assert provider.calls == 2
    assert registry.files["a.jpg"].status == "ok"
    assert registry.files["b.jpg"].status == "ok"
    assert tracker.total_cost == 4.0
    reloaded = Registry.load(tmp_path)
    assert reloaded.files["a.jpg"].text == "A"


def test_transcriber_records_an_error_without_interrupting_the_run(tmp_path):
    _image(tmp_path, "a.jpg")
    _image(tmp_path, "b.jpg")
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])

    provider = FakeProvider(
        responses=[
            RuntimeError("permanent failure"),
            TranscriptionResult(text="B", description="", tokens_in=0, tokens_out=0, model="test-model"),
        ]
    )
    transcriber = Transcriber(_config(tmp_path, workers=1), provider)

    transcriber.run(registry, "new", CostTracker(rates={}))

    statuses = {registry.files["a.jpg"].status, registry.files["b.jpg"].status}
    assert statuses == {"error", "ok"}


def test_transcriber_retries_on_retryable_error_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("regeste.core.transcriber.time.sleep", lambda _: None)
    _image(tmp_path, "a.jpg")
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg"])

    error_429 = Exception("rate limited")
    error_429.status_code = 429
    provider = FakeProvider(
        responses=[
            error_429,
            TranscriptionResult(text="ok", description="", tokens_in=0, tokens_out=0, model="test-model"),
        ]
    )
    transcriber = Transcriber(_config(tmp_path), provider)

    transcriber.run(registry, "new", CostTracker(rates={}))

    assert provider.calls == 2
    assert registry.files["a.jpg"].status == "ok"


def test_transcriber_spend_ceiling_stops_the_run_cleanly(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _image(tmp_path, name)
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg", "c.jpg"])

    provider = FakeProvider(
        responses=[
            TranscriptionResult(text="A", description="", tokens_in=1_000_000, tokens_out=0, model="test-model"),
            TranscriptionResult(text="B", description="", tokens_in=1_000_000, tokens_out=0, model="test-model"),
            TranscriptionResult(text="C", description="", tokens_in=1_000_000, tokens_out=0, model="test-model"),
        ]
    )
    tracker = CostTracker(rates={"test-model": Rate(input_per_million=1.0, output_per_million=0.0)})
    # workers=1 => deterministic processing order (sequential submission).
    transcriber = Transcriber(_config(tmp_path, workers=1, spend_ceiling=1.5), provider)

    transcriber.run(registry, "new", tracker)

    statuses = [registry.files[name].status for name in ("a.jpg", "b.jpg", "c.jpg")]
    assert statuses.count("ok") == 2
    assert statuses.count("pending") == 1  # 3rd file never launched, clean stop after ceiling hit
