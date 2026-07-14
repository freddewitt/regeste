"""Round-trip tests for `ProjectConfig` serialization (`to_meta()` / `from_meta()`)."""

from __future__ import annotations

from pathlib import Path

from regeste.core.imaging import ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig


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
