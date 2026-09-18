"""
main_window.py
--------------
The native app's top-level window: a File/Edit/View/Settings menu bar, a
main tab strip (Nesting / Stock / Parts) on TOP of a tabbed ribbon (see
ribbon.py) driving the shared `nesting_widgets.NestingPanel` (backed by a
`FilePartSource` instead of a live FreeCAD document -- see
nesting_widgets.py's module docstring for what the panel itself does), a
Stock tab for viewing/editing the loaded inventory (stock_panel.py), a
Parts tab with large per-part renders (parts_panel.py), and a light/dark
theme (theme.py, from the user-supplied alphanest-icon-pack).

The panel is built with `show_actions=False` and `settings_in_ribbon=True`:
its own scan/rescan button, bottom action row, and settings/stock/GA group
boxes are all still constructed (so its internal logic is untouched) but
not shown -- the group boxes are reparented into the ribbon's settings
tabs, and the menu bar + ribbon are this window's real controls.
"""

from PySide6 import QtCore, QtGui, QtWidgets

import part_colors
from nesting_widgets import NestingPanel
from native_app import theme
from native_app import persistence
from native_app.file_part_source import FilePartSource
from native_app.parts_panel import PartsPanel
from native_app.ribbon import Ribbon, RibbonButton
from native_app.stock_panel import StockPanel

# The panel's settings group boxes, grouped by the ribbon tab that hosts
# them in the native app (the FreeCAD workbench keeps them in the panel).
_RIBBON_SETTINGS_TABS = ("Layout", "Rules", "Cutting", "Stock", "Export")


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

        self.part_source = FilePartSource()
        self.panel = NestingPanel(self.part_source, self, show_actions=False, settings_in_ribbon=True)
        self.panel.close_requested.connect(self.close)

        self.ribbon = self._build_ribbon()
        self.stock_panel = StockPanel(self.panel, self)
        self.parts_panel = PartsPanel(self.panel, self)

        # The Nesting page stacks the ribbon above the panel, so the main
        # tab strip sits physically on top of the ribbon, and the settings
        # that used to scroll above the parts table now live in the ribbon.
        self.nesting_page = QtWidgets.QWidget()
        nesting_layout = QtWidgets.QVBoxLayout(self.nesting_page)
        nesting_layout.setContentsMargins(0, 0, 0, 0)
        nesting_layout.setSpacing(0)
        nesting_layout.addWidget(self.ribbon)
        nesting_layout.addWidget(self.panel, 1)

        # Tab order follows the job's workflow: parts are imported and
        # preflight-checked first (Parts), stock is then prepared (Stock),
        # and nesting is the last, output-producing step (Nesting).
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.parts_panel, "Parts")
        self.tabs.addTab(self.stock_panel, "Stock")
        self.tabs.addTab(self.nesting_page, "Nesting")

        central = QtWidgets.QWidget()
        central_layout = QtWidgets.QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self._build_menu_bar()
        self._set_theme(False)  # applied once at startup for a consistent look from frame 1
        # Last session's settings/inventory/tab/dark-mode/geometry are
        # restored after everything above exists; saved again on close.
        persistence.restore_preferences(self)
        self._first_preferences_loaded = True

    def closeEvent(self, event):
        # Save the session before teardown: settings widgets are still
        # alive here, so values read back the way the user left them.
        if getattr(self, "_first_preferences_loaded", False):
            persistence.save_preferences(self)
        super().closeEvent(event)

    # --------------------------------------------------------------- ribbon

    def _build_ribbon(self):
        ribbon = Ribbon(self)

        run_btn = ribbon.add_button("run-nesting", "▶", "Run Nesting")
        stop_btn = ribbon.add_button("stop-nesting", "■", "Stop Nesting")
        run_btn.setToolTip("Preview only -- safe to click repeatedly. Does not touch the inventory file on disk. "
                           "Enabled once the parts pass their readiness check in the Parts tab.")
        stop_btn.setToolTip("Stop after the current candidate evaluation and keep the best layout found so far.")
        stop_btn.setEnabled(False)
        run_btn.clicked.connect(self.panel._optimize_ordering)
        stop_btn.clicked.connect(self.panel._request_stop)

        ribbon.add_separator()
        export_btn = ribbon.add_button("export-dxf", "⬇", "Export DXF")
        export_btn.clicked.connect(self.panel._export_dxf)
        report_btn = ribbon.add_button("report", "▦", "Report")
        report_btn.setToolTip("Export a self-contained HTML nesting report of the current layout "
                              "(job totals, per-sheet stats, and a rendered image of every sheet). "
                              "Also available under File.")
        report_btn.clicked.connect(self.panel._export_report)
        # The buttons that stay locked until Parts-tab preflight passes.
        # Inventory file operations (Load/Clear/Commit) live on the Stock
        # tab now, not in the ribbon. The Microjoints toggle lives on its
        # checkbox in Cutting settings, not as a toolbar button.
        self._gated_buttons = (run_btn, export_btn, report_btn)

        # Settings group boxes, built by the panel but reparented here.
        # The ribbon has no sub-tabs: every box lands in the flow, under its
        # section caption, in the workflow order defined by
        # _RIBBON_SETTINGS_TABS. Sections wrap onto new rows as the window
        # narrows, so there is never any horizontal scrolling.
        for title in _RIBBON_SETTINGS_TABS:
            boxes = self.panel.settings_boxes.get(title)
            if boxes:
                ribbon.add_settings_tab(title, boxes)

        self._ribbon_busy = False

        def set_busy(busy):
            self._ribbon_busy = busy
            _refresh_ribbon_gates()

        def set_preflight_ready(ok):
            _refresh_ribbon_gates()

        def _refresh_ribbon_gates():
            # Run Nesting / Export / Report are locked until the parts pass
            # their readiness check (hosted in the Parts tab); File > New Job
            # stays usable so a bad job can always be cleared.
            # The Stock tab's Commit to Inventory has its own commitments
            # (an inventory must be loaded AND a layout must exist first);
            # menu actions like File > Export Nesting Report still run their
            # own internal _require_preflight() backstop.
            busy = self._ribbon_busy
            gate = self.panel.preflight_pass
            stop_btn.setEnabled(busy)
            for button in ribbon.findChildren(RibbonButton):
                if button is stop_btn:
                    continue
                if button in self._gated_buttons:
                    button.setEnabled(not busy and gate)
                else:
                    button.setEnabled(not busy)

        self.panel.busy_changed.connect(set_busy)
        self.panel.preflight_ready.connect(set_preflight_ready)
        _refresh_ribbon_gates()
        return ribbon

    # ------------------------------------------------------------ menu bar

    def _build_menu_bar(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        file_menu.addAction("New Job", self._new_job)
        file_menu.addSeparator()
        file_menu.addAction("Open Job...", self.panel._open_job)
        file_menu.addAction("Save Job", self.panel._save_job_current)
        file_menu.addAction("Save Job As...", self.panel._save_job_as)
        file_menu.addSeparator()
        file_menu.addAction("Add Parts...", self.panel._rescan)
        file_menu.addAction("Load Inventory...", self.panel._load_inventory)
        file_menu.addSeparator()
        file_menu.addAction("Export Nesting Report...", self.panel._export_report)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        edit_menu = menu.addMenu("&Edit")
        edit_menu.addAction("Remove Selected Part", self.panel._remove_selected_parts)
        edit_menu.addAction("Remove Selected Stock Row", self.stock_panel._delete_selected_rows)

        view_menu = menu.addMenu("&View")
        view_menu.addAction("Nesting Tab", lambda: self.tabs.setCurrentWidget(self.nesting_page))
        view_menu.addAction("Stock Tab", lambda: self.tabs.setCurrentWidget(self.stock_panel))
        view_menu.addAction("Parts Tab", lambda: self.tabs.setCurrentWidget(self.parts_panel))
        view_menu.addSeparator()
        view_menu.addAction("Previous Sheet", self.panel._prev_sheet)
        view_menu.addAction("Next Sheet", self.panel._next_sheet)

        settings_menu = menu.addMenu("&Settings")
        self.dark_mode_action = settings_menu.addAction("Dark Mode")
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.toggled.connect(self._set_theme)

    def _set_theme(self, dark):
        theme.apply_theme(QtWidgets.QApplication.instance(), dark)
        self.ribbon.set_theme(dark)

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
        self.panel.layout_candidates = []
        self.panel._refresh_layout_list()
        self.panel._log("Started a new job.")
        self.panel._update_readiness()
        self.panel.parts_changed.emit()