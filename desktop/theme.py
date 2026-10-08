# theme.py
"""
Sistem de stilizare centralizat pentru aplicația Cimitir.

Oferă:
    - Palete de culori (dark & light)
    - QSS complet pentru toate widget-urile
    - Font default
    - Helper `apply_theme(app, mode)` pentru switching
"""

from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import QApplication


# =============================================================================
# PALETE
# =============================================================================
DARK = {
    "bg":            "#0f172a",  # slate-900
    "bg_panel":      "#1e293b",  # slate-800
    "bg_hover":      "#334155",  # slate-700
    "bg_input":      "#1e293b",
    "border":        "#334155",
    "border_light":  "#475569",
    "text":          "#e2e8f0",  # slate-200
    "text_muted":    "#94a3b8",  # slate-400
    "text_disabled": "#64748b",  # slate-500
    "primary":       "#0ea5e9",  # sky-500
    "primary_hover": "#0284c7",  # sky-600
    "primary_soft":  "#0c4a6e",
    "success":       "#22c55e",
    "warning":       "#f59e0b",
    "danger":        "#ef4444",
    "danger_hover":  "#dc2626",
    "accent":        "#8b5cf6",  # violet-500
}

LIGHT = {
    "bg":            "#f8fafc",
    "bg_panel":      "#ffffff",
    "bg_hover":      "#f1f5f9",
    "bg_input":      "#ffffff",
    "border":        "#e2e8f0",
    "border_light":  "#cbd5e1",
    "text":          "#0f172a",
    "text_muted":    "#64748b",
    "text_disabled": "#94a3b8",
    "primary":       "#0284c7",
    "primary_hover": "#0369a1",
    "primary_soft":  "#e0f2fe",
    "success":       "#16a34a",
    "warning":       "#d97706",
    "danger":        "#dc2626",
    "danger_hover":  "#b91c1c",
    "accent":        "#7c3aed",
}


# =============================================================================
# FONT
# =============================================================================
def default_font() -> QFont:
    f = QFont("Segoe UI", 10)
    f.setStyleHint(QFont.SansSerif)
    return f


# =============================================================================
# QSS BUILDER
# =============================================================================
def build_qss(p: dict) -> str:
    return f"""
    /* ========= GLOBAL ========= */
    * {{
        font-family: "Segoe UI", "Roboto", "Arial", sans-serif;
        font-size: 10pt;
        outline: none;
    }}

    QWidget {{
        background-color: {p['bg']};
        color: {p['text']};
    }}

    /* ========= WINDOWS / DIALOGS ========= */
    QMainWindow, QDialog {{
        background-color: {p['bg']};
    }}

    /* ========= MENUBAR ========= */
    QMenuBar {{
        background-color: {p['bg_panel']};
        border-bottom: 1px solid {p['border']};
        padding: 4px;
    }}
    QMenuBar::item {{
        padding: 6px 12px;
        background: transparent;
        border-radius: 4px;
    }}
    QMenuBar::item:selected {{
        background-color: {p['bg_hover']};
        color: {p['primary']};
    }}
    QMenu {{
        background-color: {p['bg_panel']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {p['primary']};
        color: white;
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {p['border']};
        margin: 4px 8px;
    }}

    /* ========= TOOLBAR ========= */
    QToolBar {{
        background-color: {p['bg_panel']};
        border: none;
        border-bottom: 1px solid {p['border']};
        padding: 6px;
        spacing: 4px;
    }}
    QToolButton {{
        background-color: transparent;
        color: {p['text']};
        padding: 6px 12px;
        border-radius: 6px;
        border: 1px solid transparent;
    }}
    QToolButton:hover {{
        background-color: {p['bg_hover']};
        border-color: {p['border_light']};
    }}
    QToolButton:checked {{
        background-color: {p['primary_soft']};
        color: {p['primary']};
        border-color: {p['primary']};
    }}
    QToolButton:pressed {{
        background-color: {p['primary']};
        color: white;
    }}

    /* ========= STATUSBAR ========= */
    QStatusBar {{
        background-color: {p['bg_panel']};
        color: {p['text_muted']};
        border-top: 1px solid {p['border']};
        padding: 4px 10px;
    }}

    /* ========= BUTTONS ========= */
    QPushButton {{
        background-color: {p['bg_panel']};
        color: {p['text']};
        border: 1px solid {p['border_light']};
        padding: 8px 16px;
        border-radius: 6px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p['bg_hover']};
        border-color: {p['primary']};
    }}
    QPushButton:pressed {{
        background-color: {p['primary']};
        color: white;
    }}
    QPushButton:disabled {{
        color: {p['text_disabled']};
        background-color: {p['bg']};
    }}
    QPushButton[class="primary"] {{
        background-color: {p['primary']};
        color: white;
        border: none;
        font-weight: 600;
    }}
    QPushButton[class="primary"]:hover {{
        background-color: {p['primary_hover']};
    }}
    QPushButton[class="primary"]:pressed {{
        background-color: {p['primary_soft']};
        color: {p['primary']};
    }}
    QPushButton[class="danger"] {{
        background-color: {p['danger']};
        color: white;
        border: none;
        font-weight: 600;
    }}
    QPushButton[class="danger"]:hover {{
        background-color: {p['danger_hover']};
    }}
    QPushButton[class="ghost"] {{
        background-color: transparent;
        color: {p['text_muted']};
        border: none;
    }}
    QPushButton[class="ghost"]:hover {{
        background-color: {p['bg_hover']};
        color: {p['text']};
    }}

    /* ========= LINE EDITS / SPIN / DATE / COMBO ========= */
    QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit {{
        background-color: {p['bg_input']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: {p['primary']};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus {{
        border-color: {p['primary']};
    }}
    QLineEdit:disabled {{
        background-color: {p['bg']};
        color: {p['text_disabled']};
    }}
    QLineEdit[readOnly="true"] {{
        background-color: {p['bg']};
        color: {p['text_muted']};
    }}

    QComboBox {{
        background-color: {p['bg_input']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{ border-color: {p['border_light']}; }}
    QComboBox:focus {{ border-color: {p['primary']}; }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {p['text_muted']};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p['bg_panel']};
        color: {p['text']};
        border: 1px solid {p['border']};
        selection-background-color: {p['primary']};
        selection-color: white;
        padding: 4px;
        border-radius: 6px;
    }}

    QTextEdit, QPlainTextEdit {{
        background-color: {p['bg_input']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 6px;
        selection-background-color: {p['primary']};
    }}
    QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {p['primary']}; }}

    /* ========= CHECKBOX / RADIO ========= */
    QCheckBox, QRadioButton {{
        color: {p['text']};
        spacing: 8px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {p['border_light']};
        background: {p['bg_input']};
    }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {p['primary']};
        border-color: {p['primary']};
    }}

    /* ========= GROUPBOX ========= */
    QGroupBox {{
        background-color: {p['bg_panel']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        margin-top: 14px;
        padding: 12px 10px 10px 10px;
        font-weight: 600;
        color: {p['text']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 8px;
        margin-left: 8px;
        color: {p['primary']};
        background-color: {p['bg']};
    }}

    /* ========= LABELS ========= */
    QLabel {{
        color: {p['text']};
        background: transparent;
        border: none;
    }}
    QLabel[class="muted"]  {{ color: {p['text_muted']}; }}
    QLabel[class="title"]  {{ font-size: 16pt; font-weight: 700; color: {p['text']}; }}
    QLabel[class="h2"]     {{ font-size: 13pt; font-weight: 600; color: {p['text']}; }}
    QLabel[class="hint"]   {{ color: {p['text_muted']}; font-size: 9pt; }}
    QLabel[class="badge-primary"] {{
        background-color: {p['primary_soft']};
        color: {p['primary']};
        padding: 4px 10px;
        border-radius: 10px;
        font-weight: 600;
    }}

    /* ========= TABLE / LIST / TREE ========= */
    QTableView, QListView, QTreeView {{
        background-color: {p['bg_panel']};
        alternate-background-color: {p['bg']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        gridline-color: {p['border']};
        selection-background-color: {p['primary']};
        selection-color: white;
    }}
    QHeaderView::section {{
        background-color: {p['bg_hover']};
        color: {p['text']};
        padding: 8px 10px;
        border: none;
        border-right: 1px solid {p['border']};
        border-bottom: 1px solid {p['border_light']};
        font-weight: 600;
    }}
    QHeaderView::section:hover {{ background-color: {p['border_light']}; }}

    /* ========= SCROLLBARS ========= */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border_light']};
        border-radius: 6px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p['text_muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {p['border_light']};
        border-radius: 6px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {p['text_muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ========= TABS ========= */
    QTabWidget::pane {{
        border: 1px solid {p['border']};
        border-radius: 6px;
        background: {p['bg_panel']};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {p['text_muted']};
        padding: 8px 18px;
        border: none;
        border-bottom: 2px solid transparent;
        margin-right: 4px;
    }}
    QTabBar::tab:hover {{ color: {p['text']}; }}
    QTabBar::tab:selected {{
        color: {p['primary']};
        border-bottom-color: {p['primary']};
        font-weight: 600;
    }}

    /* ========= SPLITTER ========= */
    QSplitter::handle {{
        background-color: {p['border']};
    }}
    QSplitter::handle:hover {{ background-color: {p['primary']}; }}

    /* ========= DOCK ========= */
    QDockWidget {{
        color: {p['text']};
    }}
    QDockWidget::title {{
        background-color: {p['bg_panel']};
        padding: 6px;
        border-bottom: 1px solid {p['border']};
    }}

    /* ========= PROGRESS ========= */
    QProgressBar {{
        background-color: {p['bg_panel']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        text-align: center;
        color: {p['text']};
    }}
    QProgressBar::chunk {{
        background-color: {p['primary']};
        border-radius: 5px;
    }}

    /* ========= TOOLTIP ========= */
    QToolTip {{
        background-color: {p['bg_hover']};
        color: {p['text']};
        border: 1px solid {p['border_light']};
        padding: 6px 10px;
        border-radius: 4px;
    }}

    /* ========= CUSTOM: SIDEBAR NAV ========= */
    QListWidget#sidebar {{
        background-color: {p['bg_panel']};
        color: {p['text']};
        border: none;
        border-right: 1px solid {p['border']};
        padding: 8px;
        font-size: 11pt;
    }}
    QListWidget#sidebar::item {{
        padding: 12px 16px;
        margin: 2px 0;
        border-radius: 6px;
    }}
    QListWidget#sidebar::item:hover {{
        background-color: {p['bg_hover']};
    }}
    QListWidget#sidebar::item:selected {{
        background-color: {p['primary_soft']};
        color: {p['primary']};
        border-left: 3px solid {p['primary']};
    }}

    /* ========= CUSTOM: CARD ========= */
    QFrame[class="card"] {{
        background-color: {p['bg_panel']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}
    QFrame[class="card-hover"]:hover {{
        border-color: {p['primary']};
    }}
    """


# =============================================================================
# APPLY
# =============================================================================
def app_icon():
    """Iconița aplicației (icon.ico de lângă surse sau din pachetul .exe)."""
    import os
    import sys

    from PySide6.QtGui import QIcon

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(base, "icon.ico"),
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")):
        if os.path.exists(candidate):
            return QIcon(candidate)
    return QIcon()


def apply_theme(app: QApplication, mode: str = "dark") -> None:
    palette = DARK if mode == "dark" else LIGHT
    app.setFont(default_font())
    app.setStyleSheet(build_qss(palette))
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)


def get_palette(mode: str = "dark") -> dict:
    return DARK if mode == "dark" else LIGHT
