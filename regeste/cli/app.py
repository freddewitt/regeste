"""Interactive CLI loop (spec §9) — same capabilities as the GUI, conversational.

Runs headless, no PySide6 import anywhere in this module.
"""

from __future__ import annotations

import getpass
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from regeste.core.costs import CostTracker, estimate_before_run
from regeste.core.export import KNOWN_FORMATS, ExportOptions, export_registry
from regeste.core.imaging import PreprocessOptions, ResizeOptions
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.core.providers import DEFAULT_BASE_URLS
from regeste.core.registry import FileEntry, Registry
from regeste.core.transcriber import DEFAULT_SYSTEM_PROMPT, ProgressState, Transcriber, create_provider
from regeste.i18n import _, format_cost
from regeste.export import PIVOT_EXPORTERS
from regeste.pivot import build_pieces_from_registry, load_piece, save_piece
from regeste.translation import (
    DEFAULT_TRANSLATION_PROMPT,
    create_translation_provider,
    load_glossary,
    translate_piece,
)

PROVIDER_KINDS = ("claude", "gemini", "openai", "lm_studio", "llama_cpp", "ollama")
REQUIRES_API_KEY_KINDS = ("claude", "gemini", "openai")
# Vision capability isn't always exposed cleanly by these two local backends (spec
# §2.3) - offer a manual "force this model" override as a last resort, after auto
# detection has been attempted. Not offered for claude/gemini/openai/ollama, whose
# detection the spec considers reliable enough on its own.
MANUAL_MODEL_KINDS = ("lm_studio", "llama_cpp")

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".gif",
}


class _Aborted(Exception):
    """Unwinds cleanly to `run()` when the user declines to continue — never a crash."""


@dataclass
class IO:
    """Bundles the three side-effecting calls the CLI needs, so tests can fake all of them."""

    input_func: Callable[[str], str] = input
    getpass_func: Callable[[str], str] = getpass.getpass
    print_func: Callable[..., None] = print


def _list_images(source_dir: Path) -> list[str]:
    return sorted(
        p.name for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _ask(io: IO, prompt: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = io.input_func(f"{prompt}{suffix}: ").strip()
    return answer or default


def _ask_yes_no(io: IO, prompt: str, *, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    answer = io.input_func(f"{prompt} {hint} ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _ask_int(io: IO, prompt: str, *, default: int) -> int:
    answer = io.input_func(f"{prompt} [{default}]: ").strip()
    if not answer:
        return default
    try:
        return int(answer)
    except ValueError:
        return default


def _ask_float_optional(io: IO, prompt: str) -> float | None:
    answer = io.input_func(f"{prompt}: ").strip()
    if not answer:
        return None
    try:
        return float(answer)
    except ValueError:
        return None


def _ask_choice_index(io: IO, prompt: str, count: int) -> int:
    while True:
        answer = io.input_func(f"{prompt} [1-{count}]: ").strip()
        try:
            index = int(answer) - 1
        except ValueError:
            index = -1
        if 0 <= index < count:
            return index
        io.print_func(_("Invalid choice, try again."))


def _ask_source_dir(io: IO) -> Path | None:
    while True:
        raw = io.input_func(_("Source folder") + ": ")
        path = Path(raw).expanduser()
        if path.is_dir():
            return path
        io.print_func(_("Not a folder: {path}").format(path=path))
        if not _ask_yes_no(io, _("Try again?"), default=True):
            return None


def _print_registry_summary(registry: Registry, io: IO) -> None:
    total = len(registry.files)
    ok = sum(1 for entry in registry.files.values() if entry.status == "ok")
    error = sum(1 for entry in registry.files.values() if entry.status == "error")
    pending = total - ok - error
    io.print_func(
        _("Existing project found: {total} files ({ok} ok, {error} error, {pending} pending)").format(
            total=total, ok=ok, error=error, pending=pending
        )
    )


def _sync_new_files(registry: Registry, source_dir: Path) -> None:
    """Add images that showed up in `source_dir` since the last session (spec §9)."""
    for name in _list_images(source_dir):
        if name not in registry.files:
            registry.files[name] = FileEntry()


def _configure_provider(io: IO) -> ProviderConfig:
    io.print_func(_("Available providers:"))
    for i, kind in enumerate(PROVIDER_KINDS, start=1):
        io.print_func(f"  {i}. {kind}")
    kind = PROVIDER_KINDS[_ask_choice_index(io, _("Provider"), len(PROVIDER_KINDS))]

    while True:
        api_key = io.getpass_func(_("API key") + ": ") if kind in REQUIRES_API_KEY_KINDS else None
        base_url = (
            _ask(io, _("Server URL"), default=DEFAULT_BASE_URLS[kind]) if kind in DEFAULT_BASE_URLS else None
        )
        provider_config = ProviderConfig(kind=kind, model="", base_url=base_url, api_key=api_key)
        try:
            provider = create_provider(provider_config)
            models = provider.list_vision_models()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, retry offered instead of a crash
            io.print_func(_("Could not reach the provider: {error}").format(error=exc))
            if not _ask_yes_no(io, _("Try again?"), default=True):
                raise _Aborted() from exc
            continue

        if not models:
            io.print_func(_("No vision model found for this provider."))
            if kind in MANUAL_MODEL_KINDS and _ask_yes_no(
                io,
                _("Manually enter a model identifier (if not auto-detected)?"),
                default=False,
            ):
                provider_config.model = _ask(io, _("Model identifier"))
                return provider_config
            if not _ask_yes_no(io, _("Try again?"), default=True):
                raise _Aborted()
            continue

        io.print_func(_("Available vision models:"))
        for i, model in enumerate(models, start=1):
            io.print_func(f"  {i}. {model.display_name} ({model.id})")
        if kind in MANUAL_MODEL_KINDS and _ask_yes_no(
            io,
            _("Manually enter a model identifier (if not auto-detected)?"),
            default=False,
        ):
            provider_config.model = _ask(io, _("Model identifier"))
            return provider_config
        model_index = _ask_choice_index(io, _("Model"), len(models))
        provider_config.model = models[model_index].id
        return provider_config


def _configure_preprocessing(io: IO) -> PreprocessOptions:
    deskew = _ask_yes_no(io, _("Enable deskew?"), default=False)
    denoise = _ask_yes_no(io, _("Enable denoise?"), default=False)
    contrast = _ask_yes_no(io, _("Enable contrast enhancement?"), default=False)
    upscale = _ask_yes_no(io, _("Enable upscaling?"), default=False)
    upscale_quality = False
    if upscale:
        upscale_quality = _ask_yes_no(
            io, _("Use quality upscaling (Real-ESRGAN if available)?"), default=False
        )
    return PreprocessOptions(
        deskew=deskew, denoise=denoise, contrast=contrast, upscale=upscale, upscale_quality=upscale_quality
    )


def _configure_resize(io: IO) -> ResizeOptions:
    disabled = _ask_yes_no(io, _("Disable adaptive resizing?"), default=False)
    max_px_override = None
    if _ask_yes_no(io, _("Override the maximum pixel dimension?"), default=False):
        max_px_override = _ask_int(io, _("Maximum pixel dimension"), default=4096)
    max_bytes_override = None
    if _ask_yes_no(io, _("Override the maximum file size in bytes?"), default=False):
        max_bytes_override = _ask_int(io, _("Maximum file size in bytes"), default=20 * 1024 * 1024)
    return ResizeOptions(
        disabled=disabled, max_px_override=max_px_override, max_bytes_override=max_bytes_override
    )


def _configure_export(io: IO) -> ExportOptions:
    io.print_func(_("Available export formats: {formats}").format(formats=", ".join(KNOWN_FORMATS)))
    raw = _ask(io, _("Export formats (comma-separated)"), default="md,json")
    formats = frozenset(f.strip() for f in raw.split(",") if f.strip() in KNOWN_FORMATS)
    single_file = _ask_yes_no(io, _("Write a combined single-file export?"), default=True)
    per_file = _ask_yes_no(io, _("Write a per-file export?"), default=True)
    return ExportOptions(formats=formats, single_file=single_file, per_file=per_file)


def _configure_system_prompt(io: IO) -> str | None:
    """New project only (spec: resume never re-asks anything already persisted).

    Returns `None` to mean "use the default" - kept explicit rather than
    resolving `DEFAULT_SYSTEM_PROMPT` here, so `ProjectConfig.system_prompt`
    round-trips through `regeste.json` the same way for CLI and GUI.
    """
    if _ask_yes_no(io, _("Use the default system prompt?"), default=True):
        return None
    return _ask(io, _("Custom system prompt"), default=DEFAULT_SYSTEM_PROMPT)


def _configure_translation_model(io: IO) -> tuple[ProviderConfig | None, bool]:
    """New project only. Returns (separate_provider_or_None, same_as_ocr).

    "Same as OCR" keeps None but flags reuse of the OCR provider at translate time.
    """
    if _ask_yes_no(io, _("Use the same model for translation?"), default=True):
        return None, True
    io.print_func(_("Available providers:"))
    for i, kind in enumerate(PROVIDER_KINDS, start=1):
        io.print_func(f"  {i}. {kind}")
    kind = PROVIDER_KINDS[_ask_choice_index(io, _("Provider"), len(PROVIDER_KINDS))]
    api_key = io.getpass_func(_("API key") + ": ") if kind in REQUIRES_API_KEY_KINDS else None
    base_url = (
        _ask(io, _("Server URL"), default=DEFAULT_BASE_URLS[kind]) if kind in DEFAULT_BASE_URLS else None
    )
    model = _pick_translation_model(io, kind, base_url, api_key)
    return ProviderConfig(kind=kind, model=model, base_url=base_url, api_key=api_key), False


def _pick_translation_model(io: IO, kind: str, base_url: str | None, api_key: str | None) -> str:
    """Best-effort model listing for the translation provider — reuses the
    provider's model list, falling back to a manually typed identifier."""
    try:
        provider = create_provider(
            ProviderConfig(kind=kind, model="", base_url=base_url, api_key=api_key)
        )
        models = provider.list_vision_models()
    except Exception as exc:  # noqa: BLE001 - offline/unreachable -> manual entry
        io.print_func(_("Could not reach the provider: {error}").format(error=exc))
        models = []
    if models:
        io.print_func(_("Available models:"))
        for i, model in enumerate(models, start=1):
            io.print_func(f"  {i}. {model.display_name} ({model.id})")
        manual = kind in MANUAL_MODEL_KINDS and _ask_yes_no(
            io, _("Manually enter a model identifier (if not auto-detected)?"), default=False
        )
        if not manual:
            return models[_ask_choice_index(io, _("Model"), len(models))].id
    return _ask(io, _("Model identifier"))


def _configure_translation_prompt(io: IO) -> str | None:
    if _ask_yes_no(io, _("Use the default translation prompt?"), default=True):
        return None
    return _ask(io, _("Custom translation prompt"), default=DEFAULT_TRANSLATION_PROMPT)


def _translate_corpus(registry: Registry, source_dir: Path, config: ProjectConfig, io: IO) -> None:
    """Optional headless translation step: translates transcribed pieces without
    a review step (the guard is bypassed on purpose). Several comma-separated
    target languages can be given and are all produced in one pass."""
    if not _ask_yes_no(io, _("Translate the transcribed pieces now?"), default=False):
        return
    effective = config.provider if config.translation_same_as_ocr else config.translation_provider
    if effective is None or not effective.model.strip():
        io.print_func(_("No translation model is configured."))
        return
    raw = _ask(io, _("Target language(s), comma-separated (e.g. en, de)"))
    targets = list(dict.fromkeys(t.strip() for t in raw.split(",") if t.strip()))
    if not targets:
        return
    io.print_func(_("Warning: translating raw OCR without human review."))
    provider = create_translation_provider(effective.kind, effective.base_url, effective.api_key)
    glossary = load_glossary(source_dir)
    ok = err = 0
    for built in build_pieces_from_registry(registry, source_dir):
        if not built.transcription.strip():
            continue
        piece = load_piece(source_dir, built.id) or built
        if not piece.language_detected:
            piece.language_detected = built.language_detected
        saved = False
        for target in targets:
            try:
                translate_piece(
                    piece,
                    target,
                    provider,
                    effective.model.strip(),
                    glossary=glossary,
                    source_language=piece.language_detected,
                    template=config.translation_prompt,
                    enforce_guard=False,
                )
                ok += 1
                saved = True
            except Exception as exc:  # noqa: BLE001 - reported per piece, run continues
                err += 1
                io.print_func(_("Translation failed: {error}").format(error=exc))
        if saved:
            save_piece(source_dir, piece)
    io.print_func(_("Translated {ok} piece(s), {error} error(s).").format(ok=ok, error=err))


def _export_archival_formats(registry: Registry, source_dir: Path, config: ProjectConfig, io: IO) -> None:
    """Optional step: export the pivot corpus to the 12 archival formats
    (EAD, Dublin Core, METS/PREMIS, CSV, XLSX, SQLite, HTML, ZIP, Markdown, PDF)."""
    if not _ask_yes_no(io, _("Export to archival formats now?"), default=False):
        return
    pieces = [
        load_piece(source_dir, built.id) or built
        for built in build_pieces_from_registry(registry, source_dir)
    ]
    if not pieces:
        io.print_func(_("No pivot data found for this project yet."))
        return
    io.print_func(
        _("Available archival formats: {formats}").format(formats=", ".join(PIVOT_EXPORTERS))
    )
    raw = _ask(io, _("Archival formats (comma-separated)"))
    keys = [k.strip() for k in raw.split(",") if k.strip() in PIVOT_EXPORTERS]
    if not keys:
        return
    target_dir = Path(_ask(io, _("Output folder"), default=str(config.output_dir)))
    target_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key in keys:
        exporter, output_name = PIVOT_EXPORTERS[key]
        output_path = target_dir / output_name
        try:
            exporter(pieces, output_path, validated_only=False)
            written.append(output_path)
        except Exception as exc:  # noqa: BLE001 - reported per format, run continues
            io.print_func(_("Export failed: {error}").format(error=exc))
    io.print_func(_("Exported files:"))
    for path in written:
        io.print_func(f"  {path}")


def _configure_new_project(source_dir: Path, io: IO) -> ProjectConfig:
    project_name = _ask(io, _("Project name"), default=source_dir.name)
    output_dir = Path(_ask(io, _("Output folder"), default=str(source_dir)))

    provider = _configure_provider(io)

    forced_language = _ask(io, _("Force document language (optional)"), default="") or None
    system_prompt = _configure_system_prompt(io)
    translation_provider, translation_same = _configure_translation_model(io)
    translation_prompt = _configure_translation_prompt(io)

    preprocessing = _configure_preprocessing(io)
    resize = _configure_resize(io)

    workers = _ask_int(io, _("Number of parallel workers"), default=4)
    spend_ceiling = _ask_float_optional(io, _("Spend ceiling in $ (optional)"))

    export_options = _configure_export(io)

    return ProjectConfig(
        project_name=project_name,
        source_dir=source_dir,
        output_dir=output_dir,
        provider=provider,
        preprocessing=preprocessing,
        resize=resize,
        forced_language=forced_language,
        system_prompt=system_prompt,
        export=export_options,
        workers=workers,
        spend_ceiling=spend_ceiling,
        translation_provider=translation_provider,
        translation_same_as_ocr=translation_same,
        translation_prompt=translation_prompt,
    )


def _print_progress(state: ProgressState, io: IO) -> None:
    if state.projection is not None:
        projected = _("~{amount}").format(amount=format_cost(state.projection.projected_cost))
    else:
        projected = _("not enough data yet")
    line = _("{file} - {processed}/{total} - cost: {cost} - projected: {projected}").format(
        file=state.file_name,
        processed=state.processed,
        total=state.total,
        cost=format_cost(state.total_cost),
        projected=projected,
    )
    io.print_func(f"\r{line}", end="")


def _print_summary(registry: Registry, cost_tracker: CostTracker, io: IO) -> None:
    ok = sum(1 for entry in registry.files.values() if entry.status == "ok")
    error = sum(1 for entry in registry.files.values() if entry.status == "error")
    total = len(registry.files)
    io.print_func(
        _("Done: {ok} ok, {error} error, {total} total - total cost: {cost}").format(
            ok=ok, error=error, total=total, cost=format_cost(cost_tracker.total_cost)
        )
    )


def _validate_resumed_provider(config: ProjectConfig, io: IO) -> bool:
    """Resume mode only: "new" mode already validated the provider implicitly via
    `_configure_provider()`'s model listing, so repeating it there would be
    redundant. Here the provider comes straight from `regeste.json` and may no
    longer work (revoked key, local server down) - better to fail clearly before
    the run than mid-run. Returns False if the user declines to retry.
    """
    while True:
        try:
            create_provider(config.provider).list_vision_models()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, retry offered instead of a crash
            io.print_func(_("Provider unavailable: {error}").format(error=exc))
            if not _ask_yes_no(io, _("Try again?"), default=True):
                return False
            continue
        return True


def _run_transcription(
    registry: Registry, mode: Literal["new", "resume"], config: ProjectConfig, io: IO
) -> None:
    provider = create_provider(config.provider)
    transcriber = Transcriber(config, provider, system_prompt=config.system_prompt)
    cost_tracker = CostTracker(rates=config.rates)

    def on_progress(state: ProgressState) -> None:
        _print_progress(state, io)

    def handle_sigint(signum, frame) -> None:
        # Calls request_stop() instead of letting KeyboardInterrupt unwind through
        # Transcriber.run(): in-flight tasks (already submitted to the thread pool)
        # must finish and be saved to the registry, which only happens if run()
        # returns normally (spec §10).
        transcriber.request_stop()
        io.print_func(_("\nStop requested - finishing in-flight tasks..."))

    previous_handler = signal.signal(signal.SIGINT, handle_sigint)
    try:
        transcriber.run(registry, mode, cost_tracker, on_progress=on_progress)
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    io.print_func("")
    _print_summary(registry, cost_tracker, io)


def run(
    *,
    input_func: Callable[[str], str] = input,
    getpass_func: Callable[[str], str] = getpass.getpass,
    print_func: Callable[..., None] = print,
) -> int:
    """Runs the full interactive flow (spec §9). Returns a process exit code."""
    io = IO(input_func=input_func, getpass_func=getpass_func, print_func=print_func)

    source_dir = _ask_source_dir(io)
    if source_dir is None:
        return 1

    registry = Registry.load(source_dir)
    mode: Literal["new", "resume"]

    if registry is not None:
        _print_registry_summary(registry, io)
        if _ask_yes_no(io, _("Resume this project?"), default=True):
            mode = "resume"
            config = ProjectConfig.from_meta(registry.meta)
            if _ask_yes_no(io, _("Modify the project settings?"), default=False):
                try:
                    config = _configure_new_project(source_dir, io)
                    registry.meta = config.to_meta()
                except _Aborted:
                    io.print_func(_("Aborted."))
            _sync_new_files(registry, source_dir)
            registry.save()
        else:
            if not _ask_yes_no(
                io, _("This will erase existing progress - confirm?"), default=False
            ):
                io.print_func(_("Aborted."))
                return 0
            try:
                config = _configure_new_project(source_dir, io)
            except _Aborted:
                io.print_func(_("Aborted."))
                return 0
            mode = "new"
            registry = Registry.new(
                source_dir, meta=config.to_meta(), file_names=_list_images(source_dir)
            )
    else:
        try:
            config = _configure_new_project(source_dir, io)
        except _Aborted:
            io.print_func(_("Aborted."))
            return 0
        mode = "new"
        registry = Registry.new(source_dir, meta=config.to_meta(), file_names=_list_images(source_dir))

    file_list = registry.files_to_process(mode)
    if file_list:
        estimate_tracker = CostTracker(rates=config.rates)
        # Heuristic only (spec §6): proxies an "average" file as 1500 input / 500 output
        # tokens, not a measurement - real costs are shown live once the run starts.
        average_cost = estimate_tracker.file_cost(config.provider.model, 1500, 500)
        estimate = estimate_before_run(len(file_list), average_cost)
        io.print_func(_("Files to process: {count}").format(count=len(file_list)))
        io.print_func(
            _("Rough cost estimate (heuristic, not a measurement): ~{amount}").format(
                amount=format_cost(estimate)
            )
        )
        if _ask_yes_no(io, _("Start now?"), default=True):
            if mode == "resume" and not _validate_resumed_provider(config, io):
                io.print_func(_("Aborted."))
                return 0
            _run_transcription(registry, mode, config, io)
        else:
            io.print_func(_("Run cancelled."))
    else:
        io.print_func(_("Nothing to process."))

    written = export_registry(
        registry,
        source_dir=source_dir,
        output_dir=config.output_dir,
        project_name=config.project_name,
        options=config.export,
    )
    io.print_func(_("Exported files:"))
    for path in written:
        io.print_func(f"  {path}")

    _translate_corpus(registry, source_dir, config, io)
    _export_archival_formats(registry, source_dir, config, io)

    return 0
