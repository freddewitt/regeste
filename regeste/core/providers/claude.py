"""Claude provider (`anthropic` SDK)."""

from __future__ import annotations

import base64

from anthropic import Anthropic

from regeste.i18n import _

from .base import ModelInfo, Provider, TranscriptionResult, parse_language, parse_text_description

# Anthropic doesn't publish a "vision" flag via models.list(): filter on known
# families that support images (spec §2.3 — no dynamic detection possible here,
# unlike Ollama).
_VISION_FAMILIES = ("claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku")

_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str) -> None:
        self._client = Anthropic(api_key=api_key)

    @property
    def requires_api_key(self) -> bool:
        return True

    def list_vision_models(self) -> list[ModelInfo]:
        models = self._client.models.list()
        return [
            ModelInfo(id=m.id, display_name=m.display_name or m.id, requires_api_key=True)
            for m in models.data
            if any(family in m.id for family in _VISION_FAMILIES)
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

        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": _MEDIA_TYPES.get(media_type, "image/jpeg"),
                                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": full_prompt},
                    ],
                }
            ],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        text, description = parse_text_description(raw)
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model=model,
            language=parse_language(raw),
        )
