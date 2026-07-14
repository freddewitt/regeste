"""Corpus-level glossary — `source_dir/data/glossary.json`, reinjected into
every translation prompt so terminology stays consistent across pieces.
"""

from __future__ import annotations

import json
from pathlib import Path

from regeste.pivot.atomic_io import write_json_atomic

GLOSSARY_FILENAME = "glossary.json"


def glossary_path(source_dir: Path) -> Path:
    return source_dir / "data" / GLOSSARY_FILENAME


def load_glossary(source_dir: Path) -> dict[str, str]:
    path = glossary_path(source_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_glossary(source_dir: Path, glossary: dict[str, str]) -> Path:
    path = glossary_path(source_dir)
    write_json_atomic(path, glossary)
    return path
