"""
quoting/ops_assembly.py
-----------------------
Phase 2b.5: assembly labour, bought-in parts and quote-level costs.

  AssemblyLabour   base time + time per part + time per fastener, per
                   assembly (an `assembly` line's part_count/fastener_count)
  BoughtIn         a `purchased` line's unit cost (PEM nuts, bolts,
                   hinges, ...), booked to the "purchased" cost area with
                   the line's own markup when it has one
  QuoteCost        a cost that belongs to the whole quote, not to any line
                   -- packing, transport, inspection, certificates. The
                   engine adds these once per quantity break.
"""

from dataclasses import dataclass
from typing import Optional

from quoting.operations import OpCost, Operation
from quoting.rates import AssemblyRate


class AssemblyLabour(Operation):
    name = "assembly"
    cost_area = "assembly"

    def __init__(self, rates, kind: str = "*"):
        super().__init__(rates)
        self.kind = kind

    def applies_to(self, part):
        return part.part_type == "assembly" and (part.part_count > 0 or part.fastener_count > 0)

    def _row(self) -> AssemblyRate:
        return self.rates.lookup(AssemblyRate, self.kind)

    def run_time(self, part):
        row = self._row()
        return row.base_min + row.per_part_min * part.part_count + row.per_fastener_min * part.fastener_count

    def rate_per_hr(self, part):
        return self._row().rate_per_hr


class BoughtIn(Operation):
    name = "bought-in"
    cost_area = "purchased"

    def __init__(self):
        super().__init__(None)

    def applies_to(self, part):
        return part.part_type == "purchased"

    def cost(self, part, qty):
        oc = OpCost(self.name, self.cost_area, markup=part.markup)
        if part.unit_cost is None:
            oc.unit_cost = None
            oc.notes.append(f"{part.name}: bought-in part has no unit cost")
        else:
            oc.unit_cost = part.unit_cost
        if part.supplier:
            oc.notes.append(f"supplier: {part.supplier}")
        return oc


@dataclass
class QuoteCost:
    """A once-per-quote cost. `per_set` > 0 adds that much per quoted set
    on top of `amount` (e.g. packing that scales with quantity)."""
    name: str
    amount: float = 0.0
    per_set: float = 0.0
    cost_area: str = "quote"
    markup: Optional[float] = None

    def total(self, sets: int) -> float:
        return self.amount + self.per_set * sets
