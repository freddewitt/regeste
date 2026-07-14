"""CSV exports — a lightweight version (key columns) and a complete one (every pivot field)."""

from __future__ import annotations

import csv
from pathlib import Path

from regeste.pivot import Piece, global_status

from .common import filter_pieces

LIGHT_FIELDS = ("id", "call_number", "date", "sender", "recipient", "summary")

FULL_HEADER = (
    "id",
    "call_number",
    "fonds",
    "series",
    "subseries",
    "folder",
    "date",
    "sender",
    "recipient",
    "transcription",
    "summary",
    "image_path",
    "access_conditions",
    "provenance",
    "confidence_score",
    "global_status",
    "translated_languages",
)


def export_csv_light(
    pieces: list[Piece], output_path: Path, *, validated_only: bool = False
) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LIGHT_FIELDS)
        for piece in pieces:
            writer.writerow([getattr(piece, field) for field in LIGHT_FIELDS])
    return output_path


def export_csv_full(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FULL_HEADER)
        for piece in pieces:
            writer.writerow(
                [
                    piece.id,
                    piece.call_number,
                    piece.fonds,
                    piece.series,
                    piece.subseries,
                    piece.folder,
                    piece.date,
                    piece.sender,
                    piece.recipient,
                    piece.transcription,
                    piece.summary,
                    piece.image_path,
                    piece.access_conditions,
                    piece.provenance,
                    piece.confidence_score,
                    global_status(piece),
                    ",".join(sorted((piece.translations or {}).keys())),
                ]
            )
    return output_path
