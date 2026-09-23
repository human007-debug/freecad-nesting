"""
cutting_defaults.py
--------------------
Industry-standard-derived default cutting parameters (part spacing,
sheet-edge margin, microjoint tab width/spacing) as a function of material
thickness -- a starting point for a thickness the job hasn't been tuned
for yet, not a substitute for a shop's own kerf-verified numbers. Every
formula below is a published sheet-metal laser-cutting DFM rule of thumb,
not something measured on any specific machine:

  - Part spacing: published guides converge on "space cut geometry at
    least 2x material thickness" to avoid heat-affected-zone bridging
    between adjacent cuts. Floored at 1mm (a bare "2x" would recommend
    sub-1mm gaps on thin stock, impractical to handle) and capped at 8mm
    (past this "2x" gets needlessly conservative on thick plate -- shops
    don't actually double their gap again at 20mm+).
  - Sheet-edge margin: guides call for >=1x-1.5x thickness from a cut
    feature to the sheet edge, to avoid distortion near the edge. Floored
    at 2mm (real stock sheets are undersized/warped at the edge regardless
    of thickness) and capped at 10mm.
  - Microjoint tab width: "keep tabs >= 1x-2x material thickness" is the
    most-cited rule -- narrower and the tab burns through before the cut
    finishes; much wider and it stops being a *micro*joint (it becomes a
    deliberate bridge needing its own finishing cut). Floored at 0.5mm,
    capped at 3mm.
  - Microjoint target spacing: no published guide ties this to thickness
    -- it's a part-size/mass judgment call, not a material property -- so
    this is just a fixed number, matching microjoints.add_tabs()'s own
    existing default.

Every multiplier/floor/ceiling above is a COEFFICIENT, not a hardcoded
constant -- see DEFAULT_COEFFICIENTS. A shop whose own kerf-verified
numbers differ from the generic DFM guideline (e.g. a plasma shop that
needs a wider part-spacing multiplier than a laser shop) overrides them
via nesting_widgets.py's "Recommendation formula" settings box, which
persists across sessions like every other preference -- see
native_app/persistence.py's AUTOSAVE_ATTRS. This module itself never
reads that UI; it only defines the published-guideline fallback and
applies whatever coefficients it's given.

HONEST SCOPE: DEFAULT_COEFFICIENTS is drawn from laser-cutting DFM guides
specifically; plasma/waterjet on the same thickness typically wants a
wider kerf/spacing than this recommends. Always confirm against a real
test cut on your own machine before trusting these defaults (or your own
overrides of them) on a job.
"""

DEFAULT_COEFFICIENTS = {
    "part_spacing_multiplier": 2.0, "part_spacing_min": 1.0, "part_spacing_max": 8.0,
    "margin_multiplier": 1.5, "margin_min": 2.0, "margin_max": 10.0,
    "tab_width_multiplier": 1.5, "tab_width_min": 0.5, "tab_width_max": 3.0,
    "tab_target_spacing": 150.0,
}


def recommend_defaults(thickness, coefficients=None):
    """thickness: material thickness in mm (any non-positive/None-derived
    value is treated as 0, i.e. the floor values). coefficients: optional
    dict overriding some/all of DEFAULT_COEFFICIENTS' keys -- missing keys
    fall back to the published default. Returns a dict with part_spacing,
    margin, microjoint_tab_width, microjoint_target_spacing -- all mm,
    matching nesting_widgets.py's part_spacing/margin_*/microjoint_width/
    microjoint_spacing fields one-for-one."""
    c = {**DEFAULT_COEFFICIENTS, **(coefficients or {})}
    t = max(float(thickness or 0.0), 0.0)
    return {
        "part_spacing": min(max(c["part_spacing_multiplier"] * t, c["part_spacing_min"]), c["part_spacing_max"]),
        "margin": min(max(c["margin_multiplier"] * t, c["margin_min"]), c["margin_max"]),
        "microjoint_tab_width": min(max(c["tab_width_multiplier"] * t, c["tab_width_min"]), c["tab_width_max"]),
        "microjoint_target_spacing": c["tab_target_spacing"],
    }


def recommend_defaults_for_many(thicknesses, coefficients=None):
    """The safe single choice when one global setting has to cover a job
    with several material thicknesses at once: since every formula above
    is non-decreasing in thickness, this is exactly recommend_defaults()
    of the thickest one -- a larger part_spacing/margin/tab_width is never
    wrong for a thinner sheet too (just a little less tight), so applying
    the thickest group's numbers keeps every group's cut safe. The real
    fix for a mixed-thickness job is a per-(material, thickness) cutting
    profile rather than one shared global setting -- this is the
    conservative fallback until that exists."""
    thicknesses = [t for t in thicknesses if t is not None]
    if not thicknesses:
        return recommend_defaults(0.0, coefficients)
    return recommend_defaults(max(thicknesses), coefficients)
