"""
theme.py
--------
The native app's design system: one palette, one type ramp, one set of
shapes, applied app-wide as a QSS stylesheet.

Design direction (the brief: "Autodesk Fusion, and Apple in general"):

  * **Quiet chrome, loud content.** The window is a neutral surface; the
    only saturated color in it is the accent, and only on the one
    primary action and on things that genuinely need attention. Fusion's
    toolbars are grey precisely so the model isn't competing with them.
  * **Hairlines, not boxes.** Structure comes from a 1px separator and a
    change of surface, not from a border around every control. The old
    sheet had a 1px box around each group, each table, each header cell,
    each input -- a grid of boxes reads as a 2005 Win32 form.
  * **Neutral interaction states.** Hover is a faint neutral wash; only
    pressing/selecting reaches for color. (Before this, EVERY button in
    the app turned accent-colored on hover, which made the whole UI feel like
    an alarm panel and destroyed the primary button's meaning.)
  * **Room to breathe.** Bigger touch targets, 8px-grid padding, 30px
    table rows, and a real type ramp -- 12px captions, 13px body, 15/20px
    titles -- instead of one undifferentiated 11px.
  * **Soft, consistent geometry.** 6px radius on controls, 8px on cards,
    10px on the primary action; nothing square, nothing pill-shaped.

Pieces:
  - `tokens(dark)` / `color(name)`: the palette itself, so painted widgets
    (the sheet preview, the Parts tab canvas, status text) use the same
    colors the stylesheet does instead of hardcoding hexes.
  - `icon_path(name, dark)`: a themed action icon from the user-supplied
    `alphanest-icon-pack/`, or None (the caller falls back to a hand-drawn
    glyph -- see ribbon.py).
  - `mark_path()`: the app's own alpha mark, for the window/taskbar icon.
  - `apply_theme(app, dark)`: builds the QSS for that palette and sets it
    app-wide, and pushes the same palette into `nesting_widgets`' sheet
    colors so the canvas is themed too.

`assets/` holds the handful of control glyphs QSS needs as images (the
checkbox tick, the combo/spin chevrons) -- Qt can't draw a checkmark on a
restyled indicator by itself.
"""

import os
import re
from string import Template

from PySide6 import QtCore, QtGui

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_PACK_DIR = os.path.join(_PROJECT_ROOT, "alphanest-icon-pack", "icons")
_LOGO_DIR = os.path.join(_PROJECT_ROOT, "alphanest-icon-pack", "logo")
_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


# --------------------------------------------------------------- palette

# The accent is Apple's own -- #FC4269, sampled from the tab bar in
# `applereference.jpg` (Apple Music's pink-red), at the user's request that
# the app use "the same accent color Apple uses". It is lifted in dark mode
# the way Apple lifts its system colors there, so it doesn't sink into the
# grey. It replaces the icon pack's #E8352E everywhere, including inside
# the pack's own icons -- see `icon_svg()`, which recolors them on load
# rather than editing the user's files.
#
# Surfaces come in three depths, which is what replaces the old borders:
#   bg       -- the window and the workspace behind content
#   surface  -- content that sits ON the window: tables, inputs, cards
#   raised   -- chrome that sits ABOVE it: toolbar, headers, menus
LIGHT = {
    "bg": "#F4F5F7",
    "surface": "#FFFFFF",
    "raised": "#FAFBFC",
    "sunken": "#EDEFF2",
    "border": "#E2E5E9",
    "border_strong": "#C8CDD4",
    "text": "#15171B",
    "text_dim": "#646B75",
    "text_faint": "#949BA4",
    "accent": "#FC4269",
    "accent_hover": "#FD5C7E",
    "accent_press": "#E02B52",
    "accent_soft": "#FFEBF0",
    "on_accent": "#FFFFFF",
    "hover": "#ECEEF1",
    "pressed": "#E1E4E9",
    "selection": "#FFE4EB",
    "success": "#1C7C45",
    "on_success": "#FFFFFF",
    "success_soft": "#E4F3EA",
    "warning": "#9A6600",
    "danger": "#C0342C",
    # Canvas colors -- the sheet preview and the Parts tab's part render.
    "canvas": "#FFFFFF",
    "sheet": "#EFF1F4",
    "sheet_edge": "#B7BFC9",
    "grid": "#DCE0E6",
    "ink": "#2B2F36",
    "chevron": "chevron-light.svg",
    "spin_up": "up-light.svg",
    "spin_down": "down-light.svg",
}
DARK = {
    "bg": "#181A1E",
    "surface": "#22252A",
    "raised": "#1E2126",
    "sunken": "#141619",
    "border": "#2F333A",
    "border_strong": "#424852",
    "text": "#E8EAED",
    "text_dim": "#9BA3AE",
    "text_faint": "#767E89",
    "accent": "#FF5C7E",
    "accent_hover": "#FF7695",
    "accent_press": "#E8456A",
    "accent_soft": "#35222A",
    "on_accent": "#FFFFFF",
    "hover": "#2A2E35",
    "pressed": "#33383F",
    "selection": "#3C2530",
    "success": "#5CC98B",
    "on_success": "#10251A",
    "success_soft": "#1B2A22",
    "warning": "#E0A33A",
    "danger": "#F0716A",
    "canvas": "#1E2127",
    "sheet": "#2A2E35",
    "sheet_edge": "#464C56",
    "grid": "#363B43",
    "ink": "#E8EAED",
    "chevron": "chevron-dark.svg",
    "spin_up": "up-dark.svg",
    "spin_down": "down-dark.svg",
}

# The cutting canvas (the live sheet view and its Layout Results
# thumbnails): LibreCAD's drawing area in BOTH themes -- black ground, a
# grey dot grid with a darker dashed 10x "meta" grid and a white sheet
# outline -- with each part shaded in its own color on top, the way
# LaserNest shades a nest. The grid/meta-grid/origin/measure colors are
# LibreCAD's own defaults. `part` only switches the canvas into this CAD
# drawing; the parts themselves take their per-part colors.
# Pushed into nesting_widgets.set_canvas_palette() by apply_theme(); the
# report's sheet images keep the theme colors.
CANVAS = {
    "workspace": "#000000",
    "sheet": "#000000",
    "sheet_edge": "#FFFFFF",
    "grid": "#808080",
    "meta_grid": "#404040",
    "part": "#FFFFFF",
    "origin": "#FF0000",
    "measure": "#FFC200",
    "marker": "#00FFFF",
    "hint": "#808080",
    "ruler_bg": "#000000",
    "ruler_ink": "#A0A0A0",
    "ruler_tick": "#606060",
    "caption": "#FFFF00",
}
# The last theme apply_theme() was asked for. Qt's palette stays untouched
# (the theme is pure QSS), so painted widgets -- the sheet preview, the
# Parts tab's part canvas -- query these instead of the palette to pick
# their own colors. Without it a canvas would stay a bright panel in dark
# mode.
_CURRENT_DARK = False


def is_dark():
    return _CURRENT_DARK


def tokens(dark=None):
    """The whole palette for a theme (defaults to the active one)."""
    if dark is None:
        dark = _CURRENT_DARK
    return DARK if dark else LIGHT


def color(name, dark=None):
    """One palette entry as a QColor -- for painted widgets."""
    return QtGui.QColor(tokens(dark)[name])


def canvas_color():
    """The paper color painted UI draws its own backgrounds on -- matches
    the theme's QSS surfaces, so a painted canvas sits inside the themed
    window instead of floating as a fixed light rectangle."""
    return color("canvas")


# ----------------------------------------------------------------- icons

def icon_path(name, dark):
    if name is None:
        return None
    subdir = "dark" if dark else "light"
    path = os.path.join(_ICON_PACK_DIR, subdir, f"icon-{name}.svg")
    return path if os.path.isfile(path) else None


# What the pack's SVGs are built from (see any file in
# `alphanest-icon-pack/icons/`): a rounded tile, dark or light "ink" for
# the line work, one accent detail, and a muted secondary tone.
_PACK_TILE = {"#EEF0F3", "#14161A"}
_PACK_INK = {"#1B1D22", "#EDEFF2"}
_PACK_MUTED = {"#B7BCC4", "#3A4048"}
_PACK_ACCENT = "#E8352E"
_PACK_TILE_RE = re.compile(r'<rect[^>]*width="96"[^>]*height="96"[^>]*/>')


def icon_svg(name, dark, mono=None):
    """The pack icon for `name` as themed SVG source, or None. With
    `mono`, every part of it is drawn in that one color instead.

    The pack's icons are little tiles: a rounded rectangle filled with the
    old palette's background, with line work and one #E8352E detail on
    top. Dropped straight into this toolbar they read as a grid of chips
    in somebody else's colors -- most obviously the hand-drawn fallback
    tile, a blue gradient square that had nothing to do with the app at
    all. So the tile is removed and every color is mapped onto a theme
    token as the file is loaded: the icons become line glyphs in the
    current theme, accent and all. The files on disk are never touched --
    they are the user's own brand assets, and a pack updated tomorrow
    still works."""
    path = icon_path(name, dark)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return None
    palette = tokens(dark)
    ink = mono or palette["text"]
    muted = mono or palette["text_faint"]
    accent = mono or palette["accent"]
    svg = _PACK_TILE_RE.sub("", svg, count=1)
    for color in _PACK_TILE:
        svg = svg.replace(color, "none")
    for color in _PACK_INK:
        svg = svg.replace(color, ink)
    for color in _PACK_MUTED:
        svg = svg.replace(color, muted)
    return svg.replace(_PACK_ACCENT, accent)


def asset(name):
    """A QSS-safe absolute URL for one of this package's control glyphs."""
    return os.path.join(_ASSET_DIR, name).replace("\\", "/")


def mark_path():
    """The app's mark, for the window and taskbar icon: this package's own
    alpha badge (`assets/alpha-badge.svg` -- a geometric alpha on an accent
    squircle, drawn as paths). The icon pack's `mark-only.svg` is the
    fallback; it sets the letter in Georgia via an SVG `<text>` element, so
    it renders differently on every machine and not at all where that font
    is missing."""
    for path in (asset("alpha-badge.svg"), os.path.join(_LOGO_DIR, "mark-only.svg")):
        if os.path.isfile(path):
            return path
    return None


# ------------------------------------------------------------ stylesheet

# One font stack for the whole app: the platform's own UI face first (San
# Francisco on macOS, Segoe on Windows, Inter/Noto on Linux) so the app
# reads as native rather than as a Qt application wearing a costume.
FONT_STACK = '"Inter", "SF Pro Text", "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif'

_QSS = Template("""
/* ---------------------------------------------------------- foundation */
QWidget {
    background: $bg;
    color: $text;
    font-family: $font;
    font-size: 13px;
}
QMainWindow, QDialog { background: $bg; }
QWidget:disabled { color: $text_faint; }
/* Widgets that are LABELS on a surface, not surfaces themselves. Without
   this they inherit the window background above and show up as grey
   rectangles wherever they sit on a card -- every caption inside a group
   box was drawing its own little box. */
QLabel, QCheckBox, QRadioButton, QSplitter, QTabWidget, QTabBar {
    background: transparent;
}
QToolTip {
    background: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 8px;
}

/* -------------------------------------------------------------- menubar */
QMenuBar {
    background: $raised;
    color: $text;
    border-bottom: 1px solid $border;
    padding: 2px 4px;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
    border-radius: 6px;
    margin: 1px 1px;
}
QMenuBar::item:selected { background: $hover; }
QMenuBar::item:pressed { background: $pressed; }
QMenu {
    background: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 28px 6px 12px;
    border-radius: 5px;
}
QMenu::item:selected { background: $accent; color: $on_accent; }
QMenu::item:disabled { color: $text_faint; }
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 5px 8px;
}

/* ----------------------------------------------------------------- tabs */
/* A segmented control, the way Apple's tab bars work: one rounded track
   holding the tabs, and the selected one marked by a filled pill that
   floats inside it -- not by a raised 3D box, and not by an underline.
   The track hugs its tabs because QTabBar is only as wide as they are. */
QTabWidget::pane {
    border: none;
    border-top: 1px solid $border;
    background: $bg;
}
/* The main tab strip is `native_app/segmented.py` -- these two rules are
   its shape. (A QTabBar honors neither padding nor margins from a
   stylesheet, which is exactly what this shape is made of.) */
QFrame#SegmentedTrack {
    background: $sunken;
    border: 1px solid $border;
    border-radius: 15px;
}
QToolButton#SegmentedTab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 4px 16px;
    color: $text_dim;
    font-size: 13px;
    font-weight: 500;
}
QToolButton#SegmentedTab:hover { color: $text; }
QToolButton#SegmentedTab:checked {
    background: $surface;
    border-color: $border;
    color: $accent;
    font-weight: 600;
}

/* Any OTHER tab widget in the app (none today) still gets a sane look
   rather than Qt's raised 3D tabs. */
QTabBar::tab {
    background: transparent;
    color: $text_dim;
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $accent; }

/* --------------------------------------------------------------- cards */
QGroupBox {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-size: 13px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0 2px 4px 2px;
    color: $text_dim;
    font-size: 12px;
    font-weight: 600;
}
QFrame#RibbonFlatBox {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
QFrame#JobReadiness {
    background: $surface;
    border: 1px solid $border;
    border-left: 3px solid $accent;
    border-radius: 8px;
}
QLabel#JobReadinessTitle { color: $text; font-size: 15px; font-weight: 600; }
QLabel#JobReadinessDetail { color: $text_dim; }
/* Separators: a hairline, never a sunken 3D groove. (4 = QFrame.HLine,
   5 = QFrame.VLine -- Qt matches enum properties by their numeric value.) */
QFrame[frameShape="4"] {
    background: $border;
    border: none;
    max-height: 1px;
}
QFrame[frameShape="5"] {
    background: $border;
    border: none;
    max-width: 1px;
}
QFrame#RibbonSeparator {
    background: $border;
    border: none;
    max-width: 1px;
    margin: 4px 2px;
}

/* ------------------------------------------------------------- buttons */
QPushButton {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    min-height: 18px;
}
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $pressed; }
QPushButton:focus { border-color: $accent; }
/* A latched toggle (Manual adjustment, Layout results) is ON, not
   alarming: tinted and outlined in the accent, but the label stays the
   normal text color so it doesn't read as an error state. */
QPushButton:checked {
    background: $accent_soft;
    border-color: $accent;
    color: $text;
}
QPushButton:disabled {
    background: $bg;
    color: $text_faint;
    border-color: $border;
}
QPushButton#PrimaryAction {
    background: $accent;
    color: $on_accent;
    border: 1px solid $accent;
    font-weight: 600;
    padding: 6px 18px;
}
QPushButton#PrimaryAction:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#PrimaryAction:pressed { background: $accent_press; border-color: $accent_press; }
QPushButton#PrimaryAction:disabled {
    background: $sunken;
    border-color: $border;
    color: $text_faint;
}
QPushButton#SuccessAction {
    background: $success;
    color: $on_success;
    border: 1px solid $success;
    font-weight: 600;
}
QPushButton#DestructiveAction { color: $danger; border-color: $border_strong; }
QPushButton#DestructiveAction:hover { background: $accent_soft; border-color: $danger; }
QPushButton:flat { border: none; background: transparent; }
/* The in-row remove affordance on the parts table: invisible until the
   row is hovered, and never wide enough to look like a real button. */
QPushButton#RowDelete {
    background: transparent;
    border: none;
    color: $text_faint;
    padding: 0;
    font-size: 13px;
    border-radius: 6px;
}
QPushButton#RowDelete:hover { background: $accent_soft; color: $danger; }
/* Sheet paging chevrons. */
QPushButton#NavButton {
    padding: 4px 0;
    font-size: 16px;
    color: $text_dim;
}
QPushButton#NavButton:hover { color: $text; }

QToolButton {
    background: transparent;
    color: $text;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 8px;
}
QToolButton:hover { background: $hover; }
QToolButton:pressed { background: $pressed; }
QToolButton:checked { background: $accent_soft; border-color: $accent; color: $text; }
QToolButton:disabled { color: $text_faint; }
/* The ribbon's one primary action: the accent, and the only filled button
   in the toolbar. Disabled it goes inert grey rather than washed-out red,
   so a locked gate reads as "not yet" and not as a broken button. */
QToolButton#PrimaryRibbonAction {
    background: $accent;
    color: $on_accent;
    border: 1px solid $accent;
    border-radius: 7px;
    padding: 3px 10px 2px 10px;
    font-size: 12px;
    font-weight: 600;
}
QToolButton#PrimaryRibbonAction:hover { background: $accent_hover; border-color: $accent_hover; }
QToolButton#PrimaryRibbonAction:pressed { background: $accent_press; border-color: $accent_press; }
QToolButton#PrimaryRibbonAction:disabled {
    background: $sunken;
    border-color: $border;
    color: $text_faint;
}

/* --------------------------------------------------------------- input */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border-color: $text_faint;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: $accent;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: $bg;
    color: $text_faint;
}
QPlainTextEdit, QTextEdit { padding: 6px 8px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url($chevron); width: 16px; height: 16px; }
QComboBox QAbstractItemView {
    background: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: transparent;
    border: none;
    width: 18px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url($spin_up); width: 12px; height: 12px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url($spin_down); width: 12px; height: 12px;
}

QCheckBox, QRadioButton { color: $text; spacing: 8px; padding: 2px 0; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_strong;
    background: $surface;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked {
    background: $accent;
    border-color: $accent;
    image: url($check);
}
QCheckBox::indicator:indeterminate {
    background: $accent;
    border-color: $accent;
    image: url($dash);
}
QRadioButton::indicator:checked { background: $accent; border: 4px solid $surface; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background: $sunken;
    border-color: $border;
}
/* A checkbox living INSIDE a table cell is drawn by the view, not by a
   QCheckBox, so it needs the same treatment spelled out again. */
QAbstractItemView::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid $border_strong;
    border-radius: 4px;
    background: $surface;
}
QAbstractItemView::indicator:checked {
    background: $accent;
    border-color: $accent;
    image: url($check);
}

/* -------------------------------------------------------------- tables */
/* No vertical rules, no box: rows are separated by one hairline and the
   header by one more. A spreadsheet's grid belongs in a spreadsheet. */
QTableView, QTableWidget {
    background: $surface;
    alternate-background-color: $raised;
    color: $text;
    gridline-color: transparent;
    border: 1px solid $border;
    border-radius: 8px;
    selection-background-color: $selection;
    selection-color: $text;
    outline: none;
}
QTableView::item, QTableWidget::item {
    border: none;
    border-bottom: 1px solid $border;
    padding: 4px 8px;
}
QTableView::item:selected, QTableWidget::item:selected {
    background: $selection;
    color: $text;
}
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $raised;
    color: $text_dim;
    border: none;
    border-bottom: 1px solid $border;
    padding: 8px 8px;
    font-size: 12px;
    font-weight: 600;
}
QHeaderView::section:horizontal:hover { color: $text; }
QTableCornerButton::section { background: $raised; border: none; }

/* --------------------------------------------------------------- lists */
QListWidget, QListView, QTreeView {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}
QListWidget::item, QListView::item, QTreeView::item {
    border-radius: 6px;
    padding: 4px;
    margin: 1px 0;
}
QListWidget::item:hover, QTreeView::item:hover { background: $hover; }
QListWidget::item:selected, QListView::item:selected, QTreeView::item:selected {
    background: $selection;
    color: $text;
}

/* ---------------------------------------------------------- containers */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: $border; }
QStatusBar {
    background: $raised;
    color: $text_dim;
    border-top: 1px solid $border;
}
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $text_dim; }
QProgressBar {
    background: $sunken;
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: $text_dim;
}
QProgressBar::chunk { background: $accent; border-radius: 4px; }

/* ---------------------------------------------------------- scrollbars */
/* Slim, trackless, rounded -- the thumb is the only thing that shows. */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $border_strong;
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: $text_faint; }
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $border_strong;
    min-width: 32px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: $text_faint; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ------------------------------------------------------------- chrome */
/* Docked panes (main_window.py): a slim title strip on the raised chrome
   color, the pane's content on the window background. */
QMainWindow::separator { background: $border; width: 1px; height: 1px; }
QMainWindow::separator:hover { background: $accent; }
QDockWidget {
    color: $text_dim;
    font-size: 12px;
    font-weight: 600;
}
QDockWidget::title {
    background: $raised;
    border-bottom: 1px solid $border;
    padding: 5px 8px;
    text-align: left;
}
QWidget#DockPage { background: $bg; }
/* Tabs for panes docked on top of each other (Part Table | Part Details |
   Log), along the bottom edge the way Lantek's Part Viewer | Part List
   are: the ribbon's accent-rule tabs, rule on the side facing the pane. */
QMainWindow > QTabBar { background: $raised; border-top: 1px solid $border; }
QMainWindow > QTabBar::tab {
    background: $raised;
    color: $text_dim;
    padding: 5px 14px;
    border: none;
    border-top: 2px solid transparent;
}
QMainWindow > QTabBar::tab:selected { color: $accent; border-top: 2px solid $accent; }
QMainWindow > QTabBar::tab:hover { color: $text; }
/* The strip under the sheet canvas: sheet paging, zoom and the cursor's
   X/Y -- chrome, on the raised color, like a status line for the canvas. */
QWidget#CanvasStrip { background: $raised; border-top: 1px solid $border; }
QToolBar#RibbonToolBar { background: $raised; border: none; padding: 0; spacing: 0; }
QListWidget#PartsList { background: $surface; border: 1px solid $border; border-radius: 8px; padding: 3px; }
QListWidget#PartsList::item, QListWidget#PartsList::item:selected, QListWidget#PartsList::item:hover {
    background: transparent;
    border: none;
}
/* Layout Results rows are painted as cards (parts_panel.LayoutCardDelegate);
   the list itself stays out of their way. */
QListWidget#LayoutResultsList { background: transparent; border: none; }
QListWidget#LayoutResultsList::item,
QListWidget#LayoutResultsList::item:selected,
QListWidget#LayoutResultsList::item:hover { background: transparent; border: none; }
/* The status bar's sheet readouts: separate sections divided by hairlines. */
QLabel#StatusSection {
    color: $text_dim;
    padding: 0 12px;
    border-left: 1px solid $border;
}

/* The ribbon (ribbon.py): a row of tabs over one page of captioned
   groups. The tab row sits on the window background and the page on the
   raised surface, so the selected tab -- which takes the page's color and
   an accent underline -- reads as the page's own label. */
#Ribbon {
    background: $raised;
    border-bottom: 1px solid $border;
}
#RibbonTabRow {
    background: $bg;
    border-bottom: 1px solid $border;
}
QToolButton#RibbonTab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 5px 14px 4px 14px;
    color: $text_dim;
    font-size: 13px;
    font-weight: 500;
}
QToolButton#RibbonTab:hover { color: $text; background: $hover; }
QToolButton#RibbonTab:checked {
    color: $accent;
    background: $raised;
    border-bottom: 2px solid $accent;
    font-weight: 600;
}
QStackedWidget#RibbonBody, QScrollArea#RibbonPageScroll, QWidget#RibbonPage, QFrame#RibbonGroup,
QWidget#RibbonGroupContent, QWidget#RibbonFieldRow { background: $raised; }
QLabel#RibbonGroupCaption {
    background: transparent;
    color: $text_faint;
    font-size: 11px;
    padding: 0 0 1px 0;
}
QLabel#RibbonFieldLabel { background: transparent; color: $text_dim; font-size: 12px; }
QToolButton#RibbonLauncher {
    background: transparent;
    border: none;
    padding: 0 1px;
    color: $text_faint;
    font-size: 11px;
}
QToolButton#RibbonLauncher:hover { color: $accent; }
QToolButton#RibbonLarge {
    border-radius: 6px;
    padding: 3px 6px 2px 6px;
    font-size: 12px;
}
QToolButton#RibbonSmall {
    border-radius: 5px;
    padding: 1px 8px 1px 4px;
    font-size: 12px;
}
QToolButton#RibbonSmall:checked, QToolButton#RibbonLarge:checked {
    background: $accent_soft;
    border-color: transparent;
}
#RibbonGroup QCheckBox { background: transparent; font-size: 12px; padding-left: 4px; }
#RibbonGroup QDoubleSpinBox, #RibbonGroup QSpinBox, #RibbonGroup QComboBox {
    padding: 1px 6px;
    font-size: 12px;
    min-width: 72px;
}
QLabel#SheetPosReadout { color: $text_dim; font-weight: 600; }
QLabel#CoordReadout { color: $text_dim; }
/* Page-level headings: every tab opens with its name and one line about
   what it is for, the way a settings pane in Fusion does. */
QLabel#PageTitle {
    color: $text;
    font-size: 20px;
    font-weight: 600;
}
QLabel#PageSubtitle {
    color: $text_dim;
    font-size: 13px;
}
QLabel#SectionCaption {
    color: $text_faint;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 0;
}
QLabel#FieldHint { color: $text_faint; font-size: 12px; }
QLabel#DetailTitle { color: $text; font-size: 17px; font-weight: 600; }
QLabel#DetailLabel { color: $text_dim; }
QLabel#DetailValue { color: $text; font-weight: 500; }
QPlainTextEdit#ConsoleLog {
    background: $sunken;
    border: 1px solid $border;
    border-radius: 8px;
    color: $text_dim;
    font-family: "SF Mono", "JetBrains Mono", "DejaVu Sans Mono", monospace;
    font-size: 12px;
}
""")


def build_qss(dark):
    palette = dict(tokens(dark))
    palette.update(
        font=FONT_STACK,
        check=asset("check.svg"),
        dash=asset("dash.svg"),
        chevron=asset(palette["chevron"]),
        spin_up=asset(palette["spin_up"]),
        spin_down=asset(palette["spin_down"]),
    )
    return _QSS.substitute(palette)


LIGHT_QSS = build_qss(False)
DARK_QSS = build_qss(True)


def apply_theme(app, dark):
    global _CURRENT_DARK
    _CURRENT_DARK = bool(dark)
    app.setStyleSheet(DARK_QSS if _CURRENT_DARK else LIGHT_QSS)
    # The sheet preview is shared with the FreeCAD workbench, so it owns
    # its own default colors and can't import this module; push the active
    # palette into it instead (see nesting_widgets.set_sheet_palette).
    try:
        import nesting_widgets

        nesting_widgets.set_sheet_palette(tokens())
        nesting_widgets.set_canvas_palette(CANVAS)
    except Exception:
        pass
