"""Gemini provider (`google-genai` SDK)."""

from __future__ import annotations

from google import genai
from google.genai import types

from regeste.i18n import _

from .base import ModelInfo, Provider, TranscriptionResult, parse_language, parse_text_description

_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}

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
        models = self._client.models.list()
        return [
            ModelInfo(id=m.name, display_name=m.display_name or m.name, requires_api_key=True)
            for m in models
            if "generateContent" in (m.supported_actions or [])
            # Gemini exposes vision on ~all multimodal generateContent models
            # (including legacy names explicitly marked "vision", e.g. "gemini-pro-vision");
            # only exclude known non-vision families (embeddings/QA-only) and
            # variants explicitly marked text-only (e.g. "*-text").
            and not _is_non_vision_model(m.name or "")
        ]

    def transcribe(
        self,
        image_bytes: bytes,
        *,
        model: str,
        prompt: str,
        forced_language: str | None = None,
        media_type: str = "jpeg",
    ) -> TranscriptionResult:
        full_prompt = prompt
        if forced_language:
            full_prompt += "\n\n" + _("Respond in the following language: {lang}").format(
                lang=forced_language
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
        text, description = parse_text_description(raw)
        usage = response.usage_metadata
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=usage.prompt_token_count if usage else 0,
            tokens_out=usage.candidates_token_count if usage else 0,
            model=model,
            language=parse_language(raw),
        )
