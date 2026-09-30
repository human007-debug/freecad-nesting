"""
bar_nest.py
-----------
1D length nesting for bar, tube and section stock -- the linear sibling of
stock_solver.py's sheet plans (docs/QUOTING_PLAN.md, Phase 2b.3). Plain
Python, no dependencies.

MODEL: a stock bar of length L loses `trim_start` at its head (squaring the
mill end) and must keep `trim_end` at its tail, so its usable length is
L - trim_start - trim_end. Every piece cut from it consumes its own length
plus one saw/laser `kerf` (the cut that frees it). Whatever is left past
the last cut is the bar's offcut; an offcut of at least `min_remnant` goes
back to stock as a remnant (`BarNestResult.remnants`), anything shorter is
scrap.

ALGORITHM:
  1. First-fit decreasing: longest piece first, into the first open bar
     with room; a new bar is opened from the first stock entry that has
     one left and is long enough -- remnants first when `prefer_remnants`,
     then the longest stock (so small pieces can fill it).
  2. Local improvement: single-piece moves and pairwise swaps between
     bars, accepted only when they raise the sum of squared bar fills
     (remnant bars are only filled, never drained, when preferred).
     That objective prefers some bars full and others empty, so it
     steadily drains the emptiest bar -- when one empties it's dropped.
     This is what fixes FFD's classic misses (e.g. 5,5,4,4,3,3,3,3 into
     bars of 10: FFD opens 4 bars, the swaps get it to the optimal 3).
  3. Downsize: each bar is switched to the shortest available stock entry
     that still holds its pieces (remnants first when preferred).

HONEST SCOPE: a heuristic, not an exact cutting-stock solve, same stance
as stock_solver.py -- on quoting-sized jobs it reliably finds the obvious
packings, and the tests pin known optimal ones.
"""

import itertools
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

EPS = 1e-6


@dataclass
class BarStock:
    material: str
    profile: str                          # section designation, e.g. "RHS 100x50x3"
    length: float                         # mm
    quantity: Optional[int] = None        # None = unlimited (reorderable standard length)
    is_remnant: bool = False
    price_per_m: Optional[float] = None
    price_per_kg: Optional[float] = None
    kg_per_m: Optional[float] = None      # needed for per-kg pricing and scrap weight
    scrap_price_per_kg: Optional[float] = None
    id: Optional[str] = None

    def cost_per_m(self) -> Optional[float]:
        if self.price_per_m is not None:
            return self.price_per_m
        if self.price_per_kg is not None and self.kg_per_m is not None:
            return self.price_per_kg * self.kg_per_m
        return None

    def material_cost(self, length: Optional[float] = None) -> Optional[float]:
        """Cost of `length` mm of this stock (default: the whole bar)."""
        per_m = self.cost_per_m()
        if per_m is None:
            return None
        return per_m * (self.length if length is None else length) / 1000.0

    def weight_kg(self, length: Optional[float] = None) -> Optional[float]:
        if self.kg_per_m is None:
            return None
        return self.kg_per_m * (self.length if length is None else length) / 1000.0


@dataclass
class Bar:
    stock: BarStock
    kerf: float
    trim_start: float
    trim_end: float
    pieces: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def usable(self) -> float:
        return self.stock.length - self.trim_start - self.trim_end

    @property
    def used(self) -> float:
        """Usable length consumed: pieces plus one kerf each."""
        return sum(length + self.kerf for _, length in self.pieces)

    @property
    def free(self) -> float:
        return self.usable - self.used

    @property
    def offcut(self) -> float:
        """Physical length left on the bar past the last cut."""
        return self.stock.length - self.trim_start - self.used

    def fits(self, length: float) -> bool:
        return length + self.kerf <= self.free + EPS

    @property
    def piece_length(self) -> float:
        return sum(length for _, length in self.pieces)


@dataclass
class BarNestResult:
    bars: List[Bar] = field(default_factory=list)
    unplaced: List[str] = field(default_factory=list)
    remnants: List[BarStock] = field(default_factory=list)

    def utilisation(self) -> float:
        total = sum(b.stock.length for b in self.bars)
        return sum(b.piece_length for b in self.bars) / total if total else 0.0

    def stock_used(self) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for b in self.bars:
            counts[id(b.stock)] = counts.get(id(b.stock), 0) + 1
        return counts


def _expand(pieces) -> List[Tuple[str, float]]:
    """(name, length) or (name, length, qty) -> one (name, length) per piece."""
    out = []
    for p in pieces:
        name, length = p[0], float(p[1])
        qty = int(p[2]) if len(p) > 2 else 1
        if length <= 0:
            raise ValueError(f"{name}: piece length must be positive")
        out.extend([(name, length)] * qty)
    return out


def _stock_order(stocks: Sequence[BarStock], prefer_remnants: bool) -> List[BarStock]:
    return sorted(stocks, key=lambda s: (prefer_remnants and not s.is_remnant, -s.length))


class _Availability:
    def __init__(self, stocks):
        self.left = {id(s): s.quantity for s in stocks}

    def has(self, s):
        q = self.left[id(s)]
        return q is None or q > 0

    def take(self, s):
        if self.left[id(s)] is not None:
            self.left[id(s)] -= 1

    def give_back(self, s):
        if self.left[id(s)] is not None:
            self.left[id(s)] += 1


def nest_bars(pieces: Iterable, stocks: Sequence[BarStock], kerf: float = 0.0,
              trim_start: float = 0.0, trim_end: float = 0.0, min_remnant: Optional[float] = None,
              prefer_remnants: bool = True, improve: bool = True,
              max_passes: int = 200) -> BarNestResult:
    """Pack `pieces` -- (name, length_mm[, qty]) tuples -- into `stocks`
    (all assumed to be the same material and profile; the caller groups).
    Input stock quantities are not modified. See the module docstring."""
    items = sorted(_expand(pieces), key=lambda p: -p[1])
    order = _stock_order(stocks, prefer_remnants)
    avail = _Availability(stocks)
    result = BarNestResult()

    for name, length in items:
        target = next((b for b in result.bars if b.fits(length)), None)
        if target is None:
            stock = next((s for s in order if avail.has(s)
                          and length + kerf <= s.length - trim_start - trim_end + EPS), None)
            if stock is None:
                result.unplaced.append(name)
                continue
            avail.take(stock)
            target = Bar(stock, kerf, trim_start, trim_end)
            result.bars.append(target)
        target.pieces.append((name, length))

    if improve:
        _improve(result.bars, avail, max_passes, prefer_remnants)
        _downsize(result.bars, order, avail, prefer_remnants)

    if min_remnant is not None:
        for b in result.bars:
            if b.offcut >= min_remnant - EPS and b.offcut > EPS:
                result.remnants.append(replace(b.stock, length=b.offcut, quantity=1,
                                               is_remnant=True, id=None))
    return result


def _score(bars) -> float:
    return sum(b.used ** 2 for b in bars)


def _improve(bars: List[Bar], avail: _Availability, max_passes: int, prefer_remnants: bool):
    for _ in range(max_passes):
        if not _one_improving_move(bars, prefer_remnants):
            break
        for b in [b for b in bars if not b.pieces]:
            bars.remove(b)
            avail.give_back(b.stock)


def _one_improving_move(bars: List[Bar], prefer_remnants: bool) -> bool:
    """Apply the first move (emptiest bar first) that raises sum(used^2).
    With `prefer_remnants`, a remnant bar is never drained (it may still
    receive pieces) -- emptying it would trade a remnant for new stock."""
    by_fill = sorted(bars, key=lambda b: b.used)
    for src in by_fill:
        if prefer_remnants and src.stock.is_remnant:
            continue
        # Move one piece out of src into a fuller bar.
        for i, (name, length) in enumerate(src.pieces):
            for dst in reversed(by_fill):
                if dst is src or not dst.fits(length):
                    continue
                if dst.used >= src.used - EPS:  # moving to a fuller-or-equal bar raises the score
                    dst.pieces.append(src.pieces.pop(i))
                    return True
        # Swap a piece of src with a shorter piece of another bar, so the
        # other bar gets fuller (it must already be at least as full).
        for dst in reversed(by_fill):
            if dst is src or dst.used < src.used - EPS:
                continue
            for (i, (_, a)), (j, (_, b)) in itertools.product(enumerate(src.pieces), enumerate(dst.pieces)):
                delta = a - b
                if delta <= EPS or delta > dst.free + EPS:
                    continue
                before = src.used ** 2 + dst.used ** 2
                after = (src.used - delta) ** 2 + (dst.used + delta) ** 2
                if after > before + EPS:
                    src.pieces[i], dst.pieces[j] = dst.pieces[j], src.pieces[i]
                    return True
    return False


def _downsize(bars: List[Bar], order: List[BarStock], avail: _Availability, prefer_remnants: bool):
    for b in bars:
        need = b.used
        best = b.stock
        for s in order:
            if s is b.stock or not avail.has(s):
                continue
            if s.length - b.trim_start - b.trim_end + EPS < need:
                continue
            rank_s = (prefer_remnants and not s.is_remnant, s.length)
            rank_b = (prefer_remnants and not best.is_remnant, best.length)
            if rank_s < rank_b:
                best = s
        if best is not b.stock:
            avail.give_back(b.stock)
            avail.take(best)
            b.stock = best
