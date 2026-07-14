"""Single entry point for translatable strings (GUI + CLI + core) — spec §11.1.

gettext-based: catalogs live in `regeste/locale/<lang>/LC_MESSAGES/regeste.{po,mo}`,
extracted/compiled via Babel (see `babel.cfg` and `scripts/extract_translations.py`).
"""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

from babel.numbers import format_decimal

LOCALE_DIR = Path(__file__).parent / "locale"
DOMAIN = "regeste"

# Native display names, in the order the GUI language selector should show them.
LANGUAGE_NAMES: dict[str, str] = {
    "fr": "Français",
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "pt": "Português",
    "ja": "日本語",
    "zh": "中文",
    "ar": "العربية",
    "ru": "Русский",
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_NAMES)

# Languages that require a right-to-left GUI layout (spec §11.3).
RTL_LANGUAGES: frozenset[str] = frozenset({"ar"})

DEFAULT_LANGUAGE = "en"

_translation: gettext.NullTranslations | None = None
_current_language: str | None = None


def _normalize(raw: str) -> str:
    """Reduces a locale string (e.g. "fr_FR.UTF-8", "ja-JP") to its base language code."""
    return raw.split(".")[0].split("_")[0].split("-")[0].strip().lower()


def _detect_system_language() -> str:
    """Auto-detects the interface language from the OS locale (spec §11.2).

    Priority order: `LC_ALL` > `LANG` > `locale.getlocale()`. Returns a raw code
    (not yet validated against `SUPPORTED_LANGUAGES` - the caller handles fallback).
    """
    for env_var in ("LC_ALL", "LANG"):
        value = os.environ.get(env_var)
        if value:
            code = _normalize(value)
            if code:
                return code
    try:
        code, _encoding = locale.getlocale()
    except (ValueError, TypeError):
        code = None
    if code:
        return _normalize(code)
    return DEFAULT_LANGUAGE


def set_language(lang: str | None = None) -> None:
    """Loads and installs the gettext catalog for `lang`.

    `lang=None` auto-detects the system locale (`LC_ALL`/`LANG`/OS locale, in that
    order). Falls back to English if the resolved language isn't one of the 9
    supported ones, or if its catalog is missing/not yet compiled.
    """
    global _translation, _current_language
    resolved = lang or _detect_system_language()
    if resolved not in SUPPORTED_LANGUAGES:
        resolved = DEFAULT_LANGUAGE
    _translation = gettext.translation(
        DOMAIN, localedir=str(LOCALE_DIR), languages=[resolved], fallback=True
    )
    _current_language = resolved


def reset_language() -> None:
    """Clears the process-wide language/translation state.

    `set_language()` mutates the module-level `_translation`/`_current_language`
    globals (spec §11.1), so state set by one caller (GUI language switch, CLI
    `--lang`, a previous test) otherwise leaks into whatever runs next in the
    same process. Call this to force the next `get_current_language()`/`_()`
    call to re-detect the language from scratch.
    """
    global _translation, _current_language
    _translation = None
    _current_language = None


def get_current_language() -> str:
    """Returns the currently active language code, detecting it first if needed."""
    if _current_language is None:
        set_language(None)
    assert _current_language is not None
    return _current_language


def is_rtl(lang: str | None = None) -> bool:
    """Whether `lang` (or the currently active language) needs a right-to-left layout."""
    return (lang or get_current_language()) in RTL_LANGUAGES


def format_cost(amount: float) -> str:
    """Locale-aware decimal formatting for the (abstract, user-defined) cost figures
    shown live in the CLI/GUI (spec §11.3) - e.g. "1,234.56" in English, "1 234,56"
    in French. No currency symbol: `Rate` is a configurable unit, not a real ISO
    currency.
    """
    return format_decimal(amount, format="#,##0.00", locale=get_current_language())


def _(message: str) -> str:
    if _translation is None:
        set_language(None)
    assert _translation is not None
    return _translation.gettext(message)
