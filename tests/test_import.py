"""Batch photo import (Phase 4) — dialog, worker, and MainWindow wiring.

Runs headless (offscreen). No provider, no network: the import only touches the
filesystem and the registry.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QMessageBox

from regeste.core.export import ExportOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.registry import FileEntry, Registry, SourceInfo
from regeste.gui.import_dialog import BatchImportDialog, resolve_batch_id
from regeste.gui.import_worker import BatchImportWorker, list_importable_images
from regeste.gui.main_window import MainWindow


def _image(directory, name):
    Image.new("RGB", (16, 16), color="white").save(directory / name)


def _make_project(tmp_path, file_names=("a.jpg",)):
    """A project root with an existing v2 registry (the import target)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    for name in file_names:
        _image(project_dir, name)
    registry = Registry.new(project_dir, meta={}, file_names=list(file_names))
    return project_dir, registry


def _make_external_folder(tmp_path, names=("x.jpg", "y.jpg")):
    folder = tmp_path / "external"
    folder.mkdir()
    for name in names:
        _image(folder, name)
    return folder


def _run_worker(worker):
    """Run synchronously on the test thread; signals fire via direct connections."""
    results = {"progress": [], "finished": None, "failed": None}
    worker.progress.connect(lambda done, total: results["progress"].append((done, total)))
    worker.finished.connect(lambda count: results.__setitem__("finished", count))
    worker.failed.connect(lambda msg: results.__setitem__("failed", msg))
    worker.run()
    return results


# --- File filtering ------------------------------------------------------------


def test_list_importable_images_ignores_non_images(tmp_path):
    folder = _make_external_folder(tmp_path, ("x.jpg", "y.png"))
    (folder / "notes.txt").write_text("hello")
    (folder / "data.pdf").write_text("pdf")
    images = list_importable_images(folder)
    assert [p.name for p in images] == ["x.jpg", "y.png"]


# --- Worker: copy mode ----------------------------------------------------------


def test_import_with_copy_copies_files_and_registers_entries(qtbot, tmp_path):
    project_dir, registry = _make_project(tmp_path)
    folder = _make_external_folder(tmp_path, ("x.jpg", "y.jpg"))
    source_files = list_importable_images(folder)

    worker = BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=False)
    results = _run_worker(worker)

    assert results["failed"] is None
    assert results["finished"] == 2
    assert results["progress"] == [(1, 2), (2, 2)]

    for name in ("x.jpg", "y.jpg"):
        copied = project_dir / "images" / "lot1" / f"lot1_{name}"
        assert copied.exists()
        entry = registry.files[f"lot1_{name}"]
        assert entry.source.batch_id == "lot1"
        assert entry.source.physical_path == str(copied)
        assert entry.source.imported is True
        assert entry.transcription.status == "pending"

    # Persisted on disk, resumable via files_to_process("resume").
    reloaded = Registry.load(project_dir)
    assert "lot1_x.jpg" in reloaded.files
    assert "lot1_y.jpg" in reloaded.files
    assert set(reloaded.files_to_process("resume")) >= {"lot1_x.jpg", "lot1_y.jpg"}


# --- Worker: in-place mode -------------------------------------------------------


def test_import_in_place_copies_nothing_and_marks_not_imported(qtbot, tmp_path):
    project_dir, registry = _make_project(tmp_path)
    folder = _make_external_folder(tmp_path, ("x.jpg",))
    source_files = list_importable_images(folder)

    worker = BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=True)
    results = _run_worker(worker)

    assert results["failed"] is None
    assert results["finished"] == 1
    assert not (project_dir / "images").exists()
    entry = registry.files["lot1_x.jpg"]
    assert entry.source.imported is False
    assert entry.source.physical_path == str(folder / "x.jpg")


# --- Collision resolution ---------------------------------------------------------


def test_resolve_batch_id_returns_requested_when_free(qtbot, tmp_path):
    _, registry = _make_project(tmp_path)
    assert resolve_batch_id("lot1", registry) == "lot1"


def test_resolve_batch_id_suffixes_on_collision(qtbot, tmp_path):
    _, registry = _make_project(tmp_path)
    registry.files["lot1_a.jpg"] = FileEntry(source=SourceInfo(batch_id="lot1"))
    assert resolve_batch_id("lot1", registry) == "lot1-2"
    registry.files["lot1-2_a.jpg"] = FileEntry(source=SourceInfo(batch_id="lot1-2"))
    assert resolve_batch_id("lot1", registry) == "lot1-3"


def test_dialog_reports_collision_and_exposes_resolved_id(qtbot, tmp_path, monkeypatch):
    project_dir, registry = _make_project(tmp_path)
    registry.files["lot1_a.jpg"] = FileEntry(source=SourceInfo(batch_id="lot1"))
    messages = []
    monkeypatch.setattr(
        "regeste.gui.import_dialog.QMessageBox.information",
        lambda *args, **kwargs: messages.append(args[2]),
    )
    dialog = BatchImportDialog(project_dir=project_dir, registry=registry)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(tmp_path))
    dialog.batch_id_edit.setText("lot1")
    dialog.accept()
    assert dialog.batch_id == "lot1-2"
    assert messages and "lot1-2" in messages[0]


def test_dialog_import_button_disabled_until_identifier_given(qtbot, tmp_path):
    project_dir, registry = _make_project(tmp_path)
    dialog = BatchImportDialog(project_dir=project_dir, registry=registry)
    qtbot.addWidget(dialog)
    assert dialog.import_button.isEnabled() is False
    dialog.folder_edit.setText(str(tmp_path))
    assert dialog.import_button.isEnabled() is False
    dialog.batch_id_edit.setText("  ")
    assert dialog.import_button.isEnabled() is False
    dialog.batch_id_edit.setText("lot1")
    assert dialog.import_button.isEnabled() is True
    assert dialog.keep_in_place is False


# --- Resume / no-overwrite ---------------------------------------------------------


def test_reimport_never_overwrites_existing_entries_or_files(qtbot, tmp_path):
    project_dir, registry = _make_project(tmp_path)
    folder = _make_external_folder(tmp_path, ("x.jpg",))
    source_files = list_importable_images(folder)

    # First import, then simulate a completed transcription on the entry.
    _run_worker(BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=False))
    registry.record_result(
        "lot1_x.jpg", text="TRANSCRIPT", description="", tokens_in=1, tokens_out=1,
        cost=0.0, model="fake", transcription_mode="literal",
    )
    registry.save()
    copied = project_dir / "images" / "lot1" / "lot1_x.jpg"
    copied.write_bytes(b"modified-after-import")

    # Second import of the same folder/batch: nothing is touched.
    results = _run_worker(
        BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=False)
    )
    assert results["failed"] is None
    assert results["finished"] == 0
    entry = registry.files["lot1_x.jpg"]
    assert entry.transcription.status == "ok"
    assert entry.transcription.text == "TRANSCRIPT"
    assert copied.read_bytes() == b"modified-after-import"


# --- Copy failure -------------------------------------------------------------------


def test_copy_failure_leaves_no_registry_entries(qtbot, tmp_path, monkeypatch):
    project_dir, registry = _make_project(tmp_path)
    folder = _make_external_folder(tmp_path, ("x.jpg", "y.jpg"))
    source_files = list_importable_images(folder)
    before = set(registry.files)

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", _boom)
    worker = BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=False)
    results = _run_worker(worker)

    assert results["finished"] is None
    assert "disk full" in results["failed"]
    assert set(registry.files) == before
    # Nothing persisted either.
    reloaded = Registry.load(project_dir)
    assert set(reloaded.files) == before


def test_copy_failure_rolls_back_entries_from_earlier_files_in_batch(qtbot, tmp_path, monkeypatch):
    project_dir, registry = _make_project(tmp_path)
    folder = _make_external_folder(tmp_path, ("x.jpg", "y.jpg"))
    source_files = list_importable_images(folder)
    before = set(registry.files)
    original_copy2 = shutil.copy2

    def _fail_on_second(src, dst):
        if Path(src).name.startswith("y"):
            raise OSError("disk full")
        return original_copy2(src, dst)

    monkeypatch.setattr(shutil, "copy2", _fail_on_second)
    worker = BatchImportWorker(source_files, "lot1", project_dir, registry, keep_in_place=False)
    results = _run_worker(worker)

    assert results["failed"] is not None
    # x.jpg copied fine but its entry was rolled back with the rest of the batch.
    assert set(registry.files) == before
    assert set(Registry.load(project_dir).files) == before


# --- MainWindow integration ----------------------------------------------------------


class _FakeDialog:
    """Stands in for BatchImportDialog.exec() in the wired-up test (no real dialog)."""

    def __init__(self, source_folder, batch_id, keep_in_place):
        self.source_folder = source_folder
        self.batch_id = batch_id
        self.keep_in_place = keep_in_place

    def exec(self):
        from PySide6.QtWidgets import QDialog

        return QDialog.DialogCode.Accepted


def test_main_window_import_flow_end_to_end(qtbot, tmp_path, monkeypatch):
    project_dir, registry = _make_project(tmp_path)
    # set_source_dir() restores the project config - give the registry a full meta.
    registry.meta = ProjectConfig(
        project_name="proj",
        source_dir=project_dir,
        output_dir=project_dir,
        provider=ProviderConfig(kind="claude", model="m"),
        export=ExportOptions(formats=frozenset({"md"}), single_file=True, per_file=False),
    ).to_meta()
    registry.save()
    folder = _make_external_folder(tmp_path, ("x.jpg", "y.jpg"))
    monkeypatch.setattr(
        "regeste.gui.main_window.BatchImportDialog",
        lambda *args, **kwargs: _FakeDialog(folder, "lot1", False),
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

    window._on_import_batch_clicked()
    assert window._import_worker is not None
    assert window.import_batch_action.isEnabled() is False
    assert window.launch_button.isEnabled() is False

    qtbot.waitUntil(lambda: window._import_worker is None, timeout=5000)

    assert window.import_batch_action.isEnabled() is True
    assert window.launch_button.isEnabled() is True
    reloaded = Registry.load(project_dir)
    assert "lot1_x.jpg" in reloaded.files
    assert "lot1_y.jpg" in reloaded.files
    assert (project_dir / "images" / "lot1" / "lot1_x.jpg").exists()
    assert infos  # success message shown


def test_main_window_import_requires_open_project(qtbot, monkeypatch):
    infos = []
    monkeypatch.setattr(
        "regeste.gui.main_window.QMessageBox.information",
        lambda *args, **kwargs: infos.append(args),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_import_batch_clicked()
    assert infos
    assert window._import_worker is None
