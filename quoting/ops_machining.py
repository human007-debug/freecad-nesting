"""
quoting/ops_machining.py
------------------------
Phase 2b.4: per-feature machining. Deliberately NOT a milling-time
estimate -- every figure here is flagged `estimate=True` so reports can show
it's less accurate than cutting and bending.

  holes      count x seconds per feature, by kind (plain / tapped /
             countersink / counterbore) and diameter band (MachiningRate)
  pockets    volume removed / removal rate (cm^3/min, the "pocket" row)
  setup      per machining setup (`setups`, default 1 per batch)

cost = (sum(count x time per feature) + pocket time) x rate  +  setup x rate.

Which holes count: on a sheet part only non-plain holes (tapping,
countersinking, counterboring) -- plain through-holes there are laser
contours. On any other part type, every hole (see
PartFeatures.machined_hole_features).
"""

from quoting.operations import OpCost, Operation
from quoting.rates import MachiningRate


class Machining(Operation):
    name = "machining"
    cost_area = "machining"
    estimate = True

    def __init__(self, rates, setups: int = 1):
        super().__init__(rates)
        self.setups = setups

    def applies_to(self, part):
        return bool(part.machined_hole_features()) or bool(part.pockets)

    def cost(self, part, qty):
        oc = OpCost(self.name, self.cost_area, estimate=True)
        setup_min = 0.0
        rate = None
        for h in part.machined_hole_features():
            row = self.rates.lookup(MachiningRate, h.kind, h.diameter)
            minutes = h.count * row.time_s / 60.0
            oc.run_min += minutes
            oc.unit_cost += minutes * row.rate_per_hr / 60.0
            setup_min = max(setup_min, row.setup_min)
            rate = row.rate_per_hr if rate is None else max(rate, row.rate_per_hr)
        for p in part.pockets:
            row = self.rates.lookup(MachiningRate, "pocket", 0.0)
            if row.removal_cm3_min <= 0:
                raise ValueError("the pocket MachiningRate row needs a removal_cm3_min")
            minutes = (p.volume / 1000.0) / row.removal_cm3_min
            oc.run_min += minutes
            oc.unit_cost += minutes * row.rate_per_hr / 60.0
            setup_min = max(setup_min, row.setup_min)
            rate = row.rate_per_hr if rate is None else max(rate, row.rate_per_hr)
        oc.setup_min = setup_min * self.setups
        oc.setup_cost = oc.setup_min * (rate or 0.0) / 60.0
        return oc

    def setup_time(self, part):
        return self.cost(part, 1).setup_min

    def run_time(self, part):
        return self.cost(part, 1).run_min
