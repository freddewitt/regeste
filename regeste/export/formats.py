"""Shared registry of the 12 per-piece archival exporters.

`key -> (exporter_fn, output file/dir name)`. Exporters are read-only over the
pivot corpus and share the signature `exporter(pieces, output_path, *,
validated_only=False)`. Used by both the GUI Export tab and the CLI so the
format list stays defined in one place. Labels (i18n) live in the GUI.
"""

from __future__ import annotations

from collections.abc import Callable

from .csv_export import export_csv_full, export_csv_light
from .dc import export_dublin_core
from .ead import export_ead
from .html_export import export_html
from .markdown_export import export_markdown, export_markdown_obsidian
from .mets import export_mets
from .pdf_export import export_pdf
from .sqlite_export import export_sqlite
from .xlsx_export import export_xlsx
from .zip_export import export_zip

PIVOT_EXPORTERS: dict[str, tuple[Callable, str]] = {
    "ead": (export_ead, "ead.xml"),
    "dc": (export_dublin_core, "dublin_core.xml"),
    "mets": (export_mets, "mets"),
    "csv_light": (export_csv_light, "export_light.csv"),
    "csv_full": (export_csv_full, "export_full.csv"),
    "xlsx": (export_xlsx, "export.xlsx"),
    "zip": (export_zip, "archive.zip"),
    "markdown": (export_markdown, "export.md"),
    "markdown_obsidian": (export_markdown_obsidian, "obsidian"),
    "sqlite": (export_sqlite, "export.db"),
    "html": (export_html, "export.html"),
    "pdf": (export_pdf, "export.pdf"),
}
