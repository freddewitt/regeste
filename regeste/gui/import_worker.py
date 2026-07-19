"""Batch photo import — copies (or references) an external folder into a project.

File copies are long I/O that must never run on the GUI thread, so the import
lives in a `QObject` moved to its own `QThread` via `start_worker()` — the same
pattern as `TranscriptionWorker`/`ExportWorker`.

Registry writes are atomic per batch: entries are collected in memory and
`registry.save()` runs exactly once at the end. If any copy fails, the entries
added during the run are rolled back in memory and nothing is persisted, so the
registry can never point at a file whose copy did not happen.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from regeste.core.imaging import IMAGE_EXTENSIONS
from regeste.core.registry import FileEntry, Registry, SourceInfo
from regeste.i18n import _

logger = logging.getLogger(__name__)


def list_importable_images(folder: Path) -> list[Path]:
    """Image files directly inside `folder` (`IMAGE_EXTENSIONS` only), sorted by name.

    Non-image files are ignored (reported via the log, never an error).
    """
    images: list[Path] = []
    ignored = 0
    for path in sorted(folder.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(path)
        else:
            ignored += 1
    if ignored:
        logger.info(
            _("Import: ignored {count} non-image file(s) in {folder}").format(
                count=ignored, folder=folder
            )
        )
    return images


class BatchImportWorker(QObject):
    """Imports a list of image files into a project registry, off the GUI thread."""

    progress = Signal(int, int)  # done, total
    finished = Signal(int)  # number of registry entries newly created
    failed = Signal(str)

    def __init__(
        self,
        source_files: list[Path],
        batch_id: str,
        project_dir: Path,
        registry: Registry,
        keep_in_place: bool,
    ) -> None:
        super().__init__()
        self._source_files = source_files
        self._batch_id = batch_id
        self._project_dir = project_dir
        self._registry = registry
        self._keep_in_place = keep_in_place

    def run(self) -> None:
        total = len(self._source_files)
        target_dir = self._project_dir / "images" / self._batch_id
        added_keys: list[str] = []
        for done, source in enumerate(self._source_files, start=1):
            key = f"{self._batch_id}_{source.name}"
            if self._keep_in_place:
                # No copy: the registry points at the original absolute path.
                physical_path = str(source)
            else:
                destination = target_dir / key
                physical_path = str(destination)
                if destination.exists():
                    # Never overwrite an existing image: skip the copy silently.
                    logger.info(_("Import: {file} already exists, copy skipped").format(file=destination))
                else:
                    try:
                        target_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
                    except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash
                        # Roll back this run's in-memory entries: nothing was saved
                        # yet, so the registry must not keep entries for a failed batch.
                        for added in added_keys:
                            self._registry.files.pop(added, None)
                        self.failed.emit(f"{source.name}: {exc}")
                        return
            if key not in self._registry.files:
                # Existing entries are never touched (re-import is resume-safe).
                self._registry.files[key] = FileEntry(
                    source=SourceInfo(
                        batch_id=self._batch_id,
                        physical_path=physical_path,
                        imported=not self._keep_in_place,
                    ),
                )
                added_keys.append(key)
            self.progress.emit(done, total)
        try:
            self._registry.save()
        except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash
            for added in added_keys:
                self._registry.files.pop(added, None)
            self.failed.emit(str(exc))
            return
        self.finished.emit(len(added_keys))
