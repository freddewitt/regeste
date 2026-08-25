"""Gemini provider (`google-genai` SDK)."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from .base import (
    _MEDIA_TYPES,
    ModelInfo,
    Provider,
    TranscriptionResult,
    augment_prompt,
    parse_all,
)

logger = logging.getLogger(__name__)

# Known non-vision Gemini model families (embeddings, attributed Q&A) plus
# any variant explicitly marked text-only. Everything else that supports
# generateContent is treated as multimodal/vision-capable.
_NON_VISION_MARKERS = ("embed", "aqa")


def _is_non_vision_model(name: str) -> bool:
    lowered = name.lower()
    return name.endswith("-text") or any(marker in lowered for marker in _NON_VISION_MARKERS)


def call_generate_content(client: genai.Client, *, model: str, contents) -> tuple[str, int, int]:
    """Call Gemini's generateContent and return (raw_text, tokens_in, tokens_out).

    Shared by `GeminiProvider.transcribe` (image + text contents) and
    `translation.provider.GeminiTranslationProvider.translate` (text-only contents).
    """
    response = client.models.generate_content(model=model, contents=contents)
    raw = response.text or ""
    usage = response.usage_metadata
    tokens_in = usage.prompt_token_count if usage else 0
    tokens_out = usage.candidates_token_count if usage else 0
    return raw, tokens_in, tokens_out


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    @property
    def requires_api_key(self) -> bool:
        return True

    def list_vision_models(self) -> list[ModelInfo]:
        logger.debug("Gemini: fetching model list")
        models = list(self._client.models.list())
        result = [
            ModelInfo(id=m.name, display_name=m.display_name or m.name, requires_api_key=True)
            for m in models
            if "generateContent" in (m.supported_actions or [])
            # Gemini exposes vision on ~all multimodal generateContent models
            # (including legacy names explicitly marked "vision", e.g. "gemini-pro-vision");
            # only exclude known non-vision families (embeddings/QA-only) and
            # variants explicitly marked text-only (e.g. "*-text").
            and not _is_non_vision_model(m.name or "")
        ]
        logger.debug("Gemini: %d model(s) returned, %d vision-capable", len(models), len(result))
        return result

    def _build_content(self, image_bytes: bytes, prompt: str, media_type: str):
        logger.debug(
            "Gemini transcribe: image_bytes=%d, prompt_chars=%d",
            len(image_bytes), len(prompt),
        )
        return [
            types.Part.from_bytes(
                data=image_bytes, mime_type=_MEDIA_TYPES.get(media_type, "image/jpeg")
            ),
            prompt,
        ]

    def _call_api(self, model: str, contents) -> tuple[str, int, int]:
        raw, tokens_in, tokens_out = call_generate_content(self._client, model=model, contents=contents)
        logger.debug(
            "Gemini response: tokens_in=%d, tokens_out=%d, raw_chars=%d",
            tokens_in, tokens_out, len(raw),
        )
        return raw, tokens_in, tokens_out
