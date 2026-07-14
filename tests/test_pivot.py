"""Pivot model round-trip, staleness hash, validation invariants, and persistence."""

from __future__ import annotations

import pytest

from regeste.core.registry import Registry
from regeste.pivot import (
    CONTENT_FIELDS,
    Event,
    FieldValidation,
    NamedEntity,
    Piece,
    StatusChange,
    Translation,
    build_pieces_from_registry,
    bundle_corpus,
    global_status,
    hash_transcription,
    is_translation_stale,
    load_corpus,
    load_piece,
    save_piece,
)


def _sample_piece(**overrides) -> Piece:
    kwargs = dict(
        id="a.jpg",
        call_number="3 U 794/12",
        transcription="Cher Monsieur,",
    )
    kwargs.update(overrides)
    return Piece(**kwargs)


def test_piece_round_trips_through_to_meta_from_meta():
    piece = _sample_piece(
        events=[Event(type="ocr", timestamp="2026-01-01T00:00:00Z", provider="claude", model="x")],
        field_validations={"transcription": FieldValidation(status="validated", validated_by="mb")},
        status_history={"transcription": [StatusChange(status="draft", changed_at="t0")]},
        entities=[NamedEntity(text="Corbeau", entity_type="person")],
        translations={"en": Translation(text="Dear Sir,", source_hash="abc")},
    )
    restored = Piece.from_meta(piece.to_meta())
    assert restored == piece


def test_piece_round_trips_with_no_translations():
    piece = _sample_piece(translations=None)
    restored = Piece.from_meta(piece.to_meta())
    assert restored.translations is None


def test_field_validation_rejected_requires_note():
    with pytest.raises(ValueError):
        FieldValidation(status="rejected")


def test_field_validation_rejected_with_note_is_valid():
    validation = FieldValidation(status="rejected", rejection_note="illisible")
    assert validation.status == "rejected"


def test_hash_transcription_changes_with_content():
    assert hash_transcription("a") != hash_transcription("b")


def test_is_translation_stale_detects_change():
    original = "Cher Monsieur,"
    h = hash_transcription(original)
    assert is_translation_stale(original, h) is False
    assert is_translation_stale("Cher Monsieur, corrigé", h) is True


def test_build_pieces_from_registry_seeds_draft_content_fields(tmp_path):
    registry = Registry.new(tmp_path, meta={"provider": {"kind": "claude"}}, file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="hello", description="desc", tokens_in=1, tokens_out=1, cost=0.0, model="m"
    )
    pieces = build_pieces_from_registry(registry, tmp_path)
    assert len(pieces) == 1
    piece = pieces[0]
    assert piece.transcription == "hello"
    assert piece.summary == "desc"
    assert set(piece.field_validations) == set(CONTENT_FIELDS)
    assert all(v.status == "draft" for v in piece.field_validations.values())
    assert piece.events[0].type == "ocr"
    assert piece.events[0].provider == "claude"
    assert piece.events[0].model == "m"


def test_build_pieces_from_registry_skips_non_ok_entries(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])
    registry.record_result(
        "a.jpg", text="hello", description="", tokens_in=1, tokens_out=1, cost=0.0, model="m"
    )
    registry.record_error("b.jpg", "boom")
    pieces = build_pieces_from_registry(registry, tmp_path)
    assert [p.id for p in pieces] == ["a.jpg"]


def test_save_and_load_piece_roundtrip(tmp_path):
    piece = _sample_piece()
    path = save_piece(tmp_path, piece)
    assert path.exists()
    assert path == tmp_path / "data" / "pivot" / "a.jpg.json"
    reloaded = load_piece(tmp_path, "a.jpg")
    assert reloaded == piece


def test_load_piece_returns_none_when_missing(tmp_path):
    assert load_piece(tmp_path, "missing") is None


def test_load_corpus_returns_all_saved_pieces(tmp_path):
    save_piece(tmp_path, _sample_piece(id="a.jpg"))
    save_piece(tmp_path, _sample_piece(id="b.jpg"))
    corpus = load_corpus(tmp_path)
    assert sorted(p.id for p in corpus) == ["a.jpg", "b.jpg"]


def test_load_corpus_empty_when_no_pivot_dir(tmp_path):
    assert load_corpus(tmp_path) == []


def test_bundle_corpus_writes_single_json_with_all_pieces(tmp_path):
    save_piece(tmp_path, _sample_piece(id="a.jpg"))
    save_piece(tmp_path, _sample_piece(id="b.jpg"))
    bundle_path = bundle_corpus(tmp_path, tmp_path / "bundle.json")
    assert bundle_path.exists()
    import json

    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert len(data) == 2


def test_save_piece_atomic_leaves_no_temp_file(tmp_path):
    save_piece(tmp_path, _sample_piece())
    temp_files = list((tmp_path / "data" / "pivot").glob(".*.tmp"))
    assert temp_files == []


def test_global_status_draft_by_default():
    piece = _sample_piece(field_validations={f: FieldValidation() for f in CONTENT_FIELDS})
    assert global_status(piece) == "draft"


def test_global_status_validated_only_when_all_fields_validated():
    piece = _sample_piece(
        field_validations={f: FieldValidation(status="validated") for f in CONTENT_FIELDS}
    )
    assert global_status(piece) == "validated"


def test_global_status_rejected_wins_over_validated():
    validations = {f: FieldValidation(status="validated") for f in CONTENT_FIELDS}
    validations["transcription"] = FieldValidation(status="rejected", rejection_note="illisible")
    piece = _sample_piece(field_validations=validations)
    assert global_status(piece) == "rejected"


def test_global_status_to_review_when_mixed():
    validations = {f: FieldValidation(status="validated") for f in CONTENT_FIELDS}
    validations["date"] = FieldValidation(status="to_review")
    piece = _sample_piece(field_validations=validations)
    assert global_status(piece) == "to_review"
