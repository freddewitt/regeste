"""Portable project archive (spec-adjacent utility): `regeste.json` with API
keys stripped, plus a copy of every image the registry references, bundled
into a single ZIP.

Images are stored under their registry key at the archive root, and
`source.physical_path` is cleared in the bundled registry — this matches the
existing "physical_path empty -> source_dir / file_name" fallback already used
by `Transcriber._preprocess_one()` and `export_registry()`, so a project
extracted from the archive resolves its images correctly regardless of where
or on which machine it is opened, with no change needed to that resolution
logic.
"""

from __future__ import annotations

from regeste.core.atomic import atomic_output

import copy
import json
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from .registry import Registry

REGISTRY_ENTRY_NAME = "regeste.json"


def _sanitized_meta(meta: dict) -> dict:
    """Deep-copy `meta` with every provider's `api_key` stripped."""
    sanitized = copy.deepcopy(meta)
    for key in ("provider", "translation_provider"):
        provider = sanitized.get(key)
        if provider:
            provider["api_key"] = None
    return sanitized


@atomic_output
def export_project_archive(
    registry: Registry,
    output_path: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write `registry` (API keys stripped) and its images to `output_path` (a .zip).

    `on_progress(done, total)` is called after each image is added, if given.
    A referenced image missing from disk is skipped (still counted in progress)
    rather than failing the whole archive.
    """
    files_data = {}
    for name, entry in registry.files.items():
        entry_dict = asdict(entry)
        entry_dict["source"]["physical_path"] = ""
        files_data[name] = entry_dict
    registry_json = json.dumps(
        {"meta": _sanitized_meta(registry.meta), "files": files_data},
        indent=2,
        ensure_ascii=False,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(registry.files)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(REGISTRY_ENTRY_NAME, registry_json)
        for done, (name, entry) in enumerate(registry.files.items(), start=1):
            source_path = (
                Path(entry.source.physical_path)
                if entry.source.physical_path
                else registry.source_dir / name
            )
            if source_path.exists():
                zf.write(source_path, arcname=name)
            if on_progress:
                on_progress(done, total)
    return output_path
