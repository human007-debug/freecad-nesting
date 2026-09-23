"""
dxf_extract.py
---------------
Plain python3, no FreeCAD needed -- extracts nestable parts directly from a
DXF file (a flat 2D drawing) into the same JSON shape freecad_extract.py
produces, so part_import.py loads either one identically.

Unlike freecad_extract.py, this needs no bend-unfolding: a DXF flat
pattern is already flat by definition (it's a 2D drawing), so the only job
here is recognizing which entities form which part's outer contour and
which form its holes. Uses `ezdxf` (a well-maintained, pure-Python DXF
library) rather than hand-rolling a parser -- DXF's arc-in-polyline
"bulge" encoding is exactly the kind of fiddly geometry math (like
Minkowski sums, see nfp.py) where a battle-tested library beats a
hand-rolled one; getting it silently wrong would mean silently wrong part
geometry.

HOW PARTS ARE RECOGNIZED (no reliance on layer-naming conventions, since
those vary shop to shop): every closed loop the file's geometry entities
form -- already-closed entities (a closed LWPOLYLINE/POLYLINE, a CIRCLE,
a full-circle ELLIPSE) directly, plus open entities (LINE/ARC/open
polylines/splines) chained end-to-end wherever their endpoints meet within
`--chain-tolerance` -- is one loop. Loops are then nested by point-in-
polygon containment: a loop with no parent is a new part's outer contour;
everything nested one or more levels inside it becomes one of that part's
holes (this project's Part model is flat outer+holes, so a hole-within-a-
hole -- rare for sheet metal -- collapses to "just another hole of the
same part" rather than modeling a third level).

Curved entities (ARC, CIRCLE, ELLIPSE, SPLINE, and bulged polyline
segments) are flattened to straight-edge polygons via ezdxf's own
`ezdxf.path` machinery (the same "let the library handle the fiddly
curve math" choice as above) at `--tolerance` mm sagitta -- consistent
with how freecad_extract.py discretizes arcs from FreeCAD geometry.

WHAT'S IGNORED: non-geometry entities (TEXT, MTEXT, DIMENSION, HATCH,
leaders, points) and anything on a frozen/off layer -- these are
annotation, not material. INSERT (block reference) entities are exploded
into their constituent geometry via ezdxf's own `virtual_entities()`,
one level; a block that itself references another block (nested blocks)
is not recursively exploded -- rare in practice for flat nesting exports,
which are usually already just flat geometry.

MATERIAL, THICKNESS: neither exists in a DXF at all -- it's a 2D drawing
with no notion of the sheet it'll be cut from. Both are CLI overrides only
(`material:<name>=<value>` etc., matching freecad_extract.py's
convention), keyed by the detected part name.

QUANTITY: a DXF has no explicit quantity field either, but an assembly's
flat-pattern export commonly draws the same part's outline several times
(once per placement) -- `merge_duplicate_parts()` collapses those repeats
by a placement-invariant shape signature (area/perimeter/hole areas, same
idea as freecad_extract.py's `merge_duplicate_instances()`) into one part
at quantity = the repeat count, before naming ever runs. A `--qty`
override still wins over the inferred count.

DWG: not read directly -- there's no reasonable pure-Python way to parse
Autodesk's proprietary binary format. Convert DWG to DXF first (the free
ODA File Converter -- https://www.opendesign.com/guestfiles/oda_file_converter
-- is the standard tool for this, a standalone batch converter, not a
Python library) and run this on the resulting DXF.
"""

import argparse
import json
import os
import sys

import ezdxf
import ezdxf.path

from geometry import point_in_polygon, polygon_area, polygon_bbox, polygon_perimeter


GEOMETRY_TYPES = {
    "LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE",
}


def _entity_layer_visible(entity, doc):
    layer_name = entity.dxf.layer
    try:
        layer = doc.layers.get(layer_name)
    except Exception:
        return True
    if layer is None:
        return True
    return not (layer.is_off() or layer.is_frozen())


def _iter_geometry_entities(doc, msp):
    """Modelspace geometry entities, with one level of INSERT (block
    reference) explosion -- annotation/text/dimension/hatch entities and
    anything on a frozen/off layer are skipped."""
    for entity in msp:
        if not _entity_layer_visible(entity, doc):
            continue
        etype = entity.dxftype()
        if etype in GEOMETRY_TYPES:
            yield entity
        elif etype == "INSERT":
            try:
                for sub in entity.virtual_entities():
                    if sub.dxftype() in GEOMETRY_TYPES:
                        yield sub
            except Exception:
                continue


def _flatten_entity(entity, tol):
    """A geometry entity -> list of (x, y) points along it, via ezdxf's
    own curve-flattening (handles bulged polylines, arcs, circles,
    ellipses, splines uniformly -- see module docstring)."""
    path = ezdxf.path.make_path(entity)
    return [(p.x, p.y) for p in path.flattening(distance=tol)]


def _is_closed(points, tol):
    if len(points) < 3:
        return False
    (x0, y0), (x1, y1) = points[0], points[-1]
    return abs(x0 - x1) <= tol and abs(y0 - y1) <= tol


def _dedupe_consecutive(points, tol):
    out = [points[0]]
    for p in points[1:]:
        if abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    return out


def _chain_open_segments(segments, tol):
    """Chain open polylines end-to-end wherever endpoints meet within
    `tol`, greedily, until each chain closes or runs out of connections
    (an unclosed chain is dropped with a warning -- nesting needs closed
    contours). `segments` is a list of (points, layer_name) tuples (each
    already-flattened, still open); returns (loops, warnings) where each
    loop is (points, {layer_names contributing to it})."""
    remaining = list(segments)
    loops = []
    warnings = []

    def endpoints_match(a, b):
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    while remaining:
        chain, layer = remaining.pop(0)
        chain = list(chain)
        layers = {layer}
        progressed = True
        while progressed and not _is_closed(chain, tol):
            progressed = False
            for i, (seg, seg_layer) in enumerate(remaining):
                if endpoints_match(chain[-1], seg[0]):
                    chain.extend(seg[1:])
                elif endpoints_match(chain[-1], seg[-1]):
                    chain.extend(list(reversed(seg))[1:])
                elif endpoints_match(chain[0], seg[-1]):
                    chain = seg[:-1] + chain
                elif endpoints_match(chain[0], seg[0]):
                    chain = list(reversed(seg))[:-1] + chain
                else:
                    continue
                layers.add(seg_layer)
                remaining.pop(i)
                progressed = True
                break
        if _is_closed(chain, tol):
            loops.append((_dedupe_consecutive(chain[:-1], tol), layers))
        else:
            warnings.append(
                f"dropped an unclosed chain of {len(chain)} point(s) starting near "
                f"({chain[0][0]:.2f}, {chain[0][1]:.2f}) -- gap > {tol}mm somewhere"
            )
    return loops, warnings


def extract_loops(doc, msp, tol, chain_tol):
    """All closed loops in the drawing, from both already-closed entities
    and open ones chained together. Returns (loops, warnings), where each
    loop is (points, {layer_names of the entities that formed it}) -- used
    to name a part after its DXF layer when that's unambiguous (see
    group_into_parts / main). `tol` is the curve-flattening sagitta;
    `chain_tol` is the separate, normally much tighter, endpoint-matching
    distance used both to recognize an entity's own start/end as already
    coincident and to chain open entities into closed loops -- conflating
    the two would mean either over-coarse flattening making false "closed"
    detections, or an endpoint tolerance so tight legitimate chains never
    connect."""
    closed_loops = []
    open_segments = []
    warnings = []

    for entity in _iter_geometry_entities(doc, msp):
        try:
            pts = _flatten_entity(entity, tol)
        except Exception as e:
            warnings.append(f"skipped a {entity.dxftype()} entity: {e}")
            continue
        if len(pts) < 2:
            continue
        etype = entity.dxftype()
        already_closed = etype in ("CIRCLE",) or (
            etype in ("LWPOLYLINE", "POLYLINE") and bool(entity.is_closed)
        )
        layer = entity.dxf.layer
        if already_closed or _is_closed(pts, chain_tol):
            pts = _dedupe_consecutive(pts[:-1] if _is_closed(pts, chain_tol) else pts, tol)
            closed_loops.append((pts, {layer}))
        else:
            open_segments.append((pts, layer))

    chained_loops, chain_warnings = _chain_open_segments(open_segments, chain_tol)
    warnings.extend(chain_warnings)
    all_loops = [
        (pts, layers) for pts, layers in closed_loops + chained_loops
        if len(pts) >= 3 and polygon_area(pts) > 1e-6
    ]
    return all_loops, warnings


def group_into_parts(loops):
    """Nest loops by point-in-polygon containment: a loop with no parent
    is a new part's outer contour; everything nested inside it (any depth)
    becomes one of that part's holes -- see module docstring for why depth
    collapses to a flat list. `loops` is a list of (points, layer_names)
    as returned by extract_loops(); each returned part also carries the
    union of its outer + holes' layer names, for naming (see main())."""
    points_only = [pts for pts, _layers in loops]
    n = len(points_only)
    bboxes = [polygon_bbox(l) for l in points_only]
    areas = [polygon_area(l) for l in points_only]

    def bbox_contains(a, b):
        return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]

    parents = [None] * n
    for i in range(n):
        best_parent = None
        best_area = None
        for j in range(n):
            if i == j or areas[j] <= areas[i]:
                continue
            if not bbox_contains(bboxes[j], bboxes[i]):
                continue
            if not point_in_polygon(points_only[i][0], points_only[j]):
                continue
            if best_area is None or areas[j] < best_area:
                best_area = areas[j]
                best_parent = j
        parents[i] = best_parent

    parts = []
    for i in range(n):
        if parents[i] is not None:
            continue  # not a root -- it's a hole of something, handled below
        hole_indices = [k for k in range(n) if k != i and _root_of(parents, k) == i]
        holes = [points_only[k] for k in hole_indices]
        layers = set(loops[i][1])
        for k in hole_indices:
            layers |= loops[k][1]
        parts.append({"outer": points_only[i], "holes": holes, "layers": layers})
    return parts


def _root_of(parents, i):
    seen = set()
    while parents[i] is not None and i not in seen:
        seen.add(i)
        i = parents[i]
    return i


def _shape_signature(part, ndigits=2):
    """Placement-invariant fingerprint for spotting repeated instances of
    the same physical part drawn several times in one DXF (e.g. a bracket's
    flat pattern repeated once per rib in an assembly export) -- area,
    perimeter, and sorted hole areas don't change with where the loop sits
    in the drawing, unlike its raw points or bounding box. Mirrors
    freecad_extract.py's merge_duplicate_instances()/`_shape_signature`."""
    area = round(polygon_area(part["outer"]), ndigits)
    perimeter = round(polygon_perimeter(part["outer"]), ndigits)
    hole_areas = tuple(sorted(round(polygon_area(h), ndigits) for h in part["holes"]))
    return (area, perimeter, len(part["holes"]), hole_areas)


def merge_duplicate_parts(parts):
    """Collapses parts sharing a `_shape_signature` into one entry each,
    with `quantity` = the group's instance count, instead of every repeated
    instance becoming its own part at quantity 1 -- see module docstring
    and `_shape_signature`. A group of one is returned unchanged (quantity
    1), so this is a no-op for a drawing with no repeated parts. Keeps the
    first-seen instance's outer/holes; unions the merged instances' layers
    (used for naming -- see main())."""
    groups = {}
    order = []
    for part in parts:
        sig = _shape_signature(part)
        if sig not in groups:
            merged = dict(part)
            merged["quantity"] = 1
            groups[sig] = merged
            order.append(sig)
        else:
            groups[sig]["quantity"] += 1
            groups[sig]["layers"] = groups[sig]["layers"] | part["layers"]
    return [groups[sig] for sig in order]


def extract_parts(path, tol=0.25, chain_tol=0.05):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    loops, warnings = extract_loops(doc, msp, tol, chain_tol)
    parts = group_into_parts(loops)
    parts = merge_duplicate_parts(parts)
    return parts, warnings


# ------------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract nestable parts from DXF file(s) into nester.py's Part JSON format."
    )
    parser.add_argument("dxf", nargs="+", help=".dxf files to scan (convert .dwg to .dxf first -- see module docstring)")
    parser.add_argument("-o", "--output", default="parts.json")
    parser.add_argument("--tolerance", type=float, default=0.25,
                         help="max sagitta (mm) when flattening arcs/circles/splines into straight edges")
    parser.add_argument("--chain-tolerance", type=float, default=0.05,
                         help="max gap (mm) between endpoints still considered 'connected' when chaining open entities into closed loops")
    parser.add_argument("--qty", action="append", default=[], metavar="NAME=N",
                         help="quantity override for a part by name; repeatable")
    parser.add_argument("--material", action="append", default=[], metavar="NAME=VALUE",
                         help="material tag for a part by name; repeatable")
    parser.add_argument("--thickness", action="append", default=[], metavar="NAME=MM",
                         help="thickness (mm) for a part by name -- a DXF has no thickness at all; repeatable")
    args = parser.parse_args(argv)

    qty_overrides = dict(item.split("=", 1) for item in args.qty)
    qty_overrides = {k: int(v) for k, v in qty_overrides.items()}
    material_overrides = dict(item.split("=", 1) for item in args.material)
    thickness_overrides = dict(item.split("=", 1) for item in args.thickness)
    thickness_overrides = {k: float(v) for k, v in thickness_overrides.items()}

    all_parts = []
    for path in args.dxf:
        base = os.path.splitext(os.path.basename(path))[0]
        try:
            parts, warnings = extract_parts(path, tol=args.tolerance, chain_tol=args.chain_tolerance)
        except Exception as e:
            print(f"[error] {path}: {e}", file=sys.stderr)
            continue
        for w in warnings:
            print(f"[warn] {path}: {w}", file=sys.stderr)
        if not parts:
            print(f"[warn] no closed parts found in {path}", file=sys.stderr)

        used_names = set()
        for i, p in enumerate(parts):
            # Name a part after its DXF layer when every entity forming it
            # (outer + holes) agrees on one non-default layer -- a common
            # shop convention, and much more useful for --qty/--material/
            # --thickness overrides than a generic "-partN" suffix. Falls
            # back to that suffix when layers disagree or are just "0".
            candidate_layers = p["layers"] - {"0"}
            if len(candidate_layers) == 1:
                name = next(iter(candidate_layers))
            else:
                name = f"{base}-part{i + 1}" if len(parts) > 1 else base
            if name in used_names:
                suffix = 2
                while f"{name}-{suffix}" in used_names:
                    suffix += 1
                name = f"{name}-{suffix}"
            used_names.add(name)

            all_parts.append({
                "name": name,
                "source_file": os.path.basename(path),
                "points": p["outer"],
                "holes": p["holes"],
                "quantity": qty_overrides.get(name, p.get("quantity", 1)),
                "material": material_overrides.get(name),
                "thickness": thickness_overrides.get(name),
                "method": "dxf",
            })
            qty_note = f", merged from {p['quantity']} identical instances" if p.get("quantity", 1) > 1 else ""
            print(f"[ok] {name}: {len(p['outer'])} outer pts, {len(p['holes'])} hole(s){qty_note}")

    with open(args.output, "w") as f:
        json.dump({"parts": all_parts}, f, indent=2)
    print(f"Wrote {len(all_parts)} part(s) to {args.output}")


if __name__ == "__main__":
    main()
