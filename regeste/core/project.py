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
from .imaging import PreprocessOptions, ResizeOptions


@dataclass
class ProviderConfig:
    kind: str  # "claude" | "gemini" | "openai" | "lm_studio" | "llama_cpp" | "ollama"
    model: str
    base_url: str | None = None
    api_key: str | None = None  # stored in clear, explicitly accepted (spec §2.4)


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

    def to_meta(self) -> dict[str, Any]:
        """Serialize for storage in `Registry.meta` (regeste.json)."""
        return {
            "project_name": self.project_name,
            "source_dir": str(self.source_dir),
            "output_dir": str(self.output_dir),
            "provider": asdict(self.provider),
            "preprocessing": asdict(self.preprocessing),
            "resize": asdict(self.resize),
            "forced_language": self.forced_language,
            "system_prompt": self.system_prompt,
            "export": {
                "formats": sorted(self.export.formats),
                "single_file": self.export.single_file,
                "per_file": self.export.per_file,
            },
            "rates": {name: asdict(rate) for name, rate in self.rates.items()},
            "spend_ceiling": self.spend_ceiling,
            "workers": self.workers,
            "ui_language": self.ui_language,
            "translation_provider": (
                asdict(self.translation_provider) if self.translation_provider else None
            ),
            "translation_same_as_ocr": self.translation_same_as_ocr,
            "translation_prompt": self.translation_prompt,
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
            provider=ProviderConfig(**meta["provider"]),
            preprocessing=PreprocessOptions(**meta.get("preprocessing", {})),
            resize=ResizeOptions(**meta.get("resize", {})),
            forced_language=meta.get("forced_language"),
            system_prompt=meta.get("system_prompt"),
            export=ExportOptions(
                formats=frozenset(raw_export.get("formats", ["md", "json"])),
                single_file=raw_export.get("single_file", True),
                per_file=raw_export.get("per_file", True),
            ),
            rates=(
                {name: Rate(**values) for name, values in raw_rates.items()}
                if raw_rates
                else dict(DEFAULT_RATES)
            ),
            spend_ceiling=meta.get("spend_ceiling"),
            workers=meta.get("workers", 4),
            ui_language=meta.get("ui_language"),
            translation_provider=(
                ProviderConfig(**raw_tp) if (raw_tp := meta.get("translation_provider")) else None
            ),
            translation_same_as_ocr=meta.get("translation_same_as_ocr", True),
            translation_prompt=meta.get("translation_prompt"),
        )
