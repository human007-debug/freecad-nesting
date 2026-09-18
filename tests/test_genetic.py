"""
tests/test_genetic.py
-----------------------
genetic.py promises (see its module docstring's DETERMINISM section) that
for the same seed and a should_stop that never fires, parallel=True and
parallel=False produce byte-identical final results, and that should_stop
always leaves a fully-evaluated best layout even when it fires mid-search.
These tests hold that promise to the same 49-part scenario used elsewhere
in this suite.

Marked `slow` -- these run real (if small) GA searches, including spinning
up a ProcessPoolExecutor. Deselect with `pytest -m "not slow"` for a fast
inner-loop run.
"""

import pytest

from genetic import optimize_order
from tests.conftest import SHEET_H, SHEET_W

pytestmark = pytest.mark.slow


def _summarize(sheets, unplaced):
    total_parts = sum(len(sheet) for sheet in sheets)
    used_area = sum(pp.net_area() for sheet in sheets for pp in sheet)
    total_area = SHEET_W * SHEET_H * max(len(sheets), 1)
    utilization = 100.0 * used_area / total_area
    return len(sheets), total_parts, tuple(sorted(unplaced)), round(utilization, 4)


def test_serial_and_parallel_agree(demo_parts):
    sheets_serial, unplaced_serial = optimize_order(
        SHEET_W, SHEET_H, demo_parts, kerf=3.0,
        population_size=6, generations=4, seed=3, parallel=False,
    )
    sheets_parallel, unplaced_parallel = optimize_order(
        SHEET_W, SHEET_H, demo_parts, kerf=3.0,
        population_size=6, generations=4, seed=3, parallel=True, max_workers=4,
    )
    assert _summarize(sheets_serial, unplaced_serial) == _summarize(sheets_parallel, unplaced_parallel)


def test_should_stop_early_exit_in_parallel_mode(demo_parts):
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2  # let a couple of generations run, then stop

    sheets, unplaced = optimize_order(
        SHEET_W, SHEET_H, demo_parts, kerf=3.0,
        population_size=6, generations=8, seed=3, parallel=True, max_workers=4,
        should_stop=should_stop,
    )

    assert calls["n"] >= 3
    # should_stop firing mid-search must still hand back a fully-evaluated
    # best layout, never an empty/partial one.
    assert sum(len(sheet) for sheet in sheets) > 0


def test_placement_callback_fires_once_per_winner_in_parallel_mode(demo_parts):
    """Documented trade-off (genetic.py's OPTIONAL PARALLEL EVALUATION
    section): serial mode fires placement_callback for every population
    member's every placement attempt; parallel mode only replays each
    generation's winner once, serially, to drive a live view."""
    population_size, generations = 6, 4
    calls = {"n": 0}

    def on_placed(*args, **kwargs):
        calls["n"] += 1

    optimize_order(
        SHEET_W, SHEET_H, demo_parts, kerf=3.0,
        population_size=population_size, generations=generations, seed=3,
        parallel=True, max_workers=4, placement_callback=on_placed,
    )

    expanded_part_count = sum(part.quantity for part in demo_parts)
    assert calls["n"] == generations * expanded_part_count
