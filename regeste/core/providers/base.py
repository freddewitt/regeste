"""Common interface for all vision providers (Claude, Gemini, OpenAI-compatible)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def augment_prompt(prompt: str, forced_language: str | None = None) -> str:
    """Append language instruction to prompt if forced_language is set."""
    if not forced_language:
        return prompt
    return prompt + "\n\n" + "Respond in the following language: {lang}".format(lang=forced_language)


_SECTION_RE = re.compile(
    r"##\s*(TEXT|DESCRIPTION|LANGUE)\s*\n(.*?)(?=\n##\s*(?:TEXT|DESCRIPTION|LANGUE)\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def parse_all(raw: str) -> tuple[str, str, str]:
    """Single-pass extraction of `## TEXT`, `## DESCRIPTION` and `## LANGUE` sections.

    Returns ``(text, description, language)`` — each empty string if the
    corresponding section is absent.  Callers that need all three fields should
    use this instead of calling ``parse_text_description`` and ``parse_language``
    separately, to avoid scanning the response twice.
    """
    sections: dict[str, str] = {}
    for m in _SECTION_RE.finditer(raw):
        sections[m.group(1).upper()] = m.group(2).strip()
    if not sections:
        return raw.strip(), "", ""
    return sections.get("TEXT", ""), sections.get("DESCRIPTION", ""), sections.get("LANGUE", "")


def parse_text_description(raw: str) -> tuple[str, str]:
    """Split a model response into (text, description) via `## TEXT` / `## DESCRIPTION` headers.

    Shared by all providers: the output contract (spec §4) is the same regardless
    of the backend, only the network call differs. If no section is found (the
    model didn't follow the format), the raw response is returned as text, with
    an empty description.
    """
    text, description, _ = parse_all(raw)
    return text, description


def parse_language(raw: str) -> str:
    """Return the `## LANGUE` section (detected document language), or "" if absent.

    Optional section of the same output contract: a model that omits it (or an
    older prompt without it) simply yields "".
    """
    _, _, language = parse_all(raw)
    return language


@dataclass(frozen=True)
class ModelInfo:
    """A vision model offered by a provider."""

    id: str
    display_name: str
    requires_api_key: bool
    base_url: str | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    """Result of transcribing a single image.

    `tokens_in`/`tokens_out` are 0 when the backend doesn't report usage
    (some local OpenAI-compatible servers don't).
    """

    text: str
    description: str
    tokens_in: int
    tokens_out: int
    model: str
    language: str = ""


class Provider(ABC):
    """Wraps a vision SDK behind a common interface, hiding backend-specific detail.

    Field names in `regeste.json` and the exports stay identical no matter
    which provider produced the result.
    """

    @abstractmethod
    def list_vision_models(self) -> list[ModelInfo]:
        """Return only the models capable of vision — NEVER a text-only model (spec §2.3,
        the "critical blind spot": some backends expose no capability metadata at all).
        """

    def transcribe(
        self,
        image_bytes: bytes,
        *,
        model: str,
        prompt: str,
        forced_language: str | None = None,
        media_type: str = "jpeg",
    ) -> TranscriptionResult:
        """Send an already-resized image and return text + description.

        Template method: builds content payload, calls backend API, parses result.
        Subclasses override _build_content() and _call_api() only.
        """
        full_prompt = augment_prompt(prompt, forced_language)
        content = self._build_content(image_bytes, full_prompt, media_type)
        raw, tokens_in, tokens_out = self._call_api(model, content)
        text, description, language = parse_all(raw)
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            language=language,
        )

    @abstractmethod
    def _build_content(self, image_bytes: bytes, prompt: str, media_type: str):
        """Build provider-specific content payload for API call.

        Claude: dict with image source + text.
        Gemini: list of Part + text string.
        OpenAI-compat: list with text + image_url.
        """

    @abstractmethod
    def _call_api(self, model: str, content) -> tuple[str, int, int]:
        """Call the backend API and return (raw_text, tokens_in, tokens_out)."""

    @property
    @abstractmethod
    def requires_api_key(self) -> bool:
        """True if this provider needs an API key to work."""
