"""
persistence.py
--------------
Save/restore the native app's preferences to the host's standard QSettings
location: run settings (margins, microjoints, labels, GA budget, report
folder, rules, ...), the last-loaded inventory path, the active tab, the
dark-mode toggle, and the window geometry/state.

Restoring runs once at startup (MainWindow.__init__); saving runs from
MainWindow.closeEvent(), so closing the app captures whatever the last
session left in the live widgets. Every read/write is defensive -- a
corrupt settings file or a changed control must never block startup.
"""

import os

from PySide6 import QtCore, QtWidgets

import inventory as inv

_ORGANIZATION = "AlphaNest"
_APP = "AlphaNest"

# Panel attribute names that are persisted on close and restored on launch.
# Widgets are located dynamically (the panel always owns them even when the
# ribbon reparents their group box), so this list is just shared vocabulary.
AUTOSAVE_ATTRS = [
    "sheet_w", "sheet_h", "assembly_quantity", "part_spacing",
    "margin_left", "margin_right", "margin_top", "margin_bottom", "margin_apply_all",
    "kfactor", "tolerance",
    "min_feature", "min_hole_opening", "min_part_area",
    "allow_hole_nesting", "hole_clearance",
    "joint_stock_optimization",
    "ga_population", "ga_generations", "ga_optimize_rotations",
    "microjoints_enabled", "microjoint_width", "microjoint_spacing",
    "remnant_capture_enabled", "remnant_min_dimension",
    "cut_speed", "label_enabled", "label_height",
    "common_line_enabled", "auto_report_enabled", "reports_dir",
]


def _to_bool(value):
    """QSettings' INI backend (the one actually used on Linux/macOS) doesn't
    preserve a bare bool's type -- it round-trips as the STRING "true" or
    "false", and `if "false":` is True in Python (any non-empty string is
    truthy). AUTOSAVE_ATTRS' checkboxes dodge this by wrapping values in a
    ["check", value] list (Qt's list serialization IS typed correctly), but
    theme/dark is a bare bool with no such wrapper, so it needs this
    explicit parse instead of a plain truthiness check."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _kind(widget):
    if isinstance(widget, QtWidgets.QCheckBox):
        return "check"
    if isinstance(widget, QtWidgets.QSpinBox):
        return "int"
    if isinstance(widget, QtWidgets.QDoubleSpinBox):
        return "float"
    if isinstance(widget, QtWidgets.QLineEdit):
        return "str"
    return None


def _value(widget):
    kind = _kind(widget)
    if kind == "check":
        return ["check", widget.isChecked()]
    if kind == "int":
        return ["int", widget.value()]
    if kind == "float":
        return ["float", widget.value()]
    if kind == "str":
        return ["str", widget.text()]
    return None


def _apply(widget, stored):
    if not isinstance(stored, (list, tuple)) or len(stored) != 2:
        return
    kind, val = stored
    if kind == "check" and isinstance(widget, QtWidgets.QCheckBox):
        widget.setChecked(bool(val))
    elif kind == "int" and isinstance(widget, QtWidgets.QSpinBox):
        widget.setValue(int(float(val)))
    elif kind == "float" and isinstance(widget, QtWidgets.QDoubleSpinBox):
        widget.setValue(float(val))
    elif kind == "str" and isinstance(widget, QtWidgets.QLineEdit):
        widget.setText(str(val))


def save_preferences(window):
    try:
        settings = QtCore.QSettings(_ORGANIZATION, _APP)
        panel = window.panel
        settings.remove("settings")
        settings.remove("window")
        settings.remove("inventory_last_path")
        settings.remove("theme/dark")
        for attr in AUTOSAVE_ATTRS:
            stored = _value(getattr(panel, attr, None))
            if stored is not None:
                settings.setValue("settings/" + attr, stored)
        inventory_path = getattr(panel, "_inventory_path", None)
        if inventory_path:
            settings.setValue("inventory_last_path", inventory_path)
        settings.setValue("window/tab", window.tabs.currentIndex())
        settings.setValue("window/geometry", window.saveGeometry())
        settings.setValue("window/state", window.saveState())
        settings.setValue("theme/dark", window.dark_mode_action.isChecked())
        settings.sync()
    except Exception:
        pass


def restore_preferences(window):
    try:
        settings = QtCore.QSettings(_ORGANIZATION, _APP)
        panel = window.panel

        for attr in AUTOSAVE_ATTRS:
            stored = settings.value("settings/" + attr, None)
            if stored is not None:
                _apply(getattr(panel, attr, None), stored)

        # Re-point at the last inventory file if it still exists; a stale or
        # unreadable path is skipped instead of blocking startup.
        path = settings.value("inventory_last_path", None)
        if isinstance(path, str) and path and os.path.isfile(path):
            try:
                stock = inv.load_inventory(path)
                panel._set_inventory(
                    path, stock, f"Restored last inventory: {os.path.basename(path)}."
                )
            except Exception as e:
                panel._log(f"[warn] could not restore last inventory: {e}")

        geometry = settings.value("window/geometry", None)
        if geometry is not None:
            window.restoreGeometry(geometry)
        state = settings.value("window/state", None)
        if state is not None:
            window.restoreState(state)
        tab = settings.value("window/tab", None)
        if isinstance(tab, int) and 0 <= tab < window.tabs.count():
            window.tabs.setCurrentIndex(tab)
        # Applied unconditionally (not just when it differs from the
        # QAction's own built-in-unchecked default): nothing else in
        # startup ever calls apply_theme() otherwise (it's normally only
        # reached via the action's `toggled` signal), so without this a
        # session that happens to already match the default would render
        # with no stylesheet applied at all -- whatever the OS/Qt platform
        # theme happens to be, not this app's own deliberate light default.
        dark = _to_bool(settings.value("theme/dark", False))
        window.dark_mode_action.blockSignals(True)
        window.dark_mode_action.setChecked(dark)
        window.dark_mode_action.blockSignals(False)
        window._set_theme(dark)
    except Exception:
        pass