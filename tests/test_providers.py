"""Provider tests — third-party SDKs are always mocked, no real network calls."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from regeste.core.providers import ClaudeProvider, GeminiProvider, OpenAICompatProvider
from regeste.core.providers.base import parse_text_description


@pytest.fixture(autouse=True)
def _clear_ollama_vision_cache():
    """The Ollama vision cache is process-wide (class-level), so it must be
    reset between tests to keep them independent from one another."""
    OpenAICompatProvider._ollama_vision_cache_by_base_url.clear()
    yield
    OpenAICompatProvider._ollama_vision_cache_by_base_url.clear()


def test_parse_text_description_both_sections():
    raw = "## TEXT\nHello\nworld\n\n## DESCRIPTION\nAn old postcard"
    text, description = parse_text_description(raw)
    assert text == "Hello\nworld"
    assert description == "An old postcard"


def test_parse_text_description_single_section():
    text, description = parse_text_description("## DESCRIPTION\nAn official stamp")
    assert text == ""
    assert description == "An official stamp"


def test_parse_text_description_no_header_returns_everything_as_text():
    text, description = parse_text_description("Free-form model response")
    assert text == "Free-form model response"
    assert description == ""


def test_claude_provider_filters_vision_models():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.models.list.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(id="claude-sonnet-5", display_name="Claude Sonnet 5"),
            SimpleNamespace(id="claude-instant-1", display_name="Claude Instant"),
        ]
    )
    models = provider.list_vision_models()
    assert [m.id for m in models] == ["claude-sonnet-5"]


def test_claude_provider_transcribe_parses_the_result():
    provider = ClaudeProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="## TEXT\nHello")],
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
    )

    result = provider.transcribe(b"fake-bytes", model="claude-sonnet-5", prompt="Transcribe.")

    assert result.text == "Hello"
    assert result.tokens_in == 42
    assert result.tokens_out == 7
    assert result.model == "claude-sonnet-5"


def test_claude_provider_transcribe_logs_debug_request_and_response(caplog):
    """Verbose mode (Logs tab) surfaces provider request/response details at DEBUG —
    check the API key itself never ends up in a log line.
    """
    provider = ClaudeProvider(api_key="super-secret-key")
    provider._client = MagicMock()
    provider._client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="## TEXT\nHello")],
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
    )

    with caplog.at_level(logging.DEBUG, logger="regeste.core.providers.claude"):
        provider.transcribe(b"fake-bytes", model="claude-sonnet-5", prompt="Transcribe.")

    messages = " | ".join(caplog.messages)
    assert "claude-sonnet-5" in messages
    assert "tokens_in=42" in messages and "tokens_out=7" in messages
    assert "super-secret-key" not in messages


def test_gemini_provider_filters_generatecontent_and_excludes_text_only():
    provider = GeminiProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.models.list.return_value = [
        SimpleNamespace(name="gemini-2.5-pro", display_name="Gemini 2.5 Pro", supported_actions=["generateContent"]),
        SimpleNamespace(name="gemini-2.5-pro-text", display_name="Text only", supported_actions=["generateContent"]),
        SimpleNamespace(name="gemini-embed", display_name="Embeddings", supported_actions=["embedContent"]),
    ]
    models = provider.list_vision_models()
    assert [m.id for m in models] == ["gemini-2.5-pro"]


def test_gemini_provider_keeps_legacy_vision_named_models():
    provider = GeminiProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.models.list.return_value = [
        SimpleNamespace(
            name="gemini-pro-vision",
            display_name="Gemini Pro Vision",
            supported_actions=["generateContent"],
        ),
        SimpleNamespace(
            name="gemini-embed",
            display_name="Embeddings",
            supported_actions=["embedContent"],
        ),
    ]
    models = provider.list_vision_models()
    assert [m.id for m in models] == ["gemini-pro-vision"]


def test_gemini_provider_transcribe_parses_the_result():
    provider = GeminiProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.models.generate_content.return_value = SimpleNamespace(
        text="## DESCRIPTION\nAn old photograph",
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=8),
    )

    result = provider.transcribe(b"fake-bytes", model="gemini-2.5-pro", prompt="Transcribe.")

    assert result.description == "An old photograph"
    assert result.tokens_in == 12
    assert result.tokens_out == 8


def test_openai_compat_provider_transcribe_parses_the_result():
    provider = OpenAICompatProvider(base_url="http://localhost:1234/v1", kind="lm_studio")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="## TEXT\nA letter"))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
    )

    result = provider.transcribe(b"fake-bytes", model="qwen2.5vl", prompt="Transcribe.")

    assert result.text == "A letter"
    assert result.tokens_in == 5
    assert result.tokens_out == 3


def test_openai_compat_provider_filters_by_name_for_lm_studio():
    provider = OpenAICompatProvider(base_url="http://localhost:1234/v1", kind="lm_studio")
    provider._client = MagicMock()
    provider._client.models.list.return_value = SimpleNamespace(
        data=[SimpleNamespace(id="qwen2.5-vl-7b"), SimpleNamespace(id="llama-3.1-8b-instruct")]
    )
    models = provider.list_vision_models()
    assert [m.id for m in models] == ["qwen2.5-vl-7b"]


def test_openai_compat_provider_requires_api_key_only_for_openai():
    assert OpenAICompatProvider(base_url="http://x", kind="openai").requires_api_key is True
    assert OpenAICompatProvider(base_url="http://x", kind="ollama").requires_api_key is False


def test_ollama_lists_only_models_with_vision_capability(monkeypatch):
    provider = OpenAICompatProvider(base_url="http://localhost:11434/v1", kind="ollama")

    def fake_get(url, timeout):
        assert url == "http://localhost:11434/api/tags"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"models": [{"name": "qwen2.5vl"}, {"name": "llama3.1"}]},
        )

    def fake_post(url, json, timeout):
        assert url == "http://localhost:11434/api/show"
        capabilities = ["vision", "completion"] if json["model"] == "qwen2.5vl" else ["completion"]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"capabilities": capabilities})

    mock_session = SimpleNamespace(get=fake_get, post=fake_post)
    provider._session = mock_session

    models = provider.list_vision_models()

    assert [m.id for m in models] == ["qwen2.5vl"]


def test_ollama_caches_api_show_responses(monkeypatch):
    provider = OpenAICompatProvider(base_url="http://localhost:11434/v1", kind="ollama")
    show_calls = []

    def fake_get(url, timeout):
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"models": [{"name": "qwen2.5vl"}]}
        )

    def fake_post(url, json, timeout):
        show_calls.append(json["model"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"capabilities": ["vision"]})

    mock_session = SimpleNamespace(get=fake_get, post=fake_post)
    provider._session = mock_session

    provider.list_vision_models()
    provider.list_vision_models()

    assert show_calls == ["qwen2.5vl"]  # second call served from cache


def test_ollama_vision_cache_survives_across_provider_instances(monkeypatch):
    """The GUI/CLI recreate a fresh Provider on every 'Fetch models' click /
    Settings open — the cache must live above the instance (process-wide,
    keyed by base_url) or it would never actually be reused (spec §2.3)."""
    show_calls = []

    def fake_get(url, timeout):
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"models": [{"name": "qwen2.5vl"}]}
        )

    def fake_post(url, json, timeout):
        show_calls.append(json["model"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"capabilities": ["vision"]})

    mock_session = SimpleNamespace(get=fake_get, post=fake_post)

    first = OpenAICompatProvider(base_url="http://localhost:11434/v1", kind="ollama")
    first._session = mock_session
    first.list_vision_models()

    second = OpenAICompatProvider(base_url="http://localhost:11434/v1", kind="ollama")
    second._session = mock_session
    second.list_vision_models()

    assert show_calls == ["qwen2.5vl"]  # /api/show hit only once across both instances
