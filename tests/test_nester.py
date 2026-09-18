"""
tests/test_nester.py
---------------------
End-to-end smoke test for the nesting engine itself, using the same
49-part scenario demo.py nests -- this is the project's own baseline
correctness check (a change to geometry.py/nfp.py/nester.py that alters
this result, without an intentional reason to, is a regression).
"""

import pytest

from nester import Nester
from tests.conftest import SHEET_H, SHEET_W


def test_single_nest_places_all_demo_parts(demo_parts):
    nester = Nester(SHEET_W, SHEET_H, kerf=3.0)
    for part in demo_parts:
        nester.add_part(part)

    sheets, unplaced = nester.run()

    assert unplaced == []
    assert len(sheets) == 1
    assert sum(len(sheet) for sheet in sheets) == 49

    used_area = sum(pp.net_area() for sheet in sheets for pp in sheet)
    utilization = 100.0 * used_area / (SHEET_W * SHEET_H)
    assert utilization == pytest.approx(23.9291, abs=1e-3)
