# ui_utils.py
"""
Utility helpers for consistent message dialogs across the application.

Usage:
    from ui_utils import show_message, ask_confirmation, ask_text_input, show_error
"""

from typing import Optional, Tuple, Sequence
from PySide6.QtWidgets import QMessageBox, QWidget, QInputDialog
from PySide6.QtCore import Qt


def show_message(
    parent: Optional[QWidget],
    title: str,
    text: str,
    level: str = "info",
    detailed_text: Optional[str] = None,
    buttons: Optional[Sequence[QMessageBox.StandardButton]] = None,
) -> QMessageBox.StandardButton:
    """
    Show a simple message dialog and return the pressed button.

    Parameters
    - parent: optional QWidget parent
    - title: window title
    - text: main message text
    - level: 'info' | 'warning' | 'error' | 'question'
    - detailed_text: optional longer text shown in the dialog's details section
    - buttons: optional sequence of QMessageBox.StandardButton to show (defaults to Ok)

    Returns the standard button that was pressed.
    """
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)

    if detailed_text:
        msg.setDetailedText(detailed_text)

    level = (level or "info").lower()
    if level == "warning":
        msg.setIcon(QMessageBox.Warning)
    elif level == "error":
        msg.setIcon(QMessageBox.Critical)
    elif level == "question":
        msg.setIcon(QMessageBox.Question)
    else:
        msg.setIcon(QMessageBox.Information)

    if buttons:
        mask = 0
        for b in buttons:
            mask |= int(b)
        msg.setStandardButtons(QMessageBox.StandardButtons(mask))
    else:
        msg.setStandardButtons(QMessageBox.Ok)

    # Make dialog application modal so it behaves consistently across windows
    msg.setWindowModality(Qt.ApplicationModal)
    result = msg.exec()
    return QMessageBox.StandardButton(result)


def ask_confirmation(parent: Optional[QWidget], title: str, text: str, default_yes: bool = True) -> bool:
    """
    Show a Yes/No confirmation dialog. Returns True for Yes, False for No.

    default_yes controls which button is the default.
    """
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setIcon(QMessageBox.Question)
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg.setWindowModality(Qt.ApplicationModal)
    if default_yes:
        msg.setDefaultButton(QMessageBox.Yes)
    else:
        msg.setDefaultButton(QMessageBox.No)
    result = msg.exec()
    return result == QMessageBox.Yes


def ask_text_input(parent: Optional[QWidget], title: str, label: str, default: str = "") -> Tuple[bool, str]:
    """
    Show a simple text input dialog.
    Returns (ok: bool, text: str).
    """
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return bool(ok), str(text or "")


def show_error(parent: Optional[QWidget], title: str, text: str, detailed: Optional[str] = None) -> None:
    """Convenience wrapper for error dialogs."""
    show_message(parent, title, text, level="error", detailed_text=detailed)


def show_warning(parent: Optional[QWidget], title: str, text: str, detailed: Optional[str] = None) -> None:
    """Convenience wrapper for warning dialogs."""
    show_message(parent, title, text, level="warning", detailed_text=detailed)


def show_info(parent: Optional[QWidget], title: str, text: str, detailed: Optional[str] = None) -> None:
    """Convenience wrapper for informational dialogs."""
    show_message(parent, title, text, level="info", detailed_text=detailed)
