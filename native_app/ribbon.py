"""
ribbon.py
---------
The native app's ribbon, laid out the way CAM nesting suites (Lantek,
LaserNest, RADAN) lay theirs out: a row of ribbon TABS (Parts / Stock /
Nesting / Output / View), and under it one page per tab made of captioned GROUPS.
Each group holds large icon-over-label buttons for its main actions and
stacks of up to three small icon-beside-label rows (buttons, checkboxes,
compact fields) for the secondary ones, with the group's name underneath
and an optional corner launcher that opens the full settings for it.

    ribbon = Ribbon()
    page = ribbon.add_tab("Home")
    nest = page.add_group("Nest")
    run = nest.add_large("run-nesting", "▶", "Run\\nNesting", primary=True)
    stack = nest.add_small("stop-nesting", "■", "Stop")

The ribbon only lays controls out and paints them; main_window.py wires
every button to a real, already-working `NestingPanel`/`FilePartSource`
method -- no disabled placeholders for features that don't exist yet.

Icons come from the user-supplied `alphanest-icon-pack/` when available,
recolored into the current theme as they load (see theme.py's
`icon_svg()`). A new action's icon needs no code change here: drop
`icon-<name>.svg` into both `alphanest-icon-pack/icons/dark/` and
`.../light/` (96x96 viewBox) and pass that `<name>`. A button with no pack
icon falls back to a glyph this app draws itself (`glyphs.py`), and
failing that to its own character drawn in the theme's ink.
"""

import re

from PySide6 import QtCore, QtGui, QtWidgets

from native_app import glyphs, theme

LARGE_ICON_PX = 32
SMALL_ICON_PX = 16
# Pixel height of a group's content area: three small rows, or one large
# button, whichever the group has -- every group shares it so captions
# line up across the whole page.
_CONTENT_HEIGHT = 78
_ICON_CACHE = {}
# The pack draws its artwork inside roughly the middle 64 units of a 96-unit
# tile, leaving a margin for the tile's rounded corners. The tile is gone
# here (theme.icon_svg), so rendering the full 96 box would leave every
# pack icon a third smaller than the app's own glyphs beside it -- crop to
# the artwork instead.
_PACK_ARTWORK = QtCore.QRectF(14, 14, 68, 68)
_STROKE_RE = re.compile(r'stroke-width="([0-9.]+)"')


def _render_svg(svg, px):
    """Rasterize themed SVG source (see theme.icon_svg) at 2x, marked as
    such, so Qt downscales instead of upscaling and edges stay crisp."""
    from PySide6 import QtSvg

    if px <= SMALL_ICON_PX:
        # At 16px the pack's ~3.3-unit strokes land under a pixel wide and
        # go grey; thicken them so small icons carry the same weight as
        # the text beside them.
        svg = _STROKE_RE.sub(lambda m: f'stroke-width="{float(m.group(1)) * 1.5:.2f}"', svg)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    renderer.setViewBox(_PACK_ARTWORK)
    ratio = 2
    pixmap = QtGui.QPixmap(px * ratio, px * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    # Explicit bounds: render(painter) alone lays the SVG out at its own
    # 96px default size and clips everything past the canvas.
    renderer.render(painter, QtCore.QRectF(0, 0, px, px))
    painter.end()
    return glyphs.untinted(pixmap)


def _char_icon(char, color, px):
    """Last resort: the button's own character, drawn in the theme's ink."""
    ratio = 2
    pixmap = QtGui.QPixmap(px * ratio, px * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    painter.setPen(color)
    font = painter.font()
    font.setPixelSize(int(px * ratio * 0.78))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, char)
    painter.end()
    return glyphs.untinted(pixmap)


def button_icon(icon_name, char, dark, px, mono=None):
    """Icon for a ribbon button, in order of preference: the pack's own
    SVG recolored to this theme, a glyph this app draws itself, or the
    button's character. `mono` forces the whole icon into one color --
    what the primary action uses so its glyph is white on the accent."""
    key = (icon_name, char, dark, px, mono)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    palette = theme.tokens(dark)
    ink = QtGui.QColor(mono or palette["text"])
    svg = theme.icon_svg(icon_name, dark, mono=mono)
    if svg is not None:
        icon = _render_svg(svg, px)
    elif icon_name in glyphs.GLYPHS:
        accent = QtGui.QColor(mono or palette["accent"])
        icon = glyphs.glyph_icon(icon_name, ink, QtGui.QColor(palette["raised"]), size=px, accent=accent)
    else:
        icon = _char_icon(char, ink, px)
    _ICON_CACHE[key] = icon
    return icon


class RibbonButton(QtWidgets.QToolButton):
    """One ribbon action, in one of two sizes:

    * large -- a 32px icon over a (one- or two-line) label, for the
      actions a group exists for;
    * small -- a 16px icon beside a single-line label, stacked up to three
      high, for the secondary ones.

    `primary=True` marks the ONE action that matters most (Run Nesting):
    filled in the accent so it's never mistaken for its neighbours. Looks
    live in theme.py's app-wide sheet, keyed off the object names set
    here, so a theme swap restyles every button at once."""

    def __init__(self, icon_name, glyph, text, parent=None, large=False, primary=False):
        super().__init__(parent)
        self._icon_name = icon_name
        self._glyph = glyph
        self._primary = primary
        self._large = large
        if primary:
            self.setObjectName("PrimaryRibbonAction")
        else:
            self.setObjectName("RibbonLarge" if large else "RibbonSmall")
        self.setText(text)
        self.setAutoRaise(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        px = LARGE_ICON_PX if large else SMALL_ICON_PX
        self.setIconSize(QtCore.QSize(px, px))
        if large:
            self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            self.setFixedHeight(_CONTENT_HEIGHT)
            self.setMinimumWidth(56)
        else:
            self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            self.setFixedHeight(_CONTENT_HEIGHT // 3)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.set_theme(False)

    def set_theme(self, dark):
        mono = theme.tokens(dark)["on_accent"] if self._primary else None
        px = LARGE_ICON_PX if self._large else SMALL_ICON_PX
        self.setIcon(button_icon(self._icon_name, self._glyph, dark, px, mono=mono))


class RibbonGroup(QtWidgets.QFrame):
    """A captioned cluster of controls on a ribbon page. Controls flow left
    to right: each large button is its own column, and consecutive small
    items fill a column three rows deep before starting the next one.

    A group whose every control has been hidden (View > Ribbon Items)
    hides itself too -- see `refresh_visibility()` -- so a page never shows
    an empty caption over nothing."""

    launcher_clicked = QtCore.Signal()

    def __init__(self, title, parent=None, launcher=False):
        super().__init__(parent)
        self.setObjectName("RibbonGroup")
        self.title = title
        self._items = []
        self._column = None   # the small-item stack currently being filled
        self._column_rows = 0  # rows of it already used (a block can take more than one)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 0, 6, 0)
        outer.setSpacing(2)

        content = QtWidgets.QWidget(self)
        content.setObjectName("RibbonGroupContent")
        content.setFixedHeight(_CONTENT_HEIGHT)
        self._row = QtWidgets.QHBoxLayout(content)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(2)
        outer.addWidget(content)

        caption_row = QtWidgets.QHBoxLayout()
        caption_row.setContentsMargins(0, 0, 0, 0)
        caption_row.setSpacing(0)
        caption = QtWidgets.QLabel(title, self)
        caption.setObjectName("RibbonGroupCaption")
        caption.setAlignment(QtCore.Qt.AlignCenter)
        caption_row.addWidget(caption, 1)
        self.launcher = None
        if launcher:
            # The corner arrow Office-style ribbons use for "the rest of
            # this group's options" -- here, the matching section of the
            # Nesting Settings dialog.
            self.launcher = QtWidgets.QToolButton(self)
            self.launcher.setObjectName("RibbonLauncher")
            self.launcher.setText("↘")
            self.launcher.setAutoRaise(True)
            self.launcher.setCursor(QtCore.Qt.PointingHandCursor)
            self.launcher.setToolTip(f"All {title.lower()} settings...")
            self.launcher.clicked.connect(self.launcher_clicked)
            caption_row.addWidget(self.launcher, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        outer.addLayout(caption_row)

    @property
    def items(self):
        return list(self._items)

    def add_large(self, icon_name, glyph, text, primary=False, checkable=False):
        btn = RibbonButton(icon_name, glyph, text, self, large=True, primary=primary)
        btn.setCheckable(checkable)
        self._column = None
        self._row.addWidget(btn, 0, QtCore.Qt.AlignTop)
        self._items.append(btn)
        return btn

    def new_column(self):
        """Start the next small item in a fresh stack, even if the current
        one has room -- for keeping related items together."""
        self._column = None

    def add_small(self, icon_name, glyph, text, checkable=False):
        btn = RibbonButton(icon_name, glyph, text, self, large=False)
        btn.setCheckable(checkable)
        self._add_to_stack(btn)
        return btn

    def add_widget(self, widget, label=None, rows=1):
        """Adopt an existing widget (a checkbox, a spin box, a combo) as one
        small row -- with a caption beside it when `label` is given. The
        row, not the bare widget, is what View > Ribbon Items hides, so the
        caption goes with it; the row is returned for exactly that.
        `rows` > 1 gives a taller block (e.g. a 2x2 grid of fields) that
        many rows of the stack."""
        if label is None:
            row = widget
        else:
            row = QtWidgets.QWidget(self)
            row.setObjectName("RibbonFieldRow")
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(4, 0, 0, 0)
            lay.setSpacing(6)
            caption = QtWidgets.QLabel(label, row)
            caption.setObjectName("RibbonFieldLabel")
            lay.addWidget(caption)
            lay.addWidget(widget)
        widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        row.setFixedHeight(rows * _CONTENT_HEIGHT // 3)
        self._add_to_stack(row, rows)
        widget.show()
        return row

    def _add_to_stack(self, widget, rows=1):
        if self._column is None or self._column_rows + rows > 3:
            self._column_rows = 0
            self._column = QtWidgets.QVBoxLayout()
            self._column.setContentsMargins(0, 0, 0, 0)
            self._column.setSpacing(0)
            self._column.setAlignment(QtCore.Qt.AlignTop)
            self._row.addLayout(self._column)
        self._column.addWidget(widget, 0, QtCore.Qt.AlignLeft)
        self._column_rows += rows
        self._items.append(widget)

    def refresh_visibility(self):
        # isHidden(), not isVisible(): a group on a ribbon page that isn't
        # the current one is invisible without anyone having hidden it.
        self.setVisible(any(not w.isHidden() for w in self._items))


class RibbonPage(QtWidgets.QWidget):
    """One ribbon tab's content: groups left to right, divided by hairlines."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonPage")
        self.title = title
        self.groups = []
        self._lay = QtWidgets.QHBoxLayout(self)
        self._lay.setContentsMargins(8, 6, 8, 4)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)
        self._separators = []

    def add_group(self, title, launcher=False):
        index = self._lay.count() - 1   # before the trailing stretch
        if self.groups:
            sep = QtWidgets.QFrame(self)
            sep.setObjectName("RibbonSeparator")
            sep.setFrameShape(QtWidgets.QFrame.VLine)
            self._lay.insertWidget(index, sep)
            self._separators.append(sep)
            index += 1
        group = RibbonGroup(title, self, launcher=launcher)
        self._lay.insertWidget(index, group)
        self.groups.append(group)
        return group

    def refresh_visibility(self):
        for group in self.groups:
            group.refresh_visibility()
        # A rule belongs to the group after it; with no visible group on
        # BOTH sides, it would just divide nothing.
        visible = [not g.isHidden() for g in self.groups]
        for i, sep in enumerate(self._separators, start=1):
            sep.setVisible(visible[i] and any(visible[:i]))


class _PageScroll(QtWidgets.QScrollArea):
    """Holds one ribbon page. A page wider than the window scrolls sideways
    instead of forcing the whole window to its width, and the scroll area
    grows by the scrollbar's height only while one is actually showing, so
    the group captions are never clipped by it."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonPageScroll")
        self.setWidget(page)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self._fit_height()

    def _fit_height(self):
        page = self.widget()
        needs_bar = page.minimumSizeHint().width() > self.viewport().width() + 1
        bar = self.horizontalScrollBar().sizeHint().height() if needs_bar else 0
        self.setFixedHeight(page.sizeHint().height() + bar)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class Ribbon(QtWidgets.QWidget):
    """Tab row + stacked pages. `current_changed(index)` fires when the user
    switches ribbon tab (persisted by persistence.py)."""

    current_changed = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Ribbon")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tab_row = QtWidgets.QWidget(self)
        tab_row.setObjectName("RibbonTabRow")
        tab_row.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._tab_lay = QtWidgets.QHBoxLayout(tab_row)
        self._tab_lay.setContentsMargins(10, 4, 10, 0)
        self._tab_lay.setSpacing(2)
        self._tab_lay.addStretch(1)
        outer.addWidget(tab_row)

        self._stack = QtWidgets.QStackedWidget(self)
        self._stack.setObjectName("RibbonBody")
        outer.addWidget(self._stack)

        self._tab_group = QtWidgets.QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self._tab_group.idClicked.connect(self.set_current_index)
        self.pages = []

    def add_tab(self, title):
        page = RibbonPage(title)
        tab = QtWidgets.QToolButton()
        tab.setObjectName("RibbonTab")
        tab.setText(title)
        tab.setCheckable(True)
        tab.setCursor(QtCore.Qt.PointingHandCursor)
        self._tab_group.addButton(tab, len(self.pages))
        self._tab_lay.insertWidget(len(self.pages), tab)
        self._stack.addWidget(_PageScroll(page))
        self.pages.append(page)
        if len(self.pages) == 1:
            tab.setChecked(True)
        return page

    def add_corner_widget(self, widget):
        """A widget at the far right of the tab row (after the stretch)."""
        self._tab_lay.addWidget(widget)

    def current_index(self):
        return self._stack.currentIndex()

    def set_current_index(self, index):
        if not 0 <= index < len(self.pages):
            return
        self._tab_group.button(index).setChecked(True)
        if index != self._stack.currentIndex():
            self._stack.setCurrentIndex(index)
            self.current_changed.emit(index)

    def page_of(self, widget):
        """The index of the page `widget` sits on, or -1."""
        for i, page in enumerate(self.pages):
            if page.isAncestorOf(widget):
                return i
        return -1

    def refresh_visibility(self):
        for page in self.pages:
            page.refresh_visibility()
        for scroll in self.findChildren(_PageScroll):
            scroll._fit_height()

    def set_theme(self, dark):
        for btn in self.findChildren(RibbonButton):
            btn.set_theme(dark)
