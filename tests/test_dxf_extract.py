"""
tests/test_dxf_extract.py
--------------------------
merge_duplicate_parts() -- an assembly's flat-pattern DXF export commonly
draws the same part several times (once per placement), and without this
merge each repeat became its own part at quantity 1 instead of one part at
quantity N (the exact bug this covers: parts used multiple times in an
assembly were imported as separate parts)."""

import dxf_extract


def _square(ox, oy, s=20.0):
    return [(ox, oy), (ox + s, oy), (ox + s, oy + s), (ox, oy + s)]


def test_merge_duplicate_parts_collapses_identical_shapes():
    parts = [
        {"outer": _square(0, 0), "holes": [], "layers": {"0"}},
        {"outer": _square(100, 0), "holes": [], "layers": {"0"}},
        {"outer": _square(50, 200), "holes": [], "layers": {"0"}},
    ]
    merged = dxf_extract.merge_duplicate_parts(parts)
    assert len(merged) == 1
    assert merged[0]["quantity"] == 3


def test_merge_duplicate_parts_keeps_distinct_shapes_separate():
    parts = [
        {"outer": _square(0, 0, s=20), "holes": [], "layers": {"0"}},
        {"outer": _square(100, 0, s=30), "holes": [], "layers": {"0"}},
    ]
    merged = dxf_extract.merge_duplicate_parts(parts)
    assert len(merged) == 2
    assert all(p["quantity"] == 1 for p in merged)


def test_extract_parts_infers_quantity_from_repeated_instances(tmp_path):
    ezdxf = __import__("ezdxf")
    doc = ezdxf.new()
    msp = doc.modelspace()
    for ox, oy in [(0, 0), (100, 0), (200, 200)]:
        msp.add_lwpolyline(_square(ox, oy) + [_square(ox, oy)[0]], close=True)
    msp.add_lwpolyline(_square(0, 100, s=50) + [_square(0, 100, s=50)[0]], close=True)
    path = tmp_path / "dup.dxf"
    doc.saveas(str(path))

    parts, warnings = dxf_extract.extract_parts(str(path))
    assert warnings == []
    quantities = sorted(p["quantity"] for p in parts)
    assert quantities == [1, 3]
