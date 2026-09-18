"""
main.py
-------
Standalone entry point for the native nesting app -- no FreeCAD required.
`.FCStd` files are still importable via a FreeCADCmd subprocess bridge (see
freecad_bridge.py); DXF and parts.json are read directly.

Run with:
    pip install PySide6 pyclipper ezdxf
    python3 native_app/main.py

Optional trailing argument: a parts.json path (as written by the FreeCAD
workbench's "Send to AlphaNest..." command) to import automatically on
startup instead of Add Parts.

    python3 native_app/main.py parts.json
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # project root: nester/genetic/inventory/etc.

from PySide6 import QtWidgets

from native_app.main_window import MainWindow


def main(argv=None):
    opts = sys.argv[1:] if argv is None else list(argv)
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.resize(1360, 840)
    win.show()
    for path in opts:
        if os.path.splitext(path)[1].lower() not in (".json",):
            win.panel._log(f"[warn] unsupported launch file, skipped: {path}")
            continue
        try:
            extracted, logs = win.part_source.load_files([path])
        except Exception as e:  # noqa: BLE001
            win.panel._log(f"[error] {path}: {e}")
            continue
        for line in logs:
            win.panel._log(line)
        win.panel._load_parts_dict(extracted)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()