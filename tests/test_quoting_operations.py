"""Phase 2 / 2b: each operation's time and cost against hand-worked
numbers, using a small calibrated rate table so the arithmetic is exact."""

import math

import pytest

from quoting.features import Bend, HoleFeature, PartFeatures, Pocket, ProfileInfo, features_from_polygons
from quoting.operations import Bending, LaserCutting, RuleOperation, default_operations, painting
from quoting.ops_assembly import AssemblyLabour, BoughtIn, QuoteCost
from quoting.ops_machining import Machining
from quoting.ops_tube import TubeCutting
from quoting.ops_weld import WeldJoint, Welding, joints_from_contacts
from quoting.rates import (AssemblyRate, BendRate, CuttingRate, MachiningRate, RateTable, SawRate,
                           TubeLaserRate, WeldRate)


def rates():
    return RateTable([
        CuttingRate("mild steel", 3.0, 3000.0, 1.2, 120.0, 6.0, True),
        BendRate("*", 3.0, 12.0, 20.0, 10.0, 60.0, True),
        WeldRate("fillet", 3.0, 10.0, 2.0, 30.0, 15.0, 60.0, True),
        WeldRate("butt", 3.0, 20.0, 4.0, 30.0, 15.0, 60.0, True),
        SawRate(120.0, 60.0, 5.0, 60.0, True),
        TubeLaserRate("mild steel", 3.0, 3000.0, 1.2, 30.0, 12.0, 120.0, True),
        MachiningRate("tapped", 12.0, 30.0, 0.0, 20.0, 60.0, True),
        MachiningRate("plain", 12.0, 15.0, 0.0, 20.0, 60.0, True),
        MachiningRate("countersink", 25.0, 20.0, 0.0, 20.0, 60.0, True),
        MachiningRate("pocket", 1e9, 0.0, 10.0, 20.0, 60.0, True),
        AssemblyRate("*", 10.0, 2.0, 0.5, 60.0, True),
    ])


def circle(cx, cy, r, n=64):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def sheet(bends=None, **kw):
    return features_from_polygons("bracket", [(0, 0), (200, 0), (200, 100), (0, 100)],
                                  [circle(50, 50, 5), circle(150, 50, 5)],
                                  material="mild steel", thickness=3.0, bends=bends, **kw)


# --------------------------------------------------------------- laser

def test_laser_time_formula():
    part = sheet()
    op = LaserCutting(rates())
    cut = 600 + 2 * math.pi * 10
    expected_run = cut / 3000.0 + 3 * 1.2 / 60.0            # minutes
    assert op.run_time(part) == pytest.approx(expected_run)
    assert op.setup_time(part) == 6.0
    assert op.time(part, 10) == pytest.approx(6.0 + 10 * expected_run)
    oc = op.cost(part, 10)
    assert oc.setup_cost == pytest.approx(6.0 * 2.0)        # 120/hr = 2/min
    assert oc.unit_cost == pytest.approx(expected_run * 2.0)
    assert oc.per_unit(10) == pytest.approx(1.2 + expected_run * 2.0)
    assert oc.cost_area == "cutting"


def test_laser_skips_non_sheet_parts():
    assert not LaserCutting(rates()).applies_to(PartFeatures("b", part_type="machined"))


# --------------------------------------------------------------- bending

def test_bend_time_setup_plus_hits():
    part = sheet(bends=[Bend(90, 100, 3), Bend(90, 100, 3), Bend(45, 50, 3)])
    op = Bending(rates())
    assert op.applies_to(part)
    assert op.setup_time(part) == pytest.approx(20.0)                    # one tool set
    assert op.run_time(part) == pytest.approx(3 * 12 / 60.0)
    assert op.time(part, 5) == pytest.approx(20.0 + 5 * 0.6)


def test_each_extra_tool_adds_a_tool_change():
    part = sheet(bends=[Bend(90, 100, 3), Bend(90, 100, 6)])
    assert Bending(rates()).setup_time(part) == pytest.approx(20.0 + 10.0)


def test_bending_not_applied_when_bends_unknown_or_none():
    op = Bending(rates())
    assert not op.applies_to(sheet(bends=None))
    assert not op.applies_to(sheet(bends=[]))


# ------------------------------------------------------ rule-based ops

def test_paint_is_area_times_two_times_rate():
    part = sheet()
    part.extra["paint"] = True
    op = painting(rate_per_m2=12.0, setup=30.0)
    assert op.applies_to(part)
    oc = op.cost(part, 4)
    assert oc.unit_cost == pytest.approx(part.net_area / 1e6 * 2 * 12.0)
    assert oc.setup_cost == 30.0
    assert oc.cost_area == "finishing"
    assert not op.applies_to(sheet())                      # not flagged for paint


@pytest.mark.parametrize("basis,expected", [
    ("per_part", 5.0),
    ("per_kg", None),     # filled in below from the part weight
    ("per_m", None),
])
def test_rule_bases(basis, expected):
    part = sheet()
    op = RuleOperation("galvanise", basis, 5.0, markup=0.3)
    oc = op.cost(part, 1)
    if basis == "per_kg":
        expected = part.weight_kg() * 5.0
    if basis == "per_m":
        expected = part.cut_length / 1000.0 * 5.0
    assert oc.unit_cost == pytest.approx(expected)
    assert oc.markup == 0.3


def test_flat_fee_is_a_batch_charge():
    oc = RuleOperation("heat treat", "flat", 150.0).cost(sheet(), 10)
    assert oc.setup_cost == 150.0 and oc.unit_cost == 0.0
    assert oc.per_unit(10) == pytest.approx(15.0)


def test_rule_per_kg_without_weight_is_unpriced():
    part = PartFeatures("mystery", part_type="machined")
    oc = RuleOperation("plate", "per_kg", 3.0).cost(part, 1)
    assert oc.unit_cost is None and oc.per_unit(1) is None and oc.notes


def test_rule_part_type_filter_and_bad_basis():
    op = RuleOperation("deburr", "per_part", 1.0, part_types=["sheet"])
    assert op.applies_to(sheet()) and not op.applies_to(PartFeatures("x", part_type="tube"))
    with pytest.raises(ValueError):
        RuleOperation("x", "per_hour", 1.0)


# --------------------------------------------------------------- welding

def welded_bracket():
    """Base plate + two gussets: each gusset touches the base along 80 mm on
    both sides (two fillet runs) and the upright along 60 mm."""
    contacts = [
        ("base", "gusset1", 80.0), ("gusset1", "base", 80.0), ("gusset1", "upright", 60.0),
        ("base", "gusset2", 80.0), ("base", "gusset2", 80.0), ("gusset2", "upright", 60.0),
        ("base", "upright", 150.0),
        ("base", "base", 999.0),                   # self-contact is ignored
    ]
    joints = joints_from_contacts(contacts, {"base": 5.0, "gusset1": 3.0, "gusset2": 3.0, "upright": 3.0})
    return PartFeatures("bracket-assy", part_type="assembly", thickness=3.0, weld_joints=joints,
                        part_count=4)


def test_joints_from_contacts_sums_per_pair():
    joints = {(j.part_a, j.part_b): j for j in welded_bracket().weld_joints}
    assert joints[("base", "gusset1")].length == 160.0
    assert joints[("base", "gusset2")].length == 160.0
    assert joints[("base", "upright")].length == 150.0
    assert joints[("base", "upright")].thickness == 3.0       # thinner part
    assert sum(j.length for j in joints.values()) == pytest.approx(590.0)
    assert all(j.weld_type == "fillet" and j.enabled for j in joints.values())


def test_weld_cost_known_length():
    part = welded_bracket()
    op = Welding(rates())
    assert op.weld_length(part) == pytest.approx(590.0)
    oc = op.cost(part, 1)
    # 0.59 m x 10 min/m at 1/min, plus 0.59 m x 2.0 filler
    assert oc.run_min == pytest.approx(5.9)
    assert oc.unit_cost == pytest.approx(5.9 + 1.18)
    assert oc.setup_cost == pytest.approx(15.0)


def test_disabled_joint_and_butt_type():
    part = welded_bracket()
    part.weld_joints[0].enabled = False                     # bolted, not welded
    part.weld_joints[1].weld_type = "butt"
    lengths = {(j.part_a, j.part_b): j.length for j in part.weld_joints}
    oc = Welding(rates()).cost(part, 1)
    fillet_m = (590.0 - lengths[("base", "gusset1")] - lengths[("base", "gusset2")]) / 1000
    butt_m = lengths[("base", "gusset2")] / 1000
    assert oc.run_min == pytest.approx(fillet_m * 10 + butt_m * 20)


def test_manual_weld_length_and_tacks_fallback():
    part = PartFeatures("frame", part_type="assembly", thickness=3.0, manual_weld_length=1000.0, tack_count=6)
    oc = Welding(rates()).cost(part, 1)
    assert oc.run_min == pytest.approx(10.0 + 6 * 30 / 60)
    with pytest.raises(ValueError):
        WeldJoint("a", "b", 10, weld_type="spot")


# ------------------------------------------------------------ tube / saw

def rhs_member(end_cuts=("mitre", "mitre"), **kw):
    prof = ProfileInfo("RHS", 100, 50, 3, area=864.0, perimeter=300.0)
    return PartFeatures("top-rail", part_type="tube", material="mild steel", profile=prof,
                        length=1200.0, end_cuts=end_cuts, **kw)


def test_saw_time_with_mitres():
    op = TubeCutting(rates(), machine="saw")
    assert op.run_time(rhs_member(("square", "square"))) == pytest.approx(2 * 60 / 60)
    assert op.run_time(rhs_member()) == pytest.approx(2 * 1.5 * 60 / 60)
    assert op.run_time(rhs_member(("square", "cope"))) == pytest.approx((1 + 3) * 60 / 60)
    assert op.setup_time(rhs_member()) == 5.0


def test_tube_laser_time():
    op = TubeCutting(rates(), machine="laser")
    part = rhs_member(("square", "mitre"), cutout_length=400.0, cutout_count=2)
    expected = (2.5 * 300 / 3000 + 2 * 1.2 / 60) + (400 / 3000 + 2 * 1.2 / 60) + 30 / 60
    assert op.run_time(part) == pytest.approx(expected)


def test_tube_rejects_unknown_end_and_machine():
    with pytest.raises(ValueError):
        TubeCutting(rates()).run_time(rhs_member(("square", "fishmouth")))
    with pytest.raises(ValueError):
        TubeCutting(rates(), machine="plasma")


# --------------------------------------------------------------- machining

def test_plate_with_four_m8_tapped_holes():
    part = sheet(hole_features=[HoleFeature(6.8, "tapped", "M8", count=4)])
    op = Machining(rates())
    assert op.applies_to(part)
    oc = op.cost(part, 1)
    assert oc.estimate
    assert oc.run_min == pytest.approx(4 * 30 / 60)
    assert oc.unit_cost == pytest.approx(2.0)
    assert oc.setup_cost == pytest.approx(20.0)


def test_machined_block_holes_and_pocket():
    part = PartFeatures("block", part_type="machined",
                        hole_features=[HoleFeature(8, "plain", count=2), HoleFeature(10, "countersink")],
                        pockets=[Pocket(volume=20_000.0)])
    oc = Machining(rates(), setups=2).cost(part, 1)
    assert oc.run_min == pytest.approx(2 * 15 / 60 + 20 / 60 + 20.0 / 10.0)
    assert oc.setup_min == pytest.approx(40.0)


def test_sheet_plain_holes_are_not_machined():
    assert not Machining(rates()).applies_to(sheet(hole_features=[HoleFeature(10, count=2)]))


# ------------------------------------------------------ assembly / bought-in

def test_assembly_labour():
    part = PartFeatures("assy", part_type="assembly", part_count=5, fastener_count=8)
    assert AssemblyLabour(rates()).run_time(part) == pytest.approx(10 + 5 * 2 + 8 * 0.5)


def test_bought_in_bolt():
    bolt = PartFeatures("M8x25 bolt", part_type="purchased", unit_cost=0.12, supplier="Bolts Ltd",
                        markup=0.5, quantity=8)
    oc = BoughtIn().cost(bolt, 8)
    assert oc.unit_cost == 0.12 and oc.markup == 0.5 and oc.cost_area == "purchased"
    unpriced = BoughtIn().cost(PartFeatures("hinge", part_type="purchased"), 1)
    assert unpriced.unit_cost is None


def test_quote_cost_total():
    assert QuoteCost("transport", 80.0, per_set=2.5).total(10) == pytest.approx(105.0)


def test_default_operations_have_unique_names():
    ops = default_operations(rates())
    assert len({op.name for op in ops}) == len(ops)
