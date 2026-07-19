"""First pivot population from an OCR `Registry` — one `Piece` per successfully transcribed file."""

from __future__ import annotations

from pathlib import Path

from regeste.core.registry import Registry

from .models import CONTENT_FIELDS, Event, FieldValidation, Piece


def build_pieces_from_registry(registry: Registry, source_dir: Path) -> list[Piece]:
    """Seed one `Piece` per `FileEntry` with `transcription.status == "ok"`.
    
    Each piece starts as a draft: `transcription` is the raw OCR text, every
    content field is unvalidated, and the OCR run is recorded as the first
    journal event — so a later run with a different provider on the same file
    can be compared side by side in the review screen instead of overwriting it.
    """
    provider_kind = registry.meta.get("provider", {}).get("kind")
    project_mode = registry.meta.get("transcription_mode", "")
    pieces = []
    for name, entry in registry.files.items():
        if entry.transcription.status != "ok":
            continue
        display = registry.display_name(name)
        # Derive hypothesis_mode from the per-file mode, falling back to the
        # project-level mode (v1 migrated entries have no per-file mode).
        entry_mode = entry.transcription.mode or project_mode
        hypothesis = entry_mode == "hypotheses"
        pieces.append(
            Piece(
                id=display,
                transcription=entry.transcription.text,
                summary=entry.description,
                language_detected=entry.language,
                image_path=str(source_dir / display),
                hypothesis_mode=hypothesis,
                field_validations={f: FieldValidation() for f in CONTENT_FIELDS},
                events=[
                    Event(
                        type="ocr",
                        timestamp=entry.transcription.date or "",
                        provider=provider_kind,
                        model=entry.transcription.model,
                        detail=entry.transcription.text,
                    )
                ],
            )
        )
    return pieces
