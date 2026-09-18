"""
remnant.py
----------
Finds the largest axis-aligned empty rectangle left on a sheet after
placing parts on it -- used by inventory.commit_job() to automatically
record a sheet's leftover offcut back into stock as a new remnant, instead
of only ever depleting stock (see inventory.py's module docstring for the
existing cascade-then-deplete logic this complements).

HONEST SIMPLIFICATION: a sheet's true leftover area is whatever's outside
the union of its (possibly concave, hole-bearing) placed parts -- an
arbitrary, possibly-disconnected polygon region. This project's stock
model is rectangles only (StockSheet has width/height, not an arbitrary
outline -- see inventory.py), so there would be nothing to *do* with an
exact polygon remnant anyway. Instead, this computes the single largest
empty axis-aligned rectangle among each part's bounding box (not its exact
outline) -- a real, professional-tool-standard simplification (SigmaNest's
own "remnant sheet" feature works the same way: a rectangular crop of
leftover material, since that's what's actually practical to store,
track, and cut from later). Using bounding boxes as obstacles rather than
exact part outlines is conservative, not incorrect: a box strictly contains
the real part shape, so a rectangle proven empty of every box is
guaranteed empty of the real parts too -- just possibly smaller than the
true achievable remnant.

ALGORITHM: coordinate-compress the sheet into a grid using every obstacle
bbox edge (plus the sheet's own bounds) as a grid line, mark each cell
free/blocked by whether its center falls inside an obstacle, then find the
largest all-free sub-rectangle via the standard "largest rectangle in a
binary matrix" technique (histogram method, applied one grid row at a
time) -- O(rows x cols), trivial for realistic part counts.
"""


def largest_empty_rect(sheet_w, sheet_h, obstacle_bboxes):
    """obstacle_bboxes: list of (xmin, ymin, xmax, ymax). Returns
    (x, y, w, h) of the largest empty axis-aligned rectangle not
    overlapping any obstacle bbox, or None if there's no free space at all
    (or the sheet has zero area)."""
    if sheet_w <= 0 or sheet_h <= 0:
        return None

    xs = sorted(set([0.0, sheet_w] + [b[0] for b in obstacle_bboxes] + [b[2] for b in obstacle_bboxes]))
    ys = sorted(set([0.0, sheet_h] + [b[1] for b in obstacle_bboxes] + [b[3] for b in obstacle_bboxes]))
    m, k = len(xs) - 1, len(ys) - 1  # grid is m columns (x) x k rows (y)
    if m <= 0 or k <= 0:
        return None

    free = [[True] * k for _ in range(m)]
    for bx0, by0, bx1, by1 in obstacle_bboxes:
        for i in range(m):
            cx = (xs[i] + xs[i + 1]) / 2
            if not (bx0 < cx < bx1):
                continue
            for j in range(k):
                cy = (ys[j] + ys[j + 1]) / 2
                if by0 < cy < by1:
                    free[i][j] = False

    best = None  # (area, x, y, w, h)
    heights = [0] * m  # consecutive free run (in the y direction) ending at the current row, per column
    for j in range(k):
        for i in range(m):
            heights[i] = heights[i] + 1 if free[i][j] else 0

        # Largest rectangle in this histogram (monotonic stack), tracking
        # real-world coordinates via xs[]/ys[] instead of cell counts.
        stack = []  # (start_column_index, height)
        for i in range(m + 1):
            h = heights[i] if i < m else 0
            start = i
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                if sh == 0:
                    continue
                real_w = xs[i] - xs[s]
                real_h = ys[j + 1] - ys[j - sh + 1]
                real_area = real_w * real_h
                if best is None or real_area > best[0]:
                    best = (real_area, xs[s], ys[j - sh + 1], real_w, real_h)
                start = s
            stack.append((start, h))

    if best is None:
        return None
    _area, x, y, w, h = best
    return (x, y, w, h)
