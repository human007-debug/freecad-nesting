import os
import time

from part_import import load_parts
from nester import Nester
from dxf_writer import write_dxf

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SHEET_W, SHEET_H = 1500.0, 3000.0  # mm, a plausible plate stock size

parts = load_parts(os.path.join(OUT_DIR, "examples", "transom_parts.json"),
                    rotations=[0, 90, 180, 270])

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

for i, sheet in enumerate(sheets):
    polys = [(pp.name.replace(" ", "_").replace("(", "").replace(")", ""), pp.points, pp.holes) for pp in sheet]
    write_dxf(os.path.join(OUT_DIR, "examples", f"transom_sheet_{i+1}.dxf"), polys, SHEET_W, SHEET_H)

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
        ax.set_xlim(-50, SHEET_W + 50)
        ax.set_ylim(-50, SHEET_H + 50)
        ax.set_aspect("equal")
        ax.set_title(f"Transom parts -- sheet {i+1} ({SHEET_W}x{SHEET_H}mm)")
        fig.savefig(os.path.join(OUT_DIR, "examples", f"transom_nest_sheet_{i+1}.png"), dpi=120)
        print("saved preview for sheet", i + 1)
except ImportError:
    print("matplotlib not installed -- skipping PNG preview (DXF still written)")
