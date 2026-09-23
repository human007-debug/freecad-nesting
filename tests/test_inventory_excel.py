"""
tests/test_inventory_excel.py
------------------------------
The stock list is a table of sheets a shop owns, maintained by whoever owns
that stock -- so it lives in an Excel workbook, not a JSON file. These tests
pin the two halves of that:

  * the workbook is a real round trip -- every StockSheet field survives it,
    including the blanks that mean "unlimited" and "unpriced";
  * the reader is forgiving about what a HUMAN hands back, because the whole
    point of a spreadsheet is that people edit it: reordered columns, extra
    columns of their own, a title row above the table, TRUE/blank/`Y` in the
    Remnant column, `1,220` typed with a thousands separator.

JSON is still read and written for a .json path (old inventories keep
working); `convert_inventory()` moves one over.
"""

import json
import os

import pytest

import inventory

openpyxl = pytest.importorskip("openpyxl")


def _sheets():
    return [
        inventory.StockSheet(material="Mild Steel", thickness=2.0, width=1220.0, height=2440.0,
                              quantity=4, is_remnant=False, id=None,
                              price_per_kg=1.25, density_g_cm3=7.85, scrap_price_per_kg=0.3),
        # The awkward one: unlimited quantity, no id, nothing priced.
        inventory.StockSheet(material="Stainless 304", thickness=1.5, width=1220.0, height=2440.0,
                              quantity=None, is_remnant=False),
        inventory.StockSheet(material="Aluminum 5052", thickness=3.0, width=600.0, height=400.0,
                              quantity=1, is_remnant=True, id="R-001",
                              price_per_kg=4.0, density_g_cm3=2.68, scrap_price_per_kg=1.4),
    ]


def test_xlsx_round_trip_preserves_every_field(tmp_path):
    path = tmp_path / "stock.xlsx"
    inventory.save_inventory(path, _sheets())
    assert [vars(s) for s in inventory.load_inventory(path)] == [vars(s) for s in _sheets()]


def test_blank_cells_mean_unlimited_and_unpriced(tmp_path):
    """The one distinction a spreadsheet could plausibly lose: a blank
    Quantity is "unlimited" (None), NOT zero -- and zero is a real, very
    different answer ("none on hand")."""
    path = tmp_path / "stock.xlsx"
    inventory.save_inventory(path, [
        inventory.StockSheet(material="Steel", thickness=2.0, width=100.0, height=100.0,
                              quantity=None),
        inventory.StockSheet(material="Steel", thickness=2.0, width=100.0, height=100.0,
                              quantity=0),
    ])
    unlimited, none_left = inventory.load_inventory(path)
    assert unlimited.quantity is None
    assert none_left.quantity == 0
    assert unlimited.price_per_kg is None and unlimited.density_g_cm3 is None
    assert unlimited.scrap_price_per_kg is None


def _write_sheet(path, rows, title="Stock"):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = title
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def test_reads_a_hand_made_sheet_with_reordered_and_extra_columns(tmp_path):
    """What a shop's own spreadsheet actually looks like: their columns, in
    their order, with headings shortened to taste."""
    path = tmp_path / "our_stock.xlsx"
    _write_sheet(path, [
        ["Supplier", "Material", "Width", "Height", "Thickness", "Qty", "Bin"],
        ["Acme Metals", "Mild Steel", 1220, 2440, 2, 7, "A-14"],
        ["Acme Metals", "Stainless 304", 1000, 2000, 1.5, None, "B-02"],
    ])
    steel, stainless = inventory.load_inventory(path)
    assert (steel.material, steel.width, steel.height, steel.thickness) == \
        ("Mild Steel", 1220.0, 2440.0, 2.0)
    assert steel.quantity == 7
    assert stainless.quantity is None  # blank Qty is still "unlimited"
    assert steel.is_remnant is False   # no Remnant column at all


def test_reads_past_a_title_row_and_blank_spacer_rows(tmp_path):
    path = tmp_path / "titled.xlsx"
    _write_sheet(path, [
        ["Workshop stock list -- updated Monday"],
        [],
        ["ID", "Material", "Thickness (mm)", "Width (mm)", "Height (mm)", "Quantity"],
        ["S-1", "Mild Steel", 3, 1220, 2440, 2],
        [],
        ["S-2", "Mild Steel", 5, 1220, 2440, 1],
        [None, None, None, None, None, None],
    ])
    stock = inventory.load_inventory(path)
    assert [s.id for s in stock] == ["S-1", "S-2"]


@pytest.mark.parametrize("cell,expected", [
    ("Yes", True), ("yes", True), ("Y", True), ("TRUE", True), (True, True), (1, True),
    ("No", False), ("n", False), ("FALSE", False), (False, False), (0, False), (None, False),
])
def test_remnant_column_accepts_what_people_actually_type(tmp_path, cell, expected):
    path = tmp_path / f"remnant_{str(cell).lower()}.xlsx"
    _write_sheet(path, [
        ["Material", "Thickness (mm)", "Width (mm)", "Height (mm)", "Remnant"],
        ["Mild Steel", 2, 300, 450, cell],
    ])
    assert inventory.load_inventory(path)[0].is_remnant is expected


def test_thousands_separators_in_a_typed_dimension(tmp_path):
    path = tmp_path / "typed.xlsx"
    _write_sheet(path, [
        ["Material", "Thickness (mm)", "Width (mm)", "Height (mm)"],
        ["Mild Steel", "2", "1,220", "2,440"],
    ])
    sheet = inventory.load_inventory(path)[0]
    assert (sheet.width, sheet.height) == (1220.0, 2440.0)


def test_a_bad_cell_names_the_row_and_column(tmp_path):
    path = tmp_path / "bad.xlsx"
    _write_sheet(path, [
        ["Material", "Thickness (mm)", "Width (mm)", "Height (mm)"],
        ["Mild Steel", 2, 1220, 2440],
        ["Mild Steel", 2, "about a metre", 2440],
    ])
    with pytest.raises(ValueError) as excinfo:
        inventory.load_inventory(path)
    message = str(excinfo.value)
    assert "row 3" in message and "Width (mm)" in message


def test_a_workbook_with_no_stock_table_is_rejected_clearly(tmp_path):
    path = tmp_path / "not_stock.xlsx"
    _write_sheet(path, [["Invoice"], ["Total", 1234]])
    with pytest.raises(ValueError, match="no stock table"):
        inventory.load_inventory(path)


def test_rewriting_keeps_the_shops_own_other_sheets(tmp_path):
    """Every committed job rewrites the inventory file (commit_job) -- that
    must not eat the notes/pricing tab someone keeps beside the stock."""
    path = tmp_path / "stock.xlsx"
    inventory.save_inventory(path, _sheets())
    workbook = openpyxl.load_workbook(path)
    notes = workbook.create_sheet("Supplier notes")
    notes["A1"] = "Acme quote expires in March"
    workbook.save(path)

    inventory.save_inventory(path, _sheets()[:1])

    workbook = openpyxl.load_workbook(path)
    assert "Supplier notes" in workbook.sheetnames
    assert workbook["Supplier notes"]["A1"].value == "Acme quote expires in March"
    assert len(inventory.load_inventory(path)) == 1


def test_the_stock_sheet_is_the_one_read_when_there_are_several(tmp_path):
    path = tmp_path / "stock.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.title = "Read me first"
    workbook.active["A1"] = "The stock is on the next tab"
    worksheet = workbook.create_sheet(inventory.STOCK_SHEET_NAME)
    worksheet.append(["Material", "Thickness (mm)", "Width (mm)", "Height (mm)"])
    worksheet.append(["Mild Steel", 2, 1220, 2440])
    workbook.save(path)
    assert inventory.load_inventory(path)[0].material == "Mild Steel"


def test_format_follows_the_extension(tmp_path):
    """A .json path still gets JSON -- committing a job against a legacy
    inventory must not silently turn the file into a workbook."""
    json_path = tmp_path / "legacy.json"
    inventory.save_inventory(json_path, _sheets())
    with open(json_path) as f:
        assert len(json.load(f)["stock"]) == 3

    xlsx_path = tmp_path / "current.xlsx"
    inventory.save_inventory(xlsx_path, _sheets())
    with open(xlsx_path, "rb") as f:
        assert f.read(2) == b"PK"  # a zip container, i.e. a real workbook


def test_convert_inventory_moves_a_legacy_json_to_excel(tmp_path):
    json_path = tmp_path / "my_inventory.json"
    inventory.save_inventory(json_path, _sheets())
    dest = inventory.convert_inventory(json_path)
    assert os.path.splitext(dest)[1] == ".xlsx"
    assert [vars(s) for s in inventory.load_inventory(dest)] == [vars(s) for s in _sheets()]


def test_commit_job_writes_back_to_the_workbook(tmp_path):
    """The full loop: a job committed against an .xlsx inventory decrements
    quantities and records its captured remnant IN that workbook."""
    from nester import Part

    path = tmp_path / "stock.xlsx"
    inventory.save_inventory(path, [
        inventory.StockSheet(material="steel", thickness=2.0, width=1220.0, height=2440.0,
                              quantity=3, id="full", price_per_kg=2.0, density_g_cm3=7.85),
    ])
    part = Part("Plate", [(0, 0), (100, 0), (100, 100), (0, 100)], quantity=1,
                material="steel", thickness=2.0)
    inventory.commit_job([part], str(path), kerf=0.0, capture_remnants=True,
                         min_remnant_dimension=50.0)

    stock = inventory.load_inventory(path)
    full = [s for s in stock if s.id == "full"][0]
    assert full.quantity == 2
    assert [s.is_remnant for s in stock].count(True) == 1
