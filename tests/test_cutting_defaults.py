"""
tests/test_cutting_defaults.py
--------------------------------
cutting_defaults.py's formulas: monotonic in thickness, respect their
documented floors/ceilings, and recommend_defaults_for_many() picks the
conservative (thickest-group) answer for a mixed-thickness job."""

import cutting_defaults as cd


def test_floors_apply_to_thin_stock():
    rec = cd.recommend_defaults(0.1)
    assert rec["part_spacing"] == 1.0
    assert rec["margin"] == 2.0
    assert rec["microjoint_tab_width"] == 0.5


def test_ceilings_apply_to_thick_stock():
    rec = cd.recommend_defaults(50.0)
    assert rec["part_spacing"] == 8.0
    assert rec["margin"] == 10.0
    assert rec["microjoint_tab_width"] == 3.0


def test_scales_with_thickness_in_the_unclamped_range():
    rec = cd.recommend_defaults(3.0)
    assert rec["part_spacing"] == 6.0        # 2 x 3.0
    assert rec["margin"] == 4.5              # 1.5 x 3.0
    assert rec["microjoint_tab_width"] == 3.0  # 1.5 x 3.0, hits the 3mm cap exactly


def test_target_spacing_is_not_thickness_driven():
    assert cd.recommend_defaults(1.0)["microjoint_target_spacing"] == 150.0
    assert cd.recommend_defaults(10.0)["microjoint_target_spacing"] == 150.0


def test_for_many_picks_the_thickest_groups_numbers():
    mixed = cd.recommend_defaults_for_many([1.0, 6.0, 2.0])
    assert mixed == cd.recommend_defaults(6.0)


def test_for_many_empty_falls_back_to_zero_thickness_floor():
    rec = cd.recommend_defaults_for_many([])
    assert rec == cd.recommend_defaults(0.0)


def test_none_thickness_treated_as_zero():
    assert cd.recommend_defaults(None) == cd.recommend_defaults(0.0)


def test_custom_coefficients_override_the_published_defaults():
    custom = {"part_spacing_multiplier": 3.0, "part_spacing_max": 100.0}
    rec = cd.recommend_defaults(2.0, custom)
    assert rec["part_spacing"] == 6.0  # 3 x 2.0, not the default 2x formula's 4.0


def test_custom_coefficients_partial_override_keeps_the_rest_default():
    custom = {"tab_width_multiplier": 5.0}
    rec = cd.recommend_defaults(0.2, custom)
    assert rec["microjoint_tab_width"] == 1.0    # 5 x 0.2, overridden
    assert rec["margin"] == cd.DEFAULT_COEFFICIENTS["margin_min"]  # untouched, still default


def test_for_many_forwards_coefficients_to_the_thickest_group():
    custom = {"margin_multiplier": 4.0, "margin_max": 1000.0}
    rec = cd.recommend_defaults_for_many([1.0, 5.0], custom)
    assert rec["margin"] == 20.0  # 4 x 5.0 (thickest of the two)
