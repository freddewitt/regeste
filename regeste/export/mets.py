"""METS XML export — one document per piece, `digiprovMD` carries the PREMIS
processing events (`Piece.events`)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from regeste.pivot import Piece

from .common import filter_pieces

METS_NS = "http://www.loc.gov/METS/"
PREMIS_NS = "info:lc/xmlns/premis-v2"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", METS_NS)
ET.register_namespace("premis", PREMIS_NS)
ET.register_namespace("xlink", XLINK_NS)


def export_mets(pieces: list[Piece], output_dir: Path, *, validated_only: bool = False) -> Path:
    """Writes one `<piece_id>.xml` METS document per piece into `output_dir`; returns the dir."""
    pieces = filter_pieces(pieces, validated_only=validated_only)
    output_dir.mkdir(parents=True, exist_ok=True)
    for piece in pieces:
        mets = ET.Element(f"{{{METS_NS}}}mets", {"OBJID": piece.id})

        dmd_sec = ET.SubElement(mets, f"{{{METS_NS}}}dmdSec", {"ID": "dmd1"})
        md_wrap = ET.SubElement(dmd_sec, f"{{{METS_NS}}}mdWrap", {"MDTYPE": "OTHER"})
        xml_data = ET.SubElement(md_wrap, f"{{{METS_NS}}}xmlData")
        ET.SubElement(xml_data, "title").text = piece.call_number
        ET.SubElement(xml_data, "abstract").text = piece.summary
        ET.SubElement(xml_data, "transcription").text = piece.transcription

        amd_sec = ET.SubElement(mets, f"{{{METS_NS}}}amdSec")
        digiprov = ET.SubElement(amd_sec, f"{{{METS_NS}}}digiprovMD", {"ID": "digiprov1"})
        dp_wrap = ET.SubElement(digiprov, f"{{{METS_NS}}}mdWrap", {"MDTYPE": "PREMIS"})
        dp_data = ET.SubElement(dp_wrap, f"{{{METS_NS}}}xmlData")
        for event in piece.events:
            premis_event = ET.SubElement(dp_data, f"{{{PREMIS_NS}}}event")
            ET.SubElement(premis_event, f"{{{PREMIS_NS}}}eventType").text = event.type
            ET.SubElement(premis_event, f"{{{PREMIS_NS}}}eventDateTime").text = event.timestamp
            if event.provider or event.model:
                agent = ET.SubElement(premis_event, f"{{{PREMIS_NS}}}linkingAgentIdentifier")
                ET.SubElement(agent, f"{{{PREMIS_NS}}}linkingAgentIdentifierValue").text = (
                    f"{event.provider or ''}/{event.model or ''}"
                )

        file_sec = ET.SubElement(mets, f"{{{METS_NS}}}fileSec")
        file_grp = ET.SubElement(file_sec, f"{{{METS_NS}}}fileGrp")
        if piece.image_path:
            file_el = ET.SubElement(file_grp, f"{{{METS_NS}}}file", {"ID": "img1"})
            ET.SubElement(
                file_el,
                f"{{{METS_NS}}}FLocat",
                {f"{{{XLINK_NS}}}href": piece.image_path, "LOCTYPE": "URL"},
            )

        tree = ET.ElementTree(mets)
        ET.indent(tree, space="  ")
        tree.write(output_dir / f"{piece.id}.xml", encoding="utf-8", xml_declaration=True)
    return output_dir
