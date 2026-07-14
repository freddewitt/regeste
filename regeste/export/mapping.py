"""Pivot field -> EAD / Dublin Core / METS / PREMIS correspondence.

Reference documentation for `ead.py`, `dc.py`, and `mets.py` — each pivot
field maps to a specific element/attribute in each standard. Kept as a single
table so the exporters and the field-mapping acceptance criterion stay in sync.
"""

from __future__ import annotations

FIELD_MAPPING: dict[str, dict[str, str]] = {
    "call_number": {
        "ead": "did/unitid",
        "dublin_core": "dc:identifier",
        "mets": "dmdSec/mdWrap/xmlData/title",
    },
    "fonds": {
        "ead": "archdesc[@level='fonds']/did/unittitle",
        "dublin_core": "dc:relation (isPartOf)",
        "mets": "—",
    },
    "series": {
        "ead": "c[@level='series']/did/unittitle",
        "dublin_core": "dc:relation (isPartOf)",
        "mets": "—",
    },
    "subseries": {
        "ead": "c[@level='subseries']/did/unittitle",
        "dublin_core": "dc:relation (isPartOf)",
        "mets": "—",
    },
    "folder": {
        "ead": "c[@level='file']/did/unittitle",
        "dublin_core": "dc:relation (isPartOf)",
        "mets": "—",
    },
    "date": {
        "ead": "did/unitdate",
        "dublin_core": "dc:date",
        "mets": "—",
    },
    "sender": {
        "ead": "controlaccess/persname[@role='sender']",
        "dublin_core": "dc:creator",
        "mets": "—",
    },
    "recipient": {
        "ead": "controlaccess/persname[@role='recipient']",
        "dublin_core": "dc:contributor",
        "mets": "—",
    },
    "transcription": {
        "ead": "did/langmaterial/note[@type='transcription']",
        "dublin_core": "dc:description (fullText)",
        "mets": "dmdSec/mdWrap/xmlData/transcription",
    },
    "summary": {
        "ead": "did/abstract",
        "dublin_core": "dc:description",
        "mets": "dmdSec/mdWrap/xmlData/abstract",
    },
    "image_path": {
        "ead": "did/daogrp/daoloc/@href",
        "dublin_core": "—",
        "mets": "fileSec/fileGrp/file/FLocat/@xlink:href",
    },
    "access_conditions": {
        "ead": "accessrestrict",
        "dublin_core": "dc:rights",
        "mets": "—",
    },
    "provenance": {
        "ead": "custodhist",
        "dublin_core": "dc:source",
        "mets": "—",
    },
    "confidence_score": {
        "ead": "—",
        "dublin_core": "—",
        "mets": "—",
    },
    "events": {
        "ead": "processinfo",
        "dublin_core": "—",
        "mets": "amdSec/digiprovMD",
        "premis": "event/eventType, event/eventDateTime, event/linkingAgentIdentifier (provider/model)",
    },
}
