"""
ribbon.py
---------
The native app's ribbon -- one compact stack, no sub-tabs, no scrollbars:

  Row 1  The action toolbar (icon + text per button, thin separators
         between logical clusters): Run / Stop | Export DXF / Report.
         Always visible, never scrolls.

  Below  EVERY settings group box the shared `NestingPanel` builds,
         flattened into narrow single-column boxes grouped under a short
         section caption (Layout / Rules / Cutting / Stock / Export). The
         sections flow left-to-right and wrap onto new rows as the window
         narrows, so the layout ALWAYS fits -- there is no horizontal
         scrollbar; the band simply grows taller instead. A tall row of
         settings costs vertical space, but that is the deal for "everything
         always fits" (and it scales with the window width automatically).

The 5 settings sub-tabs are gone; duplicates with the Parts/Stock tabs and
mirrors of the log were dropped (the Assembly quantity row lives on the
Parts tab now, the inventory-status line and the GA/cost/report operational
labels live elsewhere), and the Microjoints toggle lives on its checkbox in
the Cutting settings -- not as a toolbar button -- so the settings stack
stays compact.

The shared `NestingPanel` builds all of its settings group boxes in
`settings_boxes` (grouped by the old tab title) and the window reparents
them in here, so the settings live in the ribbon for the native app but stay
in the panel's own scroll area in the FreeCAD workbench.

Icons come from the user-supplied `alphanest-icon-pack/` when available
(see theme.py's `icon_path()`), matched by name to each button, with a
light/dark variant swapped on `set_theme()`. Adding a new toolbar action's
icon later needs no code change here: drop `icon-<name>.svg` into both
`alphanest-icon-pack/icons/dark/` and `.../light/` (96x96 viewBox, a
rounded-rect background matching the theme, and an accent-red `#E8352E`
glyph on top -- see any existing pack icon for the exact pattern), then
pass that `<name>` to `add_button()`. A button whose action has no pack
icon falls back to a small `QPainter`-drawn gradient-square glyph instead
-- no external asset needed, and it stays visually consistent with the
surrounding pack icons' rounded-square shape.

Every button this app actually adds calls a real, already-working
`NestingPanel`/`FilePartSource` method directly (see main_window.py) --
no disabled placeholders for features that don't exist yet.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from native_app import theme


_ICON_TOP = QtGui.QColor("#5c93c9")
_ICON_BOTTOM = QtGui.QColor("#1f4d80")

# Glyph fallbacks are painted 96x96 and downscaled by QIcon to the button's
# real icon size. Downscaling stays crisp; the old 32px render was accurate
# at 32 but got UPSCALED to the 44px button icon, which is what made any
# fallback icon look soft/wonky.
_GLYPH_RENDER = 96

# Pack icons are re-rasterized 2x the button's real icon size with Qt's own
# renderer and stored with devicePixelRatio 2, so Qt downscales them instead
# of the SVG loading raster scaled up -- the edges stay crisp instead of
# going soft/mushy (the "wonky" look). Cached per file so repeated
# light/dark theme swaps don't re-rasterize.
_PACK_RENDER = 80
_PACK_ICON_SIZE = 40
_ICON_CACHE = {}


def _glyph_icon(glyph, size=_GLYPH_RENDER):
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

    grad = QtGui.QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, _ICON_TOP)
    grad.setColorAt(1, _ICON_BOTTOM)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(0, 0, size, size, round(size * 0.19), round(size * 0.19))

    painter.setPen(QtGui.QColor("white"))
    font = painter.font()
    font.setPixelSize(int(size * 0.52))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pix.rect(), QtCore.Qt.AlignCenter, glyph)
    painter.end()
    return QtGui.QIcon(pix)


def _button_icon(icon_name, glyph, dark):
    """Icon for a ribbon button: the pack SVG for the named action, or the
    hand-drawn glyph fallback if the pack has no such icon (see the module
    docstring)."""
    path = theme.icon_path(icon_name, dark)
    if not path:
        return _glyph_icon(glyph)
    cached = _ICON_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        from PySide6 import QtSvg
        renderer = QtSvg.QSvgRenderer(path)
        pm = QtGui.QPixmap(_PACK_RENDER, _PACK_RENDER)
        pm.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pm)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        renderer.render(painter)
        painter.end()
        pm.setDevicePixelRatio(_PACK_RENDER / float(_PACK_ICON_SIZE))
        ico = QtGui.QIcon(pm)
    except Exception:
        ico = QtGui.QIcon(path)
    _ICON_CACHE[path] = ico
    return ico


class RibbonButton(QtWidgets.QToolButton):
    """A slim toolbar action: small icon beside a single-line label. Sized
    to its own text so buttons don't waste width on blanket fixed sizes --
    they sit in one tidy left-aligned toolbar row (~470 px total), leaving
    the rest of the width to the settings sections."""
    def __init__(self, icon_name, glyph, text, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._glyph = glyph
        self.setText(text)
        self.setIconSize(QtCore.QSize(22, 22))
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setAutoRaise(True)
        self.setStyleSheet("QToolButton { font-size: 11px; padding: 4px 8px; font-weight: 600; }")
        text_w = max(self.fontMetrics().horizontalAdvance(line) for line in text.splitlines())
        self.setFixedSize(max(88, text_w + 22 + 4 + 22), 40)
        self.set_theme(False)

    def set_theme(self, dark):
        self.setIcon(_button_icon(self._icon_name, self._glyph, dark))


class _FlowLayout(QtWidgets.QLayout):
    """Left-to-right, wrap-to-next-row layout (Qt's stock flow layout):
    children keep their natural size and line up in rows that wrap when the
    available width runs out. This is what lets the settings sections always
    fit -- they simply flow onto more rows instead of scrolling. The height
    for a given width is reported through heightForWidth(), so the ribbon
    auto-grows to exactly what the current window width needs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hgap = 6
        self._vgap = 4
        self._items = []
        self.setContentsMargins(2, 2, 2, 2)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(width, None)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect.width(), rect)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        m = self.contentsMargins()
        w = max((it.minimumSize().width() for it in self._items), default=0)
        h = max((it.minimumSize().height() for it in self._items), default=0)
        return QtCore.QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def _arrange(self, width, rect):
        m = self.contentsMargins()
        eff = width - m.left() - m.right()
        x = m.left()
        y = m.top()
        row_h = 0
        for it in self._items:
            sh = it.sizeHint()
            if x + sh.width() > m.left() + eff + 1 and x > m.left():
                x = m.left()
                y += row_h + self._vgap
                row_h = 0
            if rect is not None:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), sh))
            x += sh.width() + self._hgap
            row_h = max(row_h, sh.height())
        return y + row_h + m.bottom()


class Ribbon(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Ribbon")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Toolbar row: slim action buttons, left-aligned, one flat line.
        toolbar = QtWidgets.QWidget(self)
        self._actions_lay = QtWidgets.QHBoxLayout(toolbar)
        self._actions_lay.setContentsMargins(8, 3, 8, 3)
        self._actions_lay.setSpacing(5)
        outer.addWidget(toolbar)

        rule = QtWidgets.QFrame(self)
        rule.setFrameShape(QtWidgets.QFrame.HLine)
        rule.setStyleSheet("color: #3d424a;")
        outer.addWidget(rule)

        # Settings flow: every section wraps to the next row when it needs
        # to, so the whole width always fits with no horizontal scrollbar.
        # Height is derived from the current width (see _FlowLayout), so the
        # band auto-grows instead of pinning the content to a maximum.
        self._flow = _FlowLayout()
        content = QtWidgets.QWidget(self)
        self._flow_container = content
        content.setLayout(self._flow)
        # Compact the settings forms that live in the ribbon (forms are for
        # the FreeCAD panel too, but only the ribbon needs them cramped):
        # smaller labels/checkboxes, tight group-box padding, and shorter
        # number-entry boxes so the sections stay narrow.
        content.setStyleSheet(
            "QLabel, QCheckBox { font-size: 10px; }"
            " QGroupBox { font-size: 10px; padding-top: 4px; }"
            " QSpinBox, QDoubleSpinBox { min-height: 18px; max-height: 22px; }"
        )
        outer.addWidget(content)

        # add_button()/add_separator()/add_stretch() land here while the
        # window builds the action toolbar; add_settings_tab() appends
        # directly to the flow.
        self._current_layout = self._actions_lay

    def add_button(self, icon_name, glyph, text, checkable=False):
        btn = RibbonButton(icon_name, glyph, text, self)
        btn.setCheckable(checkable)
        self._current_layout.addWidget(btn)
        return btn

    def add_separator(self):
        sep = QtWidgets.QFrame(self)
        sep.setObjectName("RibbonSeparator")
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setFixedHeight(28)
        self._current_layout.addWidget(sep)

    def add_stretch(self):
        self._current_layout.addStretch(1)

    @staticmethod
    def _compact_settings_box(box):
        """Cram a settings group box so its section stays narrow: tight
        group-box margins, thin form/row spacing, and each number box sized
        by its OWN range's widest text instead of one blanket width -- a
        margin spin that maxes at "1000.0 mm" is barely wider than the
        spinner, while a min-area spin that can reach "1000000000 mm²" still
        gets enough room. No box is comically wide just because another one
        might be."""
        lay = box.layout()
        if lay is not None:
            lay.setContentsMargins(6, 4, 6, 4)
        for form in box.findChildren(QtWidgets.QFormLayout):
            form.setVerticalSpacing(0)
            form.setHorizontalSpacing(4)
        for vbox in box.findChildren(QtWidgets.QVBoxLayout):
            vbox.setSpacing(2)
        for spin in box.findChildren(QtWidgets.QAbstractSpinBox):
            suffix = getattr(spin, "suffix", lambda: "")()
            if isinstance(spin, QtWidgets.QDoubleSpinBox):
                text = f"{spin.maximum():.{spin.decimals()}f}{suffix}"
            else:
                text = f"{spin.maximum()}{suffix}"
            width = spin.fontMetrics().horizontalAdvance(text) + 30
            spin.setFixedSize(min(max(width, 50), 110), 22)

    @staticmethod
    def _flatten_box(box, vertical=True):
        """Rebuild one settings group box as a *new* compact box whose
        layout is a narrow grid: every label/field pair gets its own row,
        `label | field` (the top-to-bottom column that makes each box a
        slim ~130-260px tile instead of a wide paired strip). Full-line rows
        (the 2x2 margin grid, the report folder row, the field-only status
        lines) span the whole width, and a row with a label keeps it in the
        left label column.

        Widgets are the same objects, only reparented -- every
        `self.<attr>` reference the panel holds keeps working. Hidden rows
        (sheet width/height, K-factor/curve tolerance, and the GA/cost/report
        mirrors the native ribbon hides) are appended hidden at the end. The
        old box is abandoned (Qt refuses to install a second layout on a
        widget), so add_settings_tab() splices the new box back into
        NestingPanel.settings_boxes."""
        title = box.title()
        old = box.layout()
        rows = []

        def drain_form(form):
            while form.rowCount() > 0:
                row = form.takeRow(0)
                label = row.labelItem.widget() if row.labelItem is not None else None
                field = None
                if row.fieldItem is not None:
                    field = row.fieldItem.widget()
                    if field is None:
                        field = row.fieldItem.layout()
                yield label, field

        def drain(items):
            while items.count() > 0:
                item = items.takeAt(0)
                wid = item.widget()
                lay = item.layout()
                if isinstance(lay, QtWidgets.QFormLayout):
                    yield from drain_form(lay)
                elif wid is not None:
                    yield None, wid
                elif lay is not None:
                    yield None, lay

        if isinstance(old, QtWidgets.QFormLayout):
            rows = list(drain_form(old))
        else:
            rows = list(drain(old))

        kept = []
        hidden = []  # rows hidden for the ribbon, but widgets are still
                     # referenced by the panel (the GA/cost/report mirrors,
                     # sheet width/height, k-factor, tolerance) -- they must
                     # stay parented and alive, just invisible; QHide
                     # children in a layout take no space.
        for label, field in rows:
            if isinstance(field, QtWidgets.QWidget) and not field.isVisibleTo(box):
                hidden.append((label, field))
            elif label is not None and not label.isVisibleTo(box):
                hidden.append((label, field))
            else:
                kept.append((label, field))

        def is_wide(label, field):
            return isinstance(field, QtWidgets.QLayout) or (
                label is None and isinstance(field, QtWidgets.QLabel)
            )

        new_box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(new_box)
        grid.setContentsMargins(5, 3, 5, 4)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(1)

        def add_field(field, row, col, span=1):
            if isinstance(field, QtWidgets.QLayout):
                grid.addLayout(field, row, col, 1, span)
            else:
                grid.addWidget(field, row, col, 1, span)

        row = -1
        for label, field in kept:
            if is_wide(label, field):
                row += 1
                if label is not None:
                    grid.addWidget(label, row, 0)
                    grid.setAlignment(label, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    add_field(field, row, 1, span=3)
                else:
                    add_field(field, row, 0, span=4)
                continue
            row += 1
            if label is not None:
                grid.addWidget(label, row, 0)
                grid.setAlignment(label, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            add_field(field, row, 1)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)

        # Hidden rows: reparent (alive) but invisible -- appended at the
        # end, where they collapse to zero height because they're QHidden.
        for label, field in hidden:
            row += 1
            if label is not None:
                grid.addWidget(label, row, 0, 1, 2)
                label.hide()
            add_field(field, row, 0, span=2)
            if isinstance(field, QtWidgets.QWidget):
                field.hide()
        return new_box

    def add_settings_tab(self, title, boxes):
        """Fold one former sub-tab's settings group boxes into the flow:
        each box is compacted and flattened into a narrow single-column
        tile (see _compact_settings_box/_flatten_box), then grouped in one
        section -- a short caption above the boxes. The flow places the
        sections side by side and wraps them onto new rows when the window
        narrows, so they always fit without any horizontal scrolling."""
        rebuilt = []
        for box in boxes:
            self._compact_settings_box(box)
            rebuilt.append(self._flatten_box(box, vertical=True))
        boxes[:] = rebuilt

        section = QtWidgets.QWidget(self._flow_container)
        slay = QtWidgets.QVBoxLayout(section)
        slay.setContentsMargins(0, 0, 0, 0)
        slay.setSpacing(2)
        caption = QtWidgets.QLabel(title, section)
        caption.setObjectName("RibbonSectionCaption")
        caption.setStyleSheet("font-weight: 600; padding-left: 1px;")
        slay.addWidget(caption)
        boxes_lay = QtWidgets.QHBoxLayout()
        boxes_lay.setSpacing(4)
        for box in rebuilt:
            # Keep each box top-aligned in the section so a tall neighbor
            # doesn't stretch every short box into empty space.
            box.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            boxes_lay.addWidget(box, 1)
        slay.addLayout(boxes_lay)
        self._flow.addWidget(section)

    def set_theme(self, dark):
        for btn in self.findChildren(RibbonButton):
            btn.set_theme(dark)