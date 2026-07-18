"""Translation module — third-party SDKs are always mocked, no real network calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from regeste.pivot import CONTENT_FIELDS, FieldValidation, NamedEntity, Piece, hash_transcription
from regeste.translation import (
    ClaudeTranslationProvider,
    GeminiTranslationProvider,
    OpenAICompatTranslationProvider,
    TranslationBlocked,
    TranslationProvider,
    TranslationResult,
    build_prompt,
    check_guards,
    load_glossary,
    save_glossary,
    translate_piece,
)


def _validated_piece(**overrides) -> Piece:
    kwargs = dict(
        id="a.jpg",
        transcription="Cher Monsieur, je vous écris depuis Lyon.",
        confidence_score=0.9,
        field_validations={f: FieldValidation(status="validated") for f in CONTENT_FIELDS},
    )
    kwargs.update(overrides)
    return Piece(**kwargs)


class _FakeProvider(TranslationProvider):
    name = "fake"

    def __init__(self, text: str = "Dear Sir, I am writing from Lyon.") -> None:
        self._text = text
        self.received_prompt: str | None = None
        self.received_model: str | None = None

    @property
    def requires_api_key(self) -> bool:
        return False

    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        self.received_prompt = prompt
        self.received_model = model
        return TranslationResult(text=self._text, tokens_in=10, tokens_out=8, model=model)


# --- guards -----------------------------------------------------------------


def test_check_guards_blocks_non_validated_piece():
    piece = Piece(id="a.jpg", transcription="brouillon")
    guard = check_guards(piece)
    assert guard.allowed is False
    assert guard.blocked_reason


def test_check_guards_warns_on_low_confidence_but_allows():
    piece = _validated_piece(confidence_score=0.2)
    guard = check_guards(piece)
    assert guard.allowed is True
    assert any("low" in w for w in guard.warnings)


def test_check_guards_warns_on_unknown_confidence():
    piece = _validated_piece(confidence_score=None)
    guard = check_guards(piece)
    assert guard.allowed is True
    assert any("unknown" in w for w in guard.warnings)


def test_check_guards_no_warning_on_high_confidence():
    piece = _validated_piece(confidence_score=0.95)
    guard = check_guards(piece)
    assert guard.allowed is True
    assert guard.warnings == []


# --- prompt building ----------------------------------------------------


def test_build_prompt_includes_source_text():
    piece = _validated_piece()
    prompt = build_prompt(piece, "en")
    assert piece.transcription in prompt
    assert "en" in prompt


def test_build_prompt_includes_validated_entities_only():
    piece = _validated_piece(
        entities=[
            NamedEntity(text="Lyon", entity_type="lieu", validation=FieldValidation(status="validated")),
            NamedEntity(text="Marseille", entity_type="lieu", validation=FieldValidation(status="draft")),
        ]
    )
    prompt = build_prompt(piece, "en")
    assert "Lyon" in prompt
    assert "Marseille" not in prompt


def test_build_prompt_includes_glossary():
    piece = _validated_piece()
    prompt = build_prompt(piece, "en", glossary={"cote": "call number"})
    assert "cote" in prompt
    assert "call number" in prompt


def test_build_prompt_substitutes_source_language():
    piece = _validated_piece()
    prompt = build_prompt(piece, "fr", source_language="italien")
    assert "italien" in prompt
    assert "{langue_source}" not in prompt


def test_build_prompt_custom_template_skips_injection_when_placeholder_removed():
    piece = _validated_piece()
    # A template without {glossaire} disables the glossary injection.
    template = "Traduire vers {langue_cible} : {texte_source}"
    prompt = build_prompt(piece, "en", glossary={"cote": "call number"}, template=template)
    assert piece.transcription in prompt
    assert "call number" not in prompt


# --- translate_piece ------------------------------------------------------


def test_translate_piece_blocked_on_non_validated_piece():
    piece = Piece(id="a.jpg", transcription="brouillon")
    provider = _FakeProvider()
    with pytest.raises(TranslationBlocked):
        translate_piece(piece, "en", provider, "fake-model")
    assert provider.received_prompt is None


def test_translate_piece_writes_translation():
    piece = _validated_piece()
    provider = _FakeProvider(text="Dear Sir,")
    translate_piece(piece, "en", provider, "fake-model")
    assert piece.translations["en"].text == "Dear Sir,"
    assert piece.translations["en"].provider == "fake"
    assert piece.translations["en"].source_hash == hash_transcription(piece.transcription)


def test_translate_piece_sends_glossary_and_entities_in_prompt():
    piece = _validated_piece(
        entities=[
            NamedEntity(text="Lyon", entity_type="lieu", validation=FieldValidation(status="validated")),
        ]
    )
    provider = _FakeProvider()
    translate_piece(piece, "en", provider, "fake-model", glossary={"cote": "call number"})
    assert "Lyon" in provider.received_prompt
    assert "call number" in provider.received_prompt
    assert piece.transcription in provider.received_prompt


# --- glossary persistence ------------------------------------------------


def test_glossary_round_trip(tmp_path):
    save_glossary(tmp_path, {"cote": "call number"})
    assert load_glossary(tmp_path) == {"cote": "call number"}


def test_glossary_missing_file_returns_empty_dict(tmp_path):
    assert load_glossary(tmp_path) == {}


# --- SDK-backed providers (mocked) ----------------------------------------


def test_claude_translation_provider_parses_result():
    provider = ClaudeTranslationProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Dear Sir,")],
        usage=SimpleNamespace(input_tokens=12, output_tokens=5),
    )
    result = provider.translate("translate this", model="claude-sonnet-5")
    assert result.text == "Dear Sir,"
    assert result.tokens_in == 12
    assert result.tokens_out == 5


def test_gemini_translation_provider_parses_result():
    provider = GeminiTranslationProvider(api_key="fake-key")
    provider._client = MagicMock()
    provider._client.models.generate_content.return_value = SimpleNamespace(
        text="Dear Sir,",
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=5),
    )
    result = provider.translate("translate this", model="gemini-2.5-flash")
    assert result.text == "Dear Sir,"
    assert result.tokens_in == 12
    assert result.tokens_out == 5


def test_openai_compat_translation_provider_parses_result():
    provider = OpenAICompatTranslationProvider(base_url="http://localhost:1234/v1")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Dear Sir,"))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
    )
    result = provider.translate("translate this", model="local-model")
    assert result.text == "Dear Sir,"
    assert result.tokens_in == 12
    assert result.tokens_out == 5
