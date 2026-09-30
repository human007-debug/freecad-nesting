"""
tests/test_quote_panel.py
--------------------------
The native app's Quote tab (native_app/quote_panel.py): parts from the
Parts tab become quote lines, extra lines can be added, the quote prices
at every quantity break on a worker thread, and cells can be overridden
and exported.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets

from inventory import StockSheet
from native_app import persistence
from native_app.main_window import MainWindow
from native_app.quote_panel import LineSpec, QuoteSetup, build_engine, parse_breaks
from quoting.costing import MATERIAL, QUOTE
from quoting.rates import load_rates, starter_rates


def _circle(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


PARTS = {
    "Bracket": {"outer": [(0, 0), (200, 0), (200, 100), (0, 100)],
                "holes": [_circle(50, 50, 5), _circle(150, 50, 5)],
                "thickness": 3.0, "method": "fcstd", "quantity": 2, "material": "mild steel",
                "bends": 2, "bend_details": [], "hole_features": []},
    "Plate": {"outer": [(0, 0), (120, 0), (120, 80), (0, 80)], "holes": [],
              "thickness": None, "method": "dxf", "quantity": 1, "material": None},
}


@pytest.fixture()
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "_ORGANIZATION", "AlphaNest")
    monkeypatch.setattr(persistence, "_APP", "AlphaNest-Tests")
    QtCore.QSettings.setPath(QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope,
                             str(tmp_path / "qsettings"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.quote_panel.wait()
    win.close()
    app.processEvents()


def _load(window):
    window.panel._load_parts_dict({k: dict(v) for k, v in PARTS.items()})
    qp = window.quote_panel
    qp.s_breaks.setText("1, 5, 20")    # the app's 1/10/100 re-nests 300 parts: seconds, not ms
    return qp


def _line(qp, name):
    return next(l for l in qp.lines if l.name == name)


def _price(qp):
    qp.price()
    qp.wait()
    assert qp.result is not None, qp.status_label.text()
    return qp.result


# ------------------------------------------------------------------- tab

def test_default_quantities(window):
    assert parse_breaks(window.quote_panel.s_breaks.text()) == [1, 10, 100]


def test_quote_tab_shows_the_quote_panel(window):
    window.ribbon.set_current_index(window._tab_index("Quote"))
    assert window.workspace.currentWidget() is window.quote_panel
    window.ribbon.set_current_index(window._tab_index("Nesting"))
    assert window.workspace.currentWidget() is window.panel


# ----------------------------------------------------------------- lines

def test_parts_become_sheet_lines(window):
    qp = _load(window)
    assert [l.name for l in qp.lines] == ["Bracket", "Plate"]
    bracket, plate = _line(qp, "Bracket"), _line(qp, "Plate")
    assert (bracket.material, bracket.thickness, bracket.bends, bracket.quantity) == ("mild steel", 3.0, 2, 2)
    assert plate.thickness is None and plate.bends is None       # DXF: unknown until entered
    assert qp.lines_table.rowCount() == 2


def test_edits_survive_a_parts_resync(window):
    qp = _load(window)
    qp.lines_table.selectRow(1)
    qp.f_material.setText("stainless")
    qp.f_material.editingFinished.emit()
    qp.f_thickness.setValue(2.0)
    qp.f_bends.setValue(1)
    qp.f_paint.setChecked(True)
    plate = _line(qp, "Plate")
    assert (plate.material, plate.thickness, plate.bends, plate.paint) == ("stainless", 2.0, 1, True)
    assert "painted" in qp.lines_table.item(1, 4).text()
    window.panel.parts_changed.emit()                  # e.g. another part added elsewhere
    plate = _line(qp, "Plate")
    assert (plate.material, plate.thickness, plate.bends, plate.paint) == ("stainless", 2.0, 1, True)


def test_add_and_remove_lines(window):
    qp = _load(window)
    tube = qp.add_line("tube")
    bolt = qp.add_line("purchased")
    assy = qp.add_line("assembly")
    assert [l.part_type for l in qp.lines[2:]] == ["tube", "purchased", "assembly"]
    assert qp._selected_line() is assy
    qp.lines_table.selectRow(qp.lines.index(bolt))
    qp.remove_selected_line()
    assert bolt not in qp.lines and tube in qp.lines
    qp.add_line("purchased")
    qp.add_line("purchased")
    names = [l.name for l in qp.lines]
    assert len(names) == len(set(names))


def test_parts_tab_lines_cannot_be_removed_here(window, monkeypatch):
    qp = _load(window)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    qp.lines_table.selectRow(0)
    qp.remove_selected_line()
    assert _line(qp, "Bracket")


# --------------------------------------------------------------- pricing

def test_price_quote_end_to_end(window):
    qp = _load(window)
    plate = _line(qp, "Plate")
    plate.material, plate.thickness, plate.bends = "mild steel", 3.0, 0
    qp.add_line("purchased").unit_cost = 0.2
    res = _price(qp)
    assert [b.quantity for b in res.breaks] == [1, 5, 20]
    assert qp.summary.rowCount() == 3
    heads = [qp.grid.horizontalHeaderItem(c).text() for c in range(qp.grid.columnCount())]
    assert heads[:4] == ["Line", "Type", "Qty", "Material"]
    assert {"Laser", "Bending", "Unit cost", "Line total"} <= set(heads)
    one, twenty = res.at(1), res.at(20)
    assert one.line("Bracket").cells["bending"].value is not None
    assert twenty.set_price < one.set_price
    assert "No sheet stock loaded" in qp.notes.toPlainText()             # fallback sheets used
    assert one.complete


def test_loaded_stock_is_used_instead_of_the_fallback(window):
    qp = _load(window)
    _line(qp, "Plate").thickness = 3.0
    _line(qp, "Plate").material = "mild steel"
    window.panel._inventory_template = [StockSheet("mild steel", 3.0, 1000, 500, price_per_kg=2.0,
                                                   density_g_cm3=7.85)]
    res = _price(qp)
    assert "No sheet stock loaded" not in qp.notes.toPlainText()
    sheet_kg = 1000 * 500 * 3 * 7.85 / 1e6
    b = res.at(1)
    total = 2 * b.line("Bracket").cells[MATERIAL].value + b.line("Plate").cells[MATERIAL].value
    assert total == pytest.approx(sheet_kg * 2.0)


def test_incomplete_line_is_flagged_not_hidden(window):
    qp = _load(window)                                  # Plate: no material/thickness yet
    res = _price(qp)
    assert not res.at(1).complete
    assert "Incomplete" in qp.summary.item(0, 5).text()
    assert "Plate" in qp.notes.toPlainText()


def test_override_a_cell_and_clear_it(window):
    qp = _load(window)
    _line(qp, "Plate").material, _line(qp, "Plate").thickness = "mild steel", 3.0
    res = _price(qp)
    before = res.at(1).total_price
    calculated = res.at(1).line("Bracket").cells["laser"].calculated
    qp.set_override("Bracket", "laser", 0.0, quantity=1)
    cell = res.at(1).line("Bracket").cells["laser"]
    assert cell.value == 0.0 and cell.calculated == calculated
    assert res.at(1).total_price < before
    # the override is re-applied after pricing again
    res2 = _price(qp)
    assert res2.at(1).line("Bracket").cells["laser"].value == 0.0
    qp.set_override("Bracket", "laser", None, quantity=1)
    assert res2.at(1).line("Bracket").cells["laser"].value == pytest.approx(calculated)


def test_typing_in_the_grid_overrides(window):
    qp = _load(window)
    _line(qp, "Plate").material, _line(qp, "Plate").thickness = "mild steel", 3.0
    _price(qp)
    qp.summary.selectRow(0)
    heads = [qp.grid.horizontalHeaderItem(c).text() for c in range(qp.grid.columnCount())]
    item = qp.grid.item(0, heads.index("Laser"))
    item.setText("12.5")
    QtWidgets.QApplication.processEvents()
    assert qp.result.at(1).line("Bracket").cells["laser"].value == 12.5
    transport = next(r for r in range(qp.grid.rowCount())
                     if qp.grid.item(r, 0) and qp.grid.item(r, 0).text() == "Transport")
    col = next(c for c in range(qp.grid.columnCount())
               if qp.grid.item(transport, c) and qp.grid.item(transport, c).data(QtCore.Qt.UserRole))
    qp.grid.item(transport, col).setText("0")
    QtWidgets.QApplication.processEvents()
    assert qp.result.at(1).quote_cells["transport"].value == 0.0


def test_bad_quantities_are_refused(window, monkeypatch):
    qp = _load(window)
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a))
    qp.s_breaks.setText("1, ten")
    qp.price()
    assert warned and qp.result is None and not qp.busy


# ---------------------------------------------------------- files

def test_export_quote_and_rates_round_trip(window, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    qp = _load(window)
    _line(qp, "Plate").material, _line(qp, "Plate").thickness = "mild steel", 3.0
    _price(qp)
    path = qp.export_xlsx(str(tmp_path / "quote.xlsx"))
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "1 off", "5 off", "20 off"]
    assert [c.value for c in wb["Summary"][1]][:3] == ["Sets", "Total cost", "Total price"]

    rates_path = str(tmp_path / "rates.xlsx")
    qp.export_rates_file(rates_path)
    assert load_rates(rates_path).all_rows() == starter_rates().all_rows()
    qp.load_rates_file(rates_path)
    assert qp.rates_source == "rates.xlsx"
    assert "uncalibrated" in qp.rates_label.text()


# ----------------------------------------------------- Qt-free helpers

def test_parse_breaks():
    assert parse_breaks("100, 1 10") == [1, 10, 100]
    with pytest.raises(ValueError):
        parse_breaks("0, 5")
    with pytest.raises(ValueError):
        parse_breaks(" ")


def test_build_engine_tube_and_assembly_lines():
    lines = [LineSpec("Rail", "tube", quantity=2, material="mild steel", profile_kind="RHS",
                      width=100, height=50, wall=3, length=1200, end_a="mitre", end_b="mitre",
                      price_per_m=11.0),
             LineSpec("Frame", "assembly", material="mild steel", thickness=3.0, part_count=3,
                      fastener_count=4, weld_length=800.0),
             LineSpec("Bolt", "purchased", quantity=4, unit_cost=0.15, markup_pct=100.0)]
    engine, features, notes = build_engine(lines, QuoteSetup(breaks=[1]), starter_rates(), None)
    assert [f.part_type for f in features] == ["tube", "assembly", "purchased"]
    assert features[0].profile.designation == "RHS 100x50x3"
    br = engine.price(features, breaks=[1]).breaks[0]
    assert br.complete
    assert br.line("Rail").cells[MATERIAL].value > 0
    assert set(br.line("Frame").cells) == {"welding", "assembly"}
    assert br.line("Bolt").cells["bought-in"].price == pytest.approx(0.30)
    assert br.quote_cells["transport"].value == 80.0
    assert notes == []
