"""Dublin Core (Simple DC) XML export — one `<oai_dc:dc>` record per piece."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from regeste.pivot import Piece

from .common import filter_pieces, hierarchy_path

DC_NS = "http://purl.org/dc/elements/1.1/"
OAI_DC_NS = "http://www.openarchives.org/OAI/2.0/oai_dc/"

ET.register_namespace("dc", DC_NS)
ET.register_namespace("oai_dc", OAI_DC_NS)


def export_dublin_core(
    pieces: list[Piece], output_path: Path, *, validated_only: bool = False
) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    collection = ET.Element("collection")
    for piece in pieces:
        record = ET.SubElement(collection, f"{{{OAI_DC_NS}}}dc")
        if piece.call_number:
            ET.SubElement(record, f"{{{DC_NS}}}identifier").text = piece.call_number
        if piece.date:
            ET.SubElement(record, f"{{{DC_NS}}}date").text = piece.date
        if piece.sender:
            ET.SubElement(record, f"{{{DC_NS}}}creator").text = piece.sender
        if piece.recipient:
            ET.SubElement(record, f"{{{DC_NS}}}contributor").text = piece.recipient
        if piece.summary:
            ET.SubElement(record, f"{{{DC_NS}}}description").text = piece.summary
        if piece.transcription:
            ET.SubElement(record, f"{{{DC_NS}}}description").text = piece.transcription
        for level in hierarchy_path(piece):
            ET.SubElement(record, f"{{{DC_NS}}}relation").text = level
        if piece.access_conditions:
            ET.SubElement(record, f"{{{DC_NS}}}rights").text = piece.access_conditions
        if piece.provenance:
            ET.SubElement(record, f"{{{DC_NS}}}source").text = piece.provenance
        for lang, translation in (piece.translations or {}).items():
            translated = ET.SubElement(record, f"{{{DC_NS}}}description")
            translated.set("lang", lang)
            translated.text = translation.text

    tree = ET.ElementTree(collection)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path
