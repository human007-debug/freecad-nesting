"""
quoting/costing.py
------------------
Phase 3: the quote engine -- lines x operations x quantity breaks -> a
cost breakdown in which every cell can be overridden.

QUANTITIES: each line's `PartFeatures.quantity` is its count per quoted
SET (one bracket per assembly, four bolts per assembly, ...). A quantity
break of N sets makes N x quantity of every line, and everything is
re-worked at that volume: the job is re-nested (sheet and bar), and each
operation's setup is spread over the line's batch.

MATERIAL
  sheet   nest mode (default): the whole job at this break goes through
          inventory.run_job() -- real sheets, real stock sizes, remnants
          first -- against a copy of the stock list (run_job decrements
          on-hand quantities; quoting must not). Per (material, thickness)
          group: cost of every sheet consumed (remnants x
          `remnant_price_factor`), minus scrap resale on the part of each
          sheet that isn't parts (`scrap_price_per_kg`), split over the
          placed parts in proportion to net area (default) or bounding-box
          area (`allocation="bbox"`).
          estimate mode: no nesting -- (allocation area / utilisation) of
          the best-matching new stock's price, minus scrap on the
          difference. The plan's "quick estimate" for big jobs.
  tube /  bar_nest.nest_bars() per (material, profile) group against the
  section bar stock; cost of bars consumed (whole bars, or only the length
          used when `bar_charge="used"` and the offcut is long enough to
          go back as a remnant), minus scrap on scrap-length offcuts,
          split by piece length.
  machined  `blank_cost` if given (there's no block-stock model yet).
  purchased / assembly: no material cell (bought-in price and welding/
          assembly are operations).

OPERATIONS: every operation that `applies_to` a line adds one cell, the
per-unit cost at that break (setup / batch + run). A lookup that fails
(no rate for that material/thickness, missing geometry) leaves the cell
empty (None) with a note -- the line is then `complete=False` rather than
silently cheaper.

MARKUP: per cost area (`CostingSettings.markups`, else `default_markup`);
an operation or bought-in line with its own markup overrides its area's.

OVERRIDES: `{(line, cell): value}` for every break, or
`{(break_qty, line, cell): value}` for one. Quote-level costs use the line
name `QUOTE`. The calculated value is kept next to the override.
"""

import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import inventory
from bar_nest import BarStock, nest_bars
from nester import Part
from quoting.features import PART_TYPES, PartFeatures, normalize_material
from quoting.operations import OpCost, Operation, default_operations
from quoting.ops_assembly import QuoteCost
from quoting.rates import RateLookupError, RateTable

ALLOCATIONS = ("net_area", "bbox")
MATERIAL_MODES = ("nest", "estimate")
BAR_CHARGES = ("full", "used")
QUOTE = "__quote__"
MATERIAL = "material"


@dataclass
class CostingSettings:
    allocation: str = "net_area"
    material_mode: str = "nest"
    estimate_utilisation: float = 0.75        # estimate mode only
    remnant_price_factor: float = 1.0         # 1.0 = remnants at full value, 0.5 = half, 0 = free
    scrap_credit: bool = True
    markups: Dict[str, float] = field(default_factory=dict)  # cost area -> 0.15 (= +15%)
    default_markup: float = 0.0
    kerf: float = 0.0                         # sheet nesting
    nester_kwargs: dict = field(default_factory=dict)
    run_job_kwargs: dict = field(default_factory=dict)  # e.g. joint_stock_optimization=True
    bar_kerf: float = 3.0
    bar_trim_start: float = 0.0
    bar_trim_end: float = 0.0
    bar_min_remnant: Optional[float] = None   # None = every offcut is scrap
    bar_charge: str = "full"

    def __post_init__(self):
        if self.allocation not in ALLOCATIONS:
            raise ValueError(f"allocation must be one of {ALLOCATIONS}")
        if self.material_mode not in MATERIAL_MODES:
            raise ValueError(f"material_mode must be one of {MATERIAL_MODES}")
        if self.bar_charge not in BAR_CHARGES:
            raise ValueError(f"bar_charge must be one of {BAR_CHARGES}")
        if not 0 < self.estimate_utilisation <= 1:
            raise ValueError("estimate_utilisation must be in (0, 1]")

    def markup_for(self, cost_area: str) -> float:
        return self.markups.get(cost_area, self.default_markup)


# ------------------------------------------------------------------ results

@dataclass
class CostCell:
    key: str
    cost_area: str
    calculated: Optional[float]
    override: Optional[float] = None
    markup: float = 0.0
    estimate: bool = False

    @property
    def value(self) -> Optional[float]:
        return self.override if self.override is not None else self.calculated

    @property
    def overridden(self) -> bool:
        return self.override is not None

    @property
    def price(self) -> Optional[float]:
        v = self.value
        return None if v is None else v * (1 + self.markup)


@dataclass
class LineCost:
    name: str
    part_type: str
    quantity: int                              # units at this break
    cells: Dict[str, CostCell] = field(default_factory=dict)  # per-unit
    op_costs: Dict[str, OpCost] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return all(c.value is not None for c in self.cells.values())

    @property
    def estimate(self) -> bool:
        return any(c.estimate for c in self.cells.values())

    @property
    def unit_cost(self) -> float:
        return sum(c.value for c in self.cells.values() if c.value is not None)

    @property
    def unit_price(self) -> float:
        return sum(c.price for c in self.cells.values() if c.price is not None)

    @property
    def total_cost(self) -> float:
        return self.unit_cost * self.quantity

    @property
    def total_price(self) -> float:
        return self.unit_price * self.quantity


@dataclass
class BreakResult:
    quantity: int                              # sets
    lines: List[LineCost] = field(default_factory=list)
    quote_cells: Dict[str, CostCell] = field(default_factory=dict)  # totals, not per unit
    notes: List[str] = field(default_factory=list)

    def line(self, name: str) -> LineCost:
        return next(l for l in self.lines if l.name == name)

    @property
    def complete(self) -> bool:
        return all(l.complete for l in self.lines) and all(c.value is not None for c in self.quote_cells.values())

    @property
    def total_cost(self) -> float:
        return (sum(l.total_cost for l in self.lines)
                + sum(c.value for c in self.quote_cells.values() if c.value is not None))

    @property
    def total_price(self) -> float:
        return (sum(l.total_price for l in self.lines)
                + sum(c.price for c in self.quote_cells.values() if c.price is not None))

    @property
    def set_price(self) -> float:
        return self.total_price / self.quantity

    def by_type(self) -> Dict[str, List[LineCost]]:
        out: Dict[str, List[LineCost]] = {}
        for t in PART_TYPES:
            lines = [l for l in self.lines if l.part_type == t]
            if lines:
                out[t] = lines
        return out

    def by_cost_area(self) -> Dict[str, Dict[str, float]]:
        """{cost area: {"cost": total, "price": total}} across the break."""
        out: Dict[str, Dict[str, float]] = {}
        cells = [(c, l.quantity) for l in self.lines for c in l.cells.values()]
        cells += [(c, 1) for c in self.quote_cells.values()]
        for c, n in cells:
            if c.value is None:
                continue
            d = out.setdefault(c.cost_area, {"cost": 0.0, "price": 0.0})
            d["cost"] += c.value * n
            d["price"] += c.price * n
        return out


@dataclass
class QuoteResult:
    breaks: List[BreakResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def at(self, quantity: int) -> BreakResult:
        return next(b for b in self.breaks if b.quantity == quantity)


# ------------------------------------------------------------------- engine

class QuoteEngine:
    def __init__(self, rates: RateTable, sheet_stock: Sequence[inventory.StockSheet] = (),
                 bar_stock: Sequence[BarStock] = (), operations: Optional[List[Operation]] = None,
                 settings: Optional[CostingSettings] = None, quote_costs: Iterable[QuoteCost] = ()):
        self.rates = rates
        self.sheet_stock = list(sheet_stock)
        self.bar_stock = list(bar_stock)
        self.operations = operations if operations is not None else default_operations(rates)
        self.settings = settings or CostingSettings()
        self.quote_costs = list(quote_costs)

    # --------------------------------------------------------------- API

    def price(self, parts: Sequence[PartFeatures], breaks: Sequence[int] = (1,),
              overrides: Optional[dict] = None) -> QuoteResult:
        names = [p.name for p in parts]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"line names must be unique; repeated: {dupes}")
        if any(int(q) < 1 for q in breaks):
            raise ValueError("quantity breaks must be >= 1")
        result = QuoteResult()
        uncal = self.rates.uncalibrated_rows()
        if uncal:
            result.notes.append(f"{len(uncal)} rate row(s) are still uncalibrated starter values "
                                f"(calibrate me) -- treat times as estimates")
        for q in sorted({int(q) for q in breaks}):
            result.breaks.append(self._price_break(list(parts), q, overrides or {}))
        return result

    def price_part(self, part: PartFeatures, qty: int = 1) -> LineCost:
        """One part at one quantity -- the plan's minimal first step."""
        single = dataclasses.replace(part, quantity=1)
        return self.price([single], breaks=(qty,)).breaks[0].lines[0]

    # ------------------------------------------------------------ one break

    def _price_break(self, parts: List[PartFeatures], sets: int, overrides: dict) -> BreakResult:
        br = BreakResult(quantity=sets)
        counts = {p.name: p.quantity * sets for p in parts}
        material, mat_notes = self._material(parts, counts)

        order = {t: i for i, t in enumerate(PART_TYPES)}
        for p in sorted(parts, key=lambda p: order[p.part_type]):
            n = counts[p.name]
            line = LineCost(p.name, p.part_type, n)
            line.notes.extend(mat_notes.get(p.name, []))
            if p.name in material:
                line.cells[MATERIAL] = CostCell(MATERIAL, MATERIAL, material[p.name],
                                                markup=self.settings.markup_for(MATERIAL))
            if p.part_type == "sheet" and not p.bends_known:
                line.notes.append("bend count unknown (DXF/STEP source) -- enter it by hand "
                                  "or bending is not costed")
            for op in self.operations:
                try:
                    if not op.applies_to(p):
                        continue
                    oc = op.cost(p, n)
                    value = oc.per_unit(n)
                except (RateLookupError, ValueError) as e:
                    oc, value = None, None
                    line.notes.append(f"{op.name}: {e}")
                markup = oc.markup if oc is not None and oc.markup is not None \
                    else self.settings.markup_for(op.cost_area)
                if oc is not None:
                    line.op_costs[op.name] = oc
                    line.notes.extend(oc.notes)
                line.cells[op.name] = CostCell(op.name, op.cost_area, value, markup=markup,
                                               estimate=oc.estimate if oc is not None else op.estimate)
            for key, cell in line.cells.items():
                cell.override = _override(overrides, sets, p.name, key)
            br.lines.append(line)

        for qc in self.quote_costs:
            markup = qc.markup if qc.markup is not None else self.settings.markup_for(qc.cost_area)
            cell = CostCell(qc.name, qc.cost_area, qc.total(sets), markup=markup)
            cell.override = _override(overrides, sets, QUOTE, qc.name)
            br.quote_cells[qc.name] = cell
        br.notes.extend(mat_notes.get(QUOTE, []))
        return br

    # ------------------------------------------------------------- material

    def _material(self, parts, counts) -> Tuple[Dict[str, Optional[float]], Dict[str, List[str]]]:
        """{line name: material cost per unit (None = couldn't price)} for
        every line that has a material cell, plus notes per line."""
        cost: Dict[str, Optional[float]] = {}
        notes: Dict[str, List[str]] = {}
        sheets = [p for p in parts if p.part_type == "sheet"]
        bars = [p for p in parts if p.part_type in ("tube", "section")]
        if sheets:
            if self.settings.material_mode == "nest":
                self._sheet_nested(sheets, counts, cost, notes)
            else:
                self._sheet_estimate(sheets, cost, notes)
        if bars:
            self._bar_nested(bars, counts, cost, notes)
        for p in parts:
            if p.part_type == "machined":
                cost[p.name] = p.blank_cost
                if p.blank_cost is None:
                    notes.setdefault(p.name, []).append("machined part has no blank_cost")
        return cost, notes

    def _alloc_weight(self, p: PartFeatures) -> float:
        return p.gross_area if self.settings.allocation == "bbox" else p.net_area

    def _sheet_nested(self, sheets, counts, cost, notes):
        s = self.settings
        by_name = {p.name: p for p in sheets}
        nest_parts = []
        for p in sheets:
            if not p.outer:
                cost[p.name] = None
                notes.setdefault(p.name, []).append("no flat pattern to nest")
                continue
            nest_parts.append(Part(p.name, p.outer, p.holes, quantity=counts[p.name],
                                   material=p.material, thickness=p.thickness))
        if not nest_parts:
            return
        stock = copy.deepcopy(self.sheet_stock)
        jobs = inventory.run_job(nest_parts, stock, kerf=s.kerf, **s.run_job_kwargs, **s.nester_kwargs)
        for job in jobs:
            placed: Dict[str, int] = {}
            group_total = 0.0
            unpriced = set()
            for sheet, stk in zip(job.sheets, job.sheet_stock):
                for pp in sheet:
                    placed[pp.name] = placed.get(pp.name, 0) + 1
                sheet_cost = stk.material_cost()
                if sheet_cost is None:
                    unpriced.add(stk.id or f"{stk.width:g}x{stk.height:g}")
                    continue
                if stk.is_remnant:
                    sheet_cost *= s.remnant_price_factor
                group_total += sheet_cost - self._sheet_scrap_credit(stk, sheet)
            unplaced: Dict[str, int] = {}
            for name in job.unplaced:
                unplaced[name] = unplaced.get(name, 0) + 1
            for name, n in unplaced.items():
                notes.setdefault(name, []).append(
                    f"{n} of {counts[name]} didn't nest on any {job.material} {job.thickness:g} mm stock "
                    f"-- material covers the placed units only")
            weights = {name: self._alloc_weight(by_name[name]) * n for name, n in placed.items()}
            total_w = sum(weights.values())
            group_names = {p.name for p in nest_parts
                           if (p.material or "unspecified", p.thickness if p.thickness is not None else 0.0)
                           == (job.material, job.thickness)}
            for name in group_names:
                if unpriced:
                    cost[name] = None
                    notes.setdefault(name, []).append(f"stock {', '.join(sorted(unpriced))} has no price/density")
                elif name not in placed or total_w <= 0:
                    cost[name] = None
                else:
                    cost[name] = group_total * weights[name] / total_w / placed[name]

    def _sheet_scrap_credit(self, stk, placed_parts) -> float:
        if not self.settings.scrap_credit or stk.scrap_price_per_kg is None or stk.density_g_cm3 is None:
            return 0.0
        scrap_area = max(stk.area() - sum(pp.net_area() for pp in placed_parts), 0.0)
        return scrap_area * stk.thickness * stk.density_g_cm3 / 1e6 * stk.scrap_price_per_kg

    def _sheet_estimate(self, sheets, cost, notes):
        s = self.settings
        for p in sheets:
            w, h = p.bbox
            stk = inventory.best_stock_for(p.material, p.thickness, self.sheet_stock, w, h,
                                           prefer_remnants=False) if p.material and p.thickness else None
            if stk is None or stk.price_per_kg is None:
                cost[p.name] = None
                notes.setdefault(p.name, []).append("no priced stock matches this material/thickness")
                continue
            density = stk.density_g_cm3 or p.density()
            if density is None:
                cost[p.name] = None
                notes.setdefault(p.name, []).append("no density for this material")
                continue
            kg_per_mm2 = p.thickness * density / 1e6
            consumed = self._alloc_weight(p) / s.estimate_utilisation
            value = consumed * kg_per_mm2 * stk.price_per_kg
            if s.scrap_credit and stk.scrap_price_per_kg is not None:
                value -= max(consumed - p.net_area, 0.0) * kg_per_mm2 * stk.scrap_price_per_kg
            cost[p.name] = value
            notes.setdefault(p.name, []).append(
                f"material estimated at {s.estimate_utilisation:.0%} utilisation (not nested)")

    def _bar_nested(self, bars, counts, cost, notes):
        s = self.settings
        groups: Dict[Tuple[Optional[str], str], List[PartFeatures]] = {}
        for p in bars:
            if p.profile is None or p.length <= 0:
                cost[p.name] = None
                notes.setdefault(p.name, []).append("no profile/length to nest")
                continue
            groups.setdefault((normalize_material(p.material), p.profile.designation), []).append(p)

        for (material, designation), group in groups.items():
            stock = [b for b in self.bar_stock
                     if normalize_material(b.material) == material and b.profile == designation]
            res = nest_bars([(p.name, p.length, counts[p.name]) for p in group], stock,
                            kerf=s.bar_kerf, trim_start=s.bar_trim_start, trim_end=s.bar_trim_end,
                            min_remnant=s.bar_min_remnant)
            total = 0.0
            unpriced = False
            for bar in res.bars:
                is_scrap = s.bar_min_remnant is None or bar.offcut < s.bar_min_remnant
                charged = bar.stock.length if (s.bar_charge == "full" or is_scrap) \
                    else bar.stock.length - bar.offcut
                c = bar.stock.material_cost(charged)
                if c is None:
                    unpriced = True
                    continue
                if bar.stock.is_remnant:
                    c *= s.remnant_price_factor
                if is_scrap and s.scrap_credit and bar.stock.scrap_price_per_kg is not None:
                    kg = bar.stock.weight_kg(bar.offcut)
                    if kg is not None:
                        c -= kg * bar.stock.scrap_price_per_kg
                total += c
            placed: Dict[str, int] = {}
            for bar in res.bars:
                for name, _ in bar.pieces:
                    placed[name] = placed.get(name, 0) + 1
            unplaced: Dict[str, int] = {}
            for name in res.unplaced:
                unplaced[name] = unplaced.get(name, 0) + 1
            total_len = sum(bar.piece_length for bar in res.bars)
            for p in group:
                if p.name in unplaced:
                    notes.setdefault(p.name, []).append(
                        f"{unplaced[p.name]} of {counts[p.name]} didn't fit any {designation} bar stock")
                if unpriced:
                    cost[p.name] = None
                    notes.setdefault(p.name, []).append(f"{designation} bar stock has no price")
                elif p.name not in placed or total_len <= 0:
                    cost[p.name] = None
                else:
                    cost[p.name] = total * p.length / total_len


def _override(overrides: dict, sets: int, line: str, key: str) -> Optional[float]:
    if (sets, line, key) in overrides:
        return overrides[(sets, line, key)]
    return overrides.get((line, key))
