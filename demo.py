import math
import os
import time

from nester import Part, Nester
from dxf_writer import write_dxf

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def l_bracket(w, h, t):
    """Concave L-shaped bracket, w x h overall, thickness t."""
    return [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]


def hexagon(r):
    return [(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)))
            for i in range(6)]


def t_shape(w, h, stem_w, stem_h):
    """Concave T-shape, traced as a single closed perimeter."""
    top_h = h - stem_h
    sx = (w - stem_w) / 2
    return [
        (sx, 0), (sx + stem_w, 0), (sx + stem_w, top_h),
        (w, top_h), (w, h), (0, h), (0, top_h), (sx, top_h),
    ]


def star(r_out, r_in, points=5):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi / points * i - math.pi / 2
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def notched_rect(w, h, notch=15):
    """Rectangle with a triangular notch cut into one long edge (concave)."""
    return [(0, 0), (w / 2 - notch, 0), (w / 2, notch), (w / 2 + notch, 0),
            (w, 0), (w, h), (0, h)]


def rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def circle(cx, cy, r, n=16):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


SHEET_W, SHEET_H = 1220.0, 2440.0  # a standard 4x8 ft sheet, in mm

parts = [
    Part("L-Bracket-A", l_bracket(220, 180, 45), quantity=6),
    Part("Hexagon-Plate", hexagon(70), quantity=8, rotations=[0, 30, 60, 90, 120, 150]),
    Part("T-Bracket", t_shape(200, 160, 60, 110), quantity=5),
    Part("Star-Deco", star(90, 40, 6), quantity=4, rotations=[0, 60, 120, 180, 240, 300]),
    Part("Notched-Plate", notched_rect(260, 120), quantity=6),
    Part("Small-Square", [(0, 0), (60, 0), (60, 60), (0, 60)], quantity=10),
    # NEW: parts with holes, to exercise Step 1
    Part("Mount-Plate", rect(180, 120),
         holes=[circle(40, 40, 12), circle(140, 40, 12), circle(90, 90, 12)],
         quantity=4),
    Part("Washer-Plate", rect(100, 100), holes=[circle(50, 50, 30)], quantity=6),
]

nester = Nester(SHEET_W, SHEET_H, kerf=3.0)
for p in parts:
    nester.add_part(p)

t0 = time.time()
sheets, unplaced = nester.run()
elapsed = time.time() - t0

total_parts = sum(len(s) for s in sheets)
print(f"Sheets used: {len(sheets)}")
print(f"Total parts placed: {total_parts}")
print(f"Unplaced parts: {unplaced if unplaced else 'none'}")
print(f"Nesting time: {elapsed:.2f}s")

for i, sheet in enumerate(sheets):
    total_part_area = sum(pp.net_area() for pp in sheet)
    utilization = 100.0 * total_part_area / (SHEET_W * SHEET_H)
    print(f"  Sheet {i+1}: {len(sheet)} parts, {utilization:.1f}% material utilization")

# --- export one DXF per sheet -------------------------------------------------
for i, sheet in enumerate(sheets):
    polys = [(pp.name.replace(" ", "_"), pp.points, pp.holes) for pp in sheet]
    write_dxf(os.path.join(OUT_DIR, f"sheet_{i+1}.dxf"), polys, SHEET_W, SHEET_H)

# --- render a PNG preview per sheet (holes rendered as true cut-through) -----
# Optional -- matplotlib isn't one of this project's dependencies (only
# pyclipper is, for the nesting engine itself), so skip cleanly if it's
# not installed rather than making it a hard requirement just for this demo.
try:
    import matplotlib.pyplot as plt
    import matplotlib.path as mpath
    import matplotlib.patches as mpatches

    colors = plt.cm.tab20.colors
    for i, sheet in enumerate(sheets):
        fig, ax = plt.subplots(figsize=(6, 12))
        ax.add_patch(plt.Rectangle((0, 0), SHEET_W, SHEET_H, fill=False, edgecolor="black", linewidth=1.5))
        for j, pp in enumerate(sheet):
            verts = list(pp.points) + [pp.points[0]]
            codes = [mpath.Path.MOVETO] + [mpath.Path.LINETO] * (len(pp.points) - 1) + [mpath.Path.CLOSEPOLY]
            for hole in pp.holes:
                hverts = list(hole) + [hole[0]]
                verts += hverts
                codes += [mpath.Path.MOVETO] + [mpath.Path.LINETO] * (len(hole) - 1) + [mpath.Path.CLOSEPOLY]
            path = mpath.Path(verts, codes)
            patch = mpatches.PathPatch(path, facecolor=colors[j % len(colors)],
                                        edgecolor="black", linewidth=0.8, alpha=0.9)
            ax.add_patch(patch)
            cx = sum(x for x, y in pp.points) / len(pp.points)
            cy = sum(y for x, y in pp.points) / len(pp.points)
            ax.text(cx, cy, pp.name.split("-")[0], ha="center", va="center", fontsize=6)
        ax.set_xlim(-20, SHEET_W + 20)
        ax.set_ylim(-20, SHEET_H + 20)
        ax.set_aspect("equal")
        ax.set_title(f"Sheet {i+1} ({len(sheet)} parts)")
        plt.tight_layout()
        plt.savefig(os.path.join(OUT_DIR, f"sheet_{i+1}_preview.png"), dpi=130)
        plt.close(fig)
except ImportError:
    print("(matplotlib not installed -- skipping PNG previews; DXF files were still written)")

print("Done.")
