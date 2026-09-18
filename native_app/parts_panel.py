"""
parts_panel.py
--------------
The native app's "Parts" tab: step 1 of a job. It hosts the parts
`QTableWidget` itself (the same widget the NestingPanel builds -- quantity/
material/thickness/rotations are edited here), an "Add parts" button wired to
the panel's part source scan, and the preflight readiness check whose
pass/fail unlocks the Nesting tab's Run Nesting / Commit / Export / Report
buttons. Below the table, a gallery renders every part large, with a detail
panel on selection.

Refreshes automatically on `NestingPanel.parts_changed` (Add Parts, New Job,
Open Job, Remove Selected Part) and on `NestingPanel.preflight_ready`.
"""

from PySide6 import QtCore, QtGui, QtWidgets

import geometry
import part_colors


def _draw_part(painter, rect, outer, holes, fill, outline):
    """Normalize a part's outer contour + holes into `rect` (keeping aspect
    ratio, Y flipped so it looks like the sheet on screen) and paint it."""
    if len(outer) < 3:
        return
    min_x, min_y, max_x, max_y = geometry.polygon_bbox(outer)
    w, h = max_x - min_x, max_y - min_y
    if w <= 0 or h <= 0:
        return
    margin = rect.width() * 0.04
    scale = min((rect.width() - 2 * margin) / w, (rect.height() - 2 * margin) / h)
    painter.save()
    painter.translate(rect.center().x(), rect.center().y())
    painter.scale(scale, -scale)
    painter.translate(-(min_x + w / 2), -(min_y + h / 2))

    path = QtGui.QPainterPath()
    path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in outer]))
    for hole in holes:
        if len(hole) >= 3:
            path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in hole]))
    painter.setPen(QtGui.QPen(outline, 1.5 / scale))
    painter.setBrush(fill)
    painter.drawPath(path)
    painter.restore()


def _render_icon(summary, size=150):
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    _draw_part(painter, QtCore.QRectF(0, 0, size, size), summary["outer"],
               summary["holes"],
               fill=part_colors.color_for(summary["name"]),
               outline=part_colors.outline_for(summary["name"]))
    painter.end()
    return pix


class PartCanvas(QtWidgets.QWidget):
    """The big render on the right of the Parts tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PartCanvas")
        self.setMinimumSize(220, 220)
        self._data = None

    def set_part(self, data):
        self._data = data
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#f4f6fa"))
        if not self._data:
            painter.setPen(QtGui.QColor("#8a8f98"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Select a part from the list")
            return
        _draw_part(
            painter, self.rect().adjusted(16, 16, -16, -16),
            self._data["outer"], self._data["holes"],
            fill=part_colors.color_for(self._data["name"]),
            outline=part_colors.outline_for(self._data["name"]),
        )


class PartsPanel(QtWidgets.QWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self._summaries = []

        layout = QtWidgets.QVBoxLayout(self)

        hint = QtWidgets.QLabel(
            "Step 1 of the job: the geometry the run will nest. Load parts below, "
            "edit quantity / material / thickness in the table, then run the "
            "preflight check -- the Nesting tab stays locked until the parts pass."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton(self.panel.part_source.scan_label)
        self.add_btn.setToolTip("Load part geometry from DXF, parts.json, or .FCStd files.")
        self.add_btn.clicked.connect(self.panel._rescan)
        actions.addWidget(self.add_btn)

        # The Assembly quantity spinbox lives on this tab (it multiplies
        # every part's per-assembly quantity): it's the same widget the panel
        # builds inline in its Layout-basics box, adopted here in the native
        # app. It is hidden in the ribbon's Layout section, so it only
        # appears once.
        actions.addWidget(QtWidgets.QLabel("Assembly qty"))
        actions.addWidget(self.panel.assembly_quantity)
        self.panel.assembly_quantity.show()  # hidden in the ribbon's Layout box

        self.preflight_btn = QtWidgets.QPushButton("Check readiness")
        self.preflight_btn.setToolTip("Verify material, thickness, stock, geometry, and layout before the "
                                      "Nesting tab's Run Nesting / Commit / Export / Report unlock.")
        self.preflight_btn.clicked.connect(self._run_preflight)
        actions.addWidget(self.preflight_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        # A STEP/IGES assembly can produce a "Not ready" list hundreds of
        # lines long (one part with tiny/missing geometry can throw two or
        # three issues each) -- a bare QLabel here would just grow past the
        # window with nothing to scroll, taking the whole app with it. Same
        # bounded-scroll pattern as the part-detail panel's body_scroll below.
        self.preflight_status = QtWidgets.QLabel("")
        self.preflight_status.setWordWrap(True)
        self.preflight_status.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        preflight_scroll = QtWidgets.QScrollArea()
        preflight_scroll.setObjectName("PreflightScroll")
        preflight_scroll.setWidgetResizable(True)
        preflight_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        preflight_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        preflight_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        preflight_scroll.setMaximumHeight(160)
        preflight_scroll.setWidget(self.preflight_status)
        layout.addWidget(preflight_scroll)

        page = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.panel.table.setMinimumHeight(150)
        page.addWidget(self.panel.table)
        page.setCollapsible(0, False)

        gallery = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.list = QtWidgets.QListWidget()
        self.list.setIconSize(QtCore.QSize(150, 150))
        self.list.setSpacing(4)
        self.list.setFixedWidth(240)
        self.list.currentRowChanged.connect(self._on_row_changed)
        gallery.addWidget(self.list)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self.title = QtWidgets.QLabel("No part selected")
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        right_layout.addWidget(self.title)
        # The canvas + detail form live in a scroll area: on a short screen
        # the app can shrink and the details scroll, instead of the tall
        # form+canvas column pinning the whole window's minimum HEIGHT.
        body_scroll = QtWidgets.QScrollArea()
        body_scroll.setObjectName("PartDetailScroll")
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        body_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        self.canvas = PartCanvas()
        body_layout.addWidget(self.canvas, 1)
        self._detail_labels = {}
        details = QtWidgets.QFormLayout()
        for key, label in (
            ("method", "Method"),
            ("material", "Material"),
            ("thickness", "Thickness"),
            ("quantity", "Qty / assembly"),
            ("rotations", "Rotations (deg)"),
            ("grain", "Grain-restricted"),
            ("mirror", "Mirror"),
            ("holes", "Holes"),
            ("net_area", "Net area"),
            ("bbox", "Bounding box"),
            ("cut_length", "Cut length"),
        ):
            lab = QtWidgets.QLabel("--")
            self._detail_labels[key] = lab
            details.addRow(label, lab)
        body_layout.addLayout(details)
        body_layout.addStretch(1)
        body_scroll.setWidget(body)
        right_layout.addWidget(body_scroll, 1)

        gallery.addWidget(right)
        gallery.setStretchFactor(0, 0)
        gallery.setStretchFactor(1, 1)

        page.addWidget(gallery)
        page.setCollapsible(1, False)
        page.setStretchFactor(0, 1)
        page.setStretchFactor(1, 3)
        layout.addWidget(page, 1)

        self.panel.parts_changed.connect(self.refresh)
        self.panel.preflight_ready.connect(self._on_preflight_ready)
        self.refresh()
        self._on_preflight_ready(self.panel.preflight_pass)

    # ----------------------------------------------------- preflight display

    def _run_preflight(self):
        ok, issues = self.panel._run_preflight_check()
        self._show_preflight_result(ok, issues)

    def _on_preflight_ready(self, ok):
        self._show_preflight_result(ok, self.panel.preflight_issues)

    def _show_preflight_result(self, ok, issues):
        if ok:
            self.preflight_status.setStyleSheet("color: #2e7d32; font-weight: 600;")
            self.preflight_status.setText("Ready: material, thickness, stock, geometry, and layout checks are clear.")
        else:
            self.preflight_status.setStyleSheet("color: #b3261e;")
            self.preflight_status.setText("Not ready:\n" + "\n".join(f"• {i}" for i in issues))

    # ------------------------------------------------------------- display

    def refresh(self):
        self._summaries = self.panel.part_summaries()
        self.list.blockSignals(True)
        self.list.clear()
        for s in self._summaries:
            qty = s["quantity_per_assembly"]
            mat = s["material"] or "unspecified"
            item = QtWidgets.QListWidgetItem(
                QtGui.QIcon(_render_icon(s)), f"{s['name']}\n×{qty} · {mat}"
            )
            item.setToolTip(
                f"{s['name']}  ·  {mat}, "
                f"{s['thickness']:g} mm" if s["thickness"] else f"{s['name']}  ·  {mat}"
            )
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self._summaries:
            self.list.setCurrentRow(0)
            self._show(0)
        else:
            self.title.setText("No part selected")
            self.canvas.set_part(None)
            for lab in self._detail_labels.values():
                lab.setText("--")

    def _on_row_changed(self, row):
        if 0 <= row < len(self._summaries):
            self._show(row)

    def _show(self, row):
        s = self._summaries[row]
        self.title.setText(s["name"])
        self.canvas.set_part(s)

        def num(v, unit="", dec=1):
            try:
                if v is None:
                    return "--"
                return f"{v:,.{dec}f} {unit}".strip()
            except (TypeError, ValueError):
                return "--"

        self._detail_labels["method"].setText(s["method"] or "--")
        self._detail_labels["material"].setText(s["material"] or "unspecified")
        self._detail_labels["thickness"].setText(num(s["thickness"], "mm", 2))
        self._detail_labels["quantity"].setText(str(s["quantity_per_assembly"]))
        self._detail_labels["rotations"].setText(s["rotations"] or "--")
        self._detail_labels["grain"].setText("Yes" if s["grain_restricted"] else "No")
        self._detail_labels["mirror"].setText("Yes" if s["mirror"] else "No")
        self._detail_labels["holes"].setText(str(s["hole_count"]))
        self._detail_labels["net_area"].setText(num(s["net_area"], "mm²", 1))
        bw, bh = s["bbox"]
        self._detail_labels["bbox"].setText(f"{bw:,.1f} × {bh:,.1f} mm")
        self._detail_labels["cut_length"].setText(num(s["cut_length"], "mm", 1))