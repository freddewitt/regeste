"""Transcription mode shared by core, GUI and CLI: literal vs. contextual hypotheses.

Lives in its own module (not `transcriber.py`) because both `export.py` and
`transcriber.py` need it - importing it from `transcriber.py` would create a
cycle (transcriber -> project -> export -> transcriber).
"""

from __future__ import annotations

from enum import Enum


class TranscriptionMode(str, Enum):
    """How the OCR handles illegible/damaged passages.

    LITERAL: transcribe only what is visible, mark uncertainty with [?]/[illisible].
    HYPOTHESES: additionally allow best-guess contextual readings, explicitly
    wrapped in [[double brackets]] (see `HYPOTHESES_BLOCK`).
    """

    LITERAL = "literal"
    HYPOTHESES = "hypotheses"

    @classmethod
    def from_value(cls, value: object) -> "TranscriptionMode":
        """Lenient parse for persisted values - unknown/missing falls back to LITERAL."""
        try:
            return cls(str(value))
        except ValueError:
            return cls.LITERAL


# Legend of the [[...]] notation. Injected both into the OCR system prompt (so the
# model produces the notation) and into md/txt/json/pdf exports (so the reader can
# decode it). Addresses the model / the archive reader, not the GUI user - like
# DEFAULT_SYSTEM_PROMPT, deliberately not wrapped in `_()`.
HYPOTHESES_BLOCK = """\
## HYPOTHESES

When text is illegible, damaged, or ambiguous, provide your best contextual
hypothesis enclosed in [[double brackets]]. For example:
- [[illegible: water damage]] for completely unreadable text
- [[hypothesis: 1847]] for a date where only "184_" is visible
- [[hypothesis: Martin]] for a name where only "M_t_in" is legible

Always mark hypotheses explicitly so the reader can distinguish certain
transcription from conjecture."""
