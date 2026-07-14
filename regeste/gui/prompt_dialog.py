"""Reusable prompt editor dialog — free edit, reset-to-default, save.

Shared by the OCR system prompt and the translation prompt. Optionally shows a
non-blocking warning banner when required placeholders are removed from the text
(used by translation, where deleting {glossaire}/{entites_a_preserver} silently
disables glossary and named-entity injection).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from regeste.i18n import _


class PromptEditDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str,
        current_text: str,
        default_text: str,
        warn_placeholders: list[str] | None = None,
        warning_message: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)
        self._default_text = default_text
        self._warn_placeholders = warn_placeholders or []
        self._warning_message = warning_message

        layout = QVBoxLayout(self)

        self.editor = QPlainTextEdit(current_text)
        layout.addWidget(self.editor)

        self.warning_label = QLabel(warning_message)
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #b26a00;")
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)

        self.reset_button = QPushButton(_("Reset to default"))
        self.reset_button.clicked.connect(self._on_reset)
        layout.addWidget(self.reset_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.editor.textChanged.connect(self._update_warning)
        self._update_warning()

    def _on_reset(self) -> None:
        self.editor.setPlainText(self._default_text)

    def _update_warning(self) -> None:
        if not self._warn_placeholders:
            return
        text = self.editor.toPlainText()
        missing = [p for p in self._warn_placeholders if p not in text]
        self.warning_label.setVisible(bool(missing))

    def text(self) -> str:
        return self.editor.toPlainText()
