"""Computed (not stored) global validation status of a piece — derived from its per-field statuses
so there is never a second source of truth to desynchronize from `Piece.field_validations`.
"""

from __future__ import annotations

from .models import CONTENT_FIELDS, FieldValidation, Piece


def global_status(piece: Piece) -> str:
    """"rejected" if any content field was rejected, "validated" only if all are, else
    "to_review" if any field reached that stage, otherwise "draft".
    """
    statuses = {
        piece.field_validations.get(field, FieldValidation()).status for field in CONTENT_FIELDS
    }
    if "rejected" in statuses:
        return "rejected"
    if statuses == {"validated"}:
        return "validated"
    if "to_review" in statuses:
        return "to_review"
    return "draft"
