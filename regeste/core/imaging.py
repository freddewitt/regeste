"""Adaptive per-provider resizing + optional preprocessing chain.

Everything happens in memory (the source file is never written to, spec §3.1).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover - optional dependency documented in pyproject
    cv2 = None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover - optional dependency documented in pyproject
    pass

# Default per-provider limits (spec §3.1) — user-adjustable.
DEFAULT_LIMITS: dict[str, "ProviderLimit"] = {}


@dataclass(frozen=True)
class ProviderLimit:
    max_bytes: int
    max_px: int


DEFAULT_LIMITS["claude"] = ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=8000)
DEFAULT_LIMITS["gemini"] = ProviderLimit(max_bytes=20 * 1024 * 1024, max_px=4096)
DEFAULT_LIMITS["openai_compat"] = ProviderLimit(max_bytes=20 * 1024 * 1024, max_px=4096)

# Maps a provider `kind` (`ProviderConfig.kind`, as passed to `resize_for_provider()`)
# to the `DEFAULT_LIMITS` key that governs it. Claude and Gemini each have their own
# row; every OpenAI-compatible backend (OpenAI itself, LM Studio, llama.cpp, Ollama -
# spec §2.2) shares the "openai_compat" row. Without this table, `"openai"`,
# `"lm_studio"`, `"llama_cpp"` and `"ollama"` never match a key in `DEFAULT_LIMITS`
# and used to silently fall back to a generic default instead of the intended
# 20 MB / 4096 px limit.
PROVIDER_LIMIT_KEYS: dict[str, str] = {
    "claude": "claude",
    "gemini": "gemini",
    "openai": "openai_compat",
    "lm_studio": "openai_compat",
    "llama_cpp": "openai_compat",
    "ollama": "openai_compat",
}


def resolve_provider_limit(
    provider: str, limits: dict[str, ProviderLimit] = DEFAULT_LIMITS
) -> ProviderLimit:
    """Resolves a provider `kind` to its `ProviderLimit`.

    Goes through `PROVIDER_LIMIT_KEYS` first so OpenAI-compatible kinds land on the
    shared "openai_compat" row instead of missing `limits` entirely.
    """
    key = PROVIDER_LIMIT_KEYS.get(provider, provider)
    return limits.get(key, ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=4096))


@dataclass(frozen=True)
class PreprocessOptions:
    """Preprocessing chain (spec §3.2) — each step independent."""

    deskew: bool = False
    denoise: bool = False
    contrast: bool = False
    upscale: bool = False
    upscale_quality: bool = False  # True => try Real-ESRGAN, else fall back to Pillow


@dataclass(frozen=True)
class ResizeOptions:
    """Manual override of the adaptive resize (spec §3.1)."""

    disabled: bool = False
    max_px_override: int | None = None
    max_bytes_override: int | None = None


def load_image(path: Path) -> Image.Image:
    """Open any supported format (JPG/PNG/TIFF/BMP/WEBP/HEIC/GIF...) and convert to RGB."""
    image = Image.open(path)
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def _pil_to_cv2(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _cv2_to_pil(array: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def _deskew(image: Image.Image) -> Image.Image:
    """Straighten tilt via the minimum bounding box of non-white pixels.

    No-op if cv2 is unavailable (optional dependency, see pyproject) — the
    step is silently skipped, same graceful-degradation style as Real-ESRGAN
    being absent for `_upscale`.
    """
    if cv2 is None:
        return image
    array = _pil_to_cv2(image)
    gray = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(thresh)
    if coords is None:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    height, width = array.shape[:2]
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        array, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return _cv2_to_pil(rotated)


def _denoise(image: Image.Image) -> Image.Image:
    """No-op if cv2 is unavailable (optional dependency, see pyproject)."""
    if cv2 is None:
        return image
    array = _pil_to_cv2(image)
    denoised = cv2.fastNlMeansDenoisingColored(array, None, 10, 10, 7, 21)
    return _cv2_to_pil(denoised)


def _contrast(image: Image.Image) -> Image.Image:
    """Adaptive binarization to improve text legibility.

    No-op if cv2 is unavailable (optional dependency, see pyproject).
    """
    if cv2 is None:
        return image
    array = _pil_to_cv2(image)
    gray = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
    binarized = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return Image.fromarray(binarized).convert("RGB")


def _upscale(image: Image.Image, *, quality: bool) -> Image.Image:
    """2x sharpening. `quality=True` tries Real-ESRGAN (optional extra), else Pillow LANCZOS.

    Upscaling mostly helps small characters — most of the OCR gain comes from
    deskew/denoise/contrast (spec §3.2), so it's never enabled by default.

    Real-ESRGAN also needs cv2 for the PIL<->array conversion, so the quality
    path is skipped (falling back to Pillow LANCZOS) if cv2 is unavailable
    (optional dependency, see pyproject).
    """
    if quality and cv2 is not None:
        try:
            from realesrgan import RealESRGANer  # type: ignore[import-not-found]

            # Minimal instantiation per the package's documented API; fine-grained
            # config (model, device) will be exposed in Advanced Settings (GUI phase).
            upsampler = RealESRGANer(scale=2)
            array = _pil_to_cv2(image)
            output, _ = upsampler.enhance(array, outscale=2)
            return _cv2_to_pil(output)
        except ImportError:
            pass  # fall back to Pillow below
    width, height = image.size
    return image.resize((width * 2, height * 2), Image.LANCZOS)


def preprocess(image: Image.Image, options: PreprocessOptions) -> Image.Image:
    """Apply the enabled preprocessing chain, in order deskew → denoise → contrast → upscale."""
    if options.deskew:
        image = _deskew(image)
    if options.denoise:
        image = _denoise(image)
    if options.contrast:
        image = _contrast(image)
    if options.upscale:
        image = _upscale(image, quality=options.upscale_quality)
    return image


def resize_for_provider(
    image: Image.Image,
    provider: str,
    *,
    resize_options: ResizeOptions = ResizeOptions(),
    limits: dict[str, ProviderLimit] = DEFAULT_LIMITS,
) -> bytes:
    """Bound dimensions (LANCZOS) then re-encode to JPEG at stepped quality levels.

    Never modifies the source image: everything happens in memory, the result
    is returned as JPEG bytes ready to send to the provider.
    """
    if resize_options.disabled:
        return _encode_jpeg(image, quality=95)

    limit = resolve_provider_limit(provider, limits)
    max_px = resize_options.max_px_override or limit.max_px
    max_bytes = resize_options.max_bytes_override or limit.max_bytes

    width, height = image.size
    if max(width, height) > max_px:
        scale = max_px / max(width, height)
        image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    for quality in (95, 85, 75, 65, 50, 35):
        encoded = _encode_jpeg(image, quality=quality)
        if len(encoded) <= max_bytes:
            return encoded
    return encoded  # last quality step, returned even if still over the limit


def _encode_jpeg(image: Image.Image, *, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
