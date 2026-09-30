"""quoting/tender_costing.py, costing_sheet_xlsx.py and recost.py -- the
rate-card costing sheet. Uses the placeholder example card and a small
synthetic workbook in the costing-sheet layout (no real shop data)."""

import math
import os
import shutil
import subprocess

import pytest

from quoting.rate_card import SheetFormula, example_rate_card, FastenerPrice
from quoting.tender_costing import (BomItem, TenderCosting, TenderHeader, card_rate_for, import_costing_workbook,
                                    item_weights)

openpyxl = pytest.importorskip("openpyxl")
F = SheetFormula()


def card():
    c = example_rate_card()
    c.fasteners.append(FastenerPrice("HEX HD SCREW (M8x35)", "IS 1364", 20.0))
    return c


# ---------------------------------------------------------------- weights

def test_sheet_item_by_formula_and_by_hand_count():
    c = card()
    it = BomItem("bracket", "SHEET", "AISI 304", 3, 100, 150, qty=4)
    w = item_weights(it, c)
    assert w.net_pc == pytest.approx(F.piece_weight(3, 100, 150))
    assert w.qty_per_stock == F.qty_per_sheet(3, 100, 150)
    assert w.gross == pytest.approx(4 * F.sheet_weight(3) / F.qty_per_sheet(3, 100, 150))
    # the BOM's own sheet weight and hand-counted qty/sheet win
    it2 = BomItem("panel", "SHEET", "SS 304", 1.25, 833, 1405, qty=2, qty_per_stock=2, stock_weight=25.47)
    assert item_weights(it2, c).gross_pc == pytest.approx(25.47 / 2)


def test_tube_and_bar_items():
    c = card()
    sq = item_weights(BomItem("member", "SQ. TUBE", "SS 304", 3, "40*40", 600, qty=2), c)
    assert sq.net_pc == pytest.approx(F.square_tube_weight(3, 40, 600))
    assert sq.gross_pc == pytest.approx(F.square_tube_weight(3, 40, 610))      # + 10 mm cut allowance
    pipe = item_weights(BomItem("rail", "PIPE", "SS 304", 2, 30, 1500), c)
    assert pipe.net_pc == pytest.approx(math.pi * 30 * 2 * 1500 * 8e-6)
    rod = item_weights(BomItem("pin", "ROD", "SS 304", 20, None, 100, qty=10), c)
    per_bar = math.floor(6000 / 105)
    assert rod.qty_per_stock == per_bar
    assert rod.gross_pc == pytest.approx(math.pi / 4 * 400 * 6000 * 8e-6 / per_bar)
    flat = item_weights(BomItem("flat", "FLAT", "SS 304", 5, 40, 200), c)
    assert flat.net_pc == pytest.approx(5 * 40 * 200 * 8e-6)
    assert item_weights(BomItem("M8 screw", "FASTNERS", qty=8), c).gross == 0


def test_mean_perimeter_setting():
    c = card()
    c.settings["tube_perimeter"] = "mean"
    w = item_weights(BomItem("m", "SQ. TUBE", "SS 304", 4, "45x45", 1000), c)
    assert w.net_pc == pytest.approx(4 * 4 * (45 - 4) * 1000 * 8e-6)


# ---------------------------------------------------------------- costing

def job():
    items = [
        BomItem("side", "SHEET", "AISI 304", 2, 300, 1000, qty=2),
        BomItem("base", "SHEET", "AISI 304", 2, 400, 600, qty=1),
        BomItem("leg", "SQ. TUBE", "SS 304", 3, "40*40", 800, qty=4),
        BomItem("HEX. HD SCREW (M8*35)", "FASTNERS", qty=12),
        BomItem("ODD RIVET", "FASTNERS", qty=4),
    ]
    return TenderCosting(card(), TenderHeader(customer="Test Rly", product="Test rack", quantity=10), items)


def test_material_lines_group_and_price():
    tc = job()
    lines = tc.material_lines()
    assert [(l.shape, l.size) for l in lines] == [("SHEET", "2.00mm"), ("SQ. TUBE", "40SQ*3mm")]
    sheet, tube = lines
    assert (sheet.rate, sheet.factor, tube.rate, tube.factor) == (180.0, 0.95, 200.0, 0.9)
    assert sheet.value == pytest.approx(sheet.weight * 0.95 * 180)
    assert tc.scrap_kg() == pytest.approx(tc.gross_weight - tc.net_weight)
    assert tc.scrap_credit() == pytest.approx(tc.scrap_kg() * 50.0)          # both lines scrap at 50/kg
    assert tc.material_cost == pytest.approx(sheet.value + tube.value - tc.scrap_credit())


def test_material_override_is_the_last_word():
    tc = job()
    key = ("sheet", "2.00mm", "ss304")
    tc.material_overrides[key] = {"factor": 1.0, "rate": 150.0}
    sheet = tc.material_lines()[0]
    assert (sheet.factor, sheet.rate) == (1.0, 150.0)


def test_derived_processes_from_the_bom():
    tc = job()
    procs = {p.description: p for p in tc.derived_processes()}
    laser = procs["LASER CUTTING 2mm"]
    assert laser.qty == pytest.approx((2 * (300 + 1000) * 2 + 2 * (400 + 600)) / 1000)
    assert laser.rate == pytest.approx(4.0)                                   # 2 x 2 mm on the example card
    assert procs["TUBE CUTTING"].qty == 4 and procs["TUBE CUTTING"].rate is None   # not on the example card
    fast = procs["FASTENERS"]
    assert fast.rate == pytest.approx(12 * 20.0) and "ODD RIVET" in fast.remarks


def test_totals_margin_and_lpr():
    tc = job()
    t = tc.totals()
    assert t["all_total"] == pytest.approx(t["material"] + t["process"] + t["others"])
    assert t["basic"] == pytest.approx(t["all_total"] * 1.12)
    assert t["all_inclusive"] == pytest.approx(t["basic"] * 1.18)
    assert t["tender_value"] == pytest.approx(t["all_inclusive"] * 10)
    target = 50000.0
    tc.margin_pct = tc.margin_for_rate(target)
    assert tc.totals()["all_inclusive"] == pytest.approx(target)
    assert tc.rounded_rate(12345.6) == 12300.0


@pytest.mark.parametrize("text,expected", [
    ("LASER CUTTING(4mm)", 8.0), ("LASER CUTTING 12.00mm", 48.0), ("BENDNG", 5.0), ("BENDING (<500mm)", 5.0),
    ("TUBE HOLES DRILLING", 8.0), ("WELDING", 40.0),
])
def test_card_rate_for_sheet_wording(text, expected):
    rate, _ = card_rate_for(card(), text)
    assert rate == pytest.approx(expected)


def test_card_rate_for_unknown_keeps_the_sheet_rate():
    assert card_rate_for(card(), "MESH PIERCING", 800.0) == (800.0, pytest.approx(
        "not priced from the rate card -- the sheet's own rate is kept"))
    assert card_rate_for(card(), "LASER CUTTING", 12.0)[0] == 12.0            # per-mm with no thickness


# ------------------------------------------------------ workbook round trip

def make_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "COSTING SHEET"
    rows = {1: ["COSTING SHEET"], 2: ["CUSTOMER NAME", None, None, None, None, "Test Rly"],
            7: ["TENDER NUMBER", None, None, None, None, "T-1"], 10: ["PRODUCT", None, None, None, None, "Rack"],
            12: ["QUANTITY IN NOS", None, None, None, None, "20.00 Pair"], 14: ["MATERIALS"],
            15: ["S NO", "SHAPE"], 16: [1, "SHEET", "2mm", "SS 304", "KGS", 10, 0.95, 9.5, 190, 1805],
            18: ["A", "TOTAL MATERIAL COST", None, None, None, 10, None, None, None, 1805], 19: ["PROCESS"],
            20: ["S NO", "PROCESS DESCRIPTION"],
            21: [1, "LASER CUTTING(2mm)", None, None, None, "METER", None, 5, 6, 30],
            22: [2, "PIERICNG", None, None, None, "NOS", None, 10, 2, 20],
            23: [3, "MESH PIERCING", None, None, None, "NOS", None, 1, 800, 800],
            24: ["B", "TOTAL PROCESS COST", None, None, None, None, None, None, None, 850], 25: ["OTHERS"],
            26: ["C", "BOUGHT OUT PARTS", None, None, None, None, None, None, None, 0],
            27: ["E", "PACKING& TRANSPORT CHARGES", None, None, None, None, None, None, None, 700],
            28: ["I", "ALL TOTAL  A+B+C+D+E", None, None, None, None, None, None, None, 3355],
            29: ["J", "CONTINGENCIES", None, None, None, 0.2],
            30: ["L", "GST", None, None, None, 0.18],
            31: ["M", "ALL INCLUSIVE RATE", None, None, None, None, None, None, None, 4750.68],
            32: ["O", "LPR", None, None, None, None, None, None, None, None, 4700],
            34: [None, None, None, None, "QUOTED RATE: ", None, None, 4700]}
    for r, vals in rows.items():
        for c, v in enumerate(vals, start=1):
            ws.cell(r, c, v)
    bs = wb.create_sheet("RAW MATERIAL")
    bs["A1"] = "SHOP"
    for c, (a, b) in enumerate([("S NO", None), ("MAIN QTY", None), ("DESCRIPTION", None),
                                ("RAW MATERIAL SPEC", "TYPE"), (None, "GRADE"), ("MATERIAL ", "THICK"),
                                (None, "WIDTH"), (None, "LNTH"), (None, "TOTAL QTY"), ("SHEET WT.", None),
                                ("QTY/SHEET", None), ("PIERCING", None)], start=1):
        bs.cell(3, c, a)
        bs.cell(4, c, b)
    for r, vals in enumerate([[1, 1, "SIDE", "SHEET", "SS 304", 2, 300, 1000, 2, None, None, 4],
                              [2, 1, "LEG", "SQ. TUBE", "SS 304", 3, "40*40", 800, 4, None, None, None],
                              [3, 1, "HEX. HD SCREW (M8*35)", "FASTNERS", None, None, None, None, 6]], start=5):
        for c, v in enumerate(vals, start=1):
            bs.cell(r, c, v)
    wb.save(path)


def test_import_costing_workbook(tmp_path):
    path = str(tmp_path / "old.xlsx")
    make_workbook(path)
    tc, sheet = import_costing_workbook(path, card())
    assert (tc.header.customer, tc.header.tender_number, tc.header.quantity, tc.header.unit) == \
        ("Test Rly", "T-1", 20.0, "Pair")
    assert [i.description for i in tc.items] == ["SIDE", "LEG", "HEX. HD SCREW (M8*35)"]
    assert tc.items[0].processes == {"PIERCING": 4.0}
    procs = {p.description: p for p in tc.process_lines()}
    assert procs["LASER CUTTING(2mm)"].rate == pytest.approx(4.0)             # re-priced from the card
    assert procs["MESH PIERCING"].rate == 800 and procs["MESH PIERCING"].remarks  # kept, flagged
    assert (tc.margin_label, tc.margin_pct, tc.gst_pct) == ("CONTINGENCIES", 20.0, 18.0)
    assert (tc.lpr, tc.quoted_rate) == (4700, 4700)
    assert [o.code for o in tc.others] == ["C", "E"] and tc.others_total == 700
    assert sheet["totals"]["all_inclusive"] == pytest.approx(4750.68)
    assert sheet["gross_weight"] == 10


def test_write_costing_workbook_has_live_formulas(tmp_path):
    from quoting.costing_sheet_xlsx import write_costing_workbook
    path = str(tmp_path / "old.xlsx")
    make_workbook(path)
    tc, sheet = import_costing_workbook(path, card())
    out = str(tmp_path / "new.xlsx")
    write_costing_workbook(out, tc, sheet)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["COSTING SHEET", "CONSOLIDATED BOM", "COMPARISON"]
    ws = wb["COSTING SHEET"]
    labels = {ws.cell(r, 2).value: r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value}
    m = labels["CONTINGENCIES"]
    assert ws.cell(m, 6).value == pytest.approx(0.20)
    assert str(ws.cell(labels["ALL INCLUSIVE RATE"], 10).value).startswith("=")
    assert str(ws.cell(labels["BASIC COST"], 10).value).startswith("=")
    if shutil.which("soffice") is None:
        pytest.skip("LibreOffice not installed -- formulas not recalculated")
    subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir",
                    str(tmp_path / "calc"), out], capture_output=True, timeout=180)
    calc = openpyxl.load_workbook(str(tmp_path / "calc" / "new.xlsx"), data_only=True)["COSTING SHEET"]
    assert calc.cell(labels["ALL INCLUSIVE RATE"], 10).value == pytest.approx(tc.totals()["all_inclusive"], rel=1e-6)
    assert calc.cell(labels["TOTAL MATERIAL COST"], 10).value == pytest.approx(tc.material_cost, rel=1e-6)


def test_recost_cli_match_lpr(tmp_path, capsys):
    from quoting import recost
    from quoting.rate_card import save_rate_card
    path, card_path, out = str(tmp_path / "old.xlsx"), str(tmp_path / "rate_card.xlsx"), str(tmp_path / "o.xlsx")
    make_workbook(path)
    save_rate_card(card_path, card())
    assert recost.main([path, "--card", card_path, "--out", out, "--match-lpr"]) == 0
    text = capsys.readouterr().out
    assert "margin to meet it" in text and os.path.exists(out)
    tc, _ = import_costing_workbook(path, card())
    ws = openpyxl.load_workbook(out)["COSTING SHEET"]
    labels = {ws.cell(r, 2).value: r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value}
    assert ws.cell(labels["CONTINGENCIES"], 6).value == pytest.approx(tc.margin_for_rate(4700) / 100)
    assert recost.main([path, "--card", str(tmp_path / "missing.xlsx")]) == 2
