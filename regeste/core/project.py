"""Per-project config — persisted in the registry snapshot (spec §8, "Settings persistence").

Opening a project (Resume mode) must restore its exact state: provider,
model, keys, preprocessing chain, exports, rates, workers…
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .costs import DEFAULT_RATES, Rate
from .export import ExportOptions
from .secrets import load_api_key, store_api_key
from .imaging import PreprocessOptions, ResizeOptions
from .transcription_mode import TranscriptionMode


@dataclass
class ProviderConfig:
    kind: str  # "claude" | "gemini" | "openai" | "lm_studio" | "llama_cpp" | "ollama"
    model: str
    base_url: str | None = None
    api_key: str | None = None  # in memory only: persisted in the OS keychain, not in regeste.json

    def to_meta(self) -> dict[str, Any]:
        """Serialize without the key, which goes to the keychain instead."""
        store_api_key(self.kind, self.base_url, self.api_key)
        return {**asdict(self), "api_key": None}

    @classmethod
    def from_meta(cls, raw: dict[str, Any]) -> "ProviderConfig":
        """Rebuild from stored data; a legacy clear-text key is moved to the keychain."""
        config = cls(**raw)
        if config.api_key:
            store_api_key(config.kind, config.base_url, config.api_key)
        else:
            config.api_key = load_api_key(config.kind, config.base_url)
        return config


@dataclass
class ProjectConfig:
    project_name: str
    source_dir: Path
    output_dir: Path
    provider: ProviderConfig
    preprocessing: PreprocessOptions = field(default_factory=PreprocessOptions)
    resize: ResizeOptions = field(default_factory=ResizeOptions)
    forced_language: str | None = None
    # None means "use Transcriber.DEFAULT_SYSTEM_PROMPT" - kept as an explicit None here
    # rather than importing the constant, to avoid a circular import (transcriber.py
    # already imports ProjectConfig from this module).
    system_prompt: str | None = None
    export: ExportOptions = field(
        default_factory=lambda: ExportOptions(formats=frozenset({"md", "json"}))
    )
    # Canonical transcription mode for the OCR run (LITERAL keeps the historical
    # prompt). `export.transcription_mode` mirrors it so exports include the
    # [[...]] legend too - the GUI/CLI set both from the same control.
    transcription_mode: TranscriptionMode = TranscriptionMode.LITERAL
    rates: dict[str, Rate] = field(default_factory=lambda: dict(DEFAULT_RATES))
    spend_ceiling: float | None = None
    workers: int = 4
    ui_language: str | None = None
    # The separate translation provider/model, kept even while "same as OCR" is on
    # so toggling never loses the last choice (spec: same-or-separate).
    translation_provider: ProviderConfig | None = None
    translation_same_as_ocr: bool = True
    # None means "use the default translation prompt".
    translation_prompt: str | None = None
    # Opt-in: off by default (sequential) - translation providers are more likely
    # to rate-limit than vision providers under concurrent load, so this isn't
    # turned on automatically the way OCR's `workers` is.
    translation_parallel: bool = False
    # Opt-in: off by default. When on, each file's OCR output is written to
    # `output_dir` as soon as it's transcribed (no waiting for manual review),
    # and the app doesn't auto-switch to the Review tab once the run finishes.
    no_review: bool = False

    def to_meta(self) -> dict[str, Any]:
        """Serialize for storage in `Registry.meta` (regeste.json)."""
        return {
            "project_name": self.project_name,
            "source_dir": str(self.source_dir),
            "output_dir": str(self.output_dir),
            "provider": self.provider.to_meta(),
            "preprocessing": asdict(self.preprocessing),
            "resize": asdict(self.resize),
            "forced_language": self.forced_language,
            "system_prompt": self.system_prompt,
            "export": {
                "formats": sorted(self.export.formats),
                "single_file": self.export.single_file,
                "per_file": self.export.per_file,
                "transcription_mode": self.export.transcription_mode.value,
            },
            "transcription_mode": self.transcription_mode.value,
            "rates": {name: asdict(rate) for name, rate in self.rates.items()},
            "spend_ceiling": self.spend_ceiling,
            "workers": self.workers,
            "ui_language": self.ui_language,
            "translation_provider": (
                self.translation_provider.to_meta() if self.translation_provider else None
            ),
            "translation_same_as_ocr": self.translation_same_as_ocr,
            "translation_prompt": self.translation_prompt,
            "translation_parallel": self.translation_parallel,
            "no_review": self.no_review,
        }

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> "ProjectConfig":
        """Rebuild the config from `Registry.meta`, defaulting any missing fields."""
        raw_export = meta.get("export", {})
        raw_rates = meta.get("rates")
        return cls(
            project_name=meta["project_name"],
            source_dir=Path(meta["source_dir"]),
            output_dir=Path(meta["output_dir"]),
            provider=ProviderConfig.from_meta(meta["provider"]),
            preprocessing=PreprocessOptions(**meta.get("preprocessing", {})),
            resize=ResizeOptions(**meta.get("resize", {})),
            forced_language=meta.get("forced_language"),
            system_prompt=meta.get("system_prompt"),
            export=ExportOptions(
                formats=frozenset(raw_export.get("formats", ["md", "json"])),
                single_file=raw_export.get("single_file", True),
                per_file=raw_export.get("per_file", True),
                transcription_mode=TranscriptionMode.from_value(
                    # Backward compat: older projects only have the top-level key
                    # (or none at all) - fall back to it, then to LITERAL.
                    raw_export.get("transcription_mode", meta.get("transcription_mode"))
                ),
            ),
            transcription_mode=TranscriptionMode.from_value(meta.get("transcription_mode")),
            rates=(
                {name: Rate(**values) for name, values in raw_rates.items()}
                if raw_rates
                else dict(DEFAULT_RATES)
            ),
            spend_ceiling=meta.get("spend_ceiling"),
            workers=meta.get("workers", 4),
            ui_language=meta.get("ui_language"),
            translation_provider=(
                ProviderConfig.from_meta(raw_tp) if (raw_tp := meta.get("translation_provider")) else None
            ),
            translation_same_as_ocr=meta.get("translation_same_as_ocr", True),
            translation_prompt=meta.get("translation_prompt"),
            translation_parallel=meta.get("translation_parallel", False),
            no_review=meta.get("no_review", False),
        )
