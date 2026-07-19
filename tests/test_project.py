"""Round-trip tests for `ProjectConfig` serialization (`to_meta()` / `from_meta()`)."""

from __future__ import annotations

from pathlib import Path

from regeste.core.imaging import ResizeOptions
from regeste.core.export import ExportOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.transcription_mode import TranscriptionMode


def _base_config(**overrides) -> ProjectConfig:
    kwargs = dict(
        project_name="p",
        source_dir=Path("/tmp/source"),
        output_dir=Path("/tmp/output"),
        provider=ProviderConfig(kind="claude", model="fake-model", api_key="fake-key"),
    )
    kwargs.update(overrides)
    return ProjectConfig(**kwargs)


def test_system_prompt_round_trips_when_set():
    config = _base_config(system_prompt="You are a custom transcriber.")
    restored = ProjectConfig.from_meta(config.to_meta())
    assert restored.system_prompt == "You are a custom transcriber."


def test_system_prompt_round_trips_when_none():
    config = _base_config(system_prompt=None)
    meta = config.to_meta()
    assert meta["system_prompt"] is None
    restored = ProjectConfig.from_meta(meta)
    assert restored.system_prompt is None


def test_system_prompt_defaults_to_none_when_absent_from_meta():
    """Backward compatibility: a `regeste.json` written before this field existed."""
    config = _base_config()
    meta = config.to_meta()
    del meta["system_prompt"]
    restored = ProjectConfig.from_meta(meta)
    assert restored.system_prompt is None


def test_ui_language_round_trips_when_set():
    config = _base_config(ui_language="ja")
    restored = ProjectConfig.from_meta(config.to_meta())
    assert restored.ui_language == "ja"


def test_ui_language_round_trips_when_none():
    config = _base_config(ui_language=None)
    meta = config.to_meta()
    assert meta["ui_language"] is None
    restored = ProjectConfig.from_meta(meta)
    assert restored.ui_language is None


def test_ui_language_defaults_to_none_when_absent_from_meta():
    """Backward compatibility: a `regeste.json` written before this field existed."""
    config = _base_config()
    meta = config.to_meta()
    del meta["ui_language"]
    restored = ProjectConfig.from_meta(meta)
    assert restored.ui_language is None


def test_resize_max_bytes_override_round_trips_when_set():
    config = _base_config(resize=ResizeOptions(max_bytes_override=1_000_000))
    meta = config.to_meta()
    assert meta["resize"]["max_bytes_override"] == 1_000_000
    restored = ProjectConfig.from_meta(meta)
    assert restored.resize.max_bytes_override == 1_000_000


def test_resize_max_bytes_override_defaults_to_none_when_absent_from_meta():
    """Backward compatibility: a `regeste.json` written before this field existed."""
    config = _base_config()
    meta = config.to_meta()
    del meta["resize"]["max_bytes_override"]
    restored = ProjectConfig.from_meta(meta)
    assert restored.resize.max_bytes_override is None


def test_transcription_mode_round_trips_when_hypotheses():
    config = _base_config(
        transcription_mode=TranscriptionMode.HYPOTHESES,
        export=ExportOptions(
            formats=frozenset({"md"}), transcription_mode=TranscriptionMode.HYPOTHESES
        ),
    )
    meta = config.to_meta()
    assert meta["transcription_mode"] == "hypotheses"
    assert meta["export"]["transcription_mode"] == "hypotheses"
    restored = ProjectConfig.from_meta(meta)
    assert restored.transcription_mode is TranscriptionMode.HYPOTHESES
    assert restored.export.transcription_mode is TranscriptionMode.HYPOTHESES


def test_transcription_mode_defaults_to_literal_when_absent_from_meta():
    """Backward compatibility: a `regeste.json` written before this field existed."""
    config = _base_config()
    meta = config.to_meta()
    del meta["transcription_mode"]
    del meta["export"]["transcription_mode"]
    restored = ProjectConfig.from_meta(meta)
    assert restored.transcription_mode is TranscriptionMode.LITERAL
    assert restored.export.transcription_mode is TranscriptionMode.LITERAL


def test_transcription_mode_export_falls_back_to_top_level_key():
    """Meta written with only the top-level key still propagates to export options."""
    config = _base_config(transcription_mode=TranscriptionMode.HYPOTHESES)
    meta = config.to_meta()
    del meta["export"]["transcription_mode"]
    restored = ProjectConfig.from_meta(meta)
    assert restored.export.transcription_mode is TranscriptionMode.HYPOTHESES
