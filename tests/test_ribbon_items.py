"""
tests/test_ribbon_items.py
---------------------------
View > Ribbon Items lets a shop decide which ribbon items it actually
wants. Two things have to hold for that to be usable:
the choice has to survive a restart (it is a preference, not a mood), and
the ribbon has to still look deliberate afterwards -- no empty group, no
rule left dividing nothing, and no way to end up with no route to Run
Nesting.

Also covers theme.icon_svg(), the loader that maps the icon pack's own
colors onto the current theme: the ribbon's icons and the accent are only
consistent because every pack icon is recolored on its way in.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from native_app import persistence, theme
from native_app.main_window import MainWindow

_ORG = "AlphaNest"
_APP = "AlphaNest-Tests"


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "_ORGANIZATION", _ORG)
    monkeypatch.setattr(persistence, "_APP", _APP)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.NativeFormat,
        QtCore.QSettings.Scope.UserScope,
        str(tmp_path / "qsettings"),
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def _widget(window, key):
    return next(w for k, _label, w in window.ribbon_items if k == key)


def _shown(widget):
    # Not isVisible(): an item on a ribbon tab that isn't the current one
    # is invisible without anyone having hidden it.
    return not widget.isHidden()


def _group(window, title):
    return next(g for page in window.ribbon.pages for g in page.groups if g.title == title)


def test_every_ribbon_item_starts_visible(window):
    assert all(_shown(w) for _k, _l, w in window.ribbon_items)
    assert all(a.isChecked() for a in window.ribbon_actions.values())
    assert set(window.ribbon_actions) == {k for k, _l, _w in window.ribbon_items}


def test_unticking_an_item_hides_it(window):
    window.ribbon_actions["report"].setChecked(False)
    assert not _shown(_widget(window, "report"))
    assert _shown(_widget(window, "export"))   # its neighbour is untouched


def test_a_group_with_nothing_left_in_it_goes_too(window):
    """Hiding every item in a group takes the group (caption and all) with
    it, and bringing one back brings it back."""
    cutting = _group(window, "Cutting")
    for key in ("microjoints", "tab_width", "common_edge"):
        window.ribbon_actions[key].setChecked(False)
    assert cutting.isHidden()
    window.ribbon_actions["tab_width"].setChecked(True)
    assert not cutting.isHidden()


def test_a_rule_with_nothing_left_to_divide_goes_too(window):
    """Output is Export | After Cutting: emptying After Cutting leaves the
    rule before it dividing nothing, so that rule goes too."""
    output = next(page for page in window.ribbon.pages if page.title == "Output")
    window.ribbon_actions["commit"].setChecked(False)
    assert output._separators[-1].isHidden()
    window.ribbon_actions["commit"].setChecked(True)
    assert not output._separators[-1].isHidden()


def test_tabs_run_in_the_order_a_job_does(window):
    """Parts, then stock, then the nest's settings with Run at their end,
    then output -- each step's next step is the next tab over."""
    titles = [page.title for page in window.ribbon.pages]
    assert titles == ["Parts", "Stock", "Nesting", "Output", "View"]
    nesting = window.ribbon.pages[2]
    assert nesting.groups[-1].title == "Run"
    assert nesting.isAncestorOf(_widget(window, "run"))
    assert window.ribbon.pages[3].isAncestorOf(_widget(window, "export"))


def test_launcher_opens_settings_at_its_section(window):
    _group(window, "Spacing").launcher.click()
    dialog = window._settings_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.close()


def test_key_settings_live_on_the_ribbon_not_in_the_dialog(window):
    """The job-to-job settings moved onto the Nesting tab -- the real panel
    widgets, so each has one home -- and Advanced Nesting Settings keeps
    only the rest."""
    panel = window.panel
    nesting = window.ribbon.pages[2]
    on_ribbon = (panel.part_spacing, panel.margin_top, panel.ga_population,
                 panel.ga_generations, panel.prefer_remnants, panel.microjoints_enabled,
                 panel.microjoint_width, panel.common_line_enabled, panel.allow_hole_nesting)
    assert all(nesting.isAncestorOf(w) for w in on_ribbon)
    window._open_nesting_settings()
    dialog = window._settings_dialog
    assert not any(dialog.isAncestorOf(w) for w in on_ribbon)
    assert dialog.isAncestorOf(panel.min_feature)          # a set-once rule stays
    assert dialog.isAncestorOf(panel.microjoint_spacing)   # ...and so does the fine print
    titles = {box.title() for box in dialog.findChildren(QtWidgets.QGroupBox)}
    assert "Layout optimization" not in titles             # emptied onto the ribbon: no bare frame
    dialog.close()


def test_ribbon_settings_are_locked_during_a_run(window):
    window.panel.busy_changed.emit(True)
    assert not window.panel.part_spacing.isEnabled()
    assert not window.panel.ga_population.isEnabled()
    window.panel.busy_changed.emit(False)
    assert window.panel.part_spacing.isEnabled()


def test_ribbon_toggles_follow_their_panel_buttons(window):
    results = _widget(window, "results")
    window.panel.results_toggle.setChecked(False)
    assert not results.isChecked()
    results.setChecked(True)
    assert window.panel.results_toggle.isChecked()
    window.dark_mode_action.setChecked(True)
    assert _widget(window, "dark_mode").isChecked()
    window.dark_mode_action.setChecked(False)


def test_commit_follows_the_panels_own_gate(window):
    commit = _widget(window, "commit")
    window.panel.commit_btn.setEnabled(True)
    assert commit.isEnabled()
    window.panel.commit_btn.setEnabled(False)
    assert not commit.isEnabled()


def test_show_all_puts_everything_back(window):
    for key in ("run", "stop", "export", "report", "settings", "mix_stock", "labels"):
        window.ribbon_actions[key].setChecked(False)
    window._show_all_ribbon_items()
    assert all(_shown(w) for _k, _l, w in window.ribbon_items)
    assert all(_shown(g) for page in window.ribbon.pages for g in page.groups)
    assert all(_shown(s) for page in window.ribbon.pages for s in page._separators)


def test_running_a_nest_survives_hiding_the_run_button(window):
    """Every toolbar item can be hidden, so Run/Stop also live in the Job
    menu -- otherwise a shop could tidy the one action away and have no
    route back to it."""
    window.ribbon_actions["run"].setChecked(False)
    assert not _shown(_widget(window, "run"))
    assert window.run_action is not None
    assert window.run_action.isVisible()
    assert window.run_action.shortcut() == QtGui.QKeySequence("Ctrl+R")


def test_the_ribbon_tab_survives_a_restart(window):
    window.ribbon.set_current_index(3)
    persistence.save_preferences(window)
    reopened = MainWindow()
    try:
        assert reopened.ribbon.current_index() == 3
    finally:
        reopened.close()


def test_the_choice_survives_a_restart(window):
    window.ribbon_actions["report"].setChecked(False)
    window.ribbon_actions["mix_stock"].setChecked(False)
    persistence.save_preferences(window)

    reopened = MainWindow()
    try:
        assert reopened.ribbon_actions["report"].isChecked() is False
        assert reopened.ribbon_actions["mix_stock"].isChecked() is False
        assert reopened.ribbon_actions["export"].isChecked() is True
        # ...and the widgets follow the ticks, not just the menu.
        assert not _widget(reopened, "report").isVisibleTo(reopened.ribbon)
    finally:
        reopened.close()


def test_pack_icons_are_recolored_into_the_theme():
    """The pack's icons are tiles in the old palette. They arrive as line
    glyphs in this theme's ink and accent, or the toolbar would be a row of
    chips in somebody else's colors."""
    svg = theme.icon_svg("report", dark=False)
    assert svg is not None
    palette = theme.tokens(dark=False)
    assert palette["accent"] in svg          # the pack's #E8352E, remapped
    assert "#E8352E" not in svg
    assert palette["text"] in svg
    assert "#EEF0F3" not in svg              # the tile fill is gone
    assert 'width="96" height="96" rx="18"' not in svg   # ...and the tile itself


def test_mono_icons_are_drawn_in_one_color():
    """What the primary action uses: a white glyph on the filled accent
    button, instead of a two-tone one that reads as a sticker."""
    svg = theme.icon_svg("report", dark=False, mono="#FFFFFF")
    assert svg.count("#FFFFFF") >= 3
    assert theme.tokens(dark=False)["accent"] not in svg


def test_an_unknown_icon_name_has_no_pack_svg():
    assert theme.icon_svg("no-such-action", dark=False) is None
