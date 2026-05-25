"""Pin declutter: spread overlapping map pins apart while keeping them
close to their original positions.

Iterative pairwise repulsion. For each pair of pins within `min_dist` px
of each other, push them apart along the axis between them. Converges
quickly for the pin counts we have per region (low double digits to ~70).

Original world geography is preserved as the input order; declutter only
adjusts pixel positions to remove visual collision, so the dominant
spatial layout (a goblin priest in the north stays in the north relative
to other priests) survives.
"""

from __future__ import annotations

import math


def declutter_pins(pins: dict[str, dict[str, int]],
                   min_dist: int = 12,
                   max_iters: int = 80,
                   canvas_w: int | None = None,
                   canvas_h: int | None = None,
                   bounds: dict[str, tuple[int, int, int, int]] | None = None,
                   ) -> dict[str, dict[str, int]]:
    """Returns a new dict with adjusted x,y so no two pins are within
    `min_dist` of each other.

    `canvas_w`/`canvas_h`, if given, clamp pins to stay on-canvas after
    displacement.

    `bounds`, if given, is a per-pin rect override `{name: (x0, y0, x1, y1)}`
    that takes precedence over canvas-wide clamping. Use to keep a pin
    inside its source zone on multi-zone canvases so a push doesn't drift
    it across the zone separator.
    """
    items = [(name, float(c["x"]), float(c["y"])) for name, c in pins.items()]
    n = len(items)
    if n < 2:
        return {name: {"x": int(round(x)), "y": int(round(y)),
                       **{k: v for k, v in pins[name].items() if k not in ("x", "y")}}
                for name, x, y in items}
    extras = {name: {k: v for k, v in pins[name].items() if k not in ("x", "y")}
              for name, _, _ in items}

    for _ in range(max_iters):
        moved = False
        for i in range(n):
            ni, xi, yi = items[i]
            for j in range(i + 1, n):
                nj, xj, yj = items[j]
                dx = xj - xi
                dy = yj - yi
                d2 = dx * dx + dy * dy
                if d2 >= min_dist * min_dist:
                    continue
                d = math.sqrt(d2) if d2 > 0 else 0.0
                if d < 0.001:
                    angle = (hash(ni) ^ hash(nj)) % 360
                    dx = math.cos(math.radians(angle))
                    dy = math.sin(math.radians(angle))
                    d = 1.0
                push = (min_dist - d) / 2.0
                ux, uy = dx / d, dy / d
                xi -= ux * push
                yi -= uy * push
                xj += ux * push
                yj += uy * push
                items[i] = (ni, xi, yi)
                items[j] = (nj, xj, yj)
                moved = True
        if not moved:
            break

    out: dict[str, dict[str, int]] = {}
    for name, x, y in items:
        if bounds and name in bounds:
            bx0, by0, bx1, by1 = bounds[name]
            x = max(bx0, min(bx1, x))
            y = max(by0, min(by1, y))
        else:
            if canvas_w is not None:
                x = max(0.0, min(canvas_w, x))
            if canvas_h is not None:
                y = max(0.0, min(canvas_h, y))
        out[name] = {"x": int(round(x)), "y": int(round(y)), **extras[name]}
    return out
