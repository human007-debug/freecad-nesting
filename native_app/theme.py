"""
theme.py
--------
Light/dark theming for the native app, built from the user-supplied
`alphanest-icon-pack/` (icons + brand palette: dark red accent #E8352E,
dark bg #14161A/#1E2127, light bg #EEF0F3).

Pieces:
  - icon_path(name, dark): looks up a themed action icon from the pack, or
    None if that action has no pack icon (the caller falls back to a
    hand-drawn glyph -- see ribbon.py).
  - logo_path(dark) / mark_path() / logo_pixmap(dark, height): the
    "alphanest" wordmark (swaps per theme, shown in the ribbon) and the
    plain mark (theme-independent -- used as the window/taskbar icon).
  - apply_theme(app, dark): sets an app-wide QSS stylesheet so toggling
    theme changes the whole app's look, not just icons -- a dark ribbon
    icon sitting on an otherwise-still-light ribbon strip would look
    broken.
"""

import os

from PySide6 import QtCore, QtGui

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_PACK_DIR = os.path.join(_PROJECT_ROOT, "alphanest-icon-pack", "icons")
_LOGO_DIR = os.path.join(_PROJECT_ROOT, "alphanest-icon-pack", "logo")
_LOGO_ASPECT = 440 / 160  # logo-{dark,light}-mode.svg's own viewBox ratio


def icon_path(name, dark):
    if name is None:
        return None
    subdir = "dark" if dark else "light"
    path = os.path.join(_ICON_PACK_DIR, subdir, f"icon-{name}.svg")
    return path if os.path.isfile(path) else None


def logo_path(dark):
    name = "logo-dark-mode.svg" if dark else "logo-light-mode.svg"
    path = os.path.join(_LOGO_DIR, name)
    return path if os.path.isfile(path) else None


def mark_path():
    path = os.path.join(_LOGO_DIR, "mark-only.svg")
    return path if os.path.isfile(path) else None


def logo_pixmap(dark, height=40):
    """Renders the wordmark logo at a fixed height, preserving its own
    aspect ratio -- for the ribbon's brand corner. None if the pack has no
    logo (caller just shows nothing)."""
    path = logo_path(dark)
    if not path:
        return None
    width = round(height * _LOGO_ASPECT)
    return QtGui.QIcon(path).pixmap(QtCore.QSize(width, height))


def _qss(bg, bg_panel, border, text, text_dim, accent):
    return f"""
    QMainWindow, QWidget {{
        background: {bg};
        color: {text};
    }}
    QMenuBar {{
        background: {bg_panel};
        color: {text};
        border-bottom: 1px solid {border};
    }}
    QMenuBar::item:selected {{
        background: {accent};
        color: white;
    }}
    QMenu {{
        background: {bg_panel};
        color: {text};
        border: 1px solid {border};
    }}
    QMenu::item:selected {{
        background: {accent};
        color: white;
    }}
    QTabWidget::pane {{
        border: 1px solid {border};
        background: {bg};
    }}
    QTabBar::tab {{
        background: {bg_panel};
        color: {text_dim};
        padding: 5px 14px;
        border: 1px solid {border};
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: {bg};
        color: {text};
        font-weight: 600;
    }}
    QGroupBox {{
        border: 1px solid {border};
        border-radius: 4px;
        margin-top: 8px;
        padding-top: 6px;
        color: {text};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        color: {text_dim};
    }}
    QFrame#RibbonFlatBox {{
        border: 1px solid {border};
        border-radius: 4px;
    }}
    QFrame#JobReadiness {{
        background: {bg_panel};
        border: 1px solid {border};
        border-left: 4px solid {accent};
        border-radius: 5px;
    }}
    QLabel#JobReadinessTitle {{
        color: {text};
    }}
    QLabel#JobReadinessDetail {{
        color: {text_dim};
    }}
    QTableWidget {{
        background: {bg};
        color: {text};
        gridline-color: {border};
        border: 1px solid {border};
        selection-background-color: {accent};
        selection-color: white;
    }}
    QHeaderView::section {{
        background: {bg_panel};
        color: {text_dim};
        border: 1px solid {border};
        padding: 3px;
    }}
    QPushButton {{
        background: {bg_panel};
        color: {text};
        border: 1px solid {border};
        border-radius: 3px;
        padding: 4px 10px;
    }}
    QPushButton:hover {{
        background: {accent};
        color: white;
    }}
    QPushButton#PrimaryAction {{
        background: {accent};
        color: white;
        font-weight: 600;
        padding-left: 14px;
        padding-right: 14px;
    }}
    QPushButton#PrimaryAction:hover {{
        background: #c92d27;
    }}
    QPushButton#DestructiveAction {{
        border-color: #c65a55;
    }}
    QPushButton:disabled {{
        color: {text_dim};
    }}
    QToolButton {{
        color: {text};
        border: none;
        border-radius: 4px;
    }}
    QToolButton:hover {{
        background: {border};
    }}
    QToolButton:checked {{
        background: {accent};
        color: white;
    }}
    QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {bg_panel};
        color: {text};
        border: 1px solid {border};
        border-radius: 3px;
    }}
    QCheckBox {{
        color: {text};
    }}
    QLabel {{
        color: {text};
    }}
    QScrollArea {{
        background: {bg};
        border: none;
    }}
    #Ribbon {{
        background: {bg_panel};
        border-bottom: 1px solid {border};
    }}
    QFrame#RibbonSeparator {{
        border: none;
        background: {border};
        max-width: 1px;
    }}
    """


LIGHT_QSS = _qss(
    bg="#ffffff", bg_panel="#eef0f2", border="#d2d6db",
    text="#1b1d22", text_dim="#5c6b78", accent="#E8352E",
)

DARK_QSS = _qss(
    bg="#1E2127", bg_panel="#14161A", border="#3A4048",
    text="#EDEFF2", text_dim="#9aa4ad", accent="#E8352E",
)


def apply_theme(app, dark):
    app.setStyleSheet(DARK_QSS if dark else LIGHT_QSS)
