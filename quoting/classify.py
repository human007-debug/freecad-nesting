"""
quoting/classify.py
-------------------
Phase 2b.1: sort each solid into a part type -- sheet, tube, section,
machined or purchased -- and match a constant cross-section against the
profile library.

The FreeCAD side (what the solid IS: a SheetMetal object, a flat plate, a
Fasteners-workbench object, a straight extrusion and its section) is
reduced to a plain `SolidInfo` record; everything here is pure Python and
works on that record, so it's testable without FreeCAD and the same rules
apply whatever produced the record.

Rules, first match wins (docs/QUOTING_PLAN.md 2b.1):
  1. an explicit user override;
  2. purchased -- a Fasteners-workbench object, or a name matching the
     bought-in parts list (case-insensitive, shell-style wildcards);
  3. sheet -- a SheetMetal object or a flat plate;
  4. tube / section -- a straight extrusion whose section matches the
     profile library (hollow -> tube, open/solid -> section);
  5. machined -- anything else.
Holes, threads and pockets don't change rules 1-4: a laser-cut plate with
tapped holes is still a sheet part; its tapping is costed by
ops_machining against the part's `hole_features` whatever its type.

PROFILE MATCHING (`match_profile`) works from the section's outer loop and
hole loops alone:
  - one circular hole in a circular outer -> CHS; a circle with no hole ->
    ROUND bar;
  - a rectangle (fills its bounding box) with one rectangular hole -> RHS,
    or SHS when square; a rectangle with no hole -> FLAT bar;
  - otherwise the section is probed at a few points just inside its
    bounding box (the four corners, mid-sides and centre) -- which of those
    lie in material tells an angle from a channel from an I-beam from a
    tee without needing vertex-exact geometry, so radiused roots and toes
    don't matter. A shape that passes the probes but tapers (scan lines
    across it vary continuously, unlike constant-thickness legs and webs)
    is still OTHER.
"""

import fnmatch
import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from geometry import point_in_polygon, polygon_area, polygon_bbox, polygon_perimeter
from quoting.features import PART_TYPES, ProfileInfo, fit_circle

Point = Tuple[float, float]


@dataclass
class SolidInfo:
    """What the CAD side knows about one solid, reduced to plain data."""
    name: str
    is_sheetmetal: bool = False
    is_flat_plate: bool = False
    is_fastener: bool = False
    # A straight extrusion with a constant cross-section: its section loops
    # (in the section's own 2D frame) and length. None = not an extrusion.
    section_outer: Optional[List[Point]] = None
    section_holes: List[List[Point]] = field(default_factory=list)
    length: Optional[float] = None
    end_cuts: Tuple[str, str] = ("square", "square")

    @classmethod
    def from_dict(cls, d: dict) -> "SolidInfo":
        return cls(
            name=d["name"],
            is_sheetmetal=bool(d.get("is_sheetmetal", False)),
            is_flat_plate=bool(d.get("is_flat_plate", False)),
            is_fastener=bool(d.get("is_fastener", False)),
            section_outer=[tuple(p) for p in d["section_outer"]] if d.get("section_outer") else None,
            section_holes=[[tuple(p) for p in h] for h in d.get("section_holes", [])],
            length=d.get("length"),
            end_cuts=tuple(d.get("end_cuts", ("square", "square"))),
        )


@dataclass
class Classification:
    part_type: str
    reason: str
    profile: Optional[ProfileInfo] = None


def matches_purchased(name: str, purchased_names: Iterable[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pat.lower()) for pat in purchased_names)


def classify(info: SolidInfo, purchased_names: Iterable[str] = (),
             override: Optional[str] = None) -> Classification:
    if override is not None:
        if override not in PART_TYPES:
            raise ValueError(f"override must be one of {PART_TYPES}, got {override!r}")
        profile = _profile_of(info) if override in ("tube", "section") else None
        return Classification(override, "user override", profile)
    if info.is_fastener:
        return Classification("purchased", "Fasteners-workbench object")
    if matches_purchased(info.name, purchased_names):
        return Classification("purchased", "name matches the bought-in parts list")
    if info.is_sheetmetal:
        return Classification("sheet", "SheetMetal object")
    if info.is_flat_plate:
        return Classification("sheet", "flat plate")
    profile = _profile_of(info)
    if profile is not None and profile.kind != "OTHER":
        kind = "tube" if profile.hollow else "section"
        return Classification(kind, f"constant {profile.designation} section", profile)
    reason = "extrusion with an unrecognised section" if profile is not None else "not sheet, tube or section"
    return Classification("machined", reason, profile)


def _profile_of(info: SolidInfo) -> Optional[ProfileInfo]:
    if not info.section_outer:
        return None
    return match_profile(info.section_outer, info.section_holes)


# ------------------------------------------------------------ profile match

def _is_rectangle(loop: Sequence[Point], tol: float = 0.03) -> bool:
    """Fills its bounding box (to within `tol`, allowing radiused corners)."""
    minx, miny, maxx, maxy = polygon_bbox(list(loop))
    box = (maxx - minx) * (maxy - miny)
    return box > 0 and polygon_area(list(loop)) >= (1 - tol) * box


def _round(v: float, ndigits: int = 1) -> float:
    r = round(v, ndigits)
    return float(int(r)) if r == int(r) else r


def match_profile(outer: Sequence[Point], holes: Sequence[Sequence[Point]] = ()) -> ProfileInfo:
    outer = list(outer)
    holes = [list(h) for h in holes]
    area = polygon_area(outer) - sum(polygon_area(h) for h in holes)
    perimeter = polygon_perimeter(outer)
    minx, miny, maxx, maxy = polygon_bbox(outer)
    w, h = maxx - minx, maxy - miny

    circle = fit_circle(outer)
    if circle is not None:
        d = 2 * circle[2]
        perimeter = math.pi * d
        if not holes:
            return ProfileInfo("ROUND", _round(d), _round(d), None, area, perimeter)
        if len(holes) == 1:
            inner = fit_circle(holes[0])
            if inner is not None:
                wall = circle[2] - inner[2]
                return ProfileInfo("CHS", _round(d), _round(d), _round(wall), area, perimeter)
        return ProfileInfo("OTHER", w, h, None, area, perimeter)

    if _is_rectangle(outer):
        if not holes:
            # Flat bar: width x thickness, the larger dimension first.
            big, small = max(w, h), min(w, h)
            return ProfileInfo("FLAT", _round(big), _round(small), _round(small), area, perimeter)
        if len(holes) == 1 and _is_rectangle(holes[0]):
            hx0, hy0, hx1, hy1 = polygon_bbox(holes[0])
            wall = min(hx0 - minx, maxx - hx1, hy0 - miny, maxy - hy1)
            kind = "SHS" if abs(w - h) <= 0.01 * max(w, h) else "RHS"
            big, small = max(w, h), min(w, h)
            return ProfileInfo(kind, _round(big), _round(small), _round(wall), area, perimeter)
        return ProfileInfo("OTHER", w, h, None, area, perimeter)

    if holes:
        return ProfileInfo("OTHER", w, h, None, area, perimeter)

    kind = _open_section_kind(outer, minx, miny, maxx, maxy)
    if kind != "OTHER" and not _is_built_from_rectangles(outer, minx, miny, maxx, maxy):
        kind = "OTHER"
    # Thin-walled estimate: area / (half the perimeter) is the plate
    # thickness of a section made of thin rectangles.
    wall = _round(2 * area / perimeter) if perimeter else None
    if kind == "OTHER":
        return ProfileInfo("OTHER", w, h, None, area, perimeter)
    # Larger dimension first (depth x flange, long leg x short leg), the
    # way section tables list them, whichever way round it was drawn.
    return ProfileInfo(kind, _round(max(w, h)), _round(min(w, h)), wall, area, perimeter)


def _chord(outer, horizontal: bool, at: float) -> float:
    """Total length of material along one scan line (y = at if
    `horizontal`, else x = at)."""
    xs = []
    n = len(outer)
    for i in range(n):
        (x1, y1), (x2, y2) = outer[i], outer[(i + 1) % n]
        if not horizontal:
            x1, y1, x2, y2 = y1, x1, y2, x2
        if (y1 > at) != (y2 > at):
            xs.append(x1 + (at - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return sum(xs[i + 1] - xs[i] for i in range(0, len(xs) - 1, 2))


def _is_built_from_rectangles(outer, minx, miny, maxx, maxy, samples=9, max_distinct=3) -> bool:
    """Rolled open sections are legs/flanges/webs of constant thickness, so
    scan lines across them take only a couple of distinct lengths (leg vs
    web). A tapering shape (a triangle, a gusset) gives a different length
    on every line."""
    for horizontal, lo, hi, span in ((True, miny, maxy, maxx - minx), (False, minx, maxx, maxy - miny)):
        tol = 0.02 * span
        seen = []
        for k in range(1, samples + 1):
            c = _chord(outer, horizontal, lo + (hi - lo) * k / (samples + 1))
            if all(abs(c - v) > tol for v in seen):
                seen.append(c)
        if len(seen) > max_distinct:
            return False
    return True


def _open_section_kind(outer, minx, miny, maxx, maxy) -> str:
    """Probe which characteristic points of the bounding box are in
    material. Probes sit 1% in from the edges so a leg exactly on the
    bounding box still counts."""
    w, h = maxx - minx, maxy - miny
    e = 0.01

    def inside(fx, fy):
        return point_in_polygon((minx + fx * w, miny + fy * h), outer)

    corners = {
        "bl": inside(e, e), "br": inside(1 - e, e),
        "tl": inside(e, 1 - e), "tr": inside(1 - e, 1 - e),
    }
    mid = {
        "l": inside(e, 0.5), "r": inside(1 - e, 0.5),
        "b": inside(0.5, e), "t": inside(0.5, 1 - e),
    }
    centre = inside(0.5, 0.5)
    n_corners = sum(corners.values())

    if n_corners == 3 and not centre:
        return "ANGLE"
    if n_corners == 4:
        if centre and not mid["l"] and not mid["r"]:
            return "IBEAM"          # web vertical, flanges top and bottom
        if centre and not mid["t"] and not mid["b"]:
            return "IBEAM"          # same, drawn on its side
        if not centre and (mid["l"] != mid["r"]):
            return "CHANNEL"        # web on one side, open on the other
        if not centre and (mid["t"] != mid["b"]):
            return "CHANNEL"
    if n_corners == 2 and centre:
        pairs = [("tl", "tr"), ("bl", "br"), ("tl", "bl"), ("tr", "br")]
        if any(corners[a] and corners[b] for a, b in pairs):
            return "TEE"
    return "OTHER"
