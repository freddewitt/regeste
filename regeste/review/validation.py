"""Field-level validation, status history, and inline corrections on the pivot.

Only this module and `translation/` (the `translations` field) write to a
`Piece` after `pivot/build.py` first creates it — every exporter stays read-only.
"""

from __future__ import annotations

from regeste.pivot import CONTENT_FIELDS, Event, FieldValidation, Piece, StatusChange, is_translation_stale
from regeste.pivot.utils import _now


def apply_field_validation(
    piece: Piece,
    field: str,
    status: str,
    *,
    changed_by: str | None = None,
    rejection_note: str | None = None,
) -> None:
    """Update `field`'s validation status and append to its history.

    Raises `ValueError` (via `FieldValidation.__post_init__`) if `status` is
    "rejected" without a `rejection_note`.
    """
    validated_at = _now()
    validation = FieldValidation(
        status=status,
        validated_by=changed_by,
        validated_at=validated_at,
        rejection_note=rejection_note,
    )
    piece.field_validations[field] = validation
    piece.status_history.setdefault(field, []).append(
        StatusChange(
            status=status,
            changed_at=validated_at,
            changed_by=changed_by,
            rejection_note=rejection_note,
        )
    )


def apply_correction(piece: Piece, field: str, new_value: str) -> None:
    """Inline-correct a pivot field.

    Correcting `transcription` flags every existing translation whose recorded
    `source_hash` no longer matches as `"stale"` (spec §1: obsolete translations).
    """
    setattr(piece, field, new_value)
    if field == "transcription" and piece.translations:
        for translation in piece.translations.values():
            if is_translation_stale(new_value, translation.source_hash):
                translation.status = "stale"


def apply_group_status(
    piece: Piece,
    status: str,
    *,
    changed_by: str | None = None,
    rejection_note: str | None = None,
) -> None:
    """Apply `status` to every content field of `piece` in one action (Review tab quick-triage
    buttons). Overwrites each field regardless of its current status, same override semantics
    as `queue.bulk_validate` — quick triage is a deliberate one-click decision on the whole
    piece, not a per-field merge. Goes through `apply_field_validation` field by field so
    history/rejection-note invariants stay identical to per-field review.
    """
    for content_field in CONTENT_FIELDS:
        apply_field_validation(
            piece, content_field, status, changed_by=changed_by, rejection_note=rejection_note
        )


def ocr_events(piece: Piece) -> list[Event]:
    """Every OCR run recorded on this piece, for side-by-side comparison when several
    providers ran on the same file (spec §4). The reviewer picks or merges one into
    `transcription` via `apply_correction(piece, "transcription", chosen_text)`.
    """
    return [event for event in piece.events if event.type == "ocr"]
