"""Translation providers — text-to-text.

Independent from the OCR `Provider` abstraction (`core/providers/base.py`),
which is tightly coupled to image input (`transcribe(image_bytes, ...)`).
Mirrors its architectural style (ABC, dataclass result, `requires_api_key`
property, one class per backend) without sharing code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from anthropic import Anthropic
from google import genai
from openai import OpenAI

KINDS = ("openai", "lm_studio", "llama_cpp", "ollama")


@dataclass(frozen=True)
class TranslationResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class TranslationProvider(ABC):
    name: str

    @property
    @abstractmethod
    def requires_api_key(self) -> bool: ...

    @abstractmethod
    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        """Send `prompt` (already containing source text, glossary and
        entities) and return the translated text."""


def create_translation_provider(
    kind: str, base_url: str | None = None, api_key: str | None = None
) -> TranslationProvider:
    """Build a text-to-text translation provider by kind (shared by GUI and CLI)."""
    if kind == "claude":
        return ClaudeTranslationProvider(api_key=api_key or "")
    if kind == "gemini":
        return GeminiTranslationProvider(api_key=api_key or "")
    return OpenAICompatTranslationProvider(base_url=base_url or "", api_key=api_key, kind=kind)


class ClaudeTranslationProvider(TranslationProvider):
    name = "claude"

    def __init__(self, api_key: str) -> None:
        self._client = Anthropic(api_key=api_key)

    @property
    def requires_api_key(self) -> bool:
        return True

    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return TranslationResult(
            text=text.strip(),
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model=model,
        )


class GeminiTranslationProvider(TranslationProvider):
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    @property
    def requires_api_key(self) -> bool:
        return True

    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        response = self._client.models.generate_content(model=model, contents=prompt)
        usage = response.usage_metadata
        return TranslationResult(
            text=(response.text or "").strip(),
            tokens_in=usage.prompt_token_count if usage else 0,
            tokens_out=usage.candidates_token_count if usage else 0,
            model=model,
        )


class OpenAICompatTranslationProvider(TranslationProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None = None, *, kind: str = "openai") -> None:
        if kind not in KINDS:
            raise ValueError(f"invalid kind: {kind!r} (expected {KINDS})")
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._kind = kind

    @property
    def requires_api_key(self) -> bool:
        return self._kind == "openai"

    def translate(self, prompt: str, *, model: str) -> TranslationResult:
        response = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        return TranslationResult(
            text=text.strip(),
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            model=model,
        )
