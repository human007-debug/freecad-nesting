"""
persistence.py
---------------
Save/restore the native app's preferences to the host's standard QSettings
location: run settings (margins, microjoints, labels, GA budget, report
folder, rules, ...), the last-loaded inventory path, the active ribbon tab,
the dark-mode toggle, the window geometry/state (which includes where every
docked pane sits), which items the ribbon shows (View > Ribbon Items), and -- because a preference that
resets every launch is worth less than none -- the "session taste" values
too: currency, sheet zoom, splitter proportions, the two expand/collapse
toggles, and the settings/Recommendation dialogs' own geometry.

Everything auto-saves: restoring runs once at startup (MainWindow.__init__),
saving runs from MainWindow.closeEvent(), so closing the app captures
whatever the last session left in the live widgets. There is deliberately no
"Save Preferences" button -- the app never forgets, so there's nothing to
remember to save. Every read/write is defensive -- a corrupt settings file
or a changed control must never block startup.
"""

import os

from PySide6 import QtCore, QtWidgets

import inventory as inv

_ORGANIZATION = "AlphaNest"
_APP = "AlphaNest"
# Version stamp on the saved window state (toolbar + docked panes). Bump it
# whenever the set of panes changes: restoreState() refuses a state saved
# under a different number, so the window falls back to its default pane
# layout instead of half-applying one from an older build (a pre-dock
# state leaves every new pane untabbed and piled beside the others).
_WINDOW_STATE_VERSION = 2

# Panel attribute names that are persisted on close and restored on launch.
# Widgets are located dynamically (the panel always owns them even when the
# ribbon reparents their group box), so this list is just shared vocabulary.
AUTOSAVE_ATTRS = [
    "sheet_w", "sheet_h", "assembly_quantity", "part_spacing",
    "margin_left", "margin_right", "margin_top", "margin_bottom", "margin_apply_all",
    "kfactor", "tolerance",
    "min_feature", "min_hole_opening", "min_part_area",
    "allow_hole_nesting", "hole_clearance",
    "prefer_remnants", "joint_stock_optimization", "true_joint_stock_optimization",
    "ga_population", "ga_generations", "ga_optimize_rotations", "ga_parallel",
    "microjoints_enabled", "microjoint_width", "microjoint_spacing",
    "remnant_capture_enabled", "remnant_min_dimension",
    "cut_speed", "label_enabled", "label_height",
    "common_line_enabled", "auto_report_enabled", "reports_dir",
    "rec_part_spacing_multiplier", "rec_part_spacing_min", "rec_part_spacing_max",
    "rec_margin_multiplier", "rec_margin_min", "rec_margin_max",
    "rec_tab_width_multiplier", "rec_tab_width_min", "rec_tab_width_max", "rec_tab_target_spacing",
    "zoom_spin",
]

# Recognized dialog windows, so their size/position also survive the session
# (see main_window.py -- the keys are the story each dialog opens under).


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


def _splitter_sizes(widget):
    """A QSplitter's sizes are a plain list of ints -- QSettings stores a
    QList<int> list natively, no bool-wrapping needed. [] when there's no
    splitter or it's empty (restoring that is a no-op)."""
    if widget is None or widget.count() == 0:
        return []
    return list(widget.sizes())


def _restore_splitter(widget, sizes):
    """Applies previously saved sizes to a QSplitter when safe. The two
    front ends legitimately give a splitter a different pane count, so a
    length mismatch silently keeps the built-in default rather than forcing
    a nonsense layout."""
    if not sizes or widget is None or widget.count() == 0:
        return False
    if len(sizes) != widget.count():
        return False
    widget.setSizes([max(0, int(s)) for s in sizes])
    return True


def _save_dialog_geometry(settings, key, dialog):
    if dialog is not None:
        settings.setValue(f"dialogs/{key}/geometry", dialog.saveGeometry())


def restore_dialog_geometry(window, key, dialog):
    """Applied right after a lazily-built dialog is constructed -- restore
    can't run at startup for dialogs that don't exist yet, so it defers to
    here instead. (The companion save happens up front in save_preferences().)"""
    settings = QtCore.QSettings(_ORGANIZATION, _APP)
    geometry = settings.value(f"dialogs/{key}/geometry", None)
    if geometry is not None:
        dialog.restoreGeometry(geometry)


def save_preferences(window):
    # The Parts tab hides the layout panes while it is open (see
    # MainWindow._enter_parts_mode); save the panes as they are on every
    # other tab, not as that tab borrowed them.
    parts_mode = getattr(window, "_parts_mode", False)
    if parts_mode:
        window._leave_parts_mode()
    try:
        _save_preferences(window)
    finally:
        if parts_mode:
            window._enter_parts_mode()


def _save_preferences(window):
    try:
        settings = QtCore.QSettings(_ORGANIZATION, _APP)
        panel = window.panel
        settings.remove("settings")
        settings.remove("window")
        settings.remove("inventory_last_path")
        settings.remove("theme/dark")
        settings.remove("view")
        settings.remove("ribbon")
        for attr in AUTOSAVE_ATTRS:
            stored = _value(getattr(panel, attr, None))
            if stored is not None:
                settings.setValue("settings/" + attr, stored)
        inventory_path = getattr(panel, "_inventory_path", None)
        if inventory_path:
            settings.setValue("inventory_last_path", inventory_path)
        # Which ribbon items the user keeps (View > Ribbon Items). Saved
        # per item key rather than as one list, so adding a button later
        # doesn't invalidate the choices already made about the others.
        for key, action in getattr(window, "ribbon_actions", {}).items():
            settings.setValue(f"ribbon/{key}", action.isChecked())
        ribbon = getattr(window, "ribbon", None)
        if ribbon is not None:
            settings.setValue("window/ribbon_tab", ribbon.current_index())
        settings.setValue("window/geometry", window.saveGeometry())
        settings.setValue("window/state", window.saveState(_WINDOW_STATE_VERSION))
        settings.setValue("theme/dark", window.dark_mode_action.isChecked())

        # Session-taste preferences -- written on the same close event as
        # everything else, restored defensively below. Not worth a button.
        settings.setValue("settings/currency_code", panel.currency_code)
        settings.setValue("view/manual_toggle", panel.manual_toggle.isChecked())
        settings.setValue("view/results_toggle", panel.results_toggle.isChecked())
        settings.setValue("view/nesting_splitter", _splitter_sizes(getattr(panel, "splitter", None)))
        _save_dialog_geometry(settings, "nesting_settings", getattr(window, "_settings_dialog", None))
        _save_dialog_geometry(settings, "recommendation_formula",
                              getattr(panel, "_recommendation_dialog", None))
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

        currency = settings.value("settings/currency_code", None)
        if isinstance(currency, str) and currency:
            panel.set_currency(currency)

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

        # Ticking the action is what hides/shows the widget (its `toggled`
        # signal does the work), so this restores the toolbar by restoring
        # the menu -- and an item never saved stays visible.
        for key, action in getattr(window, "ribbon_actions", {}).items():
            stored = settings.value(f"ribbon/{key}", None)
            if stored is not None:
                action.setChecked(_to_bool(stored))

        geometry = settings.value("window/geometry", None)
        if geometry is not None:
            window.restoreGeometry(geometry)
        state = settings.value("window/state", None)
        if state is not None:
            window.restoreState(state, _WINDOW_STATE_VERSION)
        ribbon_tab = settings.value("window/ribbon_tab", None)
        if ribbon_tab is not None and getattr(window, "ribbon", None) is not None:
            try:
                window.ribbon.set_current_index(int(ribbon_tab))
            except (TypeError, ValueError):
                pass
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

        # Cheap session-taste restores -- nothing here can fail startup.
        panel.manual_toggle.setChecked(_to_bool(settings.value("view/manual_toggle", False)))
        # The results toggle's own signal resizes the splitter panes, so let
        # it do its thing first, then restore the exact sizes on top.
        panel.results_toggle.setChecked(_to_bool(settings.value("view/results_toggle", True)))
        _restore_splitter(getattr(panel, "splitter", None),
                          settings.value("view/nesting_splitter", None))
    except Exception:
        pass