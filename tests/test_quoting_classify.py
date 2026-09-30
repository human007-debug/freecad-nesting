"""Phase 2b.1: part-type classification and profile matching."""

import math

import pytest

from quoting.classify import SolidInfo, classify, match_profile


def rect(w, h, x=0.0, y=0.0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def circle(r, n=48):
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def angle(a, b, t):
    return [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]


def channel(depth, flange, t):
    # Web on the left, flanges pointing right.
    return [(0, 0), (flange, 0), (flange, t), (t, t), (t, depth - t), (flange, depth - t),
            (flange, depth), (0, depth)]


def ibeam(depth, flange, tw, tf):
    x0 = (flange - tw) / 2
    return [(0, 0), (flange, 0), (flange, tf), (x0 + tw, tf), (x0 + tw, depth - tf),
            (flange, depth - tf), (flange, depth), (0, depth), (0, depth - tf), (x0, depth - tf),
            (x0, tf), (0, tf)]


def tee(width, depth, t):
    x0 = (width - t) / 2
    return [(x0, 0), (x0 + t, 0), (x0 + t, depth - t), (width, depth - t), (width, depth),
            (0, depth), (0, depth - t), (x0, depth - t)]


def test_rhs():
    p = match_profile(rect(100, 50), [rect(94, 44, 3, 3)])
    assert (p.kind, p.width, p.height, p.wall) == ("RHS", 100, 50, 3)
    assert p.designation == "RHS 100x50x3"
    assert p.hollow
    assert p.area == pytest.approx(100 * 50 - 94 * 44)


def test_shs_drawn_upright():
    p = match_profile(rect(40, 40), [rect(36, 36, 2, 2)])
    assert p.designation == "SHS 40x40x2"


def test_chs_and_round_bar():
    chs = match_profile(circle(24.15), [circle(24.15 - 3.2)])
    assert chs.kind == "CHS" and chs.designation == "CHS 48.3x3.2"
    bar = match_profile(circle(10))
    assert bar.kind == "ROUND" and not bar.hollow


def test_flat_bar():
    p = match_profile(rect(8, 60))
    assert p.designation == "FLAT 60x8x8" and p.kind == "FLAT"


@pytest.mark.parametrize("outer,kind", [
    (angle(50, 50, 5), "ANGLE"),
    (angle(75, 50, 6), "ANGLE"),
    (channel(100, 50, 6), "CHANNEL"),
    ([(-x, y) for x, y in channel(100, 50, 6)], "CHANNEL"),       # mirrored
    ([(y, x) for x, y in channel(100, 50, 6)], "CHANNEL"),        # lying down
    (ibeam(200, 100, 6, 9), "IBEAM"),
    ([(y, x) for x, y in ibeam(200, 100, 6, 9)], "IBEAM"),         # on its side
    (tee(100, 80, 8), "TEE"),
])
def test_open_sections(outer, kind):
    assert match_profile(outer).kind == kind


def test_angle_dimensions_and_wall_estimate():
    p = match_profile(angle(50, 50, 5))
    assert (p.width, p.height) == (50, 50)
    assert p.wall == pytest.approx(5, abs=0.3)


def test_unrecognised_section_is_other():
    star = [(math.cos(a) * (40 if i % 2 == 0 else 15), math.sin(a) * (40 if i % 2 == 0 else 15))
            for i, a in enumerate(math.pi / 5 * k for k in range(10))]
    assert match_profile(star).kind == "OTHER"


# ------------------------------------------------------------- classify

def test_sheetmetal_and_flat_plate_are_sheet():
    assert classify(SolidInfo("bracket", is_sheetmetal=True)).part_type == "sheet"
    assert classify(SolidInfo("plate", is_flat_plate=True)).part_type == "sheet"


def test_rhs_frame_members_are_tube_with_mitres():
    info = SolidInfo("frame-top", section_outer=rect(100, 50), section_holes=[rect(94, 44, 3, 3)],
                     length=1200, end_cuts=("mitre", "mitre"))
    c = classify(info)
    assert c.part_type == "tube"
    assert c.profile.designation == "RHS 100x50x3"


def test_open_section_is_section():
    c = classify(SolidInfo("leg", section_outer=angle(50, 50, 5), length=800))
    assert c.part_type == "section" and c.profile.kind == "ANGLE"


def test_fastener_and_bought_in_list_are_purchased():
    assert classify(SolidInfo("Screw001", is_fastener=True)).part_type == "purchased"
    c = classify(SolidInfo("Hinge-Left", is_flat_plate=True), purchased_names=["hinge*", "PEM*"])
    assert c.part_type == "purchased"          # the list wins over the geometry


def test_everything_else_is_machined():
    assert classify(SolidInfo("block")).part_type == "machined"
    odd = SolidInfo("odd", section_outer=[(0, 0), (10, 0), (5, 3)], length=100)
    assert classify(odd).part_type == "machined"


def test_override_wins_and_is_validated():
    assert classify(SolidInfo("bracket", is_sheetmetal=True), override="machined").part_type == "machined"
    with pytest.raises(ValueError):
        classify(SolidInfo("x"), override="casting")


def test_solid_info_from_dict():
    info = SolidInfo.from_dict({"name": "t", "section_outer": rect(40, 40),
                                "section_holes": [rect(36, 36, 2, 2)], "length": 500,
                                "end_cuts": ["square", "cope"]})
    assert info.end_cuts == ("square", "cope")
    assert classify(info).profile.designation == "SHS 40x40x2"
