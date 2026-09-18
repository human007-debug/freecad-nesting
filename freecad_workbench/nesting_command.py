import json
import os
import subprocess

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets


class NestingRunCommand:
    def GetResources(self):
        return {
            "MenuText": "Run Nesting...",
            "ToolTip": "Find SheetMetal-workbench parts in this document and nest them onto sheets",
        }

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "Nesting", "Open or create a document first."
            )
            return
        from nesting_panel import NestingDialog
        dlg = NestingDialog(doc, Gui.getMainWindow())
        dlg.exec_()

    def IsActive(self):
        return App.ActiveDocument is not None


class SendToAlphaNestCommand:
    """The FreeCAD -> AlphaNest half of the file bridge between the workbench
    and the standalone native app: extract this document's SheetMetal parts
    with the same in-process extraction the Run Nesting dialog uses, write
    them to the parts.json shape the native app imports, and -- if the
    project's run.sh is present -- launch that app with the file.

    This is a deliberate *file* handoff rather than live IPC between two
    running processes: the parts file is already the shared source of truth
    (freecad_extract.py's own CLI writes the same format), and a file has no
    connection state to manage when either app is closed or restarted.
    Nesting the results back INTO this FreeCAD document (drawing placed-part
    Draft wires into the sheet) is the other half of the round-trip and is
    not done yet.
    """

    def GetResources(self):
        return {
            "MenuText": "Send to AlphaNest...",
            "ToolTip": "Extract this document's SheetMetal parts into a parts.json and open them in the native AlphaNest app",
        }

    def Activated(self):
        doc = App.ActiveDocument
        if doc is None:
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "Nesting", "Open or create a document first."
            )
            return

        from nesting_panel import FreeCADDocSource
        source = FreeCADDocSource(doc)
        extracted, logs = source.scan(0.4, 0.25)
        if not extracted:
            tail = "\n".join(logs[-6:]) if logs else "No parts were found."
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(), "AlphaNest",
                f"No SheetMetal-workbench parts found in this document.\n\n{tail}",
            )
            return

        if doc.FileName:
            default = os.path.splitext(doc.FileName)[0] + ".parts.json"
        else:
            default = doc.Label + ".parts.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            Gui.getMainWindow(), "Send to AlphaNest", default, "parts.json (*.json)"
        )
        if not path:
            return

        parts = [{
            "name": label,
            "points": data["outer"],
            "holes": data["holes"],
            "thickness": data.get("thickness"),
            "method": data.get("method", "workbench"),
            "quantity": data.get("quantity", 1),
            "material": data.get("material"),
        } for label, data in extracted.items()]
        with open(path, "w") as f:
            json.dump({"parts": parts}, f, indent=2)

        run_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "native_app", "run.sh")
        if os.path.isfile(run_sh):
            try:
                subprocess.Popen(["bash", run_sh, path], start_new_session=True)
                return
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(
                    Gui.getMainWindow(), "AlphaNest",
                    f"Could not launch the native app ({e}); parts written to {path}.",
                )
                return
        QtWidgets.QMessageBox.information(
            Gui.getMainWindow(), "AlphaNest",
            f"Wrote {len(parts)} part(s) to {path}. Launch the AlphaNest app and it will open them automatically.",
        )

    def IsActive(self):
        return App.ActiveDocument is not None