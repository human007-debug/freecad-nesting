"""
nester.py
---------
Irregular-shape (true polygon, not bounding-box) 2D nesting engine.

ALGORITHM -- true no-fit-polygon (NFP) placement:
  1. Sort parts largest-area-first (classic nesting heuristic).
  2. For each rotation of a part, compute the exact NFP of every
     already-placed part against this shape (Minkowski sum via pyclipper --
     see nfp.py), union them all into one obstacle region, and subtract
     that from the sheet's inner-fit rectangle. What's left is the exact
     free space for this shape's reference point -- no approximation, no
     grid, no post-hoc compaction pass needed, because every vertex of
     that free region (including vertices created where two placed parts'
     NFPs meet, and vertices inside a concave part's NFP holes -- see
     nfp.py's docstring) is already an exact touching position.
  3. Candidates are scanned bottom-left first; the first that is exactly
     feasible (kerf spacing via an offset-inflated obstacle, or plain
     positive-area-overlap rejection when kerf is 0 -- both via pyclipper,
     not bounding boxes) is kept.
  4. If a part fits on no existing sheet, a new sheet is opened.

This used to be a heuristic (vertex-touching candidates + local binary-
search compaction) documented here as an honest stand-in for real NFP,
pending pyclipper integration. That's done now: pyclipper is a hard
dependency of this project (not an optional accelerator) -- there is no
reasonable pure-Python way to compute exact Minkowski sums for arbitrary
concave polygons, and nesting density was judged more important than
keeping a zero-dependency pitch. `geometry.py` stays pure-Python and keeps
its exact overlap/distance math -- it's still what nfp.py and this module
lean on for kerf distance checks, just not for the touching-boundary
overlap test (see nfp.intersection_area's docstring for why).

HOLES: a Part may carry inner hole loops (for correct area/DXF/visual
output). Collision/spacing is computed on the OUTER contour only -- i.e.
this engine does not (yet) nest one part's material into another part's
hole. That's a deliberate, conservative default (most shops don't want
parts nested into each other's holes anyway, for handling/QC reasons); it
can be relaxed per-part later if wanted.

HONEST LIMITATION: this places parts largest-first with a bottom-left-fill
NFP heuristic -- exact touching positions, but still a greedy heuristic
over placement ORDER and rotation choice, not a global optimum. A
genetic-algorithm layer over part ordering/rotation (see README's "Upgrade
path") is the next real density gain on top of this.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Tuple, Optional

import nfp
from geometry import (
    rotate_points, translate_points, polygon_bbox, polygon_area,
    polygons_min_distance, polygon_fits_in_sheet, bboxes_separated, point_in_polygon,
)

Point = Tuple[float, float]

AREA_EPS = 1e-6  # mm^2 -- an NFP candidate sits exactly on some obstacle's
                 # boundary by construction; only reject real area overlap,
                 # not the shared-point/shared-edge contact that implies


@dataclass
class Part:
    name: str
    points: List[Point]                       # outer contour
    holes: List[List[Point]] = field(default_factory=list)  # inner loops (cutouts)
    quantity: int = 1
    rotations: List[float] = field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    material: Optional[str] = None            # metadata only -- the 2D engine is footprint-based,
    thickness: Optional[float] = None         # material/thickness only matter for stock matching (see inventory.py)
    allow_mirror: bool = False                # also try the part's mirror image at every allowed rotation
                                              # (a sheet-metal part is often asymmetric; the flipped
                                              # footprint can nest into leftover space the original can't)

    def area(self):
        """Net area = outer area minus all hole areas."""
        a = polygon_area(self.points)
        for h in self.holes:
            a -= polygon_area(h)
        return a


@dataclass
class PlacedPart:
    name: str
    points: List[Point]          # final outer polygon, in sheet coordinates
    holes: List[List[Point]]     # final hole loops, in sheet coordinates
    rotation: float
    sheet_index: int
    mirrored: bool = False       # True if this copy was placed as the part's mirror image

    def net_area(self):
        a = polygon_area(self.points)
        for h in self.holes:
            a -= polygon_area(h)
        return a


def _transform_shape(outer, holes, angle_deg, dx=0.0, dy=0.0, pivot=(0.0, 0.0)):
    """Rotate (about pivot) then translate an outer contour + its holes together."""
    r_outer = rotate_points(outer, angle_deg, pivot)
    r_holes = [rotate_points(h, angle_deg, pivot) for h in holes]
    if dx or dy:
        r_outer = translate_points(r_outer, dx, dy)
        r_holes = [translate_points(h, dx, dy) for h in r_holes]
    return r_outer, r_holes


def _mirror_shape(outer, holes):
    """The part's mirror image -- reflect across the vertical axis (x -> -x).
    Flipping handedness like this also flips polygon winding, but every
    consumer here (geometry.py area/point-in-polygon, pyclipper's NONZERO
    fill rules in nfp.py) is orientation-independent, so no re-winding is
    needed before the shape goes through the placer."""
    return [(-x, y) for x, y in outer], [[(-x, y) for x, y in h] for h in holes]


def _normalize_to_origin(outer, holes):
    """Shift outer+holes together so outer's bbox min corner is at (0,0)."""
    minx, miny, maxx, maxy = polygon_bbox(outer)
    outer2 = translate_points(outer, -minx, -miny)
    holes2 = [translate_points(h, -minx, -miny) for h in holes]
    return outer2, holes2, maxx - minx, maxy - miny


class Nester:
    def __init__(self, sheet_w, sheet_h, kerf=0.0,
                 margin_left=0.0, margin_right=0.0, margin_top=0.0, margin_bottom=0.0,
                 allow_hole_nesting=False, hole_clearance=0.0):
        self.sheet_w = sheet_w
        self.sheet_h = sheet_h
        self.kerf = kerf
        # A no-cut border along each edge (e.g. where a machine clamps the
        # material down) -- 0 on all four sides (the default) reproduces
        # today's behavior exactly, parts flush to every edge.
        self.margin_left = margin_left
        self.margin_right = margin_right
        self.margin_top = margin_top
        self.margin_bottom = margin_bottom
        self.allow_hole_nesting = bool(allow_hole_nesting)
        self.hole_clearance = max(0.0, float(hole_clearance))
        self._parts: List[Part] = []

    def _usable_dims(self):
        return (self.sheet_w - self.margin_left - self.margin_right,
                self.sheet_h - self.margin_top - self.margin_bottom)

    def add_part(self, part: Part):
        self._parts.append(part)

    # ------------------------------------------------------------- public

    def expand_parts(self) -> List[Part]:
        """The added parts, quantity exploded into individual instances
        (same Part reference repeated `quantity` times) -- exposed so
        callers that want to search over placement ORDER (see genetic.py)
        can get the same flat list run() itself sorts, without duplicating
        this bookkeeping."""
        expanded = []
        for p in self._parts:
            for _ in range(p.quantity):
                expanded.append(p)
        return expanded

    def run(self):
        """Default heuristic ordering (largest-area-first), then place.
        Returns (sheets, unplaced). See place_order() to place a specific
        ordering instead -- e.g. one found by genetic.optimize_order()."""
        expanded = self.expand_parts()
        expanded.sort(key=lambda p: p.area(), reverse=True)
        return self.place_order(expanded)

    def place_order(self, ordered_parts: List[Part], forced_rotations: Optional[List[Optional[float]]] = None,
                    on_placed: Optional[Callable] = None):
        """Place parts in EXACTLY the given order/sequence -- no re-sorting.
        Returns (sheets, unplaced), same shape as run(). This is what makes
        placement order pluggable: run() is just one particular ordering
        (largest-first) of this same underlying placer.

        `forced_rotations`, if given, is a list parallel to `ordered_parts`:
        `forced_rotations[i]` fixes that part's rotation instead of
        exhaustively searching `part.rotations` for the best one -- lets a
        caller (genetic.optimize_order(..., optimize_rotations=True)) search
        rotation choice itself instead of leaving it to this engine's own
        greedy per-placement pick. `None` (the default, or a `None` entry
        within the list) falls back to the exhaustive search, unchanged
        from before this parameter existed.

        `on_placed`, if given, is called after every part's placement
        attempt (placed OR failed) as
        `on_placed(sheets, unplaced, idx, total, name, sheet_idx, placed_ok, sheet_w, sheet_h)`:
        `sheets`/`unplaced` are the live partial results so far (may contain
        empty inner sheet lists mid-run; callers should filter them),
        `idx`/`total` are the position and count within `ordered_parts`,
        `name` is the part's name, `sheet_idx` the sheet it was placed onto /
        attempted on, `placed_ok` whether it actually landed, and
        `sheet_w`/`sheet_h` the sheet dimensions this run is using -- lets a
        UI redraw an assembling layout per part (a live nesting animation)
        instead of only showing the finished result. Default `None` changes
        nothing and leaves the calculation pure."""
        sheets: List[List[PlacedPart]] = [[]]
        unplaced = []
        total = len(ordered_parts)

        for idx, part in enumerate(ordered_parts):
            forced = forced_rotations[idx] if forced_rotations is not None else None
            placed_ok = False
            sheet_idx = 0
            for sheet_idx in range(len(sheets)):
                placement = self._find_placement(part, sheets[sheet_idx], forced_rotation=forced)
                if placement is not None:
                    rot, mirrored, outer, holes = placement
                    sheets[sheet_idx].append(
                        PlacedPart(name=part.name, points=outer, holes=holes,
                                   rotation=rot, mirrored=mirrored, sheet_index=sheet_idx)
                    )
                    placed_ok = True
                    break
            if not placed_ok:
                new_sheet_idx = len(sheets)
                sheets.append([])
                placement = self._find_placement(part, sheets[new_sheet_idx], forced_rotation=forced)
                if placement is not None:
                    rot, mirrored, outer, holes = placement
                    sheets[new_sheet_idx].append(
                        PlacedPart(name=part.name, points=outer, holes=holes,
                                   rotation=rot, mirrored=mirrored, sheet_index=new_sheet_idx)
                    )
                    placed_ok = True
                else:
                    sheets.pop()
                    unplaced.append(part.name)
                sheet_idx = new_sheet_idx
            if on_placed is not None:
                on_placed(sheets, unplaced, idx, total, part.name, sheet_idx, placed_ok,
                          self.sheet_w, self.sheet_h)

        sheets = [s for s in sheets if s] or [[]]
        return sheets, unplaced

    # ------------------------------------------------------------ internals

    def _feasible(self, candidate_poly, cand_bbox, placed: List[PlacedPart]):
        if not polygon_fits_in_sheet(candidate_poly, self.sheet_w, self.sheet_h,
                                      margin_left=self.margin_left, margin_right=self.margin_right,
                                      margin_bottom=self.margin_bottom, margin_top=self.margin_top):
            return False
        for pp in placed:
            if bboxes_separated(cand_bbox, pp.bbox, margin=self.kerf):
                continue
            if self.kerf > 0:
                if polygons_min_distance(candidate_poly, pp.points) < self.kerf - 1e-9:
                    return False
            else:
                if nfp.intersection_area(candidate_poly, pp.points) > AREA_EPS:
                    if not (self.allow_hole_nesting and any(
                        self._fits_hole(candidate_poly, hole) for hole in pp.holes
                    )):
                        return False
        return True

    def _fits_hole(self, candidate_poly, hole):
        """Conservative containment test for opt-in part-in-hole placement."""
        if not hole or not all(point_in_polygon(p, hole) for p in candidate_poly):
            return False
        if self.hole_clearance <= 0:
            return True
        # Shrinking the parent hole makes the clearance requirement explicit;
        # every candidate vertex must remain inside the inset opening.
        inset = nfp.offset_polygon(hole, -self.hole_clearance)
        return bool(inset) and all(any(point_in_polygon(p, loop) for loop in inset)
                                   for p in candidate_poly)

    def _find_placement(self, part: Part, placed: List[PlacedPart], forced_rotation: Optional[float] = None):
        best = None  # (sort_key, rotation, mirrored, outer, holes)

        for pp in placed:
            if not hasattr(pp, "bbox"):
                pp.bbox = polygon_bbox(pp.points)

        usable_w, usable_h = self._usable_dims()
        rotations_to_try = [forced_rotation] if forced_rotation is not None else part.rotations
        # A mirrorable part is tried at every allowed rotation in BOTH
        # handednesses -- mirror first, then rotate -- so the "mirrored by
        # rotation R" orientation really is the mirror of "the 0-degree
        # part rotated by R", not a rotation of the mirror of the original.
        mirror_base = _mirror_shape(part.points, part.holes) if part.allow_mirror else None
        for rot in rotations_to_try:
            normal = _transform_shape(part.points, part.holes, rot)
            variants = [normal + (False,)]
            if mirror_base is not None:
                mirrored = _transform_shape(mirror_base[0], mirror_base[1], rot)
                variants.append(mirrored + (True,))

            for r_outer, r_holes, is_mirrored in variants:
                shape, holes_shape, w, h = _normalize_to_origin(r_outer, r_holes)

                if w > usable_w + 1e-9 or h > usable_h + 1e-9:
                    continue

                candidates = self._candidates(placed, shape, w, h)

                for (cx, cy) in candidates:
                    candidate_poly = translate_points(shape, cx, cy)
                    cand_bbox = (cx, cy, cx + w, cy + h)
                    if not self._feasible(candidate_poly, cand_bbox, placed):
                        continue
                    final_outer = candidate_poly
                    final_holes = [translate_points(hh, cx, cy) for hh in holes_shape]
                    key = (round(cy, 6), round(cx, 6))
                    if best is None or key < best[0]:
                        best = (key, rot, is_mirrored, final_outer, final_holes)
                    break  # candidates are pre-sorted bottom-left; first hit wins this rotation

        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def _candidates(self, placed: List[PlacedPart], shape, part_w, part_h):
        """Exact free-space vertices for `shape`'s reference point: the
        sheet's inner-fit rectangle (where `shape` fits inside the sheet at
        all, inset by the configured margins) minus the union of every
        placed part's NFP against `shape` (where `shape` would overlap that
        part) -- see nfp.py. Uses `nfp.compute_nfp_cached`/
        `offset_polygon_cached` rather than the raw functions -- exactly
        equivalent results (translation of a polygon translates its NFP/
        offset by the same amount, an exact identity -- see nfp.py's
        CACHING section), but shared across every other placement of the
        same part+rotation anywhere in the current process, including
        genetic.py's/stock_solver.py's many repeated re-nests of the same
        catalog."""
        usable_w, usable_h = self._usable_dims()
        min_x, min_y = self.margin_left, self.margin_bottom
        max_x = min_x + max(0.0, usable_w - part_w)
        max_y = min_y + max(0.0, usable_h - part_h)
        # A part that exactly fills a usable dimension produces a zero-area
        # inner-fit rectangle (a line, not a region) -- pyclipper rejects
        # zero-area paths outright (ClipperException "All paths are invalid").
        # Nudge the degenerate axis out by an epsilon so subtraction works;
        # the bounds filter below only keeps points within the true ifp
        # anyway, so this cannot admit a position the real geometry excludes.
        if max_x - min_x <= 1e-6:
            max_x = min_x + 1e-4
        if max_y - min_y <= 1e-6:
            max_y = min_y + 1e-4
        ifp = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

        obstacle_polys = []
        hole_candidates = []
        for pp in placed:
            if self.kerf > 0:
                if not hasattr(pp, "_kerf_offset"):
                    pp._kerf_offset = nfp.offset_polygon_cached(pp.points, self.kerf)
                stationary_variants = pp._kerf_offset
            else:
                stationary_variants = [pp.points]
            for sp in stationary_variants:
                obstacle_polys.extend(nfp.compute_nfp_cached(sp, shape))

        if self.allow_hole_nesting:
            # Add exact vertex-to-vertex candidates for each existing hole;
            # _feasible performs the final containment and clearance check.
            for pp in placed:
                for hole in pp.holes:
                    for hx, hy in hole:
                        for sx, sy in shape:
                            hole_candidates.append((round(hx - sx, 6), round(hy - sy, 6)))
                    hminx, hminy, hmaxx, hmaxy = polygon_bbox(hole)
                    sminx, sminy, smaxx, smaxy = polygon_bbox(shape)
                    hole_candidates.append((round((hminx + hmaxx - sminx - smaxx) / 2, 6),
                                            round((hminy + hmaxy - sminy - smaxy) / 2, 6)))

        obstacles = nfp.union_all(obstacle_polys) if obstacle_polys else []
        free = nfp.difference([ifp], obstacles)

        pts = set()
        for poly in free:
            for p in poly:
                pts.add((round(p[0], 6), round(p[1], 6)))
        for p in ifp:
            pts.add((round(p[0], 6), round(p[1], 6)))
        pts.update(hole_candidates)

        valid = [(x, y) for (x, y) in pts
                 if min_x - 1e-6 <= x <= max_x + 1e-6 and min_y - 1e-6 <= y <= max_y + 1e-6]
        valid.sort(key=lambda p: (p[1], p[0]))
        return valid
