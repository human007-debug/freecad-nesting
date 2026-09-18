"""
Build the ICF "TOP FLANGE FOR TRANSOM" (dwg EMU/M-O-3-032, alt g, 12/2008)
as a FreeCAD solid, from page 1 of the source PDF 5377915.pdf.

Run headless with:
    flatpak run --command=FreeCADCmd org.freecad.FreeCAD build_top_flange.py

Drawing readout (confirmed against a 400dpi crop of the source page):
  - Flat rectangular bar, SHARP (square) corners all round -- the dashed
    circle near each end in the top/side-elevation view is a detail-view
    boundary balloon (leader to the label "X"), not a physical hole or a
    rounded end; the bar's own outline inside that circle is drawn dead
    straight right up to the end.
  - Two sizes are called out in the parts table, both 1494 mm long:
        Item 1: 16 x 210 x 1494   qty 4/coach   39.180 kg/unit
        Item 2: 16 x 192 x 1494   qty 4/coach   35.821 kg/unit
  - Detail AT-X (the enlarged view of that circled end region) shows the
    actual end treatment: the TOP face's leading edge is cut back on a
    35 degree bevel, down to a 2 mm root face (land) surviving at the
    very tip on the bottom face. It runs the full width of the bar, at
    BOTH ends (only one end is fully drawn; the break-line in the middle
    of both views is the standard "shortened, symmetric part" convention
    -- confirmed by weight: modelling this end bevel at both ends alone
    brings the computed mass to within ~0.3% of the drawing's stated
    39.180 kg/unit for Item 1, i.e. no rounded ends or edge bevels
    elsewhere are needed to explain the stated weight).
  - Material: IS:2062-2006 E250 Cu C (per alteration 'g', 12/2008 -- also
    the alteration that changed this same Detail AT-X from a J-prep to a
    V-prep edge preparation, i.e. this bevel is a weld-fit lead-in, not a
    deburring chamfer).
"""

import math
import os

import FreeCAD as App
import Part

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
OUT_FILE = os.path.join(OUT_DIR, "top_flange.FCStd")

doc = App.newDocument("TopFlange")

ROOT_FACE = 2.0       # mm, vertical land from the bottom face
BEVEL_ANGLE_DEG = 35.0


def end_bevel_cut(length, width, thickness, root_face, angle_deg, right_end):
    """Triangular-prism cutting tool for Detail AT-X's end bevel: removes
    the top face's leading-edge wedge at one end of the bar, running the
    full width, down to a `root_face`-tall vertical land surviving at the
    very tip. `right_end` selects x = +length/2 (True) or -length/2
    (False)."""
    run = (thickness - root_face) * math.tan(math.radians(angle_deg))
    tip_x = length / 2.0 if right_end else -length / 2.0
    setback_x = tip_x - run if right_end else tip_x + run
    margin = width  # generous overshoot along Y; cuts into air there

    v1 = App.Vector(setback_x, 0, thickness)
    v2 = App.Vector(tip_x, 0, thickness)
    v3 = App.Vector(tip_x, 0, root_face)
    tri_edges = [
        Part.LineSegment(v1, v2).toShape(),
        Part.LineSegment(v2, v3).toShape(),
        Part.LineSegment(v3, v1).toShape(),
    ]
    tri_face = Part.Face(Part.Wire(tri_edges))
    prism = tri_face.extrude(App.Vector(0, width + 2 * margin, 0))
    prism.translate(App.Vector(0, -width / 2.0 - margin, 0))
    return prism


def top_flange(name, label, length, width, thickness):
    solid = Part.makeBox(length, width, thickness,
                          App.Vector(-length / 2.0, -width / 2.0, 0))

    solid = solid.cut(end_bevel_cut(length, width, thickness, ROOT_FACE, BEVEL_ANGLE_DEG, right_end=True))
    solid = solid.cut(end_bevel_cut(length, width, thickness, ROOT_FACE, BEVEL_ANGLE_DEG, right_end=False))

    obj = doc.addObject("Part::Feature", name)
    obj.Shape = solid
    obj.Label = label
    return obj


item1 = top_flange("TopFlange_Item1", "Top Flange Item-1 (16x210x1494, EMU-M-O-3-032)",
                    length=1494.0, width=210.0, thickness=16.0)
item1.Placement = App.Placement(App.Vector(0, -150, 0), App.Rotation())

item2 = top_flange("TopFlange_Item2", "Top Flange Item-2 (16x192x1494, EMU-M-O-3-032)",
                    length=1494.0, width=192.0, thickness=16.0)
item2.Placement = App.Placement(App.Vector(0, 150, 0), App.Rotation())

doc.recompute()

os.makedirs(OUT_DIR, exist_ok=True)
doc.saveAs(OUT_FILE)
item1.Shape.exportStep(os.path.join(OUT_DIR, "top_flange_item1.step"))
item1.Shape.exportStl(os.path.join(OUT_DIR, "top_flange_item1.stl"))
item2.Shape.exportStep(os.path.join(OUT_DIR, "top_flange_item2.step"))
item2.Shape.exportStl(os.path.join(OUT_DIR, "top_flange_item2.stl"))

print("Saved:", OUT_FILE)
print("Item1 bbox:", item1.Shape.BoundBox)
print("Item2 bbox:", item2.Shape.BoundBox)
print("Item1 volume (mm^3):", item1.Shape.Volume, "-> mass @ 7850 kg/m^3 =", item1.Shape.Volume * 7850e-9, "kg")
print("Item2 volume (mm^3):", item2.Shape.Volume, "-> mass @ 7850 kg/m^3 =", item2.Shape.Volume * 7850e-9, "kg")
