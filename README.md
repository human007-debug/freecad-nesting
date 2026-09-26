# Irregular Sheet Nesting — Proof of Concept

A Python implementation of true polygon (not bounding-box) 2D sheet
nesting, of the kind SigmaNest/DeepNest do, placing parts via real
no-fit-polygon (NFP) geometry. Three dependencies: `pip install pyclipper`
(exact Minkowski-sum/polygon-boolean math — see "How the algorithm
works"), `pip install ezdxf` (DXF import — see "DXF/DWG import") and
`pip install openpyxl` (the stock inventory's `.xlsx` file — see
"Multi-material assemblies & stock inventory"). Everything else (DXF
*export*, FreeCAD integration, the nesting engine itself) is still plain
Python, no other third-party libraries.

## Install

AlphaNest comes two ways — use either or both. Both need the **whole
repo**, not just one folder: the engine (`nester.py`, `geometry.py`, …)
lives at the repo root and is shared by the app and the workbench.

**Download:** `git clone https://github.com/human007-debug/freecad-nesting.git`,
or on GitHub click **Code → Download ZIP** and unzip it.

### Option A — standalone app (no FreeCAD needed for DXF)

Needs Python 3.10+.

```bash
cd freecad-nesting
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
```

Then start it with `native_app/run.sh` (Linux/macOS) or by double-clicking
`native_app\run.bat` (Windows). Importing `.FCStd`/STEP/IGES files also
needs FreeCAD installed. The app finds it automatically; if it can't, set
`ALPHANEST_FREECADCMD` to the full path of `FreeCADCmd`/`freecadcmd`.

### Option B — FreeCAD workbench

**Yes, it goes in FreeCAD's `Mod` folder — the whole repo folder, under
any name.** Don't copy only `freecad_workbench/`: it would be missing the
engine. The `package.xml` at the repo root tells FreeCAD where the
workbench is. Requires FreeCAD 1.0+ and the **SheetMetal** workbench
(install it from the Addon Manager).

1. Find your Mod folder. In FreeCAD's Python console
   (View → Panels → Python console) run
   `FreeCAD.getUserAppDataDir() + "Mod"`. Typical locations
   (FreeCAD 1.0 has no `v1-1` segment):

   | OS | Mod folder |
   |---|---|
   | Windows | `%APPDATA%\FreeCAD\v1-1\Mod` |
   | macOS | `~/Library/Application Support/FreeCAD/v1-1/Mod` |
   | Linux | `~/.local/share/FreeCAD/v1-1/Mod` |
   | Linux (flatpak) | `~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod` |

   Create the `Mod` folder if it doesn't exist yet.
2. Put the repo there: `git clone https://github.com/human007-debug/freecad-nesting.git`
   inside `Mod`, or move the unzipped folder in. If you'd rather keep your
   checkout somewhere else, a symlink works too:
   `ln -s /path/to/freecad-nesting <Mod folder>/freecad-nesting`.
3. Install `pyclipper` (and `networkx`, which SheetMetal's unfolder needs)
   into FreeCAD's own Python. Paste this into FreeCAD's Python console:

   ```python
   import os, platform, subprocess, FreeCAD, freecad.utils
   v = platform.python_version_tuple()
   target = os.path.join(FreeCAD.getUserAppDataDir(), "AdditionalPythonPackages", f"py{v[0]}{v[1]}")
   subprocess.run([freecad.utils.get_python_exe(), "-m", "pip", "install", "--target", target, "pyclipper", "networkx"], check=True)
   ```

   On the Linux flatpak (FreeCAD 1.1) you can skip this step: the repo's
   `vendor/` folder already has copies built for it.
4. Restart FreeCAD and pick **Nesting** from the workbench dropdown.

Alternatively, let the Addon Manager do steps 2–3: Edit → Preferences →
Addon Manager → Custom repositories → add
`https://github.com/human007-debug/freecad-nesting` (branch `master`),
then install **AlphaNest** from the Addon Manager. If it doesn't offer to
install `pyclipper` for you, run step 3 by hand.

**Workbench missing, or Run Nesting does nothing?** Check View → Panels →
Report view. A `pyclipper` warning there means step 3 hasn't been done.
If `InitGui.py not found` shows up in the log, the folder in `Mod` is
`freecad_workbench` itself instead of the whole repo.

## Status (updated after step 1 & 2)

**Step 1 — hole support: done.** `Part`/`PlacedPart` now carry an outer
contour plus a list of hole loops. Area/utilization nets out hole area, the
DXF writer emits holes as separate closed polylines (`<part>_HOLE` layer),
and the preview renderer shows true cut-throughs. Collision/spacing is
computed on the outer contour only (parts are not nested into each other's
holes) — a deliberate, conservative default. Verified: 49-part demo run,
1176 pairwise checks, 0 collision/kerf violations, 0 out-of-bounds parts.

**Step 2 — candidate generation upgrade: done, twice.** Iteration 1 moved
candidates from a coarse position grid to exact vertex-to-vertex touching
contacts — a heuristic that targets the same points a true NFP boundary
would have, without needing polygon-boolean machinery. **Iteration 2
replaced that heuristic with real NFP nesting**: for each rotation, every
already-placed part's exact no-fit-polygon against the new shape is
computed via Minkowski sum (`pyclipper`), unioned together, and subtracted
from the sheet's inner-fit rectangle — every vertex of what's left is an
exact touching position, including ones created only by two placed parts'
NFPs meeting, or inside a concave part's NFP holes (nestling into a
notch) — neither of which the old vertex-anchor heuristic could reach on
its own. `pyclipper` is now a hard dependency (see top of this file) — no
compromise on nesting quality to keep a zero-dependency pitch. Verified:
core NFP math checked against hand-derived Minkowski sums (incl. a
concave L-bracket case, confirming NFP holes compute correctly), a 3-part
"corner notch" case only a unioned NFP can find, exact kerf spacing via
offset-inflated obstacles, and the same 49-part/1176-pair independent
overlap+kerf+bounds check as Step 1 (0 violations). A/B'd against the old
heuristic on the original demo and on an artificially tight-packing
variant: identical final density on both (the heuristic's binary-search
compaction already converged to the true optimum for these shape/order
combinations), but consistently **~2.4x faster** (no more per-candidate
binary-search compaction) — see "How the algorithm works" for what's
still a heuristic (placement *order*/rotation *choice*, not placement
*position*) and "Upgrade path" for the genetic-algorithm layer that would
address that.

**Step 3 — FreeCAD part extraction: done.** `freecad_extract.py` (runs
under FreeCAD's own Python, not plain python3) walks a `.FCStd` document for
SheetMetal-workbench objects, flattens each one, and writes its outer
contour + hole loops as JSON; `part_import.py` (plain python3, no FreeCAD
needed) loads that JSON into `nester.Part` objects. See "FreeCAD
integration" below for how it identifies parts, flattens bends, and the one
FreeCAD 1.1 security-restore quirk it has to work around.

**Step 4 — Workbench UI: done.** `freecad_workbench/` is a real FreeCAD
workbench ("Nesting") with a "Run Nesting..." command that opens a Qt
dialog: it scans the active document with the same extraction logic as
`freecad_extract.py` (in-process, no JSON round-trip needed from inside
FreeCAD), lets you set sheet size/kerf/per-part quantities and rotations,
runs `Nester.run()`, previews each sheet, and exports DXF straight from the
dialog. See "Workbench UI" below for install/usage and one non-obvious
FreeCAD-1.1-flatpak gotcha it had to work around (workbenches placed in the
wrong Mod directory, and a directory-name lookup instead of `__file__`,
both explained there).

**Step 5 — multi-material assemblies & stock inventory: done.** `Part` now
carries optional `material`/`thickness` metadata; `inventory.py` (plain
python3) groups parts by `(material, thickness)` — never mixing them onto
the same sheet — matches each group to its best-fit stock sheet from an
inventory list (remnants before full sheets), and cascades to the
next-best stock as each one is exhausted. Wired into the workbench UI as
an optional "Load Inventory..." step. See "Multi-material assemblies
& stock inventory" below.

**Step 6 — genetic algorithm over part ordering: done.** `genetic.py`
closes the one placement heuristic Step 2 left open: `Nester.run()` always
places parts strictly largest-area-first, one at a time, and never
reconsiders an earlier part's placement even when a different order would
pack tighter. `genetic.optimize_order()` searches permutations of part
order with a genetic algorithm (order crossover, swap mutation, tournament
selection, elitism), using the exact NFP engine itself
(`Nester.place_order()`) as the fitness function for every candidate
ordering — no cheap approximation, so what the GA optimizes for is exactly
what gets placed. Rotation choice is still handled per-placement by the
engine's own exhaustive-over-allowed-rotations search (unchanged from
Step 2), not separately encoded in the genome. The initial population
always includes today's largest-first ordering, so the search can never
do worse than `Nester.run()` alone. **Verified with a real, meaningful
win**, not just "it runs": on the artificially tight-packing 39-part
scenario from Step 2's A/B test (900×900mm sheets), where largest-first
stranded one part alone on a nearly-empty second sheet, the GA (population
8, 6 generations, 55s) found an ordering that fits **all 39 parts onto a
single sheet at 72.6% utilization**, versus the baseline's 2 sheets at
36.3% overall utilization. **Honest cost**: fitness evaluation is a full
nesting run, so this is population×generations times slower than
`Nester.run()` alone — defaults are kept small to stay in the tens-of-
seconds range for a few dozen parts. The primary **Run Nesting** action
always runs this search; it shows elapsed time and can be stopped at any
point, keeping the best completed layout. It works with or without
a stock inventory loaded: with one loaded, `inventory.py`'s `run_job()`
runs the GA once per stock-size pass within each material/thickness
group's cascade instead of `Nester.run()`'s plain ordering. A GA-based
preview's random seed is captured and replayed exactly on "Commit to
Inventory," so committing never risks landing on a different ordering than
what was just previewed.

Wiring this into the workbench surfaced a real, pre-existing gap unrelated
to the GA itself: `nester.py`'s Step 2 `pyclipper` dependency had never
actually been vendored for FreeCAD's own bundled Python (only for the
plain-python3 side), so the *entire* workbench — not just the new button —
had been silently broken inside FreeCAD since Step 2 landed. Fixed by
vendoring `pyclipper` the same way `networkx` already was, and by moving
that vendor directory onto `sys.path` in `InitGui.py` itself rather than
inside `nesting_panel.py`'s own setup code — see "Running it" and the
workbench's third gotcha, below, for why the load order matters.

**Step 7 — DXF import: done.** `dxf_extract.py` (plain python3, no
FreeCAD needed at all) reads a `.dxf` file directly into the same JSON
shape `freecad_extract.py` produces, so `part_import.py` loads either one
identically. Unlike the FreeCAD path, a DXF flat pattern needs no bend-
unfolding — it's already a 2D drawing — so the only real job is figuring
out which entities form which part: every closed loop (an already-closed
polyline/circle, or open LINE/ARC/spline entities chained end-to-end
wherever their endpoints meet) is found first, then loops are nested by
point-in-polygon containment — a loop with no parent is a part's outer
contour, everything nested inside it becomes one of its holes. Curved
entities (arcs, circles, ellipses, splines, and bulged polyline segments)
are flattened via `ezdxf`'s own curve machinery rather than hand-rolled
bulge math — same reasoning as using `pyclipper` for Minkowski sums
instead of hand-rolling those. A part is named after its DXF layer when
every entity forming it (outer + holes) agrees on one non-default layer —
a common shop convention — falling back to `<file>-partN` otherwise.
Material/thickness/quantity are CLI overrides only (`--material
NAME=value`, etc.) since none of them exist in a DXF at all. **Verified**:
a closed polyline rectangle with two circular holes (exact area, exact
hole positions), a bulged-arc L-bracket (curve flattening doesn't crash
and produces a sane area), and — the real test of the chaining logic — the
same rectangle built from four separate, unconnected `LINE` entities
instead of one polyline, which came out with the exact expected area
(2000mm² for a 50×40 rectangle), confirming endpoint-chaining works; an
annotation `TEXT` entity was correctly ignored rather than treated as
geometry. All three fed cleanly through `part_import.py` into
`Nester.run()`. **DWG is not read directly** — there's no reasonable
pure-Python way to parse Autodesk's proprietary binary format; convert to
DXF first with the free **ODA File Converter** (a standalone tool, not a
Python library) and run this on the result. See "DXF/DWG import" below.

**Step 8 — standalone native app: done.** The FreeCAD workbench's dialog
was split into `nesting_widgets.py` (the actual settings/table/preview/
GA/inventory/export UI, independent of FreeCAD and of any one Qt binding)
and a thin FreeCAD-specific adapter (`freecad_workbench/nesting_panel.py`).
`native_app/` wraps that same shared UI in a standalone `QMainWindow` that
needs no FreeCAD process to run: DXF and `parts.json` import directly,
`.FCStd` import shells out to `FreeCADCmd` + the existing
`freecad_extract.py` CLI unchanged. Everything downstream of import (Run
Nesting, GA ordering search, inventory cascading/commit, DXF export) is the
identical code path as the workbench. **Verified**: real end-to-end runs of
both front ends after the split — the native app importing a DXF part and
an `.FCStd` part (via the subprocess bridge) into one job, matching against
a loaded inventory, GA-optimizing, exporting DXF, and clearing via "New
Job"; and the FreeCAD workbench dialog re-run start to finish (rescan, GA
optimize against inventory, commit, Close) to confirm the split didn't
regress it. One sandboxing gotcha found along the way: a flatpak-sandboxed
FreeCAD can't see arbitrary paths under the system `/tmp` (the same class
of restriction as it not seeing this project's own Claude Code scratchpad
directory during earlier testing) — `freecad_bridge.py`'s scratch output
now lives under the project directory instead. See "Native app" below.

**Step 9 — ribbon UI, microjoints, Stock tab: done.** The native app got a
ribbon toolbar (`native_app/ribbon.py`, in the visual style sketched in
`ui_mockup.html` — grouped gradient-icon buttons, no external icon assets)
driving the same `NestingPanel` methods the old button row called; the panel
itself now supports a `show_actions=False` mode so its internal
scan/action-row widgets still exist (everything that references them keeps
working) but stay hidden in favor of the ribbon — the FreeCAD workbench is
unaffected, since it keeps the default. A new **Stock tab**
(`native_app/stock_panel.py`) lets you view and edit the loaded inventory
directly — add/delete rows, edit material/thickness/size/quantity/remnant,
save back to the file (a `.xlsx` workbook since Step 23; JSON at the time)
— instead of
hand-editing it; it refreshes automatically via a new `inventory_changed`
signal on `NestingPanel`, fired after Load/Clear/Commit.

Also new: **microjoints** (`microjoints.py`) — small uncut tabs left around
a part's outer contour so a finished part doesn't shift, drop into the
machine bed, or scorch on a completed edge before the rest of the sheet
finishes cutting. Pure arc-length geometry (walk the contour's cumulative
perimeter, cut `tab_width`-mm gaps at 2-6 evenly spaced points depending on
size), with no new dependency; `dxf_writer.write_dxf()` gained an optional
`microjoints=` param that writes the tabbed contour as several open
polylines instead of one closed one (`None`, the default, is byte-for-byte
identical to before). Off by default in both front ends' new "Microjoints
(optional)" settings group; shown live as dots in the sheet preview before
ever exporting. Lead-in/lead-out and other real toolpath concerns are
explicitly **not** implemented here — the user's downstream CypCut software
already generates toolpaths (including lead-in/out and pierce sequencing)
from the exported DXF, so this project's job stops at nested layout + DXF
export.

**Verified**: standalone geometry checks (perimeter length is conserved
exactly minus the tab gaps, across a rectangle/hexagon/concave L-shape, plus
a tiny-part fallback); a real native-app run driving the actual ribbon
buttons end to end (Add Parts, Run Nesting, toggle Microjoints on/off via
the ribbon, Export DXF — confirmed open vs. closed polylines in the output
match the toggle) and the Stock tab (New Inventory, Add Row, edit, Save,
round-tripped through `inventory.load_inventory()`, auto-refresh on Clear
Inventory); and a FreeCAD workbench regression run confirming the shared
`nesting_widgets.py` changes don't affect it — action buttons still visible,
microjoints off by default, DXF export unchanged, new signals fire
correctly.

**Step 10 — job save/load, grain direction: done.** First two of a
"professional nesting software" feature list being worked through one at a
time (remnant capture, costing, part labeling, rotation-in-GA, joint stock
optimization, common-line cutting, and a nesting report come next).
- **Job save/load** (`nesting_widgets.py`'s `_job_state()`/`_save_job()`/
  `_load_job()`, wired into the native app's File menu as Open/Save/Save
  As): a job file captures the *input specification* — parts with their
  geometry embedded (so reopening doesn't depend on the original DXF/FCStd
  file still existing), every settings-panel field, and which inventory
  file was loaded (its path only; content is reloaded fresh from disk on
  open, since that file may have changed since — e.g. from a Commit). A
  missing inventory file on open logs a warning and continues without it
  rather than blocking the rest of the job from loading.
- **Grain direction constraint**: a "Grain-restricted" checkbox column in
  the parts table (shared, so the FreeCAD workbench gets it too). Checking
  it locks that part's Rotations column to `0,180` — some materials
  (brushed stainless, pre-painted coil) must keep their rolling/grain
  direction aligned to the sheet. Enforced twice for safety: the checkbox
  auto-fills and locks the Rotations cell, and `_table_parts()`
  independently forces `[0.0, 180.0]` at nesting time regardless of what
  that cell's text says, so a hand-edited job file or a desynced cell can't
  sneak an illegal rotation through.

**Verified**: real end-to-end runs of both — job save/open round-tripping
every field exactly (table contents, all settings, inventory re-linked,
Run Nesting working correctly on the reopened data), New Job correctly
clearing the tracked job path, opening a job with a missing inventory file
degrading gracefully; grain-restriction locking/unlocking correctly on
toggle, surviving a manual attempt to edit around it, and round-tripping
through job save/load; a FreeCAD workbench regression run confirming the
new table column and grain logic work there too with zero workbench-specific
code changes (it's all in the shared `nesting_widgets.py`).

**Step 11 — sheet margins, live run stats, live GA preview, part
thumbnails: done.**
- **Margins**: `Nester`/`genetic.optimize_order()` gained independent
  `margin_left`/`margin_right`/`margin_top`/`margin_bottom` (default 0,
  i.e. today's flush-to-every-edge behavior unchanged) — a no-cut border
  along a sheet edge (e.g. where a machine clamps material down). The
  inner-fit rectangle in `Nester._candidates()` simply starts at
  `(margin_left, margin_bottom)` instead of `(0, 0)` and shrinks
  accordingly, so every existing NFP/kerf/rotation code path is unaffected
  — margins are just a smaller usable rectangle, not a new placement
  concept. `inventory.run_job()`'s GA branch had a real gap fixed along the
  way: it wasn't forwarding `**nester_kwargs` to `genetic.optimize_order()`
  at all, so *any* extra Nester option (not just margins) was silently
  ignored specifically when both inventory *and* the GA were used together.
- **Timer + live efficiency stats**: a new stats bar under the sheet
  preview shows elapsed time, sheets used, parts placed, and overall
  efficiency (utilization, aggregated across every sheet in the job) after
  Run Nesting; updates live generation-by-generation
  during a GA search too.
- **Live GA preview**: `genetic.optimize_order()`'s `progress_callback` now
  also receives the current-best sheets and sheet dimensions (both already
  available at the exact call site, just not passed through before), so
  the sheet preview visibly redraws the improving layout after every
  generation instead of only showing the final result.
- **Part thumbnails**: the Parts table's Part column now shows a small
  rendered icon of each part's actual outer contour minus holes (same
  `QPainterPath`-subtraction approach as `SheetPreview`, just normalized to
  a small square), instead of only a name.
- **Margins "Apply to all" (added later)**: a checkbox next to the four
  margin fields that, when checked, syncs all four to whatever value was
  just typed into any one of them — added after the user pointed out it's
  easy to forget to update the other three when they're meant to match.
  Off by default so asymmetric margins (e.g. a clamp only along one edge)
  are unaffected. `_load_job()` restores this checkbox's state *before*
  restoring the four margin values themselves, specifically so a job saved
  with asymmetric margins can't be silently corrupted if "Apply to all" had
  been left checked from whatever job was open previously.
- **"Kerf" field renamed to "Part spacing" (added later)**: the UI-facing
  label was misleading -- this codebase never offsets exported cut
  geometry by beam width, the value has only ever been a minimum-distance
  check between placed parts (see `Nester._feasible()`), i.e. real kerf
  compensation was never implemented. Renamed the widget
  (`self.kerf` -> `self.part_spacing`) and its tooltip to describe what it
  actually controls (handling/heat-affected-zone clearance between parts),
  matching how LaserNest separates "Part interval" from any beam-width
  concept. The underlying engine parameter (`kerf=` in `Nester`/
  `genetic.optimize_order`/`inventory.run_job`/`commit_job`) is left
  unrenamed -- it's an internal, generic distance argument shared across
  files, only the UI label was inaccurate. Job files saved under the old
  label still load: `_load_job()` checks the new `part_spacing` key first
  and falls back to the old `kerf` key.

**Verified**: margin correctness checked directly against `PlacedPart`
bboxes (including asymmetric per-side margins mapping to the correct sides,
and an oversized-margin case degrading to "everything unplaced" rather than
crashing); the existing 49-part `demo.py` re-run to confirm margins
defaulting to 0 reproduce identical results to before this change; a real
native-app run capturing the live preview's text/content at each GA
generation (not just the final frame) and confirming the stats bar
populates correctly after both Run Nesting and Optimize Ordering; a
FreeCAD workbench regression run confirming all of this (thumbnails,
zero-default margins, stats bar, live-preview-then-final-view handoff)
behaves identically there. "Apply to all" separately verified: on/off
sync behavior, and a job save/reopen round-trip that deliberately leaves
the checkbox checked beforehand to prove asymmetric margins (5/15/20/0)
survive load intact — in both the native app and a FreeCAD workbench
regression run.

**Step 12 — remnant capture: done.** Closes the third item on the
"professional nesting software" list (job save/load and grain direction
were Step 10; margins/stats/live-preview/thumbnails were Step 11).
`remnant.py` finds the largest axis-aligned empty rectangle left on a cut
sheet after placing parts, using each placed part's *bounding box* (not
its exact, possibly-concave outline) as the obstacle — an honest
simplification, not a shortcut: this project's stock model is rectangles
only (`StockSheet` has width/height, no arbitrary outline), so an exact
polygon remnant would have nothing to be stored as, and a real
professional tool (SigmaNest's own "remnant sheet" feature) works the same
way for the same reason. Algorithm: coordinate-compress the sheet into a
grid from every obstacle bbox edge, then find the largest all-free
sub-rectangle via the standard "largest rectangle in a binary matrix"
technique (histogram method, one grid row at a time) — O(rows×cols),
trivial for realistic part counts. Wired into `inventory.commit_job()`
(on by default, `capture_remnants=True`): after a real commit, each cut
sheet's leftover is recorded back into the inventory file as a new
`is_remnant=True` stock entry (unique `REM-<material>-<8 hex chars>` id),
skipped if either side is under a configurable minimum (default 100mm —
not worth tracking a sliver), and logged as a `remnant_created` audit-log
event. A new "Remnant capture (optional)" settings group (checkbox +
minimum-side spinbox) controls it from both front ends, since it lives in
the shared `nesting_widgets.py`; only matters on a real Commit, never on a
Run Nesting preview.

**Verified**: the largest-empty-rectangle algorithm cross-checked against
an independent brute-force search across 200 randomized sheet/obstacle
trials (all matched exactly, including asymmetric and zero-obstacle
cases); a real native-app commit producing an actual new remnant entry
that round-trips correctly through `inventory.load_inventory()`, appears
in the audit log, and correctly produces nothing when the capture toggle
is off; the same commit-and-capture flow re-run through the FreeCAD
workbench dialog to confirm identical behavior there.

**Step 13 — costing/quoting: done.** Fourth item on the list. Deliberately
**not** weight/density-based costing — this project has no reliable way to
know a user's actual material density or supplier pricing, and a wrong
guess is worse than no number at all. Instead: `StockSheet` gained an
optional `price_per_sheet` (editable in the Stock tab's new "Price/sheet"
column, `None` = unpriced, exactly what you'd actually know from a
purchase order) — the job stats bar sums whichever used sheets' prices are
actually set, and says how many sheets were left out of the total when
some aren't priced, rather than silently under-reporting. Cut-time
estimate is separate and always available (no inventory/pricing needed):
`geometry.polygon_perimeter()` (new) summed across every placed part's
outer contour + hole perimeters, divided by a configurable cut speed
(mm/min) — explicitly labeled as a rough estimate (no pierce time, no
rapids between cuts, no acceleration), not a toolpath simulation.

**Verified**: Stock tab price entry round-trips through Save into
`inventory.load_inventory()` correctly; cut-time estimate present with no
inventory loaded and no cost line shown (no price data to report); cost
line appears correctly once nesting actually matches a priced stock entry,
correctly excluding/flagging any unpriced sheets used in the same job; a
FreeCAD workbench regression run confirming the cut-time estimate works
there identically (cost naturally doesn't apply without inventory in that
test, matching expectations).

**Later revised: weight/density-based pricing, plus scrap value.** Step
13's reasoning above (no reliable way to know density/pricing, a wrong
guess is worse than none) held as long as the software would have had to
guess those values itself. It doesn't need to: real sheet-metal purchasing
is priced per kg, not per sheet, and the shop owner already knows both
numbers for their own materials -- `price_per_sheet` became `price_per_kg`
+ `density_g_cm3` on `StockSheet` (both still `None` = unpriced, same
graceful degradation as before, just two fields instead of one; see
`StockSheet.weight_kg()`/`material_cost()`). This is a breaking change to
`inventory.json`'s schema, not an additive one -- `examples/inventory.json`
and this project's own `my_inventory.json` were migrated (empty
`price_per_kg`, densities filled in for their real named materials: 7.85
mild steel, 8.0 stainless 304, 2.68 aluminum 5052 -- physical constants,
safe to fill in even though $/kg rates aren't).

A remnant created by `commit_job()`'s auto-capture now inherits its parent
sheet's `price_per_kg`/`density_g_cm3`/`scrap_price_per_kg` (same
material, same rates) -- not so its own cost counts as cash spent (it
doesn't; it was already on hand), but so a LATER job can report what using
it saved vs. buying that weight new. A new `scrap_price_per_kg` field on
`StockSheet` itself (not a single blended job-wide rate -- scrap value
genuinely varies a lot by material, aluminum scrap being worth far more
per kg than mild steel's, for example) prices the OTHER side of the
ledger: whatever a cut sheet's leftover material doesn't become a new
remnant (too small to keep, by the same `Auto-record-new-remnants` toggle
and minimum-dimension threshold `commit_job()` already used) is valued as
sellable/discarded scrap instead, at THAT entry's own rate. The report's
new "Financials" section lays out new-sheet cost, remnant savings, and
scrap value as three separate lines plus a net figure, rather than
collapsing them into one number that would hide which lever actually
moved it.

Both `scrap_price_per_kg` and a **Currency** dropdown (INR/USD/EUR/GBP/JPY,
defaulting to INR, display-only -- every figure underneath is a plain
number with no conversion) live in the native app's Stock tab, not the
Nesting ribbon: `scrap_price_per_kg` as a column on the inventory table
right alongside Price/kg and Density (same reasoning -- it's a property of
the material, not the job), and Currency as a small picker next to
Load/Clear Inventory. `NestingPanel` (shared with the FreeCAD workbench)
holds `currency_code` as a plain attribute with a `set_currency()`
setter/`currency_changed` signal rather than owning the combobox itself,
since the workbench has no separate Stock tab to put one in -- it just
stays at the INR default there for now.

**Verified**: `StockSheet.weight_kg()`/`material_cost()` checked against a
hand-computed width x height x thickness x density x price formula, and
confirmed `None` whenever either density or price is missing rather than
silently defaulting to 0; `joint_stock_optimization` end-to-end confirmed
to rank two full-sheet candidates by actual computed cost, not just
per-kg rate (a smaller/thinner-but-pricier-per-kg sheet can still lose to
a cheaper one); a real `commit_job()` remnant-capture run confirmed the
new remnant entry inherits its parent's price/density. See
`tests/test_stock_pricing.py`.

**Step 14 — part labeling: done.** Fifth item on the list. A new "Part
labeling (optional)" toggle adds a plain `TEXT` DXF entity (the part's
name) at each part's bounding-box center, on its own `<part>_LABEL` layer,
separate from the cut geometry — so a laser/plasma controller can route
that layer to a low-power engrave pass instead of cutting it, without
touching `dxf_writer.py`'s existing `POLYLINE`-only cut output. Off by
default (`labels=None` reproduces byte-for-byte the same output as before
this option existed).

**Verified**: direct DXF output check confirming the `TEXT` entity lands
exactly at a known rectangle's centroid with the configured height; a real
native-app export confirming no `TEXT`/`_LABEL` at all with the toggle off
and both present with the toggle on; label settings round-tripping through
Save/Open Job; the same on/off export behavior re-verified through the
FreeCAD workbench dialog.

**Step 15 — rotation choice in the GA's genome: done.** Sixth item on the
list, and the one flagged since Step 6 as the next real density gain: the
exhaustive per-placement rotation search in `Nester._find_placement()`
can only ask "what's the best rotation for *this* part right now," never
"would a worse rotation here leave more room for later parts." New opt-in
`genetic.optimize_order(..., optimize_rotations=True)` searches a second
genome array (rotation-choice index per part, independent of the order
permutation) alongside order, with its own uniform crossover and
single-gene mutation, evaluated via a new `Nester.place_order(...,
forced_rotations=...)` parameter that fixes a specific rotation instead of
exhaustively searching `part.rotations`. **Honest trade-off, stated
directly in both the code and the new "Also search rotation choice"
checkbox's tooltip**: unlike the order-only search, this mode does *not*
carry the "never worse than plain Run Nesting" guarantee — forcing a
rotation trades away the exhaustive search's own local guarantee for the
chance at a jointly better one instead. `forced_rotations` defaults to
`None` everywhere, reproducing the exhaustive search exactly, so
`optimize_rotations=False` (the default) is unaffected.

**Verified**: `demo.py`'s 49-part baseline re-run to confirm `nester.py`'s
new parameter is a true no-op by default; a constructed rotation-sensitive
case confirming order-only search still never beats plain `Nester.run()`
(the existing guarantee, unbroken), rotation search runs without error and
only ever places parts at one of their own allowed rotations, and fitness
never gets worse generation-over-generation within a single search
(elitism holding); a real native-app GA run with the checkbox on/off and
round-tripped through Save/Open Job; a FreeCAD workbench regression run
confirming both GA modes work there identically.

**Step 16 — joint stock optimization: done.** Seventh item on the list.
`run_job()`'s cascade always picked its stock size via `best_stock_for()`'s
static heuristic (remnants first, then smallest fitting area) and just
trusted that guess — it never checked whether a *different* candidate size
would actually pack the group into fewer sheets. New opt-in
`joint_stock_optimization=True` makes each cascade pass nest the remaining
parts against **every** viable stock size for the group (via the new
`_candidate_stocks_for()`/`_nest_against_stock()`/`_stock_objective()`
helpers — the same nesting-or-GA call either way, just refactored out of
the main loop rather than duplicated) and keep whichever candidate
actually yields fewest sheets, then lowest total cost if priced (Step 13;
`price_per_sheet` there later became weight-derived `material_cost()` --
see "Later revised" above), then least wasted area. **Honest cost**: multiplies
nesting passes (or full GA searches) by however many candidate sizes
exist, per cascade step. **Honest limitation, unchanged from before**:
still not a true joint solve across *mixed* sizes in one pass (e.g. "2 of
A + 1 of B" beating "3 of B") — it upgrades which *single* size each pass
commits to, from a heuristic guess to an actually-measured best, not a
full cutting-stock solver (that would need real ILP tooling, a dependency
out of proportion to what this feature needed to be useful).

**Verified**: a constructed case (a small stock size that only fits one
part per sheet vs. a large one that fits the whole group on a single
sheet) confirming `best_stock_for()`'s heuristic really does pick the
worse one, and that `joint_stock_optimization=True` correctly recognizes
and picks the better one instead — 5 sheets down to 1 in the direct
`inventory.py` unit test, 20 sheets down to 1 through the real native-app
UI, and 10 sheets down to 1 through the real FreeCAD workbench dialog; the
default (`joint_stock_optimization=False`) path re-verified byte-for-byte
unchanged against `examples/parts.json`/`examples/inventory.json`.

**Step 17 — common-line cutting: done.** Eighth item, and the one flagged
from the start as the biggest remaining density-vs-cut-time feature.
`commonline.py` detects when two placed parts on the same sheet share a
full physical edge (both endpoints coincide, in either winding direction,
within a rounding tolerance) and merges it into one `COMMON_CUT`-layer cut
line instead of cutting it once per part — reusing the exact same
open-polyline-splitting technique `microjoints.py` already established
(rotate the contour to start right after the excluded edge, walk once, no
wraparound stitching needed). **Honest scope**: only exact full-edge
matches are merged — a partial overlap (one part's edge only covers part
of another's longer edge) isn't split, and safely falls back to being cut
twice, same as before this feature existed. Doesn't combine with
Microjoints in this version (whichever export enables both gets
Microjoints; the tooltip and export log both say so) — combining "cut
gaps for tabs" and "skip shared edges" on the same contour is a real
feature but a materially larger one than either alone.

**Verified**: the edge-detection and contour-splitting algorithm checked
directly against hand-built touching rectangles, confirming perimeter is
exactly conserved (each part's drawn length + the other's + the one merged
line == both parts' full perimeters) and edges are correctly excluded from
each part's own path; a real native-app export where the actual NFP
placement engine (not a hand-built test case) placed two copies of a part
edge-to-edge at zero kerf, and the resulting DXF genuinely contains a
`COMMON_CUT` entity for that real shared edge; the same real-placement
export re-verified through the FreeCAD workbench dialog.

**Step 18 — nesting report: done.** Ninth and final item on the
"professional nesting software" list. `NestingPanel._export_report()`
writes a self-contained HTML report — no new dependency, since HTML is
universally viewable and printable straight to PDF from any browser, and
embedding each sheet's layout as a base64 `<img>` keeps the whole thing one
file. Contents: job totals (sheets used, parts placed, overall efficiency,
estimated cut time, estimated material cost, unplaced parts) and a
per-sheet table with material/thickness, size, parts, efficiency, cost, and
an actual rendered image of that sheet's layout (a temporary, unshown
`SheetPreview` grabbed to a `QPixmap`, same rendering code the live preview
already uses). Reachable from the native app's File menu
("Export Nesting Report..."); lives in the shared panel so it works
identically from the FreeCAD workbench too, even without a dedicated menu
item there.

**Verified**: exporting with no job run yet correctly does nothing rather
than writing an empty/broken report; a real native-app export inspected
directly confirms well-formed HTML, an embedded PNG layout image, and every
stat field (including a real material cost once a priced stock entry was
matched); the same export re-verified through the FreeCAD workbench dialog.

This closes out all nine items from the "professional nesting software"
feature list (Steps 10-18) in the order tackled: job save/load, grain
direction, sheet margins + live run stats + live GA preview + part
thumbnails, remnant capture, costing/quoting, part labeling, rotation
choice in the GA, joint stock optimization, common-line cutting, and the
nesting report.

**Step 19 — LaserNest-style UI: done.** The user compared AlphaNest against
two real commercial nesting tools (Lantek Expert, a full CAM suite, vs.
Han's LaserNest, a narrower laser-nesting-focused tool) and asked to move
toward LaserNest's model specifically — a closer fit, since Lantek's extra
breadth (CNC posting, simulation, clamp/turnover automation) is territory
the user's downstream CypCut software already owns. Four additions, all in
the shared `nesting_widgets.py` except the ribbon flattening (native-app-
only by definition), so the FreeCAD workbench gets three of the four for
free:
- **Flat toolbar** (`native_app/ribbon.py`): dropped the grouped-box/
  caption ribbon style for a single flat row of icons with thin separators
  between clusters, closer to LaserNest's toolbar. Pure layout change — no
  action's behavior changed.
- **Layout Results panel**: every `Run Nesting`/`Optimize Ordering` click
  now appends a snapshot to a capped list of the last 8 attempts (thumbnail
  + method + sheet count + efficiency + timestamp) instead of only ever
  keeping the latest — a third `QSplitter` pane lets you browse and
  re-select any of them, mirroring LaserNest's "Plate" results panel. A
  real correctness bug was caught and fixed while building this: `Commit
  to Inventory` reads `_last_run_parts`/`_last_run_used_ga`, not
  `self.sheets` directly — without also snapshotting and restoring those
  two fields per candidate, re-selecting an older layout and clicking
  Commit would have silently committed whatever the *most recent* run was,
  not the one actually shown.
- **Manual per-part editing**: click a part directly in the sheet preview
  (new hit-testing in `SheetPreview.mousePressEvent`, inverting the exact
  screen transform `paintEvent` already uses) or pick it from a dropdown,
  then nudge its position/rotation via numeric fields. A new "Prohibit
  overlap" toggle (default on) validates the result against every other
  placed part (`nfp.intersection_area`) and the sheet bounds
  (`geometry.polygon_fits_in_sheet`) before applying — reject-with-log-
  message when checked, apply-anyway-with-a-warning when not. Numeric
  nudge only, no drag-and-drop, matching what was actually asked for.
- **On-canvas common-edge highlighting**: `SheetPreview.paintEvent` now
  calls the already-tested `commonline.find_shared_edges()` directly (no
  changes to `commonline.py` itself) and draws each merged edge highlighted
  with a caption, a visual preview of exactly what Step 17's common-line
  DXF export already does under the hood, like LaserNest's "Common-edge
  first-cut" on-canvas callout.
- **Per-part placed/requested counts**: each row's Qty spinbox is tinted
  green/amber/red (fully/partially/not placed, from `self.unplaced`) with
  a tooltip showing the exact count — LaserNest's "0 | 11" readout, using
  the Qty widget itself rather than the Part-name cell so nothing that
  reads that cell's text as a lookup key elsewhere is affected.

**Verified**: the flattened ribbon still fires every existing action;
Layout Results accumulates and caps at 8, restores exact sheets on
re-selection, and (after the fix above) correctly restores what Commit
would act on too; manual editing's translate and rotate transforms checked
directly against the resulting geometry, plus both the reject-on-overlap
and allow-with-warning paths exercised for real; common-edge highlighting
confirmed reaching the preview and matching a real NFP-placed shared edge;
placed/requested tinting checked in both the fully-placed (green) and
none-placed (red) cases; all of the above (except the native-app-only
ribbon) re-verified through the FreeCAD workbench dialog.

**Step 20 — mirroring, status readouts, auto-report: done.** Three items
from the user's second gap list, all in the shared `nesting_widgets.py`
plus the engine touch in `nester.py`, so the FreeCAD workbench gets them
for free:
- **Per-part mirroring (allow mirror)**: new "Mirror" checkbox column in
  the parts table (default off, right next to Grain-restricted, and
  independent of it — mirroring is a pure geometry operation and never
  disturbs grain direction). When checked, the engine (`nester.py`) also
  tries the part's mirror image at every allowed rotation:
  `Part.allow_mirror` → `_find_placement` generates the reflected footprint
  (`_mirror_shape`, a simple x-flip; every consumer — area,
  point-in-polygon, pyclipper's NONZERO fill — is orientation-independent,
  so no re-winding was needed) and keeps whichever handedness wins the
  bottom-left tie-break. Mirrored placements are drawn with a dashed
  purple outline in the preview and tagged "(mirrored)" in the
  manual-edit dropdown. Verified at the unit level that the mirror image
  is genuinely distinct from all four rotations AND actually gets selected
  when it packs better (15/40 asymmetric obstacle layouts in the harness
  chose it on merit); the common no-win case — loose sheets where both
  handednesses fit the same lowest point — is the intended tie-break, not
  a bug. Fixed a pre-existing engine crash this work surfaced: a part
  whose rotated box exactly equals a usable sheet dimension produced a
  zero-area inner-fit rectangle, which pyclipper rejects with
  `ClipperException: All paths are invalid` (before this step that case
  could hard-crash a run) — now guarded in `_candidates`.
- **Status-bar readouts (LaserNest-style)**: `SheetPreview` now emits live
  mouse coordinates (`coords` signal + `mouseMoveEvent`, with mouse
  tracking on so it works without a click), shown as `X: … Y: … mm` in the
  new status row under the stats line, plus a `Sheet N of M` counter
  updated by `_show_sheet()` — a persistent status readout alongside the
  nav row's existing per-sheet label.
- **Automatic nesting report**: a new "Nesting report" settings group
  (auto-report on by default, configurable output folder, default
  `reports/`, relative to the app's working directory — run.sh pins that
  to the project root). Every completed `Run Nesting` / `Optimize
  Ordering` writes `nesting_<YYYYMMDD_HHMMSS>.html` via the same shared
  `_build_report_html()` the manual `Export Report…` uses, so the two
  can't drift. The report gains a **Stock used** section: per stock size,
  sheets cut, on-hand at job start, and remaining — and, matching the
  user's explicit choice, it only *reports*; stock is still written to
  disk solely by the explicit `Commit to Inventory` action. The per-sheet
  table also notes how many of a sheet's parts were placed mirrored.
- **Job-file persistence**: both new settings plus each part's `Mirror`
  state round-trip through the job JSON (`_job_state` / `_load_job`).

**Verified**: mirror checkbox read-back and engine selection; job
save/load round-trip of the mirror column and report settings; the coords
signal (including its `None` on mouse-leave); the auto-report file being
written after both the plain run and the GA path, with the stock section
correct for a 2-entry inventory; and `demo.py` unchanged (1 sheet, 49
parts, 23.9% utilization) as a no-regression check.

**Step 21 — STEP/IGES assembly import: done.** Some client assemblies
arrive as SolidWorks/CATIA files. Neither format is parseable directly
(proprietary, undocumented, no free library reads them — the only ways in
are owning the authoring CAD tool or licensing a commercial CAD-exchange
SDK), so the supported path is: export the assembly as **STEP** (a
one-click "Save As" in either tool) and hand that to AlphaNest instead.
`freecad_extract.py` — not a new parallel script — now also opens
`.step`/`.stp`/`.iges`/`.igs` paths (`Part.insert()` into a fresh document,
works headless under FreeCADCmd, no GUI needed) alongside `.FCStd`, so both
front ends get it: the native app via `native_app/freecad_bridge.py`
(already just forwards paths to the extractor, no change to its subprocess
logic), and the FreeCAD workbench for free (`FreeCADDocSource.scan()` in
`freecad_workbench/nesting_panel.py` already calls into the same discovery
function in-process — do FreeCAD's own File → Import, then "Rescan
document").
- **Discovery**: alongside the existing SheetMetal-object walk
  (`find_sheetmetal_parts`), a new `find_plain_solid_objects` picks up any
  document object with solid geometry that isn't already part of a
  SheetMetal chain — exactly what a STEP/IGES import produces (plain
  `Part::Feature` objects, no feature history at all).
- **Flat parts**: extracted exactly like any other flat SheetMetal part —
  `detect_flat_plate`/`extract_flat` are pure geometry, so they don't care
  whether the shape came from a bend-flattened SheetMetal object or a bare
  STEP solid. Every part entry gets a new `"source"` field (`"fcstd"` /
  `"step"` / `"iges"`) for traceability; purely additive, `part_import.py`
  already ignores unknown keys.
- **Honest limitation**: a STEP/IGES file carries no SheetMetal feature
  history — it's a dumb BRep solid, not a parametric tree — so a bent part
  imported this way **cannot** be auto-unfolded; `SheetMetalNewUnfolder`
  needs a live SheetMetal feature object for its bend-allowance math, which
  a bare imported solid doesn't have, and building a from-scratch
  geometric bend-recognizer from raw BRep alone is a much larger
  undertaking with no existing scaffolding. Instead of guessing or
  silently dropping it, any non-flat plain object is written to a new
  top-level `"unresolved"` list in the output JSON (`name`, `source_file`,
  `reason`), surfaced in the native app as a `[warn] <part>: has bends, no
  SheetMetal feature history ... - supply a flat-pattern DXF` log line. The
  fix is the same mixed workflow already used for pure-DXF jobs: get a
  flat-pattern DXF for that part (SolidWorks/CATIA both export one directly
  from a sheet-metal part's flat-pattern feature) and add it via another
  "Add Parts..." pick — it merges into the same job through the existing,
  unchanged `dxf_extract.py` path, since `FilePartSource` already
  accumulates parts across repeated picks.
- **Also honest**: no automatic instance/quantity deduplication for
  repeated STEP components (same as the existing SheetMetal path) — each
  solid found is one JSON entry at quantity 1, using the same manual
  `qty:<Label>=<N>` / `material:<Label>=<name>` override convention as
  everything else in `freecad_extract.py`.
- **Fixture/tooling geometry**: a real client assembly STEP often bundles
  small incidental solids that were never meant to be cut — jig/fixture
  hardware, reference tabs — alongside the actual sheet parts (hit in
  practice: dozens of `TUBE_PARTITION_FIXTURE_2_*` solids, ~17 mm² each,
  flooding both the parts table and the preflight "Not ready" list). A new
  `min_area` override (mm², default 0/disabled) on `freecad_extract.py`
  excludes any plain flat part below that net area *before* it's ever
  added, writing it to a third `"skipped"` list (`name`, `source_file`,
  `area`, `reason`) instead — logged as `[skip]`, not `[warn]`, since
  exclusion is the intended outcome here, not a problem needing a fix. The
  native app reuses the "Minimum net part area" value already configured
  in Settings (`nesting_widgets.py`'s `min_part_area`, default 25.0) for
  this automatically — no second setting to keep in sync. This only
  applies to the new plain/STEP/IGES discovery path; existing SheetMetal-
  derived FCStd parts are unaffected.
- **Workbench parity fix**: `find_plain_solid_objects`/`extract_plain_part`
  are also now called from `freecad_workbench/nesting_panel.py`'s
  `FreeCADDocSource.scan()` — this was missed on the first pass (it only
  ever called the SheetMetal-object discovery, and had an early return
  when no SheetMetal parts were found that would have skipped plain
  objects entirely even after adding the call). Fixed so "do FreeCAD's own
  File → Import (STEP), then Rescan document" genuinely works inside the
  workbench too, not just through the native app's bridge.
- **Scroll bug fix**: `native_app/parts_panel.py`'s "Not ready" preflight
  message was a bare `QLabel` with no scroll container — harmless with a
  handful of issues, but a STEP assembly with many small/material-less
  parts can produce a "Not ready" list hundreds of lines long, which just
  grew the label past the window edge with nothing to scroll (hit in
  practice, screenshot showed ~50+ `TUBE_PARTITION_FIXTURE_2_*` rows doing
  exactly this). Now wrapped in a height-capped `QScrollArea` (same pattern
  the part-detail panel's `body_scroll` already used), so a long list
  scrolls in its own bounded box instead of taking the whole window with it.

**Verified**: `top_flange_item1.step` (a flat plate exported straight from
`build_top_flange.py`'s own STEP output) extracts correctly
(`source="step"`, `method="flat"`, correct 210×1494mm outline, thickness
16.0) with `"unresolved": []` and `"skipped": []`; a bent part's final
shape from `examples/bent_bracket_with_hole.FCStd`, re-exported as a bare
STEP file (stripping its SheetMetal history to simulate a real client
export), correctly lands in `"unresolved"` instead of crashing or
vanishing; a synthetic ~16.8 mm² box (matching the real fixture geometry's
size) run with `min_area=25` correctly lands in `"skipped"` instead of the
parts list; the same three STEP files run through
`freecad_bridge.import_fcstd()` return the matching
`(extracted, unresolved, skipped)` tuple; the full native-app
`FilePartSource.load_files()` path produces the right `[warn]`/`[skip]` log
lines and only adds the genuine flat part; and a standalone Qt check
confirmed a 200-line issues label stays bounded inside its 160px scroll
box instead of growing the window to match.

**Duplicate-instance merging: done.** Hit in practice: a real client
assembly places the same physical part multiple times (a bracket repeated
at every rib), and neither the SheetMetal-object walk nor the new plain-
STEP-object discovery has any concept of "these are the same part" -- each
placed instance got its own JSON entry at quantity 1, so a bracket used 12
times showed up as 12 separate rows instead of one row at quantity 12.
`merge_duplicate_instances()` in `freecad_extract.py` groups the extracted
parts (both SheetMetal-derived and plain STEP/IGES ones, applied once at
the end of `main()` so both paths benefit) by a placement-invariant
`_shape_signature` and collapses each group into one entry, quantity
summed, with a new `"instances"` list (source object names) for
traceability, logged as `[merge] <name>: N identical instances -> quantity
N`. Also wired into `freecad_workbench/nesting_panel.py`'s
`FreeCADDocSource.scan()` for parity inside the workbench.
- **Which geometric properties, and why NOT bounding box**: thickness, net
  area, perimeter, hole count, and sorted hole areas. A bounding box was
  tried first and dropped after it demonstrably produced false negatives:
  each instance's 2D points come from `_project_wire`'s own per-instance
  (u, v) frame, derived from that instance's *world-space* face normal, so
  two instances of the same part placed at different rotations project
  into unrelated frames -- confirmed empirically that the same part
  rotated by a non-right angle (e.g. 37°) gets a different axis-aligned
  bounding box than at 0°, which would wrongly block the merge. Area and
  perimeter are true rigid-motion invariants regardless of which frame the
  polygon was expressed in, so those are what's compared instead.
  Verified against both realistic (0°/90°/180° about various axes) and
  deliberately awkward (37°, 111° in-plane) rotations of the same part
  plus an unrelated control part of a different size — the control never
  merges, and all rotated instances of the true duplicate do, in both
  cases.
- **Honest limitation, stated up front**: this is a heuristic fingerprint,
  not true shape congruence (same spirit as `detect_flat_plate`'s own
  tolerance-based test) — two genuinely different parts that happen to
  share the exact same area, perimeter, hole count, and every hole's area
  would incorrectly merge. Rare for real sheet-metal parts, but worth
  knowing about; the `"instances"` list and `[merge]` log line make it
  easy to spot and undo (edit that row's quantity back down) if it ever
  fires wrongly.
- **Also fixed a real, unrelated passthrough gap surfaced by this work**:
  the native app's "Add Parts" table population (`nesting_widgets.py`'s
  `_rescan()`/`_load_parts_dict()`) always hard-defaulted every row to
  quantity 1 and material "unspecified", regardless of what the extractor
  JSON actually said — meaning even the pre-existing `qty:<Label>=<N>` CLI
  override was silently discarded on initial import (it only ever
  survived through an explicit job save/load). A part's merged quantity
  would have been invisible in the UI without this fix. Now both read
  `data.get("quantity", 1)` / `data.get("material")` from whatever the
  part source actually returned; `native_app/freecad_bridge.py`'s
  `import_fcstd()` and `freecad_workbench/nesting_command.py`'s "Send to
  AlphaNest" JSON writer were both extended to carry those two fields
  through as well, closing the same gap at every hop.

**Verified**: a synthetic STEP assembly with 4 instances of the same
50×30×2mm plate at 0°/90°(Z)/90°(X)/180°(Z) placements plus one unrelated
80×40×2mm control part correctly merges the 4 duplicates into one entry at
quantity 4 and leaves the control alone; a second assembly using
deliberately awkward 0°/37°/111° in-plane rotations of the same plate
still merges all 3 correctly; and the merged quantity was confirmed to
survive the full round-trip through `freecad_bridge.import_fcstd()` and
`FilePartSource.load_files()` into the exact dict shape the parts table
reads.

**Step 22 — parallel GA search, heuristic joint stock-size solver: done.**
Two independent additions.

- **Parallel GA fitness evaluation** (`genetic.py`): `optimize_order()`'s
  new `parallel=True` option evaluates each generation's population across
  a `ProcessPoolExecutor` instead of one candidate at a time -- safe
  because `Nester.place_order()` is a pure function of its arguments plus
  config fixed once at construction, never touching shared state. A UI
  callback can't cross a process boundary, so `placement_callback` (live
  per-part animation) now replays only each generation's WINNING candidate
  once, serially, instead of firing on every population member's every
  placement -- arguably a nicer live view than before, and it sidesteps
  pickling a Qt-bound closure entirely. `should_stop` is checked once per
  generation instead of once per individual in this mode. Explicitly
  forces multiprocessing's `fork` context rather than trusting the
  platform default -- found during verification that Python 3.14 changed
  Linux's own default away from `fork` to `forkserver`, which requires the
  calling script to be wrapped in `if __name__ == "__main__":` and would
  otherwise break any caller invoking this from inside a UI callback.
  Wired into both front ends as a new "Parallelize across CPU cores"
  checkbox next to "Also search rotation choice." **Verified**: the same
  seeded search run both ways on `demo.py`'s 49-part scenario produced
  byte-identical final layouts (`parallel` only changes evaluation order,
  never the RNG stream that actually drives the search), and ran
  measurably faster (69.9s serial vs. 44.6s parallel on this machine,
  population=8/generations=5).
- **Heuristic joint stock-size solver** (new `stock_solver.py`): closes
  the gap Step 16's `joint_stock_optimization` flag explicitly couldn't --
  weighing "2 sheets of size A + 1 of size B" against "3 sheets of B" as
  one combined decision, instead of committing to the single best size per
  cascade pass. A new `true_joint_stock_optimization` flag on
  `inventory.run_job()`/`commit_job()` hands each (material, thickness)
  group to `stock_solver.optimize_stock_plan()` once: it builds an
  explicit, ordered PLAN of specific stock sheets (a `_fill_one_sheet()`
  helper turns the existing single-size `nest_against_stock()` into a
  "fill exactly one sheet" primitive, with zero changes to `nester.py`
  itself), seeded from exactly `joint_stock_optimization`'s own algorithm
  (pulled out into a new shared `greedy_best_stock_step()` helper) so it
  can never do worse, then hill-climbs by proposing random neighbor plans
  -- swap two slots' order, or swap one slot's stock size for a different
  viable candidate -- and keeping only strictly-better ones, bounded by a
  ~20s time budget. **Deliberately not a MILP solver**: discussed at
  length with the user first -- the size-mix decision space per real job
  is small enough that a good heuristic reliably matches what an exact
  solver would find, OR-Tools' wheel is far heavier than this project's
  two existing hard dependencies and risky to vendor into FreeCAD's
  bundled, flatpak-sandboxed Python the way `pyclipper`/`networkx` already
  had to be, and commercial nesting software doesn't reach for MILP on
  this combined irregular-nesting-plus-stock-mix problem either -- it
  stays heuristic/multi-pass, same family as this project. Also
  deliberately hill-climbing rather than simulated annealing: the
  objective is a lexicographic `(unplaced, sheets, cost, waste)` tuple
  with no natural scalar "how much worse" to drive an annealing
  temperature against, so only strict improvements are ever accepted.
  Wired into both front ends as a new "Search size combinations (slower)"
  checkbox that also force-checks and disables the base "Joint stock
  optimization" checkbox next to it (the deeper search always uses that
  algorithm as its own starting point, so the base checkbox's value would
  otherwise be a confusing no-op once this one is on). **Verified**: a
  constructed scenario (5 identical parts; a cheap small stock size with
  only 2 sheets on hand that fits 1 part each; an unlimited expensive
  large size that fits 4 parts each) where the existing greedy algorithm's
  single-step-lookahead commits to the cheap size first, exhausts it, and
  cascades to 1 expensive sheet for the remainder (2×A + 1×B = $50, 3
  sheets) -- the new solver instead finds a reordered plan (1×A + 1×B =
  $40, 2 sheets, found via the "swap order" move) that places all 5 parts
  for $10 less on one fewer sheet; a second scenario where the greedy
  result is already optimal confirmed the solver never regresses it.

**Step 23 — the stock list as an Excel workbook: done.** The inventory was
a JSON file, and it shouldn't have been: it is a *table* — one row per
stock entry, the same ten columns the Stock tab already showed — and the
person who actually knows what's in the racks is a storeman, not a
developer. Handed a `.json` they can't sort it, filter it, total a column,
or send it to anyone; and one missed comma breaks the file for everyone.
So `inventory.py` now reads and writes `.xlsx` (openpyxl): a `Stock`
worksheet with a frozen, bold heading row, a filter row, and a Yes/No
dropdown on Remnant.

The sheet layout has exactly one definition — `_STOCK_FIELDS` in
`inventory.py`, which `STOCK_COLUMNS` and, through it, `stock_panel.py`'s
own table columns are both built from — so the sheet someone edits in Excel
and the table they edit in the app cannot drift apart.

The reader is deliberately forgiving, because the entire point of handing
someone a spreadsheet is that they edit it: headings are matched ignoring
case, spacing and the parenthesised units (`Thickness (mm)`, `thickness`
and `THICKNESS` are one column), columns can be reordered, a shop's own
extra columns (`Supplier`, `Bin`) are carried past rather than treated as
errors, the table is found beneath a title row, blank spacer rows are
skipped, Remnant takes `Yes`/`Y`/`TRUE`/blank, and a width typed `1,220`
reads as 1220. A cell that genuinely isn't a number names its row and
column in the error instead of failing anonymously. Blank still means
"unlimited"/"unpriced" — never zero, which is a real and very different
answer.

Writing preserves every OTHER worksheet in the workbook: `commit_job()`
rewrites the inventory file after every committed job, and it must not eat
the supplier-notes tab someone keeps beside the stock.

**Nothing old breaks.** Format is chosen by the path's extension, so a
`.json` inventory still loads, and a job committed against one still writes
`.json` back rather than silently becoming a workbook. `convert_inventory()`
moves one over; `my_inventory.json` and `examples/inventory.json` were both
converted (the originals kept). The Stock tab grew a **Save As...** button,
which is the same conversion from the UI — and also gives a table built up
with Add Row somewhere to live, where before that case hit a "no inventory
file loaded" dead end. openpyxl is imported lazily, inside the `.xlsx`
paths only, since this module is also imported inside FreeCAD's bundled
Python where an install can't be assumed.

The commit *audit log* stays `.log.jsonl`: it's an append-only machine
record of what was cut, not a table anyone maintains by hand — the two
files have genuinely different jobs.

Verified by 24 new tests (`tests/test_inventory_excel.py`) covering the
round trip of every field including the blanks, hand-made sheets with
reordered/extra columns and a title row, each spelling of Remnant, the
error message's row/column, other worksheets surviving a rewrite, format
following the extension, and a full `commit_job()` against an `.xlsx` —
plus a headless run of the real app: load `my_inventory.xlsx`, edit a cell
in the Stock tab, Save, reload.

**Step 24 — UI refresh: done.** The brief was "the UI looks outdated, make
it more polished — Autodesk Fusion, and Apple in general". Four rules came
out of that, and everything below follows from them:

1. **Quiet chrome, loud content.** The window is neutral; the only
   saturated color is the brand red, on the one primary action and on
   things that genuinely need attention. Previously EVERY button in the
   app turned brand red on hover, which made the whole window feel like an
   alarm panel and left the real primary action saying nothing.
2. **Hairlines, not boxes.** Structure comes from a separator and a change
   of surface. The old sheet drew a 1px box around every group, table,
   header cell and input — a grid of boxes reads as a 2005 Win32 form.
3. **Room to breathe.** A real type ramp (12px captions / 13px body /
   15–20px titles instead of one undifferentiated 11px), 8px-grid padding,
   32px table rows, and every tab opening with its name and one line about
   what it is for.
4. **The sheet is material, not a colored rectangle.** It is painted as a
   neutral plate with a 100 mm graph-paper grid and a soft drop shadow, so
   the parts are the only colored things on it — it used to be filled
   steel blue, which fought every part color on top of it and stayed
   bright blue in dark mode.

`native_app/theme.py` became the design system those rules live in: named
palette tokens for both themes, one font stack, one set of radii, and the
app-wide stylesheet generated from them (`string.Template`, so the CSS
braces stay readable). Widgets that paint themselves read the same tokens
rather than hardcoding hexes, and the sheet canvas is re-colored through a
new `nesting_widgets.set_sheet_palette()` — a hook, not an import, because
that module is shared with the FreeCAD workbench and can't depend on the
native app. The workbench, which has no stylesheet of its own, gets
`nesting_widgets.FALLBACK_QSS` (applied only when the host application has
set no stylesheet, so it can never fight a host theme).

Visible changes: a segmented tab strip (below) instead of raised 3D tabs;
a toolbar whose
buttons size themselves (the primary "Run Nesting" was being elided to
"Run ...ting" by a hand-computed fixed width that didn't know about its own
padding) with Settings parked at the right; tables with row hairlines,
aligned numeric columns and headings that no longer truncate to "tity
(blank=unlin" (see Step 23 for the Stock table's own columns); the Parts
tab's gallery as tiles with the part sitting ON them rather than saturated
blocks with the labels running over them, and its detail pane side by side
with the render instead of stacked under a wide, short strip; a borderless
in-row delete affordance (at 28px wide with the new padding, the old one
clipped its own glyph to an empty box); restyled checkboxes, combo and
spin controls (the tick, chevrons and arrows ship as SVGs in
`native_app/assets/`, since Qt can't draw them on a restyled indicator);
an empty-state line in the Layout Results panel; and a monospaced, sunken
console log.

Two Qt-specific bugs this turned up and fixed:

* **Selected rows tinted their own icons blue.** Qt renders an item's
  icon in `QIcon.Selected` mode on a selected row and, given only a Normal
  pixmap, generates that variant by washing it in the Qt PALETTE's
  highlight color — a stock blue, since this theme is pure QSS and never
  touches the palette. A selected sheet thumbnail came back blue while the
  same sheet in the preview beside it was grey. `untinted_icon()`
  registers one pixmap for every mode, leaving Qt nothing to invent.
* **Labels drew their own grey boxes.** `QWidget { background: ... }`
  reaches `QLabel`/`QCheckBox` too (both paint a styled background), so
  every caption inside a white settings card sat on its own rectangle of
  window-grey. Fixed by making those widgets explicitly transparent.

**The tab strip** (`native_app/segmented.py`) is modeled on the Apple
reference the user supplied (`applereference.jpg` -- Apple Music's tab
bar): one rounded track holding the tabs, with the selected one marked by
a filled pill that floats inside it and takes the accent color, rather
than by an underline or a raised box.

It is a real widget, not a styled `QTabBar`, because that shape is made
entirely of padding and margins and QTabBar honors neither from a
stylesheet -- styling it produced a pill taller than the track it sat in,
flush against the menu bar above. The `QTabWidget` keeps doing its own job
(pages, current index, session persistence) with its bar hidden, and the
strip drives it; sync runs both ways, so View > Nesting Tab, Ctrl+1/2/3,
the Parts tab's "Ready to nest ->" button and a restored session all move
the pill. Its three glyphs (an L-bracket, two stacked sheets, parts on a
sheet) are drawn in `QPainter` and re-stroked on selection and on a theme
change -- the icon pack's own icons are rounded tiles built for toolbar
buttons, and a tile inside a pill reads as a button within a button.

Verified by rendering the real window headlessly (`QT_QPA_PLATFORM=offscreen`,
`QWidget.grab()`) for all three tabs, the settings dialog, and a completed
two-sheet run, in both themes, and iterating on the images; plus the full
test suite after each round -- now 117 tests, the five new ones
(`tests/test_segmented_tabs.py`) pinning the strip and the pages to the
same answer whichever side moved.

**Step 25 — Apple accent, new mark, editable ribbon: done.** Five follow-ups
to Step 24, all from the same round of feedback on the running app.

**The accent is Apple's own.** `#FC4269`, sampled straight out of the tab
bar in `applereference.jpg` rather than eyeballed, lifted to `#FF5C7E` in
dark mode the way Apple lifts its system colors there. It replaces the icon
pack's `#E8352E` *everywhere*, including inside the pack's own icons: a new
`theme.icon_svg()` reads each pack SVG, drops its rounded tile background,
and maps its ink/accent/muted colors onto theme tokens as it loads. The
pack files on disk are untouched -- they are the user's brand assets, and a
pack updated tomorrow still works -- but the toolbar now renders them as
line glyphs in the current theme instead of as a row of chips in the old
palette. `mono=` collapses an icon to a single color, which is what the
primary action uses so its glyph is white *on* the filled button rather
than a pale sticker stuck to it.

**A real alpha mark** (`native_app/assets/alpha-mark.svg`, and
`alpha-badge.svg` on an accent squircle for the window/taskbar icon). The
old one was an SVG `<text>` element setting the Greek letter in Georgia --
so it rendered differently on every machine, and not at all where that font
is missing. The new one is drawn as paths: an open bowl (a single elliptical
arc) and a leg that crosses it and lands in a tail, stroked with round caps
so it survives down to a 16px icon. It was drawn by rendering it at 200 /
64 / 32px and adjusting until it read as an alpha at all three.

**The ribbon's brand corner is gone.** A wordmark wedged into the left end
of a working toolbar is decoration competing with the controls; the app's
mark belongs on the window icon, which is where it now lives.

**The Settings button has a mark that belongs to this app.** It never had a
pack icon, so it fell through to a hand-drawn blue gradient square -- a
chip in a palette the app doesn't own, which is exactly why it looked like
it had wandered in from another program. Icon fallbacks are now
`native_app/glyphs.py`: plain strokes drawn in the theme's ink (the tab
strip's three marks moved there too). Settings' own glyph is two sliders,
not a cogwheel -- the button opens shop and machine *parameters*, and a
gear's teeth turn to mush below about 24px anyway.

**View > Nesting Ribbon** ticks each toolbar item on or off, so a shop's
toolbar carries what that shop actually uses; "Show All" puts everything
back, and the choices are saved per item key with the rest of the session
(`ribbon/<key>` in persistence.py -- per key, so adding a button later
doesn't reset the choices already made about the others). Two details this
dragged in:

* A rule that ends up dividing nothing hides itself, so tidying the
  toolbar can't leave a stray line behind. The check is `isVisibleTo()`,
  not `isVisible()`: the saved choices are restored during `__init__`,
  before the window is ever shown, when `isVisible()` is False for
  everything in it -- which would have hidden all three rules permanently.
* Because *every* item can be hidden, Run and Stop needed a home that
  can't be: a new **Job** menu (Run Nesting `Ctrl+R`, Stop Nesting, Export
  DXF, Commit to Inventory), which also gives the app its first keyboard
  route to starting a nest. The preflight gate covers those actions too.

Nine new tests (`tests/test_ribbon_items.py`) cover the ticks, the
self-hiding rule, "Show All", the restart round-trip, the guarantee that
hiding Run leaves a way to run, and `icon_svg()`'s recoloring in both
normal and `mono` form. Suite: 126 tests.

## Files

| File                  | Purpose                                                             |
|-----------------------|----------------------------------------------------------------------|
| `geometry.py`         | Exact polygon math: rotate/translate, min-distance test, bbox pruning — pure Python, transparently backed by `geometry_fast` when built (see "Performance") |
| `geometry_fast.pyx` / `setup_geometry_fast.py` | Optional Cython-compiled accelerator for `geometry.py`'s two hottest functions — see "Performance" |
| `nfp.py`               | True no-fit-polygon geometry via `pyclipper`: Minkowski sums, unions, offsets (kerf), intersection-area, shape-keyed caching of both (see "Performance") |
| `nester.py`           | The nesting engine: `Part` (with holes), `PlacedPart`, NFP-exact placement |
| `genetic.py`          | Genetic algorithm over part ORDERING (not position) — `Nester.place_order()` as its fitness function |
| `dxf_writer.py`       | Hand-rolled DXF (R12/POLYLINE) exporter — outer + hole loops, one file per sheet |
| `demo.py`             | Example: 49 parts incl. plates with bolt-hole cutouts, nested onto sheet(s) |
| `freecad_extract.py`  | Runs under FreeCAD: reads `.FCStd`/`.step`/`.iges`, finds SheetMetal-workbench parts, unfolds bends, extracts plain flat STEP/IGES solids too, writes contour+hole JSON (+ an `unresolved` list for bent STEP/IGES parts needing a flat-pattern DXF) |
| `dxf_extract.py`      | Plain python3, no FreeCAD: reads a `.dxf` flat pattern into the same contour+hole JSON shape |
| `part_import.py`      | Plain python3: loads that JSON (from either extractor) into `nester.Part` objects |
| `inventory.py`        | Plain python3: material/thickness grouping + stock-inventory matching and cascading |
| `stock_solver.py`     | Heuristic hill-climbing search over an ordered plan of (possibly mixed) stock sizes, for `run_job(true_joint_stock_optimization=True)` |
| `microjoints.py`      | Pure geometry: splits a contour into open polylines with small uncut tab gaps |
| `remnant.py`          | Pure geometry: largest empty rectangle left on a cut sheet, for automatic remnant capture |
| `commonline.py`       | Pure geometry: detects/splits exact shared edges between placed parts, for common-line cutting |
| `nesting_widgets.py`  | The nesting UI itself (binding-agnostic `QWidget`), shared by the FreeCAD workbench and the native app — see "Native app" below |
| `examples/`           | Demo `.FCStd` files, a sample `.dxf`, and a sample `inventory.xlsx` (plus the legacy `inventory.json` it was converted from), for trying either extractor/the workbench |
| `freecad_workbench/`  | The "Nesting" FreeCAD workbench — a thin FreeCAD-doc adapter around `nesting_widgets.py` — see "Workbench UI" below |
| `native_app/`         | Standalone desktop app (no FreeCAD needed) around the same `nesting_widgets.py` — see "Native app" below |
| `native_app/theme.py` | The app's design system: palette tokens, type ramp, and the app-wide Qt stylesheet built from them (plus `native_app/assets/`, the control glyphs QSS needs as images) |
| `native_app/segmented.py` | The main tab strip: an Apple-style segmented control (rounded track, filled pill on the active tab) driving the bar-less `QTabWidget` |
| `native_app/glyphs.py` | The small vector marks the app draws for itself (tab strip, Settings), stroked in the current theme's colors |

## Run it

```bash
pip install pyclipper   # one-time; the nesting engine hard-requires it
python3 demo.py
```
Produces `sheet_N.dxf` (importable into FreeCAD, LibreCAD, any CAM/laser
software) and, if `matplotlib` happens to be installed too (not required),
`sheet_N_preview.png` for each sheet used.

## Tests

```bash
pip install pytest
python3 -m pytest              # full suite
python3 -m pytest -m "not slow"  # skip the real GA/multiprocessing searches (~4s vs ~22s)
```
`tests/` grew out of this project's own verification history rather than
being written speculatively up front: `test_geometry_fast.py` and
`test_nfp_cache.py` are the randomized correctness checks that caught (and
then confirmed the fix for) the two "Performance" section pitfalls below
before they shipped, `test_nester.py` pins the demo.py scenario's known
result as a regression guard, and `test_genetic.py` holds `genetic.py`'s
own documented determinism promise (serial and parallel search must agree
bit-for-bit for the same seed) plus its `should_stop`/`placement_callback`
trade-offs under `parallel=True`. `test_geometry_fast.py`'s tests skip
cleanly (not fail) if the optional `geometry_fast` extension hasn't been
built. There was no test suite in this project before this; `demo.py` was
(and remains) the original smoke test.

## How the algorithm works

1. **Sort parts largest-first** — classic nesting heuristic; big pieces are
   hardest to seat, place them while the sheet is still empty.
   `Nester.run()` does this automatically; `Nester.place_order()` places
   parts in whatever order you hand it instead, which is what
   `genetic.optimize_order()` uses to try different orderings (see
   "Honest limitation" below).
2. **Exact NFP candidate generation** — for each rotation of a part: compute
   the no-fit-polygon of every already-placed part against this shape
   (`nfp.compute_nfp`, a Minkowski sum via `pyclipper`), union all of them
   into one obstacle region, and subtract that from the sheet's inner-fit
   rectangle (the sheet, inset by this shape's own bounding box). Every
   vertex of what's left — outer boundary or hole — is an exact position
   where the shape touches an obstacle (or another obstacle's NFP) without
   overlapping it. Candidates are scanned bottom-left first.
3. **Exact feasibility check** — kerf spacing is baked into the obstacle
   itself (each placed part's polygon is offset outward by the kerf amount,
   via `pyclipper`, before computing NFP against it) when kerf > 0; with no
   kerf, a candidate is rejected only if it has real positive-area overlap
   with a placed part (`nfp.intersection_area`) — exact boundary contact
   (a shared corner or edge, which an NFP-exact candidate will often have
   by construction) is correctly treated as touching, not overlapping.
4. **No compaction step needed** — unlike a heuristic-candidate approach,
   NFP vertices are already exact touching positions, so there's nothing
   left to slide/refine afterward.
5. **Multi-sheet overflow** — if a part won't fit any existing sheet, a new
   sheet is opened automatically.

Verified: a 49-part demo run (concave L/T/star/notched shapes, hexagons,
plates with bolt-hole cutouts) placed 100% of parts with 0 violations
across all 1176 pairwise checks, using an independent verification pass
that doesn't reuse the engine's own feasibility code (bbox containment,
`nfp.intersection_area` for overlap, `geometry.polygons_min_distance` for
kerf) — see git history / conversation for the verification script.

## Production safeguards

The UI has a blocking preflight gate before nesting, export, or inventory
commit. It checks material/grade, thickness, duplicate names, geometry,
rotations, matching stock, unplaced parts, mixed material groups, and saved
manufacturing rules (minimum feature, hole opening, and net area). Jobs also
have an **Assembly quantity** multiplier: each table quantity is per assembly,
so entering 100 automatically nests 100 copies of every child requirement.
DXF release output is sorted into deterministic material/thickness folders and
includes a `release_manifest.csv` describing every sheet and its parts. Each
DXF also has a SHA-256 checksum in that manifest so downstream users can
verify that the released file was not replaced or altered.

## Part-in-part nesting (opt-in)

The workbench now exposes an opt-in **Allow part-in-hole nesting** rule. When
enabled, the NFP placer adds conservative vertex and center candidates inside
existing cutout holes, then requires the candidate outer contour to remain
fully contained in the hole's inset clearance region. The default remains off
because part-in-hole cutting can be unsafe for handling, pierce access, or
cut-order constraints. The clearance value is saved with the job, and the
existing outer-contour collision guarantees are unchanged when the option is
off.

The native ribbon uses larger 30px action icons in 70×60px buttons for better
recognition at a glance. Preflight and Stop Nesting have dedicated themed
vector icons; actions without a supplied asset use a larger pictogram fallback.

## Honest limitation vs. SigmaNest / DeepNest

Placement *position* is NFP-exact (see above) — the same geometric
technique SVGnest/DeepNest/SigmaNest use — and placement *order* now has a
genetic-algorithm search available (`genetic.py`), reachable from the
workbench UI's **Run Nesting** button whether or not a stock inventory is
loaded, instead of being fixed to largest-first. What's still a gap:
- **Rotation choice is not in the default GA genome.** `Nester.run()` is
  still available to command-line callers as the quick largest-first path,
  but the UI always uses the GA because its fitness function is a full
  nesting run and can be stopped while retaining the best result. A
  drop-in default that fires on every keystroke-adjacent interaction.
  Rotation choice is still handled by the engine's own exhaustive
  per-placement search, not searched jointly with order in the GA's
  genome.
- **No toolpath generation (lead-in/lead-out, pierce sequencing, common-line
  cutting) — deliberately, not a gap to close.** This project's job stops at
  a nested layout + DXF export; the downstream CypCut software already
  turns that DXF into a toolpath, including lead-in/out and pierce order.
  Microjoints (Step 9) are the one cut-path concern implemented here, since
  they change the part *geometry* itself (small uncut bridges), not the
  toolpath around it.

**Upgrade path**:
- Rotation choice in the GA's genome (Step 15) and a heuristic joint
  stock-size solver across mixed sizes (Step 22) are both done now; bind
  Prusa Research's **libnest2d** (C++, MIT-licensed, the actual engine
  behind PrusaSlicer's object arranger) via pybind11 remains open, for a
  production-grade NFP + genetic-algorithm engine in one package instead
  of this project's own NFP (via pyclipper) + hand-rolled GA.
- A provably-optimal MILP solver (e.g. OR-Tools) in place of
  `stock_solver.py`'s hill-climbing heuristic remains open too, at the
  cost of a much heavier dependency for a gain the discussion in Step 22
  judged usually marginal in practice — see that step for the reasoning.

## Performance

Profiling demo.py's 49-part scenario (`cProfile`) found nesting time
overwhelmingly dominated NOT by the actual Minkowski-sum/NFP math in
`nfp.py` (already pyclipper's compiled C++, ~15% of total time), but by
`geometry.py`'s exact scalar kerf-distance/overlap check
(`polygons_min_distance`/`polygons_overlap`, called from `nester.py`'s
`_feasible()`) — 84% of total nesting time, in a plain Python edge-pair
loop run millions of times over on small (4-16 vertex) polygons. Fixing
this took three attempts, two of them dead ends kept as documented lessons
rather than silently discarded:

1. **Numpy vectorization** (every edge-of-A x edge-of-B pair as one array
   op) — measured SLOWER end-to-end (8.99s vs 5.92s baseline). These
   polygons are too small for bulk vectorization to pay off; per-call numpy
   array-allocation overhead swamps the arithmetic it saves. Reverted.
2. **Reusing the already-computed kerf-offset polygon** (a pyclipper
   miter-join offset `_candidates()` builds anyway) as an overlap-area
   check, instead of geometry.py's exact distance calc — rejected outright
   after randomized testing found it doesn't just add float noise, it
   actually misclassifies real cases: false rejections from miter overshoot
   at sharp corners, and once, worse, a false ACCEPTANCE of a placement
   only 1.64mm apart under a 3mm kerf. This engine is exact by design (see
   "Honest limitation" above); an approximate stand-in for the safety check
   defeats the point of the whole engine.
3. **Algorithmic NFP caching** (`nfp.compute_nfp_cached`/
   `offset_polygon_cached`): translating a polygon translates its Minkowski
   sum / miter offset by the exact same amount — an exact identity, not an
   approximation — so the same part+rotation nested at two different sheet
   positions can share one cached pyclipper call instead of repeating it.
   `genetic.py`'s GA (and `stock_solver.py`'s hill-climb on top of it)
   re-nests the same catalog dozens to hundreds of times over; measured
   504,306 raw `nfp.compute_nfp` calls collapsing to 576 distinct shape
   pairs (~875x redundancy) on one GA search. The cache lives at the `nfp`
   module level (not per-`Nester`), so it's shared automatically across
   `inventory.py`'s cascade and `stock_solver.py`'s repeated fresh
   `Nester` instances too, with no extra wiring. Unbounded, uncleared
   automatically — see nfp.py's `clear_cache()` for a long-running-process
   caller that wants to bound it.
4. **`geometry_fast.pyx`** (Cython): a literal, unmodified port of
   `geometry.py`'s exact scalar algorithm — same orientation test, same
   on-segment check, same point-to-segment-distance formula, same `EPS` —
   compiled to a native extension. What #1 got wrong is that the fix isn't
   vectorization, it's removing CPython's per-operation interpreter
   overhead from a tight scalar loop; compiling the *same* algorithm does
   that without changing what's computed. Verified bit-for-bit equivalent
   (0 mismatches, ~1e-14 float noise from summation order) against the
   pure-Python version across 30,000 randomized polygon pairs, and measured
   30-45x faster on those pairs directly.

Combined (#3 + #4), a 12-population/8-generation GA search over demo.py's
49 parts went from 124.96s to 25.05s (~5x) — identical result (same sheet
count, same 0 unplaced, utilization matching to 4 decimal places) verified
before/after every one of these changes, not just assumed from the theory.

**`geometry_fast` is an optional accelerator, not a hard dependency** —
`geometry.py` tries to import it and falls back to its own pure-Python
implementation (functionally identical, just slower) if it isn't built,
so the project still runs anywhere with zero build step. To build it:
```bash
pip install cython setuptools   # one-time, build-only -- not needed at runtime
python3 setup_geometry_fast.py build_ext --inplace
```
needs a C compiler (`gcc`/`clang`/MSVC) on the machine doing the build;
drops a `geometry_fast*.so` next to `geometry.py`, picked up automatically
on the next import. The FreeCAD workbench's own vendored-dependency story
(see "FreeCAD integration" below) doesn't cover this yet — a compiled `.so`
needs building against FreeCAD's own bundled Python's specific
version/ABI/platform, unlike the pure-Python files `InitGui.py` currently
vendors, so the workbench falls back to pure-Python geometry.py until/
unless that's set up too.

## FreeCAD integration

This module is intentionally CAD-agnostic (`Part` just wants a list of
(x, y) polygon points), so extraction lives in its own script that runs
under FreeCAD's own Python, entirely separate from the plain-python3
nesting engine.

### Running it

Two dependencies aren't bundled with FreeCAD's own Python, and FreeCAD's
Python is its own separate install from whatever plain `python3` resolves
to on your system (a `pip install` for one does nothing for the other) —
vendor both once (adjust for a non-flatpak FreeCAD install):
```bash
flatpak run --command=pip3 org.freecad.FreeCAD install --target=vendor networkx pyclipper
```
`networkx` is needed by the SheetMetal workbench's own bend-unfolding code;
`pyclipper` is needed by `nester.py` itself (see "How the algorithm
works") — the Nesting workbench (below) imports `nester.py` too, so it
needs this vendoring done even if you never touch `freecad_extract.py`
directly. Get the vendoring wrong (missing, or built for the wrong Python
version) and the failure is silent-ish: FreeCAD's own security sandbox and
`ModuleNotFoundError`s land in the Report View, not a crash, so "the
workbench doesn't do anything when I click Run" is the usual symptom —
check the Report View for `ModuleNotFoundError: No module named
'pyclipper'` (or `networkx`) first.

Then, on a `.FCStd` document:
```bash
flatpak run --command=FreeCADCmd org.freecad.FreeCAD freecad_extract.py --pass \
    model.FCStd output=parts.json kfactor=0.4 tolerance=0.25 qty:Some-Part=6
```
FreeCADCmd's own argument parser inspects the whole command line itself and
errors on any `-`-prefixed token it doesn't recognize — so `freecad_extract.py`
takes no `--flags` of its own. Every `.FCStd` path is a bare positional arg,
every tuning knob is a bare `key=value` token, and `qty:<Label>=<N>` overrides
a part's nesting quantity (default 1 — the CAD file has no notion of "how
many of these do I need on the sheet").

Then, in plain python3 (no FreeCAD needed from here on):
```python
from part_import import load_parts
from nester import Nester

nester = Nester(1220.0, 2440.0, kerf=3.0)
for part in load_parts("parts.json"):
    nester.add_part(part)
sheets, unplaced = nester.run()
```

### How extraction finds and flattens parts

1. **Discovery**: walks the document for SheetMetal-workbench objects
   (`SMBaseBend`, `SMBendWall`, `SMHem`, …), keeps only the "leaf" of each
   chain (one that isn't itself the `baseObject` of a later SheetMetal
   feature), then forward-follows any further FreeCAD dependency — e.g. a
   `Part::Cut` punching mounting holes on top of a finished bend — to the
   actual final shape meant for the nest.
2. **Flat fast path**: if that shape is already just a plate (two large
   parallel skin faces spanning the whole part, nothing sticking out of
   that plane — true for an unbent part, or one that already went through
   the workbench's own Unfold), the outer wire and hole wires are lifted
   straight off the flat face. No unfold needed, and this deliberately
   avoids ever handing a flat plate's corner fillets or countersinks to the
   bend-unroller, which would otherwise misread them as bends.
3. **Bent parts**: otherwise it runs the SheetMetal workbench's own
   bend-flattening algorithm (`SheetMetalNewUnfolder.getUnfold`), trying
   each planar face as the flattening's reference face — largest area
   first — until one succeeds, rather than requiring the user to have
   already picked one in the GUI.
4. **Tessellation**: every wire (arcs and circles included) is discretized
   into a straight-edge polygon at a configurable chord tolerance —
   `nester.py`'s geometry only understands straight edges.

Verified end-to-end against two hand-built SheetMetal documents in
`examples/`: a real `SMBaseBend` → `SMBendWall` → `Part::Cut` chain (one
90° bend, one bolt hole punched afterward) unfolds to the expected flat
length (base + bend allowance + wall) with the hole recovered in the right
place, and a flat `SMBaseBend` plate with two holes takes the fast path and
skips the unfolder entirely. Both fed straight into `Nester.run()` and
placed with zero collisions.

### One FreeCAD 1.1 quirk worth knowing about

FreeCAD 1.1 added a document-restore security gate: opening a `.FCStd`
whose `Part::FeaturePython` objects come from a workbench FreeCAD doesn't
consider "installed" (e.g. one `git clone`d straight into `Mod/`, as this
project's SheetMetal checkout is) blocks that object's `Proxy` from being
restored — it silently comes back as `None`, even though the module
imports fine everywhere else. `Shape` and link properties (like
`baseObject`) are unaffected, so geometry extraction still works, but
identifying *which* objects are SheetMetal ones can't rely on the live
`obj.Proxy` after open. `freecad_extract.py` sidesteps this by reading each
object's saved module name straight out of `Document.xml` inside the
`.FCStd` zip (a plain XML attribute on the saved `Proxy` property) instead
of asking FreeCAD to restore and inspect the live object.

## DXF/DWG import

`dxf_extract.py` is `freecad_extract.py`'s sibling for shops (or parts)
that don't come from FreeCAD at all — a flat pattern already exported as a
`.dxf` from any CAD/CAM tool. No FreeCAD, no bend-unfolding: a DXF is
already flat by definition, so the whole job is figuring out which
entities form which part.

```bash
pip install ezdxf
python3 dxf_extract.py plate.dxf -o parts.json \
    --qty Mount-Plate=6 --material Mount-Plate="Mild Steel" --thickness Mount-Plate=2.0
```
Unlike `freecad_extract.py` (constrained by FreeCADCmd's own argument
parser — see "FreeCAD integration" above), this runs under plain python3,
so it's a normal `argparse` CLI: real `--flags`, `--help` works, multiple
`.dxf` files can be given at once and their parts all land in one
`parts.json`. `--qty`/`--material`/`--thickness` are repeatable
`NAME=value` overrides, keyed by whatever name a part came out with (see
naming, below) — a DXF has no notion of any of the three at all, so
without an override every part defaults to quantity 1 and no
material/thickness set.

### How it finds parts

1. **Closed loops first.** Every already-closed entity (a closed
   `LWPOLYLINE`/`POLYLINE`, a full `CIRCLE`) is one loop on its own.
   Curved entities — `ARC`, `CIRCLE`, `ELLIPSE`, `SPLINE`, and bulged
   polyline segments (DXF's way of encoding an arc between two polyline
   vertices) — are flattened to straight edges via `ezdxf`'s own
   `ezdxf.path` machinery, not hand-rolled bulge math: the same "a
   battle-tested library beats getting fiddly curve geometry silently
   wrong" reasoning as using `pyclipper` for Minkowski sums (see "How the
   algorithm works").
2. **Chain the rest.** Open entities (bare `LINE`s, open polylines,
   unclosed splines) are chased end-to-end wherever their endpoints meet
   within `--chain-tolerance` (default 0.05mm — deliberately much tighter
   than `--tolerance`, the curve-flattening sagitta, since conflating the
   two would either make false "closed" detections from coarse flattening
   or a chaining tolerance so tight legitimate chains never connect) until
   each chain closes; a chain that never closes is dropped with a warning
   rather than guessed at.
3. **Nest loops into parts by point-in-polygon containment.** A loop with
   no parent is a new part's outer contour; everything nested inside it,
   at any depth, becomes one of that part's holes (this project's `Part`
   model is a flat outer + holes list, not nested islands-within-holes —
   rare enough for sheet metal that collapsing depth this way is a
   reasonable simplification).
4. **Naming**: if every entity forming a part (its outer loop and all its
   holes) agrees on one DXF layer other than the default `"0"`, the part
   is named after that layer — matching how shops commonly organize a
   multi-part nesting-input DXF (one layer per part). Otherwise it falls
   back to `<file>-partN`.
5. **Ignored**: `TEXT`/`MTEXT`/`DIMENSION`/`HATCH`/leaders/points (annotation,
   not material) and anything on a frozen/off layer. `INSERT` (block
   reference) entities are exploded one level via `ezdxf`'s own
   `virtual_entities()` — a block referencing another block is not
   recursively exploded, which is rare for flat nesting-export DXFs (these
   are usually already just flat geometry, not a parametric drawing full
   of nested symbol blocks).

**Verified**: a closed-polyline rectangle with two circular holes (exact
area and hole positions), a bulged-arc L-bracket (curve flattening handles
it without error), and — the real test of the chaining logic — the same
rectangle built from four separate, disconnected `LINE` entities instead
of one polyline, which still came out with the exact expected area
(2000mm² for a 50×40 rectangle); a `TEXT` annotation entity was correctly
ignored rather than treated as geometry. All three fed cleanly through
`part_import.py` into `Nester.run()`.

### DWG

Not read directly — Autodesk's DWG is a proprietary binary format with no
open specification and no pure-Python parser worth trusting for this.
Convert to DXF first with the **[ODA File
Converter](https://www.opendesign.com/guestfiles/oda_file_converter)** (a
free standalone batch-conversion tool from the Open Design Alliance, not a
Python library), then run `dxf_extract.py` on the result. "DWG support"
here really means "one conversion step, then the DXF path above" — there
isn't a cleaner option.

## Workbench UI

`freecad_workbench/` is a small real FreeCAD workbench, "Nesting", with one
command: "Run Nesting...". `nesting_panel.py` here is now a thin adapter —
the actual settings/table/preview/log/buttons live in `nesting_widgets.py`
at the project root (shared with the native app, see "Native app" below);
this file's only real job is `FreeCADDocSource`, which scans the active
document (reusing `freecad_extract.py`'s discovery/flatten/extract
functions in-process — no JSON round-trip needed since we're already inside
FreeCAD) and wraps the shared panel in a `QDialog`. Activating it opens a
dialog that lets you set sheet width/height/kerf/K-factor/curve tolerance and
each part's material/quantity/allowed rotations, optionally load a stock
inventory (see "Multi-material assemblies & stock inventory" below), runs
the nesting, previews each sheet (parts filled, holes cut out as true
holes, navigable across sheets — and across stock sizes, if inventory is
loaded), and exports DXF per sheet from a button — no separate script to
run. **Run Nesting** runs `genetic.optimize_order()` (see Step 6 / "Honest
limitation"); population size and generations are exposed, while elapsed
time is live and a Stop button keeps the best completed layout. With a
stock inventory loaded, it runs the GA once per
stock-size pass within each material/thickness group's cascade instead of
plain largest-first. "Commit to Inventory" (below) replays the exact same
GA seed as whatever was last previewed, so a commit after a GA-based
preview never lands on a different ordering than what was shown.

### Installing it

See [Install → Option B](#option-b--freecad-workbench) at the top of this
file. In short: put the whole repo (any folder name, or a symlink to it) in
FreeCAD's Mod folder. The repo-root `package.xml` points FreeCAD's loader at
`freecad_workbench/InitGui.py`, which finds its own location from the path
FreeCAD compiled it with. The older setup, a symlink named `FreeCADNesting`
pointing straight at `freecad_workbench/`, still works too.

Restart FreeCAD, pick "Nesting" from the workbench dropdown, open or create
a document with some SheetMetal-workbench parts in it, and run
"Nesting → Run Nesting...".

### A FreeCAD-1.1-flatpak gotcha this had to work around

`App.getUserAppDataDir()` already includes the version segment (e.g. it
returns `.../FreeCAD/v1-1/`, not `.../FreeCAD/`), so FreeCAD's *actual*
auto-scanned Mod directory is `.../FreeCAD/v1-1/Mod` — a workbench dropped
into the version-less `.../FreeCAD/Mod` one level up (an easy mistake, and
where this project's SheetMetal checkout still lives, found instead via
`freecad_extract.py`'s own explicit path search rather than FreeCAD's
native discovery) is silently never scanned at all: no error, it just never
shows up in the workbench dropdown. Confirmed by walking FreeCAD's own
embedded startup script (`FreeCAD.__ModDirs__`, built from
`getUserAppDataDir()+"Mod"`) rather than guessing.

Separately: FreeCAD runs a workbench's `InitGui.py` via
`exec(compile(...))`, not `import` — so `__file__` is undefined inside it,
and any exception it raises (a `NameError` from using `__file__` anyway, or
anything else, including a malformed hand-written XPM `Icon` string that
made `Gui.addWorkbench()` throw) is caught by FreeCAD's loader and *fully
swallowed*: the workbench just doesn't appear, with nothing printed to the
terminal or the GUI. `InitGui.py` here works around the first issue by
reading its own path off its code object
(`inspect.currentframe().f_code.co_filename`, the filename FreeCAD passed to
`compile()`) instead of using `__file__`; the second was found by manually
`exec()`-ing the file the same way FreeCAD's loader does, from a script, to
surface the real traceback.

A third, found only by actually clicking "Run Nesting..." in the real
GUI after `nester.py` grew a hard `pyclipper` dependency (Step 2): the
vendored dependency has to land on `sys.path` in `InitGui.py` itself, at
workbench-*load* time, not inside `nesting_panel.py`'s own setup code —
`Initialize()` imports `nesting_command`, whose `Activated()` imports
`nesting_panel`, whose own top-level `import nester` (→ `nfp` →
`pyclipper`) already runs, and already fails, before that module's own
`sys.path` fixup ever gets a turn. `InitGui.py` makes `pyclipper`
importable itself, before anything downstream gets imported, for exactly
this reason. It uses FreeCAD's own copy if one is installed, and otherwise
*appends* `<project root>/vendor`. Appending means the vendored copy never
shadows a working install: its compiled extension only loads on
Linux/Python 3.13. Symptom when this is wrong: clicking "Run Nesting..."
does nothing at all — no dialog, no visible error — because the `import
nester` chain fails inside `Activated()` before `NestingDialog` is ever
constructed; check the Report View for `ModuleNotFoundError`.

## Native app

`native_app/` is a standalone desktop app around the exact same nesting
engine and UI (`nesting_widgets.py`) as the FreeCAD workbench — no FreeCAD
process required to run it. A ribbon toolbar (`native_app/ribbon.py`, in
the visual style sketched in `ui_mockup.html`) drives the same actions the
workbench's button row does, grouped as **Nesting** (Add Parts, Run
Nesting, Stop Nesting, New Job), **Cut Path** (a checkable Microjoints
toggle — see Step 9), **Inventory** (Load/Clear/Commit), and **Export**
(Export DXF). A **Stock** tab next to the Nesting tab
(`native_app/stock_panel.py`) shows/edits the currently loaded inventory
directly — no more hand-editing the JSON. Where the workbench scans a live
FreeCAD document, the native app's "Add Parts" imports from files instead,
routed by extension:
- **`.dxf`** — `dxf_extract.extract_parts()`, called directly (no
  subprocess, no FreeCAD).
- **`.json`** — a `parts.json` written by `freecad_extract.py` or
  `dxf_extract.py`'s own CLI, read directly.
- **`.FCStd` / `.step` / `.stp` / `.iges` / `.igs`** — still needs an actual
  FreeCAD install, but not a running FreeCAD *session*:
  `native_app/freecad_bridge.py` shells out to `FreeCADCmd` and runs
  `freecad_extract.py` exactly as its CLI already supports (see "FreeCAD
  integration" above and Step 21 for the STEP/IGES path specifically). A
  STEP/IGES part with bends comes back as `unresolved` rather than a part
  (see Step 21) — surfaced as a `[warn]` log line naming it; add a
  flat-pattern DXF for it via another "Add Parts..." pick to complete the
  job.

Everything after import — Run Nesting (GA), stock
inventory matching/cascading, Commit to Inventory, DXF export — is the
identical code path as the workbench, since both are just thin adapters
around `nesting_widgets.NestingPanel`.

A **File / Edit / View / Settings menu bar** sits above the ribbon:
- **File**: New Job, Add Parts..., Load Inventory..., Exit.
- **Edit**: Remove Selected Part (removes one row from the Nesting tab's
  parts table — new capability, previously only "New Job" could clear
  anything), Remove Selected Stock Row (mirrors the Stock tab's own Delete
  Row button).
- **Job**: Run Nesting (Ctrl+R), Stop Nesting, Export DXF..., Commit to
  Inventory... — the actions that can't live only on the ribbon, since
  every ribbon item can be hidden (below).
- **View**: jump to the Nesting/Stock/Parts tab, Previous/Next Sheet, and
  **Nesting Ribbon**, a tick per toolbar item deciding what that ribbon
  shows (saved with the session).
- **Settings**: a checkable **Dark Mode** entry — this is the "toggle
  switch" for theme, deliberately placed as a menu item rather than a
  separate on-screen widget.

**Icon pack & theme** (`native_app/theme.py`): the ribbon's icons come from
a user-supplied `alphanest-icon-pack/` when a pack icon exists for that
button, recolored into the current theme as they load (`icon_svg()` — see
Step 25); a button with no pack icon falls back to a glyph the app draws
itself (`native_app/glyphs.py`). Settings → Dark Mode doesn't just swap
icons: it applies an app-wide Qt stylesheet built from that theme's
palette, so the ribbon, menu bar, tables, canvas and every other widget
retint together — a dark icon sitting on an unthemed light ribbon strip
would look broken.

`theme.py` is the app's whole design system, not just a color swap: a
palette of named tokens (surfaces, borders, three levels of text, the
brand accent, canvas/sheet/grid colors), one font stack, one type ramp and
one set of radii, rendered into the app-wide stylesheet. Widgets that
paint themselves — the sheet preview, the Parts tab's part canvas — read
the same tokens through `theme.tokens()`/`theme.color()` instead of
hardcoding hexes, and `apply_theme()` pushes them into
`nesting_widgets.set_sheet_palette()` so the sheet canvas follows the
theme too. See Step 24 for the design rules those tokens encode.
**Gotcha hit and fixed**: `QLabel` and `QTableWidget` both inherit from
`QFrame` in Qt's class hierarchy, so an initial `QFrame { color: ... }`
rule meant only for the ribbon's vertical separator lines was silently
overriding every label's and table cell's text color app-wide (they all
rendered in the separator's border-gray instead of the real text color).
Fixed by scoping that rule to the separator's object name
(`QFrame#RibbonSeparator`) instead of the bare type selector.

A **desktop launcher** is also set up outside this repo: a "Nesting" icon
on the Desktop and in the application menu (`~/.local/share/applications/freecad-nesting.desktop`,
plus a copy in `~/Desktop/`) runs `native_app/run.sh`, which resolves the
project path itself and launches through `.venv` — no terminal needed for
day-to-day use.

Run it with:
```bash
python3 -m venv .venv                                     # a venv sidesteps Debian/Ubuntu's
.venv/bin/pip install PySide6 pyclipper ezdxf openpyxl     # "externally-managed-environment" pip block
.venv/bin/python native_app/main.py
```
PySide6 is native-app-only (the FreeCAD workbench uses FreeCAD's own bundled
Qt); `pyclipper`/`ezdxf` are already needed elsewhere. If your system
Python's `venv` module errors with `ensurepip is not available`, either
`sudo apt install python3-venv` (matching your Python's version) or point
`python3 -m venv` at a different Python that already has it (e.g. a
miniconda/Anaconda install's `python3`, which bundles its own `ensurepip`).
On Linux, a real (non-offscreen) window additionally needs `libxcb-cursor0`
(`sudo apt install libxcb-cursor0`) — Qt's error message names the missing
library directly if this is hit.

**Limitations**: `.FCStd` import still needs a working FreeCAD install
reachable as `FreeCADCmd` (default: this project's own documented flatpak
invocation, overridable via `import_fcstd(..., freecad_cmd=[...])`), with
`pyclipper`/`networkx` vendored the same way the workbench itself needs
(see "Running it" above) — the native app just doesn't need FreeCAD running
interactively for that one import path. Multiple files can be added across
repeated "Add Parts" clicks to combine into one job (there's no single
"document" to rescan the way the workbench has); "New Job" clears
everything and starts over.

## Multi-material assemblies & stock inventory

An assembly's SheetMetal parts are rarely all the same material/thickness,
and a real shop doesn't have "one infinite sheet size" — it has a stock
list (standard sheets it can order) plus remnants (offcuts from past jobs),
and a part can only be nested onto stock of its own exact material +
thickness. `inventory.py` (plain python3, no FreeCAD dependency) handles
this:

1. **Grouping**: `group_parts()` buckets parts by `(material, thickness)` —
   each bucket is its own independent nesting job, never mixed with
   another, since you genuinely cannot cut a 3mm mild-steel bracket and a
   5mm stainless bracket from the same sheet.
2. **Stock matching**: `best_stock_for()` picks the best-matching stock
   sheet for a group from an inventory list — remnants before full sheets
   (burn down scrap before cutting new material) when `prefer_remnants` is
   set (the default), then the smallest size that's still big enough for
   the group's largest part.
3. **Cascading**: `run_job()` nests a group onto its best-matching stock,
   and whatever doesn't fit — either geometrically, or because that
   stock's on-hand quantity ran out — cascades to the next-best matching
   stock still in the inventory, repeating until everything's placed or no
   matching stock is left. A group with literally no matching stock at all
   is reported as such, distinctly from parts that just didn't fit.
4. **Committing**: `run_job()` only ever mutates the `StockSheet` objects
   you hand it in memory — safe to call over and over while experimenting.
   `commit_job()` is the one function that's for real: it loads the
   inventory fresh from a file, runs the job against those actual objects,
   writes the updated quantities back to that same file, and appends one
   line per stock consumed (plus one per group left with unplaced parts) to
   a JSON-lines audit log (`<inventory path>.log.jsonl` by default) —
   timestamp, material/thickness, which stock, how many sheets, what's
   left on hand.

```python
from part_import import load_parts
from inventory import load_inventory, run_job, commit_job

parts = load_parts("parts.json")  # material/thickness come through if
                                   # freecad_extract.py was run with
                                   # material:<Label>=<name> overrides

# Preview -- in-memory only, run this as many times as you like:
stock = load_inventory("inventory.xlsx")
for result in run_job(parts, stock, kerf=3.0):
    print(result.material, result.thickness, "->", len(result.sheets), "sheet(s)",
          "unplaced:", result.unplaced, "notes:", result.notes)

# Actually cutting? This writes inventory.xlsx's quantities back to disk
# and appends to inventory.xlsx.log.jsonl:
commit_job(parts, "inventory.xlsx", kerf=3.0)
```

The inventory file is an **Excel workbook** — a `Stock` sheet, one row per
stock entry, the same ten columns the Stock tab shows (see
`examples/inventory.xlsx`, and Step 23 for why it isn't JSON any more):

| ID | Material | Thickness (mm) | Width (mm) | Height (mm) | Quantity (blank=unlimited) | Remnant | Price/kg (blank=unpriced) | Density g/cm³ (blank=unpriced) | Scrap price/kg (blank=unpriced) |
|----|----------|---------------|-----------|------------|---------------------------|---------|--------------------------|-------------------------------|--------------------------------|
| R-001 | Mild Steel | 2 | 300 | 200 | 1 | Yes | | 7.85 | |
| | Mild Steel | 2 | 1220 | 2440 | 3 | No | 1.85 | 7.85 | 0.35 |

A blank `Quantity` means unlimited (e.g. a standard sheet you can always
reorder); a remnant's quantity is however many of that exact offcut are
actually sitting in the shop. `Price/kg`/`Density`/`Scrap price/kg` are all
optional (blank = unpriced, same graceful degradation as a missing
quantity) and all per ENTRY, not a single
job-wide rate -- see the "Later revised" note under Step 13 for why cost
is weight-derived rather than a flat per-sheet price, and why scrap value
is priced per material too.

The workbench UI has this built in: give each row in the parts table a
Material (defaults to "unspecified" — there's no way to derive material
from geometry, only `qty:`/`material:` overrides or a table edit), load an
inventory workbook with the "Load Inventory..." button, and "Run Nesting"
groups/matches/cascades automatically — the sheet preview and DXF export
both follow whichever stock size each sheet actually came from. Loading no
inventory falls back to the simple single-sheet-size behavior (the Sheet
width/height fields, applied to every part regardless of material).

"Run Nesting" is always just a preview (an in-memory copy of the loaded
inventory — click it as many times as you want while tweaking rotations or
quantities). A separate **"Commit to Inventory..."** button appears once a
preview with inventory loaded has run; it asks for confirmation, then
actually deducts those sheets from the inventory file on disk via
`commit_job()` and records it in that file's audit log — the one action
in the whole dialog that isn't undone by just closing without saving.

**Honest limitation, by default**: cascading moves from one stock size to
the next *between* nesting passes, not a joint optimization across sizes
within one pass (it can't weigh "2 sheets of A + 1 of B vs. 3 sheets of B"
the way a joint solver could) — see `inventory.py`'s docstring. The
**"Search size combinations (slower)"** checkbox (Step 22) closes this via
`stock_solver.py`'s heuristic plan search; it's opt-in rather than the
default because of its honest compounding cost with the GA.

**"Prefer remnants first"** (checked by default) is what actually
guarantees remnants get used up before new material regardless of which of
the three stock-selection modes is active. `best_stock_for()`'s own
smallest-fit heuristic always preferred remnants outright, but
"Joint stock optimization" and "Search size combinations" both rank
candidate stock by fewest sheets/lowest cost/least waste instead — without
this flag, either could silently swap a perfectly usable remnant for a
full sheet that merely scored a little better on that measure. Threaded
through `best_stock_for()`, `greedy_best_stock_step()`
(`joint_stock_optimization`'s algorithm), and `stock_solver.py`'s hill-climb
cost function alike, so unchecking it means all three rank purely on
sheets/cost/waste, ignoring remnant status entirely.

### Not done yet

- **Result placement**: writing nested sheet layouts back into the FreeCAD
  document as `Draft` wires, rather than only `dxf_writer.write_dxf()`.
- **Toolpath generation is intentionally out of scope**, not a gap: the
  user's downstream CypCut software already turns the exported DXF into a
  toolpath (lead-in/out, pierce sequencing, G-code). This project's job
  stops at nested layout + DXF export.
- **`dxf_extract.py` isn't reachable from the *FreeCAD workbench* UI** — that
  one's still a CLI-only, FreeCAD-independent tool as far as the workbench
  is concerned. This is now solved for the **native app** instead, though:
  its "Add Parts" imports DXF, `parts.json`, and `.FCStd` (via the
  FreeCADCmd subprocess bridge) into the same job, repeatable across
  multiple files/assemblies — see "Native app" above. Growing the
  FreeCAD-only workbench itself to also read DXF remains low-value (a
  DXF-only user has no reason to open FreeCAD at all) now that the native
  app covers this without it.
