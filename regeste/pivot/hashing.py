"""Staleness hash for translations — recomputed whenever `Piece.transcription` changes."""

from __future__ import annotations

import hashlib


def hash_transcription(transcription: str) -> str:
    return hashlib.sha256(transcription.encode("utf-8")).hexdigest()


def is_translation_stale(transcription: str, source_hash: str) -> bool:
    return hash_transcription(transcription) != source_hash
