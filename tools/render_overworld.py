"""Renders an act overview map: a high-resolution composite of a BG3
engine level's exterior patches.

The union bbox of a level's `building == ""` patches defines the world
frame; we render at high resolution so visible areas/islands stand out
and the per-act overview tabs in PopTracker have a backdrop showing the
whole level. Optional crop / pin-overlay modes help during region work.

When invoked with ``--overworld-slug <slug>`` this also writes the
overworld geometry (canvas dims + world bbox + level) into the pack's
tools/projections.json so pack-time pin computation in generate_pack.py
projects per-apworld-region centroids onto the rendered canvas. The
centroid filters + per-region pin overrides live in tools/pin_overrides.json
(not in this script).

Without ``--overworld-slug`` the script just writes a preview PNG to
``--preview-dir`` (default: tools/_texture_work/preview/overworld/).

See MAINTAINER_GUIDE.md for the full pipeline.

Usage:
    python tools/render_overworld.py <LEVEL> --apworld <path>
        [--dds-cache <path>] [--out <path>] [--include-buildings]
        [--canvas-width N] [--overworld-slug <slug>]

Examples:
    python tools/render_overworld.py WLD_Main_A --apworld <path>
        # A1 wilderness exterior
    python tools/render_overworld.py SCL_Main_A --apworld <path>
        # A2 shadow-cursed exterior
    python tools/render_overworld.py CRE_Main_A --apworld <path>
        # mountain pass + monastery
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from hole_fill import fill_interior_holes
import project as P

# See render_multi_zone_map.py for the rationale; same DDS sizes.
Image.MAX_IMAGE_PIXELS = None

TOOLS = Path(__file__).resolve().parent
PACK = TOOLS.parent

# Default canvas width. Height derives from the level's aspect ratio so the
# whole world fits without distortion. 2400 gives ~1.4 px/world for the larger
# levels (WLD ~1700 wide) -- plenty of detail without runaway file sizes.
DEFAULT_CANVAS_W = 2400

# Pin colors -- red on the exterior, gold on patches inside a sub-building.
PIN_EXTERIOR = (255, 64, 64, 230)
PIN_INTERIOR = (255, 200, 48, 230)
PIN_RADIUS = 6

# Overworld centroid filters + per-region pin overrides live in
# tools/pin_overrides.json and are consumed at pack-build time by
# tools/generate_pack.py (via tools/project.py). This script no longer
# emits per-region pin coords -- it only writes the overworld geometry
# (canvas + world bbox + level) into projections.json.


def load_cache() -> dict:
    return json.loads((TOOLS / "bg3_npc_data.json").read_text(encoding="utf-8"))


def load_vt_index() -> dict:
    return json.loads((TOOLS / "vt_index.json").read_text(encoding="utf-8"))


def patch_bbox(p: dict) -> tuple[float, float, float, float]:
    """Centered convention: WorldX/WorldZ are the bbox CENTER."""
    return (
        p["world_x"] - p["world_w"] / 2,
        p["world_z"] - p["world_h"] / 2,
        p["world_x"] + p["world_w"] / 2,
        p["world_z"] + p["world_h"] / 2,
    )


def _overlaps_padded(a: tuple, b: tuple, pad: float) -> bool:
    ax0, az0, ax1, az1 = a
    bx0, bz0, bx1, bz1 = b
    return not (ax1 + pad < bx0 or ax0 - pad > bx1 or
                az1 + pad < bz0 or az0 - pad > bz1)


def _cluster_patches(patches: list[dict], gap: float) -> list[list[int]]:
    """Connect two patches if their world bboxes (expanded by `gap`)
    overlap; return clusters as lists of patch indices, sorted by size
    descending. Used by --crop-major to pick the main on-level island."""
    n = len(patches)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    boxes = [patch_bbox(p) for p in patches]
    for i in range(n):
        for j in range(i + 1, n):
            if _overlaps_padded(boxes[i], boxes[j], gap):
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def _island_bbox(patches: list[dict], indices: list[int]) -> tuple[float, float, float, float]:
    boxes = [patch_bbox(patches[i]) for i in indices]
    return (min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("level", help="BG3 engine level name (e.g. WLD_Main_A)")
    p.add_argument("--apworld", type=Path, required=True,
                   help="Path to the BG3 apworld checkout's worlds/bg3 dir.")
    p.add_argument("--dds-cache", type=Path,
                   default=Path(os.environ.get("BG3_TEXTURE_CACHE", "")
                                 or (TOOLS / "_texture_work" / "extracted")),
                   help="Path to the extracted Granite VT DDS root "
                        "(contains <hash>_0.dds files). Defaults to "
                        "tools/_texture_work/extracted/ or $BG3_TEXTURE_CACHE.")
    p.add_argument("--preview-dir", type=Path,
                   default=TOOLS / "_texture_work" / "preview" / "overworld",
                   help="Directory for preview PNG outputs when --out is not "
                        "set (default: tools/_texture_work/preview/overworld/, "
                        "gitignored).")
    p.add_argument("--include-buildings", action="store_true",
                   help="Include patches with non-empty BuildingUUID. Default "
                        "is exterior-only (building == '') for clean overworld.")
    p.add_argument("--canvas-width", type=int, default=DEFAULT_CANVAS_W)
    p.add_argument("--out", type=Path)
    p.add_argument("--crop-major", action="store_true",
                   help="Crop the rendered canvas to the major-char-density "
                        "island bbox. Drops decorative far-distance patches.")
    p.add_argument("--gap-world", type=float, default=50.0,
                   help="Adjacency gap (world units) for island clustering.")
    p.add_argument("--no-pins", action="store_true",
                   help="Don't overlay character pins -- clean background only.")
    p.add_argument("--padding-frac", type=float, default=0.05,
                   help="Padding around the cropped bbox (fraction of bbox size).")
    p.add_argument("--crop-percentile", type=float, default=None,
                   help="If set, crop to the N-th percentile bbox of character "
                        "pin positions on this level (e.g. 90 means 5th-95th "
                        "percentile). Centers and tightens around dense areas. "
                        "Use INSTEAD of --crop-major (and ignores --gap-world).")
    p.add_argument("--world-bbox", default=None,
                   help="Force a specific render bbox as 'x0,z0,x1,z1' in "
                        "world units. Skips crop-major + crop-percentile. "
                        "Use for previewing outlier islands or hand-tuning.")
    p.add_argument("--overworld-slug", default=None,
                   help="If set, write the overworld geometry (canvas + "
                        "world bbox + level) into the pack's "
                        "tools/projections.json under overworlds[<slug>]. "
                        "Pin computation happens at pack-build time, not here.")
    args = p.parse_args()

    dds_cache: Path = args.dds_cache

    cache = load_cache()
    vt_index = load_vt_index()
    all_patches = cache["patches"].get(args.level, [])
    if not all_patches:
        print(f"[err] no patches cached for {args.level}")
        return 1

    if args.include_buildings:
        patches = list(all_patches)
        scope = "all"
    else:
        patches = [p for p in all_patches if not p["building"]]
        scope = "exterior"
    if not patches:
        print(f"[err] no {scope} patches on {args.level}")
        return 1

    # Pin positions on the level (any character with a position, doesn't have
    # to be a kill-check character). Color depends on whether the character
    # is inside a sub-building.
    chars = [c for c in cache["characters"].values()
             if c.get("level") == args.level and c.get("position")]

    # World bbox: either union of all selected patches (default) or the
    # major-char-density island only (--crop-major). Major island = the
    # cluster of patches whose joint bbox contains the most character pins,
    # found via union-find adjacency with `gap_world` padding.
    #
    # When cropping, we also RESTRICT the paste set to only the island's
    # member patches. Without that, sibling exterior patches whose bboxes
    # incidentally clip into the major's bbox would render alongside (the
    # bottom-left bits in earlier A1 attempts) and pollute the clean view.
    major_member_set: set[int] | None = None
    if args.world_bbox is not None:
        # Manual bbox override -- skip clustering entirely. Format
        # "x0,z0,x1,z1" in world units. Useful for previewing specific
        # sub-areas (outlier islands, hand-picked regions before a full
        # crop is tuned). Padding still applies if supplied.
        try:
            x0_, z0_, x1_, z1_ = (float(v) for v in args.world_bbox.split(","))
        except ValueError:
            print(f"[err] --world-bbox needs 'x0,z0,x1,z1' floats; got {args.world_bbox!r}")
            return 1
        pad_w = (x1_ - x0_) * args.padding_frac
        pad_h = (z1_ - z0_) * args.padding_frac
        x0, z0, x1, z1 = x0_ - pad_w, z0_ - pad_h, x1_ + pad_w, z1_ + pad_h
        print(f"[info] manual bbox: x[{x0:.0f},{x1:.0f}] z[{z0:.0f},{z1:.0f}]")
    elif args.crop_percentile is not None:
        # Percentile crop: take the central N% of character XZ positions to
        # define the crop bbox. Naturally centers around the dense cluster
        # and excludes outliers without needing patch-cluster logic.
        if not chars:
            print("[err] --crop-percentile needs characters with positions")
            return 1
        xs = sorted(c["position"][0] for c in chars)
        zs = sorted(c["position"][2] for c in chars)
        lo = (100 - args.crop_percentile) / 2  # e.g. 90 -> 5
        hi = 100 - lo
        def pct(arr, p):
            idx = max(0, min(len(arr) - 1, int(round((p / 100) * (len(arr) - 1)))))
            return arr[idx]
        x0_, x1_ = pct(xs, lo), pct(xs, hi)
        z0_, z1_ = pct(zs, lo), pct(zs, hi)
        pad_w = (x1_ - x0_) * args.padding_frac
        pad_h = (z1_ - z0_) * args.padding_frac
        x0, z0, x1, z1 = x0_ - pad_w, z0_ - pad_h, x1_ + pad_w, z1_ + pad_h
        inside = sum(1 for c in chars
                     if x0 <= c["position"][0] <= x1 and z0 <= c["position"][2] <= z1)
        print(f"[info] crop-percentile {args.crop_percentile}%: bbox of {inside}/{len(chars)} chars")
    elif args.crop_major:
        # Exclude full-level mega-backdrop patches before clustering.
        # Some levels (CTY_Main_A) have one or two enormous "world" patches
        # covering the entire coordinate space (e.g. 20000x20000). They
        # bridge every smaller patch into a single cluster and balloon the
        # union bbox, defeating the crop-major intent. Heuristic: any patch
        # whose area is >10x the next-largest patch's area is a mega-patch.
        # Exclude them from clustering + bbox derivation, but keep them in
        # the paste set so they contribute backdrop within the crop window.
        areas = sorted([(p["world_w"] * p["world_h"], i) for i, p in enumerate(patches)],
                       reverse=True)
        mega_skip: set[int] = set()
        # Walk down the sorted area list looking for the first >10x drop.
        # Everything above that drop is a "mega" group. This handles both
        # single mega-patches and N co-equal mega-patches (CTY has 2
        # identical 19991x19991 backdrops; ratio inside the group is 1.0
        # but the drop to the next-smaller patch is >100x).
        for k in range(len(areas) - 1):
            if areas[k + 1][0] > 0 and areas[k][0] > 10 * areas[k + 1][0]:
                # Found the drop -- skip everything from areas[0..k] inclusive.
                for j in range(k + 1):
                    mega_skip.add(areas[j][1])
                break
        if mega_skip:
            print(f"[info] excluding {len(mega_skip)} mega-patch(es) from cluster/bbox")
        cluster_patches_idx = [i for i in range(len(patches)) if i not in mega_skip]
        cluster_patches = [patches[i] for i in cluster_patches_idx]
        clusters_local = _cluster_patches(cluster_patches, gap=args.gap_world)
        # Map local indices back to the original `patches` list.
        clusters_idx = [[cluster_patches_idx[j] for j in idxs]
                        for idxs in clusters_local]
        def chars_in_bbox(bb):
            return sum(1 for c in chars
                       if bb[0] <= c["position"][0] <= bb[2] and bb[1] <= c["position"][2] <= bb[3])
        ranked = [(chars_in_bbox(_island_bbox(patches, idxs)), len(idxs), idxs)
                  for idxs in clusters_idx]
        # Sort by char count desc, then patch count desc (chars dominate when
        # the level has chars; for levels with 0 chars the largest cluster
        # wins, which matches the visual "main backdrop").
        ranked.sort(key=lambda r: (-r[0], -r[1]))
        major_idx = ranked[0][2]
        major_member_set = set(major_idx)
        x0_, z0_, x1_, z1_ = _island_bbox(patches, major_idx)
        pad_w = (x1_ - x0_) * args.padding_frac
        pad_h = (z1_ - z0_) * args.padding_frac
        x0, z0, x1, z1 = x0_ - pad_w, z0_ - pad_h, x1_ + pad_w, z1_ + pad_h
        print(f"[info] cropping to major island: {len(major_idx)}/{len(patches)} patches, "
              f"{ranked[0][0]} chars inside")
    else:
        bboxes = [patch_bbox(p) for p in patches]
        x0 = min(b[0] for b in bboxes)
        z0 = min(b[1] for b in bboxes)
        x1 = max(b[2] for b in bboxes)
        z1 = max(b[3] for b in bboxes)
    world_w = x1 - x0
    world_h = z1 - z0
    scale = args.canvas_width / world_w
    canvas_h = int(round(world_h * scale))
    print(f"[info] {args.level}: {len(patches)} {scope} patches, {len(chars)} chars")
    print(f"       world bbox: x[{x0:.0f},{x1:.0f}] z[{z0:.0f},{z1:.0f}]"
          f"  ({world_w:.0f}x{world_h:.0f})")
    print(f"       canvas: {args.canvas_width}x{canvas_h}px  ({scale:.2f} px/world)")

    canvas = Image.new("RGBA", (args.canvas_width, canvas_h), (24, 24, 30, 255))

    def world_to_canvas(wx: float, wz: float) -> tuple[float, float]:
        cx = (wx - x0) * scale
        cy = canvas_h - (wz - z0) * scale  # flip Y: +Z north -> top
        return cx, cy

    # When cropping, decide which patches to paint:
    # - --crop-major (gap=0): start with the major member(s), then also pull
    #   in any other patch whose bbox fits ENTIRELY inside the crop window.
    #   Water-only and decorative tiles that the engine repositions inside
    #   the main patch's area get included; far-away scattered patches that
    #   only clip the crop edge stay excluded.
    # - --crop-percentile: any overlap is fine, crop window is content-driven.
    def overlaps_crop(p):
        bb = patch_bbox(p)
        return not (bb[2] < x0 or bb[0] > x1 or bb[3] < z0 or bb[1] > z1)
    def inside_crop(p):
        bb = patch_bbox(p)
        return bb[0] >= x0 and bb[2] <= x1 and bb[1] >= z0 and bb[3] <= z1
    if args.crop_major and major_member_set is not None:
        kept = []
        for i, p in enumerate(patches):
            if i in major_member_set or inside_crop(p):
                kept.append(p)
        patches = kept
        print(f"       {len(patches)} patches: island-member + bbox-inside-crop")
    elif args.crop_percentile is not None:
        patches = [p for p in patches if overlaps_crop(p)]
        print(f"       {len(patches)} patches overlap percentile crop bbox")
    elif args.world_bbox is not None:
        # Without this filter, --world-bbox loads + composites every patch
        # on the level (including 500-megapixel mega-backdrops) even when
        # most don't overlap the crop window. Drop non-overlapping patches
        # before the paste loop -- huge speedup for narrow bboxes.
        before = len(patches)
        patches = [p for p in patches if overlaps_crop(p)]
        print(f"       {len(patches)}/{before} patches overlap world-bbox window")

    # Draw patches lower-floor first; alpha cutouts in upper floors reveal lower.
    patches.sort(key=lambda p: (p["floor"], p["world_w"] * p["world_h"]))
    pasted = 0
    skipped = 0
    for patch in patches:
        gtex = vt_index.get(patch["texture_uuid"], {}).get("gtex")
        if not gtex:
            skipped += 1
            continue
        dds = dds_cache / f"{gtex}_0.dds"
        if not dds.is_file():
            skipped += 1
            continue
        try:
            with Image.open(dds) as im:
                img = im.convert("RGBA")
        except Exception:
            skipped += 1
            continue
        # Fix Granite-VT extraction gaps (uniform water/filler tiles get
        # deduped to a shared reference at engine runtime; ConverterApp
        # extract leaves those positions alpha=0 in the per-texture DDS).
        img = fill_interior_holes(img)
        pw = max(1, int(round(patch["world_w"] * scale)))
        ph = max(1, int(round(patch["world_h"] * scale)))
        img = img.resize((pw, ph), Image.LANCZOS)
        sw_x = patch["world_x"] - patch["world_w"] / 2
        ne_z = patch["world_z"] + patch["world_h"] / 2
        tlx, tly = world_to_canvas(sw_x, ne_z)
        canvas.alpha_composite(img, (int(round(tlx)), int(round(tly))))
        pasted += 1
    print(f"       pasted {pasted} patches, skipped {skipped}")

    # Overlay character dots. Useful for spotting density clusters.
    if not args.no_pins:
        draw = ImageDraw.Draw(canvas)
        for c in chars:
            wx, _, wz = c["position"]
            cx, cy = world_to_canvas(wx, wz)
            color = PIN_INTERIOR if c.get("building") else PIN_EXTERIOR
            draw.ellipse((cx - PIN_RADIUS, cy - PIN_RADIUS,
                          cx + PIN_RADIUS, cy + PIN_RADIUS),
                         fill=color, outline=(255, 255, 255, 255), width=2)

    suffix = scope
    if args.crop_major:
        suffix += "_cropped"
    if args.no_pins:
        suffix += "_clean"
    out_path = args.out or (args.preview_dir / f"{args.level}_{suffix}.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    print(f"[done] wrote {out_path}  ({out_path.stat().st_size / 1024:.0f} KB)")

    # Density summary -- character building affiliation breakdown helps see
    # which sub-areas have meaningful interior content.
    from collections import Counter
    bcounts = Counter((c.get("building") or "<exterior>") for c in chars)
    print(f"[stats] characters by building on this level:")
    for b, n in bcounts.most_common():
        print(f"        {b:24s} {n}")

    # If --overworld-slug given, persist the overworld geometry into the
    # pack's projections.json. Pack-time pin computation reads this entry
    # + bg3_npc_data.json + pin_overrides.json to project per-apworld-region
    # centroids onto the rendered canvas.
    if args.overworld_slug:
        overworld_proj = {
            "level": args.level,
            "canvas": [int(args.canvas_width), int(canvas_h)],
            "world_bbox": [float(x0), float(z0), float(x1), float(z1)],
        }
        proj_path = P.upsert_overworld_projection(
            PACK, args.overworld_slug, overworld_proj,
        )
        print(f"[done] wrote overworld geometry for "
              f"{args.overworld_slug!r} to {proj_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
