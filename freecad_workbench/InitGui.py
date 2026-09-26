"""
InitGui.py
----------
FreeCAD discovers a workbench by finding InitGui.py inside a folder under
its Mod directory (App.getUserAppDataDir()+"Mod", which already includes the
"v1-x" version segment -- e.g. ~/.var/app/org.freecad.FreeCAD/data/FreeCAD/
v1-1/Mod on the flatpak). This workbench lives in a subfolder of the repo,
not at its root, so the WHOLE repo goes into Mod/ (a git clone, the Addon
Manager, or a symlink -- any folder name works): the repo-root package.xml
tells FreeCAD's loader to run freecad_workbench/InitGui.py from there. See
README.md's "Install" section.

FreeCAD's loader runs this file via exec(compile(source, path, "exec")), NOT
import, so `__file__` is not defined here. The path it passed to compile()
is still on this code object, though, so that's where we find our own
directory -- independent of what the Mod folder is called.

The nesting engine (nester.py, geometry.py, dxf_writer.py, ...) and the
extraction script (freecad_extract.py, part_import.py) all live one level
up, in the repo root -- add that to sys.path so this workbench's command
modules can import them directly, no vendoring/duplication.

nester.py hard-requires `pyclipper` (see nfp.py), which isn't part of
FreeCAD's bundled Python. A pyclipper installed into FreeCAD's Python (the
Addon Manager's AdditionalPythonPackages, or pip) always wins; <repo>/vendor
is only a fallback, APPENDED to sys.path so it can never shadow a working
install -- its compiled extension only matches Linux/Python 3.13 (the
FreeCAD 1.1 flatpak), and on any other platform it would fail to load even
when a correct copy was installed. This has to happen HERE, at workbench-
load time: Initialize() imports nesting_command, whose Activated() imports
nesting_panel, whose top-level `import nester` (-> nfp -> pyclipper) would
already have failed before any later sys.path setup got a chance to run.
"""

import inspect
import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

_WB_DIR = os.path.dirname(os.path.realpath(inspect.currentframe().f_code.co_filename))
_PROJECT_ROOT = os.path.dirname(_WB_DIR)
for _p in (_PROJECT_ROOT, _WB_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import pyclipper  # noqa: F401
except ImportError:
    _VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
    if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
        sys.path.append(_VENDOR_DIR)
    try:
        import pyclipper  # noqa: F401
    except ImportError:
        App.Console.PrintWarning(
            "Nesting workbench: the 'pyclipper' package is missing from "
            "FreeCAD's Python, so Run Nesting won't work yet. See the "
            "'Install' section of this workbench's README.md.\n"
        )


class NestingWorkbench(Gui.Workbench):
    MenuText = "Nesting"
    ToolTip = "Sheet-metal part nesting"
    # No custom Icon for now -- FreeCAD falls back to a default one. (A
    # hand-written XPM here previously had a header/row-count mismatch that
    # made Gui.addWorkbench() throw, which InitGui.py's loader swallows
    # silently -- the workbench just never appeared, with no visible error.)

    def Initialize(self):
        from nesting_command import NestingRunCommand, SendToAlphaNestCommand
        Gui.addCommand("Nesting_Run", NestingRunCommand())
        Gui.addCommand("Nesting_SendToAlphaNest", SendToAlphaNestCommand())
        self.appendToolbar("Nesting", ["Nesting_Run", "Nesting_SendToAlphaNest"])
        self.appendMenu("Nesting", ["Nesting_Run", "Nesting_SendToAlphaNest"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(NestingWorkbench())
