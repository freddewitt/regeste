"""Claude provider (`anthropic` SDK)."""

from __future__ import annotations

import base64
import logging

from anthropic import Anthropic

from .base import (
    _MEDIA_TYPES,
    ModelInfo,
    Provider,
    TranscriptionResult,
    augment_prompt,
    parse_all,
)

logger = logging.getLogger(__name__)

# Anthropic doesn't publish a "vision" flag via models.list(): filter on known
# families that support images (spec §2.3 — no dynamic detection possible here,
# unlike Ollama).
_VISION_FAMILIES = ("claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku")


class ClaudeProvider(Provider):
    name = "claude"
    # TODO: factoriser avec translation/provider.py ClaudeTranslationProvider — patterns communs SDK Anthropic

    def __init__(self, api_key: str) -> None:
        self._client = Anthropic(api_key=api_key)

    @property
    def requires_api_key(self) -> bool:
        return True

    def list_vision_models(self) -> list[ModelInfo]:
        logger.debug("Claude: fetching model list")
        models = self._client.models.list()
        result = [
            ModelInfo(id=m.id, display_name=m.display_name or m.id, requires_api_key=True)
            for m in models.data
            if any(family in m.id for family in _VISION_FAMILIES)
        ]
        logger.debug("Claude: %d model(s) returned, %d vision-capable", len(models.data), len(result))
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
            "Claude transcribe: model=%s, image_bytes=%d, prompt_chars=%d",
            model, len(image_bytes), len(full_prompt),
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
        text, description, language = parse_all(raw)
        logger.debug(
            "Claude response: tokens_in=%d, tokens_out=%d, raw_chars=%d, text_chars=%d, description_chars=%d",
            response.usage.input_tokens, response.usage.output_tokens, len(raw), len(text), len(description),
        )
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model=model,
            language=language,
        )
