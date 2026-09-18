# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""
geometry_fast.pyx
------------------
Compiled (Cython) drop-in replacement for geometry.py's two hottest
functions, `polygons_overlap`/`polygons_min_distance` -- see geometry.py's
own module docstring for why: profiling showed these costing 84% of
nester.py's total nesting time, in a scalar segment-intersection/distance
loop called millions of times over on small (4-16 vertex) polygons.

WHY CYTHON, NOT NUMPY: a numpy-vectorized rewrite of the exact same
algorithm was tried first and measured SLOWER end-to-end (see geometry.py's
"TRIED AND REVERTED" note) -- these polygons are too small for
vectorization's per-call array-allocation overhead to pay off. What
actually costs the time is CPython's per-operation interpreter overhead in
a tight scalar loop (millions of individual float compares/multiplies, each
paying Python bytecode dispatch cost) -- exactly what compiling the same
scalar algorithm to real machine code removes, without changing the
algorithm or its exactness at all.

SAME ALGORITHM, NOT A DIFFERENT ONE: every function here is a direct,
line-for-line port of geometry.py's scalar Python -- same orientation test,
same on-segment bounding-box check, same point-to-segment-distance formula,
same EPS. This is deliberate: the point is to remove interpreter overhead,
not to change what's being computed. geometry.py's own correctness tests
(see the polygons_overlap/polygons_min_distance docstrings) apply
unchanged.

INPUT CONVERSION: polygons arrive as Python lists of (x, y) tuples (this
project's universal polygon representation -- see geometry.py). Each
polygon is copied into a flat C `double*` buffer ONCE per call (O(n) Python
list access), after which the nested edge-pair loop runs entirely in C
(`nogil`-able, no Python object overhead per comparison) -- unlike a naive
Cython port that would still call back into `poly[i]` (a Python tuple
unpack) on every inner-loop iteration.

OPTIONAL BY DESIGN: this module is a compiled accelerator, not a hard
dependency -- geometry.py imports it in a try/except and falls back to its
own pure-Python implementation if it isn't built (no C compiler available,
or nobody ran the build step yet). This preserves the "runs anywhere"
guarantee pure-Python geometry.py has always made; see README's build
instructions for how to compile this.
"""

from libc.math cimport fabs, hypot, sqrt
from libc.stdlib cimport malloc, free

cdef double EPS = 1e-9


# --------------------------------------------------------------- internals

cdef inline int _orientation(double px, double py, double qx, double qy,
                              double rx, double ry) nogil:
    cdef double val = (qy - py) * (rx - qx) - (qx - px) * (ry - qy)
    if fabs(val) < EPS:
        return 0
    return 1 if val > 0 else 2


cdef inline bint _on_segment(double px, double py, double qx, double qy,
                              double rx, double ry) nogil:
    cdef double minx = px if px < rx else rx
    cdef double maxx = px if px > rx else rx
    cdef double miny = py if py < ry else ry
    cdef double maxy = py if py > ry else ry
    return (qx >= minx - EPS and qx <= maxx + EPS and
            qy >= miny - EPS and qy <= maxy + EPS)


cdef inline bint _segments_intersect(double p1x, double p1y, double p2x, double p2y,
                                      double p3x, double p3y, double p4x, double p4y) nogil:
    cdef int o1 = _orientation(p1x, p1y, p2x, p2y, p3x, p3y)
    cdef int o2 = _orientation(p1x, p1y, p2x, p2y, p4x, p4y)
    cdef int o3 = _orientation(p3x, p3y, p4x, p4y, p1x, p1y)
    cdef int o4 = _orientation(p3x, p3y, p4x, p4y, p2x, p2y)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1x, p1y, p3x, p3y, p2x, p2y):
        return True
    if o2 == 0 and _on_segment(p1x, p1y, p4x, p4y, p2x, p2y):
        return True
    if o3 == 0 and _on_segment(p3x, p3y, p1x, p1y, p4x, p4y):
        return True
    if o4 == 0 and _on_segment(p3x, p3y, p2x, p2y, p4x, p4y):
        return True
    return False


cdef inline double _point_seg_distance(double px, double py,
                                        double ax, double ay,
                                        double bx, double by) nogil:
    cdef double dx = bx - ax
    cdef double dy = by - ay
    cdef double length_sq = dx * dx + dy * dy
    cdef double t, cx, cy
    if length_sq < EPS:
        return hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx = ax + t * dx
    cy = ay + t * dy
    return hypot(px - cx, py - cy)


cdef inline double _seg_seg_distance(double a1x, double a1y, double a2x, double a2y,
                                      double b1x, double b1y, double b2x, double b2y) nogil:
    if _segments_intersect(a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y):
        return 0.0
    cdef double d1 = _point_seg_distance(a1x, a1y, b1x, b1y, b2x, b2y)
    cdef double d2 = _point_seg_distance(a2x, a2y, b1x, b1y, b2x, b2y)
    cdef double d3 = _point_seg_distance(b1x, b1y, a1x, a1y, a2x, a2y)
    cdef double d4 = _point_seg_distance(b2x, b2y, a1x, a1y, a2x, a2y)
    cdef double best = d1
    if d2 < best:
        best = d2
    if d3 < best:
        best = d3
    if d4 < best:
        best = d4
    return best


cdef double* _to_c_array(poly, int n) except NULL:
    """Flattens a Python list of (x, y) tuples into a malloc'd [x0,y0,x1,y1,...]
    C double array -- caller must free() it. The only place this module
    touches Python objects per-point; everything downstream is pure C."""
    cdef double* buf = <double*> malloc(2 * n * sizeof(double))
    if buf == NULL:
        raise MemoryError()
    cdef int i
    for i in range(n):
        x, y = poly[i]
        buf[2 * i] = x
        buf[2 * i + 1] = y
    return buf


cdef bint _point_in_polygon_c(double x, double y, double* poly, int n) nogil:
    cdef bint inside = False
    cdef int i, j = n - 1
    cdef double xi, yi, xj, yj, x_intersect
    for i in range(n):
        xi = poly[2 * i]
        yi = poly[2 * i + 1]
        xj = poly[2 * j]
        yj = poly[2 * j + 1]
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / ((yj - yi) if fabs(yj - yi) > EPS else EPS) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


cdef bint _polygons_overlap_c(double* A, int na, double* B, int nb) nogil:
    cdef int i, j
    cdef double a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y
    for i in range(na):
        a1x = A[2 * i]; a1y = A[2 * i + 1]
        a2x = A[2 * ((i + 1) % na)]; a2y = A[2 * ((i + 1) % na) + 1]
        for j in range(nb):
            b1x = B[2 * j]; b1y = B[2 * j + 1]
            b2x = B[2 * ((j + 1) % nb)]; b2y = B[2 * ((j + 1) % nb) + 1]
            if _segments_intersect(a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y):
                return True
    if _point_in_polygon_c(A[0], A[1], B, nb):
        return True
    if _point_in_polygon_c(B[0], B[1], A, na):
        return True
    return False


# ---------------------------------------------------------------- public API

def polygons_overlap(poly_a, poly_b):
    """Exact overlap test for two simple polygons -- same semantics as
    geometry.polygons_overlap (true if edges cross, or one is fully
    contained inside the other)."""
    cdef int na = len(poly_a)
    cdef int nb = len(poly_b)
    cdef double* A = _to_c_array(poly_a, na)
    cdef double* B
    cdef bint result
    try:
        B = _to_c_array(poly_b, nb)
        try:
            result = _polygons_overlap_c(A, na, B, nb)
        finally:
            free(B)
    finally:
        free(A)
    return result


def polygons_min_distance(poly_a, poly_b):
    """Exact minimum distance between two polygon boundaries -- same
    semantics as geometry.polygons_min_distance (0.0 if they overlap)."""
    cdef int na = len(poly_a)
    cdef int nb = len(poly_b)
    cdef double* A = _to_c_array(poly_a, na)
    cdef double* B
    cdef int i, j
    cdef double a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y, d
    cdef double best
    try:
        B = _to_c_array(poly_b, nb)
        try:
            if _polygons_overlap_c(A, na, B, nb):
                return 0.0
            best = 1e308
            for i in range(na):
                a1x = A[2 * i]; a1y = A[2 * i + 1]
                a2x = A[2 * ((i + 1) % na)]; a2y = A[2 * ((i + 1) % na) + 1]
                for j in range(nb):
                    b1x = B[2 * j]; b1y = B[2 * j + 1]
                    b2x = B[2 * ((j + 1) % nb)]; b2y = B[2 * ((j + 1) % nb) + 1]
                    d = _seg_seg_distance(a1x, a1y, a2x, a2y, b1x, b1y, b2x, b2y)
                    if d < best:
                        best = d
            return best
        finally:
            free(B)
    finally:
        free(A)
