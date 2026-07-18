"""Guardrails checked before a piece may be translated."""

from __future__ import annotations

from dataclasses import dataclass, field

from regeste.i18n import _
from regeste.pivot import Piece, global_status

LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    blocked_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def check_guards(piece: Piece) -> GuardResult:
    if global_status(piece) != "validated":
        return GuardResult(allowed=False, blocked_reason=_("The piece is not validated."))

    warnings: list[str] = []
    if piece.confidence_score is None:
        warnings.append(_("OCR confidence is unknown."))
    elif piece.confidence_score < LOW_CONFIDENCE_THRESHOLD:
        warnings.append(_("OCR confidence is low."))
    return GuardResult(allowed=True, warnings=warnings)
