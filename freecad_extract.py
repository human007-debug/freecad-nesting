"""
freecad_extract.py
-------------------
Runs INSIDE FreeCAD's own Python (FreeCADCmd), not plain python3 -- it needs
the `FreeCAD`/`Part` modules and the SheetMetal workbench's unfolder.

For each .FCStd file given, it:
  1. Walks the document looking for SheetMetal-workbench objects (anything
     whose Proxy class lives in a `SheetMetal*` module -- SMBaseBend,
     SMBendWall, SMHem, etc.), keeping only the "leaf" of each chain (a
     SheetMetal object that isn't itself the `baseObject` of a later
     SheetMetal feature), then forward-follows any further FreeCAD
     dependency (e.g. a Part::Cut punching mounting holes on top of the
     bend) to the final shape actually meant for the nest.
  2. If that final shape is already flat (two large parallel skin faces
     spanning the whole part -- e.g. a part with no bends at all, or one
     that already went through the workbench's own Unfold), it lifts the
     outer wire + hole wires straight off the flat face.
  3. Otherwise it runs the SheetMetal workbench's own bend-flattening
     algorithm (`SheetMetalNewUnfolder.getUnfold`, trying each planar face
     as the flattening's reference face, largest first, until one works),
     then does the same wire lift on the unfolded result.
  4. Tessellates every wire (arcs/circles included) into a straight-edge
     polygon -- `nester.py`'s geometry only understands straight edges --
     and writes {name, points, holes, thickness, quantity, ...} for every
     part found to a JSON file.

A .step/.stp/.iges/.igs path is also accepted (imported into a fresh
document via `Part.insert`, which works headless under FreeCADCmd -- no
ImportGui/viewer needed). Those files never carry SheetMetal feature
history (STEP/IGES only store the final BRep solid, not a parametric
feature tree), so step 1's SheetMetal walk-back never finds anything in
them; instead every plain object with solid geometry that isn't already
claimed by the SheetMetal walk is tried directly against step 2's flat-plate
test. A plain object that IS flat extracts exactly like any other flat
SheetMetal part. A plain object that ISN'T flat can't be unfolded here --
step 3's algorithm needs a live SheetMetal feature object for its bend
math, which a bare imported solid doesn't have -- so it's written to a
separate `"unresolved"` list in the output JSON instead of being guessed
at or silently dropped; the shop-floor fix is to get a flat-pattern DXF for
that part (from whatever authored the bend) and run it through
`dxf_extract.py` instead. A real assembly STEP file often also carries
incidental small solids that were never meant to be cut -- jig/fixture
geometry, reference tabs, and the like -- so a plain flat object whose net
area is below `min_area` (default 0, disabled) is written to a third
`"skipped"` list instead of `"parts"`, rather than flooding the nesting
job with dozens of tiny non-parts.

An assembly commonly places the same physical part multiple times (e.g. a
bracket repeated at every rib) -- FreeCAD's SheetMetal/STEP discovery
above has no concept of "these N objects are the same part," so without
help every instance would become its own JSON entry at quantity 1. After
extraction, `merge_duplicate_instances()` groups parts by a placement-
invariant geometric fingerprint (thickness, net area, perimeter, sorted
bounding-box extents, sorted hole areas -- NOT the raw tessellated points,
which differ between instances placed at different positions/rotations
even though the underlying shape is identical) and collapses each group
into one entry with quantity = the group's instance count. This is a
pragmatic heuristic, not a true shape-congruence check -- like
`detect_flat_plate`'s tolerance-based flatness test, two genuinely
different parts that happen to share the exact same area/perimeter/bbox/
holes would incorrectly merge, but that coincidence is rare enough for
real sheet-metal parts to be an acceptable trade for not showing 50 rows
of the same bracket.

`part_import.py` (plain python3, no FreeCAD needed) then loads that JSON
into `nester.Part` objects for `nester.Nester`.

Usage (flatpak install of FreeCAD, adjust for a non-flatpak one):
    flatpak run --command=FreeCADCmd org.freecad.FreeCAD \\
        freecad_extract.py --pass model.FCStd output=parts.json \\
        kfactor=0.4 tolerance=0.25 qty:Some-Part=6

FreeCADCmd's own argument parser inspects the WHOLE command line itself and
errors on any '-'-prefixed token it doesn't recognize -- '--pass' does not
suppress that, it just also happens to let bare (non-dashed) tokens after
it reach the script's sys.argv. So this script takes no `--flags` of its
own: every .FCStd path is a bare positional arg, and every tuning knob is a
bare `key=value` token (`qty:<Label>=<N>` for a per-part quantity
override) -- see `_script_args()` and `_parse_args()` below.

The SheetMetal workbench's newer unfolder needs `networkx`, which isn't
part of FreeCAD's bundled Python. Vendor it once with e.g.:
    flatpak run --command=pip3 org.freecad.FreeCAD install \\
        --target=<this-dir>/vendor networkx
`_default_vendor_dir()` picks that up automatically.
"""

import json
import math
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import Part


def _script_args():
    """FreeCADCmd's parser swallows plain flags itself, so real args to this
    script must be passed after a literal '--pass' (FreeCAD's own escape
    hatch for handing arguments to a script) -- see module docstring."""
    argv = sys.argv[1:]  # drop the 'FreeCADCmd' pseudo-argv[0]
    rest = argv[1:] if argv else []  # drop this script's own path
    if rest and rest[0] == "--pass":
        rest = rest[1:]
    return rest


def _default_sheetmetal_dir():
    try:
        base = App.getUserAppDataDir()
    except Exception:
        return None
    for candidate in (
        os.path.join(base, "Mod", "SheetMetal"),
        os.path.normpath(os.path.join(base, "..", "Mod", "SheetMetal")),
    ):
        if os.path.isdir(candidate):
            return candidate
    return None


def _default_vendor_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "vendor")
    return candidate if os.path.isdir(candidate) else None


def _setup_paths(sheetmetal_dir, vendor_dir):
    if sheetmetal_dir and sheetmetal_dir not in sys.path:
        sys.path.insert(0, sheetmetal_dir)
    # Appended, not prepended: vendor/ is a fallback for a FreeCAD Python
    # with no networkx/pyclipper of its own, and its compiled pyclipper only
    # loads on Linux/Python 3.13 -- it must never shadow a working install.
    if vendor_dir and vendor_dir not in sys.path:
        sys.path.append(vendor_dir)


# ------------------------------------------------------------- discovery

def read_object_modules(fcstd_path):
    """Map {object_name: (module, class)} for every object in the document
    whose 'Proxy' property records a Python module (i.e. every
    Part::FeaturePython / PartDesign::FeaturePython object). Read straight
    out of the saved Document.xml, NOT via FreeCAD's live obj.Proxy -- as of
    FreeCAD 1.1, restoring a Python feature's Proxy on document-open is
    blocked unless FreeCAD considers the module's addon "installed" (its own
    internal registry, unrelated to the module being perfectly importable
    off sys.path), which silently leaves obj.Proxy as None for a workbench
    that was only ever `git clone`d into Mod/ by hand -- exactly how this
    project's own SheetMetal checkout got there. The module/class names
    are plain XML attributes on the saved property, so reading them doesn't
    need to import or execute anything the security check would block."""
    with zipfile.ZipFile(fcstd_path) as z:
        xml_bytes = z.read("Document.xml")
    root = ET.fromstring(xml_bytes)
    modules = {}
    for obj_el in root.iter("Object"):
        name = obj_el.get("name")
        if name is None:
            continue
        for prop_el in obj_el.findall("./Properties/Property[@name='Proxy']"):
            py_el = prop_el.find("Python")
            if py_el is not None and py_el.get("module"):
                modules[name] = (py_el.get("module"), py_el.get("class"))
    return modules


def get_modules_for_doc(doc):
    """Same {object_name: (module, class)} map as read_object_modules(), but
    for a document that's already open (e.g. the workbench UI's active
    document) rather than a path on disk. Prefers reading the saved file
    (handles objects restored-from-disk, where obj.Proxy is blocked -- see
    read_object_modules()'s docstring); for objects that only ever existed
    in this live session (created fresh, never saved, or saved after
    creation so the restore-block never applied to them) that will miss
    ones added since the last save, so it's topped up from the live
    obj.Proxy too, which works fine for freshly-created objects -- the
    security gate only fires on restoring a Proxy from a saved file."""
    modules = {}
    if doc.FileName and os.path.isfile(doc.FileName):
        try:
            modules.update(read_object_modules(doc.FileName))
        except Exception:
            pass
    for obj in doc.Objects:
        if obj.Name in modules:
            continue
        proxy = getattr(obj, "Proxy", None)
        if proxy is not None:
            modules[obj.Name] = (type(proxy).__module__, type(proxy).__name__)
    return modules


def _is_sheetmetal_object(obj, modules):
    module, _cls = modules.get(obj.Name, (None, None))
    return bool(module) and module.startswith("SheetMetal")


def _is_unfold_object(obj, modules):
    module, _cls = modules.get(obj.Name, (None, None))
    return module == "SheetMetalUnfoldCmd"


def _base_object_of(obj):
    link = getattr(obj, "baseObject", None)
    if link is None:
        return None
    if isinstance(link, (tuple, list)) and link:
        return link[0]
    return link


def _forward_follow(obj):
    """Follow single-consumer FreeCAD dependency links (e.g. a Part::Cut
    punching holes into a bend) to the final shape meant for the nest.
    Stops if a node has no consumers, or more than one (ambiguous -- don't
    guess which branch is 'the' finished part)."""
    seen = set()
    while obj.Name not in seen:
        seen.add(obj.Name)
        consumers = [o for o in obj.InList if hasattr(o, "Shape")]
        if len(consumers) != 1:
            return obj
        obj = consumers[0]
    return obj


def find_sheetmetal_parts(doc, modules):
    """Returns a list of (leaf_sheetmetal_object, final_shape_object)."""
    sm_objects = [o for o in doc.Objects if _is_sheetmetal_object(o, modules) and not _is_unfold_object(o, modules)]
    consumed_names = set()
    for o in sm_objects:
        base = _base_object_of(o)
        if base is not None:
            consumed_names.add(base.Name)
    leaves = [o for o in sm_objects if o.Name not in consumed_names]

    results = []
    seen_final = set()
    for leaf in leaves:
        final_obj = _forward_follow(leaf)
        if final_obj.Name in seen_final:
            continue
        seen_final.add(final_obj.Name)
        results.append((leaf, final_obj))
    return results


def find_plain_solid_objects(doc, modules, exclude_names):
    """Objects with solid geometry that aren't part of a SheetMetal chain --
    what a STEP/IGES import produces (plain Part::Feature objects, no
    feature history at all). There's no baseObject/InList chain to walk for
    these: each one is used as-is, whatever the caller finds it in."""
    results = []
    for obj in doc.Objects:
        if obj.Name in exclude_names or _is_sheetmetal_object(obj, modules):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Solids:
            continue
        results.append(obj)
    return results


# ------------------------------------------------------------- geometry

def _plane_normal(face):
    return face.normalAt(0, 0)


def detect_flat_plate(shape, tol=1e-3):
    """If `shape` is a plain flat plate (two large parallel skin faces that
    account for the whole part -- nothing sticks out of that plane), return
    (skin_face, thickness). Otherwise return None (the part has bends and
    needs the SheetMetal unfolder)."""
    planar = [f for f in shape.Faces if f.Surface.TypeId == "Part::GeomPlane"]
    if len(planar) < 2:
        return None

    by_dir = {}
    for f in planar:
        n = _plane_normal(f)
        key = (round(n.x, 4), round(n.y, 4), round(n.z, 4))
        by_dir.setdefault(key, []).append(f)

    best = None  # (face, thickness, area)
    for key, faces in by_dir.items():
        neg_key = (-key[0], -key[1], -key[2])
        if neg_key not in by_dir:
            continue
        for f1 in faces:
            n = _plane_normal(f1)
            for f2 in by_dir[neg_key]:
                d = abs((f1.Surface.Position - f2.Surface.Position).dot(n))
                area = min(f1.Area, f2.Area)
                if best is None or area > best[2]:
                    best = (f1, d, area)
    if best is None:
        return None

    face, thickness, _area = best
    if thickness < 1e-6:
        return None
    n = _plane_normal(face)
    bbox = shape.BoundBox
    extent = (
        abs(n.x) * (bbox.XMax - bbox.XMin)
        + abs(n.y) * (bbox.YMax - bbox.YMin)
        + abs(n.z) * (bbox.ZMax - bbox.ZMin)
    )
    if abs(extent - thickness) > max(tol, thickness * 0.05):
        return None  # something extends out of this plane -- it's bent
    return face, thickness


def _project_wire(wire, normal, origin, tol):
    n = normal.normalize()
    tmp = App.Vector(1, 0, 0) if abs(n.x) < 0.9 else App.Vector(0, 1, 0)
    u = tmp.cross(n).normalize()
    v = n.cross(u).normalize()

    pts3d = wire.discretize(Deflection=tol)
    pts2d = [((p - origin).dot(u), (p - origin).dot(v)) for p in pts3d]
    if len(pts2d) > 1 and math.hypot(pts2d[0][0] - pts2d[-1][0], pts2d[0][1] - pts2d[-1][1]) < 1e-6:
        pts2d.pop()
    return pts2d


def extract_flat(face, tol):
    origin = face.Vertexes[0].Point
    normal = _plane_normal(face)
    outer_hash = face.OuterWire.hashCode()
    outer = _project_wire(face.OuterWire, normal, origin, tol)
    holes = [
        _project_wire(w, normal, origin, tol)
        for w in face.Wires
        if w.hashCode() != outer_hash
    ]
    return outer, holes


def run_unfold(obj, bac):
    """Try SheetMetal's bend-flattening algorithm using each planar face of
    `obj` as the reference face, largest area first, until one succeeds
    (an arbitrary/wrong reference face raises rather than mis-flattening)."""
    import SheetMetalNewUnfolder as NewUnfolder

    shape = obj.Shape
    planar_idx = sorted(
        (i for i, f in enumerate(shape.Faces) if f.Surface.TypeId == "Part::GeomPlane"),
        key=lambda i: -shape.Faces[i].Area,
    )
    last_err = None
    for i in planar_idx:
        facename = f"Face{i + 1}"
        try:
            _sel_face, unfolded_shape, _bend_lines, root_normal, bend_infodata = NewUnfolder.getUnfold(
                bac, obj, facename
            )
            return unfolded_shape, root_normal, bend_infodata, NewUnfolder
        except Exception as e:  # noqa: BLE001 -- deliberately broad, we retry
            last_err = e
    raise RuntimeError(f"unfold failed on all {len(planar_idx)} candidate faces: {last_err}")


def extract_part(leaf, final_obj, bac, tol):
    shape = final_obj.Shape
    flat = detect_flat_plate(shape, tol)
    if flat is not None:
        face, thickness = flat
        outer, holes = extract_flat(face, tol)
        return {
            "outer": outer,
            "holes": holes,
            "thickness": thickness,
            "bends": 0,
            "method": "flat",
        }

    unfolded_shape, root_normal, bend_infodata, NewUnfolder = run_unfold(final_obj, bac)
    sketch_profile, inner_wires, hole_wires = NewUnfolder.SketchExtraction.extract_manually(
        unfolded_shape, root_normal
    )
    z = App.Vector(0, 0, 1)
    origin = App.Vector(0, 0, 0)
    outer = _project_wire(sketch_profile, z, origin, tol)
    holes = [_project_wire(w, z, origin, tol) for w in list(inner_wires) + list(hole_wires)]
    thickness = abs(unfolded_shape.BoundBox.ZLength)
    return {
        "outer": outer,
        "holes": holes,
        "thickness": thickness,
        "bends": len(bend_infodata),
        "method": "unfold",
    }


def extract_plain_part(obj, tol):
    """Only the flat-plate fast path applies to a plain (non-SheetMetal)
    object -- there's no feature history to run SheetMetal's unfolder
    against, so a bent one can't be flattened here at all. Returns None if
    the object isn't flat (caller records it as unresolved). `face.Area` is
    OCC's exact net area (outer wire minus holes) -- included so the caller
    can filter out incidental small solids (fixture/tooling geometry that
    rode along in a STEP assembly) without re-deriving area from the
    tessellated 2D points."""
    flat = detect_flat_plate(obj.Shape, tol)
    if flat is None:
        return None
    face, thickness = flat
    outer, holes = extract_flat(face, tol)
    return {
        "outer": outer,
        "holes": holes,
        "thickness": thickness,
        "bends": 0,
        "method": "flat",
        "area": face.Area,
    }


# --------------------------------------------------- duplicate instances

def _polygon_area(points):
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _polygon_perimeter(points):
    n = len(points)
    if n < 2:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _shape_signature(part, ndigits=2):
    """Placement-invariant fingerprint used to spot repeated instances of
    the same physical part in an assembly -- see module docstring. Uses
    already-tessellated `points`/`holes` (outer contour + hole loops), not
    the object's world-space Shape.

    Deliberately NOT using an axis-aligned bounding box here: each
    instance's points are tessellated in a (u, v) frame `_project_wire`
    derives independently from that instance's own world-space face
    normal (see its docstring), so two instances placed at DIFFERENT
    rotations project into unrelated frames -- confirmed empirically that
    a part rotated by a non-right angle gets a different axis-aligned
    bbox than the same part at 0 degrees, even though it's the same shape,
    which would wrongly block the merge. Area and perimeter, by contrast,
    are true rigid-motion invariants (unaffected by which 2D frame the
    polygon happens to be expressed in), so they're what this compares."""
    pts = part["points"]
    area = round(_polygon_area(pts), ndigits)
    perimeter = round(_polygon_perimeter(pts), ndigits)
    hole_areas = tuple(sorted(round(_polygon_area(h), ndigits) for h in part["holes"]))
    thickness = round(part["thickness"], ndigits) if part["thickness"] is not None else None
    return (thickness, area, perimeter, len(part["holes"]), hole_areas)


def merge_duplicate_instances(parts):
    """Collapse parts sharing a `_shape_signature` into one entry each,
    quantity = sum of the merged entries' own quantities (so an explicit
    qty: override on one instance still counts). Keeps the first-seen
    instance's name/source_object/material, adds an `"instances"` list of
    every merged instance's source_object name for traceability. A group
    of one is returned unchanged (plus the singleton `"instances"` list),
    so this is a no-op for an assembly with no repeated parts."""
    groups = {}
    order = []
    for part in parts:
        sig = _shape_signature(part)
        if sig not in groups:
            merged = dict(part)
            merged["instances"] = [part["source_object"]]
            groups[sig] = merged
            order.append(sig)
        else:
            merged = groups[sig]
            merged["quantity"] = merged.get("quantity", 1) + part.get("quantity", 1)
            merged["instances"].append(part["source_object"])
            if merged.get("material") is None and part.get("material") is not None:
                merged["material"] = part["material"]
    return [groups[sig] for sig in order]


# ------------------------------------------------------------------- CLI

class Args:
    def __init__(self):
        self.fcstd = []
        self.output = "parts.json"
        self.kfactor = 0.4
        self.kfactor_standard = "ansi"
        self.tolerance = 0.25
        self.min_area = 0.0
        self.sheetmetal_dir = None
        self.vendor_dir = None
        self.qty_overrides = {}
        self.material_overrides = {}


def _parse_args(argv):
    """Hand-rolled parser: no '-'-prefixed flags (see module docstring for
    why) -- every .FCStd path is a bare positional, every knob is a bare
    `key=value` token, `qty:<Label>=<N>` for a per-part quantity override,
    `material:<Label>=<name>` for a per-part material tag (there's no
    geometry-derived way to know a part's material -- see inventory.py)."""
    args = Args()
    for tok in argv:
        if tok.startswith("qty:") and "=" in tok:
            label, _, n = tok[len("qty:"):].partition("=")
            args.qty_overrides[label] = int(n)
        elif tok.startswith("material:") and "=" in tok:
            label, _, name = tok[len("material:"):].partition("=")
            args.material_overrides[label] = name
        elif "=" in tok and tok.split("=", 1)[0] in (
            "output", "kfactor", "kfactor_standard", "tolerance", "min_area", "sheetmetal_dir", "vendor_dir"
        ):
            key, _, value = tok.partition("=")
            if key in ("kfactor", "tolerance", "min_area"):
                value = float(value)
            setattr(args, key, value)
        else:
            args.fcstd.append(tok)
    if not args.fcstd:
        raise SystemExit("usage: freecad_extract.py <model.FCStd|model.step|model.iges> [more ...] "
                          "[output=parts.json] [kfactor=0.4] [kfactor_standard=ansi] [tolerance=0.25] "
                          "[min_area=0.0] [qty:<Label>=<N>] [material:<Label>=<name>]")
    return args


_CAD_EXTENSIONS = {
    ".fcstd": "fcstd",
    ".step": "step",
    ".stp": "step",
    ".iges": "iges",
    ".igs": "iges",
}


def _open_cad_file(abspath):
    """Returns (doc, modules, file_kind). FCStd keeps the existing
    Document.xml-based module read (see read_object_modules's docstring for
    why); STEP/IGES have no Proxy/feature-tree concept at all, so `modules`
    is just empty and every object in the resulting document is 'plain'."""
    ext = os.path.splitext(abspath)[1].lower()
    file_kind = _CAD_EXTENSIONS.get(ext)
    if file_kind is None:
        raise SystemExit(f"unsupported file type: {abspath} (expected .FCStd, .step/.stp, or .iges/.igs)")
    if file_kind == "fcstd":
        modules = read_object_modules(abspath)
        doc = App.openDocument(abspath)
    else:
        modules = {}
        doc = App.newDocument()
        Part.insert(abspath, doc.Name)
    return doc, modules, file_kind


def main(argv):
    args = _parse_args(argv)

    _setup_paths(args.sheetmetal_dir or _default_sheetmetal_dir(), args.vendor_dir or _default_vendor_dir())
    import SheetMetalNewUnfolder as NewUnfolder

    bac = NewUnfolder.BendAllowanceCalculator.from_single_value(args.kfactor, args.kfactor_standard)

    all_parts = []
    all_unresolved = []
    all_skipped = []
    for path in args.fcstd:
        abspath = os.path.abspath(path)
        doc, modules, file_kind = _open_cad_file(abspath)
        try:
            finals = find_sheetmetal_parts(doc, modules)
            if not finals and file_kind == "fcstd":
                print(f"[warn] no SheetMetal-workbench parts found in {path}", file=sys.stderr)

            exclude_names = {leaf.Name for leaf, _ in finals} | {final_obj.Name for _, final_obj in finals}
            plain_objs = find_plain_solid_objects(doc, modules, exclude_names)

            for leaf, final_obj in finals:
                label = leaf.Label or leaf.Name
                try:
                    extracted = extract_part(leaf, final_obj, bac, args.tolerance)
                except Exception as e:  # noqa: BLE001
                    print(f"[error] {label}: {e}", file=sys.stderr)
                    continue
                qty = args.qty_overrides.get(label, args.qty_overrides.get(leaf.Name, 1))
                material = args.material_overrides.get(label, args.material_overrides.get(leaf.Name))
                all_parts.append({
                    "name": label,
                    "source_file": os.path.basename(path),
                    "source_object": leaf.Name,
                    "final_object": final_obj.Name,
                    "points": extracted["outer"],
                    "holes": extracted["holes"],
                    "thickness": extracted["thickness"],
                    "bends": extracted["bends"],
                    "method": extracted["method"],
                    "source": file_kind,
                    "quantity": qty,
                    "material": material,
                })
                print(f"[ok] {label}: {len(extracted['outer'])} outer pts, "
                      f"{len(extracted['holes'])} hole(s), method={extracted['method']}, "
                      f"thickness={extracted['thickness']}")

            for obj in plain_objs:
                label = obj.Label or obj.Name
                try:
                    extracted = extract_plain_part(obj, args.tolerance)
                except Exception as e:  # noqa: BLE001
                    print(f"[error] {label}: {e}", file=sys.stderr)
                    continue
                if extracted is None:
                    reason = "has bends, no SheetMetal feature history (STEP/IGES import) - supply a flat-pattern DXF"
                    print(f"[warn] {label}: {reason}", file=sys.stderr)
                    all_unresolved.append({"name": label, "source_file": os.path.basename(path), "reason": reason})
                    continue
                if args.min_area and extracted["area"] < args.min_area:
                    print(f"[skip] {label}: net area {extracted['area']:.2f} mm² is below "
                          f"min_area={args.min_area:g} mm² - excluded as tooling/fixture geometry",
                          file=sys.stderr)
                    all_skipped.append({
                        "name": label, "source_file": os.path.basename(path),
                        "area": extracted["area"], "reason": "net area below min_area",
                    })
                    continue
                qty = args.qty_overrides.get(label, args.qty_overrides.get(obj.Name, 1))
                material = args.material_overrides.get(label, args.material_overrides.get(obj.Name))
                all_parts.append({
                    "name": label,
                    "source_file": os.path.basename(path),
                    "source_object": obj.Name,
                    "final_object": obj.Name,
                    "points": extracted["outer"],
                    "holes": extracted["holes"],
                    "thickness": extracted["thickness"],
                    "bends": extracted["bends"],
                    "method": extracted["method"],
                    "source": file_kind,
                    "quantity": qty,
                    "material": material,
                })
                print(f"[ok] {label}: {len(extracted['outer'])} outer pts, "
                      f"{len(extracted['holes'])} hole(s), method={extracted['method']}, "
                      f"thickness={extracted['thickness']}")
        finally:
            App.closeDocument(doc.Name)

    before = len(all_parts)
    all_parts = merge_duplicate_instances(all_parts)
    merged_count = before - len(all_parts)
    if merged_count:
        for part in all_parts:
            if len(part["instances"]) > 1:
                print(f"[merge] {part['name']}: {len(part['instances'])} identical instances "
                      f"({', '.join(part['instances'])}) -> quantity {part['quantity']}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"parts": all_parts, "unresolved": all_unresolved, "skipped": all_skipped}, f, indent=2)
    extra = []
    if all_unresolved:
        extra.append(f"{len(all_unresolved)} unresolved")
    if all_skipped:
        extra.append(f"{len(all_skipped)} skipped (min_area)")
    if merged_count:
        extra.append(f"{merged_count} merged as duplicate instances")
    print(f"Wrote {len(all_parts)} part(s) to {args.output}" + (f", {', '.join(extra)}" if extra else ""))


def _is_direct_invocation():
    """FreeCADCmd runs a script with __name__ set to its own basename
    ('freecad_extract'), not '__main__' -- but a plain `import
    freecad_extract` (e.g. from the workbench UI) sets __name__ to that
    exact same string too, since that's just this module's name. The two
    are indistinguishable by __name__ alone, so check whether sys.argv[1]
    (FreeCADCmd's convention: argv = [exe, script, ...args]) actually
    points at this file, rather than at whatever script FreeCAD was really
    launched to run."""
    this_file = globals().get("__file__")
    if not this_file or len(sys.argv) < 2:
        return False
    try:
        return os.path.abspath(sys.argv[1]) == os.path.abspath(this_file)
    except Exception:
        return False


if __name__ == "__main__" or _is_direct_invocation():
    main(_script_args())
