# AlphaQuote — Sheet-Metal Quoting Plan

A plan for a quoting module modelled on Hexagon RADAN Radquote, built on
top of the AlphaNest nesting engine and its FreeCAD integration. Status:
**plan only — nothing implemented yet.**

## 1. What Radquote does (research summary)

Sources are public/marketing-level (see bottom); details beyond them are
inferred from how the product is generally used.

- **Quotes sheet-metal parts, assemblies and purchased components** — a
  quote is a list of parts with quantities, per customer.
- **Geometry-driven**: cut length, pierce count, part area, weight and
  bends come from the CAD/unfold model, not manual entry.
- **Nest-driven material cost**: parts are nested to get real sheet
  utilisation, and scrap is apportioned into each part's material cost.
- **Cost centres per operation**: standard ones are material, laser
  cutting, punching and bending. Welding, painting, subcontract and
  assembly are configurable; new operations can be added. Quote-level
  costs (e.g. transport, analysis) are also supported.
- **Full cost breakdown**, every value overridable, separate markup per
  cost area (for negotiating).
- **Quantity breaks** (1 / 10 / 100 off) — setup amortised, unit price
  falls with batch size.
- **Reporting**: customer quote letters/emails and internal analysis
  reports.
- **Database**: customers, quotes, rates, materials; re-quote/revise;
  won quote → job.

### Process coverage

| Process | Radquote coverage |
|---|---|
| Laser cutting, punching, bending | Costed from geometry and RADAN's CAM engine (cycle times, material use) |
| Welding | Standard operation, including joins between parts in assemblies — rate/rule based |
| Assembly, painting, subcontract | Standard, rate/rule based; purchased parts supported |
| Machining | No dedicated feature found — would be a user-defined operation or subcontract line |
| Tube / section cutting | Not confirmed publicly. RADAN has a separate tube CAM product (Radtube); no source says Radquote costs tube parts from geometry |

Only cutting and bending are costed "smartly" from geometry; everything
else runs on user-configured rates and formulas. AlphaQuote can go
further by reading welds, profiles and hole features from FreeCAD.

## 2. What AlphaNest already provides

| Quoting needs | Already in AlphaNest |
|---|---|
| FreeCAD/STEP/DXF part import + unfold | `freecad_extract.py`, `dxf_extract.py`, `part_import.py`, `native_app/freecad_bridge.py` |
| Real nesting and utilisation | `nester.py`, `genetic.py`, `stock_solver.py` |
| Material price/kg, density, scrap value, currency | `inventory.py` (`StockSheet.material_cost()`, `weight_kg()`) |
| Remnant tracking | `remnant.py` |
| Per-thickness kerf/spacing defaults | `cutting_defaults.py` |
| Common-line cutting, microjoints | `commonline.py`, `microjoints.py` |
| App shell and FreeCAD workbench | `native_app/`, `freecad_workbench/` |
| HTML reports | `reports/` |

**Gaps:** the extractor records no bend data; there are no cut-time or
labour calculations; there is no quote or customer model.

## 3. Architecture

A new `quoting/` package beside the nesting engine, same install, two
front ends (the standalone app and the workbench), same as AlphaNest:

```
quoting/
  features.py        # per-part: cut length, pierces, area, weight, bends, holes/threads
  rates.py           # machine & operation rate tables (per material/thickness)
  operations.py      # pluggable cost centres (Operation base class + sheet ops)
  classify.py        # tag each solid: sheet / tube / section / machined / purchased
  ops_weld.py        # weld length & type from assembly joints
  ops_tube.py        # tube & section cutting costs
  ops_machining.py   # per-feature machining costs
  ops_assembly.py    # hardware + assembly labour
  costing.py         # quote engine: parts × ops × qty-breaks → cost breakdown
  quote_model.py     # Customer, Quote, QuoteLine, Revision, status
  db.py              # SQLite store (customers, quotes, rates, history)
  reports.py         # quote letter (HTML/PDF), internal breakdown, XLSX export
bar_nest.py          # 1D length nesting for bar/tube stock (sibling of stock_solver.py)
native_app/quote_panel.py            # Quote tab in the existing app
freecad_workbench/quote_command.py   # "Quote this document" in FreeCAD
```

## 4. Phases

### Phase 1 — Part features (the base everything else uses)

- Extend `freecad_extract.py` to output bend count, bend lengths, angles
  and flange directions from the SheetMetal unfold, plus total cut
  length, pierce count, internal contours, and gross vs net area.
- Also record holes and threads (for Phase 2b machining).
- DXF-only parts use the same schema; bends are "unknown" or entered
  by hand.
- Tests use known fixture parts (e.g. a 100×50 plate with 2 holes must
  give the correct cut length and pierce count).

### Phase 2 — Rate tables and operations

- Rate tables per material and thickness: cutting speed, pierce time,
  $/hr, setup time, bend time per hit and setup per tool change.
- Stored in SQLite, with import/export through XLSX in the same way
  `inventory.py` handles stock.
- Each operation is a small class with `time(part, qty)` and
  `cost(...)`; adding an operation is just another rule (per metre,
  per m², flat fee, …).
- Formulas:
  - laser time = cut length ÷ speed + pierces × pierce time
  - bend time = setup + hits × cycle time
  - paint cost = area × 2 × rate
- Ships with starter rates for mild steel, stainless and aluminium laser
  cutting, clearly marked "calibrate me".

### Phase 2b — Processes beyond sheet metal

Goal: cost welding, tube and section cutting, machining and assembly
from FreeCAD geometry where possible, and fall back to rates you enter
where not. Every process uses the same `Operation` interface as
Phase 2, so the costing engine and reports need no special cases.

**2b.1 Sort each solid into a part type (`classify.py`)**
- *Sheet*: a SheetMetal object or passes `detect_flat_plate`. Goes
  down the existing path.
- *Tube or section*: a straight extrusion with a constant cross-section.
  Match the section against a profile library (round/square/rectangular
  hollow section, angle, channel, I-beam, flat bar). Record the length
  and end cuts (square, mitred, coped).
- *Machined*: a solid that is neither of the above, or any solid with
  cylindrical holes, threads or pockets.
- *Purchased*: Fasteners-workbench objects, or names matching a
  bought-in parts list.
- You can always override the part type in the UI.

**2b.2 Welding (`ops_weld.py`)**
- In a FreeCAD assembly, find edges where two parts meet and add those
  up as weld length per joint.
- Weld type (fillet or butt) and a per-joint on/off come from settings.
  By default every contact line is a fillet weld, which you can edit.
- Cost = length × (time per metre for this weld type and thickness) ×
  $/hr + setup/fixturing per assembly + filler material per metre.
- Fallback: enter weld length or number of tacks by hand.

**2b.3 Tube and section cutting (`ops_tube.py` + `bar_nest.py`)**
- Material: stock lengths priced per metre or per kg (reuse the density
  and price-per-kg handling from `inventory.py`, adding a
  `stock_length` column).
- 1D nesting: pack part lengths into stock bars with kerf and trim ends,
  using first-fit decreasing plus local improvement. Leftover bars go
  back in as remnants.
- Cutting time: saw cuts per end (time depends on section size) or
  tube-laser time (perimeter × speed + cut-outs). You choose the
  machine type.
- Mitres and copes add a time multiplier for each end.

**2b.4 Machining (`ops_machining.py`)** — costs are per feature, not
full milling-time estimates
- Plain holes, tapped holes, countersinks and counterbores are counted
  from cylinder and cone faces, grouped by diameter.
- Pockets and slots: volume removed ÷ removal rate (rough).
- Each operation also has a setup time.
- Cost = Σ(count × time per feature) + setup, all × $/hr.
- An estimate flag shows these figures are less accurate than cutting
  and bending costs.

**2b.5 Assembly and bought-in parts (`ops_assembly.py`)**
- Bought-in and hardware lines carry unit cost, supplier and markup
  (PEM nuts, bolts, hinges, …).
- Assembly labour = base time + time per part + time per fastener.
- Quote-level costs: packing, transport, inspection, certificates.

**2b.6 Subcontract and custom operations**
- A general operation type: a flat fee, or per part, per kg, per m² or
  per metre, with its own markup.
- Covers painting, galvanising, powder coating, heat treatment, or
  anything else you add.

**Tests**
- Fixture parts: a welded bracket assembly (known weld length), a
  rectangular hollow section frame with mitres, a plate with 4× M8
  tapped holes, and a bolt from the Fasteners workbench. Check that
  each gets the right part type and features.
- `bar_nest`: checks for known best packings and for kerf and trim
  handling.

**Out of scope (for now)**
- Full CNC toolpath or cycle-time simulation.
- Robotic weld path planning.
- Tube-laser CAM output (Phase 2b only costs tube parts; it doesn't
  generate cutting programs).

### Phase 3 — Costing engine

- Material comes from a real nest through `stock_solver` (sheet) or
  `bar_nest` (tube/section), using the actual stock consumed per
  material and thickness/profile group.
- Material cost for each part is split in proportion to its net area
  (default) or its bounding-box share, minus scrap resale value
  (`scrap_price_per_kg` already exists).
- Remnants used can be priced at full value or at a discount, whichever
  you choose.
- Quantity breaks: re-nest the job at each quantity, spread the setup
  over the batch, and apply the markup for each cost area.
- Lines are grouped by part type.
- Manual overrides on any cell, with the original calculated value kept
  alongside.

### Phase 4 — Quote model, database, UI

- SQLite store for customers, quotes, lines, revisions and status
  (draft/sent/won/lost).
- Quote tab in the existing app: drag in FCStd, STEP or DXF files or a
  whole assembly, set quantities, and see the cost breakdown as a grid
  you can edit. The grid has a "Type" column and a per-line part-type
  override.
- Assemblies expand the FreeCAD document tree into part lines plus
  hardware and assembly labour.
- The FreeCAD workbench gets one button that sends the open document
  to a new quote.

### Phase 5 — Output

- Customer quote letter as HTML and PDF, with logo, terms, validity and
  a quantity-break table.
- Internal report showing the cost breakdown by operation and margin.
- XLSX export.
- Later: email draft; a won quote becomes a job whose nest is ready to
  cut.

### Phase 6 — Optional

- Win/loss analysis.
- Rate calibration from actual job times.
- CSV/API hook to push quotes into ERP systems.

## 5. Default decisions

- **Material allocation:** by net area. It's simple and easy to defend;
  bounding-box allocation is a setting.
- **Storage:** SQLite rather than loose JSON — quotes need history and
  search.
- **Rates:** starter tables for mild steel, stainless and aluminium
  laser cutting, marked "calibrate me". Real speeds depend on the
  machine.
- **Scope:** laser and press brake first, punching later (AlphaNest is
  built around laser and plasma).

## 6. Risks

- Cutting time is only as accurate as the rate table. Without a
  machine-speed database, first estimates may be off by 10–30% until
  calibrated.
- Getting bends out of FreeCAD depends on the SheetMetal model being
  clean. Imported STEP files may need a manual bend count.
- Re-nesting at every quantity break is slow on big jobs. May need a
  quick estimate mode based on utilisation, with a full nest done only
  when asked.
- Weld detection from contact edges will over-count on bolted
  assemblies. Hence per-joint on/off and the part-type override.

## 7. Suggested first step

Phase 1 (bends and cut features in the extractor) plus a minimal
`costing.py` that prices one part at one quantity. That shows the
numbers are right before any UI work.

## Sources

- [RADAN Radquote | Hexagon](https://hexagon.com/products/radan-radquote)
- [RADAN Radquote – SNC Solutions](https://sncsolutions.com.au/sheet-metal-quotation-software/)
- [Radquote sheet metal quote editor – industrie-online](https://www.industrie-online.com/en/product/8288/radquote-sheet-metal-quote-editor/hexagon-radan)
- [Radan Radquote – Dreambird](https://dreambirdlv.dreambird.eu/solutions/radan/process-management/radquote/)
- [RADAN Process Management – Technical Solutions](http://technical-solutions.lv/solutions-2/radan-process-management/)
