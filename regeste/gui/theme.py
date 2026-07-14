"""Light/dark QSS applied via Fusion style (spec §8: sober, modern, no default Qt look)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

LIGHT_QSS = """
QWidget {
    background-color: #f5f5f7;
    color: #1d1d1f;
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #d0d0d5;
    border-radius: 8px;
    margin-top: 16px;
    padding: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #4a4a4f;
}
QPushButton {
    background-color: #ffffff;
    border: 1px solid #c7c7cc;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover { background-color: #e8e8ed; }
QPushButton:pressed { background-color: #d6d6db; }
QPushButton:disabled { color: #a0a0a5; }
QPushButton:default {
    background-color: #0a84ff;
    color: #ffffff;
    border: none;
}
QPushButton:default:hover { background-color: #0071e3; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    background-color: #ffffff;
    border: 1px solid #c7c7cc;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: #0a84ff;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #0a84ff;
}
QLineEdit:disabled { color: #a0a0a5; background-color: #ececef; }
QProgressBar {
    border: 1px solid #c7c7cc;
    border-radius: 6px;
    text-align: center;
    background-color: #ffffff;
    min-height: 18px;
}
QProgressBar::chunk { background-color: #0a84ff; border-radius: 5px; }
QListWidget {
    background-color: #ffffff;
    border: 1px solid #c7c7cc;
    border-radius: 6px;
}
QTabWidget::pane { border: 1px solid #c7c7cc; border-radius: 6px; top: -1px; }
QTabBar::tab {
    padding: 6px 18px;
    margin-right: 2px;
    background-color: #e8e8ed;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { background-color: #ffffff; font-weight: 600; }
QRadioButton, QCheckBox { spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #b0b0b6;
    background-color: #ffffff;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #0a84ff;
    border: 1px solid #0a84ff;
}
"""

DARK_QSS = """
QWidget {
    background-color: #1e1e1f;
    color: #e8e8ed;
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #3a3a3c;
    border-radius: 8px;
    margin-top: 16px;
    padding: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #a1a1a6;
}
QPushButton {
    background-color: #2c2c2e;
    border: 1px solid #48484a;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover { background-color: #3a3a3c; }
QPushButton:pressed { background-color: #48484a; }
QPushButton:disabled { color: #6e6e73; }
QPushButton:default {
    background-color: #0a84ff;
    color: #ffffff;
    border: none;
}
QPushButton:default:hover { background-color: #3396ff; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    background-color: #2c2c2e;
    border: 1px solid #48484a;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: #0a84ff;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #0a84ff;
}
QLineEdit:disabled { color: #6e6e73; background-color: #242426; }
QProgressBar {
    border: 1px solid #48484a;
    border-radius: 6px;
    text-align: center;
    background-color: #2c2c2e;
    min-height: 18px;
}
QProgressBar::chunk { background-color: #0a84ff; border-radius: 5px; }
QListWidget {
    background-color: #2c2c2e;
    border: 1px solid #48484a;
    border-radius: 6px;
}
QTabWidget::pane { border: 1px solid #48484a; border-radius: 6px; top: -1px; }
QTabBar::tab {
    padding: 6px 18px;
    margin-right: 2px;
    background-color: #242426;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { background-color: #2c2c2e; font-weight: 600; }
QRadioButton, QCheckBox { spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #5a5a5e;
    background-color: #2c2c2e;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #0a84ff;
    border: 1px solid #0a84ff;
}
"""


def _apply_scheme(app: QApplication, scheme: Qt.ColorScheme) -> None:
    app.setStyleSheet(DARK_QSS if scheme == Qt.ColorScheme.Dark else LIGHT_QSS)


def apply_theme(app: QApplication) -> None:
    """Applies Fusion + the QSS matching the OS color scheme, live-switching on change."""
    app.setStyle("Fusion")
    _apply_scheme(app, app.styleHints().colorScheme())
    app.styleHints().colorSchemeChanged.connect(lambda scheme: _apply_scheme(app, scheme))
