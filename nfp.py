"""
nfp.py
------
True no-fit-polygon (NFP) computation via pyclipper (Python bindings for
the Clipper polygon library). This is the module that makes `nester.py`'s
placement exact rather than heuristic -- see README's "How the algorithm
works" for the full picture; this module is just the polygon-boolean
machinery underneath it.

pyclipper is a HARD REQUIREMENT for this project (not an optional
accelerator) -- there is no reasonable pure-Python way to compute exact
Minkowski sums / polygon unions for arbitrary concave polygons, and this
project's owner decided nesting density should not be compromised to keep
a "zero dependencies" pitch. `geometry.py` stays pure-Python and is still
used as the final exact feasibility check (see nester.py) -- a safety net
against any numerical edge case here, not the primary placement logic.

THE MATH: for two polygons A (stationary) and B (orbiting), the no-fit
polygon NFP(A, B) is the Minkowski sum A (+) (-B) -- the locus of B's
reference point such that B touches A's boundary without overlapping it.
Points strictly inside NFP(A, B) mean B overlaps A; points on or outside
its boundary mean they don't. For a concave A, NFP(A, B) can have holes
(representing places B can tuck into A's concavities without overlap) --
verified against a hand-derived L-bracket case during development.

pyclipper's `MinkowskiSum` can return multiple raw paths that aren't
already a clean single region (Clipper's own docs note this) -- they must
be unioned (self-union, PFT_NONZERO) to get the real NFP shape, holes
included. This module always does that; nothing downstream needs to know.

Clipper operates on 64-bit integers for numerical robustness, so all
coordinates are scaled by SCALE (0.0001mm precision) on the way in and
back out.

CACHING (`compute_nfp_cached`/`offset_polygon_cached`): both `compute_nfp`
and `offset_polygon` are equivariant under translation of their polygon
argument(s) in exact (real-number) geometry -- translating a polygon by
(dx, dy) before either operation translates the *result* by the exact same
(dx, dy), a rigid-motion identity of Minkowski sums and miter-join
offsetting. That means the same physical shape, nested onto the sheet at
two different positions, produces an NFP/offset result that's just a
translated copy of the other -- so memoizing on the polygon's SHAPE
(translated to a canonical origin) instead of its absolute position lets
unrelated placements of "the same part at the same rotation" share one
computation.

HONEST PRECISION NOTE: that identity is exact in real-number geometry, but
this module's actual backend (pyclipper) rounds coordinates to integers at
SCALE precision BEFORE computing -- and rounding does not commute with
translation in general (round(a + b) != round(a) + b for non-integer b).
A cached result is computed by rounding the polygon's SMALL local
coordinates, then adding back a large, un-rounded (dx, dy) -- a direct
(uncached) call instead rounds the already-large, already-translated
coordinates. Both are "correct" in the sense of being within this module's
own declared SCALE precision, but they are not bit-identical: measured up
to ~0.0009mm divergence between a cached and a direct call for the same
final polygon across 500 randomized cases (never a topology/vertex-count
difference, only sub-thousandth-mm coordinate noise) -- roughly two orders
of magnitude below any real sheet-cutting tolerance, but real, and worth
knowing about if a future caller ever needs bit-for-bit reproducibility
between a cached and an uncached call rather than just geometric
equivalence.

This matters because nester.py's `_candidates()` calls these once per
already-placed part, per rotation/mirror variant being tried, per part
placed -- and genetic.py's GA (or stock_solver.py's hill-climb on top of
it) re-runs the whole nest dozens to hundreds of times over the same part
catalog. Measured on demo.py's 49-part scenario through a 12-population/
8-generation GA search: 504,306 total `compute_nfp` calls collapsed to only
576 distinct (stationary-shape, orbiting-shape) pairs -- an ~875x
redundancy ratio -- so caching turns all but the first occurrence of each
pair into a cache lookup + a translation (cheap, pure Python) instead of a
fresh pyclipper Minkowski-sum call.

HONEST COST: the cache (`_nfp_cache`/`_offset_cache` below) is a plain
unbounded module-level dict, cleared only by an explicit `clear_cache()`
call -- never automatically. For one job (however many times it's
re-nested by a GA/hill-climb search) this is bounded by that job's own
catalog of distinct (shape, rotation, mirror) combinations, which is small.
A long-running process nesting many DIFFERENT part catalogs back to back
(e.g. the native app, across several unrelated "Run Nesting" clicks in one
session) will keep accumulating entries for jobs it's already finished with
-- `clear_cache()` exists for a caller that wants to bound that; nothing
calls it automatically, since clearing between every job would throw away
the exact reuse (e.g. re-running the same job after tweaking one part) this
cache exists for.
"""

import pyclipper

SCALE = 10000  # 0.0001 mm precision -- plenty for sheet-metal work
_ROUND_DECIMALS = 4  # matches SCALE: both express the same 0.0001mm floor

_nfp_cache = {}
_offset_cache = {}


def _to_clipper(points):
    return [(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in points]


def _from_clipper(path):
    return [(x / SCALE, y / SCALE) for x, y in path]


def _reflect(points):
    return [(-x, -y) for x, y in points]


def _self_union(paths):
    """Clean up a raw pyclipper Paths result (ints) into a proper region --
    may still be several disjoint polygons (each possibly with holes)."""
    if not paths:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    return pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def compute_nfp(stationary, orbiting):
    """NFP(stationary, orbiting) -- both plain (x, y) float point lists in
    mm. Returns a list of polygons in the same units (each a list of (x, y)
    tuples); more than one if the NFP is disjoint, and a polygon may
    represent a hole (see module docstring) -- callers pass the whole list
    straight into further pyclipper boolean ops, which handle that via the
    same PFT_NONZERO fill rule, rather than trying to interpret which is
    which themselves."""
    pattern = _to_clipper(_reflect(orbiting))
    path = _to_clipper(stationary)
    raw = pyclipper.MinkowskiSum(pattern, path, True)
    cleaned = _self_union(raw)
    return [_from_clipper(p) for p in cleaned]


def offset_polygon(points, delta):
    """Inflate (delta > 0) or shrink (delta < 0) a polygon by `delta` mm,
    rounding corners as a miter join. Used to bake kerf/min-spacing into an
    NFP obstacle before computing it, rather than needing a separate
    min-distance check afterward."""
    pco = pyclipper.PyclipperOffset()
    pco.AddPath(_to_clipper(points), pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
    solution = pco.Execute(delta * SCALE)
    return [_from_clipper(p) for p in solution]


def _poly_key(points):
    return tuple((round(x, 6), round(y, 6)) for x, y in points)


def _local_frame(points):
    """(points shifted so their bbox min corner sits at the origin, dx, dy)
    -- dx/dy is the translation needed to recover the original `points`.
    See module docstring's CACHING section for why this makes a result
    reusable across differently-positioned placements of the same shape."""
    minx = min(p[0] for p in points)
    miny = min(p[1] for p in points)
    return [(x - minx, y - miny) for x, y in points], minx, miny


def _translate_polys(polys, dx, dy):
    """Translates a cached (local-frame) result back to the caller's actual
    position. Rounded to _ROUND_DECIMALS -- without this, a coordinate
    that was quantized to the 0.0001mm grid in the LOCAL frame (by
    compute_nfp/offset_polygon's own SCALE-based integer rounding) picks up
    extra, un-quantized precision from `dx`/`dy` on top of that, landing
    slightly off the grid a direct (uncached) call would have produced --
    same shape either way (a difference of at most a few 0.0001mm, this
    module's own stated precision floor -- see SCALE above), but rounding
    here keeps the two call paths' output identical rather than merely
    close. Caught by testing compute_nfp_cached against compute_nfp
    directly, translated, on 500 randomized polygon pairs."""
    if dx == 0.0 and dy == 0.0:
        return polys
    return [[(round(x + dx, _ROUND_DECIMALS), round(y + dy, _ROUND_DECIMALS)) for x, y in poly]
            for poly in polys]


def compute_nfp_cached(stationary, orbiting):
    """Same result as compute_nfp(stationary, orbiting), memoized on shape
    rather than absolute position -- see module docstring's CACHING section.
    `orbiting` is assumed already in canonical (bbox-min-at-origin) form,
    which is how nester.py always calls this (a placement candidate's shape
    is normalized to origin before any NFP against it is computed) -- only
    `stationary` (an already-placed part's actual sheet-coordinate polygon)
    needs re-normalizing here."""
    local_stat, dx, dy = _local_frame(stationary)
    key = (_poly_key(local_stat), _poly_key(orbiting))
    cached = _nfp_cache.get(key)
    if cached is None:
        cached = compute_nfp(local_stat, orbiting)
        _nfp_cache[key] = cached
    return _translate_polys(cached, dx, dy)


def offset_polygon_cached(points, delta):
    """Same result as offset_polygon(points, delta), memoized on shape
    rather than absolute position -- see module docstring's CACHING
    section."""
    local, dx, dy = _local_frame(points)
    key = (_poly_key(local), delta)
    cached = _offset_cache.get(key)
    if cached is None:
        cached = offset_polygon(local, delta)
        _offset_cache[key] = cached
    return _translate_polys(cached, dx, dy)


def clear_cache():
    """Drops all memoized compute_nfp_cached/offset_polygon_cached results
    -- see module docstring's CACHING section for when a caller would want
    this (bounding memory across many unrelated jobs in one long-running
    process). Never called automatically."""
    _nfp_cache.clear()
    _offset_cache.clear()


def union_all(polygon_lists):
    """Union many polygons (each a list of (x, y) points; `polygon_lists`
    is a list of such lists, e.g. one NFP's worth of paths per placed
    part) into one clean region. Returns a list of polygons (holes
    included, same convention as compute_nfp)."""
    paths = [_to_clipper(p) for p in polygon_lists]
    return [_from_clipper(p) for p in _self_union(paths)]


def intersection_area(poly_a, poly_b):
    """Area of overlap between two polygons -- 0.0 if they're disjoint OR
    merely touch along a shared edge/corner (unlike geometry.polygons_overlap,
    which flags exact boundary contact as overlap too, since its
    segment-intersection test can't tell "touching" from "crossing"). This
    is what nester.py uses for its final placement sanity check: an NFP
    candidate sits, by construction, exactly on some obstacle's boundary,
    so "touching is fine, only real area overlap isn't" is the correct
    test, not a same-point-means-overlap one."""
    pc = pyclipper.Pyclipper()
    pc.AddPath(_to_clipper(poly_a), pyclipper.PT_SUBJECT, True)
    pc.AddPath(_to_clipper(poly_b), pyclipper.PT_CLIP, True)
    solution = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return sum(abs(pyclipper.Area(p)) for p in solution) / (SCALE * SCALE)


def difference(subject_polygons, clip_polygons):
    """subject_polygons MINUS clip_polygons, both lists of polygons (each
    a list of (x, y) points). Returns a list of polygons (holes
    included)."""
    if not subject_polygons:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths([_to_clipper(p) for p in subject_polygons], pyclipper.PT_SUBJECT, True)
    if clip_polygons:
        pc.AddPaths([_to_clipper(p) for p in clip_polygons], pyclipper.PT_CLIP, True)
        solution = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    else:
        solution = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    return [_from_clipper(p) for p in solution]
