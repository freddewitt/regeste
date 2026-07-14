"""EAD (Encoded Archival Description) XML export — one `<ead>` document, `<c>` per piece
grouped by fonds/série/sous-série/dossier. Field correspondence: `export/mapping.py`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from regeste.pivot import Piece

from .common import filter_pieces, hierarchy_path

_LEVEL_BY_DEPTH = {1: "series", 2: "subseries", 3: "file"}


def export_ead(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    ead = ET.Element("ead")
    archdesc = ET.SubElement(ead, "archdesc", {"level": "fonds"})
    dsc = ET.SubElement(archdesc, "dsc")

    groups: dict[tuple[str, ...], ET.Element] = {(): dsc}
    for piece in pieces:
        parent = dsc
        prefix: tuple[str, ...] = ()
        for level_name in hierarchy_path(piece):
            prefix = (*prefix, level_name)
            if prefix not in groups:
                c = ET.SubElement(parent, "c", {"level": _LEVEL_BY_DEPTH.get(len(prefix), "series")})
                did = ET.SubElement(c, "did")
                ET.SubElement(did, "unittitle").text = level_name
                groups[prefix] = c
            parent = groups[prefix]

        c = ET.SubElement(parent, "c", {"level": "item"})
        did = ET.SubElement(c, "did")
        ET.SubElement(did, "unitid").text = piece.call_number
        ET.SubElement(did, "unitdate").text = piece.date
        ET.SubElement(did, "abstract").text = piece.summary
        langmaterial = ET.SubElement(did, "langmaterial")
        ET.SubElement(langmaterial, "note", {"type": "transcription"}).text = piece.transcription

        controlaccess = ET.SubElement(c, "controlaccess")
        if piece.sender:
            ET.SubElement(controlaccess, "persname", {"role": "sender"}).text = piece.sender
        if piece.recipient:
            ET.SubElement(controlaccess, "persname", {"role": "recipient"}).text = piece.recipient
        if piece.access_conditions:
            ET.SubElement(c, "accessrestrict").text = piece.access_conditions
        if piece.provenance:
            ET.SubElement(c, "custodhist").text = piece.provenance
        for event in piece.events:
            processinfo = ET.SubElement(c, "processinfo")
            ET.SubElement(processinfo, "p").text = (
                f"{event.type} — {event.provider or ''} {event.model or ''} ({event.timestamp})".strip()
            )

    tree = ET.ElementTree(ead)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path
