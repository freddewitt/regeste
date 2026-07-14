"""md / txt / json / pdf-searchable exports, regenerable from the registry alone (spec §7)."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from pathlib import Path

from regeste.i18n import _, get_current_language

from .registry import FileEntry, Registry

KNOWN_FORMATS = ("md", "txt", "json", "pdf")

# CID fonts bundled natively with reportlab (no external font file to embed) that
# cover CJK glyphs, keyed by interface language. Other languages keep the
# base14 Helvetica fonts (spec §11.3).
_CJK_FONTS: dict[str, str] = {
    "ja": "HeiseiKakuGo-W5",
    "zh": "STSong-Light",
}


def _text_font_for_language(lang: str) -> tuple[str, str]:
    """Returns (regular, bold) font names for `lang`, registering a CID font if needed.

    CID fonts shipped with reportlab don't come in separate bold weights, so the
    same font is reused for both slots when one is selected — still avoids the
    "tofu" boxes that Helvetica renders for CJK glyphs.
    """
    cid_font = _CJK_FONTS.get(lang)
    if cid_font is None:
        return "Helvetica", "Helvetica-Bold"
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    if cid_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(cid_font))
    return cid_font, cid_font


@dataclass(frozen=True)
class ExportOptions:
    formats: frozenset[str]
    single_file: bool = True
    per_file: bool = True


def _successful_entries(registry: Registry) -> list[tuple[str, FileEntry]]:
    return sorted(
        ((name, entry) for name, entry in registry.files.items() if entry.status == "ok"),
        key=lambda pair: pair[0],
    )


def _render_markdown(entries: list[tuple[str, FileEntry]]) -> str:
    blocks = []
    for name, entry in entries:
        block = [f"**{name}** :"]
        if entry.text:
            block.append(f"\n{_('Text')}\n\n{entry.text}")
        if entry.description:
            block.append(f"\n{_('Description')}\n\n{entry.description}")
        blocks.append("\n".join(block))
    return "\n\n---\n\n".join(blocks) + "\n"


def _render_text(entries: list[tuple[str, FileEntry]]) -> str:
    blocks = []
    for name, entry in entries:
        block = [name]
        if entry.text:
            block.append(f"{_('Text')}:\n{entry.text}")
        if entry.description:
            block.append(f"{_('Description')}:\n{entry.description}")
        blocks.append("\n\n".join(block))
    return "\n\n====\n\n".join(blocks) + "\n"


def _render_json(entries: list[tuple[str, FileEntry]]) -> str:
    data = [
        {
            "name": name,
            "text": entry.text,
            "description": entry.description,
            "status": entry.status,
            "tokens_in": entry.tokens_in,
            "tokens_out": entry.tokens_out,
            "cost": entry.cost,
            "model": entry.model,
            "date": entry.date,
        }
        for name, entry in entries
    ]
    return json.dumps(data, indent=2, ensure_ascii=False)


def _render_pdf(entries: list[tuple[str, FileEntry]], source_dir: Path, output_path: Path) -> None:
    """PDF with a genuinely selectable text layer — native image then text below (spec §7.3)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    page_width, page_height = A4
    margin = 2 * cm
    max_text_width = int((page_width - 2 * margin) / (0.19 * cm))  # ~chars per line at 9pt
    font_regular, font_bold = _text_font_for_language(get_current_language())

    c = canvas.Canvas(str(output_path), pagesize=A4)
    for name, entry in entries:
        y = page_height - margin
        image_path = source_dir / name
        if image_path.exists():
            image = ImageReader(str(image_path))
            image_width, image_height = image.getSize()
            max_w = page_width - 2 * margin
            max_h = page_height * 0.55
            scale = min(max_w / image_width, max_h / image_height)
            draw_w, draw_h = image_width * scale, image_height * scale
            c.drawImage(
                image, margin, y - draw_h, width=draw_w, height=draw_h, preserveAspectRatio=True
            )
            y -= draw_h + 0.5 * cm

        c.setFont(font_bold, 11)
        c.drawString(margin, y, name)
        y -= 0.7 * cm

        for label, content in ((_("Text"), entry.text), (_("Description"), entry.description)):
            if not content:
                continue
            if y < margin:
                c.showPage()
                y = page_height - margin
            c.setFont(font_bold, 9)
            c.drawString(margin, y, label)
            y -= 0.45 * cm
            c.setFont(font_regular, 9)
            for line in content.splitlines() or [""]:
                for chunk in textwrap.wrap(line, width=max_text_width) or [""]:
                    if y < margin:
                        c.showPage()
                        y = page_height - margin
                        c.setFont(font_regular, 9)
                    c.drawString(margin, y, chunk)
                    y -= 0.4 * cm
            y -= 0.3 * cm
        c.showPage()
    c.save()


_TEXT_RENDERERS = {
    "md": _render_markdown,
    "txt": _render_text,
    "json": _render_json,
}


def export_registry(
    registry: Registry,
    *,
    source_dir: Path,
    output_dir: Path,
    project_name: str,
    options: ExportOptions,
) -> list[Path]:
    """Write the requested exports. Regenerable at any time from the registry alone (spec §5.1)."""
    entries = _successful_entries(registry)
    root = output_dir / project_name
    written_files: list[Path] = []

    if options.single_file:
        combined_dir = root / "combined"
        combined_dir.mkdir(parents=True, exist_ok=True)
        for fmt in options.formats & set(_TEXT_RENDERERS):
            path = combined_dir / f"{project_name}.{fmt}"
            path.write_text(_TEXT_RENDERERS[fmt](entries), encoding="utf-8")
            written_files.append(path)
        if "pdf" in options.formats:
            path = combined_dir / f"{project_name}.pdf"
            _render_pdf(entries, source_dir, path)
            written_files.append(path)

    if options.per_file:
        per_file_dir = root / "per_file"
        per_file_dir.mkdir(parents=True, exist_ok=True)
        for name, entry in entries:
            base = Path(name).stem
            for fmt in options.formats & set(_TEXT_RENDERERS):
                path = per_file_dir / f"{base}.{fmt}"
                path.write_text(_TEXT_RENDERERS[fmt]([(name, entry)]), encoding="utf-8")
                written_files.append(path)
            if "pdf" in options.formats:
                path = per_file_dir / f"{base}.pdf"
                _render_pdf([(name, entry)], source_dir, path)
                written_files.append(path)

    return written_files
