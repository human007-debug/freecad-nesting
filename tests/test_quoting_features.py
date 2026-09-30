"""Phase 1: per-part features from flat patterns, extractor JSON and
nester Parts -- with fixture parts whose answers are known by hand."""

import json
import math
import subprocess
import sys

import pytest

from nester import Part
from quoting.features import (Bend, HoleFeature, PartFeatures, ProfileInfo, detected_round_holes,
                              features_from_nester_part, features_from_part_json,
                              features_from_polygons, fit_circle, load_features, normalize_material)


def circle(cx, cy, r, n=64):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


RECT_100x50 = [(0, 0), (100, 0), (100, 50), (0, 50)]


def plate_100x50_two_holes(r=5.0):
    return features_from_polygons("plate", RECT_100x50, [circle(25, 25, r), circle(75, 25, r)],
                                  material="mild steel", thickness=3.0, bends=[])


def test_plate_with_two_holes_cut_length_and_pierces():
    f = plate_100x50_two_holes(r=5.0)
    # 300 mm outer + two 10 mm holes cut as true circles.
    assert f.cut_length == pytest.approx(300 + 2 * math.pi * 10, rel=1e-9)
    assert f.pierces == 3
    assert f.internal_contours == 2


def test_plate_areas_and_weight():
    f = plate_100x50_two_holes(r=5.0)
    hole_poly_area = 64 / 2 * 25 * math.sin(2 * math.pi / 64)   # tessellated circle area
    assert f.gross_area == pytest.approx(5000.0)
    assert f.net_area == pytest.approx(5000 - 2 * hole_poly_area)
    assert f.bbox == (100.0, 50.0)
    # 3 mm mild steel at 7.85 g/cm^3.
    assert f.weight_kg() == pytest.approx(f.net_area * 3 * 7.85 / 1e6)


def test_non_circular_hole_uses_polygon_perimeter():
    slot = [(10, 10), (40, 10), (40, 20), (10, 20)]
    f = features_from_polygons("slotted", RECT_100x50, [slot])
    assert f.cut_length == pytest.approx(300 + 80)
    assert f.pierces == 2


def test_fit_circle_accepts_circles_and_rejects_rectangles():
    cx, cy, r = fit_circle(circle(3, 4, 7.5, n=24))
    assert (cx, cy, r) == pytest.approx((3, 4, 7.5))
    assert fit_circle(RECT_100x50) is None
    assert fit_circle(circle(0, 0, 5, n=6)) is None      # too few points to call it a circle


def test_bends_unknown_vs_known_flat():
    unknown = features_from_polygons("a", RECT_100x50)
    assert unknown.bends is None and not unknown.bends_known and unknown.bend_count is None
    flat = features_from_polygons("b", RECT_100x50, bends=[])
    assert flat.bends_known and flat.bend_count == 0


def test_part_json_old_schema_bend_count():
    entry = {"name": "bracket", "points": RECT_100x50, "holes": [], "thickness": 2.0,
             "bends": 2, "material": "Aluminum", "quantity": 4, "source": "fcstd"}
    f = features_from_part_json(entry)
    assert f.bend_count == 2 and all(b.angle_deg is None for b in f.bends)
    assert f.quantity == 4
    assert f.density() == pytest.approx(2.70)       # "Aluminum" -> aluminium


def test_part_json_extended_schema():
    entry = {
        "name": "bracket", "points": RECT_100x50, "holes": [circle(50, 25, 3.4)], "thickness": 2.0,
        "bends": 1, "source": "fcstd",
        "bend_details": [{"angle_deg": 90.0, "length": 50.0, "radius": 2.0, "direction": None}],
        "hole_features": [{"diameter": 6.8, "kind": "tapped", "thread": "M8", "count": 1}],
    }
    f = features_from_part_json(entry)
    assert f.bends == [Bend(90.0, 50.0, 2.0, None)]
    assert f.hole_features == [HoleFeature(6.8, "tapped", "M8", None, 1)]
    assert f.machined_hole_features() == f.hole_features


def test_dxf_part_json_has_unknown_bends():
    f = features_from_part_json({"name": "d", "points": RECT_100x50, "holes": [], "source": "dxf"})
    assert f.bends is None


def test_from_nester_part_same_schema():
    p = Part("np", RECT_100x50, holes=[circle(50, 25, 10)], quantity=3, material="stainless", thickness=1.5)
    f = features_from_nester_part(p)
    assert f.part_type == "sheet" and f.quantity == 3 and f.bends is None
    assert f.cut_length == pytest.approx(300 + 2 * math.pi * 10)


def test_load_features_reads_extractor_output(tmp_path):
    path = tmp_path / "parts.json"
    path.write_text(json.dumps({"parts": [
        {"name": "p1", "points": RECT_100x50, "holes": [], "thickness": 3, "bends": 0},
        {"name": "p2", "points": RECT_100x50, "holes": [], "thickness": 3, "bends": 1},
    ], "unresolved": [], "skipped": []}))
    feats = load_features(str(path))
    assert [f.bend_count for f in feats] == [0, 1]


def test_detected_round_holes_groups_by_diameter():
    f = features_from_polygons("p", [(0, 0), (200, 0), (200, 100), (0, 100)],
                               [circle(20, 20, 4), circle(60, 20, 4), circle(100, 50, 10),
                                [(150, 40), (170, 40), (170, 60), (150, 60)]])
    assert detected_round_holes(f) == [HoleFeature(8.0, count=2), HoleFeature(20.0, count=1)]


def test_plain_holes_on_sheet_are_not_machining():
    f = features_from_polygons("p", RECT_100x50, hole_features=[HoleFeature(8, "plain", count=2),
                                                                HoleFeature(6.8, "tapped", "M8")])
    assert [h.kind for h in f.machined_hole_features()] == ["tapped"]
    f.part_type = "machined"
    assert len(f.machined_hole_features()) == 2


def test_tube_weight_and_surface_area():
    prof = ProfileInfo("RHS", 100, 50, 3, area=864.0, perimeter=300.0)
    f = PartFeatures("rail", part_type="tube", material="mild steel", profile=prof, length=1000.0)
    assert f.weight_kg() == pytest.approx(864 * 1000 * 7.85 / 1e6)
    assert f.surface_area_m2() == pytest.approx(0.3)


def test_invalid_part_type_and_hole_kind():
    with pytest.raises(ValueError):
        PartFeatures("x", part_type="weldment")
    with pytest.raises(ValueError):
        HoleFeature(8, kind="reamed")


def test_normalize_material_aliases():
    assert normalize_material("Aluminum") == "aluminium"
    assert normalize_material(" SS304 ") == "stainless"
    assert normalize_material("Mild_Steel") == "mild steel"
    assert normalize_material("Inconel") == "inconel"


def test_quoting_core_imports_without_freecad():
    """The whole core must import with FreeCAD unavailable (a None entry in
    sys.modules makes `import FreeCAD` raise ImportError)."""
    code = (
        "import sys\n"
        "sys.modules['FreeCAD'] = None; sys.modules['Part'] = None; sys.modules['FreeCADGui'] = None\n"
        "import bar_nest\n"
        "from quoting import features, classify, rates, operations, ops_weld, ops_tube, "
        "ops_machining, ops_assembly, costing\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
