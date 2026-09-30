"""Phase 3: the quote engine -- material from real nests, allocation,
remnants, quantity breaks, markups and overrides."""

import math

import pytest

from bar_nest import BarStock
from inventory import StockSheet
from quoting.costing import MATERIAL, QUOTE, CostingSettings, QuoteEngine
from quoting.features import Bend, PartFeatures, ProfileInfo, features_from_polygons
from quoting.operations import painting
from quoting.ops_assembly import QuoteCost
from quoting.ops_weld import WeldJoint
from quoting.rates import (AssemblyRate, BendRate, CuttingRate, RateTable, SawRate, WeldRate,
                           starter_rates)

DENSITY = 7.85
PRICE_KG = 1.20
SCRAP_KG = 0.20


def rates():
    return RateTable([
        CuttingRate("mild steel", 3.0, 3000.0, 1.2, 120.0, 6.0, True),
        BendRate("*", 3.0, 12.0, 20.0, 10.0, 60.0, True),
        WeldRate("fillet", 3.0, 10.0, 2.0, 30.0, 15.0, 60.0, True),
        SawRate(120.0, 60.0, 5.0, 60.0, True),
        AssemblyRate("*", 10.0, 2.0, 0.5, 60.0, True),
    ])


def sheet_stock(w=1000.0, h=500.0, **kw):
    kw.setdefault("price_per_kg", PRICE_KG)
    kw.setdefault("density_g_cm3", DENSITY)
    kw.setdefault("scrap_price_per_kg", SCRAP_KG)
    return StockSheet("mild steel", 3.0, w, h, **kw)


def kg(area_mm2, t=3.0):
    return area_mm2 * t * DENSITY / 1e6


def circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def plate(name="plate", w=100, h=50, holes=(), quantity=1, bends=(), **kw):
    return features_from_polygons(name, rect(w, h), list(holes), material="mild steel", thickness=3.0,
                                  quantity=quantity, bends=list(bends), **kw)


# ------------------------------------------------------------ one part

def test_price_one_part_at_one_quantity_estimate_mode():
    part = plate(holes=[circle(25, 25, 5), circle(75, 25, 5)])
    engine = QuoteEngine(rates(), [sheet_stock()],
                         settings=CostingSettings(material_mode="estimate", estimate_utilisation=0.8))
    line = engine.price_part(part, qty=1)
    consumed = part.net_area / 0.8
    expected_mat = kg(consumed) * PRICE_KG - kg(consumed - part.net_area) * SCRAP_KG
    assert line.cells[MATERIAL].value == pytest.approx(expected_mat)
    run = part.cut_length / 3000 + 3 * 1.2 / 60
    assert line.cells["laser"].value == pytest.approx((6.0 + run) * 2.0)
    assert set(line.cells) == {MATERIAL, "laser"}
    assert line.unit_cost == pytest.approx(expected_mat + (6.0 + run) * 2.0)
    assert line.complete and not line.estimate


def test_nested_single_sheet_whole_sheet_minus_scrap():
    part = plate()
    stock = [sheet_stock(quantity=5)]
    line = QuoteEngine(rates(), stock).price_part(part, qty=1)
    sheet_kg = kg(1000 * 500)
    expected = sheet_kg * PRICE_KG - kg(1000 * 500 - 5000) * SCRAP_KG
    assert line.cells[MATERIAL].value == pytest.approx(expected)
    assert stock[0].quantity == 5                     # quoting never consumes inventory


# ------------------------------------------------------ allocation

def two_part_job(allocation):
    holed = plate("holed", holes=[circle(50, 25, 15)], quantity=2)
    solid = plate("solid", quantity=3)
    engine = QuoteEngine(rates(), [sheet_stock()], settings=CostingSettings(allocation=allocation))
    return holed, solid, engine.price([holed, solid], breaks=(1,)).breaks[0]


def test_net_area_allocation_splits_the_group_total():
    holed, solid, br = two_part_job("net_area")
    total = kg(500000) * PRICE_KG - kg(500000 - 2 * holed.net_area - 3 * solid.net_area) * SCRAP_KG
    h, s = br.line("holed").cells[MATERIAL].value, br.line("solid").cells[MATERIAL].value
    assert 2 * h + 3 * s == pytest.approx(total)
    assert h / s == pytest.approx(holed.net_area / solid.net_area)


def test_bbox_allocation_ignores_holes():
    _, _, br = two_part_job("bbox")
    assert br.line("holed").cells[MATERIAL].value == pytest.approx(br.line("solid").cells[MATERIAL].value)


# ------------------------------------------------------ quantity breaks

def test_quantity_breaks_amortise_setup_and_renest():
    part = plate(bends=[Bend(90, 50, 3)])
    engine = QuoteEngine(rates(), [sheet_stock()])
    res = engine.price([part], breaks=(100, 1, 10))
    assert [b.quantity for b in res.breaks] == [1, 10, 100]
    run = part.cut_length / 3000 + 1 * 1.2 / 60          # one pierce: no holes
    for br in res.breaks:
        q = br.quantity
        line = br.lines[0]
        assert line.quantity == q
        assert line.cells["laser"].value == pytest.approx((6.0 / q + run) * 2.0)
        assert line.cells["bending"].value == pytest.approx(20.0 / q + 12 / 60)
    unit = [b.lines[0].unit_price for b in res.breaks]
    assert unit[0] > unit[1] > unit[2]
    # 100 parts of 100x50 need a real re-nest onto more than one 1000x500 sheet.
    material_100 = res.at(100).lines[0].cells[MATERIAL].value
    assert material_100 < res.at(1).lines[0].cells[MATERIAL].value


def test_quantity_per_set_multiplies():
    part = plate(quantity=4)
    br = QuoteEngine(rates(), [sheet_stock()]).price([part], breaks=(5,)).breaks[0]
    assert br.lines[0].quantity == 20
    assert br.set_price == pytest.approx(br.total_price / 5)


# ---------------------------------------------------------- remnants

@pytest.mark.parametrize("factor", [1.0, 0.5, 0.0])
def test_remnant_price_factor(factor):
    rem = sheet_stock(400, 300, quantity=1, is_remnant=True, scrap_price_per_kg=None)
    engine = QuoteEngine(rates(), [rem], settings=CostingSettings(remnant_price_factor=factor))
    line = engine.price_part(plate(), 1)
    assert line.cells[MATERIAL].value == pytest.approx(kg(400 * 300) * PRICE_KG * factor)


# ------------------------------------------------ incomplete / notes

def test_unpriced_stock_leaves_material_empty():
    stock = [StockSheet("mild steel", 3.0, 1000, 500)]
    line = QuoteEngine(rates(), stock).price_part(plate(), 1)
    assert line.cells[MATERIAL].value is None
    assert not line.complete
    assert any("no price" in n for n in line.notes)


def test_part_too_big_for_stock_is_noted():
    big = plate("big", 1200, 50)
    line = QuoteEngine(rates(), [sheet_stock()]).price_part(big, 1)
    assert line.cells[MATERIAL].value is None
    assert any("didn't nest" in n for n in line.notes)


def test_missing_rate_leaves_cell_empty_with_note():
    thick = features_from_polygons("thick", rect(100, 50), material="mild steel", thickness=20.0, bends=[])
    line = QuoteEngine(rates(), [], settings=CostingSettings(material_mode="estimate")).price_part(thick, 1)
    assert line.cells["laser"].value is None
    assert any("laser" in n and "outside the table" in n for n in line.notes)


def test_unknown_bends_are_flagged():
    part = features_from_polygons("dxf-part", rect(100, 50), material="mild steel", thickness=3.0)
    line = QuoteEngine(rates(), [sheet_stock()]).price_part(part, 1)
    assert "bending" not in line.cells
    assert any("bend count unknown" in n for n in line.notes)


def test_starter_rates_raise_a_calibration_note():
    res = QuoteEngine(starter_rates(), [sheet_stock()]).price([plate()])
    assert any("calibrate me" in n for n in res.notes)
    assert not QuoteEngine(rates(), [sheet_stock()]).price([plate()]).notes


# ------------------------------------------------ markup and overrides

def test_markup_per_cost_area():
    settings = CostingSettings(markups={"material": 0.10, "cutting": 0.50}, default_markup=0.2)
    line = QuoteEngine(rates(), [sheet_stock()], settings=settings).price_part(plate(bends=[Bend()]), 1)
    assert line.cells[MATERIAL].price == pytest.approx(line.cells[MATERIAL].value * 1.10)
    assert line.cells["laser"].price == pytest.approx(line.cells["laser"].value * 1.50)
    assert line.cells["bending"].price == pytest.approx(line.cells["bending"].value * 1.20)


def test_overrides_keep_the_calculated_value():
    part = plate()
    engine = QuoteEngine(rates(), [sheet_stock()])
    res = engine.price([part], breaks=(1, 10), overrides={
        ("plate", "laser"): 3.0,             # every break
        (10, "plate", MATERIAL): 0.5,        # only the 10-off break
    })
    one, ten = res.at(1).lines[0], res.at(10).lines[0]
    assert one.cells["laser"].value == 3.0 and one.cells["laser"].overridden
    assert one.cells["laser"].calculated != 3.0
    assert not one.cells[MATERIAL].overridden
    assert ten.cells[MATERIAL].value == 0.5 and ten.cells[MATERIAL].calculated > 0


# ------------------------------------------------------------ tube

def rhs(name, length, quantity=1, end_cuts=("mitre", "mitre")):
    prof = ProfileInfo("RHS", 100, 50, 3, area=864.0, perimeter=300.0)
    return PartFeatures(name, part_type="tube", material="Mild Steel", profile=prof, length=length,
                        quantity=quantity, end_cuts=end_cuts)


def test_tube_material_from_bar_nest_split_by_length():
    bars = [BarStock("mild steel", "RHS 100x50x3", 6000, price_per_m=10.0, kg_per_m=6.78,
                     scrap_price_per_kg=0.2)]
    long_, short = rhs("long", 1800, 2), rhs("short", 1150, 2)
    engine = QuoteEngine(rates(), bar_stock=bars, settings=CostingSettings(bar_kerf=3.0))
    br = engine.price([long_, short], breaks=(1,)).breaks[0]
    # One 6 m bar holds 2x1800 + 2x1150 + 4 kerf = 5912; offcut 88 mm is scrap.
    total = 60.0 - 0.088 * 6.78 * 0.2
    lc, sc = br.line("long").cells[MATERIAL].value, br.line("short").cells[MATERIAL].value
    assert 2 * lc + 2 * sc == pytest.approx(total)
    assert lc / sc == pytest.approx(1800 / 1150)
    saw = br.line("long").cells["tube cutting"].value
    assert saw == pytest.approx(5.0 / 2 + 3.0 * 60 / 60)          # setup / 2 + mitre ends


def test_bar_charge_used_leaves_remnant_uncharged():
    bars = [BarStock("mild steel", "RHS 100x50x3", 6000, price_per_m=10.0)]
    settings = CostingSettings(bar_kerf=0.0, bar_min_remnant=1000, bar_charge="used")
    line = QuoteEngine(rates(), bar_stock=bars, settings=settings).price_part(rhs("r", 2000), 1)
    assert line.cells[MATERIAL].value == pytest.approx(20.0)
    full = QuoteEngine(rates(), bar_stock=bars,
                       settings=CostingSettings(bar_kerf=0.0, bar_min_remnant=1000)).price_part(rhs("r", 2000), 1)
    assert full.cells[MATERIAL].value == pytest.approx(60.0)


def test_tube_without_matching_stock():
    line = QuoteEngine(rates()).price_part(rhs("r", 2000), 1)
    assert line.cells[MATERIAL].value is None
    assert any("didn't fit" in n for n in line.notes)


# ------------------------------------------------ a mixed quote

def welded_frame_quote():
    base = plate("base", 300, 200, holes=[circle(30, 30, 5), circle(270, 30, 5)], bends=[])
    base.extra["paint"] = True
    rail = rhs("rail", 1200, 2)
    bolt = PartFeatures("M8 bolt", part_type="purchased", unit_cost=0.15, quantity=4, markup=1.0)
    frame = PartFeatures("frame assy", part_type="assembly", thickness=3.0, part_count=3, fastener_count=4,
                         weld_joints=[WeldJoint("base", "rail", 400.0), WeldJoint("base", "rail#2", 400.0)])
    return [bolt, frame, rail, base]


def mixed_engine():
    from quoting.operations import default_operations
    r = rates()
    ops = default_operations(r) + [painting(15.0, setup=25.0)]
    bars = [BarStock("mild steel", "RHS 100x50x3", 6000, price_per_m=10.0)]
    return QuoteEngine(r, [sheet_stock()], bars, operations=ops,
                       settings=CostingSettings(markups={"material": 0.1}, default_markup=0.25),
                       quote_costs=[QuoteCost("transport", 80.0), QuoteCost("packing", 0.0, per_set=2.0)])


def test_mixed_quote_groups_and_totals():
    res = mixed_engine().price(welded_frame_quote(), breaks=(1, 10))
    br = res.at(10)
    assert list(br.by_type()) == ["sheet", "tube", "purchased", "assembly"]
    assert [l.name for l in br.lines] == ["base", "rail", "M8 bolt", "frame assy"]
    assert br.complete
    assert set(br.line("base").cells) == {MATERIAL, "laser", "painting"}
    assert set(br.line("frame assy").cells) == {"welding", "assembly"}
    assert br.line("M8 bolt").cells["bought-in"].price == pytest.approx(0.30)
    assert br.quote_cells["packing"].value == pytest.approx(20.0)
    lines_total = sum(l.total_price for l in br.lines)
    assert br.total_price == pytest.approx(lines_total + (80.0 + 20.0) * 1.25)
    areas = br.by_cost_area()
    assert set(areas) >= {"material", "cutting", "finishing", "purchased", "welding", "assembly", "quote"}
    assert sum(a["price"] for a in areas.values()) == pytest.approx(br.total_price)
    assert res.at(10).set_price < res.at(1).set_price


def test_quote_cost_override():
    res = mixed_engine().price(welded_frame_quote(), breaks=(1,),
                               overrides={(QUOTE, "transport"): 0.0})
    cell = res.at(1).quote_cells["transport"]
    assert cell.value == 0.0 and cell.calculated == 80.0


def test_input_validation():
    engine = QuoteEngine(rates(), [sheet_stock()])
    with pytest.raises(ValueError, match="unique"):
        engine.price([plate("a"), plate("a")])
    with pytest.raises(ValueError):
        engine.price([plate()], breaks=(0,))
    with pytest.raises(ValueError):
        CostingSettings(allocation="weight")
    with pytest.raises(ValueError):
        CostingSettings(estimate_utilisation=0)
