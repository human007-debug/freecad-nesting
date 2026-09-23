"""
tests/test_canvas.py
---------------------
The live sheet canvas behaves like LibreCAD's drawing area: the wheel
zooms at the cursor (the point under it stays put), a middle-drag pans,
and the Measure tool reads exact distances by snapping to part corners
and edges, in the same axes the rulers show.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest

from nester import PlacedPart
from nesting_widgets import SheetPreview


@pytest.fixture()
def preview():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = SheetPreview()
    view.show_rulers = True
    view.resize(800, 500)
    square = PlacedPart("P", [(100, 200), (160, 200), (160, 260), (100, 260)], [], 0, 0, 0)
    view.set_sheet(1220, 2440, [square])   # portrait: drawn sideways
    view.show()
    view.grab()   # one paint, so the screen transform exists
    return view


def _wheel(view, pos, clicks):
    pos = QtCore.QPointF(pos)
    for _ in range(abs(clicks)):
        event = QtGui.QWheelEvent(pos, view.mapToGlobal(pos), QtCore.QPoint(),
                                  QtCore.QPoint(0, 120 if clicks > 0 else -120), QtCore.Qt.NoButton,
                                  QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(view, event)
        view.grab()


def _click_near(view, x, y, dx=3, dy=-3):
    sx, sy = view._to_screen(x, y)
    pos = QtCore.QPoint(int(sx) + dx, int(sy) + dy)
    QTest.mouseMove(view, pos)
    QTest.mouseClick(view, QtCore.Qt.LeftButton, pos=pos)


def test_wheel_zooms_at_the_cursor(preview):
    before = preview._to_screen(160, 260)
    _wheel(preview, QtCore.QPointF(*before), 10)
    after = preview._to_screen(160, 260)
    assert preview._zoom > 5
    assert after == pytest.approx(before, abs=1e-6)


def test_zooming_back_to_fit_recenters(preview):
    _wheel(preview, QtCore.QPointF(*preview._to_screen(160, 260)), 6)
    preview.set_zoom(1.0)
    assert preview._pan == QtCore.QPointF()


def test_middle_drag_pans(preview):
    before = preview._to_screen(100, 200)
    QTest.mousePress(preview, QtCore.Qt.MiddleButton, pos=QtCore.QPoint(300, 200))
    QTest.mouseMove(preview, QtCore.QPoint(340, 230))
    QTest.mouseRelease(preview, QtCore.Qt.MiddleButton, pos=QtCore.QPoint(340, 230))
    preview.grab()
    after = preview._to_screen(100, 200)
    assert (after[0] - before[0], after[1] - before[1]) == pytest.approx((40, 30))


def test_measure_snaps_to_corners_in_ruler_axes(preview):
    got = []
    preview.measured.connect(got.append)
    _wheel(preview, QtCore.QPointF(*preview._to_screen(130, 230)), 8)
    preview.set_measure_mode(True)
    _click_near(preview, 100, 200)
    _click_near(preview, 160, 260)
    m = got[-1]
    assert m["distance"] == pytest.approx(math.hypot(60, 60))
    # The 1220x2440 sheet is drawn sideways, so the horizontal ruler reads
    # the sheet's Y: the start corner is (200, 100) on screen axes.
    assert m["start"] == pytest.approx((200, 100))
    assert m["end"] == pytest.approx((260, 160))


def test_measure_snaps_onto_an_edge(preview):
    got = []
    preview.measured.connect(got.append)
    _wheel(preview, QtCore.QPointF(*preview._to_screen(130, 230)), 8)
    preview.set_measure_mode(True)
    _click_near(preview, 100, 230, dx=2, dy=0)   # just off the left edge, mid-way
    _click_near(preview, 160, 230, dx=-2, dy=0)  # just off the right edge
    # Each end lands exactly ON its edge (sheet x = 100 / 160, the
    # vertical ruler axis here), wherever along the edge it was clicked.
    m = got[-1]
    assert (m["start"][1], m["end"][1]) == pytest.approx((100, 160))
    assert m["dy"] == pytest.approx(60)


def test_esc_clears_then_leaves_measure(preview):
    modes = []
    preview.measure_mode_changed.connect(modes.append)
    preview.set_measure_mode(True)
    _click_near(preview, 100, 200)
    QTest.keyClick(preview, QtCore.Qt.Key_Escape)
    assert preview.measure_mode and preview._measure_start is None
    QTest.keyClick(preview, QtCore.Qt.Key_Escape)
    assert not preview.measure_mode
    assert modes == [True, False]
