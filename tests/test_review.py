from __future__ import annotations

import pytest

from regeste.pivot import Event, FieldValidation, Piece, Translation, hash_transcription
from regeste.review import (
    apply_correction,
    apply_field_validation,
    apply_group_status,
    bulk_validate,
    ocr_events,
    sample,
    sorted_by_confidence,
    sorted_for_review,
)


def _piece(**overrides) -> Piece:
    kwargs = dict(id="a.jpg", transcription="Cher Monsieur,")
    kwargs.update(overrides)
    return Piece(**kwargs)


def test_apply_field_validation_updates_status_and_history():
    piece = _piece()
    apply_field_validation(piece, "transcription", "validated", changed_by="mb")
    assert piece.field_validations["transcription"].status == "validated"
    assert piece.field_validations["transcription"].validated_by == "mb"
    assert len(piece.status_history["transcription"]) == 1
    assert piece.status_history["transcription"][0].status == "validated"


def test_apply_field_validation_appends_to_existing_history():
    piece = _piece()
    apply_field_validation(piece, "transcription", "to_review")
    apply_field_validation(piece, "transcription", "validated")
    assert [c.status for c in piece.status_history["transcription"]] == ["to_review", "validated"]


def test_apply_field_validation_rejected_requires_note():
    piece = _piece()
    with pytest.raises(ValueError):
        apply_field_validation(piece, "transcription", "rejected")


def test_apply_field_validation_rejected_with_note_succeeds():
    piece = _piece()
    apply_field_validation(piece, "transcription", "rejected", rejection_note="illisible")
    assert piece.field_validations["transcription"].rejection_note == "illisible"


def test_apply_correction_updates_field():
    piece = _piece(transcription="brouillon")
    apply_correction(piece, "transcription", "corrigé")
    assert piece.transcription == "corrigé"


def test_apply_correction_flags_stale_translation_on_transcription_change():
    original = "Cher Monsieur,"
    piece = _piece(
        transcription=original,
        translations={
            "en": Translation(text="Dear Sir,", status="validated", source_hash=hash_transcription(original))
        },
    )
    apply_correction(piece, "transcription", "Cher Monsieur, corrigé")
    assert piece.translations["en"].status == "stale"


def test_apply_correction_keeps_translation_when_hash_still_matches():
    original = "Cher Monsieur,"
    piece = _piece(
        transcription=original,
        translations={
            "en": Translation(text="Dear Sir,", status="validated", source_hash=hash_transcription(original))
        },
    )
    apply_correction(piece, "transcription", original)
    assert piece.translations["en"].status == "validated"


def test_ocr_events_filters_by_type():
    piece = _piece(
        events=[
            Event(type="ocr", timestamp="t0", provider="claude", detail="texte claude"),
            Event(type="validation", timestamp="t1"),
            Event(type="ocr", timestamp="t2", provider="gemini", detail="texte gemini"),
        ]
    )
    events = ocr_events(piece)
    assert [e.provider for e in events] == ["claude", "gemini"]


def test_sorted_by_confidence_ascending_with_none_first():
    a = _piece(id="a.jpg", confidence_score=0.9)
    b = _piece(id="b.jpg", confidence_score=None)
    c = _piece(id="c.jpg", confidence_score=0.2)
    ordered = sorted_by_confidence([a, b, c])
    assert [p.id for p in ordered] == ["b.jpg", "c.jpg", "a.jpg"]


def test_sample_returns_requested_size_capped_to_corpus():
    pieces = [_piece(id=f"{i}.jpg") for i in range(5)]
    assert len(sample(pieces, 3, seed=1)) == 3
    assert len(sample(pieces, 10, seed=1)) == 5


def test_bulk_validate_only_above_threshold():
    above = _piece(id="above.jpg", confidence_score=0.95)
    below = _piece(id="below.jpg", confidence_score=0.4)
    unknown = _piece(id="unknown.jpg", confidence_score=None)
    validated = bulk_validate([above, below, unknown], threshold=0.8)
    assert [p.id for p in validated] == ["above.jpg"]
    assert all(v.status == "validated" for v in above.field_validations.values())
    assert below.field_validations == {}
    assert unknown.field_validations == {}


def test_apply_group_status_validates_every_content_field():
    piece = _piece()
    apply_group_status(piece, "validated", changed_by="mb")
    assert all(v.status == "validated" for v in piece.field_validations.values())
    assert piece.field_validations["transcription"].validated_by == "mb"


def test_apply_group_status_overwrites_heterogeneous_field_statuses():
    piece = _piece()
    apply_field_validation(piece, "call_number", "validated")
    apply_field_validation(piece, "date", "to_review")
    apply_group_status(piece, "to_review")
    assert all(v.status == "to_review" for v in piece.field_validations.values())


def test_apply_group_status_rejected_requires_note():
    piece = _piece()
    with pytest.raises(ValueError):
        apply_group_status(piece, "rejected")


def test_apply_group_status_rejected_with_note_applies_to_all_fields():
    piece = _piece()
    apply_group_status(piece, "rejected", rejection_note="illisible")
    assert all(v.rejection_note == "illisible" for v in piece.field_validations.values())


def test_sorted_for_review_orders_pending_then_validated_then_rejected():
    pending = _piece(id="pending.jpg", confidence_score=0.5)
    validated = _piece(id="validated.jpg", confidence_score=0.1)
    apply_group_status(validated, "validated")
    rejected = _piece(id="rejected.jpg", confidence_score=0.9)
    apply_group_status(rejected, "rejected", rejection_note="illisible")
    ordered = sorted_for_review([validated, rejected, pending])
    assert [p.id for p in ordered] == ["pending.jpg", "validated.jpg", "rejected.jpg"]


def test_sorted_for_review_preserves_confidence_order_within_bucket():
    low = _piece(id="low.jpg", confidence_score=0.2)
    high = _piece(id="high.jpg", confidence_score=0.8)
    ordered = sorted_for_review([high, low])
    assert [p.id for p in ordered] == ["low.jpg", "high.jpg"]
