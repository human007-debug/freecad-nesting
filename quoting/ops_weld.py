"""
quoting/ops_weld.py
-------------------
Phase 2b.2: welding.

Weld length comes from where parts touch in an assembly: the FreeCAD side
finds edges shared by two parts' solids and hands over plain
(part_a, part_b, length_mm) contact records; `joints_from_contacts` sums
those per pair of parts into one `WeldJoint` each. Every joint defaults to
an enabled fillet weld -- contact lines over-count on bolted assemblies,
which is why each joint has an `enabled` switch and an editable type.

Cost per assembly = sum over enabled joints of
    length (m) x minutes/m (for that weld type and thickness) x rate
    + length (m) x filler cost/m
  + fixturing setup per batch (the largest setup among the rows used).

Fallback when there are no joints: `manual_weld_length` (mm, costed as a
fillet at the part's thickness) and/or `tack_count` x seconds per tack.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from quoting.operations import OpCost, Operation
from quoting.rates import WeldRate

WELD_TYPES = ("fillet", "butt")


@dataclass
class WeldJoint:
    part_a: str
    part_b: str
    length: float                   # mm
    weld_type: str = "fillet"
    thickness: Optional[float] = None  # mm -- the thinner of the two parts
    enabled: bool = True

    def __post_init__(self):
        if self.weld_type not in WELD_TYPES:
            raise ValueError(f"weld_type must be one of {WELD_TYPES}, got {self.weld_type!r}")


def joints_from_contacts(contacts: Iterable[Tuple[str, str, float]],
                         thickness_by_part: Optional[Dict[str, float]] = None,
                         default_type: str = "fillet", min_length: float = 0.0) -> List[WeldJoint]:
    """Merge raw contact edges into one joint per unordered pair of parts,
    total length summed; joint thickness is the thinner part's. Pairs
    whose total is below `min_length` mm (point/incidental contacts) are
    dropped."""
    thickness_by_part = thickness_by_part or {}
    totals: Dict[Tuple[str, str], float] = {}
    for a, b, length in contacts:
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        totals[key] = totals.get(key, 0.0) + float(length)
    joints = []
    for (a, b), length in sorted(totals.items()):
        if length < min_length:
            continue
        ts = [thickness_by_part[p] for p in (a, b) if thickness_by_part.get(p) is not None]
        joints.append(WeldJoint(a, b, length, default_type, min(ts) if ts else None))
    return joints


class Welding(Operation):
    name = "welding"
    cost_area = "welding"

    def applies_to(self, part):
        return bool([j for j in part.weld_joints if j.enabled]) or bool(part.manual_weld_length) \
            or part.tack_count > 0

    def _row(self, weld_type, thickness) -> WeldRate:
        return self.rates.lookup(WeldRate, weld_type, thickness)

    def _segments(self, part):
        """(weld_type, thickness, length_mm) for everything to weld."""
        segs = []
        for j in part.weld_joints:
            if not j.enabled:
                continue
            thk = j.thickness if j.thickness is not None else part.thickness
            if thk is None:
                raise ValueError(f"{part.name}: weld joint {j.part_a}/{j.part_b} has no thickness")
            segs.append((j.weld_type, thk, j.length))
        if not segs and part.manual_weld_length:
            if part.thickness is None:
                raise ValueError(f"{part.name}: manual weld length needs the part thickness")
            segs.append(("fillet", part.thickness, part.manual_weld_length))
        return segs

    def weld_length(self, part) -> float:
        return sum(length for _, _, length in self._segments(part))

    def cost(self, part, qty):
        oc = OpCost(self.name, self.cost_area)
        setup_min = 0.0
        setup_cost = 0.0
        for weld_type, thk, length in self._segments(part):
            row = self._row(weld_type, thk)
            minutes = length / 1000.0 * row.min_per_m
            oc.run_min += minutes
            oc.unit_cost += minutes * row.rate_per_hr / 60.0 + length / 1000.0 * row.filler_per_m
            if row.setup_min >= setup_min:
                setup_min, setup_cost = row.setup_min, row.setup_min * row.rate_per_hr / 60.0
        if part.tack_count:
            thk = part.thickness
            if thk is None:
                known = [j.thickness for j in part.weld_joints if j.thickness is not None]
                thk = min(known) if known else None
            if thk is None:
                raise ValueError(f"{part.name}: tack welds need a thickness")
            row = self._row("fillet", thk)
            minutes = part.tack_count * row.tack_s / 60.0
            oc.run_min += minutes
            oc.unit_cost += minutes * row.rate_per_hr / 60.0
            if row.setup_min >= setup_min:
                setup_min, setup_cost = row.setup_min, row.setup_min * row.rate_per_hr / 60.0
        oc.setup_min, oc.setup_cost = setup_min, setup_cost
        return oc

    def setup_time(self, part):
        return self.cost(part, 1).setup_min

    def run_time(self, part):
        return self.cost(part, 1).run_min
