"""Background threads for the GUI (spec §10): transcription run + model listing.

`Transcriber.run()` and `Provider.list_vision_models()` are both blocking calls
that must never execute on the GUI thread. Each gets a `QObject` moved to its
own `QThread`; signals are queued back to the GUI thread automatically by Qt.

Same rationale applies to the pivot exporters (I/O-bound, one run can cover 12
formats over a whole corpus) and to `translate_piece()` (network call) — see
`ExportWorker`/`TranslationWorker` below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from regeste.core.costs import CostTracker
from regeste.core.project import ProviderConfig
from regeste.core.providers.base import ModelInfo
from regeste.core.registry import Registry
from regeste.core.transcriber import Transcriber, create_provider
from regeste.pivot import Piece
from regeste.translation import TranslationProvider, translate_piece


class TranscriptionWorker(QObject):
    """Runs `Transcriber.run()` off the GUI thread, relaying live progress."""

    progress = Signal(object)  # ProgressState
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        transcriber: Transcriber,
        registry: Registry,
        mode: str,
        cost_tracker: CostTracker,
    ) -> None:
        super().__init__()
        self._transcriber = transcriber
        self._registry = registry
        self._mode = mode
        self._cost_tracker = cost_tracker

    def run(self) -> None:
        try:
            self._transcriber.run(
                self._registry,
                self._mode,
                self._cost_tracker,
                on_progress=lambda state: self.progress.emit(state),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash
            self.failed.emit(str(exc))
            return
        self.finished.emit()


class ModelFetchWorker(QObject):
    """Fetches vision models for a provider off the GUI thread (Settings > Providers)."""

    succeeded = Signal(list)  # list[ModelInfo]
    failed = Signal(str)

    def __init__(self, provider_config: ProviderConfig) -> None:
        super().__init__()
        self._provider_config = provider_config

    def run(self) -> None:
        try:
            provider = create_provider(self._provider_config)
            models: list[ModelInfo] = provider.list_vision_models()
        except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(models)


class ExportWorker(QObject):
    """Runs a batch of pivot exporters off the GUI thread.

    Takes ready-made zero-argument jobs (label, callable) rather than exporter
    functions directly, so this stays ignorant of the 12 exporters' individual
    signatures (file vs. directory output) — that mapping lives in
    `gui/panels/export_panel.py`.
    """

    progress = Signal(str)  # label of the format that just finished
    finished = Signal(list)  # list[Path] written
    failed = Signal(str)

    def __init__(self, jobs: list[tuple[str, Callable[[], Path]]]) -> None:
        super().__init__()
        self._jobs = jobs

    def run(self) -> None:
        written: list[Path] = []
        try:
            for label, job in self._jobs:
                written.append(job())
                self.progress.emit(label)
        except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash
            self.failed.emit(str(exc))
            return
        self.finished.emit(written)


class TranslationWorker(QObject):
    """Runs `translate_piece()` off the GUI thread (network call)."""

    succeeded = Signal(object)  # Piece, mutated in place with the new translation
    failed = Signal(str)

    def __init__(
        self,
        piece: Piece,
        target_language: str,
        provider: TranslationProvider,
        model: str,
        *,
        glossary: dict[str, str] | None = None,
        source_language: str = "",
        template: str | None = None,
    ) -> None:
        super().__init__()
        self._piece = piece
        self._target_language = target_language
        self._provider = provider
        self._model = model
        self._glossary = glossary
        self._source_language = source_language
        self._template = template

    def run(self) -> None:
        try:
            translate_piece(
                self._piece,
                self._target_language,
                self._provider,
                self._model,
                glossary=self._glossary,
                source_language=self._source_language,
                template=self._template,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via `failed`, never a crash (incl. TranslationBlocked)
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(self._piece)


def start_worker(worker: QObject) -> QThread:
    """Moves `worker` to a fresh `QThread` and wires the standard lifecycle.

    The caller must connect its own slots and call `thread.start()` — this only
    prepares the thread and the auto-quit-on-completion wiring, so signals
    added afterward are not missed.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    if hasattr(worker, "finished"):
        worker.finished.connect(thread.quit)
    if hasattr(worker, "succeeded"):
        worker.succeeded.connect(thread.quit)
    worker.failed.connect(thread.quit)
    return thread
