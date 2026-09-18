"""
tests/test_stock_pricing.py
-----------------------------
Sheet metal is priced by weight, not by the sheet -- StockSheet.weight_kg()/
material_cost() derive cost from width/height/thickness/density_g_cm3/
price_per_kg instead of a flat per-sheet price (see inventory.py's
StockSheet docstring and README's "Multi-material assemblies" section).
These tests pin the weight/cost formula itself, its graceful "None when
unpriced" degradation, and that a captured remnant inherits its parent
stock's rate (so a later job can value "money saved" by reusing it).
"""

import inventory
from nester import Part

MM3_TO_KG_FACTOR = 1e-6  # width(mm) * height(mm) * thickness(mm) * density(g/cm^3) / 1e6 = kg


def test_weight_kg_matches_hand_computed_formula():
    stock = inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                                  density_g_cm3=7.85)
    expected = 1220 * 2440 * 2.0 * 7.85 * MM3_TO_KG_FACTOR
    assert stock.weight_kg() == expected


def test_weight_kg_none_without_density():
    stock = inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440)
    assert stock.weight_kg() is None


def test_material_cost_none_without_density_or_price():
    priced_no_density = inventory.StockSheet(material="steel", thickness=2.0, width=100, height=100,
                                              price_per_kg=2.0)
    densitied_no_price = inventory.StockSheet(material="steel", thickness=2.0, width=100, height=100,
                                               density_g_cm3=7.85)
    assert priced_no_density.material_cost() is None
    assert densitied_no_price.material_cost() is None


def test_material_cost_is_weight_times_price_per_kg():
    stock = inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                                  density_g_cm3=7.85, price_per_kg=2.0)
    assert stock.material_cost() == stock.weight_kg() * 2.0


def test_joint_stock_optimization_ranks_by_weight_derived_cost():
    """A thinner/smaller full sheet that's cheaper in TOTAL material cost
    (not just cheaper per kg) should win over a pricier one when both fit
    -- exercises _stock_objective()'s weight-derived cost end to end, not
    just the StockSheet formula in isolation."""
    part = Part("Plate", [(0, 0), (100, 0), (100, 100), (0, 100)], quantity=1,
                material="steel", thickness=2.0)
    stock = [
        inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                              quantity=None, is_remnant=False, id="cheap",
                              price_per_kg=1.0, density_g_cm3=7.85),
        inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                              quantity=None, is_remnant=False, id="pricey",
                              price_per_kg=5.0, density_g_cm3=7.85),
    ]
    results = inventory.run_job([part], stock, kerf=0.0, joint_stock_optimization=True)
    used_ids = {s.id for r in results for s in r.sheet_stock}
    assert used_ids == {"cheap"}


def test_captured_remnant_inherits_parent_price_density_and_scrap_price():
    parent = inventory.StockSheet(material="steel", thickness=2.0, width=1220, height=2440,
                                   quantity=1, is_remnant=False, id="parent",
                                   price_per_kg=2.0, density_g_cm3=7.85, scrap_price_per_kg=0.5)
    part = Part("Plate", [(0, 0), (100, 0), (100, 100), (0, 100)], quantity=1,
                material="steel", thickness=2.0)

    import tempfile
    import os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        inventory.save_inventory(path, [parent])
        inventory.commit_job([part], path, kerf=0.0, capture_remnants=True, min_remnant_dimension=50.0)
        stock_after = inventory.load_inventory(path)
        captured = [s for s in stock_after if s.is_remnant]
        assert len(captured) == 1
        assert captured[0].price_per_kg == 2.0
        assert captured[0].density_g_cm3 == 7.85
        assert captured[0].scrap_price_per_kg == 0.5
    finally:
        os.unlink(path)
        log_path = path + ".log.jsonl"
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_scrap_price_per_kg_is_a_plain_per_entry_field():
    """No global/blended rate anywhere in inventory.py -- two entries of
    different materials can carry genuinely different scrap rates."""
    steel = inventory.StockSheet(material="steel", thickness=2.0, width=100, height=100,
                                  scrap_price_per_kg=0.35)
    aluminum = inventory.StockSheet(material="aluminum", thickness=2.0, width=100, height=100,
                                     scrap_price_per_kg=1.40)
    unpriced = inventory.StockSheet(material="brass", thickness=2.0, width=100, height=100)
    assert steel.scrap_price_per_kg == 0.35
    assert aluminum.scrap_price_per_kg == 1.40
    assert unpriced.scrap_price_per_kg is None
