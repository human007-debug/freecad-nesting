"""
Build the ICF "TRANSOM COMPLETE FOR AIR SPRING SUSPENSION BOGIE"
(dwg DC/EMU M/ASR-0-3-003, alt n, 08/2024) as a FreeCAD assembly of
SheetMetal-workbench parts, for use with the freecad-nesting toolchain.

Run under FreeCAD's own Python:
    flatpak run --command=FreeCADCmd org.freecad.FreeCAD build_transom.py

Source: ICF drawing package 5377915.pdf (50-page bundle: this transom's
detail-part drawings + a Nose Suspension Bracket sub-assembly + IRS/ICF
material & weld standards). Component drawings used:
    Channel        AAA03500   10x467x2470  (bent, U section)
    Top Flange     AAA03501   16x210x1494  (flat, EMU/M-0-3-032 geometry)
    Rib type A     AAA03502   10x142x168   (flat, qty 5)
    Rib type B     AAA03503   10x142x162   (flat, qty 4)
    Side Plate RH  AAA03678   16x169x400   (flat, corner-shaped)
    Side Plate LH  AAA03679   16x169x400   (mirror of RH)
    Lug            EMU/M-0-3-041, item "LUG" (flat, qty 2; the assembly's
                   bought-out Nylon Bush bush is NOT modelled)

Simplifications (deliberate, and disclosed to the user):
  - Weld-edge bevel preps (the 35 degree / N11 edge chamfers called out on
    the Top Flange's round end, the Side Plates' short edge, etc.) are
    secondary post-cut edge treatments -- they don't change the profile a
    plasma/laser head cuts, so they are omitted from the solids.
  - The Channel's small end-of-flange step relief is modelled using the
    two dimensions on the drawing we could read with full confidence
    (2470 overall, 1668 full-height mid span -> 401 mm reduced-height
    zone each end), rather than the harder-to-read 50 mm sub-transition.
  - The Side Plates' small 8 mm return lip (implied by one section view)
    is omitted -- treated as a secondary press operation after the flat
    blank is cut, so it does not change the nested flat pattern.
  - Rib (9 total: 5x AAA03502 + 4x AAA03503) and Lug (2x) positions along
    the 2470 mm channel are an evenly-spaced, engineering-reasonable
    layout: the source assembly drawing's own rib/lug spacing dimensions
    could not be read with full confidence from the scan, and exact 3D
    position does not affect the 2D nested flat pattern of any part.
"""

import math
import os
import sys

import FreeCAD as App
import Part

SM_DIR = "/home/daksshin/.var/app/org.freecad.FreeCAD/data/FreeCAD/Mod/SheetMetal"
if SM_DIR not in sys.path:
    sys.path.insert(0, SM_DIR)

import SheetMetalBaseCmd as SMBase

OUT_DIR = "/home/daksshin/projects/freecad-nesting/examples"
OUT_FILE = os.path.join(OUT_DIR, "transom_assembly.FCStd")

doc = App.newDocument("TransomAssembly")


def make_sketch(name, plane_placement, wire_points, closed):
    """Create a Sketcher::SketchObject-free plain sketch via Part.Wire held
    in a Part::Feature, since SMBaseBend only needs .Shape (a Wire) and
    .getGlobalPlacement() on the object it's given -- it doesn't require a
    real Sketcher object."""
    pts = [App.Vector(*p, 0) for p in wire_points]
    if closed and pts[0] != pts[-1]:
        pts.append(pts[0])
    edges = [Part.LineSegment(pts[i], pts[i + 1]).toShape() for i in range(len(pts) - 1)]
    wire = Part.Wire(edges)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = wire
    obj.Placement = plane_placement
    return obj


def base_bend(name, sketch_obj, thickness, length, radius, side="Inside"):
    obj = doc.addObject("Part::FeaturePython", name)
    SMBase.SMBaseBend(obj, sketch_obj)
    obj.Thickness = thickness
    obj.Length = length
    obj.Radius = radius
    obj.BendSide = side
    sketch_obj.Visibility = False
    doc.recompute()
    return obj


def flat_plate(name, outline_pts, thickness, placement=None):
    """Flat plate as an SMBaseBend fed a CLOSED wire -- smBase() takes the
    'closed wire' branch and just makes-face + extrudes by `thickness`,
    which is exactly a flat sheet-metal blank (no bends)."""
    plc = placement if placement is not None else App.Placement()
    sk = make_sketch(name + "_Sketch", plc, outline_pts, closed=True)
    return base_bend(name, sk, thickness=thickness, length=10.0, radius=1.0, side="Inside")


def rounded_bar_outline(length, width, both_ends_round, n=24):
    """Flat-bar outline: rectangle of `length` x `width`, with one or both
    ends replaced by a semicircular cap of radius width/2 (matches the Top
    Flange / obround Rib-style ends seen throughout this drawing set).
    CCW point order; no duplicated points between straight runs and the
    arc that follows them (each arc's own first sample supplies that
    vertex)."""
    r = width / 2.0
    pts = []
    if both_ends_round:
        straight = length - width
        x0 = -straight / 2.0
        x1 = straight / 2.0
        # right semicircle: bottom -> right -> top, centered (x1, 0)
        for i in range(n + 1):
            a = -math.pi / 2 + math.pi * i / n
            pts.append((x1 + r * math.cos(a), r * math.sin(a)))
        # left semicircle: top -> left -> bottom, centered (x0, 0)
        for i in range(n + 1):
            a = math.pi / 2 + math.pi * i / n
            pts.append((x0 + r * math.cos(a), r * math.sin(a)))
    else:
        x0 = -length / 2.0
        x1 = length / 2.0
        pts.append((x0, r))
        pts.append((x0, -r))
        # right semicircle: bottom -> right -> top, centered (x1, 0)
        for i in range(n + 1):
            a = -math.pi / 2 + math.pi * i / n
            pts.append((x1 + r * math.cos(a), r * math.sin(a)))
    return pts


def chamfered_rect(w, h, chamfer, corners=("bl",)):
    """Axis-aligned rectangle centered at origin (w along X, h along Y)
    with a straight 45-degree chamfer cut on the requested corners.
    Points are emitted in CCW order bl -> br -> tr -> tl; for a chamfered
    corner, (entry point on incoming edge, exit point on outgoing edge)
    are emitted instead of the sharp corner."""
    x0, x1 = -w / 2.0, w / 2.0
    y0, y1 = -h / 2.0, h / 2.0
    cw, ch = chamfer

    # each entry: corner key -> (entry_point, exit_point, sharp_point)
    chamfer_pts = {
        "bl": ((x0, y0 + ch), (x0 + cw, y0), (x0, y0)),
        "br": ((x1 - cw, y0), (x1, y0 + ch), (x1, y0)),
        "tr": ((x1, y1 - ch), (x1 - cw, y1), (x1, y1)),
        "tl": ((x0 + cw, y1), (x0, y1 - ch), (x0, y1)),
    }
    result = []
    for key in ("bl", "br", "tr", "tl"):
        entry, exit_, sharp = chamfer_pts[key]
        if key in corners:
            result.append(entry)
            result.append(exit_)
        else:
            result.append(sharp)
    return result


def lug_outline():
    """60 wide x 55 tall, both BOTTOM corners chamfered ~10x10 (page
    EMU/M-0-3-041)."""
    return chamfered_rect(60.0, 55.0, (10.0, 10.0), corners=("bl", "br"))


def side_plate_outline(mirror=False):
    """400 long x 169 tall (AAA03678/79): left end has a small top chamfer
    (12x20) and a bottom R20 round; right end is a full semicircular
    round (radius = W/2). Built CCW, starting right after the bottom-left
    R20 fillet; each arc supplies its own first sample so no point is
    ever duplicated between a straight run and the arc following it."""
    L, W = 400.0, 169.0
    x0, x1 = -L / 2.0, L / 2.0
    y0, y1 = -W / 2.0, W / 2.0
    cw, ch = 12.0, 20.0
    r_bl = 20.0
    rr = W / 2.0  # right-end cap radius

    pts = []
    # bottom-left R20 fillet: from left edge (x0, y0+r_bl) sweeping to
    # bottom edge (x0+r_bl, y0), center (x0+r_bl, y0+r_bl), angle 180->270
    n_fillet = 8
    c = (x0 + r_bl, y0 + r_bl)
    for i in range(n_fillet + 1):
        a = math.pi + (math.pi / 2) * i / n_fillet
        pts.append((c[0] + r_bl * math.cos(a), c[1] + r_bl * math.sin(a)))
    # bottom edge -> start of right semicircular cap
    # right cap: bottom -> right -> top, centered (x1 - rr, 0)
    n_cap = 24
    ccx = x1 - rr
    for i in range(n_cap + 1):
        a = -math.pi / 2 + math.pi * i / n_cap
        pts.append((ccx + rr * math.cos(a), rr * math.sin(a)))
    # top edge runs to the top-left chamfer's entry point, then the
    # chamfer's exit point sits on the left edge
    pts.append((x0 + cw, y1))
    pts.append((x0, y1 - ch))
    # left edge closes back to the bl-fillet's start (implicit close)

    if mirror:
        pts = [(-x, y) for (x, y) in pts]
        pts.reverse()
    return pts


def rib_outline(w, h, chamfer=(10.0, 10.0)):
    return chamfered_rect(w, h, chamfer, corners=("bl",))


# ---------------------------------------------------------------- CHANNEL
CH_LEN = 2470.0
CH_OUTER_W = 190.0
CH_WALL_H = 155.0
CH_THK = 10.0
CH_RADIUS = 10.0
CH_MID_SPAN = 1668.0
CH_END_ZONE = (CH_LEN - CH_MID_SPAN) / 2.0  # 401 mm
CH_STUB_H = 6.0

# SMBaseBend's smBase() extrudes MainObject.Shape (kept in LOCAL
# coordinates) along a vector built purely from the sketch object's
# Placement.Rotation applied to local +Z -- it does NOT apply the
# sketch's own translation, and it does not pre-apply Placement to the
# Shape either. So: build the U cross-section (top-of-left-wall -> base
# -> top-of-right-wall) as a flat wire with local Y<->width, local
# X<->height... no: simplest robust option is to give the wire local
# coordinates equal to the *desired* final (Y, Z) of the cross-section,
# lying in the local Z=0 plane, then rotate the sketch object so local
# +Z (the extrude direction) maps to global +X (the channel's long
# axis) -- a 120 degree rotation about (1,1,1) cycles local (X,Y,Z) to
# global (Y,Z,X), which is exactly that.
half_w = CH_OUTER_W / 2.0
ch_sketch = doc.addObject("Part::Feature", "Channel_Section")
p0 = App.Vector(-half_w, CH_WALL_H, 0)
p1 = App.Vector(-half_w, 0.0, 0)
p2 = App.Vector(half_w, 0.0, 0)
p3 = App.Vector(half_w, CH_WALL_H, 0)
ch_edges = [Part.LineSegment(p0, p1).toShape(), Part.LineSegment(p1, p2).toShape(), Part.LineSegment(p2, p3).toShape()]
ch_sketch.Shape = Part.Wire(ch_edges)
ch_sketch.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(1, 1, 1), 120))
ch_sketch.Visibility = False

channel_bent = base_bend("Channel_Bent", ch_sketch, thickness=CH_THK, length=CH_LEN, radius=CH_RADIUS, side="Inside")
# freecad_extract.py's part "name" comes from the *leaf* SheetMetal
# object's Label (the last SheetMetal-module object before any plain
# Part::Cut chain), not the final followed shape -- label it here.
channel_bent.Label = "Channel (AAA03500) x1"
print("Channel_Bent bbox (local, before final placement):", channel_bent.Shape.BoundBox)
# The extrusion ran from local X=0 to X=CH_LEN; shift so the channel is
# centered on the assembly origin along its length.
channel_bent.Placement = App.Placement(App.Vector(-CH_LEN / 2.0, 0, 0), App.Rotation())

# End-relief cuts: drop wall height from CH_WALL_H to CH_STUB_H over the
# outer CH_END_ZONE at both ends (both walls), leaving the base untouched.
def end_relief_box(x_center, x_len):
    box = doc.addObject("Part::Box", "ChannelRelief")
    box.Length = x_len
    box.Width = CH_OUTER_W + 20
    box.Height = CH_WALL_H - CH_STUB_H + 1
    box.Placement = App.Placement(
        App.Vector(x_center - x_len / 2.0, -(CH_OUTER_W + 20) / 2.0, CH_STUB_H),
        App.Rotation())
    return box

relief1 = end_relief_box(-CH_LEN / 2.0 + CH_END_ZONE / 2.0, CH_END_ZONE)
relief2 = end_relief_box(CH_LEN / 2.0 - CH_END_ZONE / 2.0, CH_END_ZONE)
# but only cut the WALLS, not the base plate area between them -- restrict
# box to the wall thickness bands only, by cutting from a shell rather
# than full width; simplest robust approach: intersect relief with a
# "walls only" region using two side boxes instead of one full-width box.
doc.removeObject(relief1.Name)
doc.removeObject(relief2.Name)

def wall_relief_boxes(x_center, x_len):
    boxes = []
    for side_sign in (-1, 1):
        b = doc.addObject("Part::Box", "ChannelWallRelief")
        b.Length = x_len
        b.Width = CH_THK + 4
        b.Height = CH_WALL_H - CH_STUB_H + 1
        y = side_sign * half_w - (CH_THK + 4) / 2.0 * (1 if side_sign > 0 else 1)
        y = (half_w - CH_THK / 2.0) * side_sign - (CH_THK + 4) / 2.0
        b.Placement = App.Placement(
            App.Vector(x_center - x_len / 2.0, y, CH_STUB_H),
            App.Rotation())
        boxes.append(b)
    return boxes

relief_boxes = []
relief_boxes += wall_relief_boxes(-CH_LEN / 2.0 + CH_END_ZONE / 2.0, CH_END_ZONE + 1)
relief_boxes += wall_relief_boxes(CH_LEN / 2.0 - CH_END_ZONE / 2.0, CH_END_ZONE + 1)

channel_final = doc.addObject("Part::Cut", "Channel")
channel_final.Base = channel_bent
channel_final.Tool = relief_boxes[0]
doc.recompute()
cur = channel_final
for b in relief_boxes[1:]:
    nxt = doc.addObject("Part::Cut", "Channel_Cut")
    nxt.Base = cur
    nxt.Tool = b
    doc.recompute()
    cur = nxt
CHANNEL = cur

# ------------------------------------------------------------- TOP FLANGE
TF_LEN, TF_W, TF_THK = 1494.0, 210.0, 16.0
tf_outline = rounded_bar_outline(TF_LEN, TF_W, both_ends_round=True)
TOPFLANGE = flat_plate("TopFlange", tf_outline, TF_THK)
TOPFLANGE.Label = "Top Flange (AAA03501) x1"
z_wall_top = CH_WALL_H
TOPFLANGE.Placement = App.Placement(App.Vector(0, 0, z_wall_top), App.Rotation())

# ------------------------------------------------------------------ RIBS
RIB_A_W, RIB_A_H, RIB_THK = 168.0, 142.0, 10.0
RIB_B_W, RIB_B_H = 162.0, 142.0
rib_a_outline = rib_outline(RIB_A_W, RIB_A_H)
rib_b_outline = rib_outline(RIB_B_W, RIB_B_H)

RIB_A_QTY, RIB_B_QTY = 5, 4
rib_objs = []
n_ribs = RIB_A_QTY + RIB_B_QTY
usable = CH_MID_SPAN - 100.0
spacing = usable / (n_ribs + 1)
xs = [-usable / 2.0 + spacing * (i + 1) for i in range(n_ribs)]
kinds = (["A"] * RIB_A_QTY + ["B"] * RIB_B_QTY)
# interleave so ribs alternate along the length instead of grouping
interleaved = []
ai, bi = 0, 0
for i in range(n_ribs):
    if i % 2 == 0 and ai < RIB_A_QTY or bi >= RIB_B_QTY:
        interleaved.append("A"); ai += 1
    else:
        interleaved.append("B"); bi += 1

for i, (x, kind) in enumerate(zip(xs, interleaved)):
    outline = rib_a_outline if kind == "A" else rib_b_outline
    h = RIB_A_H if kind == "A" else RIB_B_H
    w = RIB_A_W if kind == "A" else RIB_B_W
    name = f"Rib_{kind}_{i+1}"
    rib = flat_plate(name, outline, RIB_THK)
    rib.Label = f"Rib {'AAA03502' if kind=='A' else 'AAA03503'} #{i+1}"
    # stand the rib upright: sketch-local X(=W) -> global Y, sketch-local
    # Y(=H) -> global Z, sketch-local Z(=thickness) -> global X (same
    # cyclic mapping used for the channel's own cross-section sketch)
    rot = App.Rotation(App.Vector(1, 1, 1), 120)
    rib.Placement = App.Placement(App.Vector(x, 0, h / 2.0), rot)
    rib_objs.append(rib)

# ------------------------------------------------------------------- LUG
# 60x55 plate, both bottom corners chamfered 10x10, one Phi15 hole on
# the vertical centerline, 35 mm down from the top edge (20 mm up from
# the bottom edge) -- EMU/M-0-3-041.
lug_outline_pts = lug_outline()
LUG_HOLE_DIA = 15.0
LUG_HOLE_Y_FROM_TOP = 35.0  # -> 55/2 - 35 = -7.5 from center
LUG_QTY = 2
lug_objs = []
for i, x in enumerate((-700.0, 700.0)):
    plate = flat_plate(f"Lug_{i+1}_Plate", lug_outline_pts, 10.0)
    plate.Label = f"Lug (EMU/M-0-3-041) #{i+1}"
    hole_cyl = doc.addObject("Part::Cylinder", f"Lug_{i+1}_Hole")
    hole_cyl.Radius = LUG_HOLE_DIA / 2.0
    hole_cyl.Height = 30.0
    hole_cyl.Placement = App.Placement(
        App.Vector(0, 55.0 / 2.0 - LUG_HOLE_Y_FROM_TOP, -10.0), App.Rotation())
    lug = doc.addObject("Part::Cut", f"Lug_{i+1}")
    lug.Base = plate
    lug.Tool = hole_cyl
    doc.recompute()
    lug.Label = f"Lug (EMU/M-0-3-041) #{i+1}"
    rot = App.Rotation(App.Vector(1, 0, 0), 180)
    lug.Placement = App.Placement(App.Vector(x, half_w + 5.0, 20.0), rot)
    lug_objs.append(lug)

# -------------------------------------------------------------- SIDE PLATES
sp_rh_outline = side_plate_outline(mirror=False)
sp_lh_outline = side_plate_outline(mirror=True)
SIDE_RH = flat_plate("SidePlate_RH", sp_rh_outline, 16.0)
SIDE_RH.Label = "Side Plate RH (AAA03678) x1"
SIDE_LH = flat_plate("SidePlate_LH", sp_lh_outline, 16.0)
SIDE_LH.Label = "Side Plate LH (AAA03679) x1"

sp_z = CH_WALL_H / 2.0
SIDE_RH.Placement = App.Placement(
    App.Vector(-CH_LEN / 2.0 + CH_END_ZONE / 2.0, half_w + CH_THK / 2.0 + 8.0, sp_z),
    App.Rotation(App.Vector(1, 0, 0), 90))
SIDE_LH.Placement = App.Placement(
    App.Vector(CH_LEN / 2.0 - CH_END_ZONE / 2.0, -half_w - CH_THK / 2.0 - 8.0, sp_z),
    App.Rotation(App.Vector(1, 0, 0), 90))

doc.recompute()

# NOTE: deliberately NOT grouping the finished parts into an
# App::DocumentObjectGroup -- Group objects have no .Placement, and
# freecad_extract.py's SheetMetal unfolder walks each part's
# getGlobalPlacement() during unfold, which raises AttributeError the
# moment a part sits inside a Group. Keep parts ungrouped at the
# document root so the nesting extractor's unfolder works.
os.makedirs(OUT_DIR, exist_ok=True)
doc.saveAs(OUT_FILE)
print("Saved:", OUT_FILE)
print("Objects:", [o.Name for o in doc.Objects])
