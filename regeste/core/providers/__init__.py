"""Registry of available providers + default base_url for local backends."""

from __future__ import annotations

from .base import ModelInfo, Provider, TranscriptionResult, parse_all, parse_text_description
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider

PROVIDER_KINDS = ("claude", "gemini", "openai", "lm_studio", "llama_cpp", "ollama")
REQUIRES_API_KEY_KINDS = ("claude", "gemini", "openai")

# Default base_url, editable by the user in Settings (spec §2.2).
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "lm_studio": "http://localhost:1234/v1",
    "llama_cpp": "http://localhost:8080/v1",
    "ollama": "http://localhost:11434/v1",
}

__all__ = [
    "ModelInfo",
    "Provider",
    "TranscriptionResult",
    "parse_text_description",
    "ClaudeProvider",
    "GeminiProvider",
    "OpenAICompatProvider",
    "PROVIDER_KINDS",
    "REQUIRES_API_KEY_KINDS",
    "DEFAULT_BASE_URLS",
]
