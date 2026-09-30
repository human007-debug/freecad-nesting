"""
quoting
-------
AlphaQuote: sheet-metal (and beyond) quoting on top of the AlphaNest
nesting engine. See docs/QUOTING_PLAN.md for the overall plan.

Everything in this package is plain Python -- no FreeCAD import anywhere.
FreeCAD only ever runs as a separate extraction step (freecad_extract.py
under FreeCADCmd) that writes JSON; this package reads that JSON (or
plain `nester.Part` objects, or hand-entered values) into `PartFeatures`
and prices it:

    features.py       per-part geometry: cut length, pierces, areas, weight,
                      bends, holes/threads, profile, pockets
    classify.py       sheet / tube / section / machined / purchased
    rates.py          rate tables (SQLite + XLSX), with "calibrate me"
                      starter rates
    operations.py     Operation base class + laser, bending, per-unit rules
    ops_weld.py       welding
    ops_tube.py       tube & section cutting (saw or tube laser)
    ops_machining.py  per-feature machining estimates
    ops_assembly.py   assembly labour, bought-in items, quote-level costs
    costing.py        the quote engine: lines x operations x quantity breaks

`bar_nest.py` (1D bar/tube nesting) lives beside `stock_solver.py` at the
repo root, since it's a nesting engine, not a quoting one.
"""
