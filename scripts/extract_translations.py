#!/usr/bin/env python3
# Usage: .venv/bin/python scripts/extract_translations.py — regenerates regeste.pot from the source
# and updates every existing regeste/locale/<lang>/LC_MESSAGES/regeste.po in place (pybabel update
# merges new/changed msgid without discarding msgstr already translated); run after adding/changing
# any `_(...)` string, then hand the diff to translators and `pybabel compile` when they're done.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIR = ROOT / "regeste" / "locale"
POT_PATH = LOCALE_DIR / "regeste.pot"
DOMAIN = "regeste"
LANGUAGES = ("fr", "en", "de", "es", "pt", "ja", "zh", "ar", "ru")


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    run(
        sys.executable, "-m", "babel.messages.frontend", "extract",
        "-F", "babel.cfg", "-o", str(POT_PATH), "regeste/",
        "--project=Regeste", "--version=0.1.0", "--copyright-holder=Regeste",
    )
    for lang in LANGUAGES:
        po_path = LOCALE_DIR / lang / "LC_MESSAGES" / f"{DOMAIN}.po"
        if po_path.exists():
            run(
                sys.executable, "-m", "babel.messages.frontend", "update",
                "-i", str(POT_PATH), "-d", str(LOCALE_DIR), "-D", DOMAIN, "-l", lang,
            )
        else:
            run(
                sys.executable, "-m", "babel.messages.frontend", "init",
                "-i", str(POT_PATH), "-d", str(LOCALE_DIR), "-D", DOMAIN, "-l", lang,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
