"""
tests/test_quote_app.py
------------------------
AlphaQuote (quote_app/): the quote list over the database, the editor
saving itself, customers, revisions, status, and the hand-offs to and from
AlphaNest.
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

from quoting.db import QuoteDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_DXF = os.path.join(ROOT, "examples", "flat_plate.dxf")
EXAMPLE_STOCK = os.path.join(ROOT, "examples", "inventory.xlsx")


@pytest.fixture()
def settings_dir(tmp_path):
    QtCore.QSettings.setPath(QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope,
                             str(tmp_path / "qsettings"))
    return tmp_path


@pytest.fixture()
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def win(app, settings_dir, monkeypatch):
    from quote_app.main_window import QuoteAppWindow
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    w = QuoteAppWindow(str(settings_dir / "quotes.db"))
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


def _cheap(win):
    win.editor.s_breaks.setText("1, 5")


def _plate_quote(win):
    rec = win.new_quote()
    _cheap(win)
    win.add_parts([EXAMPLE_DXF])
    line = win.editor.lines[0]
    line.material, line.thickness, line.bends = "mild steel", 3.0, 1
    win.editor._fill_lines()
    win._mark_dirty()
    return rec


# ------------------------------------------------------------------ basics

def test_starts_empty_then_new_quote(win):
    assert win.record is None and not win.editor.isVisible()
    assert not win.price_btn.isEnabled()
    rec = win.new_quote()
    assert win.record.id == rec.id and win.editor.isVisible() and win.price_btn.isEnabled()
    assert rec.number.startswith("Q-") and win.quote_list.rowCount() == 1
    assert rec.label in win.windowTitle()


def test_parts_from_files_become_editable_lines(win):
    _plate_quote(win)
    (line,) = win.editor.lines
    assert line.part_type == "sheet" and not line.from_parts and line.outer
    win.editor.lines_table.selectRow(0)
    win.editor.remove_selected_line()                  # user-owned here: removable
    assert win.editor.lines == []


def test_edits_save_and_come_back(win, settings_dir):
    rec = _plate_quote(win)
    win.title.setText("Guard frame")
    win.title.textEdited.emit("Guard frame")
    win.editor.add_line("purchased").unit_cost = 0.5
    win.editor.price()
    win.editor.wait()
    win.save_current()
    saved = QuoteDB(str(settings_dir / "quotes.db")).quote(rec.id)
    assert saved.title == "Guard frame"
    assert [l["name"] for l in saved.data["lines"]] == ["Mount-Plate", "Bought-in part"]
    assert [s["sets"] for s in saved.summary] == [1, 5]
    # open another quote and come back: everything is there, summary shown
    other = win.new_quote()
    assert win.record.id == other.id and win.editor.lines == []
    win.open_quote(rec.id)
    assert [l.name for l in win.editor.lines] == ["Mount-Plate", "Bought-in part"]
    assert win.editor.lines[0].thickness == 3.0
    assert win.editor.s_breaks.text() == "1, 5"
    assert win.editor.summary.rowCount() == 2 and win.editor.result is None
    assert "Q-" in win.quote_list.item(0, 0).text()
    values = {win.quote_list.item(r, 0).text(): win.quote_list.item(r, 4).text()
              for r in range(win.quote_list.rowCount())}
    assert values[f"{rec.number} r1"].endswith("@ 1")


def test_autosave_after_a_pause(win, app, settings_dir):
    rec = win.new_quote()
    win.editor.add_line("tube")
    assert win._dirty and win._save_timer.isActive()
    QtCore.QThread.msleep(1400)
    app.processEvents()
    QtCore.QCoreApplication.sendPostedEvents()
    deadline = QtCore.QDeadlineTimer(3000)
    while win._dirty and not deadline.hasExpired():
        app.processEvents()
    assert not win._dirty
    assert QuoteDB(str(settings_dir / "quotes.db")).quote(rec.id).data["lines"][0]["part_type"] == "tube"


def test_customer_typed_in_is_created_and_linked(win):
    rec = win.new_quote()
    win.customer.setEditText("Acme Fabrication")
    win._customer_edited()
    assert win.db.quote(rec.id).customer_name == "Acme Fabrication"
    assert [c.name for c in win.db.customers()] == ["Acme Fabrication"]
    assert win.quote_list.item(0, 1).text() == "Acme Fabrication"
    win.customer.setEditText("")
    win._customer_edited()
    assert win.db.quote(rec.id).customer_id is None


def test_revision_duplicate_delete(win, monkeypatch):
    rec = _plate_quote(win)
    win.save_current()
    win.mark_status("sent")
    r2 = win.revise_quote()
    assert (r2.number, r2.revision, win.record.status) == (rec.number, 2, "draft")
    assert win.db.quote(rec.id).status == "sent"
    win.latest_only.setChecked(False)
    assert win.quote_list.rowCount() == 2
    win.latest_only.setChecked(True)
    assert win.quote_list.rowCount() == 1
    dup = win.duplicate_quote()
    assert dup.number != rec.number and [l.name for l in win.editor.lines] == ["Mount-Plate"]
    win.delete_quote(confirm=False)
    assert win.db.quote(dup.id) is None and win.record is not None


def test_search_and_status_filter(win):
    a = win.new_quote()
    win.title.setText("Guard frame")
    win._mark_dirty()
    win.save_current()
    win.mark_status("won", won_quantity=5)
    win.new_quote()
    win.search.setText("guard")
    assert win.quote_list.rowCount() == 1
    win.search.clear()
    win.status_filter.setCurrentIndex(win.status_filter.findData("won"))
    assert win.quote_list.rowCount() == 1 and win.quote_list.item(0, 0).text().startswith(a.number)
    assert "Won" in win.quote_list.item(0, 3).text()


def test_stock_is_loaded_and_remembered(win, app, settings_dir):
    pytest.importorskip("openpyxl")
    win.load_stock(EXAMPLE_STOCK)
    assert win.source.stock() and "inventory.xlsx" in win.stock_label.text()
    from quote_app.main_window import QuoteAppWindow
    win.close()
    again = QuoteAppWindow(str(settings_dir / "quotes.db"))
    try:
        assert again.source.stock() and len(again.source.stock()) == len(win.source.stock())
    finally:
        again.close()


def test_currency_follows_setting(win):
    win.currency.setCurrentIndex(win.currency.findData("USD"))
    assert win.source.currency_code == "USD"
    assert win.editor._money(3) == "$3.00"


# --------------------------------------------------------------- hand-offs

def test_send_won_quote_to_alphanest(win):
    rec = _plate_quote(win)
    win.save_current()
    win.mark_status("won", won_quantity=5)
    launched = []
    win.launch_alphanest = launched.append
    path = win.send_to_alphanest()
    assert launched == [path] and os.path.basename(path) == f"{rec.number}-rev1-5sets.json"
    with open(path) as f:
        (part,) = json.load(f)["parts"]
    assert part["name"] == "Mount-Plate" and part["quantity"] == 5 and part["thickness"] == 3.0


def test_alphanest_quote_tab_sends_to_alphaquote(app, settings_dir, monkeypatch):
    from native_app import persistence
    from native_app.main_window import MainWindow
    monkeypatch.setattr(persistence, "_APP", "AlphaNest-Tests")
    nest = MainWindow()
    try:
        nest.panel._load_parts_dict({"Plate": {
            "outer": [(0, 0), (100, 0), (100, 50), (0, 50)], "holes": [], "thickness": 2.0,
            "method": "fcstd", "quantity": 3, "material": "stainless", "bends": 1}})
        launched = []
        db_path = str(settings_dir / "shared.db")
        rec = nest._send_to_alphaquote(db_path=db_path, launcher=lambda *a: launched.append(a))
        assert launched == [("--open", rec.id)]
        saved = QuoteDB(db_path).quote(rec.id)
        (line,) = saved.data["lines"]
        assert line["name"] == "Plate" and line["quantity"] == 3 and line["from_parts"] is False
        assert saved.title == "From AlphaNest" and saved.status == "draft"
    finally:
        nest.close()
    # and AlphaQuote opens it with the line intact
    from quote_app.main_window import QuoteAppWindow
    aq = QuoteAppWindow(db_path)
    try:
        aq.open_quote(rec.id)
        assert [l.name for l in aq.editor.lines] == ["Plate"]
        assert aq.editor.lines[0].material == "stainless"
    finally:
        aq.close()


def test_launchers_use_the_project_scripts(monkeypatch):
    from quote_app import launch
    calls = []
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    launch.launch_alphaquote("--open", 3)
    launch.launch_alphanest("/tmp/parts.json")
    (aq_cmd, aq_kw), (an_cmd, _) = calls
    assert aq_cmd[1].endswith(os.path.join("quote_app", "main.py")) and aq_cmd[2:] == ["--open", "3"]
    assert an_cmd[1].endswith(os.path.join("native_app", "main.py")) and an_cmd[2] == "/tmp/parts.json"
    assert aq_kw["cwd"] == launch.ROOT


def test_main_parses_arguments(monkeypatch, settings_dir):
    import quote_app.main_window as mw
    from quote_app import main as entry
    opened = {}

    class FakeWindow:
        def __init__(self, path):
            opened["db"] = path

        def resize(self, *a):
            pass

        def show(self):
            pass

        def open_quote(self, qid):
            opened["id"] = qid

    class FakeApp:
        def __init__(self, args):
            opened["qt_args"] = args

        def setApplicationName(self, name):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(mw, "QuoteAppWindow", FakeWindow)
    monkeypatch.setattr(entry.QtWidgets, "QApplication", FakeApp)
    with pytest.raises(SystemExit):
        entry.main(["--db", str(settings_dir / "x.db"), "--open", "7"])
    assert opened["db"] == str(settings_dir / "x.db") and opened["id"] == 7
