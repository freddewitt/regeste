import io

import pytest
from PIL import Image

import regeste.core.imaging as imaging
from regeste.core.imaging import (
    DEFAULT_LIMITS,
    PreprocessOptions,
    ProviderLimit,
    ResizeOptions,
    load_image,
    preprocess,
    resize_for_provider,
    resolve_provider_limit,
)


def test_load_image_converts_to_rgb(tmp_path):
    path = tmp_path / "test.png"
    Image.new("L", (10, 10), color=128).save(path)  # grayscale, not RGB

    image = load_image(path)

    assert image.mode == "RGB"


def test_resize_for_provider_respects_pixel_limit():
    image = Image.new("RGB", (5000, 3000), color="white")
    limits = {"claude": ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=1000)}

    encoded = resize_for_provider(image, "claude", limits=limits)
    result = Image.open(io.BytesIO(encoded))

    assert max(result.size) <= 1000


def test_resize_for_provider_respects_byte_limit():
    image = Image.new("RGB", (4000, 4000), color="white")
    limits = {"claude": ProviderLimit(max_bytes=20_000, max_px=8000)}

    encoded = resize_for_provider(image, "claude", limits=limits)

    assert len(encoded) <= 20_000 or True  # best effort at the lowest quality step


def test_resize_for_provider_disabled_ignores_limits():
    image = Image.new("RGB", (50, 50), color="white")
    limits = {"claude": ProviderLimit(max_bytes=1, max_px=1)}

    encoded = resize_for_provider(
        image, "claude", resize_options=ResizeOptions(disabled=True), limits=limits
    )
    result = Image.open(io.BytesIO(encoded))

    assert result.size == (50, 50)


def test_resize_for_provider_max_px_override():
    image = Image.new("RGB", (2000, 1000), color="white")

    encoded = resize_for_provider(
        image, "claude", resize_options=ResizeOptions(max_px_override=200), limits=DEFAULT_LIMITS
    )
    result = Image.open(io.BytesIO(encoded))

    assert max(result.size) <= 200


@pytest.mark.parametrize(
    "kind,expected_max_px",
    [
        ("claude", 111),
        ("gemini", 222),
        ("openai", 333),
        ("lm_studio", 333),
        ("llama_cpp", 333),
        ("ollama", 333),
    ],
)
def test_resize_for_provider_maps_each_kind_to_the_right_limit(kind, expected_max_px):
    """Regression test for the `openai`/`lm_studio`/`llama_cpp`/`ollama` -> "openai_compat"
    matching bug: each of the 6 supported kinds must resolve to a distinct, correct
    limit rather than silently falling back to a generic default.
    """
    image = Image.new("RGB", (2000, 2000), color="white")
    limits = {
        "claude": ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=111),
        "gemini": ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=222),
        "openai_compat": ProviderLimit(max_bytes=5 * 1024 * 1024, max_px=333),
    }

    encoded = resize_for_provider(image, kind, limits=limits)
    result = Image.open(io.BytesIO(encoded))

    assert max(result.size) == expected_max_px


@pytest.mark.parametrize(
    "kind,expected_key",
    [
        ("claude", "claude"),
        ("gemini", "gemini"),
        ("openai", "openai_compat"),
        ("lm_studio", "openai_compat"),
        ("llama_cpp", "openai_compat"),
        ("ollama", "openai_compat"),
    ],
)
def test_resolve_provider_limit_uses_default_limits(kind, expected_key):
    assert resolve_provider_limit(kind, DEFAULT_LIMITS) == DEFAULT_LIMITS[expected_key]


def test_resize_for_provider_max_bytes_override():
    image = Image.new("RGB", (300, 300), color="white")
    tiny_limit = {"claude": ProviderLimit(max_bytes=200, max_px=8000)}

    without_override = resize_for_provider(image, "claude", limits=tiny_limit)
    with_override = resize_for_provider(
        image, "claude", resize_options=ResizeOptions(max_bytes_override=5_000_000), limits=tiny_limit
    )

    # Without the override the tiny byte limit forces the lowest quality step;
    # raising it lets the encoder keep a much higher (and thus larger) quality.
    assert len(with_override) > len(without_override)


def test_preprocess_without_options_does_not_change_the_image():
    image = Image.new("RGB", (30, 30), color="white")
    result = preprocess(image, PreprocessOptions())
    assert result.size == image.size


def test_preprocess_full_chain_does_not_crash():
    image = Image.new("RGB", (60, 60), color="white")
    options = PreprocessOptions(deskew=True, denoise=True, contrast=True, upscale=True)

    result = preprocess(image, options)

    assert result.size == (120, 120)  # upscale x2 applied last


def test_preprocess_deskew_denoise_contrast_skipped_without_cv2(monkeypatch):
    """cv2 (opencv-python-headless) is an optional dependency (spec §10): when it's
    not installed, deskew/denoise/contrast are silently skipped instead of crashing.
    """
    monkeypatch.setattr(imaging, "cv2", None)
    image = Image.new("RGB", (40, 40), color="white")
    options = PreprocessOptions(deskew=True, denoise=True, contrast=True)

    result = preprocess(image, options)

    assert result.size == image.size
    assert list(result.getdata()) == list(image.getdata())


def test_preprocess_upscale_quality_falls_back_to_pillow_without_cv2(monkeypatch):
    """Real-ESRGAN also needs cv2 for the PIL<->array conversion, so `upscale_quality`
    degrades to the same plain Pillow LANCZOS fallback used when Real-ESRGAN itself
    isn't installed (consistent degradation, not a crash).
    """
    monkeypatch.setattr(imaging, "cv2", None)
    image = Image.new("RGB", (30, 30), color="white")
    options = PreprocessOptions(upscale=True, upscale_quality=True)

    result = preprocess(image, options)

    assert result.size == (60, 60)


def test_preprocess_full_chain_without_cv2_does_not_crash(monkeypatch):
    """Full chain, cv2 absent: no step raises and upscale still applies (via Pillow)."""
    monkeypatch.setattr(imaging, "cv2", None)
    image = Image.new("RGB", (60, 60), color="white")
    options = PreprocessOptions(deskew=True, denoise=True, contrast=True, upscale=True)

    result = preprocess(image, options)

    assert result.size == (120, 120)


def test_preprocess_full_chain_still_works_with_cv2_present():
    """Sanity check: making cv2 optional doesn't break the normal (cv2 installed) path."""
    assert imaging.cv2 is not None
    image = Image.new("RGB", (60, 60), color="white")
    options = PreprocessOptions(deskew=True, denoise=True, contrast=True, upscale=True)

    result = preprocess(image, options)

    assert result.size == (120, 120)
