"""
quote_demo.py
-------------
Example: prices a small welded frame -- a laser-cut, bent, painted base
plate, two RHS rails, four bought-in bolts and the welded assembly -- at
1, 10 and 100 sets, and prints the cost breakdown. The quoting module has
no UI yet (Phase 4 of docs/QUOTING_PLAN.md); this is the quickest way to
see it work end to end.

    python3 quote_demo.py

Uses the starter rates (every row "calibrate me"), so the numbers show
how the engine behaves, not what a job really costs.
"""

import math

from bar_nest import BarStock
from inventory import StockSheet
from quoting.costing import CostingSettings, QuoteEngine
from quoting.features import Bend, HoleFeature, PartFeatures, ProfileInfo, features_from_polygons
from quoting.operations import default_operations, painting
from quoting.ops_assembly import QuoteCost
from quoting.ops_weld import WeldJoint
from quoting.rates import starter_rates


def circle(cx, cy, r, n=48):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def build_parts():
    base = features_from_polygons(
        "Base plate", [(0, 0), (300, 0), (300, 200), (0, 200)],
        [circle(30, 30, 5), circle(270, 30, 5), circle(30, 170, 5), circle(270, 170, 5)],
        material="mild steel", thickness=3.0,
        bends=[Bend(90, 300, 3), Bend(90, 300, 3)],
        hole_features=[HoleFeature(6.8, "tapped", "M8", count=2)],
    )
    base.extra["paint"] = True
    rail = PartFeatures("RHS rail", part_type="tube", material="mild steel", length=1200.0, quantity=2,
                        profile=ProfileInfo("RHS", 100, 50, 3, area=864.0, perimeter=300.0),
                        end_cuts=("mitre", "mitre"))
    bolt = PartFeatures("M8x25 bolt", part_type="purchased", unit_cost=0.15, quantity=4,
                        supplier="Bolts Ltd", markup=1.0)
    frame = PartFeatures("Frame assembly", part_type="assembly", thickness=3.0, part_count=3,
                         fastener_count=4,
                         weld_joints=[WeldJoint("Base plate", "RHS rail", 400.0),
                                      WeldJoint("Base plate", "RHS rail #2", 400.0)])
    return [base, rail, bolt, frame]


def build_engine():
    rates = starter_rates()
    sheets = [StockSheet("mild steel", 3.0, 2500, 1250, price_per_kg=1.20, density_g_cm3=7.85,
                         scrap_price_per_kg=0.20)]
    bars = [BarStock("mild steel", "RHS 100x50x3", 6000, price_per_m=11.0, kg_per_m=6.78,
                     scrap_price_per_kg=0.20)]
    settings = CostingSettings(markups={"material": 0.10, "purchased": 0.50}, default_markup=0.25,
                               kerf=0.2, bar_kerf=3.0)
    return QuoteEngine(rates, sheets, bars, operations=default_operations(rates) + [painting(15.0, setup=25.0)],
                       settings=settings, quote_costs=[QuoteCost("Transport", 80.0)])


def fmt(v):
    return "      -" if v is None else f"{v:8.2f}"


def main():
    parts = build_parts()
    result = build_engine().price(parts, breaks=(1, 10, 100))
    for note in result.notes:
        print(f"NOTE: {note}")
    for br in result.breaks:
        print(f"\n=== {br.quantity} set(s) ===")
        for part_type, lines in br.by_type().items():
            print(f"  [{part_type}]")
            for line in lines:
                cells = ", ".join(f"{k} {fmt(c.value).strip()}" for k, c in line.cells.items())
                print(f"    {line.name:<16} x{line.quantity:<4} unit cost {fmt(line.unit_cost)}  "
                      f"unit price {fmt(line.unit_price)}   ({cells})")
                for note in line.notes:
                    print(f"      note: {note}")
        for name, cell in br.quote_cells.items():
            print(f"  [quote] {name:<16} {fmt(cell.value)}")
        print("  by cost area: " + ", ".join(f"{a} {v['price']:.2f}" for a, v in br.by_cost_area().items()))
        print(f"  TOTAL cost {br.total_cost:.2f}   price {br.total_price:.2f}   "
              f"per set {br.set_price:.2f}" + ("" if br.complete else "   (INCOMPLETE -- see notes)"))


if __name__ == "__main__":
    main()
