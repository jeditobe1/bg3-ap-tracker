"""Patch DDS hole-fill: paint interior alpha=0 pixels with neighbor color.

The Granite VT extraction leaves alpha-0 holes inside otherwise-opaque
patches. These are positions where the engine references a deduplicated
uniform tile (typically water or filler terrain); ConverterApp's tile-set
extractor doesn't populate those positions in the per-texture DDS.

For our composited maps we want those positions filled so the final image
doesn't have black dots scattered across coastlines.

Algorithm:
1. Build a binary mask of opaque pixels (alpha > threshold).
2. binary_fill_holes() flood-fills enclosed regions in the mask; the
   difference between filled and original is exactly the interior holes.
3. For each hole pixel, write the average RGB of its non-hole neighbors
   (cheap dilation-based fill). Alpha goes to opaque.

Uses scipy for the flood-fill (much faster than pure Python at typical
texture sizes of ~10kx13k px).
"""

from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage


def fill_interior_holes(img: Image.Image, alpha_threshold: int = 32,
                        max_hole_area: int = 80_000) -> Image.Image:
    """Return a copy of `img` with interior alpha-0 holes painted opaque.

    `alpha_threshold`: pixels with alpha <= this count as "transparent" for
    hole detection. Default 32 catches both fully-transparent and very-faint
    pixels that occur on tile edges.

    `max_hole_area`: only holes whose area (in pixels) is <= this get filled.
    Granite-VT missing-tile artifacts are small (one tile is typically
    128x128 or 256x256, so up to ~65K px). Authored alpha features (lakes,
    chasms, designed cutouts) are much larger and should remain transparent.
    80K (~280x280) is the sweet spot we landed on for BG3's WorldMap patches.
    """
    arr = np.array(img.convert("RGBA"))
    H, W, _ = arr.shape

    opaque = arr[..., 3] > alpha_threshold
    # binary_fill_holes flood-fills closed regions. Pixels that are False
    # in `opaque` but True in `filled` are the interior holes.
    filled = ndimage.binary_fill_holes(opaque)
    raw_holes = filled & ~opaque
    if not raw_holes.any():
        return img

    # Label connected components in the holes mask, drop ones too big to be
    # missing-tile artifacts. Keeps designer-authored alpha features intact.
    labeled, n = ndimage.label(raw_holes)
    sizes = ndimage.sum(raw_holes, labeled, range(1, n + 1))
    keep_labels = {i + 1 for i, s in enumerate(sizes) if s <= max_hole_area}
    if not keep_labels:
        return img
    # Vectorized mask: True where labeled is in keep_labels.
    holes = np.isin(labeled, list(keep_labels))

    # For each hole pixel, sample the RGB of the *nearest* opaque pixel.
    # distance_transform_edt with return_indices=True gives us, for every
    # transparent pixel, the (y,x) of the closest opaque one. We index back
    # into the original RGB to get a true neighbor color regardless of hole
    # diameter -- a gaussian blur fails the moment a hole is wider than its
    # radius (its center has no opaque support and divides to near-black).
    rgb = arr[..., :3]
    _, indices = ndimage.distance_transform_edt(~opaque, return_indices=True)
    nearest_y, nearest_x = indices  # shape (H, W) each
    nearest_rgb = rgb[nearest_y, nearest_x]  # shape (H, W, 3)

    out = arr.copy()
    out[holes, :3] = nearest_rgb[holes]
    out[holes, 3] = 255
    return Image.fromarray(out, "RGBA")


if __name__ == "__main__":
    # Smoke test on the A2 main patch.
    import sys
    from pathlib import Path
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if p is None or not p.is_file():
        print("usage: hole_fill.py <dds_path>")
        sys.exit(1)
    with Image.open(p) as src:
        before = src.convert("RGBA")
    print(f"before: {before.size} alpha-0 ratio = "
          f"{(np.array(before)[..., 3] == 0).sum() / (before.size[0] * before.size[1]):.3f}")
    after = fill_interior_holes(before)
    print(f"after:  alpha-0 ratio = "
          f"{(np.array(after)[..., 3] == 0).sum() / (after.size[0] * after.size[1]):.3f}")
    out_path = p.with_name(p.stem + "_holefill.png")
    after.save(out_path)
    print(f"wrote {out_path}")
