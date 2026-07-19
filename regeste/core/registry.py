"""`regeste.json` registry — atomic writes, New/Resume modes (spec §5).

Schema v2: namespaced entries with ``source`` / ``transcription`` sub-objects.
Schema v1 (flat fields, no ``schema_version``) is migrated transparently on load.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "regeste.json"

Status = Literal["pending", "ok", "error"]


@dataclass
class SourceInfo:
    """Where the file comes from."""

    batch_id: str = ""
    physical_path: str = ""
    imported: bool = False
    key_prefixed: bool = True


@dataclass
class TranscriptionInfo:
    """OCR run result for a single file."""

    status: Status = "pending"
    mode: str = ""
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    model: str | None = None
    date: str | None = None
    error_message: str | None = None


@dataclass
class FileEntry:
    source: SourceInfo = field(default_factory=SourceInfo)
    transcription: TranscriptionInfo = field(default_factory=TranscriptionInfo)
    description: str = ""
    language: str = ""


def _migrate_v1_entry(values: dict[str, Any], source_dir: Path) -> dict[str, Any]:
    """Convert a flat v1 ``FileEntry`` dict to the v2 nested structure.

    Called in-memory only; the migrated registry is saved as v2 immediately.
    """
    batch_id = source_dir.name
    # Rebuild the key from the caller (we don't have it here, but the caller
    # passes the raw dict which already contains the flat fields).
    return {
        "source": {
            "batch_id": batch_id,
            "physical_path": "",  # unknown for legacy entries
            "imported": False,
            "key_prefixed": False,  # migrated entries keep their original unprefixed keys
        },
        "transcription": {
            "status": values.get("status", "pending"),
            "mode": "",  # filled from meta.transcription_mode after load
            "text": values.get("text", ""),
            "tokens_in": values.get("tokens_in", 0),
            "tokens_out": values.get("tokens_out", 0),
            "cost": values.get("cost", 0.0),
            "model": values.get("model"),
            "date": values.get("date"),
            "error_message": values.get("error_message"),
        },
        "description": values.get("description", ""),
        "language": values.get("language", ""),
    }


def _is_v1(data: dict[str, Any]) -> bool:
    """A registry is v1 if ``schema_version`` is absent or 1."""
    return data.get("meta", {}).get("schema_version", 1) < 2


@dataclass
class Registry:
    """A project's registry: meta + per-file status.

    Placed at the root of the source document folder (not the output folder)
    so it travels with the archive (spec §5.1).
    """

    source_dir: Path
    meta: dict[str, Any] = field(default_factory=dict)
    files: dict[str, FileEntry] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.source_dir / REGISTRY_FILENAME

    @classmethod
    def load(cls, source_dir: Path) -> "Registry | None":
        path = source_dir / REGISTRY_FILENAME
        if not path.exists():
            logger.debug("No registry at %s", path)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        v1 = _is_v1(data)
        project_mode = data.get("meta", {}).get("transcription_mode", "")

        files: dict[str, FileEntry] = {}
        for name, values in data.get("files", {}).items():
            if v1:
                values = _migrate_v1_entry(values, source_dir)
                # Inherit project-level transcription mode for legacy entries.
                if not values["transcription"]["mode"]:
                    values["transcription"]["mode"] = project_mode
            files[name] = FileEntry(
                source=SourceInfo(**values["source"]),
                transcription=TranscriptionInfo(**values["transcription"]),
                description=values.get("description", ""),
                language=values.get("language", ""),
            )

        meta = data.get("meta", {})
        # Auto-migrate: bump schema_version and re-save.
        if v1:
            meta["schema_version"] = 2
            registry = cls(source_dir=source_dir, meta=meta, files=files)
            registry.save()
            logger.debug("Registry migrated v1 -> v2 and saved to %s", path)
            return registry

        logger.debug("Registry loaded from %s: %d file(s)", path, len(files))
        return cls(source_dir=source_dir, meta=meta, files=files)

    @classmethod
    def new(cls, source_dir: Path, meta: dict[str, Any], file_names: list[str]) -> "Registry":
        """Start a project, or start over from scratch (existing meta/status is overwritten)."""
        logger.debug("Registry: starting new project in %s, %d file(s)", source_dir, len(file_names))
        meta.setdefault("schema_version", 2)
        batch_id = source_dir.name
        files: dict[str, FileEntry] = {}
        for name in file_names:
            prefixed_name = f"{batch_id}_{name}"
            files[prefixed_name] = FileEntry(
                source=SourceInfo(
                    batch_id=batch_id,
                    physical_path=str(source_dir / name),
                    imported=False,
                ),
            )
        registry = cls(source_dir=source_dir, meta=meta, files=files)
        registry.save()
        return registry

    def files_to_process(self, mode: Literal["new", "resume"]) -> list[str]:
        """In resume mode: skip `ok`, continue `pending`, and auto-retry `error` (spec §5.2)."""
        if mode == "new":
            result = list(self.files.keys())
        else:
            result = [name for name, entry in self.files.items() if entry.transcription.status != "ok"]
        logger.debug("files_to_process(mode=%s): %d of %d file(s) due", mode, len(result), len(self.files))
        return result

    def record_result(
        self,
        file_name: str,
        *,
        text: str,
        description: str,
        tokens_in: int,
        tokens_out: int,
        cost: float,
        model: str,
        language: str = "",
        transcription_mode: str = "",
    ) -> None:
        entry = self.files.get(file_name) or FileEntry()
        entry.transcription = TranscriptionInfo(
            status="ok",
            mode=transcription_mode,
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            model=model,
            date=datetime.now(timezone.utc).isoformat(),
        )
        entry.description = description
        entry.language = language
        self.files[file_name] = entry
        logger.debug(
            "%s: recorded ok (model=%s, tokens_in=%d, tokens_out=%d, cost=%.4f)",
            file_name, model, tokens_in, tokens_out, cost,
        )

    def record_error(self, file_name: str, message: str) -> None:
        entry = self.files.get(file_name) or FileEntry()
        entry.transcription = TranscriptionInfo(
            status="error",
            date=datetime.now(timezone.utc).isoformat(),
            error_message=message,
        )
        self.files[file_name] = entry
        logger.debug("%s: recorded error - %s", file_name, message)

    def display_name(self, key: str) -> str:
        """Return the human-readable file name for a registry key.
        
        If the entry has ``key_prefixed=True`` and the batch_id is set,
        strips the ``<batch_id>_`` prefix from the key. Otherwise returns
        the key unchanged (backward compatibility with migrated v1 entries
        and entries without a batch_id).
        """
        entry = self.files.get(key)
        if entry is not None and entry.source.key_prefixed and entry.source.batch_id:
            prefix = entry.source.batch_id + "_"
            if key.startswith(prefix):
                return key[len(prefix):]
        return key

    def get_by_display_name(self, display_name: str) -> FileEntry | None:
        """Look up a FileEntry by its human-readable display name.
        
        Searches for a key whose display_name() matches the given name.
        Returns None if not found.
        """
        for key in self.files:
            if self.display_name(key) == display_name:
                return self.files[key]
        return None

    def save(self) -> None:
        """Atomic write: temp file + `os.replace`, never a corrupted registry (spec §5.1)."""
        data = {
            "meta": self.meta,
            "files": {name: asdict(entry) for name, entry in self.files.items()},
        }
        self.source_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.source_dir, prefix=f".{REGISTRY_FILENAME}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
            logger.debug("Registry saved to %s (%d file(s))", self.path, len(self.files))
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
