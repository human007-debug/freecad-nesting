"""
stock_solver.py
----------------
Heuristic search over WHICH STOCK SHEETS (drawn from possibly several
different sizes) to cut a (material, thickness) group's parts from, as one
combined plan -- closes the real gap inventory.py's own module docstring
names: run_job()'s cascade commits to one best-fit size per pass and only
moves to a different size once that one's exhausted or geometrically full,
so it can never weigh "2 sheets of A + 1 of B" against "3 sheets of B" the
way a true joint solver would.

This module reuses the exact same Nester/genetic.optimize_order() engine as
everything else in this project for the actual geometric packing -- only
WHICH sheet sizes to draw from, and in what order, is new. A PLAN is just
an ordered list of specific StockSheet objects (the sequence of physical
sheets to cut, drawn from inventory.py's candidate list); "evaluating" a
plan means walking it slot by slot, nesting whatever's left onto each
slot's stock size in turn via `_fill_one_sheet()` -- a thin wrapper around
`inventory.nest_against_stock()` that takes only its FIRST sheet, so a
single-size, possibly-multi-sheet nester becomes a "fill exactly one sheet
of this size" primitive with zero changes to nester.py itself.

HONEST SCOPE: this is a heuristic (hill-climbing) search over plans, not a
provably-optimal MILP/ILP solve -- deliberately. The combinatorial space
here (a handful of candidate stock sizes, low tens of sheets per real job)
is small enough that a good local search reliably finds the same answer a
solver would in practice, and pulling in a MILP library (e.g. OR-Tools)
would be a much heavier, harder-to-vendor dependency than this project's
existing pyclipper/ezdxf for a gain that's usually marginal outside
constructed edge cases -- real nesting software (SigmaNest/DeepNest-class)
doesn't use MILP for this combined irregular-nesting + stock-mix problem
either, for the same reason. The search always starts from exactly
`inventory.greedy_best_stock_step()`'s own cascade result (the existing
`joint_stock_optimization` algorithm) as its baseline plan, so it can only
find something better, never worse -- same guarantee genetic.py's GA
already established for part ordering.

WHY HILL-CLIMBING, NOT SIMULATED ANNEALING: the objective is the
lexicographic tuple `(unplaced_count, sheets_used, total_cost, wasted_area)`
-- there's no natural single scalar "how much worse" a candidate plan is to
drive an annealing acceptance probability against (unplaced parts, sheet
count, currency, and area don't have a principled common unit), and
inventing a weighted-sum scalarization just to get annealing's "occasionally
accept a worse move" behavior would be exactly the kind of made-up
abstraction this codebase avoids elsewhere. So this only ever accepts a
strictly better neighbor (by tuple comparison, which IS well-defined
lexicographically) -- plain hill-climbing with random neighbor proposals,
bounded by both `max_iterations` and `time_budget_seconds`.

HONEST COST: each neighbor evaluation replays the ENTIRE plan from scratch
(not incrementally from where it diverges from the current plan), so one
iteration costs `len(plan)` nesting passes -- multiplied by a full GA
search per pass if `use_ga=True` is also on. Small defaults plus the hard
time budget are what keep this bounded, same reasoning as every other
"HONEST COST" callout already in this codebase (see genetic.py,
inventory.py's `joint_stock_optimization`).
"""

import random
import time
from typing import Callable, Dict, List, Optional, Tuple

import inventory
from nester import Part, PlacedPart


def _fill_one_sheet(stock, remaining: List[Part], kerf: float, use_ga: bool,
                     ga_kwargs: Optional[dict], nester_kwargs: dict, should_stop):
    """Nests `remaining` against `stock` and keeps only its FIRST sheet --
    turning `inventory.nest_against_stock()` (which may produce several
    sheets of one size to fit everything) into a "fill exactly one sheet"
    primitive. Everything not on that first sheet -- its own unplaced names,
    plus any parts that landed on a second/third sheet -- becomes leftover
    for the plan's next slot. Returns (placed_sheet_or_None, leftover_names,
    note_or_None)."""
    sheets, unplaced_names, note = inventory.nest_against_stock(
        stock, remaining, kerf, use_ga, ga_kwargs, nester_kwargs, should_stop)
    if not sheets:
        all_names = [p.name for p in remaining for _ in range(p.quantity)]
        return None, all_names, note
    placed = sheets[0]
    leftover = list(unplaced_names) + [pp.name for extra in sheets[1:] for pp in extra]
    return placed, leftover, note


def _evaluate_plan(plan: List["inventory.StockSheet"], parts: List[Part],
                    template_by_name: Dict[str, Part], kerf: float, use_ga: bool,
                    ga_kwargs: Optional[dict], nester_kwargs: dict, should_stop):
    """Walks `plan` slot by slot, nesting whatever's left onto each slot's
    stock in turn. Returns (placed_sheets, sheet_stock, unplaced_names,
    notes) -- sheet_stock[i] is the StockSheet placed_sheets[i] was cut
    from, same shape as inventory.JobResult's own fields."""
    remaining = list(parts)
    placed_sheets: List[List[PlacedPart]] = []
    sheet_stock: List["inventory.StockSheet"] = []
    notes: List[str] = []

    for stock in plan:
        if not remaining or (should_stop is not None and should_stop()):
            break
        placed, leftover_names, note = _fill_one_sheet(
            stock, remaining, kerf, use_ga, ga_kwargs, nester_kwargs, should_stop)
        if note:
            notes.append(note)
        if placed:
            placed_sheets.append(placed)
            sheet_stock.append(stock)
        remaining = inventory.names_to_parts(leftover_names, template_by_name) if leftover_names else []

    unplaced_names = [p.name for p in remaining for _ in range(p.quantity)]
    return placed_sheets, sheet_stock, unplaced_names, notes


def _plan_cost(placed_sheets: List[List[PlacedPart]], sheet_stock: List["inventory.StockSheet"],
               unplaced_names: List[str], prefer_remnants: bool = True) -> Tuple[int, int, int, float, float]:
    """Lexicographic (unplaced_count, non_remnant_sheets, sheets_used,
    total_cost, wasted_area) -- unplaced comes first because leaving parts
    uncut is worse than any cost/waste difference; cost is +inf if any
    sheet used is unpriced, so a fully-priced plan always wins a tie
    against a partially-priced one, and two all-unpriced plans still fall
    through to comparing wasted area.

    `non_remnant_sheets` (count of plan slots that are NOT a remnant, 0 when
    `prefer_remnants` is False) sits right after unplaced_count and before
    sheets_used/cost/waste -- a plan that uses more remnants always beats
    one that uses fewer, regardless of any cost/waste difference, matching
    inventory.py's own always-remnants-first default (see its
    `_stock_objective()`). Without this, hill-climbing could quietly
    trade a usable remnant for a full sheet that merely scores a little
    better on cost/waste -- exactly the failure mode `prefer_remnants`
    exists to prevent, and this search's neighbor moves (`_random_neighbor`)
    don't know or care about remnant status themselves, so the cost
    function is the only place this preference can actually be enforced."""
    n_unplaced = len(unplaced_names)
    n_sheets = len(placed_sheets)
    non_remnant_sheets = sum(1 for s in sheet_stock if not s.is_remnant) if prefer_remnants else 0
    priced = bool(sheet_stock) and all(s.material_cost() is not None for s in sheet_stock)
    cost = sum(s.material_cost() for s in sheet_stock) if priced else float("inf")
    used_area = sum(pp.net_area() for sheet in placed_sheets for pp in sheet)
    total_area = sum(s.area() for s in sheet_stock)
    wasted = total_area - used_area
    return (n_unplaced, non_remnant_sheets, n_sheets, cost, wasted)


def _plan_respects_quantities(plan: List["inventory.StockSheet"]) -> bool:
    """A stock sheet with a finite `.quantity` can't appear in the plan
    more times than are actually on hand (counted by object identity, since
    two distinct StockSheet entries can share the same size)."""
    counts: Dict[int, int] = {}
    for s in plan:
        counts[id(s)] = counts.get(id(s), 0) + 1
    return all(s.quantity is None or counts[id(s)] <= s.quantity for s in plan)


def _random_neighbor(plan: List["inventory.StockSheet"], material: str, thickness: float,
                      stock_inventory: List["inventory.StockSheet"], min_w: float, min_h: float,
                      rng: random.Random) -> Optional[List["inventory.StockSheet"]]:
    """One of two moves: swap two slots' order (order matters -- an earlier
    fill changes what's left for later slots), or replace one slot's stock
    choice with a different viable candidate size. Returns None if there's
    no useful move available (e.g. only one candidate size exists)."""
    if len(plan) < 2:
        move = "swap_size"
    else:
        move = rng.choice(("swap_order", "swap_size"))

    neighbor = list(plan)
    if move == "swap_order":
        i, j = rng.sample(range(len(neighbor)), 2)
        neighbor[i], neighbor[j] = neighbor[j], neighbor[i]
        return neighbor

    candidates = inventory.candidate_stocks_for(material, thickness, stock_inventory, min_w, min_h)
    if not candidates:
        return None
    idx = rng.randrange(len(neighbor))
    neighbor[idx] = rng.choice(candidates)
    return neighbor


def _construct_greedy_plan(material: str, thickness: float, parts: List[Part],
                            stock_inventory: List["inventory.StockSheet"], kerf: float, use_ga: bool,
                            ga_kwargs: Optional[dict], nester_kwargs: dict, should_stop,
                            max_stock_switches: int, prefer_remnants: bool = True):
    """Repeats `inventory.greedy_best_stock_step()` -- the exact algorithm
    `joint_stock_optimization` already runs per cascade step -- recording
    each step's chosen StockSheet into an explicit plan list as it goes.
    This is the search's starting point (never worse than today's existing
    `joint_stock_optimization` result, since it's the very same steps) and,
    since it already nests as it constructs, doubles as that baseline
    plan's own evaluation -- no separate re-evaluation needed. Returns
    (plan, placed_sheets, sheet_stock, unplaced_names, notes)."""
    template_by_name = {p.name: p for p in parts}
    remaining = list(parts)
    plan: List["inventory.StockSheet"] = []
    placed_sheets: List[List[PlacedPart]] = []
    sheet_stock: List["inventory.StockSheet"] = []
    notes: List[str] = []

    for _ in range(max_stock_switches):
        if not remaining or (should_stop is not None and should_stop()):
            break
        min_w, min_h = inventory.group_bbox(remaining)
        stock, sheets, unplaced_names, note = inventory.greedy_best_stock_step(
            material, thickness, remaining, stock_inventory, kerf, use_ga,
            ga_kwargs, nester_kwargs, should_stop, min_w, min_h, prefer_remnants)
        if stock is None:
            break
        if note:
            notes.append(note)
        plan.append(stock)
        placed_sheets.extend(sheets)
        sheet_stock.extend([stock] * len(sheets))
        if not unplaced_names or len(unplaced_names) == sum(p.quantity for p in remaining):
            remaining = inventory.names_to_parts(unplaced_names, template_by_name) if unplaced_names else []
            break
        remaining = inventory.names_to_parts(unplaced_names, template_by_name)

    unplaced_names = [p.name for p in remaining for _ in range(p.quantity)]
    return plan, placed_sheets, sheet_stock, unplaced_names, notes


def optimize_stock_plan(material: str, thickness: float, parts: List[Part],
                         stock_inventory: List["inventory.StockSheet"], kerf: float = 0.0,
                         use_ga: bool = False, ga_kwargs: Optional[dict] = None,
                         nester_kwargs: Optional[dict] = None, max_stock_switches: int = 20,
                         time_budget_seconds: Optional[float] = 20.0, max_iterations: int = 200,
                         prefer_remnants: bool = True,
                         seed: Optional[int] = None, should_stop: Optional[Callable[[], bool]] = None):
    """Searches for a good ordered PLAN of specific stock sheets (possibly
    mixed sizes) to cut `parts` (one (material, thickness) group) from as a
    single combined job, instead of `inventory.run_job()`'s default
    per-pass single-size cascade. See module docstring for the algorithm
    and its honest scope/cost. Returns (sheets, sheet_stock, unplaced_names,
    notes) -- the same shape `inventory.run_job()`'s cascade loop already
    accumulates into a JobResult.

    `prefer_remnants` (default True, forwarded to both the construction
    baseline and `_plan_cost()`) keeps this search from undoing
    inventory.py's always-remnants-first default in the name of a
    marginally cheaper/less-wasteful all-full-sheet plan -- see
    `_plan_cost()`'s docstring."""
    nester_kwargs = nester_kwargs or {}
    template_by_name = {p.name: p for p in parts}
    rng = random.Random(seed)

    plan, best_sheets, best_sheet_stock, best_unplaced, best_notes = _construct_greedy_plan(
        material, thickness, parts, stock_inventory, kerf, use_ga, ga_kwargs, nester_kwargs,
        should_stop, max_stock_switches, prefer_remnants)
    best_cost = _plan_cost(best_sheets, best_sheet_stock, best_unplaced, prefer_remnants)

    if len(plan) < 2:
        return best_sheets, best_sheet_stock, best_unplaced, best_notes

    min_w, min_h = inventory.group_bbox(parts)
    current_plan, current_cost = plan, best_cost
    start = time.time()

    for _ in range(max_iterations):
        if should_stop is not None and should_stop():
            break
        if time_budget_seconds is not None and time.time() - start > time_budget_seconds:
            break

        neighbor = _random_neighbor(current_plan, material, thickness, stock_inventory, min_w, min_h, rng)
        if neighbor is None or not _plan_respects_quantities(neighbor):
            continue

        n_sheets, n_stock, n_unplaced, n_notes = _evaluate_plan(
            neighbor, parts, template_by_name, kerf, use_ga, ga_kwargs, nester_kwargs, should_stop)
        n_cost = _plan_cost(n_sheets, n_stock, n_unplaced, prefer_remnants)

        if n_cost < current_cost:
            current_plan, current_cost = neighbor, n_cost
            if n_cost < best_cost:
                best_cost = n_cost
                best_sheets, best_sheet_stock, best_unplaced, best_notes = n_sheets, n_stock, n_unplaced, n_notes

    return best_sheets, best_sheet_stock, best_unplaced, best_notes
