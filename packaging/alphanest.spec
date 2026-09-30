# PyInstaller spec for the standalone AlphaNest app. Build with
# `python packaging/build.py` (renders the icon first), not by hand.
#
# Output (in dist/):
#   Windows -> AlphaNest.exe     one file, no install, no Python needed
#   Linux   -> AlphaNest         one file (chmod +x and run)
#   macOS   -> AlphaNest.app     an app bundle (one-file .app is deprecated
#                                in PyInstaller and slow to launch)
#
# Data files keep their repo-relative layout inside the bundle, so the
# `os.path.dirname(__file__)` lookups in theme.py and freecad_bridge.py
# resolve unchanged. FreeCAD itself is NOT bundled: .FCStd/.STEP/.IGES
# import still needs a FreeCAD install (see freecad_bridge.py); DXF and
# parts.json import work without it.

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
BUILD_DIR = os.path.join(ROOT, "build")

datas = [
    (os.path.join(ROOT, "alphanest-icon-pack"), "alphanest-icon-pack"),
    (os.path.join(ROOT, "native_app", "assets"), os.path.join("native_app", "assets")),
    # Run by FreeCADCmd's own Python, never imported by the app.
    (os.path.join(ROOT, "freecad_extract.py"), "."),
]
# SheetMetal's unfolder needs networkx in FreeCAD's Python; freecad_extract.py
# falls back to vendor/ for it (see its _default_vendor_dir()).
for name in ("networkx", "networkx-3.6.1.dist-info"):
    path = os.path.join(ROOT, "vendor", name)
    if os.path.isdir(path):
        datas.append((path, os.path.join("vendor", name)))

icon = None
for candidate in ("icon.ico", "icon.icns", "icon.png"):
    path = os.path.join(BUILD_DIR, candidate)
    if os.path.isfile(path):
        icon = path
        break

a = Analysis(
    [os.path.join(ROOT, "native_app", "main.py")],
    pathex=[ROOT],
    datas=datas,
    # Imported inside functions, or only on some code paths.
    hiddenimports=["dxf_extract", "part_import", "stock_solver", "ezdxf", "openpyxl"],
    # vendor/ is for FreeCAD's Python only -- the app uses the pip-installed
    # pyclipper. matplotlib is an optional extra of demo.py, not the app.
    excludes=["tkinter", "matplotlib", "pytest", "FreeCAD", "FreeCADGui", "PySide", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AlphaNest",
              console=False, icon=icon)
    coll = COLLECT(exe, a.binaries, a.datas, name="AlphaNest")
    app = BUNDLE(coll, name="AlphaNest.app", icon=icon,
                 bundle_identifier="com.alphanest.app",
                 info_plist={"NSHighResolutionCapable": True})
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="AlphaNest",
              console=False, icon=icon, upx=False)
