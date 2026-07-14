"""Pivot model — one archival record per piece, union of ISAD(G)/EAD/Dublin Core/METS/PREMIS fields.

Source of truth for export/review/translation. Built from the OCR `Registry`
(`pivot/build.py`) but never written to by the OCR pipeline itself — only
`review/` (statuses, history, inline corrections) and `translation/` (the
`translations` field) mutate it afterwards. Exporters are strictly read-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Fields whose validation status/history is tracked individually.
CONTENT_FIELDS = ("call_number", "date", "sender", "recipient", "transcription")

FieldStatus = str  # "draft" | "to_review" | "validated" | "rejected"


@dataclass
class Event:
    """One append-only entry in a piece's processing journal (PREMIS-style)."""

    type: str  # "ocr" | "correction" | "validation" | ...
    timestamp: str
    provider: str | None = None
    model: str | None = None
    detail: str = ""


@dataclass
class FieldValidation:
    status: FieldStatus = "draft"
    validated_by: str | None = None
    validated_at: str | None = None
    rejection_note: str | None = None

    def __post_init__(self) -> None:
        if self.status == "rejected" and not self.rejection_note:
            raise ValueError("rejection_note is required when status is 'rejected'")


@dataclass
class StatusChange:
    status: FieldStatus
    changed_at: str
    changed_by: str | None = None
    rejection_note: str | None = None


@dataclass
class NamedEntity:
    text: str
    entity_type: str = ""
    validation: FieldValidation = field(default_factory=FieldValidation)


@dataclass
class Translation:
    text: str
    summary: str = ""
    provider: str = ""
    model: str = ""
    date: str = ""
    status: str = "draft"  # "draft" | "validated"
    notes: str = ""
    source_hash: str = ""


@dataclass
class Piece:
    id: str
    call_number: str = ""
    fonds: str = ""
    series: str = ""
    subseries: str = ""
    folder: str = ""
    date: str = ""
    sender: str = ""
    recipient: str = ""
    transcription: str = ""
    summary: str = ""
    image_path: str = ""
    access_conditions: str = ""
    provenance: str = ""
    language_detected: str = ""
    confidence_score: float | None = None
    events: list[Event] = field(default_factory=list)
    field_validations: dict[str, FieldValidation] = field(default_factory=dict)
    status_history: dict[str, list[StatusChange]] = field(default_factory=dict)
    entities: list[NamedEntity] = field(default_factory=list)
    translations: dict[str, Translation] | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "call_number": self.call_number,
            "fonds": self.fonds,
            "series": self.series,
            "subseries": self.subseries,
            "folder": self.folder,
            "date": self.date,
            "sender": self.sender,
            "recipient": self.recipient,
            "transcription": self.transcription,
            "summary": self.summary,
            "image_path": self.image_path,
            "access_conditions": self.access_conditions,
            "provenance": self.provenance,
            "language_detected": self.language_detected,
            "confidence_score": self.confidence_score,
            "events": [asdict(e) for e in self.events],
            "field_validations": {k: asdict(v) for k, v in self.field_validations.items()},
            "status_history": {
                k: [asdict(c) for c in changes] for k, changes in self.status_history.items()
            },
            "entities": [
                {
                    "text": ent.text,
                    "entity_type": ent.entity_type,
                    "validation": asdict(ent.validation),
                }
                for ent in self.entities
            ],
            "translations": (
                {lang: asdict(t) for lang, t in self.translations.items()}
                if self.translations is not None
                else None
            ),
        }

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> "Piece":
        raw_translations = meta.get("translations")
        return cls(
            id=meta["id"],
            call_number=meta.get("call_number", ""),
            fonds=meta.get("fonds", ""),
            series=meta.get("series", ""),
            subseries=meta.get("subseries", ""),
            folder=meta.get("folder", ""),
            date=meta.get("date", ""),
            sender=meta.get("sender", ""),
            recipient=meta.get("recipient", ""),
            transcription=meta.get("transcription", ""),
            summary=meta.get("summary", ""),
            image_path=meta.get("image_path", ""),
            access_conditions=meta.get("access_conditions", ""),
            provenance=meta.get("provenance", ""),
            language_detected=meta.get("language_detected", ""),
            confidence_score=meta.get("confidence_score"),
            events=[Event(**e) for e in meta.get("events", [])],
            field_validations={
                k: FieldValidation(**v) for k, v in meta.get("field_validations", {}).items()
            },
            status_history={
                k: [StatusChange(**c) for c in changes]
                for k, changes in meta.get("status_history", {}).items()
            },
            entities=[
                NamedEntity(
                    text=ent["text"],
                    entity_type=ent.get("entity_type", ""),
                    validation=FieldValidation(**ent.get("validation", {})),
                )
                for ent in meta.get("entities", [])
            ],
            translations=(
                {lang: Translation(**t) for lang, t in raw_translations.items()}
                if raw_translations is not None
                else None
            ),
        )
