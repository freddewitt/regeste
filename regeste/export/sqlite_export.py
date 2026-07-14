"""SQLite export — a single `.db` file with `pieces`, `events`, and `translations` tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from regeste.pivot import Piece, global_status

from .common import filter_pieces


def export_sqlite(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    output_path.unlink(missing_ok=True)
    conn = sqlite3.connect(output_path)
    try:
        conn.execute(
            """
            CREATE TABLE pieces (
                id TEXT PRIMARY KEY, call_number TEXT, fonds TEXT, series TEXT,
                subseries TEXT, folder TEXT, date TEXT, sender TEXT, recipient TEXT,
                transcription TEXT, summary TEXT, image_path TEXT,
                access_conditions TEXT, provenance TEXT, confidence_score REAL,
                status TEXT
            )
            """
        )
        conn.execute(
            "CREATE TABLE events ("
            "piece_id TEXT, type TEXT, timestamp TEXT, provider TEXT, model TEXT, detail TEXT)"
        )
        conn.execute(
            "CREATE TABLE translations (piece_id TEXT, language TEXT, text TEXT, status TEXT)"
        )
        for piece in pieces:
            conn.execute(
                "INSERT INTO pieces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
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
                ),
            )
            for event in piece.events:
                conn.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?)",
                    (
                        piece.id,
                        event.type,
                        event.timestamp,
                        event.provider,
                        event.model,
                        event.detail,
                    ),
                )
            for lang, translation in (piece.translations or {}).items():
                conn.execute(
                    "INSERT INTO translations VALUES (?,?,?,?)",
                    (piece.id, lang, translation.text, translation.status),
                )
        conn.commit()
    finally:
        conn.close()
    return output_path
