"""Package entry point (spec §12.1): launches the GUI by default, CLI with `--cli`."""

from __future__ import annotations

import argparse

from regeste.i18n import set_language


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regeste")
    parser.add_argument("--cli", action="store_true", help="run the interactive CLI instead of the GUI")
    parser.add_argument(
        "--lang", default=None, help="interface language, overrides LANG/LC_ALL (e.g. fr, en)"
    )
    parser.add_argument(
        "--mode",
        choices=("literal", "hypotheses"),
        default=None,
        help="transcription mode (CLI only): pre-selects it for new projects, overrides it for resumed ones",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Always resolved here (even when --lang is absent): auto-detects LANG/LC_ALL
    # so the GUI can read the active language before building the main window
    # (spec §11.3, RTL at startup) instead of relying on `_()`'s lazy detection.
    set_language(args.lang)

    if not args.cli:
        from regeste.gui import run as run_gui

        return run_gui()

    from regeste.cli import run
    from regeste.core.transcription_mode import TranscriptionMode

    mode = TranscriptionMode.from_value(args.mode) if args.mode else None
    return run(transcription_mode=mode)


if __name__ == "__main__":
    raise SystemExit(main())
