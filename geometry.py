"""
geometry.py
-----------
Minimal, dependency-free 2D polygon geometry needed for irregular nesting:
  - rotation / translation
  - exact polygon-vs-polygon overlap test (edge intersection + containment)
  - exact minimum distance between two polygons (for kerf/spacing enforcement)

No hard dependency required -- the pure Python + math implementation below
runs anywhere, unmodified from this project's original zero-dependency
pitch. See PERFORMANCE below for the optional compiled accelerator.

All polygons are represented as a list of (x, y) tuples, assumed simple
(non-self-intersecting) and given in either winding order. Holes are not
modeled in this proof of concept (outer contour only).

PERFORMANCE: `polygons_overlap`/`polygons_min_distance` are nester.py's
single hottest call -- profiling demo.py's 49-part scenario showed them
costing 84% of total nesting time (vs. 15% for the actual Minkowski-sum/NFP
math in nfp.py, which is already pyclipper's compiled C++), because the
scalar, edge-pair-at-a-time segment-intersection/distance loop below is
called millions of times over on small (4-16 vertex) polygons.

Two fixes were tried before landing on the one below:
  - A numpy-vectorized rewrite (every edge-of-A x edge-of-B pair as one
    array op) was measured SLOWER end-to-end (8.99s vs 5.92s) -- these
    polygons are too small for vectorization to pay off; per-call numpy
    array-allocation overhead dominates.
  - Reusing nester.py's already-computed kerf-offset polygon (a pyclipper
    miter-join offset) as an overlap-area check, instead of an exact
    distance calc, was measured to actually MISCLASSIFY placements against
    a scalar ground truth in randomized testing (both false rejections from
    miter overshoot at sharp corners, and -- worse -- a false acceptance of
    a placement 1.64mm apart under a 3mm kerf) -- rejected outright, this
    project's engine is exact by design, not heuristic.
  - What actually costs the time is CPython's per-operation interpreter
    overhead in the scalar loop itself, not the algorithm or the
    boolean-op backend -- so `geometry_fast.pyx` is a literal, unmodified
    port of the exact same scalar algorithm below to Cython, compiled to a
    native extension. Verified bit-for-bit equivalent (0 mismatches, ~1e-14
    float noise) against this pure-Python version across 30,000 randomized
    polygon pairs, and measured 30-45x faster on those same pairs.

This module still defines the full pure-Python implementation below
unconditionally (so the project keeps running with zero build step / on
any platform pyclipper itself can't reach a compiler for); at import time
it's transparently replaced by the compiled `geometry_fast` module's
versions if that extension has been built (see README's build
instructions) -- same function names, same call signature either way.
"""

import math

EPS = 1e-9


# ---------------------------------------------------------------- transforms

def rotate_points(points, angle_deg, pivot=(0.0, 0.0)):
    """Rotate points by angle_deg (CCW, degrees) around pivot."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    px, py = pivot
    out = []
    for x, y in points:
        x0, y0 = x - px, y - py
        out.append((x0 * ca - y0 * sa + px, x0 * sa + y0 * ca + py))
    return out


def translate_points(points, dx, dy):
    return [(x + dx, y + dy) for x, y in points]


def polygon_bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def normalize_to_origin(points):
    """Shift polygon so its bbox min corner sits at (0, 0). Returns (shifted_points, w, h)."""
    minx, miny, maxx, maxy = polygon_bbox(points)
    shifted = translate_points(points, -minx, -miny)
    return shifted, maxx - minx, maxy - miny


def polygon_area(points):
    """Signed shoelace area (absolute value = true area)."""
    n = len(points)
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def polygon_perimeter(points):
    """Closed-loop perimeter -- last point implicitly connects back to the
    first, same convention as polygon_area()/nester.py/dxf_writer.py."""
    n = len(points)
    total = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


# ---------------------------------------------------------- point-in-polygon

def point_in_polygon(pt, poly):
    """Ray-casting point-in-polygon test."""
    x, y = pt
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)):
            x_intersect = (xj - xi) * (y - yi) / ((yj - yi) if abs(yj - yi) > EPS else EPS) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


# --------------------------------------------------------- segment geometry

def _orientation(p, q, r):
    val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
    if abs(val) < EPS:
        return 0
    return 1 if val > 0 else 2


def _on_segment(p, q, r):
    return (min(p[0], r[0]) - EPS <= q[0] <= max(p[0], r[0]) + EPS and
            min(p[1], r[1]) - EPS <= q[1] <= max(p[1], r[1]) + EPS)


def segments_intersect(p1, p2, p3, p4):
    """True proper/improper segment intersection test (handles collinear overlap)."""
    o1 = _orientation(p1, p2, p3)
    o2 = _orientation(p1, p2, p4)
    o3 = _orientation(p3, p4, p1)
    o4 = _orientation(p3, p4, p2)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p3, p2):
        return True
    if o2 == 0 and _on_segment(p1, p4, p2):
        return True
    if o3 == 0 and _on_segment(p3, p1, p4):
        return True
    if o4 == 0 and _on_segment(p3, p2, p4):
        return True
    return False


def _point_seg_distance(p, a, b):
    """Distance from point p to segment ab."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _seg_seg_distance(a1, a2, b1, b2):
    if segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _point_seg_distance(a1, b1, b2),
        _point_seg_distance(a2, b1, b2),
        _point_seg_distance(b1, a1, a2),
        _point_seg_distance(b2, a1, a2),
    )


# --------------------------------------------------------- polygon-polygon

def _polygons_overlap_py(poly_a, poly_b):
    """
    Exact overlap test for two simple polygons: true if they intersect
    (edges cross) OR one is fully contained inside the other.
    """
    na, nb = len(poly_a), len(poly_b)
    for i in range(na):
        a1, a2 = poly_a[i], poly_a[(i + 1) % na]
        for j in range(nb):
            b1, b2 = poly_b[j], poly_b[(j + 1) % nb]
            if segments_intersect(a1, a2, b1, b2):
                return True
    # No edges cross -> either fully separate, or one fully inside the other
    if point_in_polygon(poly_a[0], poly_b):
        return True
    if point_in_polygon(poly_b[0], poly_a):
        return True
    return False


def _polygons_min_distance_py(poly_a, poly_b):
    """Exact minimum distance between two polygon boundaries. 0.0 if they overlap."""
    if _polygons_overlap_py(poly_a, poly_b):
        return 0.0
    na, nb = len(poly_a), len(poly_b)
    best = math.inf
    for i in range(na):
        a1, a2 = poly_a[i], poly_a[(i + 1) % na]
        for j in range(nb):
            b1, b2 = poly_b[j], poly_b[(j + 1) % nb]
            d = _seg_seg_distance(a1, a2, b1, b2)
            if d < best:
                best = d
    return best


# Transparently prefer the compiled accelerator (see PERFORMANCE above) --
# same names, same signatures, same exactness either way. Nothing else in
# this codebase should call the `_py` versions directly; they're kept as
# the pure-Python fallback AND as geometry_fast.pyx's correctness reference.
try:
    from geometry_fast import polygons_overlap, polygons_min_distance
except ImportError:
    polygons_overlap = _polygons_overlap_py
    polygons_min_distance = _polygons_min_distance_py


def polygon_fits_in_sheet(poly, sheet_w, sheet_h, margin_left=0.0, margin_right=0.0,
                           margin_bottom=0.0, margin_top=0.0):
    for x, y in poly:
        if (x < margin_left - EPS or y < margin_bottom - EPS
                or x > sheet_w - margin_right + EPS or y > sheet_h - margin_top + EPS):
            return False
    return True


def bboxes_separated(bbox_a, bbox_b, margin=0.0):
    """Cheap O(1) rejection test: True if the two bboxes are far enough apart
    that the polygons cannot possibly overlap or violate `margin` spacing.
    bbox_a/b are (minx, miny, maxx, maxy)."""
    axmin, aymin, axmax, aymax = bbox_a
    bxmin, bymin, bxmax, bymax = bbox_b
    return (axmax + margin < bxmin - EPS or bxmax + margin < axmin - EPS or
            aymax + margin < bymin - EPS or bymax + margin < aymin - EPS)
