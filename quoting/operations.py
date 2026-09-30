"""
quoting/operations.py
---------------------
Phase 2: pluggable cost centres.

Every operation -- laser, press brake, weld, saw, machining, assembly,
paint, subcontract -- is one small `Operation` subclass with the same
interface, so the costing engine and reports never special-case a process:

    applies_to(part)      does this operation run on this part at all?
    setup_time(part)      minutes per batch (amortised over the quantity)
    run_time(part)        minutes per unit
    time(part, qty)       setup + qty x run
    cost(part, qty)       -> OpCost: setup cost per batch, cost per unit

By default cost = time x the rate row's currency/hr, plus `consumables()`
per unit. Rule-based operations (`RuleOperation`: flat fee, per part, per
kg, per m^2, per metre) override `cost()` instead of working from time.

Formulas (docs/QUOTING_PLAN.md, Phase 2):
  laser time = cut length / speed + pierces x pierce time
  bend time  = setup + hits x cycle time   (+ a tool change per extra tool)
  paint cost = area x 2 x rate             (`painting()`)

Each operation also names the COST AREA it books to (material, cutting,
bending, welding, ...) -- the engine applies a separate markup per cost
area, which is what lets a quote be negotiated one area at a time.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from quoting.features import PartFeatures
from quoting.rates import BendRate, CuttingRate, RateTable


@dataclass
class OpCost:
    operation: str
    cost_area: str
    setup_min: float = 0.0
    run_min: float = 0.0              # per unit
    setup_cost: float = 0.0           # per batch
    unit_cost: Optional[float] = 0.0  # per unit: run time + consumables; None = can't price
    markup: Optional[float] = None    # overrides the cost-area markup when set
    estimate: bool = False            # less accurate than cutting/bending (machining)
    notes: List[str] = field(default_factory=list)

    def per_unit(self, qty: int) -> Optional[float]:
        """Unit cost with the batch setup spread over `qty`."""
        if self.unit_cost is None:
            return None
        return self.setup_cost / qty + self.unit_cost

    def total(self, qty: int) -> Optional[float]:
        if self.unit_cost is None:
            return None
        return self.setup_cost + self.unit_cost * qty


class Operation:
    name = "operation"
    cost_area = "other"
    estimate = False

    def __init__(self, rates: Optional[RateTable] = None):
        self.rates = rates

    def applies_to(self, part: PartFeatures) -> bool:
        return False

    def setup_time(self, part: PartFeatures) -> float:
        return 0.0

    def run_time(self, part: PartFeatures) -> float:
        return 0.0

    def time(self, part: PartFeatures, qty: int) -> float:
        return self.setup_time(part) + qty * self.run_time(part)

    def rate_per_hr(self, part: PartFeatures) -> float:
        return 0.0

    def consumables(self, part: PartFeatures) -> float:
        """Per-unit non-labour cost (filler wire, bought-in price, ...)."""
        return 0.0

    def cost(self, part: PartFeatures, qty: int) -> OpCost:
        rate_min = self.rate_per_hr(part) / 60.0
        setup, run = self.setup_time(part), self.run_time(part)
        return OpCost(self.name, self.cost_area, setup_min=setup, run_min=run,
                      setup_cost=setup * rate_min,
                      unit_cost=run * rate_min + self.consumables(part),
                      estimate=self.estimate)


# ----------------------------------------------------------------- sheet ops

class LaserCutting(Operation):
    name = "laser"
    cost_area = "cutting"

    def applies_to(self, part):
        return part.part_type == "sheet" and part.cut_length > 0

    def _row(self, part) -> CuttingRate:
        return self.rates.lookup(CuttingRate, part.material, part.thickness)

    def setup_time(self, part):
        return self._row(part).setup_min

    def run_time(self, part):
        row = self._row(part)
        return part.cut_length / row.speed_mm_min + part.pierces * row.pierce_s / 60.0

    def rate_per_hr(self, part):
        return self._row(part).rate_per_hr


class Bending(Operation):
    """Hits = bend count. Setup covers the first tool set; every distinct
    extra tool (bend radius) a part needs adds a tool change. A part whose
    bends are UNKNOWN (DXF/STEP) doesn't get a silent zero: `applies_to`
    is False and the engine notes it, so the missing count is visible."""
    name = "bending"
    cost_area = "bending"

    def applies_to(self, part):
        return part.part_type == "sheet" and bool(part.bends)

    def _row(self, part) -> BendRate:
        return self.rates.lookup(BendRate, part.material, part.thickness)

    @staticmethod
    def tool_sets(part) -> int:
        return max(1, len({b.radius for b in part.bends or []}))

    def setup_time(self, part):
        row = self._row(part)
        return row.setup_min + row.tool_change_min * (self.tool_sets(part) - 1)

    def run_time(self, part):
        return len(part.bends) * self._row(part).hit_s / 60.0

    def rate_per_hr(self, part):
        return self._row(part).rate_per_hr


# ------------------------------------------------------------ rule-based ops

RULE_BASES = ("flat", "per_part", "per_kg", "per_m2", "per_m")


class RuleOperation(Operation):
    """Plan 2b.6: a general cost line -- painting, galvanising, powder
    coating, heat treatment, anything subcontracted.

      flat      `rate` once per batch (a lot charge)
      per_part  `rate` per unit
      per_kg    `rate` x finished weight
      per_m2    `rate` x surface area (sheet: net area x `sides`)
      per_m     `rate` x length (tube/section length, else cut length)

    `setup` is an extra per-batch charge on any basis. `markup`, when set,
    replaces the cost area's markup for this line. `part_types` limits
    which parts it applies to (None = all); `when`, if given, is a
    predicate on the part for anything finer (e.g. `lambda p:
    p.extra.get("paint")`)."""

    def __init__(self, name: str, basis: str, rate: float, setup: float = 0.0,
                 cost_area: str = "subcontract", markup: Optional[float] = None,
                 part_types: Optional[Sequence[str]] = None, sides: int = 2, when=None):
        super().__init__(None)
        if basis not in RULE_BASES:
            raise ValueError(f"basis must be one of {RULE_BASES}, got {basis!r}")
        self.name, self.basis, self.rate, self.setup = name, basis, rate, setup
        self.cost_area, self.markup = cost_area, markup
        self.part_types = tuple(part_types) if part_types else None
        self.sides, self.when = sides, when

    def applies_to(self, part):
        if self.part_types is not None and part.part_type not in self.part_types:
            return False
        return self.when is None or bool(self.when(part))

    def measure(self, part) -> Optional[float]:
        if self.basis in ("flat", "per_part"):
            return 1.0
        if self.basis == "per_kg":
            return part.weight_kg()
        if self.basis == "per_m2":
            return part.surface_area_m2(self.sides)
        if part.part_type in ("tube", "section"):
            return part.length / 1000.0
        return part.cut_length / 1000.0

    def cost(self, part, qty):
        oc = OpCost(self.name, self.cost_area, markup=self.markup, setup_cost=self.setup)
        if self.basis == "flat":
            oc.setup_cost += self.rate
            return oc
        m = self.measure(part)
        if m is None:
            oc.notes.append(f"{self.name}: {self.basis} needs a weight (density/geometry unknown)")
            oc.unit_cost = None
            return oc
        oc.unit_cost = m * self.rate
        return oc


def painting(rate_per_m2: float, setup: float = 0.0, sides: int = 2, **kw) -> RuleOperation:
    """paint cost = area x sides (default 2) x rate -- applies to parts
    whose `extra["paint"]` is truthy unless `when` is given."""
    kw.setdefault("when", lambda p: p.extra.get("paint"))
    kw.setdefault("cost_area", "finishing")
    return RuleOperation("painting", "per_m2", rate_per_m2, setup=setup, sides=sides, **kw)


def default_operations(rates: RateTable, tube_machine: str = "saw") -> List[Operation]:
    """Every built-in cost centre, in quote-column order."""
    from quoting.ops_assembly import AssemblyLabour, BoughtIn
    from quoting.ops_machining import Machining
    from quoting.ops_tube import TubeCutting
    from quoting.ops_weld import Welding
    return [LaserCutting(rates), Bending(rates), TubeCutting(rates, machine=tube_machine),
            Machining(rates), Welding(rates), AssemblyLabour(rates), BoughtIn()]
