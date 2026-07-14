"""Markdown exports — a simple listing and an Obsidian-compatible variant with YAML frontmatter."""

from __future__ import annotations

from pathlib import Path

from regeste.pivot import Piece, global_status

from .common import filter_pieces


def export_markdown(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    lines = [f"- **{piece.id}** — {piece.summary or piece.transcription[:80]}" for piece in pieces]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _yaml_escape(value: str) -> str:
    return value.replace('"', '\\"')


def export_markdown_obsidian(
    pieces: list[Piece], output_dir: Path, *, validated_only: bool = False
) -> Path:
    """Writes one Markdown file per piece with YAML frontmatter into `output_dir`."""
    pieces = filter_pieces(pieces, validated_only=validated_only)
    output_dir.mkdir(parents=True, exist_ok=True)
    for piece in pieces:
        frontmatter = [
            "---",
            f'cote: "{_yaml_escape(piece.call_number)}"',
            f'date: "{_yaml_escape(piece.date)}"',
            f'expediteur: "{_yaml_escape(piece.sender)}"',
            f'destinataire: "{_yaml_escape(piece.recipient)}"',
            f"statut: {global_status(piece)}",
            "---",
            "",
        ]
        body = [f"# {piece.call_number or piece.id}", "", piece.transcription]
        (output_dir / f"{piece.id}.md").write_text(
            "\n".join(frontmatter + body) + "\n", encoding="utf-8"
        )
    return output_dir
