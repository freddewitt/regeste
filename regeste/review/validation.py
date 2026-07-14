"""Field-level validation, status history, and inline corrections on the pivot.

Only this module and `translation/` (the `translations` field) write to a
`Piece` after `pivot/build.py` first creates it — every exporter stays read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone

from regeste.pivot import Event, FieldValidation, Piece, StatusChange, is_translation_stale


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def ocr_events(piece: Piece) -> list[Event]:
    """Every OCR run recorded on this piece, for side-by-side comparison when several
    providers ran on the same file (spec §4). The reviewer picks or merges one into
    `transcription` via `apply_correction(piece, "transcription", chosen_text)`.
    """
    return [event for event in piece.events if event.type == "ocr"]
