"""
glyphs.py
---------
Small vector glyphs the app draws for itself, in the current theme's
colors: the three tab-strip marks (segmented.py) and the ribbon's marks for
every action the icon pack has no icon for (ribbon.py) -- Settings, Open,
Save, zoom, sheet paging and the rest.

They exist because the icon pack can't cover these two jobs. Its icons are
rounded TILES -- a filled rectangle with line work on top, sized for a
toolbar button -- so one inside a tab pill reads as a button within a
button, and it has no icon at all for Settings, which fell through to a
hand-drawn blue gradient square that matched nothing else on screen.

Everything here is a plain 2px-ish stroke on a transparent ground, drawn
in whatever color the caller passes, so a glyph tints with selection and
with the theme instead of carrying its own palette. `bg` is the surface
the glyph sits on, for the shapes that overlap themselves and need to
occlude what's behind. `accent` is the one highlight color a glyph may
use for its "this is the verb" detail (a plus, a check), the same way the
pack's icons carry one accent stroke; it defaults to `color`.
"""

from PySide6 import QtCore, QtGui

GLYPH_PX = 18           # drawn at 2x and marked as such, so it stays crisp
_STROKE = 1.6


def _pen(color, width=_STROKE):
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    return pen


def _draw_parts(painter, color, bg, accent=None):
    """An L-bracket: the silhouette of a sheet-metal part."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawPolygon(QtGui.QPolygonF([
        QtCore.QPointF(3.4, 3.2), QtCore.QPointF(7.2, 3.2), QtCore.QPointF(7.2, 11),
        QtCore.QPointF(14.6, 11), QtCore.QPointF(14.6, 14.8), QtCore.QPointF(3.4, 14.8),
    ]))


def _draw_stock(painter, color, bg, accent=None):
    """Two sheets, one behind the other -- stock on hand. The front one is
    filled with whatever it sits on, so the overlap reads as depth instead
    of as a grid of lines."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRoundedRect(QtCore.QRectF(6.4, 3.2, 8.4, 8.4), 1.6, 1.6)
    painter.setBrush(bg)
    painter.drawRoundedRect(QtCore.QRectF(3.2, 6.4, 8.4, 8.4), 1.6, 1.6)


def _draw_nesting(painter, color, bg, accent=None):
    """Two parts placed on a sheet -- the nest itself. Deliberately only
    two, with air between them: at 18px, a sheet packed with shapes fills
    in and reads as a solid block."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRoundedRect(QtCore.QRectF(2.6, 2.6, 12.8, 12.8), 2.6, 2.6)
    painter.setBrush(color)
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawRoundedRect(QtCore.QRectF(5, 5, 4.6, 3.1), 0.9, 0.9)
    painter.drawRoundedRect(QtCore.QRectF(8.8, 9.6, 4.2, 2.7), 0.9, 0.9)


def _draw_settings(painter, color, bg, accent=None):
    """Two sliders, not a cogwheel: this button opens shop and machine
    PARAMETERS, and a gear's teeth turn to mush below about 24px anyway.
    Each knob is filled with the button's own background so the track
    reads as passing behind it."""
    painter.setPen(_pen(color))
    for y, knob_x in ((6.4, 11.6), (11.6, 6.4)):
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawLine(QtCore.QPointF(2.8, y), QtCore.QPointF(15.2, y))
        painter.setBrush(bg)
        painter.drawEllipse(QtCore.QPointF(knob_x, y), 2.1, 2.1)


def _draw_open(painter, color, bg, accent):
    """A folder with its flap lifted."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawPolyline(QtGui.QPolygonF([
        QtCore.QPointF(2.6, 13.8), QtCore.QPointF(2.6, 4.2), QtCore.QPointF(6.8, 4.2),
        QtCore.QPointF(8.2, 5.8), QtCore.QPointF(13.6, 5.8), QtCore.QPointF(13.6, 8),
    ]))
    painter.setPen(_pen(accent))
    painter.drawPolygon(QtGui.QPolygonF([
        QtCore.QPointF(2.6, 13.8), QtCore.QPointF(5, 8.2), QtCore.QPointF(15.6, 8.2),
        QtCore.QPointF(13.2, 13.8),
    ]))


def _draw_save(painter, color, bg, accent):
    """A floppy disk -- still the one save mark everybody reads."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawPolygon(QtGui.QPolygonF([
        QtCore.QPointF(3, 3), QtCore.QPointF(12.6, 3), QtCore.QPointF(15, 5.4),
        QtCore.QPointF(15, 15), QtCore.QPointF(3, 15),
    ]))
    painter.drawRect(QtCore.QRectF(6, 3, 5.4, 3.4))
    painter.setPen(_pen(accent))
    painter.drawRect(QtCore.QRectF(5.4, 10, 7.2, 5))


def _draw_remove(painter, color, bg, accent):
    """A part outline with a minus badge."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRoundedRect(QtCore.QRectF(2.8, 2.8, 9.4, 9.4), 1.6, 1.6)
    painter.setPen(_pen(accent, 1.9))
    painter.setBrush(bg)
    painter.drawEllipse(QtCore.QPointF(12.6, 12.6), 3.4, 3.4)
    painter.drawLine(QtCore.QPointF(10.9, 12.6), QtCore.QPointF(14.3, 12.6))


def _magnifier(painter, color):
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(QtCore.QPointF(7.6, 7.6), 4.8, 4.8)
    painter.drawLine(QtCore.QPointF(11.2, 11.2), QtCore.QPointF(15.2, 15.2))


def _draw_zoom_in(painter, color, bg, accent):
    _magnifier(painter, color)
    painter.setPen(_pen(accent))
    painter.drawLine(QtCore.QPointF(5.4, 7.6), QtCore.QPointF(9.8, 7.6))
    painter.drawLine(QtCore.QPointF(7.6, 5.4), QtCore.QPointF(7.6, 9.8))


def _draw_zoom_out(painter, color, bg, accent):
    _magnifier(painter, color)
    painter.setPen(_pen(accent))
    painter.drawLine(QtCore.QPointF(5.4, 7.6), QtCore.QPointF(9.8, 7.6))


def _draw_zoom_fit(painter, color, bg, accent):
    """Four corner brackets around a sheet: fit the whole sheet."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx = 9 - sx * 6.2
        cy = 9 - sy * 6.2
        painter.drawPolyline(QtGui.QPolygonF([
            QtCore.QPointF(cx, cy + sy * 3.2), QtCore.QPointF(cx, cy), QtCore.QPointF(cx + sx * 3.2, cy),
        ]))
    painter.setPen(_pen(accent))
    painter.drawRect(QtCore.QRectF(6, 6.8, 6, 4.4))


def _chevron(painter, color, direction):
    painter.setPen(_pen(color, 1.9))
    painter.setBrush(QtCore.Qt.NoBrush)
    x0, x1 = (10.6, 6.4) if direction < 0 else (7.4, 11.6)
    painter.drawPolyline(QtGui.QPolygonF([
        QtCore.QPointF(x0, 4.4), QtCore.QPointF(x1, 9), QtCore.QPointF(x0, 13.6),
    ]))


def _draw_prev_sheet(painter, color, bg, accent):
    _chevron(painter, color, -1)


def _draw_next_sheet(painter, color, bg, accent):
    _chevron(painter, color, 1)


def _draw_layout_results(painter, color, bg, accent):
    """A column of sheet thumbnails -- the Layout Results pane."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawRoundedRect(QtCore.QRectF(2.6, 2.6, 12.8, 12.8), 2, 2)
    painter.drawLine(QtCore.QPointF(10.4, 2.6), QtCore.QPointF(10.4, 15.4))
    painter.setPen(_pen(accent))
    for y in (5.6, 9, 12.4):
        painter.drawLine(QtCore.QPointF(12.2, y), QtCore.QPointF(13.6, y))


def _draw_manual(painter, color, bg, accent):
    """A four-way move cross: nudge a part by hand."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawLine(QtCore.QPointF(9, 2.6), QtCore.QPointF(9, 15.4))
    painter.drawLine(QtCore.QPointF(2.6, 9), QtCore.QPointF(15.4, 9))
    painter.setPen(_pen(accent))
    for (ax, ay), (bx, by), (cx, cy) in (
            ((7, 4.6), (9, 2.6), (11, 4.6)), ((7, 13.4), (9, 15.4), (11, 13.4)),
            ((4.6, 7), (2.6, 9), (4.6, 11)), ((13.4, 7), (15.4, 9), (13.4, 11))):
        painter.drawPolyline(QtGui.QPolygonF([
            QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by), QtCore.QPointF(cx, cy),
        ]))


def _draw_dark_mode(painter, color, bg, accent):
    """A crescent moon."""
    path = QtGui.QPainterPath()
    path.addEllipse(QtCore.QPointF(9, 9), 6, 6)
    cut = QtGui.QPainterPath()
    cut.addEllipse(QtCore.QPointF(12.2, 6.4), 5, 5)
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawPath(path.subtracted(cut))


def _draw_recommend(painter, color, bg, accent):
    """A wand with a spark: fill these fields in for me."""
    painter.setPen(_pen(color, 1.9))
    painter.drawLine(QtCore.QPointF(3.2, 14.8), QtCore.QPointF(11, 7))
    painter.setPen(_pen(accent))
    for (ax, ay), (bx, by) in (((13.4, 2.2), (13.4, 6.2)), ((11.4, 4.2), (15.4, 4.2)),
                               ((6.2, 3), (6.2, 5.4)), ((5, 4.2), (7.4, 4.2))):
        painter.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by))


def _draw_formula(painter, color, bg, accent):
    """f(x) -- the recommendation formula's coefficients."""
    font = painter.font()
    font.setPixelSize(11)
    font.setItalic(True)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(QtCore.QRectF(0, 0, 18, 18), QtCore.Qt.AlignCenter, "f(x)")


def _draw_measure(painter, color, bg, accent):
    """A ruler lying on a slant, with tick marks: measure a distance."""
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.save()
    painter.translate(9, 9)
    painter.rotate(-40)
    painter.drawRoundedRect(QtCore.QRectF(-8, -3, 16, 6), 1, 1)
    painter.setPen(_pen(accent, 1.3))
    for i, x in enumerate((-5, -2.5, 0, 2.5, 5)):
        painter.drawLine(QtCore.QPointF(x, -3), QtCore.QPointF(x, -0.4 if i % 2 else 0.6))
    painter.restore()


GLYPHS = {
    "measure": _draw_measure,
    "parts": _draw_parts,
    "stock": _draw_stock,
    "nesting": _draw_nesting,
    "settings": _draw_settings,
    "open-job": _draw_open,
    "save-job": _draw_save,
    "remove-part": _draw_remove,
    "zoom-in": _draw_zoom_in,
    "zoom-out": _draw_zoom_out,
    "zoom-fit": _draw_zoom_fit,
    "prev-sheet": _draw_prev_sheet,
    "next-sheet": _draw_next_sheet,
    "layout-results": _draw_layout_results,
    "manual-adjust": _draw_manual,
    "dark-mode": _draw_dark_mode,
    "recommend": _draw_recommend,
    "formula": _draw_formula,
}


def glyph_icon(name, color, bg, size=GLYPH_PX, accent=None):
    """A `name` glyph stroked in `color`, as a QIcon, or an empty icon if
    there is no such glyph."""
    draw = GLYPHS.get(name)
    if draw is None:
        return QtGui.QIcon()
    ratio = 2
    pixmap = QtGui.QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.scale(size / GLYPH_PX, size / GLYPH_PX)   # glyphs are drawn in an 18px grid
    draw(painter, color, bg, accent if accent is not None else color)
    painter.end()
    return untinted(pixmap)


def untinted(pixmap):
    """A QIcon that looks the same in every state. Left to itself Qt
    generates the `Selected` copy by washing the pixmap in the Qt
    PALETTE's highlight color -- a stock blue, since this app's theme is
    pure QSS and never sets a palette (the same trap
    `nesting_widgets.untinted_icon` exists for)."""
    icon = QtGui.QIcon()
    for mode in (QtGui.QIcon.Normal, QtGui.QIcon.Selected, QtGui.QIcon.Active):
        icon.addPixmap(pixmap, mode)
    return icon
