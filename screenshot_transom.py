import FreeCAD as App
import FreeCADGui as Gui
import time

App.openDocument("/home/daksshin/projects/freecad-nesting/examples/transom_assembly.FCStd")
time.sleep(0.5)
gdoc = Gui.activeDocument()
print("gdoc:", gdoc)
view = gdoc.activeView()
print("view:", view)

for name, fn in [("iso", "viewIsometric"), ("top", "viewTop"), ("front", "viewFront")]:
    getattr(view, fn)()
    view.fitAll()
    time.sleep(0.5)
    path = f"/home/daksshin/projects/freecad-nesting/examples/transom_{name}.png"
    view.saveImage(path, 1600, 1200, "White")
    print("saved", path)

print("DONE_SCREENSHOTS")
import os
os._exit(0)
