"""
quoting/features.py
-------------------
Phase 1 of docs/QUOTING_PLAN.md: the per-part facts every cost is built
from. Plain Python -- no FreeCAD import. A `PartFeatures` is filled from:

  - freecad_extract.py's JSON (`features_from_part_json`) -- which, run
    under FreeCADCmd, now also writes `bend_details` and `hole_features`
    next to the existing `bends` count;
  - dxf_extract.py / hand-built `nester.Part` objects
    (`features_from_nester_part`) -- a DXF flat pattern carries no bend
    information, so bends stay *unknown* (None) unless entered by hand;
  - hand entry, for tube/section, machined, purchased and assembly lines
    (construct `PartFeatures` directly).

One schema for every source and part type: fields that don't apply to a
part type just stay at their empty defaults.

CUT LENGTH / PIERCES: one pierce per closed contour (the outer profile plus
every internal contour). Hole loops that are circles (see `fit_circle`)
count their true circumference rather than the tessellated polygon's
slightly shorter perimeter -- the laser cuts the arc, not the chords.

UNITS: millimetres throughout (mm, mm^2, mm^3), density in g/cm^3, weight
in kg. Area/length helpers suffixed `_m2`/`_m` convert for per-m^2/per-m
rates.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from geometry import polygon_area, polygon_bbox, polygon_perimeter

Point = Tuple[float, float]

PART_TYPES = ("sheet", "tube", "section", "machined", "purchased", "assembly")

# Hole kinds that are a machining operation even on a laser-cut sheet part
# (a plain through-hole on a sheet part is just another laser contour).
HOLE_KINDS = ("plain", "tapped", "countersink", "counterbore")

# g/cm^3 -- used only when a part has no density of its own. Same numbers
# inventory.StockSheet's docstring quotes.
DEFAULT_DENSITIES = {
    "mild steel": 7.85,
    "stainless": 8.0,
    "aluminium": 2.70,
    "galvanised": 7.85,
    "brass": 8.5,
    "copper": 8.96,
}

_MATERIAL_ALIASES = {
    "ms": "mild steel", "steel": "mild steel", "cs": "mild steel", "carbon steel": "mild steel",
    "s235": "mild steel", "s275": "mild steel", "s355": "mild steel",
    "ss": "stainless", "stainless steel": "stainless", "304": "stainless", "316": "stainless",
    "ss304": "stainless", "ss316": "stainless",
    "al": "aluminium", "aluminum": "aluminium", "alu": "aluminium",
    "5052": "aluminium", "5083": "aluminium", "6061": "aluminium", "6082": "aluminium",
    "galv": "galvanised", "galvanized": "galvanised", "zintec": "galvanised",
}


def normalize_material(name: Optional[str]) -> Optional[str]:
    """Canonical lower-case material family for rate/density lookup
    ("Aluminum" / "AL" / "6082" -> "aluminium"). Unknown names come back
    lower-cased and trimmed, unchanged otherwise -- a shop's own material
    names still work as long as the rate table uses the same spelling."""
    if name is None:
        return None
    key = " ".join(str(name).strip().lower().replace("_", " ").split())
    return _MATERIAL_ALIASES.get(key, key)


def default_density(material: Optional[str]) -> Optional[float]:
    return DEFAULT_DENSITIES.get(normalize_material(material))


# --------------------------------------------------------------- sub-records

@dataclass
class Bend:
    """One press-brake hit. Any detail may be unknown (None) -- e.g. a
    STEP-derived part where only the count is known."""
    angle_deg: Optional[float] = None
    length: Optional[float] = None        # mm, along the bend line
    radius: Optional[float] = None        # inside radius, mm
    direction: Optional[str] = None       # "up" / "down" relative to the flat's top face


@dataclass
class HoleFeature:
    """A group of identical holes (`count` of them)."""
    diameter: float
    kind: str = "plain"                   # one of HOLE_KINDS
    thread: Optional[str] = None          # e.g. "M8" for a tapped hole
    depth: Optional[float] = None         # None = through
    count: int = 1

    def __post_init__(self):
        if self.kind not in HOLE_KINDS:
            raise ValueError(f"hole kind must be one of {HOLE_KINDS}, got {self.kind!r}")


@dataclass
class Pocket:
    volume: float                         # mm^3 of material removed
    kind: str = "pocket"                  # "pocket" or "slot" -- informational


@dataclass
class ProfileInfo:
    """A tube/section's cross-section, as matched by classify.match_profile
    or entered by hand."""
    kind: str                             # RHS, SHS, CHS, FLAT, ROUND, ANGLE, CHANNEL, IBEAM, TEE, OTHER
    width: float                          # mm (outer diameter for CHS/ROUND)
    height: float                         # mm
    wall: Optional[float] = None          # wall / leg thickness, mm
    area: Optional[float] = None          # cross-section area, mm^2
    perimeter: Optional[float] = None     # outer perimeter, mm

    @property
    def hollow(self) -> bool:
        return self.kind in ("RHS", "SHS", "CHS")

    @property
    def designation(self) -> str:
        """Stock-matching key, e.g. "RHS 100x50x3", "CHS 48.3x3.2"."""
        def f(v):
            return f"{v:g}"
        if self.kind in ("CHS",):
            return f"CHS {f(self.width)}x{f(self.wall or 0)}"
        if self.kind == "ROUND":
            return f"ROUND {f(self.width)}"
        dims = f"{f(self.width)}x{f(self.height)}"
        if self.wall is not None:
            dims += f"x{f(self.wall)}"
        return f"{self.kind} {dims}"

    @property
    def max_dimension(self) -> float:
        return max(self.width, self.height)


# ----------------------------------------------------------------- the part

@dataclass
class PartFeatures:
    name: str
    part_type: str = "sheet"              # one of PART_TYPES
    material: Optional[str] = None
    thickness: Optional[float] = None     # mm (sheet gauge; wall for a tube if set)
    quantity: int = 1                     # per quoted set (see costing.py)
    density_g_cm3: Optional[float] = None  # None = DEFAULT_DENSITIES by material

    # sheet: the flat pattern and what the laser does to it
    outer: List[Point] = field(default_factory=list)
    holes: List[List[Point]] = field(default_factory=list)
    cut_length: float = 0.0               # mm, every contour
    pierces: int = 0
    internal_contours: int = 0
    net_area: float = 0.0                 # mm^2, outer minus holes
    gross_area: float = 0.0               # mm^2, bounding box of the flat
    bbox: Tuple[float, float] = (0.0, 0.0)
    bends: Optional[List[Bend]] = None    # None = unknown (DXF / STEP), [] = known flat

    # tube / section
    profile: Optional[ProfileInfo] = None
    length: float = 0.0                   # mm
    end_cuts: Tuple[str, str] = ("square", "square")  # square / mitre / cope
    cutout_length: float = 0.0            # mm of tube-laser cut-outs along the length
    cutout_count: int = 0

    # machining (any part type) -- plus solid volume for a machined blank
    hole_features: List[HoleFeature] = field(default_factory=list)
    pockets: List[Pocket] = field(default_factory=list)
    volume: Optional[float] = None        # mm^3
    blank_cost: Optional[float] = None    # per unit; machined parts have no stock model

    # purchased / bought-in
    unit_cost: Optional[float] = None
    supplier: Optional[str] = None
    markup: Optional[float] = None        # overrides the cost-area markup (0.2 = +20%)

    # assembly (welding + assembly labour)
    weld_joints: list = field(default_factory=list)  # ops_weld.WeldJoint
    manual_weld_length: Optional[float] = None        # mm, fallback when no joints
    tack_count: int = 0
    part_count: int = 0
    fastener_count: int = 0

    surface_area: Optional[float] = None  # mm^2 painted/coated area; None = derived
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, object] = field(default_factory=dict)  # custom-operation inputs

    def __post_init__(self):
        if self.part_type not in PART_TYPES:
            raise ValueError(f"part_type must be one of {PART_TYPES}, got {self.part_type!r}")

    # ------------------------------------------------------------ derived

    @property
    def bends_known(self) -> bool:
        return self.bends is not None

    @property
    def bend_count(self) -> Optional[int]:
        return None if self.bends is None else len(self.bends)

    def density(self) -> Optional[float]:
        return self.density_g_cm3 if self.density_g_cm3 is not None else default_density(self.material)

    def weight_kg(self) -> Optional[float]:
        """Finished-part weight: flat net area x thickness for sheet, section
        area x length for tube/section, `volume` for anything else. None
        when the density or the geometry isn't known."""
        rho = self.density()
        if rho is None:
            return None
        if self.part_type == "sheet" and self.net_area and self.thickness:
            vol = self.net_area * self.thickness
        elif self.part_type in ("tube", "section") and self.profile and self.profile.area and self.length:
            vol = self.profile.area * self.length
        elif self.volume:
            vol = self.volume
        else:
            return None
        return vol * rho / 1e6

    def surface_area_m2(self, sides: int = 2) -> float:
        """Coated area in m^2. Explicit `surface_area` wins; otherwise a
        sheet is net area x `sides` (edges ignored, per the plan's
        `area x 2 x rate`), a tube/section is outer perimeter x length."""
        if self.surface_area is not None:
            return self.surface_area / 1e6
        if self.part_type == "sheet":
            return self.net_area * sides / 1e6
        if self.part_type in ("tube", "section") and self.profile and self.profile.perimeter:
            return self.profile.perimeter * self.length / 1e6
        return 0.0

    def machined_hole_features(self) -> List[HoleFeature]:
        """Holes that need a machining operation: everything on a
        non-sheet part, but only non-plain holes on a sheet part (plain
        through-holes there are laser contours, already in cut_length)."""
        if self.part_type == "sheet":
            return [h for h in self.hole_features if h.kind != "plain"]
        return list(self.hole_features)


# ------------------------------------------------------------------- geometry

def fit_circle(loop: Sequence[Point], rel_tol: float = 0.02, min_points: int = 8
               ) -> Optional[Tuple[float, float, float]]:
    """(cx, cy, r) if `loop` is a tessellated circle -- every vertex within
    `rel_tol` x r of the mean radius about the vertex centroid -- else None.
    Vertices of a tessellated circle are evenly spaced, so their plain mean
    is the centre; for anything else the radius spread gives it away."""
    n = len(loop)
    if n < min_points:
        return None
    cx = sum(p[0] for p in loop) / n
    cy = sum(p[1] for p in loop) / n
    radii = [math.hypot(x - cx, y - cy) for x, y in loop]
    r = sum(radii) / n
    if r <= 0:
        return None
    if max(abs(ri - r) for ri in radii) > rel_tol * r:
        return None
    # A tessellation's vertices sit ON the circle; its chords sit inside,
    # so the vertex radius is the true radius.
    return cx, cy, max(radii)


def contour_length(loop: Sequence[Point]) -> float:
    """What the laser actually cuts for one closed loop: the true
    circumference for a circle, the polygon perimeter otherwise."""
    circle = fit_circle(loop)
    if circle is not None:
        return 2 * math.pi * circle[2]
    return polygon_perimeter(list(loop))


def features_from_polygons(name: str, outer: Sequence[Point], holes: Sequence[Sequence[Point]] = (),
                           material: Optional[str] = None, thickness: Optional[float] = None,
                           quantity: int = 1, bends: Optional[List[Bend]] = None,
                           hole_features: Optional[List[HoleFeature]] = None,
                           density_g_cm3: Optional[float] = None) -> PartFeatures:
    """A sheet part's features from its flat pattern (outer contour + hole
    loops, mm). `bends=None` means "unknown", not "none"."""
    outer = [tuple(p) for p in outer]
    holes = [[tuple(p) for p in h] for h in holes]
    if len(outer) < 3:
        raise ValueError(f"{name}: outer contour needs at least 3 points")
    minx, miny, maxx, maxy = polygon_bbox(outer)
    w, h = maxx - minx, maxy - miny
    net = polygon_area(outer) - sum(polygon_area(hl) for hl in holes)
    cut = contour_length(outer) + sum(contour_length(hl) for hl in holes)
    return PartFeatures(
        name=name, part_type="sheet", material=material, thickness=thickness,
        quantity=quantity, density_g_cm3=density_g_cm3,
        outer=outer, holes=holes,
        cut_length=cut, pierces=1 + len(holes), internal_contours=len(holes),
        net_area=net, gross_area=w * h, bbox=(w, h),
        bends=bends, hole_features=list(hole_features or []),
    )


def features_from_nester_part(part, bends: Optional[List[Bend]] = None) -> PartFeatures:
    """From a `nester.Part` (DXF import, hand-built parts). The nesting
    Part carries no bend data, so bends stay unknown unless given."""
    return features_from_polygons(part.name, part.points, part.holes, material=part.material,
                                  thickness=part.thickness, quantity=part.quantity, bends=bends)


def _parse_bends(entry: dict) -> Optional[List[Bend]]:
    details = entry.get("bend_details")
    if details:
        return [Bend(**{k: d.get(k) for k in ("angle_deg", "length", "radius", "direction")})
                for d in details]
    count = entry.get("bends")
    if isinstance(count, list):
        return [Bend(**{k: d.get(k) for k in ("angle_deg", "length", "radius", "direction")})
                for d in count]
    if count is None:
        return None
    # A bare count: known number of hits, unknown detail. freecad_extract's
    # flat-plate path writes 0 -- a genuinely flat part, so [] (known).
    return [Bend() for _ in range(int(count))]


def features_from_part_json(entry: dict) -> PartFeatures:
    """One element of freecad_extract.py's / a DXF import's `parts` list.
    Accepts both the old schema (`bends` as a count) and the extended one
    (`bend_details`, `hole_features`). A DXF-sourced entry (`source` ==
    "dxf") with no bend data is left as bends-unknown."""
    bends = _parse_bends(entry)
    if entry.get("source") == "dxf" and not entry.get("bend_details") and not entry.get("bends"):
        bends = None
    holes = [HoleFeature(diameter=float(h["diameter"]), kind=h.get("kind", "plain"),
                         thread=h.get("thread"), depth=h.get("depth"), count=int(h.get("count", 1)))
             for h in entry.get("hole_features", [])]
    return features_from_polygons(
        entry["name"], entry["points"], entry.get("holes", []),
        material=entry.get("material"), thickness=entry.get("thickness"),
        quantity=int(entry.get("quantity", 1)), bends=bends, hole_features=holes,
        density_g_cm3=entry.get("density_g_cm3"),
    )


def load_features(json_path: str) -> List[PartFeatures]:
    """Every part in a freecad_extract.py output file."""
    import json
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return [features_from_part_json(p) for p in data["parts"]]


def detected_round_holes(features: PartFeatures, ndigits: int = 1) -> List[HoleFeature]:
    """The circular internal contours of a sheet flat, grouped by diameter
    -- what a DXF-only part can offer when a tapping/countersinking
    operation needs to know which holes exist. They're returned as
    `plain`; the user promotes the ones that are tapped."""
    by_dia: Dict[float, int] = {}
    for loop in features.holes:
        c = fit_circle(loop)
        if c is not None:
            d = round(2 * c[2], ndigits)
            by_dia[d] = by_dia.get(d, 0) + 1
    return [HoleFeature(diameter=d, count=n) for d, n in sorted(by_dia.items())]
