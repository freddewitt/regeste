"""First pivot population from an OCR `Registry` — one `Piece` per successfully transcribed file."""

from __future__ import annotations

from pathlib import Path

from regeste.core.registry import Registry

from .models import CONTENT_FIELDS, Event, FieldValidation, Piece


def build_pieces_from_registry(registry: Registry, source_dir: Path) -> list[Piece]:
    """Seed one `Piece` per `FileEntry` with `status == "ok"`.

    Each piece starts as a draft: `transcription` is the raw OCR text, every
    content field is unvalidated, and the OCR run is recorded as the first
    journal event — so a later run with a different provider on the same file
    can be compared side by side in the review screen instead of overwriting it.
    """
    provider_kind = registry.meta.get("provider", {}).get("kind")
    pieces = []
    for name, entry in registry.files.items():
        if entry.status != "ok":
            continue
        pieces.append(
            Piece(
                id=name,
                transcription=entry.text,
                summary=entry.description,
                language_detected=entry.language,
                image_path=str(source_dir / name),
                field_validations={f: FieldValidation() for f in CONTENT_FIELDS},
                events=[
                    Event(
                        type="ocr",
                        timestamp=entry.date or "",
                        provider=provider_kind,
                        model=entry.model,
                        detail=entry.text,
                    )
                ],
            )
        )
    return pieces
