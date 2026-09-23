"""
segmented.py
------------
The window's main tab strip: an Apple-style segmented control -- one
rounded track with the tabs inside it, and the selected tab marked by a
filled pill that floats within the track (see `applereference.jpg`, Apple
Music's tab bar: grey capsule, white pill under the active item, accent
color on its glyph and label).

Why this isn't just a styled `QTabBar`: the shape depends entirely on
padding and margins -- a track that hugs its tabs with an even inset all
round, and a pill inset within that -- and QTabBar honors neither from a
stylesheet. Styling it produced a pill taller than the track it sits in,
flush against the menu bar above. So the QTabWidget keeps doing its job
(pages, current index, persistence) with its own bar hidden, and this
widget drives it.

It stays in sync both ways: pressing a segment sets the QTabWidget's page,
and any other route to a page -- View > Nesting Tab, Ctrl+1/2/3, the Parts
tab's "Ready to nest ->" button, a restored session -- moves the pill.

The glyphs come from `glyphs.py` rather than from `alphanest-icon-pack/`:
the pack's icons are rounded tiles made to sit on toolbar buttons, and a
tile inside a pill would read as a button within a button. They are plain
strokes that take their color from the theme, so they tint with selection
the way the reference's do.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from native_app import glyphs, theme

_GLYPH_PX = glyphs.GLYPH_PX


class SegmentedTabs(QtWidgets.QFrame):
    """The track. `tabs` is the QTabWidget it drives; `segments` is a list
    of (page widget, label, glyph name)."""

    def __init__(self, tabs, segments, parent=None):
        super().__init__(parent)
        self.setObjectName("SegmentedTrack")
        self.tabs = tabs
        self._buttons = []
        self._glyphs = []

        tabs.tabBar().hide()   # this widget IS the tab bar now

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for index, (page, label, glyph) in enumerate(segments):
            button = QtWidgets.QToolButton(self)
            button.setObjectName("SegmentedTab")
            button.setText(label)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            button.setIconSize(QtCore.QSize(_GLYPH_PX, _GLYPH_PX))
            button.setFixedHeight(30)
            button.clicked.connect(
                lambda _checked, w=page: self.tabs.setCurrentWidget(w))
            group.addButton(button, index)
            layout.addWidget(button)
            self._buttons.append(button)
            self._glyphs.append(glyph)

        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        tabs.currentChanged.connect(self._sync)
        self._sync(tabs.currentIndex())

    def _sync(self, index):
        """Move the pill to whichever page the QTabWidget is actually on."""
        for i, button in enumerate(self._buttons):
            button.setChecked(i == index)
        self.refresh_icons()

    def refresh_icons(self):
        """Re-stroke the glyphs in the current theme's colors -- they're
        pixmaps, so they keep the palette they were drawn with (the
        selected one takes the accent, like the reference)."""
        palette = theme.tokens()
        for button, glyph in zip(self._buttons, self._glyphs):
            checked = button.isChecked()
            button.setIcon(glyphs.glyph_icon(
                glyph,
                QtGui.QColor(palette["accent" if checked else "text_dim"]),
                QtGui.QColor(palette["surface" if checked else "sunken"]),
            ))
