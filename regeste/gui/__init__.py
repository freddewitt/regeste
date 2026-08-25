"""GUI facade (PySide6) — calls the core, implements no business logic (spec §1)."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from regeste.i18n import is_rtl

from .main_window import MainWindow
from .theme import apply_theme


def _fix_macos_menu_bar_name() -> None:
    """Without a signed .app bundle, macOS reads the running process's name (e.g.
    "Python") for the bold application menu instead of `CFBundleName` — this patches
    the in-memory bundle info Qt/AppKit read from, so dev runs (`Regeste.command`)
    show "Regeste" like a properly packaged build would.
    """
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
    except ImportError:
        return
    info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "Regeste"


def run() -> int:
    """Builds the `QApplication`, applies the theme, shows the main window, runs the loop."""
    _fix_macos_menu_bar_name()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Regeste")
    app.setApplicationDisplayName("Regeste")
    apply_theme(app)
    # RTL (spec §11.3): read the active language (set by `__main__.py` from --lang or
    # LANG/LC_ALL) before building the main window, so every widget lays out correctly
    # from the start rather than only after a later Settings change.
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight)
    window = MainWindow()
    window.show()
    return app.exec()
