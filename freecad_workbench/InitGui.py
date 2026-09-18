"""
InitGui.py
----------
FreeCAD discovers a workbench by finding this file (InitGui.py) directly
inside a folder under Mod/ -- specifically App.getUserAppDataDir()+"Mod"
(which, confusingly, already includes the "v1-x" version segment -- e.g.
~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod on this flatpak
install, NOT the version-less .../FreeCAD/Mod one level up). This folder is
meant to be symlinked there under the name "FreeCADNesting" -- see
README.md's "Workbench UI" section for the one-line setup command -- so the
actual source stays in the project repo, not copied into FreeCAD's Mod
directory.

FreeCAD's loader runs this file via exec(compile(...)), NOT import, so
`__file__` is not defined here -- can't use the usual
os.path.dirname(__file__) trick to find our own directory. Instead, look
ourselves up by name in FreeCAD.__ModDirs__, which the loader populates
(from that same Mod-directory scan) before running any InitGui.py.

The nesting engine (nester.py, geometry.py, dxf_writer.py) and the
extraction script (freecad_extract.py, part_import.py) all live one level
up, in the project root -- add that to sys.path so this workbench's command
modules can import them directly, no vendoring/duplication.

nester.py hard-requires `pyclipper` (see nfp.py), which isn't part of
FreeCAD's bundled Python -- it needs vendoring into <project root>/vendor
the same way networkx is vendored for the SheetMetal workbench (see
README.md). That vendor dir has to land on sys.path HERE, at workbench-
load time, not later inside nesting_panel.py's own setup code: this file's
Initialize() imports nesting_command, whose Activated() imports
nesting_panel, whose own top-level `import nester` chain (nester -> nfp ->
pyclipper) would already have failed by the time any of that module's own
sys.path setup got a chance to run.
"""

import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

_WB_FOLDER_NAME = "FreeCADNesting"
_WB_DIR = None
for _d in getattr(App, "__ModDirs__", []):
    if os.path.basename(os.path.normpath(_d)) == _WB_FOLDER_NAME:
        _WB_DIR = os.path.realpath(_d)
        break
if _WB_DIR is None:
    _f = globals().get("__file__")
    if _f:
        _WB_DIR = os.path.dirname(os.path.realpath(_f))
    else:
        raise RuntimeError(
            f"NestingWorkbench: could not find a Mod/ entry named "
            f"'{_WB_FOLDER_NAME}' in FreeCAD.__ModDirs__ -- is the symlink "
            f"named exactly that? See README.md's 'Workbench UI' section."
        )

_PROJECT_ROOT = os.path.dirname(_WB_DIR)
_VENDOR_DIR = os.path.join(_PROJECT_ROOT, "vendor")
for _p in (_VENDOR_DIR, _PROJECT_ROOT, _WB_DIR):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


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
