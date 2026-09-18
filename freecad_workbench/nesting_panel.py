"""
nesting_panel.py
-----------------
The "Run Nesting" dialog, as seen from inside FreeCAD: a thin adapter that
wraps the shared `nesting_widgets.NestingPanel` (settings, parts table,
sheet preview, GA search, inventory, DXF export -- all FreeCAD-independent)
in a `QDialog`, and supplies the one genuinely FreeCAD-specific piece:
scanning the active document for SheetMetal-workbench parts (reusing
freecad_extract.py's discovery/flatten/extract functions directly,
in-process -- no JSON round-trip needed here since we're already inside
FreeCAD).

See nesting_widgets.py's module docstring for the `part_source` contract
this' `FreeCADDocSource` implements, and for how "Run Nesting"/"Optimize
Ordering"/"Commit to Inventory" behave -- none of that lives here anymore.
"""

import FreeCAD as App
from PySide import QtCore, QtWidgets

import freecad_extract as fe
from nesting_widgets import NestingPanel


class FreeCADDocSource:
    """Scans a live FreeCAD document for SheetMetal-workbench parts --
    exactly what NestingDialog._rescan() used to do inline before the panel
    was split out into nesting_widgets.py. Also picks up plain (non-
    SheetMetal) flat objects -- what you get after FreeCAD's own File ->
    Import of a STEP/IGES file, since that path carries no SheetMetal
    feature history at all -- via the same `find_plain_solid_objects`/
    `extract_plain_part` freecad_extract.py's CLI uses; see that module's
    docstring for why a bent plain object can't be unfolded here and lands
    in a log warning instead."""

    scan_label = "Rescan document"

    def __init__(self, doc):
        self.doc = doc

    def scan(self, kfactor, tolerance, min_area=0.0):
        logs = []
        entries = []  # fe.merge_duplicate_instances()'s input shape -- see freecad_extract.py

        fe._setup_paths(fe._default_sheetmetal_dir(), fe._default_vendor_dir())
        try:
            import SheetMetalNewUnfolder as NewUnfolder
        except Exception as e:
            logs.append(f"Could not load the SheetMetal unfolder ({e}). Is the "
                        f"workbench installed and networkx vendored? See README.md.")
            return {}, logs
        bac = NewUnfolder.BendAllowanceCalculator.from_single_value(kfactor, "ansi")

        modules = fe.get_modules_for_doc(self.doc)
        finals = fe.find_sheetmetal_parts(self.doc, modules)

        for leaf, final_obj in finals:
            label = leaf.Label or leaf.Name
            try:
                data = fe.extract_part(leaf, final_obj, bac, tolerance)
            except Exception as e:
                logs.append(f"[error] {label}: {e}")
                continue
            entries.append({
                "name": label, "source_object": leaf.Name, "points": data["outer"], "holes": data["holes"],
                "thickness": data["thickness"], "method": data["method"], "quantity": 1, "material": None,
            })

        exclude_names = {leaf.Name for leaf, _ in finals} | {final_obj.Name for _, final_obj in finals}
        plain_objs = fe.find_plain_solid_objects(self.doc, modules, exclude_names)
        for obj in plain_objs:
            label = obj.Label or obj.Name
            try:
                data = fe.extract_plain_part(obj, tolerance)
            except Exception as e:
                logs.append(f"[error] {label}: {e}")
                continue
            if data is None:
                logs.append(f"[warn] {label}: has bends, no SheetMetal feature history -- "
                            f"supply a flat-pattern DXF for this part.")
                continue
            if min_area and data["area"] < min_area:
                logs.append(f"[skip] {label}: net area {data['area']:.2f} mm² is below "
                            f"{min_area:g} mm² -- excluded as tooling/fixture geometry.")
                continue
            entries.append({
                "name": label, "source_object": obj.Name, "points": data["outer"], "holes": data["holes"],
                "thickness": data["thickness"], "method": data["method"], "quantity": 1, "material": None,
            })

        # Collapse repeated instances of the same physical part (e.g. a
        # bracket placed at every rib) into one entry with a summed
        # quantity -- see freecad_extract.py's module docstring.
        merged = fe.merge_duplicate_instances(entries)
        extracted = {}
        for part in merged:
            if len(part["instances"]) > 1:
                logs.append(f"[merge] {part['name']}: {len(part['instances'])} identical instances "
                            f"-> quantity {part['quantity']}")
            extracted[part["name"]] = {
                "outer": part["points"], "holes": part["holes"], "thickness": part["thickness"],
                "method": part["method"], "quantity": part["quantity"], "material": part["material"],
            }

        if not extracted:
            logs.append("No SheetMetal-workbench or flat parts found in this document.")
        else:
            logs.append(f"Found {len(extracted)} part(s).")
        return extracted, logs


class NestingDialog(QtWidgets.QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Nesting -- %s" % doc.Label)
        # Make this a plain top-level WINDOW, not a Dialog, or the window
        # manager classifies it as _NET_WM_WINDOW_TYPE_DIALOG and (on e.g.
        # Cinnamon/Muffin) denies maximize entirely -- the maximize button
        # never appears no matter which Qt hints are set. Qt.Window keeps
        # title/system-menu/close automatically; modality below preserves
        # the modal-block-the-main-FreeCAD-window behavior exec_() gave it.
        self.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
        )
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(1000, 640)

        self.panel = NestingPanel(FreeCADDocSource(doc), self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        self.panel.close_requested.connect(self.reject)
