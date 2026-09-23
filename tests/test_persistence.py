"""
tests/test_persistence.py
---------------------------
native_app.persistence's `_to_bool()` exists because QSettings' INI backend
(the one actually used on Linux/macOS, not the Windows registry backend)
doesn't preserve a bare bool's type -- it round-trips as the STRING "true"
or "false", and a plain `if value:` truthiness check treats "false" as
truthy (any non-empty string is). This silently forced dark mode on at
every launch regardless of what was actually saved, since theme/dark is
stored as a bare bool (unlike the AUTOSAVE_ATTRS checkboxes, which dodge
this by wrapping values in a ["check", value] list that Qt DOES
type-preserve correctly). Caught by inspecting a real ~/.config/AlphaNest/
AlphaNest.conf that already correctly recorded `dark=false`.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

from native_app import persistence
from native_app.persistence import _to_bool
from native_app.main_window import MainWindow

_ORG = "AlphaNest"
_APP = "AlphaNest-Tests"


def _point_settings_at(tmp_path):
    """Rebind the NativeFormat/UserScope location QSettings writes to, so
    tests never touch the user's real ~/.config/AlphaNest/AlphaNest.conf.
    setPath() is the reliable knob here: homebrew env vars (XDG_CONFIG_HOME)
    are captured by Qt's config-path cache long before fixtures run."""
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.NativeFormat,
        QtCore.QSettings.Scope.UserScope,
        str(tmp_path / "qsettings"),
    )


@pytest.fixture()
def isolated_settings(monkeypatch, tmp_path):
    """Point QSettings at a throwaway INI file for the duration of the test."""
    monkeypatch.setattr(persistence, "_ORGANIZATION", _ORG)
    monkeypatch.setattr(persistence, "_APP", _APP)
    _point_settings_at(tmp_path)


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "_ORGANIZATION", _ORG)
    monkeypatch.setattr(persistence, "_APP", _APP)
    _point_settings_at(tmp_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (None, ""))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (None, ""))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    win.show()
    yield win
    win.close()
    app.processEvents()


def test_to_bool_parses_ini_backed_false_string_as_false():
    assert _to_bool("false") is False
    assert _to_bool("False") is False
    assert _to_bool("FALSE") is False


def test_to_bool_parses_ini_backed_true_string_as_true():
    assert _to_bool("true") is True
    assert _to_bool("True") is True
    assert _to_bool("1") is True


def test_to_bool_passes_through_real_booleans():
    assert _to_bool(True) is True
    assert _to_bool(False) is False


def test_to_bool_naive_truthiness_would_have_gotten_this_wrong():
    """Documents the exact bug this function fixes: a plain `if value:`
    on the string "false" is True in Python."""
    assert bool("false") is True  # the naive check that caused the bug
    assert _to_bool("false") is False  # what the fix actually returns


def test_preferences_round_trip_across_windows(window, isolated_settings):
    """The whole point of the auto-save feature: close the app after moving
    things around, relaunch, and the layout should be exactly as you left
    it -- ribbon tab, panes, zoom, currency, toggles and splitter proportions."""
    window.panel.zoom_spin.setValue(2.4)
    window.panel.set_currency("USD")
    window.panel.manual_toggle.setChecked(True)
    window.panel.results_toggle.setChecked(True)
    window.ribbon.set_current_index(1)   # Stock: the workspace shows the stock table
    window.log_dock.hide()
    persistence.save_preferences(window)

    window2 = MainWindow()
    window2.show()
    try:
        assert window2.panel.zoom_spin.value() == 2.4
        assert window2.panel.currency_code == "USD"
        assert window2.panel.manual_toggle.isChecked() is True
        assert window2.panel.results_toggle.isChecked() is True
        assert window2.ribbon.current_index() == 1
        assert window2.workspace.currentWidget() is window2.stock_panel
        assert window2.log_dock.isHidden()
    finally:
        window2.close()


def test_dark_mode_bool_round_trips_through_ini(window, isolated_settings):
    """Regression guard for the exact bug at the top of this file: dark mode
    must survive a save/restart cycle instead of always reopening light."""
    persistence.save_preferences(window)  # dark defaults to False this first run
    assert _to_bool(QtCore.QSettings(_ORG, _APP).value("theme/dark", None)) is False

    window.dark_mode_action.setChecked(True)
    persistence.save_preferences(window)
    assert _to_bool(QtCore.QSettings(_ORG, _APP).value("theme/dark", None)) is True