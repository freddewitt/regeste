"""`regeste.json` registry — atomic writes, New/Resume modes (spec §5)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

REGISTRY_FILENAME = "regeste.json"

Status = Literal["pending", "ok", "error"]


@dataclass
class FileEntry:
    status: Status = "pending"
    text: str = ""
    description: str = ""
    language: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    model: str | None = None
    date: str | None = None
    error_message: str | None = None


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
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        files = {name: FileEntry(**values) for name, values in data.get("files", {}).items()}
        return cls(source_dir=source_dir, meta=data.get("meta", {}), files=files)

    @classmethod
    def new(cls, source_dir: Path, meta: dict[str, Any], file_names: list[str]) -> "Registry":
        """Start a project, or start over from scratch (existing meta/status is overwritten)."""
        files = {name: FileEntry() for name in file_names}
        registry = cls(source_dir=source_dir, meta=meta, files=files)
        registry.save()
        return registry

    def files_to_process(self, mode: Literal["new", "resume"]) -> list[str]:
        """In resume mode: skip `ok`, continue `pending`, and auto-retry `error` (spec §5.2)."""
        if mode == "new":
            return list(self.files.keys())
        return [name for name, entry in self.files.items() if entry.status != "ok"]

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
    ) -> None:
        self.files[file_name] = FileEntry(
            status="ok",
            text=text,
            description=description,
            language=language,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            model=model,
            date=datetime.now(timezone.utc).isoformat(),
        )

    def record_error(self, file_name: str, message: str) -> None:
        self.files[file_name] = FileEntry(
            status="error",
            date=datetime.now(timezone.utc).isoformat(),
            error_message=message,
        )

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
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
