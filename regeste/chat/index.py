"""Lexical (BM25) search over the pivot corpus — no network, no extra dependency.

Why not embeddings: they need a second model/API (Claude has none, local servers
differ), cost money per piece, and archives are full of proper names and old
spellings where exact-ish word matching works well. The index is rebuilt in
memory in a fraction of a second, so it never goes stale on disk.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from regeste.pivot import Piece

CHUNK_CHARS = 1500
_PREFIX = 6  # crude stemming: "lettres"/"lettre", "marchands"/"marchand" share a stem
_K1, _B = 1.5, 0.75
_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [t[:_PREFIX] for t in _TOKEN.findall(folded) if len(t) > 1]


@dataclass(frozen=True)
class Chunk:
    piece: Piece
    text: str  # what is shown to the model (header + a slice of the transcription)
    part: int  # 1-based slice number within the piece
    parts: int


def _header(piece: Piece) -> str:
    bits = [f"Cote : {piece.call_number or piece.id}"]
    if piece.date:
        bits.append(f"Date : {piece.date}")
    if piece.sender:
        bits.append(f"Expéditeur : {piece.sender}")
    if piece.recipient:
        bits.append(f"Destinataire : {piece.recipient}")
    if piece.summary:
        bits.append(f"Résumé : {piece.summary}")
    if piece.entities:
        bits.append("Entités : " + ", ".join(e.text for e in piece.entities[:20]))
    return "\n".join(bits)


def _split(text: str) -> list[str]:
    """Cut at paragraph/line boundaries into slices of roughly CHUNK_CHARS."""
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text]
    slices, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > CHUNK_CHARS:  # a single huge line
            if current:
                slices.append(current)
                current = ""
            slices.append(line[:CHUNK_CHARS])
            line = line[CHUNK_CHARS:]
        if len(current) + len(line) > CHUNK_CHARS and current:
            slices.append(current)
            current = ""
        current += line
    if current.strip():
        slices.append(current)
    return slices


def make_chunks(piece: Piece) -> list[Chunk]:
    header = _header(piece)
    slices = _split(piece.transcription or "") or [""]
    return [
        Chunk(piece, f"{header}\n\n{body.strip()}".strip(), i, len(slices))
        for i, body in enumerate(slices, 1)
    ]


class CorpusIndex:
    def __init__(self, pieces: list[Piece]) -> None:
        self.pieces = [p for p in pieces if (p.transcription or "").strip() or p.summary]
        self.chunks: list[Chunk] = [c for p in self.pieces for c in make_chunks(p)]
        self._tf = [Counter(tokenize(c.text)) for c in self.chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self.chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        terms = set(tokenize(query))
        if not terms or not self.chunks:
            return []
        scored: list[tuple[float, int]] = []
        for i, tf in enumerate(self._tf):
            score = 0.0
            norm = _K1 * (1 - _B + _B * self._len[i] / (self._avg or 1))
            for t in terms:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (_K1 + 1) / (f + norm)
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda s: -s[0])
        return [self.chunks[i] for _, i in scored[:k]]

    def catalogue(self, max_chars: int = 6000) -> str:
        """One compact line per piece (cote, date, correspondents), so the model
        can answer overview questions that retrieval alone cannot."""
        lines, used = [], 0
        for p in self.pieces:
            who = " → ".join(x for x in (p.sender, p.recipient) if x)
            line = " | ".join(x for x in (p.call_number or p.id, p.date, who) if x)
            if used + len(line) + 1 > max_chars:
                lines.append(f"… ({len(self.pieces) - len(lines)} autres documents non listés)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)
