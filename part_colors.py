"""Per-part-type display colors.

Every distinct part *name* (the parts-table label, e.g. ``ALPHA-PLATE``)
is assigned one color from ``PALETTE``, first-come-first-served. Copies of
the same part share that color across every view -- the sheet preview, the
parts-table thumbnails, the Layout-results list and the HTML report (all of
which grab the same :class:`SheetPreview`), and the native Parts tab -- so
a part is always the same color wherever you look.

Palette choices: vivid but mid-lightness so fills read on both the dark and
light app themes and stay distinct from the sheet fill (#3d6690) and the
mirror/common-edge accents (#b44dd1, #ff4fa0). Colors far enough apart in
hue/lightness to tell similar shapes apart at a glance, with ~3 colors kept
in reserve-less territory: after 20 names the map wraps (rare in practice).

The map is reset whenever the parts table is cleared/rescanned (see
NestingPanel), so identical part lists get identical colors each session
and deleting a part releases its color.
"""

from collections import OrderedDict

from PySide6 import QtGui

# 20 distinct fills, arranged so neighbours are clearly separated.
PALETTE = [
    "#f26b5e",   # 0  coral red
    "#f2a541",   # 1  amber orange
    "#f0d25a",   # 2  golden yellow
    "#a9d15c",   # 3  lime green
    "#5fbf6a",   # 4  leaf green
    "#3ec7a0",   # 5  mint teal
    "#43cad8",   # 6  cyan
    "#4f8ff7",   # 7  azure blue
    "#5f7cf0",   # 8  indigo
    "#8a6cf0",   # 9  violet
    "#c06cf0",   # 10 purple
    "#f06cf0",   # 11 magenta
    "#f06fb8",   # 12 pink
    "#f07b9c",   # 13 rose
    "#e8846a",   # 14 salmon
    "#cf9275",   # 15 tan
    "#8ad06b",   # 16 light green
    "#6bd8b8",   # 17 aqua
    "#7fbdf0",   # 18 sky
    "#b79df0",   # 19 lilac
]

_hex_cache = {c: QtGui.QColor(c) for c in PALETTE}

_assigned = OrderedDict()  # part-name -> palette index


def reset():
    """Forget all assignments. Next ``color_for`` calls start from 0 again."""
    _assigned.clear()


def color_for(name):
    """Return the :class:`QColor` reserved for ``name``.

    The first time a name is seen it takes the lowest unused palette index;
    after that it always returns the same color. If more distinct names than
    palette entries appear, indexes wrap (still stable per name).
    """
    idx = _assigned.get(name)
    if idx is None:
        idx = len(_assigned) % len(PALETTE)
        _assigned[name] = idx
    return _hex_cache[PALETTE[idx]]


def _darken(qcolor, factor=0.55):
    """Scale an RGB color's channels toward black by `factor`."""
    return QtGui.QColor(
        int(qcolor.red() * factor),
        int(qcolor.green() * factor),
        int(qcolor.blue() * factor),
        qcolor.alpha(),
    )


def outline_for(name):
    """The stroke color for a part: a darkened version of its own fill, so
    each part is monochrome and the outline stays readable on the sheet."""
    return _darken(color_for(name))


_LIGHTEN_FACTOR = None  # reserved: nothing reads this at present