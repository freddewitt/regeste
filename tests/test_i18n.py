"""Tests for the gettext-based i18n mechanism (spec §11) — no real OS locale relied upon."""

from __future__ import annotations

from regeste import i18n


def _reset_language_state(monkeypatch):
    """Isolates each test from whatever language a previous test/module already set."""
    monkeypatch.setattr(i18n, "_translation", None)
    monkeypatch.setattr(i18n, "_current_language", None)


def test_set_language_explicit_lang_is_used(monkeypatch):
    _reset_language_state(monkeypatch)
    i18n.set_language("fr")
    assert i18n.get_current_language() == "fr"


def test_set_language_none_respects_lc_all_over_lang(monkeypatch):
    _reset_language_state(monkeypatch)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    i18n.set_language(None)
    assert i18n.get_current_language() == "de"


def test_set_language_none_falls_back_to_lang_when_lc_all_unset(monkeypatch):
    _reset_language_state(monkeypatch)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    i18n.set_language(None)
    assert i18n.get_current_language() == "ja"


def test_set_language_none_falls_back_to_system_locale_when_no_env_vars(monkeypatch):
    _reset_language_state(monkeypatch)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("es_ES", "UTF-8"))
    i18n.set_language(None)
    assert i18n.get_current_language() == "es"


def test_unsupported_language_falls_back_to_english(monkeypatch):
    _reset_language_state(monkeypatch)
    i18n.set_language("xx")
    assert i18n.get_current_language() == "en"


def test_unsupported_detected_locale_falls_back_to_english(monkeypatch):
    _reset_language_state(monkeypatch)
    monkeypatch.setenv("LC_ALL", "xx_XX.UTF-8")
    i18n.set_language(None)
    assert i18n.get_current_language() == "en"


def test_rtl_languages_contains_only_arabic():
    assert i18n.RTL_LANGUAGES == frozenset({"ar"})
    assert "ar" in i18n.RTL_LANGUAGES
    for lang in i18n.SUPPORTED_LANGUAGES:
        if lang != "ar":
            assert lang not in i18n.RTL_LANGUAGES


def test_is_rtl_reflects_current_language(monkeypatch):
    _reset_language_state(monkeypatch)
    i18n.set_language("ar")
    assert i18n.is_rtl() is True
    i18n.set_language("fr")
    assert i18n.is_rtl() is False


def test_supported_languages_has_the_nine_spec_languages():
    assert i18n.SUPPORTED_LANGUAGES == ("fr", "en", "de", "es", "pt", "ja", "zh", "ar", "ru")
    assert set(i18n.LANGUAGE_NAMES) == set(i18n.SUPPORTED_LANGUAGES)


def test_english_catalog_is_an_identity_translation(monkeypatch):
    _reset_language_state(monkeypatch)
    i18n.set_language("en")
    assert i18n._("Settings") == "Settings"


def test_format_cost_is_locale_aware(monkeypatch):
    _reset_language_state(monkeypatch)
    i18n.set_language("fr")
    assert i18n.format_cost(1234.5) == "1 234,50"  # French uses a narrow no-break space
    i18n.set_language("en")
    assert i18n.format_cost(1234.5) == "1,234.50"
