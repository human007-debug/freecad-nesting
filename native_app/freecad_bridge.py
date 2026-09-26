"""
freecad_bridge.py
------------------
Lets the standalone native app import `.FCStd` (and now `.step`/`.stp`/
`.iges`/`.igs`) files without running inside FreeCAD itself, by shelling
out to `FreeCADCmd` and reusing `freecad_extract.py` exactly as documented
in the main README ("FreeCAD integration" -> "Running it") -- the extractor
already dispatches on file extension itself, so this bridge just forwards
whatever paths it's given, same as before.

STEP/IGES files carry no SheetMetal feature history (see
`freecad_extract.py`'s module docstring), so a bent part imported that way
can't be auto-unfolded -- the extractor reports those in a separate
`"unresolved"` list instead of guessing, which `import_fcstd()` now returns
alongside the usual extracted-parts dict so the caller can tell the user
which parts still need a flat-pattern DXF. A real assembly STEP also tends
to carry small incidental solids that were never meant to be cut (jig/
fixture geometry) -- pass `min_area` (mm², default 0/disabled) to have the
extractor exclude those instead of importing them as parts; they come back
in a third `"skipped"` list.

Requires a FreeCAD install: `find_freecad_cmd()` looks for it (the
ALPHANEST_FREECADCMD environment variable, FreeCADCmd on PATH, the standard
Windows/macOS install locations, then the Linux flatpak), with
`pyclipper`/`networkx` importable from FreeCAD's Python -- same prerequisite
the FreeCAD workbench itself already has (see README's "Install").

The scratch output file is created under the project directory rather than
the system temp dir: a flatpak-sandboxed FreeCAD generally can't see
arbitrary host paths under `/tmp` (confirmed by hitting exactly this --
`freecad_extract.py` ran and printed `[ok]` lines but then failed with
`No such file or directory` writing to a `tempfile.TemporaryDirectory()`
default location), the same class of sandboxing restriction as FreeCAD's
flatpak being unable to see this project's own Claude Code scratchpad
directory. The project directory itself is a path the sandbox can already
read/write (the workbench relies on this too), so scratch files go there
instead.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile


DEFAULT_FREECAD_CMD = ["flatpak", "run", "--command=FreeCADCmd", "org.freecad.FreeCAD"]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXTRACTOR = os.path.join(_PROJECT_ROOT, "freecad_extract.py")


def find_freecad_cmd():
    """The command prefix that runs FreeCAD's headless FreeCADCmd on this
    machine, or DEFAULT_FREECAD_CMD if nothing better is found (so the
    eventual error still names a real command)."""
    env = os.environ.get("ALPHANEST_FREECADCMD")
    if env:
        return [env]
    for name in ("FreeCADCmd", "freecadcmd"):
        found = shutil.which(name)
        if found:
            return [found]
    if sys.platform == "win32":
        roots = [os.environ.get(v) for v in ("ProgramFiles", "LOCALAPPDATA")]
        hits = []
        for root in filter(None, roots):
            hits += glob.glob(os.path.join(root, "FreeCAD*", "bin", "[Ff]ree[Cc][Aa][Dd][Cc]md.exe"))
            hits += glob.glob(os.path.join(root, "Programs", "FreeCAD*", "bin", "[Ff]ree[Cc][Aa][Dd][Cc]md.exe"))
        if hits:
            return [sorted(hits)[-1]]  # newest version folder sorts last
    if sys.platform == "darwin":
        for app in ("/Applications/FreeCAD.app", os.path.expanduser("~/Applications/FreeCAD.app")):
            for rel in ("Contents/Resources/bin/freecadcmd", "Contents/Resources/bin/FreeCADCmd",
                        "Contents/MacOS/FreeCADCmd"):
                if os.path.isfile(os.path.join(app, rel)):
                    return [os.path.join(app, rel)]
    return DEFAULT_FREECAD_CMD


def import_fcstd(paths, kfactor=0.4, tolerance=0.25, min_area=0.0, qty_overrides=None,
                  material_overrides=None, freecad_cmd=None, timeout=300):
    """Runs freecad_extract.py against `paths` via FreeCADCmd and returns
    `(extracted, unresolved, skipped)`: `extracted` is
    `{label: {"outer", "holes", "thickness", "method"}}` -- the same shape
    `nesting_widgets.NestingPanel._extracted` expects from any part source --
    `unresolved` is a list of `{"name", "source_file", "reason"}` for any
    STEP/IGES part that has bends but no SheetMetal history to unfold from,
    and `skipped` is a list of `{"name", "source_file", "area", "reason"}`
    for any plain flat part excluded by `min_area`.
    Raises RuntimeError (with the subprocess's stdout+stderr attached) on a
    nonzero exit or a missing output file, so the native app's log shows
    the real error (e.g. a ModuleNotFoundError for pyclipper/networkx)
    instead of it vanishing the way it would into FreeCAD's own Report View."""
    freecad_cmd = freecad_cmd or find_freecad_cmd()

    with tempfile.TemporaryDirectory(dir=_PROJECT_ROOT) as tmp_dir:
        out_path = os.path.join(tmp_dir, "parts.json")
        cmd = [*freecad_cmd, _EXTRACTOR, "--pass", *paths,
               f"output={out_path}", f"kfactor={kfactor}", f"tolerance={tolerance}", f"min_area={min_area}"]
        for label, n in (qty_overrides or {}).items():
            cmd.append(f"qty:{label}={n}")
        for label, name in (material_overrides or {}).items():
            cmd.append(f"material:{label}={name}")

        # CREATE_NO_WINDOW: FreeCADCmd.exe is a console program, and would
        # otherwise flash a terminal window over the app on every import.
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, creationflags=flags)
        if result.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(
                f"freecad_extract.py failed (exit {result.returncode}) for {', '.join(paths)}:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)

    extracted = {}
    for p in data["parts"]:
        extracted[p["name"]] = {
            "outer": p["points"],
            "holes": p["holes"],
            "thickness": p.get("thickness"),
            "method": p.get("method", "fcstd"),
            "quantity": p.get("quantity", 1),
            "material": p.get("material"),
        }
    unresolved = data.get("unresolved", [])
    skipped = data.get("skipped", [])
    return extracted, unresolved, skipped
