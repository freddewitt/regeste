"""Consultation PDF — image and transcription side by side, one page per piece.

Reuses the CJK-aware font selection from `core/export.py` rather than duplicating it
(that module is otherwise untouched — the existing OCR-registry exports stay as-is).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from regeste.core.export import _text_font_for_language
from regeste.i18n import get_current_language
from regeste.pivot import Piece

from .common import filter_pieces


def export_pdf(
    pieces: list[Piece],
    output_path: Path,
    *,
    validated_only: bool = False,
    target_language: str | None = None,
) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    pieces = filter_pieces(pieces, validated_only=validated_only)
    page_width, page_height = A4
    margin = 1.5 * cm
    half_width = (page_width - 3 * margin) / 2
    font_regular, font_bold = _text_font_for_language(get_current_language())

    c = canvas.Canvas(str(output_path), pagesize=A4)
    for piece in pieces:
        image_path = Path(piece.image_path) if piece.image_path else None
        if image_path and image_path.exists():
            image = ImageReader(str(image_path))
            iw, ih = image.getSize()
            scale = min(half_width / iw, (page_height - 2 * margin) / ih)
            c.drawImage(
                image,
                margin,
                margin,
                width=iw * scale,
                height=ih * scale,
                preserveAspectRatio=True,
            )

        x = margin * 2 + half_width
        y = page_height - margin
        c.setFont(font_bold, 11)
        c.drawString(x, y, piece.call_number or piece.id)
        y -= 0.7 * cm
        c.setFont(font_regular, 9)
        max_chars = int(half_width / (0.19 * cm))

        def _draw_wrapped(text: str, y: float) -> float:
            for line in text.splitlines() or [""]:
                for chunk in textwrap.wrap(line, width=max_chars) or [""]:
                    if y < margin:
                        c.showPage()
                        y = page_height - margin
                        c.setFont(font_regular, 9)
                    c.drawString(x, y, chunk)
                    y -= 0.4 * cm
            return y

        y = _draw_wrapped(piece.transcription, y)

        translation = (piece.translations or {}).get(target_language) if target_language else None
        if translation:
            y -= 0.3 * cm
            if y < margin:
                c.showPage()
                y = page_height - margin
            c.setFont(font_bold, 10)
            c.drawString(x, y, f"Traduction ({target_language})")
            y -= 0.5 * cm
            c.setFont(font_regular, 9)
            y = _draw_wrapped(translation.text, y)

        c.showPage()
    c.save()
    return output_path
