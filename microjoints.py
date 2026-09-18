"""
microjoints.py
--------------
Splits a part's outer cut contour into several open polylines with small
gaps left uncut at evenly spaced points around the perimeter -- "microjoints"
or "tabs" in shop terms. Without them, a fully-cut part (especially a small
one, or one deep inside a dense nest) can shift, drop into the machine bed,
or scorch on a finished edge before the rest of the sheet is done cutting;
a few small uncut bridges hold it in place until it's manually snapped free.

Only meant for a part's OUTER contour -- holes are left closed. A hole's
cutout is scrap, not a part that needs to stay attached to anything.

Pure geometry, no dependencies (same spirit as geometry.py): walk the
polygon's cumulative arc length, decide how many tabs fit, and cut gaps at
evenly spaced points.
"""

import math


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _cumulative_lengths(points):
    """cum[i] = arc length from points[0] to points[i] walking forward;
    total = full perimeter including the closing edge points[-1]->points[0]."""
    n = len(points)
    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + _dist(points[i - 1], points[i])
    total = cum[-1] + _dist(points[-1], points[0])
    return cum, total


def _point_at_arclength(points, cum, total, s):
    n = len(points)
    s = s % total
    for i in range(n):
        seg_start = cum[i]
        seg_end = cum[i + 1] if i + 1 < n else total
        if s <= seg_end + 1e-9:
            seg_len = seg_end - seg_start
            t = 0.0 if seg_len < 1e-12 else (s - seg_start) / seg_len
            p0 = points[i]
            p1 = points[(i + 1) % n]
            return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))
    return points[-1]


def add_tabs(points, tab_width=1.5, min_tabs=2, max_tabs=6, target_spacing=150.0):
    """Splits a closed contour (`points`, last point implicitly connects
    back to the first -- same convention nester.py/dxf_writer.py use) into a
    list of open polylines with `tab_width`-mm gaps left uncut at evenly
    spaced points around the perimeter.

    Number of tabs is `perimeter / target_spacing`, clamped to
    [min_tabs, max_tabs]. Tab centers start at half the tab spacing rather
    than at arc-length 0, so no gap ever straddles the seam where the walk
    wraps back to the start vertex -- that keeps the wrap-around stitch
    below simple instead of needing a special case for it.

    Falls back to `[points]` unchanged if the perimeter is too short for
    tabs to make sense (they'd consume more of the edge than they're
    meant to protect)."""
    n = len(points)
    if n < 3:
        return [list(points)]

    cum, total = _cumulative_lengths(points)
    num_tabs = max(min_tabs, min(max_tabs, round(total / target_spacing)))
    if num_tabs <= 0 or total < num_tabs * tab_width * 3:
        return [list(points)]

    events = [(cum[i], points[i], "vertex") for i in range(n)]
    for k in range(num_tabs):
        center = (k + 0.5) * total / num_tabs
        start_s = center - tab_width / 2
        end_s = center + tab_width / 2
        events.append((start_s % total, _point_at_arclength(points, cum, total, start_s), "start"))
        events.append((end_s % total, _point_at_arclength(points, cum, total, end_s), "end"))
    # At an exact tie, process start/end before a coincident vertex, so a
    # vertex that happens to land exactly on a gap boundary is excluded.
    events.sort(key=lambda e: (e[0], e[2] != "vertex"))

    segments = []
    current = []
    in_gap = False
    for _s, p, kind in events:
        if kind == "start":
            # Include the point right up to the tear before closing the
            # segment off -- otherwise the last stretch before every gap
            # (roughly half of each segment's real length) gets silently
            # dropped.
            current.append(p)
            segments.append(current)
            current = []
            in_gap = True
        elif kind == "end":
            in_gap = False
            current = [p]
        elif not in_gap:
            current.append(p)
    if current:
        segments.append(current)

    # The walk covers arc-length [0, total) linearly, but the contour is
    # actually closed: the piece before the first gap and the piece after
    # the last gap are the same continuous cut arc, meeting at vertex 0 --
    # stitch them into one segment.
    if len(segments) > 1:
        segments[0] = segments.pop() + segments[0]

    return [seg for seg in segments if len(seg) >= 2]
