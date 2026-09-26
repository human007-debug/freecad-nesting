"""
part_import.py
---------------
Plain python3, no FreeCAD dependency. Loads the JSON written by
freecad_extract.py (run separately under FreeCADCmd) into nester.Part
objects.

    from part_import import load_parts
    nester = Nester(SHEET_W, SHEET_H, kerf=3.0)
    for part in load_parts("parts.json"):
        nester.add_part(part)
"""

import json

from nester import Part


def load_parts(json_path, rotations=None):
    """rotations: optional override list applied to every loaded part
    (freecad_extract.py doesn't know a part's allowed rotations -- that's
    a shop-floor decision, not something derivable from the CAD geometry)."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    parts = []
    for p in data["parts"]:
        kwargs = dict(
            name=p["name"],
            points=[tuple(pt) for pt in p["points"]],
            holes=[[tuple(pt) for pt in hole] for hole in p["holes"]],
            quantity=p.get("quantity", 1),
            material=p.get("material"),
            thickness=p.get("thickness"),
        )
        if rotations is not None:
            kwargs["rotations"] = rotations
        parts.append(Part(**kwargs))
    return parts
