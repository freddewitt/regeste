"""Review queue helpers — sorted by ascending OCR confidence, sampling, bulk validation
by threshold (spec §4).
"""

from __future__ import annotations

import random

from regeste.pivot import CONTENT_FIELDS, Piece

from .validation import apply_field_validation


def sorted_by_confidence(pieces: list[Piece]) -> list[Piece]:
    """Lowest confidence first; pieces with no OCR confidence signal (`None`) come first,
    reviewed with the same caution as low-confidence ones.
    """
    return sorted(
        pieces,
        key=lambda p: (p.confidence_score is not None, p.confidence_score or 0.0),
    )


def sample(pieces: list[Piece], n: int, *, seed: int | None = None) -> list[Piece]:
    """Random sample of up to `n` pieces, for statistical validation on large corpora."""
    rng = random.Random(seed)
    return rng.sample(pieces, min(n, len(pieces)))


def bulk_validate(
    pieces: list[Piece], threshold: float, *, validated_by: str | None = None
) -> list[Piece]:
    """Auto-validate every content field of pieces whose OCR confidence >= `threshold`.

    Pieces with no confidence signal (`None`) are never auto-validated.
    """
    validated = []
    for piece in pieces:
        if piece.confidence_score is None or piece.confidence_score < threshold:
            continue
        for field in CONTENT_FIELDS:
            apply_field_validation(piece, field, "validated", changed_by=validated_by)
        validated.append(piece)
    return validated
