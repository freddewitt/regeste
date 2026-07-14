"""Common interface for all vision providers (Claude, Gemini, OpenAI-compatible)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

_SECTION_RE = re.compile(
    r"##\s*(TEXT|DESCRIPTION|LANGUE)\s*\n(.*?)(?=\n##\s*(?:TEXT|DESCRIPTION|LANGUE)\s*\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def parse_text_description(raw: str) -> tuple[str, str]:
    """Split a model response into (text, description) via `## TEXT` / `## DESCRIPTION` headers.

    Shared by all providers: the output contract (spec §4) is the same regardless
    of the backend, only the network call differs. If no section is found (the
    model didn't follow the format), the raw response is returned as text, with
    an empty description.
    """
    sections = {m.group(1).upper(): m.group(2).strip() for m in _SECTION_RE.finditer(raw)}
    if not sections:
        return raw.strip(), ""
    return sections.get("TEXT", ""), sections.get("DESCRIPTION", "")


def parse_language(raw: str) -> str:
    """Return the `## LANGUE` section (detected document language), or "" if absent.

    Optional section of the same output contract: a model that omits it (or an
    older prompt without it) simply yields "".
    """
    for match in _SECTION_RE.finditer(raw):
        if match.group(1).upper() == "LANGUE":
            return match.group(2).strip()
    return ""


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

    @abstractmethod
    def transcribe(
        self,
        image_bytes: bytes,
        *,
        model: str,
        prompt: str,
        forced_language: str | None = None,
    ) -> TranscriptionResult:
        """Send an already-resized image and return text + description.

        The model must respond with tagged sections (`## TEXT` / `## DESCRIPTION`)
        that the implementation is responsible for parsing.
        """

    @property
    @abstractmethod
    def requires_api_key(self) -> bool:
        """True if this provider needs an API key to work."""
