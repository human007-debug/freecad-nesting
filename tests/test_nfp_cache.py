"""
tests/test_nfp_cache.py
------------------------
Correctness of nfp.py's shape-keyed caching (compute_nfp_cached /
offset_polygon_cached) against the uncached functions they wrap, under
translation -- see nfp.py's CACHING docstring for the exact-in-real-number-
geometry argument these rely on, and its "HONEST PRECISION NOTE" for why
this test uses a tolerance rather than exact equality: pyclipper quantizes
to a 0.0001mm integer grid BEFORE computing, and that quantization doesn't
commute with translation, so a cached result (quantized in a small local
frame, then translated by an un-rounded offset) can differ from a direct
call's result (quantized after already being translated) by a small,
bounded amount -- measured up to ~0.0009mm/~0.0019mm here, three orders of
magnitude below any real sheet-cutting tolerance, and never a difference in
topology (vertex/polygon count).
"""

import random

import pytest

import nfp
from tests.conftest import rand_polygon

# Generous headroom above the measured ~0.0009mm (compute_nfp) / ~0.0019mm
# (offset_polygon) worst case, while still far tighter than any real
# cutting tolerance -- this checks "within the system's own declared
# precision", not "bit-identical".
TOLERANCE_MM = 0.01


def _flatten_sorted(polys):
    return sorted(tuple(round(v, 6) for v in p) for poly in polys for p in poly)


def _max_point_diff(pts_a, pts_b):
    return max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(pts_a, pts_b))


@pytest.mark.parametrize("seed", range(5))
def test_compute_nfp_cached_matches_direct_under_translation(seed):
    rng = random.Random(seed)
    for _ in range(100):
        base = rand_polygon(rng, n_range=(3, 8), scale=rng.uniform(20, 60))
        orbiting = rand_polygon(rng, n_range=(3, 8), scale=rng.uniform(10, 40))
        dx, dy = rng.uniform(-500, 500), rng.uniform(-500, 500)
        translated = [(x + dx, y + dy) for x, y in base]

        direct = _flatten_sorted(nfp.compute_nfp(translated, orbiting))
        cached = _flatten_sorted(nfp.compute_nfp_cached(translated, orbiting))

        assert len(direct) == len(cached)
        assert _max_point_diff(direct, cached) < TOLERANCE_MM


@pytest.mark.parametrize("seed", range(5))
def test_offset_polygon_cached_matches_direct_under_translation(seed):
    rng = random.Random(seed + 10_000)
    kerf = 3.0
    for _ in range(100):
        base = rand_polygon(rng, n_range=(3, 8), scale=rng.uniform(20, 60))
        dx, dy = rng.uniform(-500, 500), rng.uniform(-500, 500)
        translated = [(x + dx, y + dy) for x, y in base]

        direct = _flatten_sorted(nfp.offset_polygon(translated, kerf))
        cached = _flatten_sorted(nfp.offset_polygon_cached(translated, kerf))

        assert len(direct) == len(cached)
        assert _max_point_diff(direct, cached) < TOLERANCE_MM


def test_compute_nfp_cached_hit_path_is_consistent():
    """Calling compute_nfp_cached twice with the exact same shapes should
    hit the cache the second time and return the identical (not just
    close) result -- there's no quantization-order discrepancy to tolerate
    here since both calls take the same path."""
    base = rand_polygon(random.Random(5), n_range=(4, 6), scale=40)
    orbiting = rand_polygon(random.Random(6), n_range=(4, 6), scale=30)
    first = nfp.compute_nfp_cached(base, orbiting)
    second = nfp.compute_nfp_cached(base, orbiting)
    assert first == second


def test_clear_cache_empties_both_caches():
    base = rand_polygon(random.Random(1), scale=40)
    orbiting = rand_polygon(random.Random(2), scale=30)
    nfp.compute_nfp_cached(base, orbiting)
    nfp.offset_polygon_cached(base, 3.0)
    assert nfp._nfp_cache
    assert nfp._offset_cache

    nfp.clear_cache()
    assert not nfp._nfp_cache
    assert not nfp._offset_cache
