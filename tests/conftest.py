"""Shared pytest fixtures.

`regeste.i18n` keeps its active language in process-wide module-level globals
(`_translation` / `_current_language`, spec §11.1). `set_language()` mutates
them directly and there is no automatic reset between calls, so whichever
test last switches language (GUI "change language" tests, CLI `--lang`
tests, `tests/test_i18n.py`) leaves that language active for every test that
runs afterwards in the same pytest process. That makes assertions on
hard-coded English strings (e.g. error messages) depend on test execution
order - see `tests/test_cli.py::test_resume_provider_validation_failure_*`
and its GUI counterpart, which failed after `fr` leaked in from an earlier
test.

This autouse fixture forces English before each test, so no test's result
depends on what a previous test did to the global i18n state.

`tests/test_i18n.py` mocks `LANG`/`LC_ALL`/`locale.getlocale()` itself, via
its own `_reset_language_state()` helper called at the top of each test body
- i.e. *after* this fixture has already run - so it still fully controls
language detection for its own assertions; this fixture only guarantees a
clean, known starting point.
"""

from __future__ import annotations

import pytest

from regeste import i18n


@pytest.fixture(autouse=True)
def _reset_i18n_state(monkeypatch):
    monkeypatch.setattr(i18n, "_translation", None)
    monkeypatch.setattr(i18n, "_current_language", None)
    i18n.set_language("en")
    yield
