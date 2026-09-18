"""
tests/conftest.py
------------------
Shared fixtures for the pytest suite. The part catalog here is the same
49-part scenario demo.py nests (6 shapes, some with holes, quantities up to
10) -- every test that needs "a real, moderately complex nesting job"
reuses this one, already-characterized scenario (known sheet count and
utilization -- see test_nester.py) rather than each test inventing its own
throwaway geometry.
"""

import math
import random

import pytest

from nester import Part

SHEET_W, SHEET_H = 1220.0, 2440.0  # a standard 4x8 ft sheet, in mm -- same as demo.py


def _l_bracket(w, h, t):
    return [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]


def _hexagon(r):
    return [(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)))
            for i in range(6)]


def _t_shape(w, h, stem_w, stem_h):
    top_h = h - stem_h
    sx = (w - stem_w) / 2
    return [
        (sx, 0), (sx + stem_w, 0), (sx + stem_w, top_h),
        (w, top_h), (w, h), (0, h), (0, top_h), (sx, top_h),
    ]


def _star(r_out, r_in, points=5):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi / points * i - math.pi / 2
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def _notched_rect(w, h, notch=15):
    return [(0, 0), (w / 2 - notch, 0), (w / 2, notch), (w / 2 + notch, 0),
            (w, 0), (w, h), (0, h)]


def _rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def _circle(cx, cy, r, n=16):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def make_demo_parts():
    """A fresh copy of demo.py's 49-part catalog every call -- callers that
    nest it more than once in one test (e.g. comparing serial vs. parallel
    search) get independent Part lists, even though the Part objects
    themselves are never mutated by nesting."""
    return [
        Part("L-Bracket-A", _l_bracket(220, 180, 45), quantity=6),
        Part("Hexagon-Plate", _hexagon(70), quantity=8, rotations=[0, 30, 60, 90, 120, 150]),
        Part("T-Bracket", _t_shape(200, 160, 60, 110), quantity=5),
        Part("Star-Deco", _star(90, 40, 6), quantity=4, rotations=[0, 60, 120, 180, 240, 300]),
        Part("Notched-Plate", _notched_rect(260, 120), quantity=6),
        Part("Small-Square", [(0, 0), (60, 0), (60, 60), (0, 60)], quantity=10),
        Part("Mount-Plate", _rect(180, 120),
             holes=[_circle(40, 40, 12), _circle(140, 40, 12), _circle(90, 90, 12)],
             quantity=4),
        Part("Washer-Plate", _rect(100, 100), holes=[_circle(50, 50, 30)], quantity=6),
    ]


@pytest.fixture
def demo_parts():
    return make_demo_parts()


def rand_polygon(rng, n_range=(3, 12), scale=100.0, cx=0.0, cy=0.0):
    """A random simple (non-self-intersecting) polygon: points placed at
    increasing angles around (cx, cy) so the perimeter never crosses
    itself, with independently randomized radii so it's neither a regular
    polygon nor always convex."""
    n = rng.randint(*n_range)
    angles = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    return [(cx + rng.uniform(0.3, 1.0) * scale * math.cos(a),
             cy + rng.uniform(0.3, 1.0) * scale * math.sin(a)) for a in angles]


@pytest.fixture(autouse=True)
def _clear_nfp_cache():
    """nfp.py's compute_nfp_cached/offset_polygon_cached memoize into
    module-level dicts (see nfp.py's CACHING docstring) -- shared mutable
    state that would otherwise leak cache hits/misses between tests.
    Cleared before AND after every test so neither the previous test's
    cache contents nor this test's can bleed into a neighbor."""
    import nfp
    nfp.clear_cache()
    yield
    nfp.clear_cache()
