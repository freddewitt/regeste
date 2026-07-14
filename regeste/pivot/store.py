"""Per-piece JSON persistence under `source_dir/data/pivot/` — travels with the source archive,
same rationale as `regeste.json` (`core/registry.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

from .atomic_io import write_json_atomic
from .models import Piece

PIVOT_DIRNAME = Path("data") / "pivot"


def pivot_dir(source_dir: Path) -> Path:
    return source_dir / PIVOT_DIRNAME


def piece_path(source_dir: Path, piece_id: str) -> Path:
    return pivot_dir(source_dir) / f"{piece_id}.json"


def save_piece(source_dir: Path, piece: Piece) -> Path:
    path = piece_path(source_dir, piece.id)
    write_json_atomic(path, piece.to_meta())
    return path


def load_piece(source_dir: Path, piece_id: str) -> Piece | None:
    path = piece_path(source_dir, piece_id)
    if not path.exists():
        return None
    return Piece.from_meta(json.loads(path.read_text(encoding="utf-8")))


def load_corpus(source_dir: Path) -> list[Piece]:
    directory = pivot_dir(source_dir)
    if not directory.exists():
        return []
    return [
        Piece.from_meta(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]


def bundle_corpus(source_dir: Path, output_path: Path) -> Path:
    """Bundle every piece into a single JSON file (optional, e.g. for archiving/sharing)."""
    pieces = load_corpus(source_dir)
    write_json_atomic(output_path, [p.to_meta() for p in pieces])
    return output_path
