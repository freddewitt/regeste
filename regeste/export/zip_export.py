"""ZIP export — fonds/série/pièce tree with `images/`, `transcriptions/`, `metadata.json`,
`README.md`.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from regeste.pivot import Piece, global_status

from .common import filter_pieces, hierarchy_path

_README = """# Export Regeste

{count} pièce(s) exportée(s). Voir `metadata.json` pour le détail par pièce,
`images/` pour les fac-similés et `transcriptions/` pour le texte transcrit.
"""


def export_zip(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    metadata = []
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for piece in pieces:
            base = "/".join((*hierarchy_path(piece), piece.id))
            image_path = Path(piece.image_path) if piece.image_path else None
            if image_path and image_path.exists():
                zf.write(image_path, f"images/{base}{image_path.suffix}")
            zf.writestr(f"transcriptions/{base}.txt", piece.transcription)
            metadata.append(
                {
                    "id": piece.id,
                    "call_number": piece.call_number,
                    "path": base,
                    "date": piece.date,
                    "sender": piece.sender,
                    "recipient": piece.recipient,
                    "summary": piece.summary,
                    "status": global_status(piece),
                }
            )
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
        zf.writestr("README.md", _README.format(count=len(pieces)))
    return output_path
