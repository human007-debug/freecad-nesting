import FreeCAD as App
import FreeCADGui as Gui
import time

App.openDocument("/home/daksshin/projects/freecad-nesting/examples/top_flange.FCStd")
time.sleep(1.5)
doc = App.ActiveDocument
for o in doc.Objects:
    print("obj:", o.Name, "shape valid:", not o.Shape.isNull(), "bbox:", o.Shape.BoundBox)
    o.ViewObject.Visibility = True
doc.recompute()
Gui.updateGui()
gdoc = Gui.activeDocument()
view = gdoc.activeView()
view.viewIsometric()
view.fitAll()
time.sleep(1.0)

for name, fn in [("iso", "viewIsometric"), ("top", "viewTop"), ("front", "viewFront")]:
    getattr(view, fn)()
    view.fitAll()
    Gui.updateGui()
    time.sleep(1.0)
    path = f"/home/daksshin/projects/freecad-nesting/examples/top_flange_{name}.png"
    view.saveImage(path, 1600, 1000, "White")
    print("saved", path)

print("DONE_SCREENSHOTS")
import os
os._exit(0)
