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

    def transcribe(
        self,
        image_bytes: bytes,
        *,
        model: str,
        prompt: str,
        forced_language: str | None = None,
        media_type: str = "jpeg",
    ) -> TranscriptionResult:
        full_prompt = augment_prompt(prompt, forced_language)
        logger.debug(
            "Gemini transcribe: model=%s, image_bytes=%d, prompt_chars=%d",
            model, len(image_bytes), len(full_prompt),
        )

        response = self._client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes, mime_type=_MEDIA_TYPES.get(media_type, "image/jpeg")
                ),
                full_prompt,
            ],
        )
        raw = response.text or ""
        text, description, language = parse_all(raw)
        usage = response.usage_metadata
        logger.debug(
            "Gemini response: tokens_in=%d, tokens_out=%d, raw_chars=%d, text_chars=%d, description_chars=%d",
            usage.prompt_token_count if usage else 0,
            usage.candidates_token_count if usage else 0,
            len(raw), len(text), len(description),
        )
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=usage.prompt_token_count if usage else 0,
            tokens_out=usage.candidates_token_count if usage else 0,
            model=model,
            language=language,
        )
