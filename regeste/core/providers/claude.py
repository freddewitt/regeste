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


def call_messages(client: Anthropic, *, model: str, content, max_tokens: int = 4096) -> tuple[str, int, int]:
    """Call the Anthropic Messages API and return (raw_text, tokens_in, tokens_out).

    Shared by `ClaudeProvider.transcribe` (image + text content) and
    `translation.provider.ClaudeTranslationProvider.translate` (text-only content) —
    both send a single-turn user message and extract the same response shape.
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    return raw, response.usage.input_tokens, response.usage.output_tokens


class ClaudeProvider(Provider):
    name = "claude"

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

    def _build_content(self, image_bytes: bytes, prompt: str, media_type: str):
        logger.debug(
            "Claude transcribe: image_bytes=%d, prompt_chars=%d",
            len(image_bytes), len(prompt),
        )
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _MEDIA_TYPES.get(media_type, "image/jpeg"),
                    "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                },
            },
            {"type": "text", "text": prompt},
        ]

    def _call_api(self, model: str, content) -> tuple[str, int, int]:
        logger.debug("Claude calling API: model=%s", model)
        raw, tokens_in, tokens_out = call_messages(self._client, model=model, content=content)
        logger.debug(
            "Claude response: tokens_in=%d, tokens_out=%d, raw_chars=%d",
            tokens_in, tokens_out, len(raw),
        )
        return raw, tokens_in, tokens_out
