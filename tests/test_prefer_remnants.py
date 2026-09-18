"""
tests/test_prefer_remnants.py
-------------------------------
`prefer_remnants` (default True) makes inventory.py always rank a usable
remnant ahead of a full sheet -- not just in `best_stock_for()`'s static
heuristic (which already did this), but also in `joint_stock_optimization`
and `true_joint_stock_optimization`, which rank stock candidates by
sheets/cost/waste and could otherwise silently trade a usable remnant for a
full sheet that merely scores a little better on that objective. These
tests pin that guarantee for all three modes, and confirm
`prefer_remnants=False` cleanly falls back to pure sheets/cost/waste/area
ranking.
"""

import inventory
from nester import Part

PART = Part("Plate", [(0, 0), (100, 0), (100, 100), (0, 100)], quantity=1,
            material="steel", thickness=2.0)


def _stock_with_cheaper_full_sheet():
    """A remnant that fits, plus a full sheet that ALSO fits and comes out
    cheaper overall -- the scenario where a cost-aware ranking (unlike
    best_stock_for()'s pure smallest-area default) could otherwise prefer
    the full sheet. Cost is weight-derived (width x height x thickness x
    density x price_per_kg -- see StockSheet.material_cost()), so the
    remnant's price_per_kg here is set deliberately, unrealistically high
    relative to the full sheet's just to make its much smaller TOTAL
    weight still cost more overall -- the point is exercising the ranking
    logic, not modeling real material prices."""
    return [
        inventory.StockSheet(material="steel", thickness=2.0, width=200, height=200,
                              quantity=1, is_remnant=True, id="R-1",
                              price_per_kg=1000.0, density_g_cm3=7.85),
        inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                              quantity=None, is_remnant=False, id="full",
                              price_per_kg=1.0, density_g_cm3=7.85),
    ]


def _stock_used_ids(results):
    return {s.id for r in results for s in r.sheet_stock}


def test_best_stock_for_prefers_remnant_over_smaller_area_full_sheet():
    """Isolates the remnant-status tiebreak from the smallest-area one: the
    remnant here is LARGER by area than the full sheet, so only
    prefer_remnants (not "just pick the smaller one") explains picking it."""
    stock = [
        inventory.StockSheet(material="steel", thickness=2.0, width=1000, height=1000,
                              quantity=1, is_remnant=True, id="R-big"),
        inventory.StockSheet(material="steel", thickness=2.0, width=300, height=300,
                              quantity=None, is_remnant=False, id="full-small"),
    ]
    picked = inventory.best_stock_for("steel", 2.0, stock, 100, 100, prefer_remnants=True)
    assert picked.id == "R-big"

    picked = inventory.best_stock_for("steel", 2.0, stock, 100, 100, prefer_remnants=False)
    assert picked.id == "full-small"


def test_run_job_default_cascade_respects_prefer_remnants():
    for prefer, expected in ((True, {"R-1"}), (False, {"R-1"})):
        # Both settings pick the remnant here since it's also the smaller
        # candidate by area -- see the joint-optimization variants below
        # for the case that actually discriminates on cost.
        results = inventory.run_job([PART], _stock_with_cheaper_full_sheet(), kerf=0.0,
                                     prefer_remnants=prefer)
        assert _stock_used_ids(results) == expected


def test_joint_stock_optimization_respects_prefer_remnants():
    results = inventory.run_job([PART], _stock_with_cheaper_full_sheet(), kerf=0.0,
                                 joint_stock_optimization=True, prefer_remnants=True)
    assert _stock_used_ids(results) == {"R-1"}

    results = inventory.run_job([PART], _stock_with_cheaper_full_sheet(), kerf=0.0,
                                 joint_stock_optimization=True, prefer_remnants=False)
    assert _stock_used_ids(results) == {"full"}


def test_true_joint_stock_optimization_respects_prefer_remnants():
    results = inventory.run_job([PART], _stock_with_cheaper_full_sheet(), kerf=0.0,
                                 true_joint_stock_optimization=True, prefer_remnants=True)
    assert _stock_used_ids(results) == {"R-1"}

    results = inventory.run_job([PART], _stock_with_cheaper_full_sheet(), kerf=0.0,
                                 true_joint_stock_optimization=True, prefer_remnants=False)
    assert _stock_used_ids(results) == {"full"}
