"""
quoting/ops_tube.py
-------------------
Phase 2b.3: tube and section cutting time. (The material side -- packing
lengths into stock bars -- is bar_nest.py, driven from costing.py.)

Two machines, chosen per operation instance:

  saw        time per end, by section-size band (SawRate.cut_s), x the
             end's multiplier. Two ends per part.
  laser      tube laser: each end cuts the outer perimeter at the wall's
             speed (x the end's multiplier) plus a pierce, plus any
             cut-outs along the length (cutout_length / speed + one pierce
             per cut-out), plus load/unload per part.

End multipliers default to square 1.0, mitre 1.5, cope 3.0 -- a mitre is
a longer, angled cut and usually a second set-up; a cope is a profiled
end that on a saw means several cuts plus hand fitting.
"""

from typing import Dict, Optional

from quoting.operations import Operation
from quoting.rates import SawRate, TubeLaserRate

DEFAULT_END_MULTIPLIERS = {"square": 1.0, "mitre": 1.5, "cope": 3.0}
MACHINES = ("saw", "laser")


class TubeCutting(Operation):
    name = "tube cutting"
    cost_area = "cutting"

    def __init__(self, rates, machine: str = "saw", end_multipliers: Optional[Dict[str, float]] = None):
        super().__init__(rates)
        if machine not in MACHINES:
            raise ValueError(f"machine must be one of {MACHINES}, got {machine!r}")
        self.machine = machine
        self.end_multipliers = {**DEFAULT_END_MULTIPLIERS, **(end_multipliers or {})}

    def applies_to(self, part):
        return part.part_type in ("tube", "section") and part.profile is not None and part.length > 0

    def end_factor(self, part) -> float:
        """Sum of both ends' multipliers (2.0 for two square ends)."""
        try:
            return sum(self.end_multipliers[e] for e in part.end_cuts)
        except KeyError as e:
            raise ValueError(f"{part.name}: unknown end cut {e.args[0]!r} "
                             f"(known: {sorted(self.end_multipliers)})") from None

    def _row(self, part):
        if self.machine == "saw":
            return self.rates.lookup(SawRate, None, part.profile.max_dimension)
        wall = part.profile.wall if part.profile.wall is not None else part.thickness
        return self.rates.lookup(TubeLaserRate, part.material, wall)

    def setup_time(self, part):
        return self._row(part).setup_min

    def run_time(self, part):
        row = self._row(part)
        ends = self.end_factor(part)
        if self.machine == "saw":
            return ends * row.cut_s / 60.0
        perimeter = part.profile.perimeter
        if not perimeter:
            raise ValueError(f"{part.name}: tube laser time needs the section perimeter")
        end_min = ends * perimeter / row.speed_mm_min + len(part.end_cuts) * row.pierce_s / 60.0
        cutout_min = part.cutout_length / row.speed_mm_min + part.cutout_count * row.pierce_s / 60.0
        return end_min + cutout_min + row.load_s / 60.0

    def rate_per_hr(self, part):
        return self._row(part).rate_per_hr
