"""Retrieval-augmented chat over the corpus (core: no Qt, no GUI import)."""

from __future__ import annotations

from dataclasses import dataclass

from regeste.pivot import Piece
from regeste.translation.provider import TranslationProvider

from .index import Chunk, CorpusIndex
from .prompt import DEFAULT_CHAT_PROMPT

HISTORY_TURNS = 6  # last N messages kept in the prompt
HISTORY_CHARS = 1500  # per message
SHORT_QUESTION_WORDS = 6  # shorter than this: also search with the previous question


@dataclass(frozen=True)
class Source:
    number: int
    piece: Piece
    part: int
    parts: int

    @property
    def label(self) -> str:
        cote = self.piece.call_number or self.piece.id
        date = f" — {self.piece.date}" if self.piece.date else ""
        part = f" (extrait {self.part}/{self.parts})" if self.parts > 1 else ""
        return f"[{self.number}] {cote}{date}{part}"


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    sources: list[Source]
    tokens_in: int
    tokens_out: int


class ChatEngine:
    """Single-turn text providers are reused: the history is folded into the
    prompt, which keeps one code path for Claude, Gemini and OpenAI-compatible."""

    def __init__(
        self,
        pieces: list[Piece],
        provider: TranslationProvider,
        model: str,
        *,
        instruction: str | None = None,
        top_k: int = 8,
    ) -> None:
        self._index = CorpusIndex(pieces)
        self._provider = provider
        self._model = model
        self._instruction = instruction or DEFAULT_CHAT_PROMPT
        self._top_k = max(1, top_k)

    @property
    def piece_count(self) -> int:
        return len(self._index.pieces)

    def build_prompt(
        self, question: str, history: list[tuple[str, str]]
    ) -> tuple[str, list[Source]]:
        query = question
        if len(question.split()) < SHORT_QUESTION_WORDS:
            previous = [t for role, t in history if role == "user"]
            if previous:
                query = f"{previous[-1]} {question}"
        chunks: list[Chunk] = self._index.search(query, self._top_k)
        sources = [Source(i, c.piece, c.part, c.parts) for i, c in enumerate(chunks, 1)]

        parts = [self._instruction.strip()]
        catalogue = self._index.catalogue()
        if catalogue:
            parts.append(
                f"CATALOGUE ({self.piece_count} documents) :\n{catalogue}"
            )
        if chunks:
            extracts = "\n\n".join(f"[{s.number}]\n{c.text}" for s, c in zip(sources, chunks))
            parts.append(f"EXTRAITS :\n{extracts}")
        else:
            parts.append("EXTRAITS : (aucun extrait ne correspond à la recherche)")
        if history:
            recent = history[-HISTORY_TURNS:]
            lines = [
                f"{'Utilisateur' if role == 'user' else 'Assistant'} : {text[:HISTORY_CHARS]}"
                for role, text in recent
            ]
            parts.append("CONVERSATION JUSQU'ICI :\n" + "\n".join(lines))
        parts.append(f"QUESTION : {question}")
        return "\n\n---\n\n".join(parts), sources

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> ChatAnswer:
        prompt, sources = self.build_prompt(question, history or [])
        result = self._provider.translate(prompt, model=self._model)
        return ChatAnswer(result.text, sources, result.tokens_in, result.tokens_out)
