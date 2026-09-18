"""
commonline.py
-------------
Detects when two placed parts on the same sheet share a physical cut edge
(their outer contours touch along a full edge, endpoint to endpoint) and
merges it into a single cut line instead of cutting the same line twice --
a real technique real nesting software uses to save cut time and kerf loss
on tightly-packed, especially rectangular-grid, layouts.

HONEST SCOPE: this only detects and merges EXACT full-edge matches -- both
endpoints of one part's edge coincide (within a small rounding tolerance)
with both endpoints of another part's edge, in either direction. A
*partial* overlap (e.g. part B's shorter edge only covers part of part A's
longer edge) is NOT split and merged -- that would need edge-splitting
this module doesn't attempt, so those cases safely fall back to being cut
twice, exactly like today. This still covers the common case in practice:
rectangular (or straight-edge) parts pushed flush against each other,
which is exactly what NFP-exact bottom-left placement often produces for
grid-friendly layouts, and exactly where common-line cutting matters most
in real shops.

Does not compose with microjoints in this version (see dxf_writer.py) --
combining "cut gaps for tabs" and "skip shared edges" on the same contour
at once is a real feature but a materially larger one; kept as two
independent, mutually-exclusive options for now rather than half-solving
their interaction.
"""


def _edge_key(p1, p2, decimals=3):
    """A direction-independent, tolerance-quantized key for an edge, so
    two edges that coincide (in either winding direction, which is what
    two touching polygons' shared boundary actually looks like -- they
    trace it in opposite directions) hash to the same key."""
    a = (round(p1[0], decimals), round(p1[1], decimals))
    b = (round(p2[0], decimals), round(p2[1], decimals))
    return tuple(sorted([a, b]))


def find_shared_edges(outers):
    """outers: list of outer-contour point lists, one per placed part on
    one sheet. Returns (shared_by_part, merged_edges):
      shared_by_part: {part_index: set(edge_index)} -- which edges of each
        part's own contour are shared with another part's, to be excluded
        from that part's own cut path.
      merged_edges: list of (p1, p2) -- the physical shared lines, one
        entry per shared edge regardless of how many parts reference it,
        to be cut exactly once (on their own layer)."""
    groups = {}
    for pi, outer in enumerate(outers):
        n = len(outer)
        for i in range(n):
            p1, p2 = outer[i], outer[(i + 1) % n]
            groups.setdefault(_edge_key(p1, p2), []).append((pi, i, p1, p2))

    shared_by_part = {}
    merged_edges = []
    for entries in groups.values():
        if len(entries) < 2:
            continue
        for pi, ei, _p1, _p2 in entries:
            shared_by_part.setdefault(pi, set()).add(ei)
        _pi0, _ei0, p1_0, p2_0 = entries[0]
        merged_edges.append((p1_0, p2_0))
    return shared_by_part, merged_edges


def split_at_shared_edges(outer, shared_edge_indices):
    """outer: a closed contour's point list (last point implicitly
    connects back to the first, same convention as microjoints.py/
    nester.py/dxf_writer.py). shared_edge_indices: edge-start indices to
    exclude from this part's own cut path. Returns a list of open
    polylines covering everything else -- same wrap-around stitch idea as
    microjoints.add_tabs() (rotate to start right after a shared edge, so
    there's no wraparound segment left to stitch back together)."""
    n = len(outer)
    if not shared_edge_indices:
        return [list(outer)]

    start_edge = min(shared_edge_indices)
    start_vertex = (start_edge + 1) % n
    rotated = [outer[(start_vertex + k) % n] for k in range(n)]
    rotated_shared = {k for k in range(n) if (start_vertex + k) % n in shared_edge_indices}

    segments = []
    current = [rotated[0]]
    for k in range(n - 1):  # edge k = (rotated[k], rotated[k+1]); the wrap edge (k=n-1) is start_edge itself, already accounted for by starting here
        if k in rotated_shared:
            segments.append(current)
            current = [rotated[k + 1]]
        else:
            current.append(rotated[k + 1])
    if len(current) >= 2:
        segments.append(current)
    return [s for s in segments if len(s) >= 2]
