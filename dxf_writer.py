"""
dxf_writer.py
-------------
Hand-rolled, dependency-free DXF (R12 / AC1009) writer.

Only what's needed for nesting output: closed POLYLINE entities (one per
part, plus one for the sheet boundary), each on its own layer. R12 POLYLINE
is the most universally-supported DXF entity -- FreeCAD, LibreCAD, every
CAM package, and every laser/plasma controller reads it without issue.

Optional microjoints (see microjoints.py): when enabled, a part's outer
contour is written as several OPEN polylines with small gaps instead of one
closed one -- `_polyline_entity()`'s `closed=False` already covers this, no
new DXF entity type needed.

Optional part labeling: a plain TEXT entity (part name) at each part's
bounding-box center, on its own `<part>_LABEL` layer -- separate from the
cut layer so a laser/plasma controller can route it to a distinct
low-power engrave pass instead of cutting it.

Optional common-line cutting (see commonline.py): when two placed parts on
the sheet share a full physical edge, that edge is cut once (on a
'COMMON_CUT' layer) instead of once per part. Does not combine with
microjoints in this version -- see commonline.py's module docstring.
"""

from microjoints import add_tabs
from commonline import find_shared_edges, split_at_shared_edges


def _polyline_entity(points, layer, closed=True):
    lines = [
        "0", "POLYLINE",
        "8", layer,
        "66", "1",
        "70", "1" if closed else "0",
    ]
    for x, y in points:
        lines += ["0", "VERTEX", "8", layer, "10", f"{x:.6f}", "20", f"{y:.6f}"]
    lines += ["0", "SEQEND"]
    return lines


def _text_entity(x, y, height, text, layer):
    return ["0", "TEXT", "8", layer, "10", f"{x:.6f}", "20", f"{y:.6f}", "40", f"{height:.6f}", "1", text]


def write_dxf(path, parts, sheet_w=None, sheet_h=None, microjoints=None, labels=None,
              common_line_cutting=False):
    """
    parts: list of (layer_name, outer_points, hole_loops) tuples, one per
           part, where outer_points/hole_loops are lists of (x, y) in sheet
           coordinates. hole_loops may be an empty list.
    sheet_w/h: optional -- if given, draws the sheet boundary rectangle on
               layer 'SHEET_BOUNDARY'.
    microjoints: optional dict of kwargs forwarded to
                 microjoints.add_tabs() (tab_width, target_spacing, etc.) --
                 when given, each part's OUTER contour is written as several
                 open polylines with small uncut gaps instead of one closed
                 one. None (the default) writes exactly like before. Holes
                 are never tabbed -- a hole's cutout is scrap, not a part
                 that needs to stay attached to anything.
    labels: optional dict, e.g. {"height": 5.0} -- when given, each part's
            layer name is also written as a TEXT entity at that part's
            bounding-box center, on a separate `<layer>_LABEL` layer, so a
            laser/plasma controller can route it to a low-power engrave
            pass instead of cutting it. None (the default) omits labels
            entirely, same as before this option existed.
    common_line_cutting: when True, a full edge shared by two parts on
                          this sheet is cut once (on layer 'COMMON_CUT')
                          instead of once per part -- see commonline.py for
                          the exact-full-edge-match scope this covers.
                          Ignored (treated as off) whenever `microjoints`
                          is also given -- the two don't combine in this
                          version, see commonline.py's module docstring.
    """
    lines = ["0", "SECTION", "2", "ENTITIES"]

    if sheet_w is not None and sheet_h is not None:
        boundary = [(0, 0), (sheet_w, 0), (sheet_w, sheet_h), (0, sheet_h)]
        lines += _polyline_entity(boundary, "SHEET_BOUNDARY")

    use_common_line = common_line_cutting and microjoints is None
    shared_by_part, merged_edges = (
        find_shared_edges([outer for _layer, outer, _holes in parts]) if use_common_line else ({}, [])
    )

    for idx, (layer, outer, holes) in enumerate(parts):
        if microjoints is not None:
            for segment in add_tabs(outer, **microjoints):
                lines += _polyline_entity(segment, layer, closed=False)
        elif use_common_line and idx in shared_by_part:
            for segment in split_at_shared_edges(outer, shared_by_part[idx]):
                lines += _polyline_entity(segment, layer, closed=False)
        else:
            lines += _polyline_entity(outer, layer)
        for hole in holes:
            lines += _polyline_entity(hole, f"{layer}_HOLE")
        if labels is not None:
            xs = [p[0] for p in outer]
            ys = [p[1] for p in outer]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            lines += _text_entity(cx, cy, labels.get("height", 5.0), layer, f"{layer}_LABEL")

    if use_common_line:
        for p1, p2 in merged_edges:
            lines += _polyline_entity([p1, p2], "COMMON_CUT", closed=False)

    lines += ["0", "ENDSEC", "0", "EOF"]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
