"""Render a single-zone region map: backdrop PNG + projection metadata.

The pin bbox (with padding) drives the canvas world bbox. We composite every
live patch on the region's level whose world bbox overlaps the pin bbox,
then crop/scale to PopTracker's per-region pixel canvas. The renderer
records the world<->canvas transform into the pack's projections.json so
pack-time pin computation (tools/generate_pack.py) re-projects chars
through the same math.

Per-region world-coord offsets and per-character overrides live in
tools/pin_overrides.json (not in this script).

See MAINTAINER_GUIDE.md for the full pipeline.

Usage:
    python tools/render_region_map.py <region_slug> --level <LEVEL>
        --apworld <path> [--dds-cache <path>]
        [--out images/maps/<slug>.png] [--no-write-map]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

from PIL import Image

from hole_fill import fill_interior_holes
import project as P

# BG3 backdrop mega-patches (SCL_Main_A, CRE_Main_A, etc.) are 500+ megapixel
# DDS textures. PIL's default decompression-bomb guard rejects them with
# DecompressionBombError, which the patch loop's `except Exception` would
# silently swallow -- dropping the overworld backdrops. Disable the guard;
# the source files are local, not user-supplied.
Image.MAX_IMAGE_PIXELS = None

TOOLS = Path(__file__).resolve().parent
PACK = TOOLS.parent

# PopTracker map canvas size. Matches the size the pack generator uses for
# the placeholder PNGs it ships; pin coords are integers in this space.
MAP_W = 480
MAP_H = 320

# Bottom strip reserved for the quest panel (rendered by the generator at
# pack time -- pins go there via quest_grid_pin_position). The texture
# composite uses the area above the panel so quest pins overlay a separate
# section instead of crowding the area map.
QUEST_PANEL_FRAC = 0.20
DECLUTTER_MIN_DIST_PX = 12   # auto-scaled with MAP_W elsewhere

# Padding added to the pin world bbox so pins don't sit at the edge.
PIN_PADDING_FRAC = 0.20

# Floor on the canvas world bbox. Acts as a max-zoom cap: even when the pin
# cluster is tiny (e.g. the nautiloid helm fight is 39x17 world units), the
# canvas won't crop closer than this, so the surrounding texture context
# stays visible. Tune up if pins feel too zoomed in; tune down to crop tighter.
MIN_CANVAS_WORLD_WIDTH = 100.0
MIN_CANVAS_WORLD_HEIGHT = 70.0

# Filter: patches whose Building affiliation matches one of these will be
# included in the composite. For now we accept everything; later regions
# may want to scope (e.g. building == "" for outdoor, or a specific building
# name for interior regions).
INCLUDE_ALL_PATCHES = True


def load_cache() -> dict:
    return json.loads((TOOLS / "bg3_npc_data.json").read_text(encoding="utf-8"))


def load_vt_index() -> dict:
    return json.loads((TOOLS / "vt_index.json").read_text(encoding="utf-8"))


def patch_bbox(p: dict) -> tuple[float, float, float, float]:
    """(x0, z0, x1, z1) in world coords using the centered convention."""
    return (
        p["world_x"] - p["world_w"] / 2,
        p["world_z"] - p["world_h"] / 2,
        p["world_x"] + p["world_w"] / 2,
        p["world_z"] + p["world_h"] / 2,
    )


def rect_overlaps(a: tuple, b: tuple) -> bool:
    ax0, az0, ax1, az1 = a
    bx0, bz0, bx1, bz1 = b
    return not (ax1 < bx0 or ax0 > bx1 or az1 < bz0 or az0 > bz1)


def compute_canvas_world_bbox(pin_positions: Iterable[tuple[float, float]],
                              pad_frac: float,
                              aspect: float,
                              min_w: float = 0.0,
                              min_h: float = 0.0) -> tuple[float, float, float, float]:
    """Pin-bbox + padding, expanded to match a target aspect ratio (w/h) and
    floored at (min_w, min_h) world units. Returns (x0, z0, x1, z1) world bbox
    to project onto a canvas of `aspect`."""
    xs = [p[0] for p in pin_positions]
    zs = [p[1] for p in pin_positions]
    if not xs:
        raise ValueError("no pins to bbox")
    x0, x1 = min(xs), max(xs)
    z0, z1 = min(zs), max(zs)
    cx = (x0 + x1) / 2
    cz = (z0 + z1) / 2
    # Pad.
    w = max(1.0, x1 - x0)
    h = max(1.0, z1 - z0)
    pad_w = w * pad_frac
    pad_h = h * pad_frac
    w = (x1 - x0) + 2 * pad_w
    h = (z1 - z0) + 2 * pad_h
    # Apply minimum bbox floor (max-zoom cap).
    w = max(w, min_w)
    h = max(h, min_h)
    # Match aspect: extend the shorter dim symmetrically.
    cur_aspect = w / h
    if cur_aspect < aspect:
        w = h * aspect
    elif cur_aspect > aspect:
        h = w / aspect
    return cx - w / 2, cz - h / 2, cx + w / 2, cz + h / 2


def render_quest_panel(canvas: Image.Image, full_size: tuple[int, int]) -> None:
    """Draw the bottom quest-panel strip onto an already-composited canvas.

    Matches the placeholder generator's panel layout so pins land on a
    visually-distinct strip below the area map regardless of whether the
    region is using a texture or a placeholder."""
    from PIL import ImageDraw, ImageFont
    w, h = full_size
    panel_h = int(round(h * QUEST_PANEL_FRAC))
    panel_top = h - panel_h
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, panel_top, w - 1, h - 1), fill=(28, 24, 24, 255))
    draw.line((0, panel_top, w - 1, panel_top), fill=(255, 255, 255, 200), width=1)
    try:
        font = ImageFont.truetype("arial.ttf", max(14, w // 60))
    except OSError:
        font = ImageFont.load_default()
    draw.text((6, panel_top + 4), "Quests", fill=(255, 255, 255, 220), font=font)


def expand_until_overlap(bbox: tuple[float, float, float, float],
                          patches: list[dict],
                          slacks: tuple[float, ...] = (25, 50, 100, 200, 400),
                          ) -> tuple[tuple[float, float, float, float], list[dict]]:
    """If no patches overlap `bbox`, expand outward by each slack value until
    we find some. Pin char positions tag spawn triggers that occasionally
    sit just outside their containing patch's metadata bbox (Druid Grove
    chars are ~15 world units west of the main wilderness patch bbox); a
    small expansion picks them up without distorting the projection.

    Returns the (possibly expanded) bbox and the list of overlapping patches.
    """
    selected = [p for p in patches if rect_overlaps(patch_bbox(p), bbox)]
    if selected:
        return bbox, selected
    x0, z0, x1, z1 = bbox
    for slack in slacks:
        expanded = (x0 - slack, z0 - slack, x1 + slack, z1 + slack)
        selected = [p for p in patches if rect_overlaps(patch_bbox(p), expanded)]
        if selected:
            print(f"  expanded canvas bbox by {slack:.0f} world units to find {len(selected)} patches")
            return expanded, selected
    return bbox, []


def composite_region(canvas_bbox: tuple[float, float, float, float],
                     patches: list[dict],
                     vt_index: dict,
                     dds_cache: Path,
                     canvas_size: tuple[int, int]) -> Image.Image:
    """Return an RGBA canvas with all overlapping patches painted in floor-
    ascending order (low floors at the bottom, high floors on top via alpha)."""
    x0, z0, x1, z1 = canvas_bbox
    cw, ch = canvas_size
    world_w = x1 - x0
    world_h = z1 - z0
    scale = cw / world_w  # uniform: we already matched aspect
    canvas = Image.new("RGBA", (cw, ch), (24, 24, 30, 255))

    selected = [p for p in patches if rect_overlaps(patch_bbox(p), canvas_bbox)]
    # Sort by floor ascending so higher floors layer on top.
    selected.sort(key=lambda p: (p["floor"], p["world_w"] * p["world_h"]))
    print(f"  compositing {len(selected)} overlapping patches:")

    for p in selected:
        gtex = vt_index.get(p["texture_uuid"], {}).get("gtex")
        if not gtex:
            print(f"    [skip] no gtex for {p.get('uuid','?')[:8]} ({p['building'] or '<world>'} fl{p['floor']})")
            continue
        dds = dds_cache / f"{gtex}_0.dds"
        if not dds.is_file():
            print(f"    [skip] missing {dds.name}")
            continue
        try:
            with Image.open(dds) as im:
                img = im.convert("RGBA")
        except Exception as e:
            # A handful of patches in the bank have degenerate 2x2/0x0 DDS
            # outputs that Pillow can't decode (extraction artifacts on
            # tiles that the engine reuses as point markers, not as real
            # area textures). Skip them -- they don't carry meaningful
            # visible content anyway.
            print(f"    [skip] {dds.name}: {e}")
            continue
        # Fix Granite-VT extraction gaps (uniform water/filler tiles get
        # deduped to a shared reference at engine runtime; ConverterApp
        # extract leaves those positions alpha=0 in the per-texture DDS).
        img = fill_interior_holes(img)
        # Resize patch to its scaled-pixel footprint on the canvas.
        patch_w_px = max(1, int(round(p["world_w"] * scale)))
        patch_h_px = max(1, int(round(p["world_h"] * scale)))
        img = img.resize((patch_w_px, patch_h_px), Image.LANCZOS)
        # Paste origin: top-left of patch in canvas pixel coords.
        sw_x = p["world_x"] - p["world_w"] / 2
        ne_z = p["world_z"] + p["world_h"] / 2
        tlx = (sw_x - x0) * scale
        tly = ch - (ne_z - z0) * scale  # flip Y: +Z north -> top
        canvas.alpha_composite(img, (int(round(tlx)), int(round(tly))))
        print(f"    [paste] fl{p['floor']:>2d} {p['building'] or '<world>':12s} {patch_w_px}x{patch_h_px}px @ ({int(tlx)},{int(tly)})  status={p.get('trigger_status','?')}")
    return canvas


def apname_to_region(apworld_dir: Path) -> dict[str, str]:
    """Parse apworld locationids.py to map AP location name -> region slug."""
    import ast
    src = (apworld_dir / "locationids.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "LOCATION_NAME_ID_REGION":
                    return {ap: r for ap, _lid, r in ast.literal_eval(node.value)}
    return {}


def kill_uuid_to_apname(apworld_dir: Path) -> dict[str, str]:
    """Walk apworld bg3_locations.py for `Kill-S_..._<uuid>` -> AP location name.
    Reuses the same regex as build_npc_data.py."""
    import ast, re
    src = (apworld_dir / "bg3_locations.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    rx = re.compile(r"^Kill-(S_.*?)_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not (isinstance(elt, ast.List) and len(elt.elts) >= 2):
                continue
            try:
                row = ast.literal_eval(elt)
            except Exception:
                continue
            if not (isinstance(row[0], str) and rx.match(row[0])):
                continue
            uuid = rx.match(row[0]).group(2)
            ap_names = row[1] if isinstance(row[1], list) else [row[1]]
            for n in ap_names:
                out.setdefault(uuid, str(n))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("region_slug", help="e.g. 'tutorial' for nautiloid")
    p.add_argument("--level", required=True, help="e.g. 'TUT_Avernus_C'")
    p.add_argument("--apworld", type=Path, required=True,
                   help="Path to the BG3 apworld checkout's worlds/bg3 dir.")
    p.add_argument("--dds-cache", type=Path,
                   default=Path(os.environ.get("BG3_TEXTURE_CACHE", "")
                                 or (TOOLS / "_texture_work" / "extracted")),
                   help="Path to the extracted Granite VT DDS root "
                        "(contains <hash>_0.dds files). Defaults to "
                        "tools/_texture_work/extracted/ or $BG3_TEXTURE_CACHE.")
    p.add_argument("--out", type=Path)
    p.add_argument("--projections-out", type=Path)
    p.add_argument("--no-write-map", action="store_true",
                   help="Don't write the PNG; useful for projection-only runs.")
    args = p.parse_args(argv)

    dds_cache: Path = args.dds_cache

    cache = load_cache()
    overrides = P.load_overrides(PACK)
    # Inject synthetic chars + apply position/building overrides on the
    # loaded cache. In-memory only; never written back to bg3_npc_data.json.
    P.apply_overrides_to_cache(cache, overrides, verbose=True)
    vt_index = load_vt_index()
    patches = cache["patches"].get(args.level, [])
    if not patches:
        print(f"[err] no patches cached for level {args.level}")
        return 1

    # Collect kill characters that belong to THIS apworld region (not all
    # chars on the level). Levels like WLD_Main_A host many apworld regions
    # (beach, crypt, grove, ...) so filtering avoids dragging neighbor pins
    # into this region's bbox and onto its map.
    uuid_to_ap = kill_uuid_to_apname(args.apworld)
    ap_to_region = apname_to_region(args.apworld)
    pin_positions_by_ap: dict[str, tuple[float, float]] = {}
    for uuid, ch in cache["characters"].items():
        if ch.get("level") != args.level or not ch.get("position"):
            continue
        ap_name = uuid_to_ap.get(uuid)
        if not ap_name:
            continue
        if ap_to_region.get(ap_name) != args.region_slug:
            continue
        x, _, z = ch["position"]
        pin_positions_by_ap[ap_name] = (x, z)
    if not pin_positions_by_ap:
        print(f"[err] no kill pins for region {args.region_slug!r} on {args.level}")
        return 1
    print(f"[ok] {len(pin_positions_by_ap)} {args.region_slug!r} kill pins on {args.level}")

    # Area aspect matches MAP_W / area_h, NOT MAP_W / MAP_H -- the quest
    # panel reserves the bottom strip, so kill pins + texture both live
    # in the area portion of the canvas. Computing the bbox with the
    # full-canvas aspect causes pins to project to a different y than
    # the texture renders to (vertical misalignment).
    area_h_calc = int(round(MAP_H * (1.0 - QUEST_PANEL_FRAC)))
    # Apply per-region world-coord offset to char positions if configured
    # (tools/pin_overrides.json::region_world_offsets). Used to translate
    # trigger-spawn coords onto the actual fight location.
    pin_positions_by_ap = {
        n: P.apply_region_offset(args.region_slug, wx, wz, overrides)
        for n, (wx, wz) in pin_positions_by_ap.items()
    }
    canvas_bbox = compute_canvas_world_bbox(
        pin_positions_by_ap.values(), PIN_PADDING_FRAC, MAP_W / area_h_calc,
        min_w=MIN_CANVAS_WORLD_WIDTH, min_h=MIN_CANVAS_WORLD_HEIGHT,
    )
    # If the pin bbox doesn't intersect any patches (e.g. Druid Grove --
    # chars are at trigger-spawn coords ~15 world units west of the main
    # wilderness patch's metadata bbox), expand to include neighbors and
    # re-fit the aspect to the new bbox.
    canvas_bbox, _ = expand_until_overlap(canvas_bbox, patches)
    # Re-aspect-fit after expansion so the canvas world ratio still
    # matches the area canvas pixel ratio.
    cx_b = (canvas_bbox[0] + canvas_bbox[2]) / 2
    cz_b = (canvas_bbox[1] + canvas_bbox[3]) / 2
    bw = canvas_bbox[2] - canvas_bbox[0]
    bh = canvas_bbox[3] - canvas_bbox[1]
    target_aspect = MAP_W / area_h_calc
    if bw / bh < target_aspect:
        bw = bh * target_aspect
    else:
        bh = bw / target_aspect
    canvas_bbox = (cx_b - bw / 2, cz_b - bh / 2, cx_b + bw / 2, cz_b + bh / 2)
    x0, z0, x1, z1 = canvas_bbox
    print(f"[ok] canvas world bbox: x[{x0:.1f}, {x1:.1f}] z[{z0:.1f}, {z1:.1f}]  "
          f"({x1-x0:.1f}x{z1-z0:.1f} world)")

    # Area texture occupies the TOP (1 - QUEST_PANEL_FRAC) of the canvas;
    # bottom strip is reserved for the quest panel the generator overlays.
    area_h = int(round(MAP_H * (1.0 - QUEST_PANEL_FRAC)))
    out_png = args.out or (PACK / "images" / "maps" / f"{args.region_slug}.png")
    if not args.no_write_map:
        full = Image.new("RGBA", (MAP_W, MAP_H), (24, 24, 30, 255))
        area = composite_region(canvas_bbox, patches, vt_index, dds_cache, (MAP_W, area_h))
        full.paste(area, (0, 0))
        render_quest_panel(full, (MAP_W, MAP_H))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        full.save(out_png)
        print(f"[done] wrote {out_png}")

    # Record the world->canvas transform for this region. Single-zone
    # regions get one "*"-tagged zone that matches any char on the level
    # (the apworld region-tag already routes chars correctly; building
    # tags don't disambiguate here). Canvas rect covers the area-only
    # portion above the quest panel.
    region_proj = {
        "canvas": [MAP_W, MAP_H],
        "zones": [
            {
                "title": args.region_slug,
                "level": args.level,
                "char_building": ["*"],
                "patch_building": ["*"],
                "world_bbox": [float(v) for v in canvas_bbox],
                "canvas_rect": [0, 0, MAP_W, area_h],
            }
        ],
    }
    proj_path = P.upsert_region_projection(PACK, args.region_slug, region_proj)
    print(f"[done] updated {proj_path} (single-zone projection for "
          f"'{args.region_slug}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
