"""Portable project archive: API keys stripped, images bundled, round-trips on extraction."""

from __future__ import annotations

import json
import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PIL import Image

from regeste.core.export import ExportOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.project_archive import REGISTRY_ENTRY_NAME, export_project_archive
from regeste.core.registry import Registry
from regeste.gui.main_window import MainWindow
from regeste.gui.worker import ProjectArchiveWorker


def _image(directory, name):
    Image.new("RGB", (16, 16), color="white").save(directory / name)


def _make_project(tmp_path, file_names=("a.jpg", "b.jpg")):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    for name in file_names:
        _image(project_dir, name)
    registry = Registry.new(project_dir, meta={}, file_names=list(file_names))
    registry.meta = ProjectConfig(
        project_name="proj",
        source_dir=project_dir,
        output_dir=project_dir,
        provider=ProviderConfig(kind="claude", model="m", api_key="secret-key"),
        translation_provider=ProviderConfig(kind="gemini", model="t", api_key="other-secret"),
        export=ExportOptions(formats=frozenset({"md"}), single_file=True, per_file=False),
    ).to_meta()
    registry.save()
    return project_dir, registry


def _run_worker(worker):
    results = {"progress": [], "finished": None, "failed": None}
    worker.progress.connect(lambda done, total: results["progress"].append((done, total)))
    worker.finished.connect(lambda path: results.__setitem__("finished", path))
    worker.failed.connect(lambda message: results.__setitem__("failed", message))
    worker.run()
    return results


# --- Core: export_project_archive --------------------------------------------------


def test_archive_contains_registry_and_images(tmp_path):
    project_dir, registry = _make_project(tmp_path)
    archive_path = tmp_path / "out.zip"

    export_project_archive(registry, archive_path)

    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
        assert names == {REGISTRY_ENTRY_NAME, *registry.files.keys()}
        data = json.loads(zf.read(REGISTRY_ENTRY_NAME))

    assert data["meta"]["provider"]["api_key"] is None
    assert data["meta"]["translation_provider"]["api_key"] is None
    for entry in data["files"].values():
        assert entry["source"]["physical_path"] == ""


def test_archive_reports_progress(tmp_path):
    project_dir, registry = _make_project(tmp_path, ("a.jpg", "b.jpg", "c.jpg"))
    archive_path = tmp_path / "out.zip"
    progress = []

    export_project_archive(registry, archive_path, on_progress=lambda done, total: progress.append((done, total)))

    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_archive_extraction_round_trips_via_registry_load(tmp_path):
    project_dir, registry = _make_project(tmp_path)
    archive_path = tmp_path / "out.zip"
    export_project_archive(registry, archive_path)

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(extract_dir)

    reloaded = Registry.load(extract_dir)
    assert reloaded is not None
    assert set(reloaded.files) == set(registry.files)
    for name, entry in reloaded.files.items():
        assert entry.source.physical_path == ""
        assert (extract_dir / name).exists()
    assert reloaded.meta["provider"]["api_key"] is None


def test_archive_skips_missing_image_without_failing(tmp_path):
    project_dir, registry = _make_project(tmp_path, ("a.jpg", "b.jpg"))
    (project_dir / "b.jpg").unlink()
    archive_path = tmp_path / "out.zip"
    remaining_key = next(k for k in registry.files if k.endswith("a.jpg"))

    export_project_archive(registry, archive_path)

    with zipfile.ZipFile(archive_path) as zf:
        assert set(zf.namelist()) == {REGISTRY_ENTRY_NAME, remaining_key}


# --- Worker --------------------------------------------------------------------------


def test_worker_writes_archive(qtbot, tmp_path):
    project_dir, registry = _make_project(tmp_path)
    archive_path = tmp_path / "out.zip"

    worker = ProjectArchiveWorker(registry, archive_path)
    results = _run_worker(worker)

    assert results["failed"] is None
    assert results["finished"] == archive_path
    assert archive_path.exists()


# --- MainWindow wiring -----------------------------------------------------------


def test_main_window_export_archive_requires_open_project(qtbot, monkeypatch):
    infos = []
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.information",
        lambda *args, **kwargs: infos.append(args),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_export_archive_clicked()
    assert window._archive_worker is None
    assert infos


def test_main_window_export_archive_writes_zip(qtbot, tmp_path, monkeypatch):
    project_dir, registry = _make_project(tmp_path)
    archive_path = tmp_path / "out.zip"
    monkeypatch.setattr(
        "regeste.gui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(archive_path), "Zip (*.zip)"),
    )
    infos = []
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.information",
        lambda *args, **kwargs: infos.append(args),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_source_dir(project_dir)
    assert window._registry is not None

    window._on_export_archive_clicked()
    assert window._archive_worker is not None
    assert window.export_archive_action.isEnabled() is False
    assert window.launch_button.isEnabled() is False

    qtbot.waitUntil(lambda: window._archive_worker is None, timeout=5000)

    assert window.export_archive_action.isEnabled() is True
    assert window.launch_button.isEnabled() is True
    assert archive_path.exists()
    assert infos
