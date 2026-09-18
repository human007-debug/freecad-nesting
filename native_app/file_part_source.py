"""
file_part_source.py
--------------------
The native app's part source (see nesting_widgets.py's module docstring
for the `part_source` contract): there's no single "document" to rescan
outside FreeCAD, so this accumulates parts from files picked one or more
times via "Add Parts...", routed by extension:
  .dxf                    -> dxf_extract.extract_parts() (direct library call, no subprocess)
  .json                   -> read the {"parts": [...]} file directly (not
                              part_import.load_parts(), which returns
                              nester.Part objects rather than the raw
                              {outer, holes, thickness, method} shape needed here)
  .fcstd/.step/.stp/.iges/.igs -> freecad_bridge.import_fcstd() (shells out to FreeCADCmd;
                              the extractor itself dispatches further by extension)

A STEP/IGES assembly with bent parts will only yield its FLAT parts here --
bent ones come back from freecad_bridge as "unresolved" (no SheetMetal
feature history to unfold from) and are surfaced as a `[warn]` log line
naming the part, same as any other warning. The fix is to add a
flat-pattern DXF for that part via another "Add Parts..." pick -- it merges
into the same job through the .dxf branch above.

A real assembly STEP also tends to carry small incidental solids that were
never meant to be cut -- jig/fixture geometry, reference tabs -- which
would otherwise flood the parts table and the preflight "Not ready" list
with junk. `min_area` (mm², reuses the same "Minimum net part area" value
already configured in Settings -- see `nesting_widgets.py`'s
`min_part_area`, default 25.0) is forwarded to `freecad_bridge.import_fcstd`
so the extractor excludes those before they're ever added; they're logged
as `[skip]`, not `[warn]`, since excluding them is the intended outcome,
not a problem to fix.
"""

import json
import os

from PySide6 import QtWidgets

from native_app import freecad_bridge


class FilePartSource:
    scan_label = "Add Parts..."
    scan_on_init = False  # don't pop a file picker before the window is even shown

    def __init__(self):
        self._extracted = {}  # accumulated across repeated "Add Parts..." clicks

    def reset(self):
        self._extracted = {}

    def scan(self, kfactor, tolerance, min_area=0.0):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None, "Add parts", "",
            "Supported files (*.dxf *.json *.FCStd *.step *.stp *.iges *.igs);;All files (*)"
        )
        if not paths:
            return dict(self._extracted), []
        return self.load_files(paths, kfactor=kfactor, tolerance=tolerance, min_area=min_area)

    def load_files(self, paths, kfactor=0.4, tolerance=0.25, min_area=0.0):
        """The interactive-picker-free half of scan(): import an explicit
        list of files. Used by scan() above and by the app's command-line
        startup when a parts.json path is passed in (e.g. by the FreeCAD
        workbench's Send to AlphaNest command, which launches the app with
        the file it just wrote)."""
        logs = []
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            skipped = []
            try:
                if ext == ".dxf":
                    added, warnings = self._scan_dxf(path)
                elif ext == ".json":
                    added, warnings = self._scan_json(path), []
                elif ext in (".fcstd", ".step", ".stp", ".iges", ".igs"):
                    added, unresolved, skipped = freecad_bridge.import_fcstd(
                        [path], kfactor=kfactor, tolerance=tolerance, min_area=min_area
                    )
                    warnings = [f"{u['name']}: {u['reason']}" for u in unresolved]
                else:
                    logs.append(f"[warn] unsupported file type, skipped: {path}")
                    continue
            except Exception as e:
                logs.append(f"[error] {os.path.basename(path)}: {e}")
                continue
            logs.extend(f"[warn] {os.path.basename(path)}: {w}" for w in warnings)
            logs.extend(f"[skip] {os.path.basename(path)}: {s['name']} "
                        f"(net area {s['area']:.2f} mm², excluded as tooling/fixture)" for s in skipped)
            self._extracted.update(added)
            logs.append(f"{os.path.basename(path)}: added {len(added)} part(s).")

        logs.append(f"Total: {len(self._extracted)} part(s).")
        return dict(self._extracted), logs

    def _scan_dxf(self, path):
        import dxf_extract

        parts, warnings = dxf_extract.extract_parts(path)
        base = os.path.splitext(os.path.basename(path))[0]
        extracted = {}
        for i, p in enumerate(parts):
            # Same naming convention as dxf_extract.py's own CLI (main()):
            # named after its DXF layer when every entity forming it agrees
            # on one non-default layer, else "<file>-partN".
            candidate_layers = p["layers"] - {"0"}
            name = next(iter(candidate_layers)) if len(candidate_layers) == 1 else (
                f"{base}-part{i + 1}" if len(parts) > 1 else base
            )
            original, suffix = name, 2
            while name in self._extracted or name in extracted:
                name = f"{original}-{suffix}"
                suffix += 1
            extracted[name] = {"outer": p["outer"], "holes": p["holes"], "thickness": None, "method": "dxf"}
        return extracted, warnings

    def _scan_json(self, path):
        with open(path) as f:
            data = json.load(f)
        extracted = {}
        for p in data["parts"]:
            extracted[p["name"]] = {
                "outer": p["points"],
                "holes": p["holes"],
                "thickness": p.get("thickness"),
                "method": p.get("method", "json"),
                "quantity": p.get("quantity", 1),
                "material": p.get("material"),
            }
        return extracted
