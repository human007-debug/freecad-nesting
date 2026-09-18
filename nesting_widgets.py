"""
nesting_widgets.py
-------------------
The nesting UI itself (settings, parts table, sheet preview, GA search,
stock inventory, DXF export) as a plain `QWidget`, independent of both
FreeCAD and any one Qt binding -- shared between `freecad_workbench/
nesting_panel.py` (a thin FreeCAD-doc-scanning adapter around this) and
`native_app/` (a thin file-import adapter around this), so the two front
ends can't drift apart on the actual nesting logic.

Binding-agnostic: FreeCAD's own `PySide` module is a compat shim over
whatever Qt binding it bundles (PySide6 as of FreeCAD 1.1); a standalone
install has no such shim, just a plain `pip install PySide6`. Falling back
from one import to the other lets the exact same widget code run in both
places.

Doc-source-agnostic: `NestingPanel` doesn't know how to find parts on its
own -- it's handed a `part_source` (an object with `.scan_label`, the text
for its scan/rescan button, and `.scan(kfactor, tolerance) -> (extracted,
log_lines)`, where `extracted` is `{label: {"outer", "holes", "thickness",
"method"}}`, the same shape `freecad_extract.extract_part()` returns).
`freecad_workbench/nesting_panel.py`'s `FreeCADDocSource` scans a live
FreeCAD document; `native_app/file_part_source.py`'s `FilePartSource` scans
DXF/JSON/`.FCStd` files picked from disk. A source may also set
`scan_on_init = False` to opt out of the automatic scan `NestingPanel`
otherwise does on construction -- `FilePartSource` does this, since its
scan pops a modal file picker and shouldn't fire before the window is even
shown. `kfactor`/`tolerance` are threaded
through `scan()` rather than read via a getter closure into the panel,
since a source is constructed before the panel that owns those spinboxes
exists -- passing them as call arguments sidesteps that ordering problem
entirely (sources that don't need them, like DXF/JSON import, just ignore
them).
"""

import base64
import copy
import csv
import hashlib
import datetime
import json
import os
import random
import time

try:
    from PySide import QtCore, QtGui, QtWidgets   # FreeCAD's own compat shim
except ImportError:
    from PySide6 import QtCore, QtGui, QtWidgets  # standalone install

import geometry
import nester
import nfp
import genetic
import dxf_writer
import inventory as inv
import microjoints as mj
import commonline
import part_colors
import remnant as rem


SHEET_FILL = QtGui.QColor("#3d6690")
SHEET_STROKE = QtGui.QColor("#223a52")
COMMON_EDGE_COLOR = QtGui.QColor("#ff4fa0")

_MAX_LAYOUT_CANDIDATES = 8

# Display-only -- Price/kg, Scrap price, and every computed financial figure
# are all just plain floats underneath (no currency-aware math anywhere);
# this only decides which symbol prefixes them in the UI and report.
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
DEFAULT_CURRENCY = "INR"


class SheetPreview(QtWidgets.QWidget):
    """Draws one nested sheet: boundary, each placed part's outer contour
    filled, holes cut out as true holes via QPainterPath subtraction.

    Clicking a part emits `clicked(index)` (index into `self.placed_parts`,
    or -1 if the click hit empty sheet) -- for manual per-part editing (see
    NestingPanel's part_selector/_apply_manual_edit)."""

    clicked = QtCore.Signal(int)
    coords = QtCore.Signal(object)  # (x, y) in sheet mm while the mouse moves, or None when it leaves
    zoom_changed = QtCore.Signal(float)  # new zoom factor, from the mouse wheel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheet_w = 1.0
        self.sheet_h = 1.0
        self.placed_parts = []
        self.microjoint_cfg = None
        self.common_line_cfg = None
        self._scale = 0.0
        self._off_x = 0.0
        self._off_y = 0.0
        # 1.0 = sheet fits the widget exactly (the popular case); anything
        # above magnifies the fit scale, cropping at the widget edge while
        # the sheet stays centered. The whole paint/hit-test/chord-display
        # math already runs through self._scale, so only this factor and the
        # centered offsets have to change -- no separate panned view needed.
        self._zoom = 1.0
        self.setMouseTracking(True)
        # Kept small on purpose (not 320x320): the sheet preview is the
        # largest single contributor to the whole window's layout minimum
        # size. A big minimum here makes the window open at nearly full
        # screen height, which Linux window managers (especially
        # Cinnamon/Muffin) then treat as a fixed-size window -- the
        # maximize button and resize handles silently disappear. The
        # preview scales whatever it's given, so a small floor just loses
        # headroom, never clarity. Kept small so the whole panel (and thus
        # the app) can be shrunk well below a 768px-tall screen -- a near-
        # full-screen minimum is what makes Cinnamon/Muffin drop the resize
        # handles entirely.
        self.setMinimumSize(130, 130)

    def set_sheet(self, w, h, placed_parts, microjoint_cfg=None, common_line_cfg=None):
        self.sheet_w = max(float(w), 1e-6)
        self.sheet_h = max(float(h), 1e-6)
        self.placed_parts = placed_parts
        self.microjoint_cfg = microjoint_cfg
        self.common_line_cfg = common_line_cfg
        self.update()

    def set_zoom(self, factor):
        self._zoom = max(0.5, min(4.0, float(factor)))
        self.update()

    def zoom_in_step(self, step):
        new_zoom = round((self._zoom + step) * 10) / 10
        self._zoom = max(0.5, min(4.0, new_zoom))
        self.zoom_changed.emit(self._zoom)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.zoom_in_step(0.1 if delta > 0 else -0.1)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        margin = 6
        avail_w = self.width() - 2 * margin
        avail_h = self.height() - 2 * margin
        if avail_w <= 0 or avail_h <= 0:
            return
        scale = min(avail_w / self.sheet_w, avail_h / self.sheet_h)
        # Centered magnification: at zoom>1 the sheet is drawn larger than
        # the widget and clipped at the edges, keeping the sheet's center in
        # view. Everything downstream (hit-testing, chord readout) is driven
        # by the stored transform, so this stays exact.
        scale *= self._zoom
        off_x = margin + (avail_w - self.sheet_w * scale) / 2
        off_y = margin + (avail_h - self.sheet_h * scale) / 2
        # Stored so mousePressEvent() can invert exactly this transform for
        # click hit-testing, without duplicating the margin/scale math.
        self._scale, self._off_x, self._off_y = scale, off_x, off_y

        def to_screen(x, y):
            # sheet coords are Y-up; screen is Y-down.
            return QtCore.QPointF(off_x + x * scale, off_y + (self.sheet_h - y) * scale)

        painter.setPen(QtGui.QPen(SHEET_STROKE, 2))
        painter.setBrush(SHEET_FILL)
        painter.drawPolygon(QtGui.QPolygonF([
            to_screen(0, 0), to_screen(self.sheet_w, 0),
            to_screen(self.sheet_w, self.sheet_h), to_screen(0, self.sheet_h),
        ]))

        for pp in self.placed_parts:
            painter.setBrush(part_colors.color_for(pp.name))
            if pp.mirrored:
                dashed_pen = QtGui.QPen(part_colors.outline_for(pp.name), 1.5)
                dashed_pen.setStyle(QtCore.Qt.DashLine)
                painter.setPen(dashed_pen)
            else:
                painter.setPen(QtGui.QPen(part_colors.outline_for(pp.name), 1.5))
            path = QtGui.QPainterPath()
            outer = QtGui.QPolygonF([to_screen(x, y) for x, y in pp.points])
            path.addPolygon(outer)
            path.closeSubpath()
            for hole in pp.holes:
                hole_poly = QtGui.QPolygonF([to_screen(x, y) for x, y in hole])
                hole_path = QtGui.QPainterPath()
                hole_path.addPolygon(hole_poly)
                hole_path.closeSubpath()
                path = path.subtracted(hole_path)
            painter.drawPath(path)

        if self.microjoint_cfg:
            # Not the real cut path -- just a marker at each tab gap's
            # midpoint, so tabs are visible in the preview before ever
            # exporting (the real gapped geometry is what write_dxf() emits).
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
            painter.setBrush(QtGui.QColor("#ffffff"))
            for pp in self.placed_parts:
                segments = mj.add_tabs(pp.points, **self.microjoint_cfg)
                if len(segments) < 2:
                    continue  # too small for real tabs -- add_tabs() left it closed, no gaps to mark
                for i in range(len(segments)):
                    gap_start = segments[i][-1]
                    gap_end = segments[(i + 1) % len(segments)][0]
                    mid = ((gap_start[0] + gap_end[0]) / 2, (gap_start[1] + gap_end[1]) / 2)
                    painter.drawEllipse(to_screen(*mid), 3, 3)

        if self.common_line_cfg is not None:
            # Highlights exactly the edges commonline.find_shared_edges()
            # (already used for real by dxf_writer.py's export) would merge
            # into one cut -- a visual preview of the same detection, like
            # LaserNest's on-canvas "Common-edge first-cut" callout.
            _shared_by_part, merged_edges = commonline.find_shared_edges(
                [pp.points for pp in self.placed_parts]
            )
            if merged_edges:
                painter.setPen(QtGui.QPen(COMMON_EDGE_COLOR, 3))
                for p1, p2 in merged_edges:
                    painter.drawLine(to_screen(*p1), to_screen(*p2))
                painter.setPen(QtGui.QPen(COMMON_EDGE_COLOR))
                painter.drawText(margin + 4, self.height() - margin - 4, "Common-edge cutting active")

    def mouseMoveEvent(self, event):
        if self._scale <= 0:
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        x = (pos.x() - self._off_x) / self._scale
        y = self.sheet_h - (pos.y() - self._off_y) / self._scale
        if 0.0 - 1e-6 <= x <= self.sheet_w + 1e-6 and 0.0 - 1e-6 <= y <= self.sheet_h + 1e-6:
            self.coords.emit((x, y))
        else:
            self.coords.emit(None)

    def leaveEvent(self, _event):
        self.coords.emit(None)

    def mousePressEvent(self, event):
        if self._scale <= 0:
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        x = (pos.x() - self._off_x) / self._scale
        y = self.sheet_h - (pos.y() - self._off_y) / self._scale
        for i in range(len(self.placed_parts) - 1, -1, -1):
            if geometry.point_in_polygon((x, y), self.placed_parts[i].points):
                self.clicked.emit(i)
                return
        self.clicked.emit(-1)


class _PartsTable(QtWidgets.QTableWidget):
    """The parts table plus the two deletion affordances a desktop user
    expects but a bare QTableWidget doesn't have: the Delete/Backspace key
    and a right-click "Remove Selected Part" context menu. Without them the
    only way to remove a part is the Edit menu item, which is routinely
    never found -- the reported bug is literally "I can't delete a part
    once I've added it" and the fix is making delete discoverable."""

    delete_requested = QtCore.Signal()

    def __init__(self, rows, columns, parent=None):
        super().__init__(rows, columns, parent)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0:
            return
        # Right-click should mean "this row": select it (replacing any
        # stale selection) so the action below deletes what the user
        # actually clicked, not whatever happened to be selected before.
        self.selectRow(row)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Remove Selected Part", self.delete_requested.emit)
        menu.exec(self.viewport().mapToGlobal(pos))


class NestingPanel(QtWidgets.QWidget):
    """Everything from settings down to the action buttons. See module
    docstring for the `part_source` contract this is built around.

    "Run Nesting" is a pure preview: with an inventory loaded, it works on
    an in-memory deep copy (inventory.run_job()), safe to click repeatedly
    while tweaking quantities/rotations. "Commit to Inventory..." is the
    only action that actually writes anything back to disk
    (inventory.commit_job()) -- deducting the last preview's sheets from
    the loaded inventory file for real and appending to its audit log --
    and asks for confirmation first, since that's not undoable by clicking
    another button.

    "Run Nesting" calls genetic.optimize_order(), searching part ORDERINGS
    with the exact NFP engine as its own fitness function (see genetic.py's
    docstring). With a stock inventory loaded, this re-runs the search once
    per stock-size pass within each material/thickness group's cascade
    (inventory.run_job(..., use_ga=True)); its random seed is fixed up
    front and replayed identically on Commit, so a commit after a GA-based
    preview can't land on a different ordering than what was shown.
    """
    close_requested = QtCore.Signal()

    inventory_changed = QtCore.Signal()  # fires after load/clear/commit -- e.g. for a Stock tab to refresh from
    busy_changed = QtCore.Signal(bool)   # fires around a long GA run -- e.g. for external chrome (a ribbon) to disable itself
    parts_changed = QtCore.Signal()      # fires when the parts table is repopulated/cleared -- e.g. for a Parts tab to refresh from
    operational = QtCore.Signal(str)     # fires whenever the live job stats line is rewritten -- lets out-of-box
                                         # readouts (the ribbon's Layout optimization / Estimate boxes) mirror it
    preflight_ready = QtCore.Signal(bool)  # fires whenever the parts-level preflight gate changes -- lets the
                                           # app disable Run Nesting / Commit / Export / Report until parts pass
    currency_changed = QtCore.Signal(str)  # fires whenever currency_code changes (set_currency(), or a job
                                            # load restoring one) -- e.g. for a Stock tab's currency picker to
                                            # stay in sync when the change came from somewhere else

    def __init__(self, part_source, parent=None, show_actions=True, settings_in_ribbon=False):
        super().__init__(parent)
        self.part_source = part_source
        # When False, the scan/rescan button and the bottom action row are
        # still built (everything that references them, like
        # self.export_btn.setEnabled(...) or the GA run's
        # findChildren(QPushButton) disable-all, keeps working unmodified)
        # but just not added to a visible layout -- for the native app,
        # whose ribbon replaces them visually. The FreeCAD workbench keeps
        # the default, so its behavior is unchanged.
        self.show_actions = show_actions
        # When True (native app), the settings/stock/GA/export group boxes
        # below are still built -- every widget reference keeps working --
        # but they're NOT stacked in the panel's own scroll area; instead
        # they're collected in settings_boxes grouped by ribbon tab, for
        # the window to reparent into its tabbed ribbon. The FreeCAD
        # workbench keeps the default False, so the panel looks exactly as
        # it always has there.
        self.settings_in_ribbon = settings_in_ribbon
        self.settings_boxes = {}  # ribbon tab title -> [QGroupBox, ...], filled in _build_ui

        self._extracted = {}  # label -> extract_part()-shaped dict
        self.sheets = []          # list of PlacedPart lists, one per sheet
        self.sheet_dims = []      # parallel list of (w, h), one per sheet
        self.sheet_job_label = [] # parallel list of "<material>/<thickness>mm" (or "") per sheet
        self.sheet_prices = []    # parallel list of Optional[float] (StockSheet.material_cost()), or None with no inventory
        self.used_stock = []      # parallel list of the StockSheet each sheet was cut from (None with no inventory)
        # Display only -- decides which symbol prefixes Price/kg, Scrap
        # price, and the report's Financials section; every figure
        # underneath is a plain number, no conversion. No widget of its own
        # on this panel -- the native app's Stock tab owns the picker (see
        # StockPanel) and calls set_currency(); the FreeCAD workbench has
        # no stock-editing UI at all yet, so it stays at the default there.
        self.currency_code = DEFAULT_CURRENCY
        self.layout_candidates = []  # last _MAX_CANDIDATES Run Nesting results, newest last
        self._stop_requested = False
        self._search_started_at = None
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._update_search_elapsed)
        self.unplaced = []
        self.current_sheet_index = 0
        self._selected_part_idx = None   # index into the current sheet's placed-part list, for manual editing
        self._inventory_template = None  # List[StockSheet] as loaded from disk, never mutated
        self._inventory_path = None      # where to write back to on Commit
        self._last_run_parts = None      # parts used in the last run -- what Commit actually commits
        self._last_run_used_ga = False   # whether that run went through the GA (needs the same seed replayed on Commit)
        self._last_run_ga_kwargs = None  # ga_kwargs (incl. the seed used) to replay identically on Commit
        self._job_path = None            # where "Save Job" writes to once a job's been saved/opened once
        self.preflight_pass = False       # parts-level gate: Run Nesting/Commit/Export/Report stay disabled until True
        self.preflight_issues = []        # last "why the gate is closed" list, shown in the Parts tab

        self._build_ui()
        # Sources whose scan is free and local (e.g. a live FreeCAD doc)
        # default to scanning immediately; a source whose scan pops a modal
        # file picker (the native app's FilePartSource) opts out via
        # `scan_on_init = False`, so the window doesn't open straight into
        # a blocking dialog.
        if getattr(self.part_source, "scan_on_init", True):
            self._rescan()

    # ------------------------------------------------------------------ UI

    def _stash_settings(self, tab, box, top_layout=None):
        """Attach one settings group box to the panel's own scroll area, or
        -- when built for the ribbon (settings_in_ribbon=True) -- just
        record which ribbon tab it belongs to so the window can reparent it
        there. The box is always constructed either way."""
        self.settings_boxes.setdefault(tab, []).append(box)
        if top_layout is not None:
            top_layout.addWidget(box)

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)

        # A compact, always-visible state summary keeps the next useful
        # action obvious without hiding any of the detailed controls below.
        # This intentionally lives in the shared panel so the native app and
        # FreeCAD workbench communicate the same readiness state.
        readiness = QtWidgets.QFrame()
        readiness.setObjectName("JobReadiness")
        readiness_layout = QtWidgets.QVBoxLayout(readiness)
        readiness_layout.setContentsMargins(12, 5, 12, 5)
        readiness_layout.setSpacing(2)
        self.readiness_title = QtWidgets.QLabel("No parts loaded")
        self.readiness_title.setObjectName("JobReadinessTitle")
        self.readiness_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.readiness_detail = QtWidgets.QLabel("Add parts to begin. The layout will be checked before it can be exported.")
        self.readiness_detail.setObjectName("JobReadinessDetail")
        self.readiness_detail.setWordWrap(True)
        readiness_layout.addWidget(self.readiness_title)
        readiness_layout.addWidget(self.readiness_detail)
        root.addWidget(readiness)
        if not self.show_actions:
            # Pure status banner -- no logic reads it, and every state it
            # reports (no parts, shortfall, or "ready") is also visible in the
            # console log and at export time. Hiding it in the native window
            # sends its ~52px to the sheet preview. The FreeCAD workbench
            # keeps it as its primary at-a-glance state readout.
            readiness.hide()

        # Settings/inventory/GA groups are stacked in a scroll area with a
        # capped height, rather than straight into `root`: three group boxes
        # plus the table/preview/log/button row can add up to more vertical
        # space than a laptop screen has, and a plain QVBoxLayout would just
        # push the button row off the bottom of the screen with no way to
        # reach it. Capping this area's height and letting it scroll
        # internally keeps the button row visible on any screen size.
        # The native app (settings_in_ribbon=True) skips this scroll area
        # entirely: the boxes below are collected in self.settings_boxes
        # instead and reparented into the window's ribbon tabs.
        top_scroll = None
        top_layout = None
        if not self.settings_in_ribbon:
            top_scroll = QtWidgets.QScrollArea()
            top_scroll.setWidgetResizable(True)
            top_scroll.setMaximumHeight(280)
            top_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            top_container = QtWidgets.QWidget()
            top_layout = QtWidgets.QVBoxLayout(top_container)
            top_layout.setContentsMargins(0, 0, 0, 0)
            top_scroll.setWidget(top_container)

        settings_box = QtWidgets.QGroupBox("Layout basics")
        form = QtWidgets.QFormLayout(settings_box)

        self.sheet_w = QtWidgets.QDoubleSpinBox()
        self.sheet_w.setRange(1, 100000)
        self.sheet_w.setValue(1220.0)
        self.sheet_w.setSuffix(" mm")

        self.sheet_h = QtWidgets.QDoubleSpinBox()
        self.sheet_h.setRange(1, 100000)
        self.sheet_h.setValue(2440.0)
        self.sheet_h.setSuffix(" mm")

        self.part_spacing = QtWidgets.QDoubleSpinBox()
        self.part_spacing.setRange(0, 100)
        self.part_spacing.setDecimals(2)
        self.part_spacing.setValue(3.0)
        self.part_spacing.setSuffix(" mm")
        self.part_spacing.setToolTip("Minimum gap enforced between nested parts (and between a part "
                                      "and the sheet edge/margin). Not a cut-path offset -- this engine "
                                      "doesn't compensate exported geometry for beam width, so treat it "
                                      "as a handling/heat-affected-zone clearance, not true kerf.")

        self.assembly_quantity = QtWidgets.QSpinBox()
        self.assembly_quantity.setRange(1, 9999)
        self.assembly_quantity.setValue(1)
        self.assembly_quantity.setToolTip(
            "Number of complete assemblies to make. Each Parts-table quantity is the quantity "
            "required per assembly and is multiplied by this value when nesting."
        )

        def _make_margin_spin():
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0, 1000)
            spin.setDecimals(1)
            spin.setValue(0.0)
            spin.setSuffix(" mm")
            spin.setToolTip("No-cut border along this edge of the sheet (e.g. where a machine "
                             "clamps the material down). 0 = parts can sit flush to this edge.")
            return spin

        self.margin_left = _make_margin_spin()
        self.margin_right = _make_margin_spin()
        self.margin_top = _make_margin_spin()
        self.margin_bottom = _make_margin_spin()

        self.kfactor = QtWidgets.QDoubleSpinBox()
        self.kfactor.setRange(0.0, 2.0)
        self.kfactor.setDecimals(2)
        self.kfactor.setSingleStep(0.05)
        self.kfactor.setValue(0.4)
        self.kfactor.setToolTip("Bend-allowance K-factor used when unfolding bent parts (FreeCAD import only). "
                                 "Change and Rescan to apply.")

        self.tolerance = QtWidgets.QDoubleSpinBox()
        self.tolerance.setRange(0.01, 10.0)
        self.tolerance.setDecimals(2)
        self.tolerance.setValue(0.25)
        self.tolerance.setSuffix(" mm")
        self.tolerance.setToolTip("Max chord deviation when discretizing arcs/circles into straight edges. "
                                   "Change and Rescan to apply.")

        form.addRow("Sheet width", self.sheet_w)
        form.addRow("Sheet height", self.sheet_h)
        form.addRow("Assembly quantity", self.assembly_quantity)
        # In the native app the Assembly quantity belongs on the Parts tab
        # (it's the per-job multiplier for the parts table), so the ribbon
        # doesn't expose this row. It's taken out of this form and hidden --
        # the Parts tab adopts the very same widget, so the panel's
        # reference and job save/load keep working untouched. The FreeCAD
        # workbench keeps the row in this box.
        if self.settings_in_ribbon:
            for row_idx in range(form.rowCount()):
                item = form.itemAt(row_idx, QtWidgets.QFormLayout.FieldRole)
                if item is not None and item.widget() is self.assembly_quantity:
                    result = form.takeRow(row_idx)
                    if result.labelItem is not None and result.labelItem.widget() is not None:
                        result.labelItem.widget().hide()
                    # Reparent to the long-lived panel BEFORE hiding: the
                    # original box is redundant here and can be garbage
                    # collected later, which would delete this spin along
                    # with it. On the panel it survives untouched until the
                    # Parts tab adopts the very same widget.
                    self.assembly_quantity.setParent(self)
                    self.assembly_quantity.hide()
                    break
        form.addRow("Part spacing", self.part_spacing)
        # Margins as a compact 2x2 grid -- Up/Down on the first row,
        # Left/Right on the second -- instead of four spins sharing one
        # wide line: it reads like an edge map, and with "Apply to all" on
        # its own row BELOW the grid (not in the grid's first line) the
        # whole box stays a narrow column that fits the ribbon's tiles.
        margin_grid = QtWidgets.QGridLayout()
        margin_grid.setHorizontalSpacing(4)
        margin_grid.setVerticalSpacing(2)
        for row, (label, spin) in enumerate(
                (("Up", self.margin_top), ("Down", self.margin_bottom),
                 ("Left", self.margin_left), ("Right", self.margin_right))):
            margin_grid.addWidget(QtWidgets.QLabel(label), row // 2, (row % 2) * 2)
            margin_grid.addWidget(spin, row // 2, (row % 2) * 2 + 1)
        self.margin_apply_all = QtWidgets.QCheckBox("Apply to all")
        self.margin_apply_all.setToolTip("When checked, editing any one margin sets all four to match it -- "
                                          "so a symmetric margin never gets left half-updated by mistake.")
        for spin in (self.margin_left, self.margin_right, self.margin_top, self.margin_bottom):
            spin.valueChanged.connect(self._sync_margins)
        form.addRow("Margins", margin_grid)
        form.addRow(self.margin_apply_all)
        form.addRow("K-factor (bend allowance)", self.kfactor)
        form.addRow("Curve tolerance", self.tolerance)
        # Sheet width/height are intentionally NOT shown: nesting always
        # sizes parts against real inventory stock now, so the sheet size
        # UI has nothing to control. The widgets still exist and keep their
        # defaults -- the no-inventory fallback path and job save/load
        # still read/write them -- just nothing in the UI exposes them.
        for _spin in (self.sheet_w, self.sheet_h):
            _label = form.labelForField(_spin)
            if _label is not None:
                _label.setVisible(False)
            _spin.setVisible(False)
        # K-factor and curve tolerance are only meaningful for FreeCAD
        # (.FCStd) imports; the native app's ribbon has nowhere useful to
        # show them, and hiding them saves two rows -- the defaults work
        # for the common case, and any changes live in the job file.
        if self.settings_in_ribbon:
            for _spin in (self.kfactor, self.tolerance):
                _label = form.labelForField(_spin)
                if _label is not None:
                    _label.setVisible(False)
                _spin.setVisible(False)
        self._stash_settings("Layout", settings_box, top_layout)

        rules_box = QtWidgets.QGroupBox("Manufacturing safety rules")
        rules_form = QtWidgets.QFormLayout(rules_box)
        def rule_spin(value, tip):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0.0, 10000.0)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.setSuffix(" mm")
            spin.setToolTip(tip + " Set to 0 to disable this rule.")
            return spin
        self.min_feature = rule_spin(1.0, "Reject contours with an edge shorter than this machine-safe feature size.")
        self.min_hole_opening = rule_spin(1.5, "Reject holes whose bounding opening is smaller than this value.")
        self.min_part_area = QtWidgets.QDoubleSpinBox()
        self.min_part_area.setRange(0.0, 1000000000.0)
        self.min_part_area.setDecimals(2)
        self.min_part_area.setValue(25.0)
        self.min_part_area.setSuffix(" mm²")
        self.min_part_area.setToolTip("Reject parts with net area below this value. Set to 0 to disable.")
        rules_form.addRow("Minimum feature", self.min_feature)
        rules_form.addRow("Minimum hole opening", self.min_hole_opening)
        rules_form.addRow("Minimum net part area", self.min_part_area)
        self.allow_hole_nesting = QtWidgets.QCheckBox("Allow part-in-hole nesting")
        self.allow_hole_nesting.setToolTip("Opt-in: place a part fully inside another part's cutout, subject to clearance.")
        self.hole_clearance = rule_spin(1.0, "Required clearance from a parent hole edge.")
        rules_form.addRow(self.allow_hole_nesting)
        rules_form.addRow("Hole nesting clearance", self.hole_clearance)
        self._stash_settings("Rules", rules_box, top_layout)

        inv_box = QtWidgets.QGroupBox("Stock and material")
        inv_box_layout = QtWidgets.QVBoxLayout(inv_box)
        # Load/Clear stacked top-to-bottom instead of side-by-side -- a
        # narrower button column keeps this box compact next to the other
        # ribbon boxes (and they read as "do A, then B" vertically).
        inv_buttons = QtWidgets.QVBoxLayout()
        inv_buttons.setSpacing(2)
        load_inv_btn = QtWidgets.QPushButton("Load Inventory JSON...")
        load_inv_btn.clicked.connect(self._load_inventory)
        clear_inv_btn = QtWidgets.QPushButton("Clear")
        clear_inv_btn.clicked.connect(self._clear_inventory)
        inv_buttons.addWidget(load_inv_btn)
        inv_buttons.addWidget(clear_inv_btn)
        if not self.show_actions:
            # Native app: inventory FILE operations moved to the Stock tab
            # (Load / Clear / Commit to Inventory all live there now). The
            # buttons are still built+referenced (the FreeCAD workbench,
            # show_actions=True, keeps them in this box); they're just
            # hidden here so the ribbon's Stock tab only carries run
            # settings + the load-status line, not file management.
            load_inv_btn.hide()
            clear_inv_btn.hide()
        self.inventory_status = QtWidgets.QLabel(
            "None loaded -- a fixed 1220 x 2440 mm sheet is used for every part, "
            "regardless of Material."
        )
        self.inventory_status.setWordWrap(True)
        inv_box_layout.addLayout(inv_buttons)
        inv_box_layout.addWidget(self.inventory_status)

        self.prefer_remnants = QtWidgets.QCheckBox("Prefer remnants first")
        self.prefer_remnants.setChecked(True)
        self.prefer_remnants.setToolTip(
            "Always use up a matching remnant (offcut from a past job) before cutting into a full sheet, "
            "even if a full sheet would come out a little cheaper or less wasteful -- burns down scrap "
            "first, which is what the two checkboxes below already do UNLESS this is on: they rank "
            "candidate stock by fewest sheets/lowest cost/least waste, and without this could otherwise "
            "trade away a perfectly usable remnant for a full sheet that merely scores a little better on "
            "that measure. Uncheck to let those two rank purely on sheets/cost/waste, ignoring remnant "
            "status entirely."
        )
        inv_box_layout.addWidget(self.prefer_remnants)

        self.joint_stock_optimization = QtWidgets.QCheckBox("Joint stock optimization")
        self.joint_stock_optimization.setToolTip(
            "Each cascade pass nests against EVERY viable stock size for the group and keeps whichever "
            "actually yields fewest sheets (then lowest cost if priced, then least waste), instead of "
            "trusting the remnants-first/smallest-first heuristic to have picked the best one. Honest "
            "cost: multiplies nesting passes (or full GA searches) by however many candidate sizes exist, "
            "per cascade step -- meaningfully slower. Still not a true joint solve across mixed sizes in "
            "one pass (e.g. \"2 of A + 1 of B\" vs \"3 of B\"), just a better-informed single-size choice."
        )
        inv_box_layout.addWidget(self.joint_stock_optimization)

        self.true_joint_stock_optimization = QtWidgets.QCheckBox("Search size combinations (slower)")
        self.true_joint_stock_optimization.setToolTip(
            "Closes the real gap the checkbox above still can't: hill-climbs over an explicit, ordered "
            "plan of specific stock sheets -- possibly mixed sizes -- for the group's WHOLE remaining "
            "parts at once, instead of committing to one best-fit size per pass. Always starts from "
            "exactly the plan \"Joint stock optimization\" above would produce, so it never does worse, "
            "only sometimes better. Honest cost: replays the entire candidate plan from scratch on every "
            "search step (a few dozen extra nesting passes within a ~20s budget per material/thickness "
            "group) -- combined with Layout optimization's GA this compounds badly, so use both together "
            "only on small jobs."
        )
        self.true_joint_stock_optimization.toggled.connect(self._on_true_joint_toggled)
        inv_box_layout.addWidget(self.true_joint_stock_optimization)
        self._stash_settings("Stock", inv_box, top_layout)

        ga_box = QtWidgets.QGroupBox("Layout optimization")
        ga_form = QtWidgets.QFormLayout(ga_box)

        self.ga_population = QtWidgets.QSpinBox()
        self.ga_population.setRange(2, 200)
        self.ga_population.setValue(12)
        self.ga_population.setToolTip("Number of candidate orderings per generation. Higher = better "
                                       "search, linearly slower (each one is a full nesting pass).")

        self.ga_generations = QtWidgets.QSpinBox()
        self.ga_generations.setRange(1, 200)
        self.ga_generations.setValue(8)
        self.ga_generations.setToolTip("Number of generations to evolve. Higher = better search, "
                                        "linearly slower.")

        ga_form.addRow("Population size", self.ga_population)
        ga_form.addRow("Generations", self.ga_generations)
        self.ga_hint = QtWidgets.QLabel(
            "The elapsed timer never ends a search automatically; press Stop to keep the best layout so far."
        )
        self.ga_hint.setWordWrap(True)
        ga_form.addRow(self.ga_hint)
        self.ga_optimize_rotations = QtWidgets.QCheckBox("Also search rotation choice")
        self.ga_optimize_rotations.setToolTip("Lets the search trade off an earlier part's rotation against "
                                               "how it constrains later parts, instead of leaving rotation to "
                                               "this engine's own greedy per-placement pick. Honest trade-off: "
                                               "unlike order-only search, this mode does NOT guarantee a result "
                                               "at least as good as plain Run Nesting -- it can occasionally "
                                               "do worse on a given search, since it trades away that "
                                               "per-placement guarantee for the chance at a jointly better one.")
        ga_form.addRow(self.ga_optimize_rotations)
        self.ga_parallel = QtWidgets.QCheckBox("Parallelize across CPU cores")
        self.ga_parallel.setToolTip(
            "Evaluates each generation's candidate orderings across a process pool instead of one at a "
            "time -- every candidate is a fully independent nesting pass, so this is a real wall-clock "
            "speedup with no change to search quality (same seed -> same final result, parallel or not). "
            "Spawns up to one worker process per CPU core. Trade-off: live per-part animation during the "
            "search is replaced by a once-per-generation replay of the current best layout, instead of "
            "showing every candidate being assembled."
        )
        ga_form.addRow(self.ga_parallel)
        # Live operational readout for the ribbon's Layout optimization box
        # (and the workbench's copy): mirrors stats_label, so it shows the
        # running search's generation/sheets/efficiency and stops on the
        # final layout totals, instead of a static hint.
        self.ga_runtime = QtWidgets.QLabel("No search run yet -- Elapsed: --")
        self.ga_runtime.setObjectName("GaOperational")
        self.ga_runtime.setStyleSheet("color: #8a8f98; font-size: 10px;")
        self.ga_runtime.setWordWrap(True)
        ga_form.addRow(self.ga_runtime)
        self._stash_settings("Layout", ga_box, top_layout)

        mj_box = QtWidgets.QGroupBox("Cutting options")
        mj_layout = QtWidgets.QVBoxLayout(mj_box)
        self.microjoints_enabled = QtWidgets.QCheckBox("Leave small uncut tabs on outer edges")
        self.microjoints_enabled.setToolTip("Keeps a cut part attached to its scrap/sheet by a few small "
                                             "uncut bridges, instead of coming fully free the instant the "
                                             "last cut finishes -- prevents it shifting, dropping into the "
                                             "machine bed, or scorching on a finished edge before the rest "
                                             "of the sheet is done. Applied on Export DXF and shown as dots "
                                             "in the preview; holes are never tabbed (a hole's cutout is "
                                             "scrap, not a part).")
        mj_form = QtWidgets.QFormLayout()
        self.microjoint_width = QtWidgets.QDoubleSpinBox()
        self.microjoint_width.setRange(0.2, 20.0)
        self.microjoint_width.setDecimals(2)
        self.microjoint_width.setValue(1.5)
        self.microjoint_width.setSuffix(" mm")
        self.microjoint_spacing = QtWidgets.QDoubleSpinBox()
        self.microjoint_spacing.setRange(10.0, 2000.0)
        self.microjoint_spacing.setDecimals(0)
        self.microjoint_spacing.setValue(150.0)
        self.microjoint_spacing.setSuffix(" mm")
        self.microjoint_spacing.setToolTip("Target distance between tabs -- the actual count is clamped "
                                            "to 2-6 tabs per part regardless of perimeter length.")
        mj_form.addRow("Tab width", self.microjoint_width)
        mj_form.addRow("Target spacing", self.microjoint_spacing)
        mj_layout.addWidget(self.microjoints_enabled)
        mj_layout.addLayout(mj_form)
        self.microjoints_enabled.toggled.connect(lambda _checked: self._show_sheet())
        self._stash_settings("Cutting", mj_box, top_layout)

        remnant_box = QtWidgets.QGroupBox("Inventory after cutting")
        remnant_layout = QtWidgets.QVBoxLayout(remnant_box)
        self.remnant_capture_enabled = QtWidgets.QCheckBox("Auto-record new remnants")
        self.remnant_capture_enabled.setChecked(True)
        self.remnant_capture_enabled.setToolTip("After a real Commit, finds the largest empty rectangle left "
                                                 "on each cut sheet and adds it back to the inventory as a new "
                                                 "remnant -- instead of only ever depleting stock. Only "
                                                 "matters when actually committing; Run Nesting previews never "
                                                 "touch the inventory file regardless of this setting.")
        remnant_form = QtWidgets.QFormLayout()
        self.remnant_min_dimension = QtWidgets.QDoubleSpinBox()
        self.remnant_min_dimension.setRange(0.0, 2000.0)
        self.remnant_min_dimension.setDecimals(0)
        self.remnant_min_dimension.setValue(100.0)
        self.remnant_min_dimension.setSuffix(" mm")
        self.remnant_min_dimension.setToolTip("A leftover rectangle narrower than this on either side is "
                                               "dropped rather than recorded -- not worth tracking as stock.")
        remnant_form.addRow("Minimum side length", self.remnant_min_dimension)
        remnant_layout.addLayout(remnant_form)
        # The tick box comes AFTER the size entry (size first, then whether
        # to record offcuts at that size) -- matches how the setting is read.
        remnant_layout.addWidget(self.remnant_capture_enabled)
        self._stash_settings("Stock", remnant_box, top_layout)

        cost_box = QtWidgets.QGroupBox("Estimate")
        cost_form = QtWidgets.QFormLayout(cost_box)
        self.cut_speed = QtWidgets.QDoubleSpinBox()
        self.cut_speed.setRange(1.0, 100000.0)
        self.cut_speed.setDecimals(0)
        self.cut_speed.setValue(3000.0)
        self.cut_speed.setSuffix(" mm/min")
        self.cut_speed.setToolTip("A rough cut-time estimate -- total cut length (every placed part's "
                                   "outer contour + hole perimeters, across every sheet) divided by this "
                                   "speed. Not a real toolpath simulation (no pierce time, no rapids "
                                   "between cuts, no acceleration) -- just a ballpark from cut length alone.")
        cost_form.addRow("Cut speed", self.cut_speed)
        # Operational readout for the ribbon's Export tab: mirrors stats_label
        # after a run, so this box shows the current job's real totals --
        # cut length, estimated cut time, material cost -- not a static hint.
        self.cut_estimate = QtWidgets.QLabel("No layout yet -- run nesting to estimate cut time and cost.")
        self.cut_estimate.setObjectName("CostOperational")
        self.cut_estimate.setStyleSheet("color: #8a8f98; font-size: 10px;")
        self.cut_estimate.setWordWrap(True)
        cost_form.addRow(self.cut_estimate)
        self._stash_settings("Export", cost_box, top_layout)

        label_box = QtWidgets.QGroupBox("Export details")
        label_layout = QtWidgets.QVBoxLayout(label_box)
        self.label_enabled = QtWidgets.QCheckBox("Etch each part's name at export")
        self.label_enabled.setToolTip("Adds a TEXT entity (the part name) at each part's center, on a "
                                       "separate '<part>_LABEL' layer -- route that layer to a low-power "
                                       "engrave pass instead of cutting it.")
        label_form = QtWidgets.QFormLayout()
        self.label_height = QtWidgets.QDoubleSpinBox()
        self.label_height.setRange(0.5, 500.0)
        self.label_height.setDecimals(1)
        self.label_height.setValue(5.0)
        self.label_height.setSuffix(" mm")
        label_form.addRow("Text height", self.label_height)
        label_layout.addWidget(self.label_enabled)
        label_layout.addLayout(label_form)
        self._stash_settings("Export", label_box, top_layout)

        common_line_box = QtWidgets.QGroupBox("Shared-edge cutting")
        common_line_layout = QtWidgets.QVBoxLayout(common_line_box)
        self.common_line_enabled = QtWidgets.QCheckBox("Cut shared edges once")
        self.common_line_enabled.setToolTip("Only merges an EXACT full-edge match (both endpoints coincide) "
                                             "between two parts on the same sheet -- a partial overlap falls "
                                             "back to being cut twice, same as today. Covers the common case: "
                                             "rectangular/straight-edge parts pushed flush together. Doesn't "
                                             "combine with Microjoints -- if both are checked, Microjoints "
                                             "wins and this is skipped for that export.")
        common_line_layout.addWidget(self.common_line_enabled)
        self._stash_settings("Cutting", common_line_box, top_layout)

        report_box = QtWidgets.QGroupBox("Nesting report")
        report_layout = QtWidgets.QVBoxLayout(report_box)
        self.report_hint = QtWidgets.QLabel("Every completed run writes a self-contained HTML report: job totals, "
                                            "per-sheet layouts, and stock used vs. remaining. 'Export Report…' in "
                                            "the actions row still writes one manually to wherever you choose.")
        self.report_hint.setWordWrap(True)
        report_layout.addWidget(self.report_hint)
        self.auto_report_enabled = QtWidgets.QCheckBox("Auto-write report after each run")
        self.auto_report_enabled.setChecked(True)
        self.auto_report_enabled.setToolTip("Off saves disk noise when you're just experimenting; the report "
                                            "logic itself is unchanged.")
        report_layout.addWidget(self.auto_report_enabled)
        report_dir_row = QtWidgets.QHBoxLayout()
        self.reports_dir = QtWidgets.QLineEdit("reports")
        self.reports_dir.setToolTip("Folder the auto-report is written to, relative to the app's working "
                                    "directory (run.sh pins the working directory to the project root).")
        report_dir_row.addWidget(QtWidgets.QLabel("Folder"))
        report_dir_row.addWidget(self.reports_dir, 1)

        def _browse_reports_dir():
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Auto-report folder", self.reports_dir.text())
            if path:
                self.reports_dir.setText(path)

        browse_reports_btn = QtWidgets.QPushButton("Browse…")
        browse_reports_btn.clicked.connect(_browse_reports_dir)
        report_dir_row.addWidget(browse_reports_btn)
        report_layout.addLayout(report_dir_row)
        # Operational readout: what the last report run actually produced.
        self.report_status = QtWidgets.QLabel("No report written yet.")
        self.report_status.setObjectName("ReportOperational")
        self.report_status.setStyleSheet("color: #8a8f98; font-size: 10px;")
        self.report_status.setWordWrap(True)
        report_layout.addWidget(self.report_status)
        self._stash_settings("Export", report_box, top_layout)

        # These status/helper labels exist for the workbench's tall scroll
        # panel. In the ribbon the Stock tab and the log carry the same
        # information, so hide them there -- keeping the band short. The
        # Microjoints checkbox stays visible: the native ribbon has no
        # separate toggle for it, so this checkbox in Cutting settings is
        # the only way to enable/disable the feature there. The GA/cost/
        # report operational labels just mirror the log, so they only add
        # width that the ribbon has nowhere to put.
        if self.settings_in_ribbon:
            self.ga_hint.hide()
            self.report_hint.hide()
            self.inventory_status.hide()
            self.ga_runtime.hide()
            self.cut_estimate.hide()
            self.report_status.hide()

        if top_layout is not None:
            top_layout.addStretch(1)
            root.addWidget(top_scroll)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        rescan_row_widget = QtWidgets.QWidget(left)
        rescan_row = QtWidgets.QHBoxLayout(rescan_row_widget)
        rescan_row.setContentsMargins(0, 0, 0, 0)
        rescan_btn = QtWidgets.QPushButton(self.part_source.scan_label, rescan_row_widget)
        rescan_btn.clicked.connect(self._rescan)
        rescan_row.addWidget(rescan_btn)
        rescan_row.addStretch(1)
        if self.show_actions:
            left_layout.addWidget(rescan_row_widget)
        else:
            # A widget outside any layout is still shown by default,
            # floating at (0, 0) over whatever else is there -- hide it
            # explicitly rather than just omitting it from the layout.
            rescan_row_widget.hide()

        self.table = _PartsTable(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Part", "Material", "Qty / assembly", "Rotations (deg)", "Thickness", "Grain-restricted",
             "Mirror", "Method"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QtCore.QSize(32, 32))
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.itemChanged.connect(self._refresh_preflight)
        self.table.delete_requested.connect(self._remove_selected_parts)
        # In the native app (show_actions=False) the parts table doesn't
        # live in this split view: the window adopts the SAME widget into
        # its Parts tab. The FreeCAD workbench keeps the default, so the
        # table stays put here. Either way the widget itself always exists
        # -- every part-name lookup, qty read, and row operation targets it.
        if self.show_actions:
            left_layout.addWidget(self.table)
            left_layout.addStretch(1)
            splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self.preview = SheetPreview()
        self.preview.clicked.connect(self._on_preview_part_clicked)
        self.preview.coords.connect(self._on_preview_coords)
        right_layout.addWidget(self.preview, 1)
        # Single nav line: sheet dropdown + prev/next sit on one row so the
        # preview above gets that extra ~25px of height. In the native app
        # (landscape 1200x800 window, portrait 1220x2440 stock) the sheet's
        # on-screen size is height-limited, so every pixel of preview height
        # is worth roughly 1px of sheet rendering -- a second control row is
        # real estate given away.
        nav_row = QtWidgets.QHBoxLayout()
        self.sheet_selector = QtWidgets.QComboBox()
        self.sheet_selector.setToolTip("Jump straight to any sheet in this job -- like Lantek's Nesting "
                                        "Explorer tree, but flattened into one dropdown since a nested job "
                                        "here is a single material/thickness cascade, not a multi-job tree.")
        self.sheet_selector.activated.connect(self._on_sheet_selector_activated)
        self.prev_btn = QtWidgets.QPushButton("<<")
        self.prev_btn.setToolTip("Previous sheet")
        self.prev_btn.clicked.connect(self._prev_sheet)
        self.sheet_label = QtWidgets.QLabel("No sheets yet")
        self.sheet_label.setAlignment(QtCore.Qt.AlignCenter)
        self.next_btn = QtWidgets.QPushButton(">>")
        self.next_btn.setToolTip("Next sheet")
        self.next_btn.clicked.connect(self._next_sheet)
        # Sheet jump control sits left (same spot its own row had), then a
        # zoom magnifier, then the pair of paging buttons around the centered
        # "2 / 5" counter. The zoom spin is how a portrait 1220x2440 stock
        # sheet stops looking tiny in a landscape window: the sheet can be
        # magnified 4x beyond its fit size (cropped, still centered) so the
        # cuts actually read on screen.
        nav_row.addWidget(self.sheet_selector)
        nav_row.addWidget(QtWidgets.QLabel("Zoom"))
        self.zoom_spin = QtWidgets.QDoubleSpinBox()
        self.zoom_spin.setRange(0.5, 4.0)
        self.zoom_spin.setSingleStep(0.1)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setSuffix("x")
        self.zoom_spin.setValue(1.0)
        self.zoom_spin.setFixedWidth(80)
        self.zoom_spin.setToolTip("Magnify the sheet beyond its fit-to-screen size. "
                                   "1.00x shows the whole sheet; higher zooms crop around the "
                                   "sheet's center (the mouse wheel over the preview does the same).")
        self.zoom_spin.valueChanged.connect(self.preview.set_zoom)
        self.preview.zoom_changed.connect(self.zoom_spin.setValue)
        nav_row.addWidget(self.zoom_spin)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.sheet_label, 1)
        nav_row.addWidget(self.next_btn)
        self.manual_toggle = QtWidgets.QPushButton("Manual adjustment")
        self.manual_toggle.setCheckable(True)
        self.manual_toggle.setToolTip("Open/close the per-part nudge controls. "
                                       "Collapsed by default so the sheet preview keeps the height. "
                                       "Clicking a part in the preview re-opens it automatically.")
        self.manual_toggle.toggled.connect(self._on_manual_toggled)
        nav_row.addWidget(self.manual_toggle)
        self.results_toggle = QtWidgets.QPushButton("Layout results")
        self.results_toggle.setCheckable(True)
        self.results_toggle.setChecked(True)
        self.results_toggle.setToolTip("Show/hide the Layout Results thumbnail column. "
                                       "Hiding gives the sheet preview the full width "
                                       "(almost all of it matters once you zoom in).")
        self.results_toggle.toggled.connect(self._toggle_layout_results)
        nav_row.addWidget(self.results_toggle)
        right_layout.addLayout(nav_row)

        manual_box = QtWidgets.QGroupBox("Manual part adjustment")
        self.manual_box = manual_box
        # A collapsing toggle button (above) owns visibility: in the native
        # window the box stays hidden by default so its ~77px of height goes
        # to the sheet preview; the FreeCAD workbench keeps it expanded by
        # default (its panel had always shown it). Hidden widgets keep their
        # state, so every manual-edit method works with the box closed.
        manual_box.setVisible(self.show_actions)
        self.manual_toggle.setChecked(self.show_actions)
        manual_layout = QtWidgets.QVBoxLayout(manual_box)
        manual_layout.setContentsMargins(6, 4, 6, 4)
        manual_layout.setSpacing(1)
        # Two rows instead of one: a single row of Part + dX + dY + Rotate +
        # Prohibit overlap + Apply spans ~770px, which makes it the largest
        # single contributor to the whole window's layout minimum WIDTH.
        # That forces a wide window on small screens and, combined with the
        # preview's old 320px floor, left Cinnamon/Muffin nothing to resize
        # to -- see the SheetPreview comment above. Splitting into two rows
        # keeps every control, just narrower.
        manual_row = QtWidgets.QHBoxLayout()
        self.part_selector = QtWidgets.QComboBox()
        self.part_selector.setToolTip("Click a part in the preview above, or pick it here directly.")
        self.part_selector.currentIndexChanged.connect(self._on_part_selector_changed)
        self.manual_dx = QtWidgets.QDoubleSpinBox()
        self.manual_dx.setRange(-100000, 100000)
        self.manual_dx.setDecimals(2)
        self.manual_dx.setSuffix(" mm")
        self.manual_dy = QtWidgets.QDoubleSpinBox()
        self.manual_dy.setRange(-100000, 100000)
        self.manual_dy.setDecimals(2)
        self.manual_dy.setSuffix(" mm")
        manual_row.addWidget(QtWidgets.QLabel("Part"))
        manual_row.addWidget(self.part_selector, 1)
        manual_row.addWidget(QtWidgets.QLabel("dX"))
        manual_row.addWidget(self.manual_dx)
        manual_row.addWidget(QtWidgets.QLabel("dY"))
        manual_row.addWidget(self.manual_dy)
        manual_layout.addLayout(manual_row)

        manual_row2 = QtWidgets.QHBoxLayout()
        self.manual_rotation = QtWidgets.QDoubleSpinBox()
        self.manual_rotation.setRange(-360, 360)
        self.manual_rotation.setDecimals(1)
        self.manual_rotation.setSuffix(" deg")
        self.manual_rotation.setToolTip("Rotates the part in place (about its own bounding-box center), "
                                         "added to whatever rotation it currently has.")
        self.prohibit_overlap = QtWidgets.QCheckBox("Prohibit overlap")
        self.prohibit_overlap.setChecked(True)
        self.prohibit_overlap.setToolTip("Reject a manual move that would overlap another part or fall "
                                          "outside the sheet, instead of applying it anyway with a warning.")
        apply_manual_btn = QtWidgets.QPushButton("Apply")
        apply_manual_btn.clicked.connect(self._apply_manual_edit)
        manual_row2.addWidget(QtWidgets.QLabel("Rotate"))
        manual_row2.addWidget(self.manual_rotation)
        manual_row2.addWidget(self.prohibit_overlap)
        manual_row2.addWidget(apply_manual_btn)
        manual_row2.addStretch(1)
        manual_layout.addLayout(manual_row2)
        right_layout.addWidget(manual_box)

        splitter.addWidget(right)

        layouts_col = QtWidgets.QWidget()
        layouts_layout = QtWidgets.QVBoxLayout(layouts_col)
        layouts_layout.addWidget(QtWidgets.QLabel("Layout results"))
        self.layout_list = QtWidgets.QListWidget()
        self.layout_list.setIconSize(QtCore.QSize(96, 96))
        self.layout_list.itemClicked.connect(self._on_layout_candidate_clicked)
        layouts_layout.addWidget(self.layout_list)
        splitter.addWidget(layouts_col)
        self.layouts_col = layouts_col
        self.splitter = splitter  # kept so the results toggle can resize panes

        if self.show_actions:
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
            splitter.setStretchFactor(2, 1)
        else:
            splitter.setStretchFactor(0, 1)  # preview/manual column
            splitter.setStretchFactor(1, 1)  # layout-results column
        root.addWidget(splitter, 1)

        self.stats_label = QtWidgets.QLabel("Elapsed: -- -- no sheets yet.")
        self.stats_label.setAlignment(QtCore.Qt.AlignCenter)
        root.addWidget(self.stats_label)
        if not self.show_actions:
            # The native ribbon already mirrors this live text (ga_runtime /
            # cut_estimate on the Layout-optimization & Estimate boxes), so a
            # second centered copy is one wasted row of preview height here.
            # Hidden, not removed: _set_stats keeps writing text for the
            # ribbon/programmatic use.
            self.stats_label.hide()

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(4, 0, 4, 0)
        self.sheet_pos_label = QtWidgets.QLabel("Sheet -- of --")
        self.sheet_pos_label.setObjectName("SheetPosReadout")
        self.sheet_pos_label.setStyleSheet("color: #8a8f98; font-weight: 600;")
        self.coord_label = QtWidgets.QLabel("X: --   Y: -- mm")
        self.coord_label.setObjectName("CoordReadout")
        self.coord_label.setStyleSheet("color: #8a8f98;")
        self.coord_label.setAlignment(QtCore.Qt.AlignRight)
        status_row.addWidget(self.sheet_pos_label)
        status_row.addStretch(1)
        status_row.addWidget(self.coord_label)
        root.addLayout(status_row)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setMinimumSize(0, 46)
        root.addWidget(self.log)

        # Built unconditionally (same reasoning as rescan_row_widget above):
        # existing methods reference the action buttons directly
        # directly, and the GA run's findChildren(QPushButton) disable-all
        # needs them to exist as real children of `self` regardless of
        # whether this row is actually shown.
        btn_row_widget = QtWidgets.QWidget(self)
        btn_row = QtWidgets.QHBoxLayout(btn_row_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        run_btn = QtWidgets.QPushButton("Create layout", btn_row_widget)
        run_btn.setObjectName("PrimaryAction")
        run_btn.setToolTip("Preview only -- safe to click repeatedly while experimenting. "
                            "Uses exact NFP placement plus ordering optimization; does not touch inventory on disk.")
        run_btn.clicked.connect(self._optimize_ordering)
        preflight_btn = QtWidgets.QPushButton("Check readiness", btn_row_widget)
        preflight_btn.setToolTip("Check material, thickness, stock, and geometry before releasing a nesting job.")
        preflight_btn.clicked.connect(self._show_preflight)
        self.stop_btn = QtWidgets.QPushButton("Stop", btn_row_widget)
        self.stop_btn.setToolTip("Stop after the current candidate evaluation and keep the best layout found so far.")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._request_stop)
        self.export_btn = QtWidgets.QPushButton("Export DXF...", btn_row_widget)
        self.export_btn.setObjectName("SecondaryAction")
        self.export_btn.clicked.connect(self._export_dxf)
        self.export_btn.setEnabled(False)
        self.commit_btn = QtWidgets.QPushButton("Commit stock...", btn_row_widget)
        self.commit_btn.setObjectName("DestructiveAction")
        self.commit_btn.setToolTip("Actually deduct the sheets used above from the inventory file "
                                    "on disk, and record it in that file's audit log. Not reversible "
                                    "by clicking a button -- only do this once you've actually cut.")
        self.commit_btn.clicked.connect(self._commit_inventory)
        self.commit_btn.setEnabled(False)
        close_btn = QtWidgets.QPushButton("Close", btn_row_widget)
        close_btn.clicked.connect(self.close_requested.emit)
        btn_row.addWidget(run_btn)
        btn_row.addWidget(preflight_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.commit_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        if self.show_actions:
            root.addWidget(btn_row_widget)
        else:
            btn_row_widget.hide()  # see rescan_row_widget's comment above

        # The ribbon/workbench operational readouts (Layout optimization,
        # Estimate) mirror the live stats broadcast so the boxes always show
        # the current job's real numbers -- generation/sheets/efficiency
        # while a search runs, then cut time/cost totals once it lands.
        self.operational.connect(self.ga_runtime.setText)
        self.operational.connect(self.cut_estimate.setText)

        self._update_readiness()

    def _refresh_preflight(self):
        """Recompute the parts-level preflight gate (material, thickness,
        geometry, plus stock-match checks when an inventory is loaded) and
        broadcast whether the current part list may be nested/committed/
        exported. Kept cheap and guarded against mid-edit table states --
        it runs on every item edit and parts-table repopulation, and the
        real production-blocking check still happens inside each action."""
        part_count = self.table.rowCount() if hasattr(self, "table") else 0
        if not part_count:
            self.preflight_issues = ["No parts loaded yet -- add parts first."]
            self.preflight_pass = False
            self.preflight_ready.emit(False)
            return
        try:
            self.preflight_issues = self._preflight_issues()
        except Exception:
            return  # table is mid-edit; the next edit repopulates anyway
        self.preflight_pass = not self.preflight_issues
        self.preflight_ready.emit(self.preflight_pass)

    def _run_preflight_check(self):
        """Non-modal preflight run for the Parts-tab button: recomputes the
        gate and returns ``(ok, [issue, ...])`` for the tab to display. The
        workbench's modal ``_show_preflight()`` is untouched."""
        self._refresh_preflight()
        if self.preflight_pass:
            self._log("[preflight] Passed: material, thickness, stock, geometry, and layout checks are clear.")
        else:
            self._log("[preflight] " + " | ".join(self.preflight_issues))
        return self.preflight_pass, list(self.preflight_issues)

    def _update_readiness(self):
        """Refresh the plain-language state summary at the top of the panel."""
        self._refresh_preflight()
        part_count = self.table.rowCount() if hasattr(self, "table") else 0
        if not part_count:
            self.readiness_title.setText("No parts loaded")
            self.readiness_detail.setText("Add parts to begin. The layout will be checked before it can be exported.")
            return
        if not self.sheets:
            stock_text = "Stock selected" if self._inventory_template is not None else "Sheet size mode"
            self.readiness_title.setText(f"{part_count} part{'s' if part_count != 1 else ''} ready — {stock_text}")
            self.readiness_detail.setText("Review quantities and material, then choose Create layout. Nothing is written to disk by that action.")
            return
        placed = sum(len(sheet) for sheet in self.sheets)
        requested = sum(self.table.cellWidget(row, 2).value() * self.assembly_quantity.value()
                        for row in range(self.table.rowCount()) if self.table.cellWidget(row, 2))
        if self.unplaced:
            self.readiness_title.setText(f"Layout needs attention — {placed} of {requested} requested parts placed")
            self.readiness_detail.setText("Some parts could not be placed. Review the highlighted rows before exporting.")
        else:
            self.readiness_title.setText(f"Layout ready — {placed} of {requested} requested parts placed")
            self.readiness_detail.setText("Run Check readiness before export, or create another layout after changing settings.")

    def _log(self, msg):
        self.log.appendPlainText(msg)

    def _preflight_issues(self, require_layout=False):
        """Return production-blocking issues; do not silently nest unknown stock."""
        issues, seen_names = [], set()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip()
            data = self._extracted.get(name, {})
            material = self.table.item(row, 1).text().strip()
            thickness = data.get("thickness")
            outer = data.get("outer", [])

            if not name:
                issues.append(f"Row {row + 1}: part name is blank.")
            elif name in seen_names:
                issues.append(f"{name}: duplicate part name.")
            seen_names.add(name)
            if not material or material.casefold() == "unspecified":
                issues.append(f"{name or 'Row ' + str(row + 1)}: material/grade is missing.")
            if thickness is None or thickness <= 0:
                issues.append(f"{name or 'Row ' + str(row + 1)}: thickness is missing or invalid.")
            if len(outer) < 3 or geometry.polygon_area(outer) <= geometry.EPS:
                issues.append(f"{name or 'Row ' + str(row + 1)}: outer contour is invalid or has zero area.")
            else:
                loops = [outer] + data.get("holes", [])
                edge_lengths = [
                    ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    for loop in loops for a, b in zip(loop, loop[1:] + loop[:1])
                ]
                if self.min_feature.value() and edge_lengths and min(edge_lengths) < self.min_feature.value():
                    issues.append(f"{name}: feature edge {min(edge_lengths):g} mm is below the {self.min_feature.value():g} mm rule.")
                net_area = geometry.polygon_area(outer) - sum(geometry.polygon_area(h) for h in data.get("holes", []))
                if self.min_part_area.value() and net_area < self.min_part_area.value():
                    issues.append(f"{name}: net area {net_area:g} mm² is below the {self.min_part_area.value():g} mm² rule.")
                for hole_index, hole in enumerate(data.get("holes", []), 1):
                    if len(hole) >= 3:
                        min_x, min_y, max_x, max_y = geometry.polygon_bbox(hole)
                        opening = min(max_x - min_x, max_y - min_y)
                        if self.min_hole_opening.value() and opening < self.min_hole_opening.value():
                            issues.append(f"{name}: hole {hole_index} opening {opening:g} mm is below the {self.min_hole_opening.value():g} mm rule.")

            rotations = self.table.item(row, 3).text()
            try:
                if not [float(x) for x in rotations.split(",") if x.strip()]:
                    raise ValueError
            except ValueError:
                issues.append(f"{name or 'Row ' + str(row + 1)}: rotations must be comma-separated numbers.")

            if self._inventory_template is not None and material and material.casefold() != "unspecified" and thickness:
                matches = [s for s in self._inventory_template
                           if s.material == material and abs(s.thickness - thickness) <= 1e-3
                           and (s.quantity is None or s.quantity > 0)]
                if not matches:
                    issues.append(f"{name}: no available stock matches {material} / {thickness:g} mm.")

        if require_layout and self.unplaced:
            issues.append("The current layout has unplaced parts: " + ", ".join(self.unplaced) + ".")
        if self._inventory_template is None:
            material_groups = {
                (self.table.item(row, 1).text().strip(), self._extracted.get(self.table.item(row, 0).text(), {}).get("thickness"))
                for row in range(self.table.rowCount())
            }
            if len(material_groups) > 1:
                issues.append("Multiple material/thickness groups require a stock inventory; this prevents mixed-material layouts.")
        return issues

    def _show_preflight(self):
        issues = self._preflight_issues(require_layout=bool(self.sheets))
        if issues:
            message = "Preflight failed:\n\n" + "\n".join(f"• {issue}" for issue in issues)
            self._log("[preflight] " + " | ".join(issues))
            QtWidgets.QMessageBox.critical(self, "Nesting preflight", message)
            return False
        self._log("[preflight] Passed: material, thickness, stock, geometry, and layout checks are clear.")
        QtWidgets.QMessageBox.information(self, "Nesting preflight", "Passed. This job is ready to nest or release.")
        return True

    def _require_preflight(self, action, require_layout=False):
        issues = self._preflight_issues(require_layout=require_layout)
        if not issues:
            return True
        self._log(f"[preflight] Blocked {action}: " + " | ".join(issues))
        QtWidgets.QMessageBox.critical(
            self, "Nesting preflight blocked",
            f"Cannot {action} until these issues are fixed:\n\n" + "\n".join(f"• {issue}" for issue in issues),
        )
        return False

    def _request_stop(self):
        if self._search_started_at is not None:
            self._stop_requested = True
            self.stop_btn.setEnabled(False)
            self._log("Stop requested -- keeping the best completed layout so far.")

    def _update_search_elapsed(self):
        if self._search_started_at is not None:
            elapsed = time.monotonic() - self._search_started_at
            self._set_stats(f"Elapsed: {elapsed:.1f}s -- searching; press Stop to keep best.")

    def _set_stats(self, text):
        """Update the live job-statistics strip and broadcast it to any
        out-of-box readouts (the ribbon's Layout optimization / Estimate
        boxes) on the `operational` signal."""
        self.stats_label.setText(text)
        self.operational.emit(text)

    # ------------------------------------------------------------ scanning

    def _part_thumbnail(self, name, data, size=32):
        """A small rendered icon of the part's outer contour minus holes,
        for the Parts table's Part column -- reuses the exact same
        PainterPath-subtraction approach and colors as SheetPreview's
        per-part drawing (filled with the part name's color, outlined with a
        darkened version), just normalized to fit a small square instead of
        placed at sheet coordinates."""
        outer = data.get("outer")
        if not outer:
            return QtGui.QIcon()
        xs = [p[0] for p in outer]
        ys = [p[1] for p in outer]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        w = max(maxx - minx, 1e-6)
        h = max(maxy - miny, 1e-6)
        pad = 3
        avail = size - 2 * pad
        scale = min(avail / w, avail / h)

        def to_screen(x, y):
            return QtCore.QPointF(pad + (x - minx) * scale, size - pad - (y - miny) * scale)

        path = QtGui.QPainterPath()
        path.addPolygon(QtGui.QPolygonF([to_screen(x, y) for x, y in outer]))
        path.closeSubpath()
        for hole in data.get("holes", []):
            hole_path = QtGui.QPainterPath()
            hole_path.addPolygon(QtGui.QPolygonF([to_screen(x, y) for x, y in hole]))
            hole_path.closeSubpath()
            path = path.subtracted(hole_path)

        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(part_colors.outline_for(name), 1))
        painter.setBrush(part_colors.color_for(name))
        painter.drawPath(path)
        painter.end()
        return QtGui.QIcon(pix)

    def _add_table_row(self, label, data, material="unspecified", quantity=1,
                        rotations="0,90,180,270", grain_restricted=False, mirror=False):
        """Adds one row to the parts table for `label` (already present in
        self._extracted) -- shared by _rescan() (fresh scan, always default
        material/qty/rotations) and _load_job() (restoring saved values).

        Column 5 ("Grain-restricted") is a checkbox: some materials
        (brushed stainless, pre-painted coil, wood-grain laminate) must
        keep a part's rolling/grain direction aligned to the sheet, i.e.
        only 0/180 deg rotation is actually allowed. Checking it forces
        the Rotations column to "0,180" and locks it -- _table_parts()
        also enforces this independently at nesting time, so the two can
        never disagree even if a row is somehow left in an inconsistent
        state.

        Column 6 ("Mirror") is a checkbox: checking it lets the nester
        also try the part's mirror image at every allowed rotation (the
        flipped footprint can nest into leftover space the original
        cannot). Independent of Grain-restricted -- mirroring is a pure
        geometry operation and doesn't disturb grain direction."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        part_colors.color_for(label)  # reserve the color in table-row order
        name_item = QtWidgets.QTableWidgetItem(label)
        name_item.setIcon(self._part_thumbnail(label, data))
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(material))

        qty_spin = QtWidgets.QSpinBox()
        qty_spin.setRange(1, 9999)
        qty_spin.setValue(quantity)
        self.table.setCellWidget(row, 2, qty_spin)

        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("0,180" if grain_restricted else rotations))
        if grain_restricted:
            self.table.item(row, 3).setFlags(self.table.item(row, 3).flags() & ~QtCore.Qt.ItemIsEditable)

        thickness = data.get("thickness")
        thickness_text = f"{thickness:.2f} mm" if thickness is not None else "?"
        self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(thickness_text))

        grain_item = QtWidgets.QTableWidgetItem()
        grain_item.setCheckState(QtCore.Qt.Checked if grain_restricted else QtCore.Qt.Unchecked)
        self.table.setItem(row, 5, grain_item)

        mirror_item = QtWidgets.QTableWidgetItem()
        mirror_item.setCheckState(QtCore.Qt.Checked if mirror else QtCore.Qt.Unchecked)
        self.table.setItem(row, 6, mirror_item)

        self.table.setItem(row, 7, QtWidgets.QTableWidgetItem(data["method"]))
        for col in (0, 4, 7):
            item = self.table.item(row, col)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)

    def _on_table_item_changed(self, item):
        if item.column() != 5:  # only react to the Grain-restricted checkbox
            return
        rotations_item = self.table.item(item.row(), 3)
        if rotations_item is None:
            return
        if item.checkState() == QtCore.Qt.Checked:
            rotations_item.setText("0,180")
            rotations_item.setFlags(rotations_item.flags() & ~QtCore.Qt.ItemIsEditable)
        else:
            rotations_item.setFlags(rotations_item.flags() | QtCore.Qt.ItemIsEditable)

    def _rescan(self):
        part_colors.reset()
        self.table.setRowCount(0)
        self._extracted, logs = self.part_source.scan(
            self.kfactor.value(), self.tolerance.value(), min_area=self.min_part_area.value()
        )
        for line in logs:
            self._log(line)

        for label, data in self._extracted.items():
            self._add_table_row(label, data, material=data.get("material") or "unspecified",
                                 quantity=data.get("quantity", 1))
        self._update_readiness()
        self.parts_changed.emit()

    def _load_parts_dict(self, extracted, log_msg=None):
        """Populate the parts table from an already-extracted {label: data}
        dict without a part_source.scan() round-trip -- the native app's
        startup path when a parts.json path is passed on the command line
        (e.g. by the FreeCAD workbench's Send to AlphaNest command)."""
        self._extracted = dict(extracted)
        part_colors.reset()
        self.table.setRowCount(0)
        for label, data in self._extracted.items():
            self._add_table_row(label, data, material=data.get("material") or "unspecified",
                                 quantity=data.get("quantity", 1))
        if log_msg:
            self._log(log_msg)
        self._update_readiness()
        self.parts_changed.emit()

    def part_summaries(self):
        """Read-only per-part snapshot (geometry + table attributes) for the
        native app's Parts tab. Degenerate geometry degrades to None/0
        placeholders rather than crashing the gallery."""
        summaries = []
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            if label_item is None:
                continue
            label = label_item.text()
            data = self._extracted.get(label, {})
            outer = data.get("outer", [])
            holes = list(data.get("holes", []))
            qty = 1
            qty_w = self.table.cellWidget(row, 2)
            if qty_w is not None:
                try:
                    qty = qty_w.value()
                except Exception:
                    qty = 1

            def cell(r, c):
                it = self.table.item(r, c)
                return it.text() if it else ""

            summary = {
                "name": label,
                "material": cell(row, 1),
                "rotations": cell(row, 3),
                "quantity_per_assembly": qty,
                "grain_restricted": bool(self.table.item(row, 5)
                                         and self.table.item(row, 5).checkState() == QtCore.Qt.Checked),
                "mirror": bool(self.table.item(row, 6)
                               and self.table.item(row, 6).checkState() == QtCore.Qt.Checked),
                "method": data.get("method", ""),
                "thickness": data.get("thickness"),
                "outer": outer,
                "holes": holes,
                "net_area": None, "bbox": (0.0, 0.0), "cut_length": 0.0, "hole_count": len(holes),
            }
            try:
                gross = geometry.polygon_area(outer)
                hole_area = sum(geometry.polygon_area(h) for h in holes)
                summary["net_area"] = gross - hole_area
            except Exception:
                pass
            try:
                min_x, min_y, max_x, max_y = geometry.polygon_bbox(outer)
                summary["bbox"] = (max_x - min_x, max_y - min_y)
            except Exception:
                pass
            try:
                summary["cut_length"] = sum(
                    ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    for loop in [outer] + holes
                    for a, b in zip(loop, loop[1:] + loop[:1])
                )
            except Exception:
                pass
            summaries.append(summary)
        return summaries

    # ------------------------------------------------------------ inventory

    def _set_inventory(self, path, stock, log_msg):
        """Shared by _load_inventory(), the Stock tab's "New Inventory...",
        and _commit_inventory()'s post-commit reload -- one place that
        updates _inventory_template/_inventory_path/status text and emits
        inventory_changed, so a Stock tab (or anything else) always sees a
        consistent view regardless of which of those triggered it."""
        self._inventory_template = stock
        self._inventory_path = path
        if stock:
            self.inventory_status.setText(
                f"{len(stock)} stock entr{'y' if len(stock) == 1 else 'ies'} loaded from "
                f"{os.path.basename(path)} -- parts are matched to stock by Material + Thickness; "
                f"sheet size is taken from the matched stock."
            )
        else:
            self.inventory_status.setText(
                f"{os.path.basename(path)} -- empty, add stock in the Stock tab."
            )
        self._log(log_msg)
        self.commit_btn.setEnabled(False)  # need a fresh Run Nesting against this inventory first
        self.inventory_changed.emit()
        self._update_readiness()

    def _load_inventory(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load inventory JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            stock = inv.load_inventory(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Nesting", f"Could not load inventory: {e}")
            return
        self._set_inventory(path, stock, f"Loaded inventory: {os.path.basename(path)} ({len(stock)} entries).")

    def _clear_inventory(self):
        self._inventory_template = None
        self._inventory_path = None
        self.inventory_status.setText(
            "None loaded -- a fixed 1220 x 2440 mm sheet is used for every part, regardless of Material."
        )
        self.commit_btn.setEnabled(False)
        self.inventory_changed.emit()
        self._update_readiness()

    # ----------------------------------------------------------------- job

    def _job_state(self):
        """The full input specification for the current job -- parts (with
        their geometry embedded, so reopening doesn't depend on the
        original DXF/FCStd file still existing at the same path), every
        settings-panel field, and which inventory file was loaded (its
        path only -- inventory content is reloaded fresh from that file on
        open, since it's already its own persisted, possibly-since-changed
        file, not something a job snapshot should freeze in place)."""
        parts = []
        for row in range(self.table.rowCount()):
            label = self.table.item(row, 0).text()
            data = self._extracted.get(label)
            if data is None:
                continue
            parts.append({
                "label": label,
                "material": self.table.item(row, 1).text(),
                "quantity": self.table.cellWidget(row, 2).value(),
                "rotations": self.table.item(row, 3).text(),
                "grain_restricted": self.table.item(row, 5).checkState() == QtCore.Qt.Checked,
                "allow_mirror": self.table.item(row, 6).checkState() == QtCore.Qt.Checked,
                "outer": data["outer"], "holes": data["holes"],
                "thickness": data.get("thickness"), "method": data.get("method", "job"),
            })
        return {
            "schema": "alphanest-job/1",
            "settings": {
                "sheet_w": self.sheet_w.value(), "sheet_h": self.sheet_h.value(),
                "assembly_quantity": self.assembly_quantity.value(),
                "min_feature": self.min_feature.value(), "min_hole_opening": self.min_hole_opening.value(),
                "min_part_area": self.min_part_area.value(),
                "allow_hole_nesting": self.allow_hole_nesting.isChecked(), "hole_clearance": self.hole_clearance.value(),
                "part_spacing": self.part_spacing.value(), "kfactor": self.kfactor.value(),
                "tolerance": self.tolerance.value(),
                "margin_left": self.margin_left.value(), "margin_right": self.margin_right.value(),
                "margin_top": self.margin_top.value(), "margin_bottom": self.margin_bottom.value(),
                "margin_apply_all": self.margin_apply_all.isChecked(),
                "prefer_remnants": self.prefer_remnants.isChecked(),
                "currency": self.currency_code,
                "joint_stock_optimization": self.joint_stock_optimization.isChecked(),
                "true_joint_stock_optimization": self.true_joint_stock_optimization.isChecked(),
                "ga_population": self.ga_population.value(), "ga_generations": self.ga_generations.value(),
                "ga_optimize_rotations": self.ga_optimize_rotations.isChecked(),
                "ga_parallel": self.ga_parallel.isChecked(),
                "microjoints_enabled": self.microjoints_enabled.isChecked(),
                "microjoint_width": self.microjoint_width.value(),
                "microjoint_spacing": self.microjoint_spacing.value(),
                "remnant_capture_enabled": self.remnant_capture_enabled.isChecked(),
                "remnant_min_dimension": self.remnant_min_dimension.value(),
                "cut_speed": self.cut_speed.value(),
                "label_enabled": self.label_enabled.isChecked(),
                "label_height": self.label_height.value(),
                "common_line_enabled": self.common_line_enabled.isChecked(),
                "auto_report_enabled": self.auto_report_enabled.isChecked(),
                "reports_dir": self.reports_dir.text(),
            },
            "inventory_path": self._inventory_path,
            "parts": parts,
        }

    def _save_job(self, path):
        with open(path, "w") as f:
            json.dump(self._job_state(), f, indent=2)
        self._job_path = path
        self._log(f"Saved job to {path}.")

    def _load_job(self, path):
        with open(path) as f:
            data = json.load(f)

        settings = data.get("settings", {})
        self.sheet_w.setValue(settings.get("sheet_w", self.sheet_w.value()))
        self.sheet_h.setValue(settings.get("sheet_h", self.sheet_h.value()))
        self.assembly_quantity.setValue(settings.get("assembly_quantity", self.assembly_quantity.value()))
        self.min_feature.setValue(settings.get("min_feature", self.min_feature.value()))
        self.min_hole_opening.setValue(settings.get("min_hole_opening", self.min_hole_opening.value()))
        self.min_part_area.setValue(settings.get("min_part_area", self.min_part_area.value()))
        self.allow_hole_nesting.setChecked(settings.get("allow_hole_nesting", False))
        self.hole_clearance.setValue(settings.get("hole_clearance", self.hole_clearance.value()))
        # "kerf" is the old (pre-rename) job-file key -- fall back to it so
        # jobs saved before this field was renamed still load correctly.
        self.part_spacing.setValue(settings.get("part_spacing", settings.get("kerf", self.part_spacing.value())))
        self.kfactor.setValue(settings.get("kfactor", self.kfactor.value()))
        self.tolerance.setValue(settings.get("tolerance", self.tolerance.value()))
        # "Apply to all" restored BEFORE the four margin values: if it were
        # still checked from whatever job was open previously, setting
        # margin_left below would immediately force all four to match it
        # via _sync_margins(), corrupting an asymmetric-margin job on load.
        self.margin_apply_all.setChecked(settings.get("margin_apply_all", False))
        self.margin_left.setValue(settings.get("margin_left", self.margin_left.value()))
        self.margin_right.setValue(settings.get("margin_right", self.margin_right.value()))
        self.margin_top.setValue(settings.get("margin_top", self.margin_top.value()))
        self.margin_bottom.setValue(settings.get("margin_bottom", self.margin_bottom.value()))
        self.prefer_remnants.setChecked(settings.get("prefer_remnants", True))
        self.set_currency(settings.get("currency", DEFAULT_CURRENCY))
        self.joint_stock_optimization.setChecked(settings.get("joint_stock_optimization", False))
        self.true_joint_stock_optimization.setChecked(settings.get("true_joint_stock_optimization", False))
        self.ga_population.setValue(settings.get("ga_population", self.ga_population.value()))
        self.ga_generations.setValue(settings.get("ga_generations", self.ga_generations.value()))
        self.ga_optimize_rotations.setChecked(settings.get("ga_optimize_rotations", False))
        self.ga_parallel.setChecked(settings.get("ga_parallel", False))
        self.microjoints_enabled.setChecked(settings.get("microjoints_enabled", False))
        self.microjoint_width.setValue(settings.get("microjoint_width", self.microjoint_width.value()))
        self.microjoint_spacing.setValue(settings.get("microjoint_spacing", self.microjoint_spacing.value()))
        self.remnant_capture_enabled.setChecked(settings.get("remnant_capture_enabled", True))
        self.remnant_min_dimension.setValue(settings.get("remnant_min_dimension", self.remnant_min_dimension.value()))
        self.cut_speed.setValue(settings.get("cut_speed", self.cut_speed.value()))
        self.label_enabled.setChecked(settings.get("label_enabled", False))
        self.label_height.setValue(settings.get("label_height", self.label_height.value()))
        self.common_line_enabled.setChecked(settings.get("common_line_enabled", False))
        self.auto_report_enabled.setChecked(settings.get("auto_report_enabled", True))
        self.reports_dir.setText(settings.get("reports_dir", "reports"))

        self.table.setRowCount(0)
        self._extracted = {}
        part_colors.reset()
        for p in data.get("parts", []):
            self._extracted[p["label"]] = {
                "outer": p["outer"], "holes": p["holes"],
                "thickness": p.get("thickness"), "method": p.get("method", "job"),
            }
            self._add_table_row(
                p["label"], self._extracted[p["label"]],
                material=p.get("material", "unspecified"), quantity=p.get("quantity", 1),
                rotations=p.get("rotations", "0,90,180,270"),
                grain_restricted=p.get("grain_restricted", False),
                mirror=p.get("allow_mirror", False),
            )

        inventory_path = data.get("inventory_path")
        if inventory_path:
            if os.path.isfile(inventory_path):
                stock = inv.load_inventory(inventory_path)
                self._set_inventory(inventory_path, stock,
                                     f"Loaded inventory from job: {os.path.basename(inventory_path)}.")
            else:
                self._log(f"[warn] job's inventory file no longer exists, skipped: {inventory_path}")

        # No stale results from before this job was opened.
        self.sheets = []
        self.sheet_dims = []
        self.sheet_job_label = []
        self.sheet_prices = []
        self.used_stock = []
        self.unplaced = []
        self.current_sheet_index = 0
        self.export_btn.setEnabled(False)
        self.commit_btn.setEnabled(False)
        self._show_sheet()

        self._job_path = path
        self._log(f"Opened job {path} ({len(data.get('parts', []))} part(s)).")
        self.parts_changed.emit()

    def _save_job_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save job", "", "AlphaNest job (*.json)")
        if not path:
            return
        self._save_job(path)

    def _save_job_current(self):
        if self._job_path:
            self._save_job(self._job_path)
        else:
            self._save_job_as()

    def _open_job(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open job", "", "AlphaNest job (*.json)")
        if not path:
            return
        try:
            self._load_job(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Nesting", f"Could not open job: {e}")

    # ------------------------------------------------------------- nesting

    def _table_parts(self):
        parts = []
        for row in range(self.table.rowCount()):
            label = self.table.item(row, 0).text()
            data = self._extracted.get(label)
            if data is None:
                continue
            material = self.table.item(row, 1).text().strip() or None
            qty_per_assembly = self.table.cellWidget(row, 2).value()
            qty = qty_per_assembly * self.assembly_quantity.value()
            rot_text = self.table.item(row, 3).text()
            try:
                rotations = [float(x) for x in rot_text.split(",") if x.strip() != ""]
            except ValueError:
                rotations = [0.0, 90.0, 180.0, 270.0]
                self._log(f"[warn] couldn't parse rotations for {label}, using default")
            if not rotations:
                rotations = [0.0]
            if self.table.item(row, 5).checkState() == QtCore.Qt.Checked:
                # Grain-restricted: enforced here independently of whatever
                # the Rotations column's text happens to say, so a locked
                # cell that somehow got out of sync (or a job file hand-
                # edited outside the app) can't sneak an illegal rotation
                # through.
                rotations = [0.0, 180.0]
            mirror_item = self.table.item(row, 6)
            parts.append(nester.Part(
                name=label,
                points=[tuple(p) for p in data["outer"]],
                holes=[[tuple(p) for p in h] for h in data["holes"]],
                quantity=qty,
                rotations=rotations,
                material=material,
                thickness=data.get("thickness"),
                allow_mirror=mirror_item is not None and mirror_item.checkState() == QtCore.Qt.Checked,
            ))
        return parts

    def _remove_selected_parts(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self._log("Remove Selected Part: nothing selected.")
            return
        for row in rows:
            label = self.table.item(row, 0).text()
            self._extracted.pop(label, None)
            self.table.removeRow(row)
        self._log(f"Removed {len(rows)} part(s) from the job.")
        self._update_readiness()
        self.parts_changed.emit()

    def _margin_kwargs(self):
        return {
            "margin_left": self.margin_left.value(), "margin_right": self.margin_right.value(),
            "margin_top": self.margin_top.value(), "margin_bottom": self.margin_bottom.value(),
            "allow_hole_nesting": self.allow_hole_nesting.isChecked(),
            "hole_clearance": self.hole_clearance.value(),
        }

    def _sync_margins(self, value):
        if not self.margin_apply_all.isChecked():
            return
        for spin in (self.margin_left, self.margin_right, self.margin_top, self.margin_bottom):
            if spin.value() != value:
                spin.blockSignals(True)  # avoid re-triggering this same handler for each sibling
                spin.setValue(value)
                spin.blockSignals(False)

    def _total_cut_length(self):
        """Every placed part's outer contour + hole perimeters, across
        every sheet -- a laser/plasma's cut time is roughly proportional
        to this, which is all _job_stats_text()'s cut-time estimate claims
        to be: no pierce time, no rapids between cuts, no acceleration,
        just cut length / cut speed."""
        total = 0.0
        for sheet in self.sheets:
            for pp in sheet:
                total += geometry.polygon_perimeter(pp.points)
                for hole in pp.holes:
                    total += geometry.polygon_perimeter(hole)
        return total

    def _overall_utilization(self):
        if not self.sheets:
            return 0.0
        total_area = sum(w * h for w, h in self.sheet_dims)
        used_area = sum(pp.net_area() for sheet in self.sheets for pp in sheet)
        return 100.0 * used_area / total_area if total_area > 0 else 0.0

    def _job_stats_text(self, elapsed):
        if not self.sheets:
            return f"Elapsed: {elapsed:.1f}s -- no sheets yet."
        total_util = self._overall_utilization()
        total_placed = sum(len(sheet) for sheet in self.sheets)
        text = (f"Elapsed: {elapsed:.1f}s -- {len(self.sheets)} sheet(s), {total_placed} part(s) placed, "
                f"{total_util:.1f}% overall efficiency")

        cut_length = self._total_cut_length()
        cut_minutes = cut_length / self.cut_speed.value() if self.cut_speed.value() > 0 else 0.0
        text += f" -- est. cut time {cut_minutes:.1f} min ({cut_length / 1000:.1f} m of cuts)"

        priced = [p for p in self.sheet_prices if p is not None]
        if priced:
            total_cost = sum(priced)
            unpriced = len(self.sheet_prices) - len(priced)
            note = f" ({unpriced} sheet(s) unpriced, not included)" if unpriced else ""
            text += f" -- est. material cost {total_cost:.2f}{note}"

        return text

    # ------------------------------------------------------- layout results

    def _sheet_thumbnail(self, w, h, sheet, size=96):
        """Renders one sheet to a small icon -- same grab-to-QPixmap
        technique _export_report() uses for its embedded images, just via
        a temporary, never-shown SheetPreview instead of the live one."""
        preview = SheetPreview()
        preview.resize(size, size)
        preview.set_sheet(w, h, sheet, self._microjoint_cfg(), self._common_line_cfg())
        pix = preview.grab()
        return QtGui.QIcon(pix)

    def _update_placed_counts(self):
        """Tints each row's Qty spinbox by how much of that part actually
        got placed vs. requested (green = all of it, amber = some, red =
        none) -- LaserNest's "0 | 11"-style at-a-glance readout, using the
        Qty widget itself rather than the Part-name cell so nothing that
        reads the name cell's text as a lookup key elsewhere is affected.
        Also logs a one-line placed/requested summary after every run."""
        unplaced_counts = {}
        for name in self.unplaced:
            unplaced_counts[name] = unplaced_counts.get(name, 0) + 1
        summary = []
        short_total = 0
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            qty_widget = self.table.cellWidget(row, 2)
            if label_item is None or qty_widget is None:
                continue
            requested = qty_widget.value() * self.assembly_quantity.value()
            placed = max(0, requested - unplaced_counts.get(label_item.text(), 0))
            short_total += requested - placed
            summary.append(
                f"{placed}/{requested} {label_item.text()}"
                + ("" if placed >= requested else " (short)")
            )
            qty_widget.setToolTip(
                f"{placed} / {requested} placed "
                f"({qty_widget.value()} per assembly × {self.assembly_quantity.value()} assemblies)"
            )
            if placed >= requested:
                color = "#8fd19e"  # green -- fully placed
            elif placed > 0:
                color = "#ffd479"  # amber -- partially placed
            else:
                color = "#f28b82"  # red -- none placed
            qty_widget.setStyleSheet(f"QSpinBox {{ background-color: {color}; color: #1b1d22; }}")
        if summary:
            self._log(
                "  Placement summary: " + " · ".join(summary)
                + (" -- everything placed" if short_total == 0 else f" ({short_total} part(s) short)")
            )

    def _record_candidate(self, method):
        """Snapshots the just-finished run as a browsable candidate in the
        Layout Results panel -- called at the end of a nesting run,
        after self.sheets/etc. are already set to that result."""
        if not self.sheets:
            return
        self.layout_candidates.append({
            "sheets": self.sheets, "sheet_dims": self.sheet_dims,
            "sheet_job_label": self.sheet_job_label, "sheet_prices": self.sheet_prices,
            "used_stock": self.used_stock,
            "unplaced": list(self.unplaced), "method": method,
            "timestamp": time.strftime("%H:%M:%S"),
            # Commit reads these, not self.sheets directly -- capture them
            # too so re-selecting an older candidate and committing acts on
            # what's actually shown, not whatever the most recent run was.
            "last_run_parts": self._last_run_parts,
            "last_run_used_ga": self._last_run_used_ga,
            "last_run_ga_kwargs": self._last_run_ga_kwargs,
        })
        del self.layout_candidates[:-_MAX_LAYOUT_CANDIDATES]
        self._refresh_layout_list()

    def _refresh_layout_list(self):
        """Rebuilds the Layout Results panel as one row per SHEET of the
        current run (they're styled with a 96px sheet thumbnail and the
        same per-part colors), so the panel truthfully shows every sheet
        the nesting produced. Clicking a row jumps the preview to it."""
        self.layout_list.clear()
        if not self.layout_candidates:
            return  # no run completed yet (or a new job cleared it)
        n_sheets = len(self.sheets)
        for i, (sheet, dims) in enumerate(zip(self.sheets, self.sheet_dims)):
            w, h = dims
            area = w * h
            used = sum(pp.net_area() for pp in sheet)
            util = 100.0 * used / area if area > 0 else 0.0
            job_label = self.sheet_job_label[i]
            prefix = f"{job_label} -- " if job_label else ""
            label = (f"{prefix}Sheet {i + 1} / {n_sheets}\n"
                     f"{w:g}x{h:g} mm, {len(sheet)} parts, {util:.1f}% util")
            item = QtWidgets.QListWidgetItem(
                self._sheet_thumbnail(w, h, sheet) if sheet else QtGui.QIcon(),
                label,
            )
            item.setData(QtCore.Qt.UserRole, i)
            self.layout_list.addItem(item)
        if 0 <= self.current_sheet_index < self.layout_list.count():
            self.layout_list.setCurrentRow(self.current_sheet_index)

    def _clear_layout_candidates(self):
        """Drops every accumulated Layout Results candidate so the list
        reflects only the run about to start. Each run records exactly one
        candidate, so old runs' results never linger next to the new one."""
        self.layout_candidates = []
        self._refresh_layout_list()

    def _on_layout_candidate_clicked(self, item):
        idx = item.data(QtCore.Qt.UserRole)
        if 0 <= idx < len(self.sheets):
            self.current_sheet_index = idx
            self._show_sheet()

    def _run(self):
        # Kept for integrations that called the old quick-run helper.
        # The public Run Nesting action always uses the optimizer below.
        if not self._extracted:
            self._log("Nothing to nest -- rescan/add parts first.")
            return
        if not self._require_preflight("run nesting"):
            return
        self._clear_layout_candidates()

        start_time = time.time()
        parts = self._table_parts()
        self._last_run_parts = parts
        self._last_run_used_ga = False
        self._last_run_ga_kwargs = None
        self.commit_btn.setEnabled(False)  # stale until this run finishes below
        self.sheets = []
        self.sheet_dims = []
        self.sheet_job_label = []
        self.sheet_prices = []
        self.used_stock = []
        self.unplaced = []

        if self._inventory_template is not None:
            # Fresh copy each run: StockSheet.quantity is mutated as stock
            # is consumed, and re-clicking "Run Nesting" (e.g. after tweaking
            # a rotation) should reflect one clean run, not cumulative
            # depletion across clicks.
            working_stock = copy.deepcopy(self._inventory_template)
            results = inv.run_job(parts, working_stock, kerf=self.part_spacing.value(),
                                   joint_stock_optimization=self.joint_stock_optimization.isChecked(),
                                   prefer_remnants=self.prefer_remnants.isChecked(),
                                   **self._margin_kwargs())
            for r in results:
                job_label = f"{r.material} / {r.thickness:g} mm"
                for sheet, stock in zip(r.sheets, r.sheet_stock):
                    self.sheets.append(sheet)
                    self.sheet_dims.append((stock.width, stock.height))
                    self.sheet_job_label.append(job_label)
                    self.sheet_prices.append(stock.material_cost())
                    self.used_stock.append(stock)
                self.unplaced.extend(r.unplaced)
                placed = sum(len(s) for s in r.sheets)
                self._log(f"{job_label}: nested {placed} part(s) onto {len(r.sheets)} sheet(s).")
                for note in r.notes:
                    self._log(f"  [note] {note}")
        else:
            n = nester.Nester(self.sheet_w.value(), self.sheet_h.value(), kerf=self.part_spacing.value(),
                               **self._margin_kwargs())
            for p in parts:
                n.add_part(p)
            sheets, self.unplaced = n.run()
            self.sheets = [s for s in sheets if s]
            self.sheet_dims = [(self.sheet_w.value(), self.sheet_h.value())] * len(self.sheets)
            self.sheet_job_label = [""] * len(self.sheets)
            self.sheet_prices = [None] * len(self.sheets)  # no inventory -- no price data
            self.used_stock = [None] * len(self.sheets)
            total_placed = sum(len(s) for s in self.sheets)
            self._log(f"Nested {total_placed} part(s) onto {len(self.sheets)} sheet(s).")

        self.current_sheet_index = 0
        if self.unplaced:
            self._log(f"[warn] {len(self.unplaced)} part(s) did not fit: {', '.join(self.unplaced)}")
        self.export_btn.setEnabled(bool(self.sheets))
        self.commit_btn.setEnabled(bool(self.sheets) and self._inventory_path is not None)
        self._set_stats(self._job_stats_text(time.time() - start_time))
        self._update_placed_counts()
        self._record_candidate("Run Nesting")
        self._show_sheet()
        self._write_auto_report()

    def _optimize_ordering(self):
        if not self._extracted:
            self._log("Nothing to nest -- rescan/add parts first.")
            return
        if not self._require_preflight("run nesting"):
            return
        self._clear_layout_candidates()

        start_time = time.time()
        self._search_started_at = time.monotonic()
        self._stop_requested = False
        parts = self._table_parts()
        self._last_run_parts = parts
        self.commit_btn.setEnabled(False)  # stale until this run finishes below

        buttons = self.findChildren(QtWidgets.QPushButton)
        for b in buttons:
            b.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._search_timer.start()
        self.busy_changed.emit(True)
        QtWidgets.QApplication.processEvents()

        def progress(gen, fitness, best_sheets, sheet_w, sheet_h):
            live_gen[0] = gen
            sheets_used, unplaced_count, neg_util = fitness
            elapsed = time.time() - start_time
            self._log(f"  GA gen {gen}: best so far = {sheets_used} sheet(s), "
                       f"{unplaced_count} unplaced, {-neg_util * 100:.1f}% utilization")
            self._set_stats(
                f"Elapsed: {elapsed:.1f}s -- generation {gen}, {sheets_used} sheet(s) so far, "
                f"{-neg_util * 100:.1f}% efficiency on the current pass"
            )
            # Live preview: redraw the current best ordering's first sheet as
            # the search improves, rather than only showing the final result.
            first_sheet = best_sheets[0] if best_sheets else []
            self.sheet_label.setText(f"Live preview -- generation {gen}...")
            self.preview.set_sheet(sheet_w, sheet_h, first_sheet, self._microjoint_cfg(), self._common_line_cfg())
            QtWidgets.QApplication.processEvents()

        # Live per-part placement animation: every candidate's parts appear
        # on the sheet one at a time as the search evaluates it, instead of
        # jumping straight to the finished best layout. Throttled to ~30fps
        # so a fast GA doesn't grind itself to a crawl repainting each and
        # every placement -- when placements come faster than that, only the
        # latest one per frame is drawn, which is all the eye can use anyway.
        live_gen = [0]
        live_last_paint = [0.0]

        def placement(sheets, _p_unplaced, idx, total, name, sheet_idx, placed_ok, sheet_w, sheet_h):
            now = time.monotonic()
            if now - live_last_paint[0] < 0.03:
                return
            live_last_paint[0] = now
            clean = [s for s in sheets if s]
            if not clean:
                return
            display = clean[sheet_idx] if sheet_idx < len(clean) else clean[0]
            verb = "placed" if placed_ok else "no fit"
            self.sheet_label.setText(
                f"Live: gen {live_gen[0]} -- {idx + 1}/{total} {name} {verb} "
                f"(on sheet {sheet_idx + 1}) -- {len(clean)} sheet(s) so far..."
            )
            self.preview.set_sheet(sheet_w, sheet_h, display, self._microjoint_cfg(), self._common_line_cfg())
            QtWidgets.QApplication.processEvents()

        # Fix a seed up front (rather than leaving it random) so that if this
        # run is later committed, _commit_inventory() can replay the exact
        # same GA search instead of risking a different, uncommitted-to
        # ordering winning the re-roll.
        ga_kwargs = dict(
            population_size=self.ga_population.value(), generations=self.ga_generations.value(),
            progress_callback=progress,
            placement_callback=placement,
            optimize_rotations=self.ga_optimize_rotations.isChecked(),
            parallel=self.ga_parallel.isChecked(),
            seed=random.randint(0, 2**31 - 1),
        )

        self._log(f"Running genetic ordering search (population={self.ga_population.value()}, "
                   f"generations={self.ga_generations.value()}). Press Stop to keep the best result so far.")
        self.sheets = []
        self.sheet_dims = []
        self.sheet_job_label = []
        self.sheet_prices = []
        self.used_stock = []
        self.unplaced = []
        try:
            if self._inventory_template is not None:
                working_stock = copy.deepcopy(self._inventory_template)
                results = inv.run_job(parts, working_stock, kerf=self.part_spacing.value(),
                                       use_ga=True, ga_kwargs=ga_kwargs,
                                       joint_stock_optimization=self.joint_stock_optimization.isChecked(),
                                       true_joint_stock_optimization=self.true_joint_stock_optimization.isChecked(),
                                       prefer_remnants=self.prefer_remnants.isChecked(),
                                       should_stop=lambda: self._stop_requested,
                                       **self._margin_kwargs())
                self._last_run_used_ga = True
                # The live-placement callback is observation-only; omit it
                # from the replay record so _commit_inventory()'s re-run
                # doesn't animate (there's no final _show_sheet() after a
                # commit, so a live frame would otherwise be left frozen on
                # a half-assembled candidate on-screen).
                self._last_run_ga_kwargs = {k: v for k, v in ga_kwargs.items() if k != "placement_callback"}
                for r in results:
                    job_label = f"{r.material} / {r.thickness:g} mm"
                    for sheet, stock in zip(r.sheets, r.sheet_stock):
                        self.sheets.append(sheet)
                        self.sheet_dims.append((stock.width, stock.height))
                        self.sheet_job_label.append(job_label)
                        self.sheet_prices.append(stock.material_cost())
                        self.used_stock.append(stock)
                    self.unplaced.extend(r.unplaced)
                    placed = sum(len(s) for s in r.sheets)
                    self._log(f"{job_label}: GA-nested {placed} part(s) onto {len(r.sheets)} sheet(s).")
                    for note in r.notes:
                        self._log(f"  [note] {note}")
            else:
                sheets, unplaced = genetic.optimize_order(
                    self.sheet_w.value(), self.sheet_h.value(), parts, kerf=self.part_spacing.value(),
                    should_stop=lambda: self._stop_requested,
                    **self._margin_kwargs(), **ga_kwargs,
                )
                self._last_run_used_ga = False  # nothing to replay -- no inventory to commit against
                self._last_run_ga_kwargs = None
                self.sheets = [s for s in sheets if s]
                self.sheet_dims = [(self.sheet_w.value(), self.sheet_h.value())] * len(self.sheets)
                self.sheet_job_label = [""] * len(self.sheets)
                self.sheet_prices = [None] * len(self.sheets)
                self.used_stock = [None] * len(self.sheets)
                self.unplaced = unplaced
                total_placed = sum(len(s) for s in self.sheets)
                self._log(f"GA result: nested {total_placed} part(s) onto {len(self.sheets)} sheet(s).")
        except Exception as e:
            self._log(f"[error] genetic ordering search failed: {e}")
            return
        finally:
            self._search_timer.stop()
            self._search_started_at = None
            for b in buttons:
                b.setEnabled(True)
            self.busy_changed.emit(False)

        self.current_sheet_index = 0
        if self.unplaced:
            self._log(f"[warn] {len(self.unplaced)} part(s) did not fit: {', '.join(self.unplaced)}")
        self.export_btn.setEnabled(bool(self.sheets))
        self.commit_btn.setEnabled(
            bool(self.sheets) and self._inventory_path is not None and self._last_run_used_ga
        )
        self._set_stats(self._job_stats_text(time.time() - start_time))
        self._update_placed_counts()
        self._record_candidate("Run Nesting")
        self._show_sheet()
        self._write_auto_report()

    def _commit_inventory(self):
        if self._inventory_path is None or not self._last_run_parts:
            return
        if not self._require_preflight("commit inventory", require_layout=True):
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Commit to inventory",
            f"This will deduct the sheets shown in the last nesting run from\n"
            f"{self._inventory_path}\nand record it in that file's audit log.\n\n"
            f"Only do this once you've actually cut these sheets. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        try:
            # If the last run went through the GA, replay it with the exact
            # same seed -- otherwise this fresh run could legitimately land
            # on a different ordering than the one just previewed, silently
            # committing something the user never saw.
            results = inv.commit_job(
                self._last_run_parts, self._inventory_path, kerf=self.part_spacing.value(),
                use_ga=self._last_run_used_ga, ga_kwargs=self._last_run_ga_kwargs,
                joint_stock_optimization=self.joint_stock_optimization.isChecked(),
                true_joint_stock_optimization=self.true_joint_stock_optimization.isChecked(),
                prefer_remnants=self.prefer_remnants.isChecked(),
                capture_remnants=self.remnant_capture_enabled.isChecked(),
                min_remnant_dimension=self.remnant_min_dimension.value(),
                **self._margin_kwargs(),
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Nesting", f"Could not commit to inventory: {e}")
            return
        for r in results:
            for note in r.notes:
                self._log(f"[committed] {r.material} / {r.thickness:g} mm: {note}")
        # Reload from disk -- it's the new source of truth after commit.
        self._inventory_template = inv.load_inventory(self._inventory_path)
        self.inventory_status.setText(
            f"{len(self._inventory_template)} stock entries -- committed just now "
            f"(see {os.path.basename(self._inventory_path)}.log.jsonl for the audit trail)."
        )
        self._log(f"Committed to {self._inventory_path}.")
        self.commit_btn.setEnabled(False)  # last run's parts are now already committed
        self.inventory_changed.emit()

    def _microjoint_cfg(self):
        if not self.microjoints_enabled.isChecked():
            return None
        return {"tab_width": self.microjoint_width.value(), "target_spacing": self.microjoint_spacing.value()}

    def _common_line_cfg(self):
        return {} if self.common_line_enabled.isChecked() else None

    def set_currency(self, code):
        """Called by the native app's Stock tab currency picker (see
        StockPanel), and by _load_job() restoring a saved job -- display
        only, see self.currency_code's own comment. Emits currency_changed
        regardless of the source, so a Stock tab picker stays in sync even
        when a job load is what actually changed it."""
        self.currency_code = code
        self.currency_changed.emit(code)

    def _show_sheet(self):
        self.part_selector.blockSignals(True)
        self.part_selector.clear()
        if not self.sheets:
            self.sheet_label.setText("No sheets yet")
            self.sheet_pos_label.setText("Sheet -- of --")
            self.preview.set_sheet(self.sheet_w.value(), self.sheet_h.value(), [])
            self.part_selector.blockSignals(False)
            self._selected_part_idx = None
            self._refresh_sheet_selector()
            return
        idx = self.current_sheet_index
        sheet = self.sheets[idx]
        w, h = self.sheet_dims[idx]
        area = w * h
        util = 100.0 * sum(pp.net_area() for pp in sheet) / area if area > 0 else 0.0
        job_label = self.sheet_job_label[idx]
        prefix = f"{job_label} -- " if job_label else ""
        self.sheet_label.setText(
            f"{prefix}Sheet {idx + 1} / {len(self.sheets)} -- {w:g}x{h:g} mm, "
            f"{len(sheet)} parts, {util:.1f}% utilization"
        )
        self.preview.set_sheet(w, h, sheet, self._microjoint_cfg(), self._common_line_cfg())
        self.sheet_pos_label.setText(f"Sheet {idx + 1} of {len(self.sheets)}")
        for i, pp in enumerate(sheet):
            marker = " (mirrored)" if pp.mirrored else ""
            self.part_selector.addItem(f"{i}: {pp.name}{marker}")
        self.part_selector.blockSignals(False)
        self._selected_part_idx = 0 if self.part_selector.count() else None
        self._refresh_sheet_selector()
        if self.layout_list.count():
            self.layout_list.setCurrentRow(min(idx, self.layout_list.count() - 1))

    def _refresh_sheet_selector(self):
        """Rebuilds the sheet-jump dropdown to match self.sheets -- cheap
        enough to redo on every _show_sheet() call (jobs here top out at a
        few dozen sheets, nothing like the hundred-plus row tree a shop's
        real nesting software has to virtualize)."""
        self.sheet_selector.blockSignals(True)
        self.sheet_selector.clear()
        for i, sheet in enumerate(self.sheets):
            w, h = self.sheet_dims[i]
            area = w * h
            util = 100.0 * sum(pp.net_area() for pp in sheet) / area if area > 0 else 0.0
            job_label = self.sheet_job_label[i]
            prefix = f"{job_label} -- " if job_label else ""
            self.sheet_selector.addItem(f"Sheet {i + 1}: {prefix}{w:g}x{h:g} mm, {util:.0f}% util")
        if self.sheets:
            self.sheet_selector.setCurrentIndex(self.current_sheet_index)
        self.sheet_selector.blockSignals(False)

    def _on_sheet_selector_activated(self, idx):
        if 0 <= idx < len(self.sheets):
            self.current_sheet_index = idx
            self._show_sheet()

    def _prev_sheet(self):
        if self.sheets and self.current_sheet_index > 0:
            self.current_sheet_index -= 1
            self._show_sheet()

    def _next_sheet(self):
        if self.sheets and self.current_sheet_index < len(self.sheets) - 1:
            self.current_sheet_index += 1
            self._show_sheet()

    # ------------------------------------------------------- manual editing

    def _toggle_layout_results(self, checked):
        """Collapsible Layout Results column: hide/show the thumbnail list so
        the sheet preview can claim the whole splitter width."""
        if not hasattr(self, "layouts_col"):
            return
        self.layouts_col.setVisible(checked)
        if checked and getattr(self, "splitter", None) is not None:
            total = max(self.splitter.width(), 400)
            self.splitter.setSizes([total * 3 // 4, total // 4])

    def _on_manual_toggled(self, checked):
        self.manual_box.setVisible(checked)

    def _on_true_joint_toggled(self, checked):
        # "Search size combinations" always uses "Joint stock optimization"'s
        # own algorithm as its starting plan (see stock_solver.py), so the
        # base checkbox's value is moot whenever this one is on -- force it
        # checked and disabled instead of leaving a confusing redundant
        # control, and give control back when this one is unchecked again.
        if checked:
            self.joint_stock_optimization.setChecked(True)
        self.joint_stock_optimization.setEnabled(not checked)

    def _on_preview_part_clicked(self, idx):
        if 0 <= idx < self.part_selector.count():
            self.part_selector.setCurrentIndex(idx)
            if not self.manual_box.isVisible():
                # The box is collapsed by default to keep the preview tall;
                # explicitly clicking a part in the preview is a clear signal
                # the user wants to adjust it, so surface the controls.
                self.manual_toggle.setChecked(True)

    def _on_preview_coords(self, xy):
        if xy is None:
            self.coord_label.setText("X: --   Y: -- mm")
            return
        x, y = xy
        self.coord_label.setText(f"X: {x:.1f}   Y: {y:.1f} mm")

    def _on_part_selector_changed(self, idx):
        self._selected_part_idx = idx if idx >= 0 else None

    def _apply_manual_edit(self):
        if not self.sheets or self._selected_part_idx is None:
            self._log("Manual edit: no part selected.")
            return
        sheet = self.sheets[self.current_sheet_index]
        idx = self._selected_part_idx
        if not (0 <= idx < len(sheet)):
            return
        pp = sheet[idx]
        dx, dy, rot = self.manual_dx.value(), self.manual_dy.value(), self.manual_rotation.value()
        if not dx and not dy and not rot:
            self._log("Manual edit: dX/dY/Rotate are all 0, nothing to apply.")
            return

        new_outer = list(pp.points)
        new_holes = [list(h) for h in pp.holes]
        if rot:
            minx, miny, maxx, maxy = geometry.polygon_bbox(new_outer)
            pivot = ((minx + maxx) / 2, (miny + maxy) / 2)
            new_outer = geometry.rotate_points(new_outer, rot, pivot)
            new_holes = [geometry.rotate_points(h, rot, pivot) for h in new_holes]
        if dx or dy:
            new_outer = geometry.translate_points(new_outer, dx, dy)
            new_holes = [geometry.translate_points(h, dx, dy) for h in new_holes]

        w, h = self.sheet_dims[self.current_sheet_index]
        out_of_bounds = not geometry.polygon_fits_in_sheet(new_outer, w, h)
        overlaps = next(
            (other.name for j, other in enumerate(sheet)
             if j != idx and nfp.intersection_area(new_outer, other.points) > 1e-6),
            None,
        )
        if self.prohibit_overlap.isChecked() and (out_of_bounds or overlaps):
            reason = "would fall outside the sheet bounds" if out_of_bounds else f"would overlap {overlaps}"
            self._log(f"[warn] manual edit rejected: {pp.name} {reason}.")
            return
        if out_of_bounds:
            self._log(f"[warn] {pp.name} now falls outside the sheet bounds (overlap check disabled).")
        if overlaps:
            self._log(f"[warn] {pp.name} now overlaps {overlaps} (overlap check disabled).")

        sheet[idx] = nester.PlacedPart(
            name=pp.name, points=new_outer, holes=new_holes,
            rotation=(pp.rotation + rot) % 360, sheet_index=pp.sheet_index,
        )
        self._log(f"Manually adjusted {pp.name}: dX={dx:g}, dY={dy:g}, rotate+={rot:g} deg.")
        self._show_sheet()
        self.part_selector.setCurrentIndex(idx)

    # -------------------------------------------------------------- export

    def _export_dxf(self):
        if not self.sheets:
            return
        if not self._require_preflight("export DXF", require_layout=True):
            return
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Export DXF -- choose a folder")
        if not out_dir:
            return
        microjoint_cfg = self._microjoint_cfg()
        label_cfg = {"height": self.label_height.value()} if self.label_enabled.isChecked() else None
        common_line = self.common_line_enabled.isChecked()
        if common_line and microjoint_cfg:
            self._log("[note] Common-line cutting doesn't combine with Microjoints -- Microjoints wins for this export.")
        grouped_sheets = {}
        for i, sheet in enumerate(self.sheets):
            keys = {(pp.material, pp.thickness) for pp in sheet}
            if len(keys) != 1:
                QtWidgets.QMessageBox.critical(self, "Export blocked", "A layout contains mixed material/thickness parts.")
                return
            grouped_sheets.setdefault(next(iter(keys)), []).append((i, sheet))

        manifest_rows = []
        for group_number, ((material, thickness), entries) in enumerate(sorted(grouped_sheets.items()), 1):
            safe_material = "".join(c if c.isalnum() or c in "-_" else "_" for c in material)
            group_name = f"{group_number:02d}_{safe_material}_{thickness:.2f}mm"
            group_dir = os.path.join(out_dir, group_name)
            os.makedirs(group_dir, exist_ok=True)
            for sheet_number, (i, sheet) in enumerate(entries, 1):
                w, h = self.sheet_dims[i]
                polys = [(pp.name.replace(" ", "_"), pp.points, pp.holes) for pp in sheet]
                filename = f"sheet_{sheet_number:03d}_{safe_material}_{thickness:.2f}mm.dxf"
                path = os.path.join(group_dir, filename)
                dxf_writer.write_dxf(path, polys, w, h, microjoints=microjoint_cfg, labels=label_cfg,
                                      common_line_cutting=common_line)
                with open(path, "rb") as dxf_file:
                    checksum = hashlib.sha256(dxf_file.read()).hexdigest()
                manifest_rows.append([group_name, filename, material, thickness, w, h, len(sheet),
                                      checksum, "; ".join(pp.name for pp in sheet)])
                self._log(f"Wrote {path}")

        manifest_path = os.path.join(out_dir, "release_manifest.csv")
        with open(manifest_path, "w", newline="") as manifest_file:
            writer = csv.writer(manifest_file)
            writer.writerow(["release_group", "file", "material_grade", "thickness_mm", "sheet_width_mm",
                             "sheet_height_mm", "parts_placed", "sha256", "part_names"])
            writer.writerows(manifest_rows)
        self._log(f"Wrote strict release manifest: {manifest_path}")
        return

        for i, sheet in enumerate(self.sheets):
            if not sheet:
                continue
            w, h = self.sheet_dims[i]
            job_label = self.sheet_job_label[i]
            suffix = "_" + job_label.replace(" ", "").replace("/", "_") if job_label else ""
            polys = [(pp.name.replace(" ", "_"), pp.points, pp.holes) for pp in sheet]
            path = os.path.join(out_dir, f"sheet_{i + 1}{suffix}.dxf")
            dxf_writer.write_dxf(path, polys, w, h, microjoints=microjoint_cfg, labels=label_cfg,
                                  common_line_cutting=common_line)
            extras = ", ".join(n for n, on in (("microjoints", microjoint_cfg), ("labels", label_cfg),
                                                ("common-line cutting", common_line and not microjoint_cfg)) if on)
            self._log(f"Wrote {path}" + (f" (with {extras})" if extras else ""))

    def _export_report(self):
        """A self-contained HTML nesting report -- job totals (sheets,
        parts, efficiency, cut time, cost) plus a per-sheet table with an
        embedded PNG of each sheet's actual layout. No new dependency: HTML
        is universally viewable and printable to PDF straight from any
        browser, and a base64-embedded <img> keeps the whole report one
        file, nothing to keep track of alongside it.

        The HTML itself is built by _build_report_html(), shared with the
        automatic post-run report (_write_auto_report), so the two can't
        drift apart."""
        if not self.sheets:
            self._log("Nothing to report -- run a nesting job first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export nesting report", "nesting_report.html", "HTML (*.html)"
        )
        if not path:
            return
        self._write_report_file(path, self._build_report_html())

    def _job_financials(self):
        """Estimated cost/scrap-value breakdown for the currently shown
        layout (self.sheets/used_stock). Cost comes from
        StockSheet.weight_kg()/material_cost() (width x height x thickness
        x density_g_cm3 x price_per_kg -- see inventory.py) -- an entry
        missing either density or price simply doesn't contribute, rather
        than the whole estimate failing.

        A remnant's own material_cost() isn't cash spent this job (it was
        already on hand) -- it's reported separately as "money saved" by
        not having to buy that same weight of material new, which is the
        actual point of `prefer_remnants` existing at all.

        "Scrap value" mirrors the SAME remnant-capture decision
        commit_job() would actually make for each sheet (same
        `remnant.largest_empty_rect()` call, same Auto-record-new-remnants
        checkbox and minimum-dimension setting) so it only values the part
        of a sheet's waste that would NOT become a new remnant -- i.e.
        material that's actually sold as scrap or discarded. This is an
        ESTIMATE: a real Commit could land differently if the inventory
        file on disk has changed since this layout was previewed.

        Returns a dict of totals; any total with nothing priced behind it
        is None rather than a misleading 0."""
        capture_enabled = self.remnant_capture_enabled.isChecked()
        min_dim = self.remnant_min_dimension.value()

        new_sheets = new_weight = new_cost = 0.0
        remnant_sheets = remnant_weight = remnant_savings = 0.0
        scrap_weight = scrap_value = 0.0
        any_new_priced = any_remnant_priced = any_scrap_priced = False

        used_stock = getattr(self, "used_stock", []) or []
        for sheet, stock in zip(self.sheets, used_stock):
            if stock is None:
                continue
            weight = stock.weight_kg()
            cost = stock.material_cost()
            if stock.is_remnant:
                remnant_sheets += 1
                if weight is not None:
                    remnant_weight += weight
                if cost is not None:
                    remnant_savings += cost
                    any_remnant_priced = True
            else:
                new_sheets += 1
                if weight is not None:
                    new_weight += weight
                if cost is not None:
                    new_cost += cost
                    any_new_priced = True

            if weight is None or stock.density_g_cm3 is None or not sheet:
                continue
            used_weight = (sum(pp.net_area() for pp in sheet) * stock.thickness
                           * stock.density_g_cm3 / 1e6)
            waste_weight = max(0.0, weight - used_weight)
            captured_weight = 0.0
            if capture_enabled:
                obstacle_bboxes = [geometry.polygon_bbox(pp.points) for pp in sheet]
                found = rem.largest_empty_rect(stock.width, stock.height, obstacle_bboxes)
                if found is not None:
                    _x, _y, rect_w, rect_h = found
                    if rect_w >= min_dim and rect_h >= min_dim:
                        captured_weight = rect_w * rect_h * stock.thickness * stock.density_g_cm3 / 1e6
            true_scrap = max(0.0, waste_weight - captured_weight)
            scrap_weight += true_scrap
            # Per-entry, not a global rate -- scrap value genuinely varies
            # a lot by material (see StockSheet.scrap_price_per_kg).
            if stock.scrap_price_per_kg is not None:
                scrap_value += true_scrap * stock.scrap_price_per_kg
                any_scrap_priced = True

        net_cost = None
        if any_new_priced:
            net_cost = new_cost - (scrap_value if any_scrap_priced else 0.0)

        return {
            "new_sheets": int(new_sheets), "new_weight_kg": new_weight,
            "new_material_cost": new_cost if any_new_priced else None,
            "remnant_sheets": int(remnant_sheets), "remnant_weight_kg": remnant_weight,
            "remnant_savings": remnant_savings if any_remnant_priced else None,
            "scrap_weight_kg": scrap_weight,
            "scrap_value": scrap_value if any_scrap_priced else None,
            "net_cost": net_cost,
        }

    def _build_report_html(self):
        """Shared HTML body for both the manual Export Report and the
        automatic post-run report. Reads self.sheets/sheet_dims/used_stock
        -- i.e. whatever layout is currently shown -- and embeds one PNG
        preview per sheet plus a stock-consumption section."""
        microjoint_cfg = self._microjoint_cfg()
        common_line_cfg = self._common_line_cfg()
        currency_symbol = CURRENCY_SYMBOLS.get(self.currency_code, "")
        rows = []
        for i, sheet in enumerate(self.sheets):
            w, h = self.sheet_dims[i]
            area = w * h
            util = 100.0 * sum(pp.net_area() for pp in sheet) / area if area > 0 else 0.0
            job_label = self.sheet_job_label[i] or "--"
            price = self.sheet_prices[i] if i < len(self.sheet_prices) else None
            stock_i = self.used_stock[i] if i < len(getattr(self, "used_stock", [])) else None
            if price is None:
                price_text = "unpriced"
            elif stock_i is not None and stock_i.is_remnant:
                price_text = f"{currency_symbol}{price:,.2f} saved"  # remnant: already on hand, not cash spent this job
            else:
                price_text = f"{currency_symbol}{price:,.2f}"

            preview = SheetPreview()
            preview.resize(400, 400)
            preview.set_sheet(w, h, sheet, microjoint_cfg, common_line_cfg)
            pix = preview.grab()
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.WriteOnly)
            pix.save(buf, "PNG")
            img_b64 = base64.b64encode(bytes(buf.data())).decode("ascii")

            mirrored = sum(1 for pp in sheet if pp.mirrored)
            parts_text = str(len(sheet)) if not mirrored else f"{len(sheet)} ({mirrored} mirrored)"
            rows.append(
                f"<tr><td><img src='data:image/png;base64,{img_b64}' width='220'></td>"
                f"<td>{i + 1}</td><td>{job_label}</td><td>{w:g} x {h:g} mm</td>"
                f"<td>{parts_text}</td><td>{util:.1f}%</td><td>{price_text}</td></tr>"
            )

        total_placed = sum(len(s) for s in self.sheets)
        total_area = sum(w * h for w, h in self.sheet_dims)
        used_area = sum(pp.net_area() for s in self.sheets for pp in s)
        total_util = 100.0 * used_area / total_area if total_area > 0 else 0.0
        cut_length = self._total_cut_length()
        cut_minutes = cut_length / self.cut_speed.value() if self.cut_speed.value() > 0 else 0.0
        unplaced_text = ", ".join(self.unplaced) if self.unplaced else "none"

        fin = self._job_financials()

        def money(v):
            return f"{currency_symbol}{v:,.2f}" if v is not None else "n/a (no priced/densitied stock used)"

        cost_text = money(fin["net_cost"])
        financials_section = f"""
<h2>Financials</h2>
<p>Estimate only, from each stock entry's own Price/kg, Density, and Scrap price/kg (Stock tab) --
an entry missing any of these is left out of the corresponding total rather than guessed at.
A remnant's cost isn't cash spent this job (it was already on hand); it's shown separately as money
saved by not buying that weight of material new.</p>
<ul>
  <li>New sheets cut: {fin['new_sheets']} ({fin['new_weight_kg']:.1f} kg) -- material cost: {money(fin['new_material_cost'])}</li>
  <li>Remnants used: {fin['remnant_sheets']} ({fin['remnant_weight_kg']:.1f} kg) -- money saved vs. buying new: {money(fin['remnant_savings'])}</li>
  <li>Estimated scrap (waste not captured as a new remnant): {fin['scrap_weight_kg']:.1f} kg -- recoverable value: {money(fin['scrap_value'])}</li>
  <li><strong>Net material cost (new sheets minus scrap value recovered): {cost_text}</strong></li>
</ul>"""

        # Stock consumed/remaining -- only meaningful when this job ran
        # against an inventory (each sheet has a concrete StockSheet behind it).
        stock_section = ""
        used_stock = getattr(self, "used_stock", []) or []
        if any(s is not None for s in used_stock):
            used_counts = {}
            for s in used_stock:
                if s is None:
                    continue
                key = (s.id, s.material, s.thickness, s.width, s.height)
                used_counts[key] = used_counts.get(key, 0) + 1
            template = self._inventory_template or []
            stock_rows = []
            # A full sheet's id is routinely None (only remnants get an
            # auto-generated id) -- sorting tuples with a bare None
            # alongside a str id raises TypeError in Python 3, so sort on
            # "" in None's place; the actual (possibly-None) sid is still
            # what gets displayed/matched below, unchanged.
            for (sid, mat, thk, w, h), used in sorted(used_counts.items(), key=lambda kv: (kv[0][0] or "", kv[0][1:])):
                match = next(
                    (t for t in template
                     if (t.id, t.material, t.thickness, t.width, t.height) == (sid, mat, thk, w, h)),
                    None,
                )
                if match is None or match.quantity is None:
                    on_hand = "unlimited"
                    remaining = "unlimited"
                else:
                    on_hand = str(match.quantity)
                    remaining = str(max(match.quantity - used, 0))
                label = f"{mat} / {thk:g} mm, {w:g}x{h:g} mm"
                stock_rows.append(
                    f"<tr><td>{label}</td><td>{used}</td><td>{on_hand}</td><td>{remaining}</td></tr>"
                )
            stock_section = f"""
<h2>Stock used</h2>
<p>On-hand is the inventory file's quantity at the start of this job. Committing to inventory
(the Commit action) is a separate, explicit step; simply running a layout never changes the file.</p>
<table>
<tr><th>Stock</th><th>Sheets cut</th><th>On hand</th><th>Remaining</th></tr>
{"".join(stock_rows)}
</table>"""

        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Nesting Report</title>
<style>
  body {{ font-family: sans-serif; margin: 24px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  td, th {{ border: 1px solid #ccc; padding: 6px 10px; text-align: center; }}
  th {{ background: #eef0f2; }}
  ul {{ line-height: 1.6; }}
</style></head>
<body>
<h1>Nesting Report</h1>
<p>Generated: {datetime.datetime.now().isoformat(timespec="seconds")}</p>
<h2>Job totals</h2>
<ul>
  <li>Sheets used: {len(self.sheets)}</li>
  <li>Parts placed: {total_placed}</li>
  <li>Overall efficiency: {total_util:.1f}%</li>
  <li>Estimated cut time: {cut_minutes:.1f} min ({cut_length / 1000:.1f} m of cuts)</li>
  <li>Net material cost: {cost_text}</li>
  <li>Unplaced parts: {unplaced_text}</li>
</ul>
{financials_section}
{stock_section}
<h2>Sheets</h2>
<table>
<tr><th>Layout</th><th>#</th><th>Material / Thickness</th><th>Size</th><th>Parts</th><th>Efficiency</th><th>Cost/Saved</th></tr>
{"".join(rows)}
</table>
</body></html>"""

    def _write_report_file(self, path, html):
        with open(path, "w") as f:
            f.write(html)
        self._log(f"Wrote nesting report to {path}.")

    def _write_auto_report(self):
        """Writes the post-run HTML report to the configured folder (the
        same _build_report_html() the manual Export Report uses), unless
        disabled or there's nothing to report. Fire-and-forget: a report
        is a convenience artifact, not something worth blocking a run on."""
        if not self.sheets:
            return
        if not getattr(self, "auto_report_enabled", None) or not self.auto_report_enabled.isChecked():
            return
        folder = self.reports_dir.text().strip()
        if not folder:
            self._log("[report] auto-report folder is empty, skipped.")
            return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self._log(f"[report] could not create '{folder}': {e}")
            return
        filename = "nesting_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".html"
        path = os.path.join(folder, filename)
        try:
            self._write_report_file(path, self._build_report_html())
            self.report_status.setText(f"Last report written: {path}")
        except Exception as e:
            self.report_status.setText(f"Report FAILED: {e}")
            self._log(f"[report] auto-report failed: {e}")
