"""
main_window.py
--------------
The native app's top-level window, laid out as one workspace the way CAM
nesting suites (Lantek, LaserNest) lay theirs out:

* a tabbed ribbon across the top (ribbon.py -- Parts / Stock / Nesting / Output /
  View, each a page of captioned groups);
* the sheet canvas in the middle -- the shared `nesting_widgets.NestingPanel`
  (backed by a `FilePartSource` instead of a live FreeCAD document), which
  swaps to the stock table (stock_panel.py) while the ribbon's Stock tab is
  open;
* the job's panes docked around it -- Parts list on the left, Layout Results
  on the right, Part Table / Part Details / Log tabbed along the bottom
  (parts_panel.py builds the parts panes) -- movable, closable, and
  remembered between sessions;
* a status bar that pairs one plain-language line about the job with the
  canvas sheet's figures (size, use %, remnant %, sheet n / N, cursor X/Y),
  and a window title naming the open job;
* a light/dark theme (theme.py, from the user-supplied alphanest-icon-pack).

The panel is built with `show_actions=False` and `settings_in_ribbon=True`:
its own scan/rescan button, bottom action row, and settings/stock/GA group
boxes are all still constructed (so its internal logic is untouched) but
not shown directly. The settings a shop changes from job to job (spacing,
margins, search budget, placement, cutting options) are taken out of their
boxes onto the ribbon's Nesting tab; every other box is reparented, whole,
into the "Advanced Nesting Settings" dialog (_open_nesting_settings), which
each ribbon group's corner launcher opens at that group's section.

The sheet view is drawn as a cutting canvas -- LibreCAD-style black in both themes
(theme.CANVAS), with mm rulers and the sheet's material/size in its corner.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

import geometry
import part_colors
import remnant as remnant_mod
from nesting_widgets import NestingPanel
from native_app import theme
from native_app import persistence
from native_app.file_part_source import FilePartSource
from native_app.parts_panel import LayoutCardDelegate, PartsPanel
from native_app.ribbon import Ribbon
from native_app.stock_panel import StockPanel

# The panel's settings group boxes, grouped by the ribbon tab that hosts
# them in the native app (the FreeCAD workbench keeps them in the panel).
# Width cap for the number fields adopted onto the ribbon (see _take_setting).
_RIBBON_FIELD_PX = 88
_RIBBON_SETTINGS_TABS = ("Layout", "Rules", "Cutting", "Stock", "Export")
_QWIDGETSIZE_MAX = (1 << 24) - 1   # Qt's QWIDGETSIZE_MAX, which PySide doesn't export


class _EnabledMirror(QtCore.QObject):
    """Makes `mirror` follow `source`'s enabled state -- for a ribbon button
    standing in for a panel button whose enabling the panel manages itself
    (Commit to Inventory). `set_blocked(True)` holds the mirror disabled
    regardless, e.g. while a run is going."""

    def __init__(self, source, mirror, parent=None):
        super().__init__(parent)
        self._source = source
        self._mirror = mirror
        self._blocked = False
        source.installEventFilter(self)
        self._sync()

    def set_blocked(self, blocked):
        self._blocked = blocked
        self._sync()

    def _sync(self):
        self._mirror.setEnabled(self._source.isEnabled() and not self._blocked)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.EnabledChange:
            self._sync()
        return False


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AlphaNest")
        # Make the standard window controls explicit rather than relying on
        # defaults: some platform themes/WMs drop the maximize button from a
        # plain QMainWindow; asking for all three standard buttons up front
        # guarantees minimize + maximize + close stay available.
        self.setWindowFlags(self.windowFlags()
                            | QtCore.Qt.WindowMinimizeButtonHint
                            | QtCore.Qt.WindowMaximizeButtonHint)
        mark = theme.mark_path()
        if mark:
            self.setWindowIcon(QtGui.QIcon(mark))

        # The theme goes on BEFORE any of this window's widgets exist: the
        # shared panel gives itself a minimal fallback stylesheet when its
        # host hasn't got one (see nesting_widgets.FALLBACK_QSS, which is
        # what the FreeCAD workbench runs on), and that check has to see
        # this app's sheet already in place.
        theme.apply_theme(QtWidgets.QApplication.instance(), False)

        self.part_source = FilePartSource()
        self.panel = NestingPanel(self.part_source, self, show_actions=False, settings_in_ribbon=True)
        self.panel.close_requested.connect(self.close)
        # The Recommendation-formula dialog is built lazily, inside the
        # shared panel -- adopt its open method here so its geometry can be
        # restored alongside the main Settings dialog's on every reopen.
        _orig_open_rec = self.panel._open_recommendation_settings

        def _open_rec_with_geometry():
            _orig_open_rec()
            dlg = getattr(self.panel, "_recommendation_dialog", None)
            if dlg is not None:
                persistence.restore_dialog_geometry(self, "recommendation_formula", dlg)

        self.panel._open_recommendation_settings = _open_rec_with_geometry

        self.stock_panel = StockPanel(self.panel, self)
        self.parts_panel = PartsPanel(self.panel, self)

        # One workspace, the way CAM nesting suites lay theirs out: the
        # sheet canvas in the middle with the job's panes docked around it
        # (movable, closable, tabbable -- and remembered between sessions).
        # The center follows the ribbon tab: the Parts tab shows the parts
        # themselves (see _enter_parts_mode), the Stock tab the stock
        # table, and every other tab the sheet.
        self.nesting_page = self.panel
        self.workspace = QtWidgets.QStackedWidget()
        self.workspace.setObjectName("Workspace")
        self.workspace.addWidget(self.panel)
        self.workspace.addWidget(self.stock_panel)
        self.parts_page = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.parts_page.setObjectName("PartsWorkspace")
        self.parts_page.setChildrenCollapsible(False)
        self.workspace.addWidget(self.parts_page)
        self._parts_mode = False
        self._parts_mode_hidden = []
        self.setCentralWidget(self.workspace)
        self._build_canvas()
        self._build_docks()

        self.ribbon = self._build_ribbon()
        self.ribbon.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.ribbon.current_changed.connect(self._on_ribbon_tab_changed)
        # In a toolbar rather than the central widget, so it spans the whole
        # window ABOVE the docks instead of sitting beside them. Not movable
        # and not listed in the dock/toolbar context menu -- the ribbon is
        # the app's controls, not an optional pane.
        ribbon_bar = QtWidgets.QToolBar("Ribbon", self)
        ribbon_bar.setObjectName("RibbonToolBar")
        ribbon_bar.setMovable(False)
        ribbon_bar.setFloatable(False)
        ribbon_bar.addWidget(self.ribbon)
        ribbon_bar.toggleViewAction().setVisible(False)
        self.addToolBar(QtCore.Qt.TopToolBarArea, ribbon_bar)

        self._build_menu_bar()
        self._build_status_bar()
        # What View > Reset Layout goes back to -- captured before the last
        # session's layout is restored over it.
        self._default_dock_state = self.saveState()
        self._set_theme(False)  # ribbon icons + panels, on top of the sheet applied above
        # Last session's settings/inventory/tab/dark-mode/geometry are
        # restored after everything above exists; saved again on close.
        persistence.restore_preferences(self)
        self._first_preferences_loaded = True
        # Restoring the tab only signals when it CHANGES the tab; put the
        # center in step with whichever tab it ended up on either way.
        self._on_ribbon_tab_changed(self.ribbon.current_index())

    # -------------------------------------------------------------- canvas

    def _build_canvas(self):
        """The sheet view as a cutting canvas: edge to edge in the
        workspace, LibreCAD-style black (theme.CANVAS) with mm rulers and the sheet's
        material/size in its corner, and one slim strip under it for sheet
        paging, zoom and the cursor's X/Y."""
        panel = self.panel
        panel.preview.use_canvas_palette = True
        panel.preview.show_rulers = True
        panel.layout().setContentsMargins(0, 0, 0, 0)
        panel.layout().setSpacing(0)
        column = panel.preview.parentWidget().layout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        nav = panel.nav_row
        for i in range(column.count()):
            if column.itemAt(i).layout() is nav:
                column.takeAt(i)
                strip = QtWidgets.QWidget()
                strip.setObjectName("CanvasStrip")
                strip.setAttribute(QtCore.Qt.WA_StyledBackground, True)
                nav.setParent(None)
                strip.setLayout(nav)
                nav.setContentsMargins(10, 5, 10, 5)
                column.insertWidget(i, strip)
                self.canvas_strip = strip
                break
        # The sheet position and the cursor's X/Y are the status bar's job
        # now (_build_status_bar), the way Lantek's bottom line carries
        # them; the panel's own readouts stay, hidden, as state it writes.
        panel.coord_label.hide()
        panel.sheet_pos_label.hide()
        # The strip's long "Sheet 1 / 3 -- 1220x2440 mm, 6 parts, ..." line
        # said again what the status bar's sections now say; the strip keeps
        # just its controls, and the sheet picker gets the room to name its
        # sheets instead of eliding them to "Sheet 1:".
        panel.sheet_label.hide()
        nav.insertStretch(nav.indexOf(panel.next_btn) + 1, 1)
        panel.sheet_selector.setMinimumWidth(300)

    # --------------------------------------------------------------- docks

    def _add_dock(self, title, name, widget, area):
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(name)   # saveState()/restoreState() key
        dock.setWidget(widget)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetClosable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self):
        panel = self.panel
        self.setDockOptions(QtWidgets.QMainWindow.AnimatedDocks
                            | QtWidgets.QMainWindow.AllowTabbedDocks
                            | QtWidgets.QMainWindow.AllowNestedDocks)
        # The bottom panes run the full width under the side panes' ends,
        # like Lantek's Part Viewer; the side panes keep the full height
        # beside the canvas otherwise.
        self.setCorner(QtCore.Qt.BottomLeftCorner, QtCore.Qt.LeftDockWidgetArea)
        self.setCorner(QtCore.Qt.BottomRightCorner, QtCore.Qt.RightDockWidgetArea)
        self.setTabPosition(QtCore.Qt.BottomDockWidgetArea, QtWidgets.QTabWidget.South)

        self.parts_dock = self._add_dock("Parts", "PartsDock", self.parts_panel.list_page,
                                         QtCore.Qt.LeftDockWidgetArea)

        # Layout Results moves out of the panel's splitter into its own
        # pane. The pane's title replaces the column's caption, and the
        # column's width cap (sized for a splitter) goes -- a dock is
        # resized by its splitter handle like any other pane.
        results = panel.layouts_col
        for caption in results.findChildren(QtWidgets.QLabel, "SectionCaption"):
            caption.hide()
        results.setMaximumWidth(_QWIDGETSIZE_MAX)
        results.setMinimumWidth(230)
        results.layout().setContentsMargins(8, 6, 8, 6)
        panel.layout_list.setObjectName("LayoutResultsList")
        panel.layout_list.setIconSize(QtCore.QSize(80, 80))
        panel.layout_list.setItemDelegate(LayoutCardDelegate(panel.layout_list))
        panel.layout_list.setMouseTracking(True)
        self.results_dock = self._add_dock("Layout Results", "LayoutResultsDock", results,
                                           QtCore.Qt.RightDockWidgetArea)
        # Showing/hiding the results is now the pane's job, not a splitter
        # resize inside the panel.
        panel.results_toggle.toggled.disconnect(panel._toggle_layout_results)
        results.show()
        self._sync_dock(self.results_dock, panel.results_toggle)

        self.table_dock = self._add_dock("Part Table", "PartTableDock", self.parts_panel.table_page,
                                         QtCore.Qt.BottomDockWidgetArea)
        self.details_dock = self._add_dock("Part Details", "PartDetailsDock",
                                           self.parts_panel.detail_page, QtCore.Qt.BottomDockWidgetArea)
        log_page = QtWidgets.QWidget()
        log_page.setObjectName("DockPage")
        log_layout = QtWidgets.QVBoxLayout(log_page)
        log_layout.setContentsMargins(8, 6, 8, 6)
        panel.log.setMaximumHeight(_QWIDGETSIZE_MAX)
        log_layout.addWidget(panel.log)
        self.log_dock = self._add_dock("Log", "LogDock", log_page, QtCore.Qt.BottomDockWidgetArea)
        self.tabifyDockWidget(self.table_dock, self.details_dock)
        self.tabifyDockWidget(self.details_dock, self.log_dock)
        self.table_dock.raise_()
        for dock in (self.table_dock, self.details_dock, self.log_dock):
            dock.widget().setMinimumHeight(150)

        self.resizeDocks([self.parts_dock, self.results_dock], [300, 250], QtCore.Qt.Horizontal)
        self.resizeDocks([self.table_dock], [210], QtCore.Qt.Vertical)

        # The panel's own Manual adjustment / Layout results buttons are the
        # ribbon's View tab now; the buttons stay (hidden) as the state the
        # ribbon, persistence and the panel itself all read.
        panel.results_toggle.hide()
        panel.manual_toggle.hide()
        panel.parts_changed.connect(self._reveal_parts)

    def _reveal_parts(self):
        """Loading parts brings the Parts list forward if it was closed --
        a job's parts are the one thing that should never be out of sight
        just because a pane was closed in some earlier session. (Check
        readiness does the same for the Part Table.)"""
        if self.panel._extracted and self.parts_dock.isHidden():
            self._show_dock(self.parts_dock)

    def _show_dock(self, dock):
        dock.show()
        dock.raise_()

    def _on_ribbon_tab_changed(self, index):
        title = self.ribbon.pages[index].title
        if title == "Parts":
            self._enter_parts_mode()
            self.workspace.setCurrentWidget(self.parts_page)
            return
        self._leave_parts_mode()
        self.workspace.setCurrentWidget(self.stock_panel if title == "Stock" else self.panel)

    def _enter_parts_mode(self):
        """The Parts tab is about the parts, not the nest: the Part Table
        and the selected part's large render move out of their docks into
        the center (table above, part below -- the old Parts page's
        layout), and every other pane -- the LAYOUT's (Layout Results),
        the Log, and the two now-empty ones -- is hidden, so the tab shows
        only the parts. The Parts list stays on the left.
        _leave_parts_mode() puts everything back as it was."""
        if self._parts_mode:
            return
        self._parts_mode = True
        docks = (self.results_dock, self.table_dock, self.details_dock, self.log_dock)
        self._parts_mode_hidden = [dock for dock in docks if not dock.isHidden()]
        for dock in docks:
            dock.hide()
        for page in (self.parts_panel.table_page, self.parts_panel.detail_page):
            self.parts_page.addWidget(page)
            page.show()
        self.parts_page.setSizes([1, 1])

    def _leave_parts_mode(self):
        if not self._parts_mode:
            return
        self._parts_mode = False
        self.table_dock.setWidget(self.parts_panel.table_page)
        self.details_dock.setWidget(self.parts_panel.detail_page)
        for dock in self._parts_mode_hidden:
            dock.show()
        self._parts_mode_hidden = []

    def _tab_index(self, title):
        return next(i for i, page in enumerate(self.ribbon.pages) if page.title == title)

    def _show_stock_table(self):
        self.ribbon.set_current_index(self._tab_index("Stock"))

    def _show_sheet_workspace(self):
        if self.workspace.currentWidget() is not self.panel:
            self.ribbon.set_current_index(self._tab_index("Nesting"))

    def _reset_layout(self):
        in_parts = self._parts_mode
        self._leave_parts_mode()
        self.restoreState(self._default_dock_state)
        self.resizeDocks([self.parts_dock, self.results_dock], [300, 250], QtCore.Qt.Horizontal)
        self.resizeDocks([self.table_dock], [210], QtCore.Qt.Vertical)
        self.table_dock.raise_()
        if in_parts:
            self._enter_parts_mode()

    # -------------------------------------------------------------- status

    def _build_status_bar(self):
        """The bottom line, in two halves.

        Left: one sentence that tells the truth about the job no matter
        which pane is open -- "how is it doing?" and "what do I do next?"
        -- phrased in a human voice instead of "preflight gate closed".

        Right: the sheet on the canvas as numbers, in separate sections the
        way Lantek's status bar reads -- sheet size, how much of it the
        parts use, how big a remnant it leaves, which sheet of how many,
        and where the cursor is on it. The sheet sections only appear once
        there is a sheet to describe."""
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.job_status = QtWidgets.QLabel("")
        self.job_status.setObjectName("JobStatus")
        self.job_status.setContentsMargins(8, 0, 8, 0)
        bar.addWidget(self.job_status, 1)

        def section(tip):
            label = QtWidgets.QLabel("")
            label.setObjectName("StatusSection")
            label.setToolTip(tip)
            bar.addPermanentWidget(label)
            return label

        self.status_sheet_size = section("Size of the sheet on the canvas.")
        self.status_use = section("Share of this sheet's area the placed parts cover (net of holes).")
        self.status_remnant = section(
            "Largest rectangular offcut this sheet leaves, as a share of the sheet -- measured "
            "around the parts' bounding boxes, the same way Commit records remnants.")
        self.status_sheet_pos = section("Which sheet the canvas shows, of how many in this layout.")
        self.status_coords = section("Cursor position on the sheet.")
        # Wide enough for "X 12,345.6   Y 12,345.6" up front, so the
        # sections to its left don't shuffle as the cursor moves.
        self.status_coords.setMinimumWidth(
            self.status_coords.fontMetrics().horizontalAdvance("X 00,000.0   Y 00,000.0") + 26)
        self.status_units = section("All lengths are in millimetres.")
        self.status_units.setText("mm")
        self._sheet_sections = (self.status_sheet_size, self.status_use,
                                self.status_remnant, self.status_sheet_pos)
        self._last_operational = ""
        # Every state change that could rewrite the answer is a signal the
        # panel already emits -- no polling, no duplication.
        self.panel.parts_changed.connect(self._refresh_job_status)
        self.panel.preflight_ready.connect(self._refresh_job_status)
        self.panel.inventory_changed.connect(self._refresh_job_status)
        self.panel.operational.connect(self._on_operational)
        self.panel.sheet_shown.connect(self._refresh_sheet_status)
        self.panel.preview.coords.connect(self._refresh_coords)
        self.panel.job_path_changed.connect(self._refresh_title)
        self._refresh_job_status()
        self._refresh_sheet_status(-1)
        self._refresh_coords(None)
        self._refresh_title()

    def _refresh_sheet_status(self, index):
        panel = self.panel
        if not (0 <= index < len(panel.sheets)):
            for label in self._sheet_sections:
                label.hide()
            return
        sheet = panel.sheets[index]
        w, h = panel.sheet_dims[index]
        area = w * h
        used = sum(pp.net_area() for pp in sheet)
        remnant = remnant_mod.largest_empty_rect(w, h, [geometry.polygon_bbox(pp.points) for pp in sheet])
        remnant_area = remnant[2] * remnant[3] if remnant else 0.0
        self.status_sheet_size.setText(f"{w:g} \u00d7 {h:g}")
        self.status_use.setText(f"Use {100.0 * used / area:.1f} %" if area else "Use --")
        self.status_remnant.setText(f"Remnant {100.0 * remnant_area / area:.1f} %" if area else "Remnant --")
        self.status_sheet_pos.setText(f"Sheet {index + 1} / {len(panel.sheets)}")
        for label in self._sheet_sections:
            label.show()

    def _refresh_coords(self, xy):
        if xy is None:
            self.status_coords.setText("X --   Y --")
        else:
            self.status_coords.setText(f"X {xy[0]:,.1f}   Y {xy[1]:,.1f}")

    def _refresh_title(self, *_):
        path = self.panel._job_path
        name = os.path.splitext(os.path.basename(path))[0] if path else "Untitled job"
        self.setWindowTitle(f"AlphaNest \u2014 {name}")

    def _on_operational(self, text):
        # Cached for when the run's own live text is the most useful thing
        # to show (a result exists); _refresh_job_status() decides whether
        # to surface it or something more instructive.
        self._last_operational = text
        if self.panel.sheets:
            self.job_status.setText(text)

    def _refresh_job_status(self, *_):
        panel = self.panel
        has_parts = bool(panel._extracted)
        if panel.sheets and self._last_operational:
            self.job_status.setText(self._last_operational)
            self.job_status.setToolTip("")
            return
        if not has_parts:
            msg = "Start by adding parts -- Add Parts (Ctrl+I) takes DXF, JSON, or FreeCAD files."
        elif not panel.preflight_pass:
            msg = "The Part Table lists what needs attention before the parts can be nested."
        elif not panel._inventory_template:
            msg = "Ready to nest -- load stock sheets from the ribbon's Stock tab to plan material cost."
        else:
            msg = "Ready to nest -- press Run Nesting (Ctrl+R)."
        self.job_status.setText(msg)
        self.job_status.setToolTip(msg)

    def closeEvent(self, event):
        # Save the session before teardown: settings widgets are still
        # alive here, so values read back the way the user left them.
        if getattr(self, "_first_preferences_loaded", False):
            persistence.save_preferences(self)
        super().closeEvent(event)

    # --------------------------------------------------------------- ribbon

    def _build_ribbon(self):
        """The tabbed ribbon (ribbon.py), one tab per step of a job, left
        to right in the order they happen: Parts (job file, add/check
        parts), Stock (inventory, shown as the stock table), Nesting (the
        per-nest options, ending in Run/Stop), Output (DXF, report, commit
        to inventory), then View. Every button calls a real,
        already-working panel method."""
        ribbon = Ribbon(self)
        panel = self.panel
        items = []   # (key, menu label, widget) -- see View > Ribbon Items

        # --------------------------------------------------------- Parts
        # The tabs run in the order a job does -- Parts, Stock, Nesting (its
        # settings, then Run at the end), Output -- so each step's next
        # step is the next tab over, never a trip back to an earlier one.
        parts_tab = ribbon.add_tab("Parts")
        job = parts_tab.add_group("Job")
        new_btn = job.add_small("new-job", "+", "New")
        new_btn.clicked.connect(self._new_job)
        open_btn = job.add_small("open-job", "O", "Open...")
        open_btn.clicked.connect(panel._open_job)
        save_btn = job.add_small("save-job", "S", "Save")
        save_btn.clicked.connect(panel._save_job_current)
        new_btn.setToolTip("Clear the parts and layout and start a new job (Ctrl+N).")
        open_btn.setToolTip("Open a saved job file (Ctrl+O).")
        save_btn.setToolTip("Save this job (Ctrl+S).")

        parts = parts_tab.add_group("Parts")
        add_btn = parts.add_large("add-parts", "+", "Add\nParts")
        add_btn.setToolTip("Import DXF, parts JSON, or FreeCAD files (Ctrl+I).")
        add_btn.clicked.connect(panel._rescan)
        check_btn = parts.add_small("preflight", "\u2713", "Check readiness")
        check_btn.setToolTip("Run the parts readiness check -- Run Nesting stays locked until it passes.")
        check_btn.clicked.connect(self._check_readiness)
        remove_btn = parts.add_small("remove-part", "\u2212", "Remove selected")
        remove_btn.setToolTip("Remove the parts selected in the parts table.")
        remove_btn.clicked.connect(panel._remove_selected_parts)

        # --------------------------------------------------------- Stock
        stock = ribbon.add_tab("Stock")
        inventory = stock.add_group("Inventory")
        load_btn = inventory.add_large("load-inventory", "\u2b06", "Load\nInventory")
        load_btn.setToolTip("Load stock from an inventory workbook (.xlsx).")
        load_btn.clicked.connect(panel._load_inventory)
        clear_btn = inventory.add_small("clear-inventory", "\u2715", "Clear inventory")
        clear_btn.setToolTip("Remove all loaded inventory (asks for the administrator password).")
        clear_btn.clicked.connect(lambda: self.stock_panel._clear_with_confirm())

        pricing = stock.add_group("Pricing")
        currency_row = pricing.add_widget(self.stock_panel.currency, "Currency")
        self.stock_panel.hide_header_actions()

        # ------------------------------------------------------- Nesting
        # The settings a shop actually changes from job to job, right on the
        # ribbon -- the real panel widgets, taken out of their settings
        # boxes (_take_setting), so each setting has exactly one home and
        # job save/load and persistence keep reading the same objects.
        # Everything else stays behind Advanced Nesting Settings.
        nesting = ribbon.add_tab("Nesting")
        spacing_group = nesting.add_group("Spacing", launcher=True)
        spacing_group.launcher_clicked.connect(lambda: self._open_nesting_settings("Layout"))
        spacing_row = spacing_group.add_widget(self._take_setting(panel.part_spacing), "Part spacing")
        margins_row = spacing_group.add_widget(self._take_margin_grid(), rows=2)
        spacing_group.new_column()
        margin_all_row = spacing_group.add_widget(self._take_setting(panel.margin_apply_all))
        panel.margin_apply_all.setText("Same margin on all edges")
        recommend_btn = spacing_group.add_small("recommend", "\u2728", "Suggest cut settings")
        recommend_btn.setToolTip(panel.recommend_cutting_btn.toolTip())
        recommend_btn.clicked.connect(panel._recommend_cutting_params)
        formula_btn = spacing_group.add_small("formula", "f", "Formula...")
        formula_btn.setToolTip("Edit the coefficients Suggest cut settings computes from.")
        formula_btn.clicked.connect(lambda: panel._open_recommendation_settings())

        search = nesting.add_group("Optimization", launcher=True)
        search.launcher_clicked.connect(lambda: self._open_nesting_settings("Layout"))
        population_row = search.add_widget(self._take_setting(panel.ga_population), "Population")
        generations_row = search.add_widget(self._take_setting(panel.ga_generations), "Generations")
        search.new_column()
        panel.ga_optimize_rotations.setText("Search rotations")
        panel.ga_parallel.setText("Use all CPU cores")
        rotations_row = search.add_widget(self._take_setting(panel.ga_optimize_rotations))
        parallel_row = search.add_widget(self._take_setting(panel.ga_parallel))

        placement = nesting.add_group("Placement", launcher=True)
        placement.launcher_clicked.connect(lambda: self._open_nesting_settings("Stock"))
        remnants_row = placement.add_widget(self._take_setting(panel.prefer_remnants))
        mix_row = placement.add_widget(panel.true_joint_stock_optimization)
        panel.allow_hole_nesting.setText("Nest parts in holes")
        in_hole_row = placement.add_widget(self._take_setting(panel.allow_hole_nesting))
        placement.new_column()
        clearance_row = placement.add_widget(self._take_setting(panel.hole_clearance), "Hole clearance")

        cutting = nesting.add_group("Cutting", launcher=True)
        cutting.launcher_clicked.connect(lambda: self._open_nesting_settings("Cutting"))
        panel.microjoints_enabled.setText("Microjoints")
        microjoints_row = cutting.add_widget(self._take_setting(panel.microjoints_enabled))
        tab_width_row = cutting.add_widget(self._take_setting(panel.microjoint_width), "Tab width")
        panel.common_line_enabled.setText("Common-edge cutting")
        common_row = cutting.add_widget(self._take_setting(panel.common_line_enabled))

        advanced = nesting.add_group("Advanced")
        settings_btn = advanced.add_large("settings", "\u2699", "More\nSettings")
        settings_btn.setToolTip("Advanced Nesting Settings: everything that doesn't change from job to job -- safety rules, "
                                "joint stock optimization, microjoint spacing, remnant capture, cut "
                                "speed, label height and the report folder. Opens in its own window; "
                                "values apply live, nothing here needs to be \"applied\".")
        settings_btn.clicked.connect(lambda: self._open_nesting_settings())
        # What stays behind in the dialog, tidied for life without the rows
        # that moved: the Formula button is on the ribbon, and two fields
        # lost the checkbox that used to say what they belonged to.
        panel.recommendation_settings_btn.hide()
        for field, text in ((panel.microjoint_spacing, "Microjoint tab spacing"),
                            (panel.label_height, "Label text height")):
            layout = self._layout_holding(field)
            label = layout.labelForField(field) if isinstance(layout, QtWidgets.QFormLayout) else None
            if label is not None:
                label.setText(text)
        self._settings_dialog = None
        self._settings_captions = {}
        self._ribbon_settings = (panel.part_spacing, panel.margin_left, panel.margin_right,
                                 panel.margin_top, panel.margin_bottom, panel.margin_apply_all,
                                 panel.ga_population, panel.ga_generations, panel.ga_optimize_rotations,
                                 panel.ga_parallel, panel.prefer_remnants,
                                 panel.true_joint_stock_optimization, panel.allow_hole_nesting,
                                 panel.hole_clearance, panel.microjoints_enabled, panel.microjoint_width,
                                 panel.common_line_enabled, panel.label_enabled)

        nest = nesting.add_group("Run")
        run_btn = nest.add_large("run-nesting", "\u25b6", "Run\nNesting", primary=True)
        stop_btn = nest.add_large("stop-nesting", "\u25a0", "Stop")
        run_btn.setToolTip("Preview only -- safe to click repeatedly. Does not touch the inventory file on disk. "
                           "Enabled once the parts pass their readiness check (Parts tab).")
        stop_btn.setToolTip("Stop after the current candidate evaluation and keep the best layout found so far.")
        stop_btn.setEnabled(False)
        run_btn.clicked.connect(panel._optimize_ordering)
        stop_btn.clicked.connect(panel._request_stop)

        # -------------------------------------------------------- Output
        output_tab = ribbon.add_tab("Output")
        output = output_tab.add_group("Export")
        export_btn = output.add_large("export-dxf", "\u2b07", "Export\nDXF")
        export_btn.setToolTip("Write every nested sheet to DXF for the cutting machine.")
        export_btn.clicked.connect(panel._export_dxf)
        report_btn = output.add_large("report", "\u25a6", "Report")
        report_btn.setToolTip("Export a self-contained HTML nesting report of the current layout "
                              "(job totals, per-sheet stats, and a rendered image of every sheet). "
                              "Also available under File.")
        report_btn.clicked.connect(panel._export_report)
        panel.label_enabled.setText("Part name labels")
        labels_row = output.add_widget(panel.label_enabled)

        after_cut = output_tab.add_group("After Cutting", launcher=True)
        after_cut.launcher_clicked.connect(lambda: self._open_nesting_settings("Stock"))
        commit_btn = after_cut.add_large("commit-inventory", "\u2713", "Commit to\nInventory")
        commit_btn.setToolTip("Deduct the sheets this layout uses from the inventory file (and add "
                              "its remnants). Needs a loaded inventory and a fresh Run Nesting.")
        commit_btn.clicked.connect(panel._commit_inventory)

        # The panel owns when committing is allowed (its own commit_btn's
        # enabled state, flipped all through a run); mirror it.
        self._commit_mirror = _EnabledMirror(panel.commit_btn, commit_btn, self)

        # ---------------------------------------------------------- View
        view = ribbon.add_tab("View")
        sheet = view.add_group("Sheet")
        prev_btn = sheet.add_small("prev-sheet", "\u2039", "Previous")
        prev_btn.clicked.connect(panel._prev_sheet)
        next_btn = sheet.add_small("next-sheet", "\u203a", "Next")
        next_btn.clicked.connect(panel._next_sheet)
        sheet.new_column()
        zoom_in_btn = sheet.add_small("zoom-in", "+", "Zoom in")
        zoom_in_btn.clicked.connect(lambda: panel.preview.zoom_in_step(0.25))
        zoom_out_btn = sheet.add_small("zoom-out", "\u2212", "Zoom out")
        zoom_out_btn.clicked.connect(lambda: panel.preview.zoom_in_step(-0.25))
        fit_btn = sheet.add_small("zoom-fit", "\u25a1", "Fit sheet")
        fit_btn.clicked.connect(lambda: panel.zoom_spin.setValue(1.0))
        measure_btn = sheet.add_large("measure", "\u2194", "Measure", checkable=True)
        measure_btn.setToolTip(panel.measure_toggle.toolTip())
        self._sync_toggle(panel.measure_toggle, measure_btn)

        show = view.add_group("Panes")
        parts_btn = show.add_large("parts", "\u25a4", "Parts\nList", checkable=True)
        parts_btn.setToolTip("Show/hide the Parts list pane.")
        self._sync_dock(self.parts_dock, parts_btn)
        results_btn = show.add_large("layout-results", "\u25a4", "Layout\nResults", checkable=True)
        results_btn.setToolTip(panel.results_toggle.toolTip())
        self._sync_toggle(panel.results_toggle, results_btn)
        manual_btn = show.add_large("manual-adjust", "\u271b", "Manual\nAdjust", checkable=True)
        manual_btn.setToolTip(panel.manual_toggle.toolTip())
        self._sync_toggle(panel.manual_toggle, manual_btn)
        table_btn = show.add_small("preflight", "\u2637", "Part table", checkable=True)
        self._sync_dock(self.table_dock, table_btn)
        details_btn = show.add_small("parts", "\u25a3", "Part details", checkable=True)
        self._sync_dock(self.details_dock, details_btn)
        log_btn = show.add_small("report", "\u2630", "Log", checkable=True)
        self._sync_dock(self.log_dock, log_btn)
        show.new_column()
        reset_btn = show.add_small("zoom-fit", "\u21ba", "Reset layout")
        reset_btn.setToolTip("Put every pane back where it started: Parts on the left, Layout "
                             "Results on the right, Part Table / Part Details / Log tabbed along "
                             "the bottom.")
        reset_btn.clicked.connect(self._reset_layout)

        appearance = view.add_group("Appearance")
        self.dark_mode_btn = appearance.add_large("dark-mode", "\u263e", "Dark\nMode", checkable=True)
        self.dark_mode_btn.setToolTip("Switch between the light and dark theme.")

        # What View > Ribbon Items offers: (key, menu label, widget). The
        # key is what the choice is saved under, so renaming a label later
        # doesn't silently reset anyone's ribbon. The first eight keys are
        # the old single-row toolbar's, kept so saved choices carry over.
        items = [
            ("run", "Run Nesting", run_btn),
            ("stop", "Stop Nesting", stop_btn),
            ("export", "Export DXF", export_btn),
            ("report", "Report", report_btn),
            ("recommend", "Suggest cut settings", recommend_btn),
            ("mix_stock", "Mix stock sizes", mix_row),
            ("labels", "Part name labels", labels_row),
            ("settings", "Advanced Nesting Settings", settings_btn),
            ("new_job", "New job", new_btn),
            ("open_job", "Open job", open_btn),
            ("save_job", "Save job", save_btn),
            ("add_parts", "Add Parts", add_btn),
            ("check", "Check readiness", check_btn),
            ("remove_part", "Remove selected part", remove_btn),
            ("spacing", "Part spacing", spacing_row),
            ("margins", "Sheet margins", margins_row),
            ("margin_all", "Same margin on all edges", margin_all_row),
            ("population", "Population", population_row),
            ("generations", "Generations", generations_row),
            ("search_rotations", "Search rotations", rotations_row),
            ("parallel", "Use all CPU cores", parallel_row),
            ("prefer_remnants", "Prefer remnants first", remnants_row),
            ("part_in_hole", "Nest parts in holes", in_hole_row),
            ("hole_clearance", "Hole clearance", clearance_row),
            ("microjoints", "Microjoints", microjoints_row),
            ("tab_width", "Tab width", tab_width_row),
            ("common_edge", "Common-edge cutting", common_row),
            ("formula", "Recommendation formula", formula_btn),
            ("load_inventory", "Load inventory", load_btn),
            ("currency", "Currency", currency_row),
            ("clear_inventory", "Clear inventory", clear_btn),
            ("commit", "Commit to inventory", commit_btn),
            ("prev_sheet", "Previous sheet", prev_btn),
            ("next_sheet", "Next sheet", next_btn),
            ("zoom_in", "Zoom in", zoom_in_btn),
            ("zoom_out", "Zoom out", zoom_out_btn),
            ("zoom_fit", "Fit sheet", fit_btn),
            ("measure", "Measure", measure_btn),
            ("results", "Layout results", results_btn),
            ("manual", "Manual adjustment", manual_btn),
            ("pane_parts", "Parts list pane", parts_btn),
            ("pane_table", "Part table pane", table_btn),
            ("pane_details", "Part details pane", details_btn),
            ("pane_log", "Log pane", log_btn),
            ("reset_layout", "Reset layout", reset_btn),
            ("dark_mode", "Dark mode", self.dark_mode_btn),
        ]
        self.ribbon_items = items

        # Locked until the parts pass their readiness check (and while a
        # run is going); the Job menu's Run/Stop mirror the same gate.
        self._gated_buttons = (run_btn, export_btn, report_btn)
        # Anything that changes the job, the parts or the stock underneath
        # a running search is locked for the run's duration. The View tab
        # is not: paging and zooming through sheets as they appear is what
        # you WANT to be doing while it runs.
        self._busy_locked = (new_btn, open_btn, save_btn, add_btn, check_btn, remove_btn,
                             recommend_btn, formula_btn, load_btn, clear_btn,
                             settings_btn) + self._ribbon_settings
        self._ribbon_busy = False

        def set_busy(busy):
            self._ribbon_busy = busy
            _refresh_ribbon_gates()

        def _refresh_ribbon_gates(*_):
            busy = self._ribbon_busy
            gate = panel.preflight_pass
            stop_btn.setEnabled(busy)
            run_action = getattr(self, "run_action", None)
            stop_action = getattr(self, "stop_action", None)
            if run_action is not None:
                run_action.setEnabled(not busy and gate)
            if stop_action is not None:
                stop_action.setEnabled(busy)
            for button in self._gated_buttons:
                button.setEnabled(not busy and gate)
            for widget in self._busy_locked:
                widget.setEnabled(not busy)
            self._commit_mirror.set_blocked(busy)

        panel.busy_changed.connect(set_busy)
        panel.preflight_ready.connect(_refresh_ribbon_gates)
        _refresh_ribbon_gates()
        return ribbon

    def _check_readiness(self):
        """The readiness check shows its verdict above the Part Table, so
        it brings the Parts tab (where that table is) forward."""
        self.ribbon.set_current_index(self._tab_index("Parts"))
        self.parts_panel._run_preflight()

    @staticmethod
    def _layout_holding(widget):
        """The layout (anywhere under the widget's parent) that manages
        `widget`, or None."""
        parent = widget.parentWidget()
        if parent is None:
            return None
        for layout in [parent.layout()] + parent.findChildren(QtWidgets.QLayout):
            if layout is not None and layout.indexOf(widget) >= 0:
                return layout
        return None

    def _take_setting(self, widget):
        """Take one setting widget out of its panel settings box, row label
        and all, so the ribbon can adopt it -- the box keeps the rest of
        its rows and goes into Advanced Nesting Settings as before."""
        layout = self._layout_holding(widget)
        if isinstance(layout, QtWidgets.QFormLayout):
            row, _role = layout.getWidgetPosition(widget)
            label = layout.labelForField(widget)
            result = layout.takeRow(row)
            if label is not None:
                label.hide()
            del result
        elif layout is not None:
            layout.removeWidget(widget)
        # Reparented to the window (not left orphaned) until the ribbon
        # adopts it -- a parentless widget would be shown as its own window.
        widget.setParent(self)
        if isinstance(widget, QtWidgets.QAbstractSpinBox):
            # "12.50 mm" fits in this; left to size itself a spin box sizes
            # for its widest possible value, and the Nesting tab (settings
            # AND Run) ran out of room and cut its buttons' labels off.
            widget.setMaximumWidth(_RIBBON_FIELD_PX)
        return widget

    def _take_margin_grid(self):
        """The four sheet-margin spins, taken out of Layout basics as the
        2x2 Up/Down, Left/Right grid they already sit in there."""
        panel = self.panel
        grid = self._layout_holding(panel.margin_top)
        form = None
        for layout in panel.margin_top.parentWidget().findChildren(QtWidgets.QFormLayout):
            if layout.getLayoutPosition(grid)[0] >= 0:
                form = layout
        if form is not None:
            row, _role = form.getLayoutPosition(grid)
            label = form.labelForField(grid)
            form.takeRow(row)
            if label is not None:
                label.hide()
        for spin in (panel.margin_top, panel.margin_bottom, panel.margin_left, panel.margin_right):
            spin.setMaximumWidth(_RIBBON_FIELD_PX)
        block = QtWidgets.QWidget(self)
        block.setObjectName("RibbonFieldRow")
        grid.setParent(None)
        block.setLayout(grid)
        grid.setContentsMargins(4, 0, 0, 0)
        grid.setVerticalSpacing(0)
        return block

    @staticmethod
    def _sync_dock(dock, button):
        """Keep a checkable button in step with a pane's visibility. Not
        _sync_toggle() on the pane's toggleViewAction: that action shows
        and hides its pane when TRIGGERED, and merely setting it checked
        (what a mirrored button would do) leaves the pane where it was."""
        button.setChecked(not dock.isHidden())
        dock.toggleViewAction().toggled.connect(button.setChecked)
        button.toggled.connect(dock.setVisible)

    @staticmethod
    def _sync_toggle(source, mirror):
        """Keep two checkable buttons in step (toggled only fires on an
        actual change, so the pair can't ping-pong)."""
        mirror.setChecked(source.isChecked())
        source.toggled.connect(mirror.setChecked)
        mirror.toggled.connect(source.setChecked)

    # ---------------------------------------------------------- settings

    def _open_nesting_settings(self, section=None):
        """Shows the "Advanced Nesting Settings" dialog -- built once, lazily, on
        first open, out of every settings group box the panel constructed
        MINUS the three widgets the ribbon adopted directly (see
        _build_ribbon). Non-modal, same reasoning as the panel's own
        Recommendation-formula dialog: every field is read fresh at Run
        Nesting/export time, so there's nothing to "apply" -- leaving this
        open while working the rest of the window is fine."""
        if self._settings_dialog is None:
            self._settings_dialog = self._build_settings_dialog()
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()
        # A ribbon group's corner launcher opens the dialog at its own
        # section rather than at the top of the whole list.
        caption = self._settings_captions.get(section)
        if caption is not None:
            scroll = self._settings_dialog.findChild(QtWidgets.QScrollArea)
            QtCore.QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(caption.y()))

    @staticmethod
    def _has_visible_rows(box):
        return any(not child.isHidden()
                   for child in box.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly))

    def _build_settings_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Advanced Nesting Settings")
        # Same guarantee MainWindow itself makes up front: whatever the WM's
        # idea of a dialog's buttons is, this window explicitly asks for the
        # full standard control set -- minimize + maximize + close -- rather
        # than trusting a platform default that some window managers thin out
        # (see the identical comment on __init__'s setWindowFlags).
        dialog.setWindowFlags(dialog.windowFlags()
                              | QtCore.Qt.WindowMinimizeButtonHint
                              | QtCore.Qt.WindowMaximizeButtonHint
                              | QtCore.Qt.WindowCloseButtonHint)
        # Wide enough for the widest settings row at the theme's control
        # padding -- at the old 460 the spin boxes' own arrows were clipped
        # off the right edge and the whole page scrolled sideways.
        dialog.resize(600, 780)
        # A floor as well as a default: this window's size is restored from
        # the last session, and a geometry saved before the redesign would
        # otherwise reopen too narrow for its own controls.
        dialog.setMinimumWidth(560)
        outer = QtWidgets.QVBoxLayout(dialog)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        scroll = QtWidgets.QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setSpacing(10)

        # Same category order as the old ribbon settings tabs, so this reads
        # like the direct continuation of that layout -- just off the
        # always-visible ribbon and into a window you open when setting up
        # for a new material/machine, not every time you nest.
        for title in _RIBBON_SETTINGS_TABS:
            # A box whose every row moved onto the ribbon (Layout
            # optimization, say) would be an empty frame with a title.
            boxes = [box for box in self.panel.settings_boxes.get(title, ())
                     if self._has_visible_rows(box)]
            if not boxes:
                continue
            caption = QtWidgets.QLabel(title.upper())
            caption.setObjectName("SectionCaption")
            content_layout.addWidget(caption)
            self._settings_captions[title] = caption
            for box in boxes:
                content_layout.addWidget(box)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_btn)
        outer.addLayout(close_row)
        # Same size/position the user last left it in -- reopening a
        # preferences window in a fresh default spot every session is the
        # sort of amnesia the rest of this file's autosave policy avoids.
        persistence.restore_dialog_geometry(self, "nesting_settings", dialog)
        return dialog

    # ------------------------------------------------------------ menu bar

    def _build_menu_bar(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        file_menu.addAction("New Job", self._new_job, QtGui.QKeySequence.StandardKey.New)
        file_menu.addSeparator()
        file_menu.addAction("Open Job...", self.panel._open_job, QtGui.QKeySequence.StandardKey.Open)
        file_menu.addAction("Save Job", self.panel._save_job_current, QtGui.QKeySequence.StandardKey.Save)
        file_menu.addAction("Save Job As...", self.panel._save_job_as,
                            QtGui.QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        file_menu.addAction("Add Parts...", self.panel._rescan, QtGui.QKeySequence("Ctrl+I"))
        file_menu.addAction("Load Inventory...", self.panel._load_inventory)
        file_menu.addSeparator()
        file_menu.addAction("Export Nesting Report...", self.panel._export_report)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, QtGui.QKeySequence("Ctrl+Q"))

        edit_menu = menu.addMenu("&Edit")
        edit_menu.addAction("Remove Selected Part", self.panel._remove_selected_parts)
        edit_menu.addAction("Remove Selected Stock Row", self.stock_panel._delete_selected_rows)

        # Every ribbon button can be hidden (View > Nesting Ribbon), so the
        # two actions that RUN the thing need a home that can't be hidden.
        # They were ribbon-only before, which also left the app with no
        # keyboard way to start a nest.
        job_menu = menu.addMenu("&Job")
        self.run_action = job_menu.addAction("Run Nesting", self.panel._optimize_ordering,
                                             QtGui.QKeySequence("Ctrl+R"))
        self.stop_action = job_menu.addAction("Stop Nesting", self.panel._request_stop)
        self.stop_action.setEnabled(False)
        job_menu.addSeparator()
        job_menu.addAction("Export DXF...", self.panel._export_dxf)
        job_menu.addAction("Commit to Inventory...", self.panel._commit_inventory)

        view_menu = menu.addMenu("&View")
        view_menu.addAction("Sheet Workspace", self._show_sheet_workspace, QtGui.QKeySequence("Ctrl+1"))
        view_menu.addAction("Stock Table", self._show_stock_table, QtGui.QKeySequence("Ctrl+2"))
        view_menu.addSeparator()
        panes_menu = view_menu.addMenu("Panes")
        for dock in (self.parts_dock, self.results_dock, self.table_dock, self.details_dock, self.log_dock):
            panes_menu.addAction(dock.toggleViewAction())
        panes_menu.addSeparator()
        panes_menu.addAction("Reset Layout", self._reset_layout)
        view_menu.addSeparator()
        view_menu.addAction("Previous Sheet", self.panel._prev_sheet, QtGui.QKeySequence("Ctrl+PgUp"))
        view_menu.addAction("Next Sheet", self.panel._next_sheet, QtGui.QKeySequence("Ctrl+PgDown"))
        view_menu.addSeparator()
        self._build_ribbon_menu(view_menu)

        settings_menu = menu.addMenu("&Settings")
        self.dark_mode_action = settings_menu.addAction("Dark Mode")
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.toggled.connect(self._set_theme)
        self._sync_toggle(self.dark_mode_action, self.dark_mode_btn)
        settings_menu.addSeparator()
        settings_menu.addAction("Advanced Nesting Settings...", lambda: self._open_nesting_settings(),
                                QtGui.QKeySequence("Ctrl+,"))
        settings_menu.addAction("Recommendation Formula...", self.panel._open_recommendation_settings)

    def _build_ribbon_menu(self, view_menu):
        """View > Ribbon Items: one tick per ribbon item, grouped under its
        ribbon tab, so the ribbon carries what this shop actually uses.
        Choices are saved with the rest of the session (persistence.py)."""
        ribbon_menu = view_menu.addMenu("Ribbon Items")
        self.ribbon_actions = {}
        by_page = {}
        for key, label, widget in self.ribbon_items:
            by_page.setdefault(self.ribbon.page_of(widget), []).append((key, label))
        for index, page in enumerate(self.ribbon.pages):
            ribbon_menu.addSection(page.title)
            for key, label in by_page.get(index, []):
                action = ribbon_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(True)
                action.toggled.connect(
                    lambda checked, k=key: self._set_ribbon_item_visible(k, checked))
                self.ribbon_actions[key] = action
        ribbon_menu.addSeparator()
        ribbon_menu.addAction("Show All", self._show_all_ribbon_items)

    def _set_ribbon_item_visible(self, key, visible):
        for item_key, _label, widget in self.ribbon_items:
            if item_key == key:
                widget.setVisible(visible)
                break
        self._refresh_ribbon_separators()

    def _show_all_ribbon_items(self):
        for action in self.ribbon_actions.values():
            action.setChecked(True)

    def _refresh_ribbon_separators(self):
        # A group with every item hidden goes, and so does any rule left
        # dividing nothing (see RibbonPage.refresh_visibility).
        self.ribbon.refresh_visibility()

    def _set_theme(self, dark):
        # The status bar used to be restyled by hand here; it is part of
        # the app-wide sheet now (theme.py, `QStatusBar`), so a theme swap
        # is the stylesheet plus the ribbon's light/dark icon variants and
        # nothing else.
        theme.apply_theme(QtWidgets.QApplication.instance(), dark)
        self.ribbon.set_theme(dark)
        # persistence.py restores the menu action with its signals blocked,
        # so the ribbon's copy of the toggle is put in step here instead.
        self.dark_mode_btn.blockSignals(True)
        self.dark_mode_btn.setChecked(bool(dark))
        self.dark_mode_btn.blockSignals(False)
        for panel in (self.parts_panel, self.stock_panel):
            if hasattr(panel, "on_theme_changed"):
                panel.on_theme_changed()

    # --------------------------------------------------------------- other

    def _new_job(self):
        # Just clear state -- don't call panel._rescan(), since
        # FilePartSource.scan() always opens a file picker and "New Job"
        # should only reset, not immediately prompt for more files.
        self.part_source.reset()
        self.panel._extracted = {}
        part_colors.reset()
        self.panel.table.setRowCount(0)
        self.panel._job_path = None  # no longer tied to whatever job was open before
        self._refresh_title()
        # No stale results from before this job started -- Layout Results
        # (and the sheet preview/selector) are driven straight off
        # self.panel.sheets now, so it must actually be cleared here, not
        # just have its old browsable-candidates list reset.
        self.panel.sheets = []
        self.panel.sheet_dims = []
        self.panel.sheet_job_label = []
        self.panel.sheet_prices = []
        self.panel.used_stock = []
        self.panel.unplaced = []
        self.panel.current_sheet_index = 0
        self.panel.export_btn.setEnabled(False)
        self.panel.commit_btn.setEnabled(False)
        self.panel._show_sheet()
        self.panel._refresh_layout_list()
        self.panel._log("Started a new job.")
        self.panel._update_readiness()
        self.panel.parts_changed.emit()