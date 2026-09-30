"""quoting/rate_card.py: the shop rate card and the BOM sheet formula.
Only the placeholder example card is used here -- a shop's real card is
local and never in the repo."""

import math

import pytest

from quoting.rate_card import (FastenerPrice, MaterialRate, OtherCharge, ProcessRate, RateCard,
                               RateCardError, SheetFormula, default_rate_card_path, example_rate_card,
                               grade_family, load_rate_card, save_rate_card, shape_class)


# ------------------------------------------------------------- names

@pytest.mark.parametrize("name,family", [
    ("SS 304", "ss304"), ("AISI 304", "ss304"), ("X5CrNi1810", "ss304"), ("SS 304 -2B", "ss304"),
    ("Ck 304", "ss304"), ("X2CrNi12 (SS409m)", "ss409m"), ("C- K 201  X2CrNi12", "ss409m"),
    ("X04Cr12 (410)", "ss410"), ("Mild Steel", "ms"), ("Inconel 625", "inconel 625"),
])
def test_grade_family(name, family):
    assert grade_family(name) == family


@pytest.mark.parametrize("name,cls", [
    ("SHEET", "sheet"), ("PLATE", "plate"), ("SQ. TUBE", "tube"), ("REC TUBE", "tube"), ("PIPE", "tube"),
    ("SQ. ROD", "bar"), ("ROD", "bar"), ("FLAT", "bar"), ("45 SQ TUBE", "tube"),
])
def test_shape_class(name, cls):
    assert shape_class(name) == cls


# -------------------------------------------------------- sheet formula

def test_sheet_formula_matches_the_bom_sheets():
    f = SheetFormula()
    # weight per piece: t x W x L x 8e-6
    assert f.piece_weight(2, 300, 2000) == pytest.approx(9.6)
    # a 1250 x 2500 sheet is weighed as 1260 x 2510
    assert f.sheet_weight(5) == pytest.approx(5 * 1260 * 2510 * 8e-6)
    # qty/sheet = floor(1250/(W+5)) x floor(2500/(L+t))
    assert f.qty_per_sheet(5, 100, 150) == math.floor(1250 / 105) * math.floor(2500 / 155)
    assert f.qty_per_sheet(3, 20, 50) == 50 * 47
    # gross = sheet weight / qty
    assert f.gross_weight(5, 100, 150) == pytest.approx(f.sheet_weight(5) / f.qty_per_sheet(5, 100, 150))
    # a hand-counted qty/sheet overrides the formula
    assert f.gross_weight(2, 100, 1900, qty_per_sheet=10) == pytest.approx(f.sheet_weight(2) / 10)


def test_long_part_takes_its_own_strip():
    f = SheetFormula()
    # 60 wide x 9000 long: longer than the sheet, charged a 1260 x (L+20) strip
    w = f.gross_weight(1, 60, 9000)
    per_strip = math.floor(1250 / 65)
    assert w == pytest.approx(1 * 1260 * 9020 * 8e-6 / per_strip)


def test_tube_and_bar_weights():
    f = SheetFormula()
    assert f.square_tube_weight(3, 40, 600) == pytest.approx(3 * 40 * 4 * 600 * 8e-6)
    assert f.rect_tube_weight(2, 12, 25, 1000) == pytest.approx(2 * 74 * 1000 * 8e-6)
    assert f.round_tube_weight(2, 30, 1500) == pytest.approx(math.pi * 30 * 2 * 1500 * 8e-6)
    assert f.bar_weight(20, 100) == pytest.approx(math.pi / 4 * 400 * 100 * 8e-6)


def test_part_bigger_than_the_sheet_width_is_refused():
    with pytest.raises(ValueError):
        SheetFormula().gross_weight(2, 1300, 500)


# ------------------------------------------------------------ lookups

def test_material_lookup_by_grade_shape_and_thickness():
    card = example_rate_card()
    assert card.material("AISI 304", "SHEET", 2).rate_per_kg == 180.0
    assert card.material("SS 304 -2B", "SQ. TUBE", 3).usage_factor == 0.9
    assert card.material("X5CrNi1810", "PLATE", 12).usage_factor == 1.0
    with pytest.raises(RateCardError):
        card.material("SS 316", "SHEET", 2)


def test_narrowest_band_wins_and_plate_falls_back_to_sheet():
    card = RateCard(materials=[
        MaterialRate("SS304", shape="sheet", thk_from=0, thk_to=100, rate_per_kg=100),
        MaterialRate("SS304", shape="sheet", thk_from=1, thk_to=1.5, rate_per_kg=120),
    ])
    assert card.material("SS304", "sheet", 1.25).rate_per_kg == 120
    assert card.material("SS304", "sheet", 3).rate_per_kg == 100
    assert card.material("SS304", "plate", 8).rate_per_kg == 100


def test_process_rates_fixed_and_per_mm():
    card = example_rate_card()
    assert card.process_rate("laser cutting", 4) == pytest.approx(8.0)     # 2 x 4 mm
    assert card.process_rate("LASER CUTTING", 12) == pytest.approx(48.0)   # 4 x 12 mm band
    assert card.process_rate("Bending") == 5.0
    with pytest.raises(ValueError):
        card.process_rate("LASER CUTTING")
    with pytest.raises(RateCardError):
        card.process_rate("ANODISING")


def test_others_and_settings():
    card = example_rate_card()
    assert card.other("E").item == "PACKING & TRANSPORT CHARGES"
    assert card.other("inspection fees").amount == 500.0
    assert card.setting("gst_pct") == 18.0 and card.setting("margin_label") == "MARGIN"
    assert "Example shop" in card.summary()


# ------------------------------------------------------------ storage

def test_xlsx_round_trip(tmp_path):
    pytest.importorskip("openpyxl")
    card = example_rate_card()
    card.settings["margin_pct"] = 15.0
    card.settings["length_gap_is_thickness"] = False
    path = str(tmp_path / "rate_card.xlsx")
    save_rate_card(path, card)
    back = load_rate_card(path)
    assert back.materials == card.materials and back.processes == card.processes
    assert back.others == card.others and back.fasteners == card.fasteners
    assert back.setting("margin_pct") == 15.0 and back.setting("length_gap_is_thickness") is False
    assert back.formula.length_gap_is_thickness is False and back.source == path


def test_hand_edited_card_with_notes_and_blank_rows(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Materials"
    ws.append(["Material", "Shape", "Thk From", "Thk To", "Rate per kg", "Usage Factor", "My comments"])
    ws.append(["SS409M", "sheet", 0, 2.5, "1,23", 0.95, "from supplier"])
    ws.append([None] * 7)
    ws2 = wb.create_sheet("Settings")
    ws2.append(["key", "value"])
    ws2.append(["GST pct", 12])
    path = str(tmp_path / "card.xlsx")
    wb.save(path)
    card = load_rate_card(path)
    (m,) = card.materials
    assert (m.material, m.rate_per_kg, m.usage_factor, m.scrap_per_kg) == ("SS409M", 123.0, 0.95, 0.0)
    assert card.setting("gst_pct") == 12.0 and card.setting("sheet_width") == 1250.0


def test_default_path_is_local_not_the_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHAQUOTE_RATE_CARD", str(tmp_path / "x.xlsx"))
    assert default_rate_card_path() == str(tmp_path / "x.xlsx")
    monkeypatch.delenv("ALPHAQUOTE_RATE_CARD")
    monkeypatch.setenv("ALPHAQUOTE_DB", str(tmp_path / "db" / "quotes.db"))
    assert default_rate_card_path() == str(tmp_path / "db" / "rate_card.xlsx")


def test_gitignore_keeps_rate_cards_out_of_commits():
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("rate_card.xlsx", "elcon_rate_card.xlsx", "examples/rate_card.xlsx"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=root)
        assert r.returncode == 0, f"{name} is not git-ignored"
