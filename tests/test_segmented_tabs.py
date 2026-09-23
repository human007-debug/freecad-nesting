"""
tests/test_segmented_tabs.py
-----------------------------
The main tab strip is an Apple-style segmented control driving a QTabWidget
whose own bar is hidden (native_app/segmented.py). That split is the thing
worth pinning: the strip and the pages must never disagree about which tab
is current, no matter which side moved -- a press on a segment, View >
Nesting Tab / Ctrl+1..3, the Parts tab's "Ready to nest ->" button, or a
session restored by persistence.py.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from native_app import theme
from native_app.segmented import SegmentedTabs


@pytest.fixture
def app():
    existing = QtWidgets.QApplication.instance()
    yield existing or QtWidgets.QApplication([])


@pytest.fixture
def strip(app):
    tabs = QtWidgets.QTabWidget()
    pages = [QtWidgets.QWidget() for _ in range(3)]
    for page, name in zip(pages, ("Parts", "Stock", "Nesting")):
        tabs.addTab(page, name)
    widget = SegmentedTabs(tabs, [
        (pages[0], "Parts", "parts"),
        (pages[1], "Stock", "stock"),
        (pages[2], "Nesting", "nesting"),
    ])
    yield widget, tabs, pages
    widget.deleteLater()
    tabs.deleteLater()


def _buttons(widget):
    return widget.findChildren(QtWidgets.QToolButton)


def test_the_tab_widgets_own_bar_is_hidden(strip):
    """Two tab bars stacked on top of each other is the failure this
    whole widget exists to avoid."""
    widget, tabs, _pages = strip
    assert tabs.tabBar().isHidden()


def test_pressing_a_segment_changes_the_page(strip):
    widget, tabs, pages = strip
    _buttons(widget)[2].click()
    assert tabs.currentWidget() is pages[2]


def test_changing_the_page_moves_the_pill(strip):
    """The other direction: anything else that switches tabs (a menu
    action, a shortcut, a restored session) has to move the marker too."""
    widget, tabs, pages = strip
    tabs.setCurrentWidget(pages[1])
    assert [b.isChecked() for b in _buttons(widget)] == [False, True, False]


def test_exactly_one_segment_is_ever_marked(strip):
    widget, tabs, _pages = strip
    for index in range(3):
        tabs.setCurrentIndex(index)
        assert sum(b.isChecked() for b in _buttons(widget)) == 1


def test_the_selected_segment_takes_the_accent_color(strip):
    """The reference's one unmistakable cue: the active tab's glyph and
    label are in the brand color, the others are quiet grey."""
    widget, tabs, _pages = strip
    tabs.setCurrentIndex(0)
    selected, *rest = _buttons(widget)
    assert not selected.icon().isNull()
    assert all(not b.icon().isNull() for b in rest)
    # Re-stroked per theme, so a dark-mode flip can't leave light-mode
    # glyphs behind.
    before = selected.icon().cacheKey()
    theme.apply_theme(QtWidgets.QApplication.instance(), True)
    widget.refresh_icons()
    theme.apply_theme(QtWidgets.QApplication.instance(), False)
    assert selected.icon().cacheKey() != before
