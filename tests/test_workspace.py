"""
tests/test_workspace.py
------------------------
The native window is one workspace: the sheet canvas in the middle, the
job's panes docked around it, and the ribbon's Stock tab swapping the
center to the stock table. These pin down the wiring that makes that feel
like one app rather than panes that happen to share a window: the ribbon
and View menu toggles follow the panes, the Parts list's badges tell the
truth about a run, and an old saved layout can't scramble the panes.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

from native_app import persistence
from native_app.main_window import MainWindow
from native_app.parts_panel import _BADGE_ROLE


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "_ORGANIZATION", "AlphaNest")
    monkeypatch.setattr(persistence, "_APP", "AlphaNest-Tests")
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


def _load_two_parts(window):
    square = [(0, 0), (50, 0), (50, 50), (0, 50)]
    window.panel._load_parts_dict({
        "A": {"outer": square, "holes": [], "method": "flat", "thickness": 2.0},
        "B": {"outer": square, "holes": [], "method": "flat", "thickness": 2.0},
    })


def test_the_center_follows_the_ribbon_tab(window):
    """Parts shows the parts, Stock the stock table, and the tabs after
    them (Nesting, Output, View) the sheet."""
    assert window.workspace.currentWidget() is window.parts_page   # the first tab: Parts
    window._show_stock_table()
    assert window.workspace.currentWidget() is window.stock_panel
    for title in ("Nesting", "Output", "View"):
        window.ribbon.set_current_index(window._tab_index(title))
        assert window.workspace.currentWidget() is window.panel


def test_the_parts_tab_shows_the_parts_not_the_layout(window):
    """On the Parts tab the Part Table and the large part render fill the
    center and the layout pane is out of the way; leaving the tab puts
    both back in their panes and brings the layout pane back."""
    parts = window.parts_panel
    assert window.parts_page.isAncestorOf(parts.table_page)
    assert window.parts_page.isAncestorOf(parts.detail_page)
    assert window.results_dock.isHidden() and window.log_dock.isHidden()
    assert not window.parts_dock.isHidden()   # the parts list stays
    window.ribbon.set_current_index(window._tab_index("Nesting"))
    assert window.table_dock.widget() is parts.table_page
    assert window.details_dock.widget() is parts.detail_page
    assert not window.results_dock.isHidden() and not window.log_dock.isHidden()


def test_saving_on_the_parts_tab_keeps_the_layout_panes(window):
    """The Parts tab only borrows the layout panes' space: closing the app
    there must not save them as closed."""
    persistence.save_preferences(window)
    assert window._parts_mode and window.results_dock.isHidden()   # still on the Parts tab
    reopened = MainWindow()
    try:
        reopened.ribbon.set_current_index(reopened._tab_index("Nesting"))
        assert not reopened.results_dock.isHidden()
    finally:
        reopened.close()


def test_bottom_panes_share_one_tabbed_area(window):
    window.ribbon.set_current_index(window._tab_index("Nesting"))   # a tab with the panes out
    tabbed = window.tabifiedDockWidgets(window.table_dock)
    assert window.details_dock in tabbed and window.log_dock in tabbed


def test_layout_results_toggle_follows_its_pane(window):
    window.ribbon.set_current_index(window._tab_index("Nesting"))   # a tab with the panes out
    window.results_dock.close()
    assert not window.panel.results_toggle.isChecked()
    window.panel.results_toggle.setChecked(True)
    assert not window.results_dock.isHidden()


def test_parts_list_badges_count_required_then_placed(window):
    _load_two_parts(window)
    window.panel.assembly_quantity.setValue(3)
    items = [window.parts_panel.list.item(i) for i in range(2)]
    assert [it.data(_BADGE_ROLE) for it in items] == [("×3", "none")] * 2

    # A run that placed every B but one A short.
    window.panel.sheets = [[object()]]
    window.panel.unplaced = ["A"]
    window.panel.busy_changed.emit(False)
    assert items[0].data(_BADGE_ROLE) == ("2/3", "some")
    assert items[1].data(_BADGE_ROLE) == ("3/3", "full")


def test_filter_hides_parts_that_dont_match(window):
    _load_two_parts(window)
    window.parts_panel.filter.setText("b")
    assert window.parts_panel.list.item(0).isHidden()
    assert not window.parts_panel.list.item(1).isHidden()


def test_a_pre_dock_saved_layout_is_ignored(window):
    """A window state saved by the old tabbed build has no panes in it;
    restoring it must leave the default pane layout, not a scrambled one."""
    old_state = QtWidgets.QMainWindow().saveState()   # version 0, no docks
    settings = QtCore.QSettings(persistence._ORGANIZATION, persistence._APP)
    settings.setValue("window/state", old_state)
    settings.sync()
    reopened = MainWindow()
    try:
        reopened.ribbon.set_current_index(reopened._tab_index("Nesting"))   # a tab with the panes out
        assert reopened.log_dock in reopened.tabifiedDockWidgets(reopened.table_dock)
    finally:
        reopened.close()


def test_the_live_canvas_is_dark_with_rulers_but_the_report_is_not(window):
    import nesting_widgets

    preview = window.panel.preview
    assert preview.use_canvas_palette and preview.show_rulers
    canvas = nesting_widgets._preview_colors(True)
    report = nesting_widgets._preview_colors(False)
    assert canvas["sheet"].lightness() < 80
    assert report["sheet"] == nesting_widgets.SHEET_FILL   # report images keep the theme colors


def test_ruler_ticks_stay_readable_at_any_scale():
    import nesting_widgets

    for scale in (0.05, 0.3, 1.0, 8.0):
        step = nesting_widgets._ruler_step(scale)
        assert step * scale >= nesting_widgets._RULER_MIN_LABEL_PX or step == nesting_widgets._RULER_STEPS_MM[-1]


def test_status_bar_describes_the_sheet_on_the_canvas(window):
    import nester

    square = [(0, 0), (500, 0), (500, 500), (0, 500)]
    part = nester.PlacedPart(name="A", points=square, holes=[], rotation=0.0,
                             sheet_index=0, mirrored=False)
    panel = window.panel
    assert window.status_use.isHidden()                  # no sheet, no sheet figures
    panel.sheets = [[part], []]
    panel.sheet_dims = [(1000.0, 1000.0), (1000.0, 1000.0)]
    panel.sheet_job_label = ["", ""]
    panel.current_sheet_index = 0
    panel._show_sheet()
    assert not window.status_use.isHidden()
    assert window.status_sheet_size.text() == "1000 × 1000"
    assert window.status_use.text() == "Use 25.0 %"
    assert window.status_remnant.text() == "Remnant 50.0 %"  # a 500 x 1000 strip beside the part
    assert window.status_sheet_pos.text() == "Sheet 1 / 2"
    panel.preview.coords.emit((12.34, 5.0))
    assert window.status_coords.text() == "X 12.3   Y 5.0"


def test_window_title_names_the_job(window, tmp_path):
    assert window.windowTitle() == "AlphaNest — Untitled job"
    path = tmp_path / "bracket_run.json"
    window.panel._save_job(str(path))
    assert window.windowTitle() == "AlphaNest — bracket_run"
    window._new_job()
    assert window.windowTitle() == "AlphaNest — Untitled job"


def test_loading_parts_brings_a_closed_parts_pane_back(window):
    window.parts_dock.close()
    _load_two_parts(window)
    assert not window.parts_dock.isHidden()


def test_reset_layout_restores_every_pane(window):
    window.ribbon.set_current_index(window._tab_index("Nesting"))
    for dock in (window.parts_dock, window.results_dock, window.details_dock, window.log_dock):
        dock.close()
    window._reset_layout()
    assert all(not d.isHidden() for d in (window.parts_dock, window.results_dock, window.table_dock,
                                          window.details_dock, window.log_dock))
    assert window.log_dock in window.tabifiedDockWidgets(window.table_dock)


def test_a_run_keeps_the_window_live_and_reports_only_its_real_sheets(window):
    """The search runs on a worker thread: the window keeps processing
    events throughout (it used to go "not responding" for a whole
    generation in parallel mode), and the result is exactly the run's own
    sheets -- not the last live candidate with the real ones appended."""
    import time

    import inventory as inv

    panel = window.panel
    extracted, _logs = window.part_source.load_files(["examples/parts.json"])
    panel._load_parts_dict(extracted)
    panel._set_inventory("examples/inventory.json", inv.load_inventory("examples/inventory.json"), "test")
    panel.ga_population.setValue(4)
    panel.ga_generations.setValue(2)
    assert panel._run_preflight_check()[0]

    ticks = []
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    panel._optimize_ordering()
    timer.stop()

    assert len(ticks) >= 2                                      # events kept flowing mid-run
    assert all(panel.sheet_job_label)                           # every sheet is a real, labelled result
    placed = sum(len(s) for s in panel.sheets)
    required = sum(s["quantity_per_assembly"] for s in panel.part_summaries())
    assert placed + len(panel.unplaced) == required             # no duplicated candidate sheet


def test_export_dxf_writes_one_folder_per_material_and_a_manifest(window, tmp_path, monkeypatch):
    """Export DXF used to do nothing: it read material/thickness off
    PlacedPart (which has neither), and the AttributeError died silently
    in the button's slot."""
    import inventory as inv

    panel = window.panel
    extracted, _logs = window.part_source.load_files(["examples/parts.json"])
    panel._load_parts_dict(extracted)
    panel._set_inventory("examples/inventory.json", inv.load_inventory("examples/inventory.json"), "test")
    panel.ga_population.setValue(4)
    panel.ga_generations.setValue(2)
    assert panel._run_preflight_check()[0]
    panel._optimize_ordering()

    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *a, **k: errors.append(a))
    panel._export_dxf()

    assert not errors
    dxfs = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.dxf"))
    assert dxfs == ["01_Mild_Steel_2.00mm/sheet_001_Mild_Steel_2.00mm.dxf",
                    "02_Stainless_304_3.00mm/sheet_001_Stainless_304_3.00mm.dxf"]
    assert (tmp_path / "release_manifest.csv").is_file()


def test_a_failed_export_says_so_in_the_window(window, monkeypatch):
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(window.panel, "_export_dxf_files", lambda: 1 / 0)
    window.panel._export_dxf()
    assert errors and "DXF export failed" in window.panel.log.toPlainText()
