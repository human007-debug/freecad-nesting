"""
quoting/quote_model.py
----------------------
A quote as the user edits it -- the lines (`LineSpec`) and the settings
(`QuoteSetup`) -- plus everything Qt-free around it:

  * `build_engine()` turns them into a `costing.QuoteEngine` and its
    `PartFeatures`, ready to price;
  * `quote_to_dict()` / `quote_from_dict()` make a quote plain JSON, which
    is what quoting/db.py stores and what the apps pass between them;
  * `nesting_parts_json()` writes a won quote's sheet parts in the
    parts.json shape AlphaNest imports (native_app/main.py's launch arg).

Both front ends -- AlphaNest's Quote tab and the AlphaQuote app -- edit
this same model through native_app/quote_panel.py.
"""

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bar_nest import BarStock
from inventory import StockSheet
from quoting.costing import CostingSettings, QuoteEngine
from quoting.features import (Bend, HoleFeature, PartFeatures, ProfileInfo, default_density,
                              features_from_polygons)
from quoting.operations import default_operations, painting
from quoting.ops_assembly import QuoteCost

LINE_TYPES = ("sheet", "tube", "purchased", "assembly")
TYPE_LABELS = {"sheet": "Sheet", "tube": "Tube / section", "section": "Tube / section",
               "purchased": "Bought-in", "assembly": "Assembly"}
PROFILE_KINDS = ("RHS", "SHS", "CHS", "FLAT", "ANGLE")
END_CUTS = ("square", "mitre", "cope")
TAP_DRILL = {"M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5, "M12": 10.2}
COST_AREAS = ("material", "cutting", "bending", "machining", "welding", "assembly",
              "finishing", "purchased", "quote")
DEFAULT_MARKUPS = {"material": 10.0, "purchased": 25.0}
DEFAULT_MARKUP = 25.0
FALLBACK_SHEET = (3000.0, 1500.0)


# ------------------------------------------------------------------- model

@dataclass
class LineSpec:
    """One quote line as the user edits it. `to_features()` turns it into
    the engine's PartFeatures."""
    name: str
    part_type: str = "sheet"
    quantity: int = 1
    material: str = ""
    thickness: Optional[float] = None
    from_parts: bool = False              # mirrors a row of the Parts tab
    # sheet
    outer: list = field(default_factory=list)
    holes: list = field(default_factory=list)
    bends: Optional[int] = None           # None = unknown
    bend_details: list = field(default_factory=list)
    tapped_holes: int = 0
    tap_size: str = "M8"
    paint: bool = False
    # tube / section
    profile_kind: str = "RHS"
    width: float = 100.0
    height: float = 50.0
    wall: float = 3.0
    length: float = 1000.0
    end_a: str = "square"
    end_b: str = "square"
    bar_length: float = 6000.0
    price_per_m: float = 10.0
    # bought-in
    unit_cost: float = 0.0
    supplier: str = ""
    markup_pct: Optional[float] = None
    # assembly
    part_count: int = 0
    fastener_count: int = 0
    weld_length: float = 0.0
    weld_type: str = "fillet"

    # -- tube geometry -------------------------------------------------
    def profile(self) -> ProfileInfo:
        w, h, t = self.width, self.height, self.wall
        kind = self.profile_kind
        if kind == "SHS":
            h = w
        if kind == "CHS":
            area = math.pi / 4 * (w ** 2 - max(w - 2 * t, 0) ** 2)
            return ProfileInfo("CHS", w, w, t, area, math.pi * w)
        if kind in ("RHS", "SHS"):
            area = w * h - max(w - 2 * t, 0) * max(h - 2 * t, 0)
            return ProfileInfo(kind, max(w, h), min(w, h), t, area, 2 * (w + h))
        if kind == "FLAT":
            return ProfileInfo("FLAT", max(w, t), min(w, t), t, w * t, 2 * (w + t))
        return ProfileInfo("ANGLE", max(w, h), min(w, h), t, t * (w + h - t), 2 * (w + h))

    def to_features(self) -> PartFeatures:
        material = self.material.strip() or None
        if self.part_type == "sheet":
            if self.bend_details:
                bends = [Bend(d.get("angle_deg"), d.get("length"), d.get("radius"), d.get("direction"))
                         for d in self.bend_details]
                if self.bends is not None and self.bends != len(bends):
                    bends = [Bend() for _ in range(self.bends)]
            else:
                bends = None if self.bends is None else [Bend() for _ in range(self.bends)]
            holes = [HoleFeature(TAP_DRILL.get(self.tap_size, 6.8), "tapped", self.tap_size,
                                 count=self.tapped_holes)] if self.tapped_holes else []
            f = features_from_polygons(self.name, self.outer, self.holes, material=material,
                                       thickness=self.thickness, quantity=self.quantity,
                                       bends=bends, hole_features=holes)
            f.extra["paint"] = self.paint
            return f
        if self.part_type == "tube":
            prof = self.profile()
            f = PartFeatures(self.name, part_type="section" if not prof.hollow else "tube",
                             material=material, quantity=self.quantity, profile=prof,
                             length=self.length, end_cuts=(self.end_a, self.end_b),
                             thickness=self.wall)
            f.extra["paint"] = self.paint
            return f
        if self.part_type == "purchased":
            return PartFeatures(self.name, part_type="purchased", quantity=self.quantity,
                                unit_cost=self.unit_cost, supplier=self.supplier or None,
                                markup=None if self.markup_pct is None else self.markup_pct / 100.0)
        return PartFeatures(self.name, part_type="assembly", quantity=self.quantity, material=material,
                            thickness=self.thickness, part_count=self.part_count,
                            fastener_count=self.fastener_count,
                            manual_weld_length=self.weld_length or None)

    def bar_stock(self) -> Optional[BarStock]:
        if self.part_type != "tube":
            return None
        prof = self.profile()
        density = default_density(self.material)
        kg_per_m = prof.area * density / 1000.0 if density and prof.area else None
        return BarStock(self.material or "", prof.designation, self.bar_length,
                        price_per_m=self.price_per_m, kg_per_m=kg_per_m)

    def summary(self) -> str:
        if self.part_type == "sheet":
            t = f"{self.thickness:g} mm" if self.thickness else "? mm"
            bends = "bends ?" if self.bends is None else f"{self.bends} bend(s)"
            extra = [f"{self.tapped_holes}x {self.tap_size}"] if self.tapped_holes else []
            if self.paint:
                extra.append("painted")
            return ", ".join([t, bends] + extra)
        if self.part_type == "tube":
            return f"{self.profile().designation} x {self.length:g} mm, ends {self.end_a}/{self.end_b}"
        if self.part_type == "purchased":
            return f"{self.unit_cost:.2f} each" + (f" from {self.supplier}" if self.supplier else "")
        return (f"{self.part_count} parts, {self.fastener_count} fasteners, "
                f"{self.weld_length:g} mm weld")


@dataclass
class QuoteSetup:
    breaks: List[int] = field(default_factory=lambda: [1, 10, 100])
    material_mode: str = "nest"
    allocation: str = "net_area"
    tube_machine: str = "saw"
    markups: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MARKUPS))  # percent
    default_markup: float = DEFAULT_MARKUP
    paint_rate: float = 15.0
    paint_setup: float = 25.0
    transport: float = 80.0
    fallback_price_per_kg: float = 1.20
    kerf: float = 0.0


def parse_breaks(text: str) -> List[int]:
    """"1, 10, 100" -> [1, 10, 100]; raises ValueError on anything else."""
    out = []
    for tok in text.replace(";", ",").replace(" ", ",").split(","):
        if tok.strip():
            v = int(tok)
            if v < 1:
                raise ValueError("quantities must be 1 or more")
            out.append(v)
    if not out:
        raise ValueError("enter at least one quantity")
    return sorted(set(out))


def build_engine(lines: List[LineSpec], setup: QuoteSetup, rates, stock: Optional[List[StockSheet]]):
    """(engine, features, notes) for `lines` -- Qt-free so it can be tested
    and run on a worker thread."""
    notes = []
    features = [l.to_features() for l in lines]
    sheet_stock = list(stock or [])
    if not sheet_stock:
        seen = set()
        for f in features:
            if f.part_type == "sheet" and f.material and f.thickness:
                key = (f.material, f.thickness)
                if key in seen:
                    continue
                seen.add(key)
                sheet_stock.append(StockSheet(f.material, f.thickness, *FALLBACK_SHEET,
                                              price_per_kg=setup.fallback_price_per_kg,
                                              density_g_cm3=f.density()))
        if seen:
            notes.append(f"No sheet stock loaded: sheet material is priced on "
                         f"{FALLBACK_SHEET[0]:g}x{FALLBACK_SHEET[1]:g} sheets at "
                         f"{setup.fallback_price_per_kg:g}/kg.")
    bars, seen_bars = [], set()
    for l in lines:
        b = l.bar_stock()
        if b is not None and (b.material, b.profile) not in seen_bars:
            seen_bars.add((b.material, b.profile))
            bars.append(b)
    ops = default_operations(rates, tube_machine=setup.tube_machine) + [
        painting(setup.paint_rate, setup=setup.paint_setup)]
    settings = CostingSettings(
        allocation=setup.allocation, material_mode=setup.material_mode, kerf=setup.kerf,
        markups={k: v / 100.0 for k, v in setup.markups.items()},
        default_markup=setup.default_markup / 100.0)
    quote_costs = [QuoteCost("transport", setup.transport)] if setup.transport else []
    return QuoteEngine(rates, sheet_stock, bars, operations=ops, settings=settings,
                       quote_costs=quote_costs), features, notes


# ---------------------------------------------------------- serialization

QUOTE_FORMAT = 1


def line_to_dict(line: LineSpec) -> dict:
    d = dataclasses.asdict(line)
    d["outer"] = [list(p) for p in line.outer]
    d["holes"] = [[list(p) for p in h] for h in line.holes]
    return d


def line_from_dict(d: dict) -> LineSpec:
    known = {f.name for f in dataclasses.fields(LineSpec)}
    data = {k: v for k, v in d.items() if k in known}
    data["outer"] = [tuple(p) for p in data.get("outer", [])]
    data["holes"] = [[tuple(p) for p in h] for h in data.get("holes", [])]
    return LineSpec(**data)


def setup_to_dict(setup: QuoteSetup) -> dict:
    return dataclasses.asdict(setup)


def setup_from_dict(d: dict) -> QuoteSetup:
    known = {f.name for f in dataclasses.fields(QuoteSetup)}
    return QuoteSetup(**{k: v for k, v in d.items() if k in known})


def quote_to_dict(lines: List[LineSpec], setup: QuoteSetup, overrides: Optional[dict] = None) -> dict:
    """Plain-JSON form of a quote. `overrides` is the editor's
    {(sets, line, cell): value} -- stored as a list, JSON has no tuple keys."""
    return {
        "format": QUOTE_FORMAT,
        "lines": [line_to_dict(l) for l in lines],
        "setup": setup_to_dict(setup),
        "overrides": [[s, line, key, v] for (s, line, key), v in (overrides or {}).items()],
    }


def quote_from_dict(d: dict):
    """(lines, setup, overrides) back from `quote_to_dict()`'s output."""
    lines = [line_from_dict(x) for x in d.get("lines", [])]
    setup = setup_from_dict(d.get("setup", {}))
    overrides = {(int(s), line, key): float(v) for s, line, key, v in d.get("overrides", [])}
    return lines, setup, overrides


def result_summary(result) -> List[dict]:
    """What a quote list needs to show without re-pricing: per break."""
    return [{"sets": br.quantity, "cost": round(br.total_cost, 2), "price": round(br.total_price, 2),
             "set_price": round(br.set_price, 2), "complete": br.complete} for br in result.breaks]


def nesting_parts_json(lines: List[LineSpec], sets: int) -> dict:
    """The sheet lines of a quote, `sets` sets' worth, as AlphaNest's
    parts.json (the shape freecad_extract.py writes). Lines with no flat
    pattern are skipped."""
    parts = []
    for l in lines:
        if l.part_type != "sheet" or not l.outer:
            continue
        parts.append({
            "name": l.name,
            "points": [list(p) for p in l.outer],
            "holes": [[list(p) for p in h] for h in l.holes],
            "thickness": l.thickness,
            "material": l.material or None,
            "quantity": l.quantity * sets,
            "method": "quote",
            "bends": l.bends,
            "bend_details": l.bend_details,
        })
    return {"parts": parts, "unresolved": [], "skipped": []}
