import json

from PIL import Image
from reportlab.pdfbase import pdfmetrics

from regeste.core.export import ExportOptions, export_registry
from regeste.core.registry import Registry
from regeste.core.transcription_mode import TranscriptionMode
from regeste.i18n import set_language


def _registry_with_one_entry(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["archive.jpg"])
    registry.record_result(
        "archive.jpg",
        text="Hello world",
        description="An old postcard",
        tokens_in=100,
        tokens_out=50,
        cost=0.02,
        model="claude-sonnet-5",
    )
    return registry


def test_export_skips_unsuccessful_files(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["pending.jpg"])
    output = tmp_path / "output"
    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"json"})),
    )
    content = json.loads((output / "my_project" / "combined" / "my_project.json").read_text())
    assert content == []


def test_export_markdown_and_json_single_file(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    files = export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"md", "json"}), per_file=False),
    )

    md_path = output / "my_project" / "combined" / "my_project.md"
    json_path = output / "my_project" / "combined" / "my_project.json"
    assert set(files) == {md_path, json_path}
    assert "archive.jpg" in md_path.read_text()
    assert "Hello world" in md_path.read_text()

    data = json.loads(json_path.read_text())
    assert data == [
        {
            "name": "archive.jpg",
            "text": "Hello world",
            "description": "An old postcard",
            "status": "ok",
            "tokens_in": 100,
            "tokens_out": 50,
            "cost": 0.02,
            "model": "claude-sonnet-5",
            "date": data[0]["date"],
        }
    ]


def test_export_per_file(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"txt"}), single_file=False),
    )

    path = output / "my_project" / "per_file" / "archive.txt"
    assert path.exists()
    assert "Hello world" in path.read_text()


def test_export_pdf_searchable_with_source_image(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    Image.new("RGB", (200, 100), color="white").save(tmp_path / "archive.jpg")
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False),
    )

    pdf_path = output / "my_project" / "combined" / "my_project.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0


def test_export_pdf_without_source_image_does_not_crash(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False),
    )

    assert (output / "my_project" / "combined" / "my_project.pdf").exists()


def test_export_pdf_registers_cjk_font_for_japanese_interface(tmp_path):
    set_language("ja")
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False),
    )

    assert (output / "my_project" / "combined" / "my_project.pdf").exists()
    assert "HeiseiKakuGo-W5" in pdfmetrics.getRegisteredFontNames()


def test_export_pdf_registers_cjk_font_for_chinese_interface(tmp_path):
    set_language("zh")
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False),
    )

    assert (output / "my_project" / "combined" / "my_project.pdf").exists()
    assert "STSong-Light" in pdfmetrics.getRegisteredFontNames()


def test_export_pdf_keeps_helvetica_for_non_cjk_interface(tmp_path):
    set_language("fr")
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False),
    )

    assert (output / "my_project" / "combined" / "my_project.pdf").exists()


_HYPOTHESES_OPTIONS = dict(transcription_mode=TranscriptionMode.HYPOTHESES)


def test_hypotheses_mode_prepends_legend_to_combined_markdown(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"md"}), per_file=False, **_HYPOTHESES_OPTIONS),
    )

    content = (output / "my_project" / "combined" / "my_project.md").read_text()
    assert content.startswith("## HYPOTHESES")
    assert "[[hypothesis:" in content
    assert "Hello world" in content  # entries still exported below the legend


def test_hypotheses_mode_prepends_legend_to_each_per_file_text(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"txt"}), single_file=False, **_HYPOTHESES_OPTIONS),
    )

    content = (output / "my_project" / "per_file" / "archive.txt").read_text()
    assert content.startswith("## HYPOTHESES")


def test_hypotheses_mode_wraps_json_with_legend(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"json"}), per_file=False, **_HYPOTHESES_OPTIONS),
    )

    payload = json.loads((output / "my_project" / "combined" / "my_project.json").read_text())
    assert payload["transcription_mode"] == "hypotheses"
    assert payload["hypotheses_legend"].startswith("## HYPOTHESES")
    assert [entry["name"] for entry in payload["entries"]] == ["archive.jpg"]


def test_literal_mode_keeps_exports_free_of_legend(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"md", "json"}), per_file=False),
    )

    md = (output / "my_project" / "combined" / "my_project.md").read_text()
    assert "## HYPOTHESES" not in md
    payload = json.loads((output / "my_project" / "combined" / "my_project.json").read_text())
    assert isinstance(payload, list)  # historical structure, no wrapper object


def test_hypotheses_mode_pdf_gets_an_intro_legend_page(tmp_path):
    registry = _registry_with_one_entry(tmp_path)
    Image.new("RGB", (200, 100), color="white").save(tmp_path / "archive.jpg")
    output = tmp_path / "output"

    export_registry(
        registry,
        source_dir=tmp_path,
        output_dir=output,
        project_name="my_project",
        options=ExportOptions(formats=frozenset({"pdf"}), per_file=False, **_HYPOTHESES_OPTIONS),
    )

    pdf_path = output / "my_project" / "combined" / "my_project.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0
