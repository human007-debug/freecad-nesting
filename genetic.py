"""
genetic.py
----------
Genetic-algorithm search over PART ORDERING for nester.Nester.

nester.py already finds the exact NFP-tightest POSITION for a part given
whatever is already placed before it, trying every allowed ROTATION and
keeping the best fit -- both exact, not heuristic (see nester.py's
docstring). What it can't do on its own is reconsider ORDER: Nester.run()
places parts strictly largest-area-first, one at a time, and never
revisits an earlier part's placement even when a later part would have
fit the remaining space better first. That's the real remaining heuristic
gap called out in README's "Honest limitation" section -- this module
closes it by searching part orderings with a genetic algorithm, using the
exact engine itself (Nester.place_order()) as the fitness function for
every candidate ordering it tries.

HONEST TRADE-OFF: fitness evaluation IS a full nesting run -- no cheap
approximation is used for search, since a genuinely faster proxy would
need its own separate packer to build and maintain, and would risk
optimizing for a metric that doesn't match what the real engine actually
places. So this is much slower than Nester.run() alone: population_size *
generations full nesting passes, versus one. Defaults are kept small
(population=12, generations=8) to stay in the tens-of-seconds range for a
few dozen parts; `time_budget_seconds` caps worst-case runtime for larger
jobs by returning the best ordering found so far rather than running
indefinitely. The initial population always includes today's plain
largest-first ordering, so the search can never do WORSE than
Nester.run() alone -- only find something better, or nothing better.

GENOME: a permutation of INDICES into the expanded (quantity-exploded)
part list -- not the Part objects themselves. Two parts placed with
quantity > 1 look identical to a dataclass equality check, so operating on
indices instead of values sidesteps any ambiguity in crossover/mutation
about which "copy" is which (there's no actual difference, but indices are
simpler to reason about and standard practice for permutation GAs anyway).

OPTIONAL ROTATION GENOME (`optimize_rotations=True`): a second array,
parallel to the expanded part list (indexed by ORIGINAL part identity, not
position-in-order, so it doesn't need to move when order is permuted) --
`rotation_choice[i]` selects `expanded[i].rotations[rotation_choice[i] % len(...)]`,
forced via `Nester.place_order(..., forced_rotations=...)` instead of that
engine's own exhaustive per-placement rotation search. This searches
JOINTLY over order and rotation, which the exhaustive per-placement search
structurally can't do (it can only ask "what's the best rotation for THIS
part right now", never "would a worse rotation here leave more room for
later parts"). Off by default -- HONEST TRADE-OFF: unlike the order-only
search, this mode does NOT carry the "never worse than plain Nester.run()"
guarantee below, since forcing a rotation trades away the exhaustive
per-placement search's own local guarantee for whatever the GA finds
instead; elitism still means fitness only ever improves generation over
generation, just not necessarily past what the exhaustive search alone
would already reach.

FITNESS (minimized, lexicographic): (sheets_used, unplaced_count,
-utilization_fraction). Fewer sheets always wins; among orderings using
the same sheet count, fewer unplaced parts wins (only possible from a
degenerate ordering -- everything placeable under one order remains
placeable under any other, just possibly using more sheets to do it);
among ties on both, higher material utilization wins.

OPTIONAL PARALLEL EVALUATION (`parallel=True`): every population member's
fitness evaluation is a fully independent `Nester.place_order()` call --
`Nester` only mutates its own local `sheets`/`unplaced` lists per call,
never `self` (sheet dims/kerf/margins are set once in `__init__` and never
touched again), so there is no shared-mutable-state hazard running them
concurrently. Since `pyclipper`'s NFP math is CPU-bound C code, real
parallelism needs separate processes, not threads (the GIL). A
`ProcessPoolExecutor` is created once for the whole search (not per
generation); its `initializer` builds one `Nester` + the `expanded` part
list per worker process up front, so neither is re-pickled per individual.
HONEST TRADE-OFFS, both because a UI callback can't cross a process
boundary:
  - `placement_callback` (live per-part animation) can't run inside a
    worker. Instead of disabling parallel mode whenever a callback is
    given -- which would make it dead on arrival, since both front ends
    always pass one -- each generation's WINNING candidate alone is
    replayed once, serially, through the ordinary `evaluate()` closure
    (which already forwards `on_placed=placement_callback`), in the main
    process WHILE the workers evaluate the next generation (the base
    order is replayed during the first one), so the view animates during
    the wait instead of freezing through it. This is
    arguably a nicer live view than serial mode's, which fires the
    callback for every population member's every placement attempt.
  - `should_stop` is checked once per generation, before that generation's
    batch is submitted, instead of before every individual -- a Stop click
    may wait for the current generation's batch (evaluated in parallel
    across `max_workers` workers) rather than aborting mid-generation.
DETERMINISM: for the same `seed` and a `should_stop` that never fires,
`parallel=True` and `parallel=False` produce byte-identical final results
-- evaluation order never affects a fitness computation, and the only RNG
consumption (crossover/mutation) stays in the main process either way.
Verified by running the same seeded search both ways on demo.py's 49-part
scenario. STARTUP METHOD: explicitly forces multiprocessing's `fork`
context (`multiprocessing.get_context("fork")`) rather than trusting
whatever the platform/Python-version default happens to be -- discovered
during verification that Python 3.14 switched Linux's own default away
from `fork` to `forkserver`, which needs the calling script itself wrapped
in `if __name__ == "__main__":` (forkserver/spawn re-import the entry
script in a fresh interpreter) and would otherwise break any caller that
invokes `optimize_order(parallel=True)` at module scope, e.g. from a UI
callback. Forcing `fork` sidesteps that entirely, and is also the
behavior this module actually wants: a forked worker inherits the
parent's already-patched `sys.path`, which matters for the FreeCAD
workbench (vendors `pyclipper`/`networkx` via `InitGui.py`). `fork` isn't
available on Windows at all; there `parallel=True` falls back to
whatever `ProcessPoolExecutor` would otherwise pick (untested).
"""

import multiprocessing
import random
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, List, Optional

from nester import Nester, Part, PlacedPart

# Populated once per worker process by _init_worker(), read by
# _evaluate_in_worker() -- see optimize_order()'s `parallel=True` path.
_worker_nester: Optional[Nester] = None
_worker_expanded: Optional[List[Part]] = None


def _init_worker(sheet_w, sheet_h, kerf, margin_left, margin_right, margin_top, margin_bottom,
                  allow_hole_nesting, hole_clearance, expanded):
    global _worker_nester, _worker_expanded
    _worker_nester = Nester(sheet_w, sheet_h, kerf=kerf,
                             margin_left=margin_left, margin_right=margin_right,
                             margin_top=margin_top, margin_bottom=margin_bottom,
                             allow_hole_nesting=allow_hole_nesting, hole_clearance=hole_clearance)
    _worker_expanded = expanded


def _evaluate_in_worker(order_idx: List[int], rot_choice: Optional[List[int]]):
    """Runs inside a worker process -- must not touch anything from the
    parent's closures (nothing here does; `_worker_nester`/`_worker_expanded`
    are this process's own copies, set once by _init_worker())."""
    expanded = _worker_expanded
    ordered = [expanded[i] for i in order_idx]
    forced = None
    if rot_choice is not None:
        forced = [expanded[oi].rotations[rot_choice[oi] % len(expanded[oi].rotations)] for oi in order_idx]
    sheets, unplaced = _worker_nester.place_order(ordered, forced_rotations=forced)
    fitness = _fitness(sheets, unplaced, _worker_nester.sheet_w, _worker_nester.sheet_h)
    return fitness, sheets, unplaced


def _fitness(sheets: List[List[PlacedPart]], unplaced: List[str], sheet_w: float, sheet_h: float):
    sheets = [s for s in sheets if s]
    used_area = sum(pp.net_area() for s in sheets for pp in s)
    total_area = sheet_w * sheet_h * max(len(sheets), 1)
    utilization = used_area / total_area if total_area > 0 else 0.0
    return (len(sheets), len(unplaced), -utilization)


def _order_crossover(a: List[int], b: List[int], rng: random.Random) -> List[int]:
    """Classic OX: copy a slice from `a` into the child at the same
    positions, then fill the remaining positions with `b`'s values in
    `b`'s own order, skipping whatever the slice from `a` already used."""
    n = len(a)
    if n < 2:
        return list(a)
    i, j = sorted(rng.sample(range(n), 2))
    child: List[Optional[int]] = [None] * n
    child[i:j + 1] = a[i:j + 1]
    used = set(child[i:j + 1])
    fill_positions = [p for p in range(n) if child[p] is None]
    fill_values = [v for v in b if v not in used]
    for p, v in zip(fill_positions, fill_values):
        child[p] = v
    return child


def _swap_mutate(order: List[int], rng: random.Random):
    if len(order) < 2:
        return
    i, j = rng.sample(range(len(order)), 2)
    order[i], order[j] = order[j], order[i]


def _uniform_crossover(a: List[int], b: List[int], rng: random.Random) -> List[int]:
    """Each gene independently inherited from `a` or `b` -- unlike order,
    a rotation-choice array isn't a permutation, so there's no "used
    value" bookkeeping needed the way _order_crossover() has."""
    return [a[i] if rng.random() < 0.5 else b[i] for i in range(len(a))]


def _rotation_mutate(rot_choice: List[int], expanded: List[Part], rng: random.Random):
    if not rot_choice:
        return
    i = rng.randrange(len(rot_choice))
    n_rot = len(expanded[i].rotations)
    if n_rot > 1:
        rot_choice[i] = rng.randrange(n_rot)


def optimize_order(sheet_w: float, sheet_h: float, parts: List[Part], kerf: float = 0.0,
                    margin_left: float = 0.0, margin_right: float = 0.0,
                    margin_top: float = 0.0, margin_bottom: float = 0.0,
                    allow_hole_nesting: bool = False, hole_clearance: float = 0.0,
                    optimize_rotations: bool = False,
                    population_size: int = 12, generations: int = 8, elite_count: int = 2,
                    mutation_rate: float = 0.15, tournament_size: int = 3,
                    time_budget_seconds: Optional[float] = None, seed: Optional[int] = None,
                    progress_callback: Optional[Callable] = None,
                    placement_callback: Optional[Callable] = None,
                    should_stop: Optional[Callable[[], bool]] = None,
                    parallel: bool = False, max_workers: Optional[int] = None):
    """Search part orderings (and optionally rotation choices -- see
    `optimize_rotations` in the module docstring) with a GA; returns
    (sheets, unplaced) for the best found -- same shape as Nester.run().
    `progress_callback`, if given, is called after every generation as
    `progress_callback(generation_index, best_fitness_so_far, best_sheets_so_far, sheet_w, sheet_h)`
    -- useful for a UI to show live progress (including redrawing the
    current-best layout) during what can be a multi-minute search. See
    module docstring for the trade-offs and defaults.

    `placement_callback`, if given, is forwarded to every `place_order()`
    the fitness evaluations run (see `Nester.place_order(..., on_placed=)`
    for its exact signature) -- it fires after every part placement attempt,
    so a UI can show each candidate's parts being assembled onto the sheet
    one by one, live, during the search itself. It is purely observational;
    like `progress_callback`, it never consumes the GA's RNG or changes the
    search, so callers that replay a run with the same `seed` get the exact
    same result with or without it.

    `parallel`/`max_workers`: evaluate each generation's population across a
    process pool instead of one at a time -- see the module docstring's
    "OPTIONAL PARALLEL EVALUATION" section for the safety argument and
    honest trade-offs (mainly: `placement_callback` only ever sees each
    generation's winner, replayed once, not every candidate). `max_workers`
    defaults to `os.cpu_count()` (via `ProcessPoolExecutor`'s own default)."""
    rng = random.Random(seed)
    tournament_size = max(2, min(tournament_size, population_size))
    elite_count = max(0, min(elite_count, population_size - 1))

    n = Nester(sheet_w, sheet_h, kerf=kerf,
               margin_left=margin_left, margin_right=margin_right,
               margin_top=margin_top, margin_bottom=margin_bottom,
               allow_hole_nesting=allow_hole_nesting, hole_clearance=hole_clearance)
    for p in parts:
        n.add_part(p)
    expanded = n.expand_parts()
    size = len(expanded)
    if size <= 1:
        return n.place_order(expanded, on_placed=placement_callback)

    def evaluate(order_idx, rot_choice):
        ordered = [expanded[i] for i in order_idx]
        forced = None
        if rot_choice is not None:
            forced = [expanded[oi].rotations[rot_choice[oi] % len(expanded[oi].rotations)] for oi in order_idx]
        sheets, unplaced = n.place_order(ordered, forced_rotations=forced, on_placed=placement_callback)
        return _fitness(sheets, unplaced, sheet_w, sheet_h), sheets, unplaced

    # Seed with today's default heuristic (largest-first) plus random
    # shuffles -- when optimize_rotations is off (the default), rot_choice
    # stays None for every individual (forced_rotations=None reproduces
    # the exhaustive per-placement search exactly), so the GA still can
    # never do worse than Nester.run() alone, same guarantee as before this
    # option existed.
    base_order = sorted(range(size), key=lambda i: expanded[i].area(), reverse=True)
    if optimize_rotations:
        population = [(base_order, [0] * size)] + [
            (rng.sample(range(size), size), [rng.randrange(len(expanded[i].rotations)) for i in range(size)])
            for _ in range(population_size - 1)
        ]
    else:
        population = [(base_order, None)] + [
            (rng.sample(range(size), size), None) for _ in range(population_size - 1)
        ]

    executor = None
    if parallel:
        # Created once for the whole search, not per generation -- see
        # module docstring's "OPTIONAL PARALLEL EVALUATION" for why `fork`
        # is forced explicitly instead of trusting the platform default.
        try:
            mp_context = multiprocessing.get_context("fork")
        except ValueError:
            mp_context = None  # e.g. Windows -- no "fork" context exists there
        executor = ProcessPoolExecutor(
            max_workers=max_workers, mp_context=mp_context, initializer=_init_worker,
            initargs=(sheet_w, sheet_h, kerf, margin_left, margin_right, margin_top, margin_bottom,
                      allow_hole_nesting, hole_clearance, expanded),
        )

    best_fitness, best_sheets, best_unplaced = None, None, None
    # Parallel mode's live-view replay target: the base order before the
    # first generation, then each generation's winner.
    replay = population[0]
    start = time.time()

    try:
        for gen in range(generations):
            scored = []
            if executor is not None:
                # should_stop is checked once per generation here, not per
                # individual (unlike the serial branch below) -- see module
                # docstring for why.
                if should_stop is not None and should_stop() and best_fitness is not None:
                    return best_sheets, best_unplaced
                futures = [executor.submit(_evaluate_in_worker, order_idx, rot_choice)
                           for order_idx, rot_choice in population]
                if placement_callback is not None:
                    # Replay the previous winner WHILE the workers run this
                    # generation, purely to drive the live view -- replaying
                    # it after the batch left the canvas frozen for the whole
                    # batch and then flashed the replay by in a blink.
                    evaluate(*replay)
                for (order_idx, rot_choice), future in zip(population, futures):
                    fitness, sheets, unplaced = future.result()
                    scored.append((fitness, order_idx, rot_choice, sheets, unplaced))
                    if best_fitness is None or fitness < best_fitness:
                        best_fitness, best_sheets, best_unplaced = fitness, sheets, unplaced
                scored.sort(key=lambda t: t[0])
                replay = (scored[0][1], scored[0][2])
            else:
                for order_idx, rot_choice in population:
                    if should_stop is not None and should_stop() and best_fitness is not None:
                        # Only return a fully evaluated candidate, never a
                        # partial generation, so Stop always leaves a usable
                        # best layout.
                        return best_sheets, best_unplaced
                    fitness, sheets, unplaced = evaluate(order_idx, rot_choice)
                    scored.append((fitness, order_idx, rot_choice, sheets, unplaced))
                    if best_fitness is None or fitness < best_fitness:
                        best_fitness, best_sheets, best_unplaced = fitness, sheets, unplaced
                scored.sort(key=lambda t: t[0])

            if progress_callback is not None:
                progress_callback(gen, best_fitness, best_sheets, sheet_w, sheet_h)

            out_of_time = time_budget_seconds is not None and time.time() - start > time_budget_seconds
            if out_of_time or gen == generations - 1:
                break

            next_population = [(order_idx, rot_choice) for _, order_idx, rot_choice, _, _ in scored[:elite_count]]
            while len(next_population) < population_size:
                pa_order, pa_rot = min(rng.sample(scored, tournament_size), key=lambda t: t[0])[1:3]
                pb_order, pb_rot = min(rng.sample(scored, tournament_size), key=lambda t: t[0])[1:3]
                child_order = _order_crossover(pa_order, pb_order, rng)
                if rng.random() < mutation_rate:
                    _swap_mutate(child_order, rng)
                child_rot = None
                if optimize_rotations:
                    child_rot = _uniform_crossover(pa_rot, pb_rot, rng)
                    if rng.random() < mutation_rate:
                        _rotation_mutate(child_rot, expanded, rng)
                next_population.append((child_order, child_rot))
            population = next_population
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    return best_sheets, best_unplaced
