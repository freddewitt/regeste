"""Run orchestration: workers, exponential backoff, clean stop (spec §5.2, §6, §10)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

from .costs import CostTracker, Projection
from .imaging import load_image, preprocess, resize_for_provider
from .project import ProjectConfig, ProviderConfig
from .providers import ClaudeProvider, DEFAULT_BASE_URLS, GeminiProvider, OpenAICompatProvider, Provider
from .providers.base import TranscriptionResult
from .registry import Registry

MAX_ATTEMPTS = 5
INITIAL_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 60.0

# Instruction sent to the model (spec §4) — configurable per project, not an
# interface string, so not wrapped in `_()` (it addresses the model, not the user).
DEFAULT_SYSTEM_PROMPT = """\
Tu es un expert en transcription paléographique et en OCR de documents d'archives.

Contexte du document :
- Cote : {cote}
- Fonds/série : {fonds_serie}
- Période probable : {periode}
- Nature de l'écriture : {type_ecriture}

Consignes :
1. Transcris intégralement et fidèlement le texte visible sur l'image, dans l'ordre de lecture d'origine.
2. Conserve l'orthographe, la ponctuation, les majuscules et les abréviations d'origine, même fautives ou désuètes. Ne corrige, ne modernise, ne normalise jamais le texte.
3. Le document peut être manuscrit, dactylographié, ou mélanger les deux. Fonde ta lecture sur le tracé visible, pas sur ce qui te semble probable linguistiquement.
4. Le texte peut être rédigé dans une langue autre que le français, ou mélanger plusieurs langues. Transcris chaque passage dans sa langue d'origine, sans traduire.
5. Signale un mot incertain par [?] et un passage illisible par [illisible]. Ne devine jamais un mot à partir du seul contexte si le tracé ne le confirme pas visuellement.
6. Note les ratures avec {texte barré} et les ajouts/insertions avec <texte>, à leur position d'origine.
7. Si l'image ne contient pas de texte, laisse la transcription vide et fournis une description documentaire de l'image.

Réponds avec des sections markdown exactement sous cette forme, en omettant celles qui ne s'appliquent pas :

## TEXT
<transcription intégrale>

## DESCRIPTION
<description documentaire, si pertinent>

## LANGUE
<langue principale détectée du document>
"""

# Placeholders resolved from the current piece's metadata at OCR call time.
# At OCR time the archival fields (cote, fonds/série…) are not yet assigned —
# they are entered later during review — so missing values render as this marker.
OCR_PLACEHOLDER_KEYS = ("cote", "fonds_serie", "periode", "type_ecriture")
MISSING_PLACEHOLDER = "(non renseigné)"


def resolve_ocr_placeholders(prompt: str, values: dict[str, str] | None = None) -> str:
    """Replace the known OCR placeholders ({cote}, {fonds_serie}, {periode},
    {type_ecriture}) with piece metadata, missing ones -> MISSING_PLACEHOLDER.

    Only these four keys are substituted, so literal braces used as transcription
    conventions in the prompt (e.g. {texte barré}) are left untouched.
    """
    values = values or {}
    resolved = prompt
    for key in OCR_PLACEHOLDER_KEYS:
        resolved = resolved.replace("{" + key + "}", values.get(key) or MISSING_PLACEHOLDER)
    return resolved


def create_provider(config: ProviderConfig) -> Provider:
    if config.kind == "claude":
        return ClaudeProvider(api_key=config.api_key)
    if config.kind == "gemini":
        return GeminiProvider(api_key=config.api_key)
    if config.kind in ("openai", "lm_studio", "llama_cpp", "ollama"):
        base_url = config.base_url or DEFAULT_BASE_URLS[config.kind]
        return OpenAICompatProvider(base_url=base_url, api_key=config.api_key, kind=config.kind)
    raise ValueError(f"unknown provider: {config.kind!r}")


def _is_retryable(exc: Exception) -> bool:
    """Rate limit (429) or server error (5xx) => retry ; anything else is fatal."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or 500 <= status < 600
    name = type(exc).__name__.lower()
    return any(hint in name for hint in ("ratelimit", "timeout", "internalserver", "serviceunavailable"))


@dataclass
class ProgressState:
    file_name: str
    processed: int
    total: int
    total_cost: float
    projection: Projection | None


class Transcriber:
    """Runs a job over a `Registry`: calls the provider, handles retries and clean stop."""

    def __init__(
        self,
        config: ProjectConfig,
        provider: Provider,
        system_prompt: str | None = DEFAULT_SYSTEM_PROMPT,
    ):
        self.config = config
        self.provider = provider
        # `None` resolves to the default here so callers (CLI/GUI) can pass
        # `config.system_prompt` directly without duplicating this fallback themselves.
        prompt = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
        # Per-piece archival metadata isn't known at OCR time, so placeholders
        # resolve to the "missing" marker for now (mechanism kept for later).
        self.system_prompt = resolve_ocr_placeholders(prompt)
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _process_one(
        self, file_name: str
    ) -> tuple[str, TranscriptionResult | None, Exception | None]:
        if self._stop.is_set():
            return file_name, None, None  # task never launched: left as-is in the registry
        try:
            path = self.config.source_dir / file_name
            image = load_image(path)
            image = preprocess(image, self.config.preprocessing)
            image_bytes = resize_for_provider(
                image, self.config.provider.kind, resize_options=self.config.resize
            )
            result = self._call_with_retry(image_bytes)
            return file_name, result, None
        except Exception as exc:  # noqa: BLE001 - error recorded in the registry, run doesn't crash
            return file_name, None, exc

    def _call_with_retry(self, image_bytes: bytes) -> TranscriptionResult:
        delay = INITIAL_DELAY_SECONDS
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self.provider.transcribe(
                    image_bytes,
                    model=self.config.provider.model,
                    prompt=self.system_prompt,
                    forced_language=self.config.forced_language,
                )
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1 or not _is_retryable(exc):
                    raise
                time.sleep(delay)
                delay = min(delay * 2, MAX_DELAY_SECONDS)
        raise RuntimeError("unreachable")  # pragma: no cover

    def run(
        self,
        registry: Registry,
        mode: Literal["new", "resume"],
        cost_tracker: CostTracker,
        *,
        on_progress: Callable[[ProgressState], None] | None = None,
    ) -> None:
        """Process the due files, saving the registry after EVERY file (spec §10: atomic writes)."""
        file_list = registry.files_to_process(mode)
        total = len(file_list)
        names = iter(file_list)
        processed = 0
        workers = max(1, self.config.workers)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures: dict = {}

            def _submit_next() -> None:
                # Never submit after a stop has been requested: this is what guarantees
                # that not-yet-launched tasks stay not launched (spec §10).
                if self._stop.is_set():
                    return
                name = next(names, None)
                if name is not None:
                    futures[executor.submit(self._process_one, name)] = name

            for _ in range(workers):
                _submit_next()

            while futures:
                future = next(as_completed(futures))
                del futures[future]
                name, result, error = future.result()
                if result is None and error is None:
                    continue  # not launched: stop was requested before it started

                processed += 1
                if error is not None:
                    registry.record_error(name, str(error))
                else:
                    cost = cost_tracker.file_cost(result.model, result.tokens_in, result.tokens_out)
                    cost_tracker.record(cost)
                    registry.record_result(
                        name,
                        text=result.text,
                        description=result.description,
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        cost=cost,
                        model=result.model,
                        language=result.language,
                    )
                registry.save()

                if on_progress:
                    on_progress(
                        ProgressState(
                            file_name=name,
                            processed=processed,
                            total=total,
                            total_cost=cost_tracker.total_cost,
                            projection=cost_tracker.project(total),
                        )
                    )

                ceiling = self.config.spend_ceiling
                if ceiling is not None and cost_tracker.total_cost >= ceiling:
                    self.request_stop()

                _submit_next()
