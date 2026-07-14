"""Shared helpers for per-piece exporters — every exporter here is a pure, read-only
function over `Piece` objects (no exporter writes to the pivot).
"""

from __future__ import annotations

from regeste.pivot import Piece, global_status


def filter_pieces(pieces: list[Piece], *, validated_only: bool = False) -> list[Piece]:
    if not validated_only:
        return list(pieces)
    return [p for p in pieces if global_status(p) == "validated"]


def hierarchy_path(piece: Piece) -> tuple[str, ...]:
    """Fonds/série/sous-série/dossier path, skipping empty levels."""
    return tuple(
        level for level in (piece.fonds, piece.series, piece.subseries, piece.folder) if level
    )
