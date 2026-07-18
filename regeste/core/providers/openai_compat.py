"""OpenAI-compatible provider — covers OpenAI, LM Studio, llama.cpp and Ollama.

All four backends speak the same chat completions protocol
(`/v1/chat/completions`). Only vision-model detection differs
(spec §2.3, the critical blind spot):

- **Ollama**: `/api/tags` lists every model without indicating its
  capabilities. Each model must be queried via `/api/show` and only kept if
  `capabilities` contains `"vision"`. Cached at the class level (keyed by
  `base_url`), for the lifetime of the process, so the check doesn't re-run
  every time Settings is opened — the GUI and CLI both create a fresh
  `Provider` instance on each open, so an instance-level cache would never
  survive between opens.
- **OpenAI, LM Studio, llama.cpp**: none of these three APIs expose a vision
  capability. Filter on the model name instead (heuristic) — the user remains
  responsible for installing a vision model (e.g. `qwen2.5vl` locally).
"""

from __future__ import annotations

import base64
import logging
from typing import ClassVar

import requests
from openai import OpenAI

from .base import (
    _MEDIA_TYPES,
    ModelInfo,
    Provider,
    TranscriptionResult,
    augment_prompt,
    parse_all,
)

logger = logging.getLogger(__name__)

# Naming heuristic for OpenAI / LM Studio / llama.cpp, since their APIs expose
# no vision capability. Ollama doesn't use this list: it has its own reliable
# mechanism via /api/show.
_VISION_NAME_HINTS = (
    "vision",
    "vl",
    "llava",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "o1",
    "o3",
    "o4",
    "pixtral",
    "minicpm-v",
)

KINDS = ("openai", "lm_studio", "llama_cpp", "ollama")


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    # Process-wide cache of Ollama `/api/show` results, keyed by base_url so
    # distinct Ollama servers don't share results. The GUI and CLI both
    # instantiate a new Provider on every "Fetch models" click / Settings
    # open, so this must live above the instance to actually be reused
    # (spec §2.3).
    _ollama_vision_cache_by_base_url: ClassVar[dict[str, dict[str, bool]]] = {}

    def __init__(self, base_url: str, api_key: str | None = None, *, kind: str = "openai") -> None:
        if kind not in KINDS:
            raise ValueError(f"invalid kind: {kind!r} (expected {KINDS})")
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._base_url = base_url.rstrip("/")
        self._kind = kind
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        """Return a reusable HTTP session (connection pooling across calls)."""
        if self._session is None:
            self._session = requests.Session()
        return self._session

    @property
    def requires_api_key(self) -> bool:
        return self._kind == "openai"

    def list_vision_models(self) -> list[ModelInfo]:
        if self._kind == "ollama":
            return self._list_ollama_vision_models()
        logger.debug("%s: fetching model list from %s", self._kind, self._base_url)
        models = self._client.models.list()
        result = [
            ModelInfo(
                id=m.id,
                display_name=m.id,
                requires_api_key=self.requires_api_key,
                base_url=self._base_url,
            )
            for m in models.data
            if self._looks_like_vision(m.id)
        ]
        logger.debug(
            "%s: %d model(s) returned, %d vision-capable", self._kind, len(models.data), len(result)
        )
        return result

    def _looks_like_vision(self, model_id: str) -> bool:
        lowered = model_id.lower()
        return any(hint in lowered for hint in _VISION_NAME_HINTS)

    def _ollama_root(self) -> str:
        # base_url for chat completions is .../v1 ; /api/tags and /api/show
        # live at the root of the Ollama server.
        return self._base_url.removesuffix("/v1")

    def _list_ollama_vision_models(self) -> list[ModelInfo]:
        logger.debug("Ollama: fetching model tags from %s", self._ollama_root())
        tags = self._get_session().get(f"{self._ollama_root()}/api/tags", timeout=10)
        tags.raise_for_status()
        names = [m["name"] for m in tags.json().get("models", [])]
        logger.debug("Ollama: %d model(s) found", len(names))

        cache = self._ollama_vision_cache_by_base_url.setdefault(self._base_url, {})

        result = []
        for name in names:
            if name not in cache:
                logger.debug("Ollama: checking vision capability for %s (not cached)", name)
                show = self._get_session().post(
                    f"{self._ollama_root()}/api/show", json={"model": name}, timeout=10
                )
                show.raise_for_status()
                capabilities = show.json().get("capabilities", [])
                cache[name] = "vision" in capabilities
            if cache[name]:
                result.append(
                    ModelInfo(id=name, display_name=name, requires_api_key=False, base_url=self._base_url)
                )
        logger.debug("Ollama: %d vision-capable model(s)", len(result))
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
            "%s transcribe: model=%s, base_url=%s, image_bytes=%d, prompt_chars=%d",
            self._kind, model, self._base_url, len(image_bytes), len(full_prompt),
        )

        mime = _MEDIA_TYPES.get(media_type, "image/jpeg")
        data_url = f"data:{mime};base64,{base64.standard_b64encode(image_bytes).decode('ascii')}"

        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": full_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        raw = response.choices[0].message.content or ""
        text, description, language = parse_all(raw)
        usage = response.usage
        logger.debug(
            "%s response: tokens_in=%d, tokens_out=%d, raw_chars=%d, text_chars=%d, description_chars=%d",
            self._kind,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
            len(raw), len(text), len(description),
        )
        return TranscriptionResult(
            text=text,
            description=description,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            model=model,
            language=language,
        )
