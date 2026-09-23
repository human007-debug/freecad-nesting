"""
parts_panel.py
--------------
Everything the native app shows about the job's PARTS, as three widgets the
main window docks around the sheet canvas:

* `list_page` -- the Parts list (left dock): one row per part with its
  thumbnail, size, material/thickness and a placed/required badge -- green
  once a run placed every copy, amber for some, red for none -- the way
  LaserNest's and Lantek's part lists read at a glance. A filter box on top.
* `table_page` -- the Part Table (bottom dock): the parts `QTableWidget`
  itself (the same widget the NestingPanel builds -- quantity/material/
  thickness/rotations are edited here) under the readiness verdict whose
  pass/fail unlocks Run Nesting / Export / Report.
* `detail_page` -- Part Details (bottom dock): the selected part drawn large
  beside its figures.

Refreshes automatically on `NestingPanel.parts_changed` (Add Parts, New Job,
Open Job, Remove Selected Part), on `preflight_ready`, and when a run ends
(`busy_changed(False)`) so the badges show what it placed.
"""

import math

from PySide6 import QtCore, QtGui, QtWidgets

import geometry
import part_colors
from nesting_widgets import untinted_icon
from native_app import theme


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


# The gallery thumbnail: a rounded tile with the part sitting ON it,
# rather than the part painted edge-to-edge. A rectangular part filled its
# whole 150px icon before, so the list read as a column of saturated
# blocks with the labels running over them.
ICON_SIZE = 72


def _render_icon(summary, size=ICON_SIZE):
    ratio = 2  # rendered at 2x and marked as such: crisp on any display
    pix = QtGui.QPixmap(size * ratio, size * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    tile = QtCore.QRectF(0.5, 0.5, size - 1, size - 1)
    painter.setPen(QtGui.QPen(theme.color("border"), 1))
    painter.setBrush(theme.color("sunken"))
    painter.drawRoundedRect(tile, size * 0.16, size * 0.16)
    _draw_part(painter, tile.adjusted(9, 9, -9, -9), summary["outer"],
               summary["holes"],
               fill=part_colors.color_for(summary["name"]),
               outline=part_colors.outline_for(summary["name"]))
    painter.end()
    return pix


class PartCanvas(QtWidgets.QWidget):
    """The large part render on the Parts tab, drawn the way the sheet
    canvas draws a nest (theme.CANVAS): LibreCAD's black drawing area with
    its dot grid and dashed 10x meta grid, the part shaded in its own color
    (see-through fill, diagonal hatch, outline), a red origin cross at the
    part's lower-left corner and its size in the top-right corner."""

    # Grid squares are 10 mm, stepped up tenfold until they are at least
    # this many px apart -- the same rule as the sheet canvas's grid.
    _GRID_MIN_PX = 9.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PartCanvas")
        self.setMinimumSize(260, 260)
        self._data = None

    def set_part(self, data):
        self._data = data
        self.update()

    def paintEvent(self, event):
        colors = {k: QtGui.QColor(v) for k, v in theme.CANVAS.items()}
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        card = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(card, 10, 10)
        painter.setClipPath(clip)
        painter.fillRect(card, colors["workspace"])
        outer = (self._data or {}).get("outer") or []
        box = geometry.polygon_bbox(outer) if len(outer) >= 3 else None
        if box is None or box[2] <= box[0] or box[3] <= box[1]:
            painter.setPen(colors["hint"])
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "Select a part to see it here")
            return
        min_x, min_y, max_x, max_y = box
        w, h = max_x - min_x, max_y - min_y
        area = card.adjusted(36, 36, -36, -36)
        scale = min(area.width() / w, area.height() / h)
        # Screen position of the part's lower-left corner (its local origin).
        ox = area.center().x() - w * scale / 2
        oy = area.center().y() + h * scale / 2

        def to_screen(x, y):
            return QtCore.QPointF(ox + (x - min_x) * scale, oy - (y - min_y) * scale)

        self._paint_grid(painter, colors, card, scale, ox, oy)

        base = part_colors.color_for(self._data["name"])
        path = QtGui.QPainterPath()
        path.addPolygon(QtGui.QPolygonF([to_screen(x, y) for x, y in outer]))
        path.closeSubpath()
        for hole in self._data.get("holes") or []:
            if len(hole) >= 3:
                hole_path = QtGui.QPainterPath()
                hole_path.addPolygon(QtGui.QPolygonF([to_screen(x, y) for x, y in hole]))
                hole_path.closeSubpath()
                path = path.subtracted(hole_path)
        fill = QtGui.QColor(base)
        fill.setAlpha(80)
        hatch = QtGui.QColor(base)
        hatch.setAlpha(150)
        painter.setPen(QtGui.QPen(base.lighter(125), 1.5))
        painter.setBrush(fill)
        painter.drawPath(path)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(hatch, QtCore.Qt.BDiagPattern))
        painter.drawPath(path)

        o = QtCore.QPointF(ox, oy)
        painter.setPen(QtGui.QPen(colors["origin"], 1))
        painter.drawLine(o - QtCore.QPointF(16, 0), o + QtCore.QPointF(16, 0))
        painter.drawLine(o - QtCore.QPointF(0, 16), o + QtCore.QPointF(0, 16))

        font = painter.font()
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(colors["caption"])
        painter.drawText(card.adjusted(12, 8, -12, -8), QtCore.Qt.AlignRight | QtCore.Qt.AlignTop,
                         f"{w:.1f} \u00d7 {h:.1f} mm")

    def _paint_grid(self, painter, colors, card, scale, ox, oy):
        step = 10.0
        while step * scale < self._GRID_MIN_PX:
            step *= 10.0
        step_px = step * scale
        left, top, right, bottom = card.left(), card.top(), card.right(), card.bottom()
        i0 = math.ceil((left - ox) / step_px)
        j0 = math.ceil((oy - bottom) / step_px)
        xs = [(i, ox + i * step_px) for i in range(i0, i0 + int((right - left) / step_px) + 2)
              if ox + i * step_px <= right]
        ys = [(j, oy - j * step_px) for j in range(j0, j0 + int((bottom - top) / step_px) + 2)
              if oy - j * step_px >= top]
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        meta = QtGui.QPen(colors["meta_grid"], 1)
        meta.setDashPattern([3, 3])
        painter.setPen(meta)
        for i, x in xs:
            if i % 10 == 0:
                painter.drawLine(QtCore.QPointF(x, top), QtCore.QPointF(x, bottom))
        for j, y in ys:
            if j % 10 == 0:
                painter.drawLine(QtCore.QPointF(left, y), QtCore.QPointF(right, y))
        painter.setPen(QtGui.QPen(colors["grid"], 1))
        painter.drawPoints(QtGui.QPolygonF([QtCore.QPointF(x, y) for _i, x in xs for _j, y in ys]))
        painter.restore()


# Row geometry of the Parts list: a thumbnail tile, three lines of text,
# and the badge at the right edge.
_ROW_ICON = 48
_ROW_HEIGHT = 64
_BADGE_ROLE = QtCore.Qt.UserRole + 1   # (text, state) -- state: "none" | "full" | "some" | "zero"
_SUMMARY_ROLE = QtCore.Qt.UserRole + 2


class _PartRowDelegate(QtWidgets.QStyledItemDelegate):
    """Paints one Parts-list row: thumbnail, name / size / material lines,
    and a rounded placed/required badge. Painted, not built from child
    widgets, so a 500-part job scrolls as fast as a 5-part one."""

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), _ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(option.rect)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        if selected or hovered:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(theme.color("selection" if selected else "hover"))
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 6, 6)

        icon = index.data(QtCore.Qt.DecorationRole)
        icon_rect = QtCore.QRect(int(rect.left()) + 8, int(rect.center().y() - _ROW_ICON / 2),
                                 _ROW_ICON, _ROW_ICON)
        if isinstance(icon, QtGui.QIcon):
            icon.paint(painter, icon_rect)

        badge_text, state = index.data(_BADGE_ROLE) or ("", "none")
        font = QtGui.QFont(option.font)
        font.setPixelSize(11)
        font.setBold(True)
        metrics = QtGui.QFontMetrics(font)
        badge_w = metrics.horizontalAdvance(badge_text) + 14 if badge_text else 0
        badge = QtCore.QRectF(rect.right() - 10 - badge_w, rect.center().y() - 10, badge_w, 20)
        if badge_text:
            fill, ink = {
                "full": (theme.color("success_soft"), theme.color("success")),
                "some": (theme.color("sunken"), theme.color("warning")),
                "zero": (theme.color("accent_soft"), theme.color("danger")),
            }.get(state, (theme.color("sunken"), theme.color("text_dim")))
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, 10, 10)
            painter.setFont(font)
            painter.setPen(ink)
            painter.drawText(badge, QtCore.Qt.AlignCenter, badge_text)

        summary = index.data(_SUMMARY_ROLE) or {}
        text_left = icon_rect.right() + 12
        text_rect = QtCore.QRectF(text_left, rect.top() + 9,
                                  badge.left() - 8 - text_left, rect.height() - 18)
        name_font = QtGui.QFont(option.font)
        name_font.setPixelSize(13)
        name_font.setWeight(QtGui.QFont.DemiBold)
        small = QtGui.QFont(option.font)
        small.setPixelSize(11)
        line_h = text_rect.height() / 3
        lines = (
            (name_font, theme.color("text"), summary.get("name", "")),
            (small, theme.color("text_dim"), summary.get("size", "")),
            (small, theme.color("text_faint"), summary.get("stock", "")),
        )
        for i, (f, color, text) in enumerate(lines):
            painter.setFont(f)
            painter.setPen(color)
            line = QtCore.QRectF(text_rect.left(), text_rect.top() + i * line_h, text_rect.width(), line_h)
            elided = QtGui.QFontMetrics(f).elidedText(text, QtCore.Qt.ElideRight, int(line.width()))
            painter.drawText(line, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, elided)
        painter.restore()


class LayoutCardDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the Layout Results pane's rows as cards, the way LaserNest
    lists its layouts: the sheet's thumbnail on the left and its figures
    stacked beside it -- title, size, parts and use, material -- instead
    of one paragraph of text wrapping around the icon.

    Reads the rows NestingPanel._refresh_layout_list() already writes
    ("Sheet i of n" / "W × H mm · N parts · U%" / material), so the panel
    and the FreeCAD workbench's plain list are unchanged. A row with no
    sheet index (the "No layouts yet" placeholder) is drawn as a hint."""

    _HEIGHT = 96
    _THUMB = 80

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), self._HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(option.rect).adjusted(2, 3, -2, -3)
        text = index.data(QtCore.Qt.DisplayRole) or ""
        if index.data(QtCore.Qt.UserRole) is None:
            painter.setPen(theme.color("text_faint"))
            painter.drawText(rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, text)
            painter.restore()
            return

        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        painter.setPen(QtGui.QPen(theme.color("accent" if selected else "border"), 1.2 if selected else 1))
        painter.setBrush(theme.color("selection" if selected else ("hover" if hovered else "surface")))
        painter.drawRoundedRect(rect, 8, 8)

        thumb = QtCore.QRect(int(rect.left()) + 8, int(rect.center().y() - self._THUMB / 2),
                             self._THUMB, self._THUMB)
        icon = index.data(QtCore.Qt.DecorationRole)
        if isinstance(icon, QtGui.QIcon) and not icon.isNull():
            icon.paint(painter, thumb)

        lines = text.split("\n")
        title = lines[0] if lines else ""
        figures = lines[1].split(" \u00b7 ") if len(lines) > 1 else []
        size = figures[0] if figures else ""
        detail = " \u00b7 ".join(figures[1:])
        material = lines[2] if len(lines) > 2 else ""

        left = thumb.right() + 12
        width = rect.right() - 10 - left
        title_font = QtGui.QFont(option.font)
        title_font.setPixelSize(13)
        title_font.setWeight(QtGui.QFont.DemiBold)
        small = QtGui.QFont(option.font)
        small.setPixelSize(11)
        rows = [(title_font, theme.color("text"), title),
                (small, theme.color("text_dim"), size),
                (small, theme.color("text_dim"), detail),
                (small, theme.color("text_faint"), material)]
        rows = [r for r in rows if r[2]]
        line_h = 17
        top = rect.center().y() - line_h * len(rows) / 2
        for i, (font, color, value) in enumerate(rows):
            painter.setFont(font)
            painter.setPen(color)
            line = QtCore.QRectF(left, top + i * line_h, width, line_h)
            painter.drawText(line, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                             QtGui.QFontMetrics(font).elidedText(value, QtCore.Qt.ElideRight, int(width)))
        painter.restore()


class PartsPanel(QtCore.QObject):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self._summaries = []
        self.list_page = self._build_list_page()
        self.table_page = self._build_table_page()
        self.detail_page = self._build_detail_page()

        self.panel.parts_changed.connect(self.refresh)
        self.panel.preflight_ready.connect(self._on_preflight_ready)
        self.panel.busy_changed.connect(lambda busy: None if busy else self._refresh_badges())
        # Editing a quantity in the table changes what "required" means.
        self.panel.table.itemChanged.connect(lambda *_: self._refresh_badges())
        self.panel.assembly_quantity.valueChanged.connect(lambda *_: self._refresh_badges())
        self.panel.table.currentCellChanged.connect(
            lambda row, *_: self.list.setCurrentRow(row) if row >= 0 else None)
        self.refresh()
        self._on_preflight_ready(self.panel.preflight_pass)

    # --------------------------------------------------------------- build

    def _build_list_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("DockPage")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("Filter parts by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("PartsList")
        self.list.setItemDelegate(_PartRowDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self.list, 1)

        # The Assembly quantity multiplies every part's per-assembly count,
        # so it sits with the list totals it changes. It's the same widget
        # the panel builds in its Layout-basics box, adopted here.
        self.list_footer = QtWidgets.QLabel("")
        self.list_footer.setObjectName("FieldHint")
        layout.addWidget(self.list_footer)
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(6)
        assembly_label = QtWidgets.QLabel("Assemblies")
        assembly_label.setObjectName("FieldHint")
        footer.addWidget(assembly_label)
        self.panel.assembly_quantity.setFixedWidth(76)
        footer.addWidget(self.panel.assembly_quantity)
        footer.addStretch(1)
        self.panel.assembly_quantity.show()  # hidden in the settings dialog's Layout box
        layout.addLayout(footer)
        page.setMinimumWidth(270)
        return page

    def _build_table_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("DockPage")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        # A STEP/IGES assembly can produce a "Not ready" list hundreds of
        # lines long -- a bare QLabel would grow past the dock with nothing
        # to scroll, so the verdict sits in a capped scroll area.
        self.preflight_status = QtWidgets.QLabel("")
        self.preflight_status.setWordWrap(True)
        self.preflight_status.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        preflight_scroll = QtWidgets.QScrollArea()
        preflight_scroll.setObjectName("PreflightScroll")
        preflight_scroll.setWidgetResizable(True)
        preflight_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        preflight_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        preflight_scroll.setWidget(self.preflight_status)
        self._preflight_scroll = preflight_scroll
        layout.addWidget(preflight_scroll)
        self.panel.table.setMinimumHeight(80)
        layout.addWidget(self.panel.table, 1)
        return page

    def _build_detail_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("DockPage")
        body = QtWidgets.QHBoxLayout(page)
        body.setContentsMargins(8, 6, 8, 6)
        body.setSpacing(16)
        # Side by side, not stacked: a dock is wide and short, so the
        # render takes the left and the figures read against it.
        self.canvas = PartCanvas()
        self.canvas.setMinimumSize(160, 120)
        body.addWidget(self.canvas, 3)

        column = QtWidgets.QVBoxLayout()
        column.setSpacing(4)
        self.title = QtWidgets.QLabel("No part selected")
        self.title.setObjectName("DetailTitle")
        self.subtitle = QtWidgets.QLabel("")
        self.subtitle.setObjectName("FieldHint")
        column.addWidget(self.title)
        column.addWidget(self.subtitle)
        # Two columns of figures: eleven rows stacked would need more
        # height than a bottom dock usually has.
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        self._detail_labels = {}
        fields = (
            ("method", "Method"), ("material", "Material"), ("thickness", "Thickness"),
            ("quantity", "Qty / assembly"), ("rotations", "Rotations (deg)"),
            ("grain", "Grain-restricted"), ("mirror", "Mirror"), ("holes", "Holes"),
            ("net_area", "Net area"), ("bbox", "Bounding box"), ("cut_length", "Cut length"),
        )
        per_col = (len(fields) + 1) // 2
        for i, (key, label) in enumerate(fields):
            lab = QtWidgets.QLabel("--")
            lab.setObjectName("DetailValue")
            self._detail_labels[key] = lab
            caption = QtWidgets.QLabel(label)
            caption.setObjectName("DetailLabel")
            row, col = i % per_col, (i // per_col) * 2
            grid.addWidget(caption, row, col)
            grid.addWidget(lab, row, col + 1)
        column.addLayout(grid)
        column.addStretch(1)
        body.addLayout(column, 4)
        return page

    def on_theme_changed(self):
        """Thumbnails are rendered pixmaps, so they keep the colors of
        whichever theme drew them -- a light/dark flip redraws them.
        Called by MainWindow._set_theme()."""
        self.refresh()
        self._show_preflight_result(self.panel.preflight_pass, self.panel.preflight_issues)
        self.canvas.update()

    # ----------------------------------------------------- preflight display

    def _run_preflight(self):
        ok, issues = self.panel._run_preflight_check()
        self._show_preflight_result(ok, issues)

    def _on_preflight_ready(self, ok):
        self._show_preflight_result(ok, self.panel.preflight_issues)

    def _show_preflight_result(self, ok, issues):
        # The verdict is written the way a person would say it, not the way
        # a linter prints it.
        if ok:
            self.preflight_status.setStyleSheet(
                f"color: {theme.tokens()['success']}; font-weight: 600;")
            self.preflight_status.setText("All parts are ready to nest.")
        else:
            self.preflight_status.setStyleSheet(f"color: {theme.tokens()['danger']};")
            lines = "\n".join(f"\u2022 {i}" for i in issues)
            self.preflight_status.setText("A few things need attention before nesting:" + (f"\n{lines}" if lines else ""))
        self._fit_preflight_height()

    def _fit_preflight_height(self):
        """The verdict box grows to what it has to say, up to a cap -- a
        one-line "all good" shouldn't reserve six lines of blank."""
        needed = self.preflight_status.sizeHint().height() + 4
        self._preflight_scroll.setFixedHeight(max(20, min(110, needed)))

    # ------------------------------------------------------------- display

    def refresh(self):
        self._summaries = self.panel.part_summaries()
        current = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for s in self._summaries:
            bw, bh = s["bbox"]
            mat = s["material"] or "unspecified"
            thickness = f"{s['thickness']:g} mm" if s["thickness"] else "thickness not set"
            item = QtWidgets.QListWidgetItem(untinted_icon(_render_icon(s, _ROW_ICON)), s["name"])
            item.setData(_SUMMARY_ROLE, {
                "name": s["name"],
                "size": f"{bw:,.1f} \u00d7 {bh:,.1f} mm",
                "stock": f"{mat} \u00b7 {thickness}",
            })
            item.setToolTip(f"{s['name']}  \u00b7  {mat}, {thickness}")
            item.setSizeHint(QtCore.QSize(0, _ROW_HEIGHT))
            self.list.addItem(item)
        self.list.blockSignals(False)
        # A quantity is a spin box in the table, not an item, so its edits
        # never reach itemChanged -- listen to each one directly (the rows
        # are rebuilt on every parts_changed, which is what calls this).
        for row in range(self.panel.table.rowCount()):
            qty = self.panel.table.cellWidget(row, 2)
            if qty is not None and not qty.property("_badge_hooked"):
                qty.setProperty("_badge_hooked", True)
                qty.valueChanged.connect(lambda *_: self._refresh_badges())
        self._refresh_badges()
        self._apply_filter(self.filter.text())
        if self._summaries:
            row = current if 0 <= current < len(self._summaries) else 0
            self.list.setCurrentRow(row)
            self._show(row)
        else:
            self.title.setText("No part selected")
            self.subtitle.setText("")
            self.canvas.set_part(None)
            for lab in self._detail_labels.values():
                lab.setText("--")

    def _refresh_badges(self):
        """Each row's badge: "×N" (copies required) before a run, then
        "placed/required" colored by how much of it the run placed."""
        panel = self.panel
        assemblies = panel.assembly_quantity.value()
        has_run = bool(panel.sheets)
        unplaced = {}
        for name in panel.unplaced if has_run else ():
            unplaced[name] = unplaced.get(name, 0) + 1
        # Straight off the table, not part_summaries(): this runs on every
        # table edit, and the summaries compute every part's geometry.
        table = panel.table
        rows = []
        for row in range(table.rowCount()):
            label = table.item(row, 0)
            qty = table.cellWidget(row, 2)
            if label is not None:
                rows.append((label.text(), qty.value() if qty is not None else 1))
        total_required = total_placed = 0
        for i in range(min(self.list.count(), len(rows))):
            name, per_assembly = rows[i]
            required = per_assembly * assemblies
            total_required += required
            if not has_run:
                badge = (f"\u00d7{required}", "none")
            else:
                placed = max(0, required - unplaced.get(name, 0))
                total_placed += placed
                state = "full" if placed >= required else ("some" if placed else "zero")
                badge = (f"{placed}/{required}", state)
            self.list.item(i).setData(_BADGE_ROLE, badge)
        n = len(rows)
        if not n:
            self.list_footer.setText("No parts yet -- Add Parts (Ctrl+I).")
        elif has_run:
            self.list_footer.setText(f"{n} parts \u00b7 {total_placed} of {total_required} placed")
        else:
            self.list_footer.setText(f"{n} parts \u00b7 {total_required} to nest")
        self.list.viewport().update()

    def _apply_filter(self, text):
        needle = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_row_changed(self, row):
        if 0 <= row < len(self._summaries):
            self._show(row)
            # Keep the Part Table on the same part, so an edit lands where
            # the user is looking.
            if self.panel.table.currentRow() != row:
                self.panel.table.selectRow(row)

    def _show(self, row):
        s = self._summaries[row]
        self.title.setText(s["name"])
        thickness = f"{s['thickness']:g} mm" if s["thickness"] else "thickness not set"
        self.subtitle.setText(
            f"{s['material'] or 'unspecified'} \u00b7 {thickness} \u00b7 \u00d7{s['quantity_per_assembly']} per assembly"
        )
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
        self._detail_labels["net_area"].setText(num(s["net_area"], "mm\u00b2", 1))
        bw, bh = s["bbox"]
        self._detail_labels["bbox"].setText(f"{bw:,.1f} \u00d7 {bh:,.1f} mm")
        self._detail_labels["cut_length"].setText(num(s["cut_length"], "mm", 1))
