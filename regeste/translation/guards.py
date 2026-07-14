"""Guardrails checked before a piece may be translated."""

from __future__ import annotations

from dataclasses import dataclass, field

from regeste.pivot import Piece, global_status

LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    blocked_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def check_guards(piece: Piece) -> GuardResult:
    if global_status(piece) != "validated":
        return GuardResult(allowed=False, blocked_reason="La pièce n'est pas validée.")

    warnings: list[str] = []
    if piece.confidence_score is None:
        warnings.append("Confiance OCR inconnue.")
    elif piece.confidence_score < LOW_CONFIDENCE_THRESHOLD:
        warnings.append("Confiance OCR basse.")
    return GuardResult(allowed=True, warnings=warnings)
