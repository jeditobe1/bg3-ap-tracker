"""Render a multi-zone region map: two or more game-space areas under a
single apworld region, laid out grid-style on one canvas.

Goblin Camp is the canonical case -- the apworld bundles the open-air
camp courtyard and the Shattered Sanctum dungeon interior under one
`goblin_camp` region, but in-game they're separate loading zones with
characters at world positions ~400 units apart. The compositor:

  1. For each zone, selects its kill chars via a building-tag filter.
  2. Computes the zone's world bbox from those char positions + padding.
  3. Picks the exterior patches that overlap that bbox AND match the
     zone's building filter (so the exterior zone gets `building == ""`
     patches near the camp, and the Sanctum zone gets `building == "GoblinCamp"`
     patches across all floors).
  4. Renders each zone into its own sub-canvas with the standard hole-fill.
  5. Pastes the sub-canvases into a grid layout on the final canvas with
     a pixel separator and per-zone label bars.
  6. Records the per-zone world<->canvas transform into projections.json.

Outputs the composite PNG into the pack's images/maps/ folder and the
zone projection metadata into the pack's tools/projections.json. Pin
canvas-coords are NOT emitted here -- pack-time pin computation in
generate_pack.py consumes projections.json + pin_overrides.json +
bg3_npc_data.json.

Character overrides (synthetic chars, position overrides, building
overrides) live in tools/pin_overrides.json (not in this script).

See MAINTAINER_GUIDE.md for the full pipeline.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw

from hole_fill import fill_interior_holes
import project as P

# BG3 backdrop mega-patches (e.g. CRE_Main_A floor-0 building="") are
# 500+ megapixel DDS textures. PIL's default decompression-bomb guard
# rejects them with DecompressionBombError, which our patch-loop's
# broad `except Exception` would silently swallow -- dropping the
# cliff-trail/Underdark backdrops from the render. Disable the guard;
# the source files are local, not user-supplied.
Image.MAX_IMAGE_PIXELS = None

TOOLS = Path(__file__).resolve().parent
PACK = TOOLS.parent

# PopTracker map canvas. 960x640 doubles our default per-region size --
# gives finer detail on the source texture under PopTracker's scaler at the
# cost of larger PNG file size (~5x). Pin coords are in canvas-pixel space,
# so the maps.json entry for this region needs `location_size` scaled to
# match (we double the global location_size for multi-zone maps).
CANVAS_W = 960
CANVAS_H = 640
ZONE_GAP_PX = 12               # pixel separator between side-by-side zones
ZONE_PAD_FRAC = 0.20           # world-bbox padding before projection (each side)
ZONE_LABEL_HEIGHT = 32         # px reserved for a zone title bar at top
# Bottom strip reserved for the quest panel; zones render above it.
QUEST_PANEL_FRAC = 0.20
# Per-region zone configuration. Each entry under `zones` is one game-space
# area to render. `building` is matched against character cache 'building'
# (use None to match exterior chars, list of strs to match any of them).
# `patch_building` is matched against patch 'building' the same way.
#
# Character-level fix-ups (synthetic chars, position overrides, building
# overrides) live in tools/pin_overrides.json and are applied via
# project.apply_overrides_to_cache(). Edit overrides there rather than
# adding python dicts here.
REGION_DEFINITIONS = {
    "goblin_camp": {
        "level": "WLD_Main_A",
        "zones": [
            {
                "title": "Camp",
                "char_building": [None, "GoblinWalkway"],
                "patch_building": [""],
            },
            {
                "title": "Shattered Sanctum",
                "char_building": ["GoblinCamp", "GoblinCampPrison"],
                "patch_building": ["GoblinCamp", "GoblinCampPrison"],
            },
        ],
    },
    # Grymforge apworld region spans two distinct WLD_Main_A clusters:
    # the Selunite Outpost / Ruined City area (Larian-tagged "KethericCity"
    # in the WorldMap data despite being Act 1) and the cave-and-forge
    # exterior leading to the Adamantine Forge. Zone order = in-game
    # encounter order: player enters the Forge Approach (ruined city
    # area with mind flayers + Nere) first, then proceeds to the forge.
    "grymforge": {
        "level": "WLD_Main_A",
        "zones": [
            {
                "title": "Forge Approach",
                "char_building": ["KethericCity"],
                "patch_building": ["KethericCity"],
            },
            {
                "title": "The Adamantine Forge",
                "char_building": [None],
                "patch_building": [""],
            },
        ],
    },
    # Blighted Village has two clearly-separated kill clusters in
    # WLD_Main_A: the village surface (goblin patrol + ettercaps) around
    # x[0..50], and the Well/Spider Queen lair ~500 world units SW. Both
    # are tagged with building="" (exterior) in the patch data, so we
    # split by world position rather than building tag.
    "blighted_village": {
        "level": "WLD_Main_A",
        "zones": [
            {
                "title": "The Well",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_filter": (-700, -500, -400, -250),  # x0,z0,x1,z1
            },
            {
                "title": "Village Surface",
                "char_building": [None, "RuinedBuilding", "Smithy", "Apothercary"],
                "patch_building": ["", "RuinedBuilding", "Smithy", "Apothercary"],
                "world_bbox_filter": (-50, 300, 200, 500),
            },
        ],
    },
    # Monastery apworld region: three zones, 2-column grid so the dense
    # monastery interior gets full canvas height while the two sparse
    # outdoor zones share the left column.
    #   - Top-left:    Mountain Trailhead (WLD_Main_A, Gith Raiders).
    #   - Bottom-left: Mountain Pass cliff trail (CRE_Main_A exterior,
    #                  cultists/undead). Same worldspace as the monastery
    #                  -- the player walks from trail to monastery without
    #                  a portal -- but rendered separately so the east-wing
    #                  monastery mobs aren't shrunk to nothing.
    #   - Right full:  Rosymorn Monastery (CRE_Main_A interior).
    "monastery": {
        "level": "CRE_Main_A",
        "zones": [
            {
                "title": "Mountain Trailhead",
                "level": "WLD_Main_A",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_override": (-220.0, 530.0, -40.0, 680.0),
                "cell": (0, 0, 1, 1),
            },
            {
                "title": "Mountain Pass",
                "char_building": [None],
                "patch_building": [""],
                # Cultists span ~25 world units; widen to 250 so the
                # mega-patch backdrop renders at a useful scale (the
                # crop-source-before-resize logic in render_zone makes
                # this possible without exploding to 20k+ px intermediate
                # images).
                "world_bbox_override": (-200.0, -250.0, 50.0, 0.0),
                "cell": (0, 1, 1, 1),
            },
            {
                "title": "Rosymorn Monastery",
                "char_building": ["Monastary"],
                # Pull both the exterior backdrop ("") and Monastary
                # interior patches. render_zone draws the exterior on
                # TOP of same-floor interior patches, so the rendered
                # view shows the natural worldmap building (roofs +
                # courtyard exterior) rather than a floor-N cutaway.
                "patch_building": ["", "Monastary"],
                "cell": (1, 0, 1, 2),
            },
        ],
    },
    # Nautiloid tutorial on TUT_Avernus_C. Two zones split by floor on
    # patches that all share building="Nautiloid":
    #   - Lower Deck (left): floor-0 Nautiloid patch (the 168x92 lower
    #     deck where the 3 First-Fight Imps spawn). Imps retagged to a
    #     synthetic "LowerDeck" building via overrides so they filter
    #     cleanly out of the Helm zone.
    #   - Helm (right): floor-1 Nautiloid patch (the 301x187 helm with
    #     the iconic mind-flayer symbol). 6 Helm Devil + BackUp chars.
    # The floor-0 building="" 485x198 backdrop is intentionally excluded
    # (out-of-date data per maintainer); patch_floors filter on each
    # zone restricts which floors render, since both target patches
    # share the "Nautiloid" tag and overlap spatially.
    "tutorial": {
        "level": "TUT_Avernus_C",
        "zones": [
            {
                "title": "Lower Deck",
                "char_building": ["LowerDeck"],
                "patch_building": ["Nautiloid"],
                "patch_floors": [0],
                # Imps cluster at x[-57,-38] z[-398,-393] (~20x5 world).
                # Bbox centered on the cluster with enough breathing
                # room to read the deck shape around them.
                "world_bbox_override": (-90.0, -415.0, -10.0, -375.0),
                "cell": (0, 0, 1, 1),
            },
            {
                "title": "Helm",
                "char_building": ["Nautiloid"],
                "patch_building": ["Nautiloid"],
                "patch_floors": [1],
                # Helm chars cluster at x[-76,-37] z[-394,-380] (~40x14
                # world). Bbox covers the helm symbol + immediate deck.
                "world_bbox_override": (-115.0, -415.0, 5.0, -360.0),
                "cell": (1, 0, 1, 1),
            },
        ],
    },
    # East Act 2 (Reithwin Cursed Forest) on SCL_Main_A. Single zone.
    # Kar'niss + caravan are a triggered patrol-fight in this same
    # forest area; their cached coords are at quest-trigger spawn
    # points elsewhere on SCL but they're overridden up via
    # char_position_overrides (pin_overrides.json) to project into the
    # main forest cluster.
    "east_act2": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Cursed Forest",
                "char_building": [None, "CursedForestTower", "WaterlocksKiln"],
                "patch_building": ["", "CursedForestTower", "WaterlocksKiln"],
                "cell": (0, 0, 1, 1),
            },
        ],
    },
    # West Act 2 (Reithwin Town + basements) on SCL_Main_A. Three
    # distinct worldspace cells:
    #   - Reithwin Town: main town (Tollhouse + Brewery + Hospital +
    #     Fishery + exterior).
    #   - Town Basement: separate cell at z~-750 with basement
    #     shadows + wraith.
    #   - HoH Morgue: House of Healing morgue + acid pit cell at z~-940.
    # 2-col x 2-row grid: town full-left, basement + morgue stacked right.
    # (apworld 0.4.5 reassigned the 39 LL-defense waves to last_light,
    # so they no longer need a sub-cell here.)
    "west_act2": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Reithwin Town",
                "char_building": [None, "Tax", "Brewery", "Hospital", "Fishery"],
                "patch_building": ["", "Tax", "Brewery", "Hospital", "Fishery"],
                # Exclude basement + AcidPit outliers from the town bbox.
                "world_bbox_filter": (-300, -150, 200, 250),
                "cell": (0, 0, 1, 2),
            },
            {
                "title": "Town Basement",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_filter": (60, -800, 200, -700),
                "cell": (1, 0, 1, 1),
            },
            {
                "title": "HoH Morgue",
                "char_building": [None, "AcidPit"],
                "patch_building": ["", "AcidPit"],
                "world_bbox_filter": (0, -1000, 100, -900),
                "cell": (1, 1, 1, 1),
            },
        ],
    },
    # Moonrise Towers on SCL_Main_A. Two zones:
    #   - Towers: Moonrise + Moonrise4 building cluster at z~-200 plus
    #     the courtyard exterior.
    #   - Dungeon: the prison / Z'rell quarters basement-style level at
    #     (~590, -650), placed in a separate worldmap cell on SCL even
    #     though it's in-game accessed from the Moonrise interior.
    "moonrise": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Moonrise Towers",
                "char_building": [None, "Moonrise", "Moonrise4"],
                "patch_building": ["", "Moonrise", "Moonrise4"],
                "world_bbox_filter": (-300, -300, 0, -150),
                "cell": (0, 0, 1, 1),
            },
            {
                "title": "Towers Dungeon",
                "char_building": ["Dungeon"],
                "patch_building": ["Dungeon", ""],
                "world_bbox_filter": (500, -700, 700, -600),
                "cell": (1, 0, 1, 1),
            },
        ],
    },
    # Gauntlet of Shar on SCL_Main_A. Single zone -- the gauntlet's
    # Silent Library, Soft Step trial, Self-Same trial, Yurgir fight,
    # and Balthazar's necromancer chamber are all one continuous
    # worldspace cluster on SCL. Cloaker + Displacer are mini-bosses on
    # the exterior approach (Shadow-Cursed Lands SE) and get pulled in
    # by the exterior bucket; Cursed Justiciar summons that the apworld
    # tags into shar_gauntlet but live at the Colony are overridden
    # back next to Balthazar via char_position_overrides (pin_overrides.json).
    "shar_gauntlet": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Gauntlet of Shar",
                "char_building": [None, "Shar"],
                "patch_building": ["", "Shar"],
                # Restrict to the gauntlet cluster so the Cloaker /
                # Displacer exterior-tagged bosses don't drag the bbox
                # outward to their distant approach positions.
                "world_bbox_filter": (-900, -900, -600, -650),
                "cell": (0, 0, 1, 1),
            },
        ],
    },
    # Mindflayer Colony on SCL_Main_A. Single zone. Myrkul (S_MOO_Ketheric)
    # is cached at the Moonrise platform but the Colony Showdown goal
    # plays out at MFC_Arena; char_position_overrides (pin_overrides.json)
    # puts him there.
    "mindflayer": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Mindflayer Colony",
                "char_building": [None, "MFC_Arena"],
                "patch_building": ["", "MFC_Arena"],
                "cell": (0, 0, 1, 1),
            },
        ],
    },
    # Last Light Inn apworld region: three zones on SCL_Main_A.
    #   - Last Light Inn (full left): the inn proper + Fist Marcus on
    #     the bridge approach (LastLightInn building + nearby exterior
    #     at z[100, 190]).
    #   - Defense (top right): 39 inn-assault wave chars that spawn on
    #     the beach north of the inn (z[190, 230]). apworld upstream
    #     reassigned these from west_act2 to last_light in 0.4.5.
    #   - Meenlock Cave (bottom right): hidden basement-style cavern
    #     at z~-700 with the 6 Meenlocks.
    "last_light": {
        "level": "SCL_Main_A",
        "zones": [
            {
                "title": "Last Light Inn",
                "char_building": [None, "LastLightInn"],
                "patch_building": ["", "LastLightInn"],
                "world_bbox_filter": (-200, 100, 100, 190),
                "cell": (0, 0, 1, 2),
            },
            {
                "title": "Defense",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_filter": (-100, 190, 0, 230),
                "cell": (1, 0, 1, 1),
            },
            {
                "title": "Meenlock Cave",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_filter": (-50, -720, 60, -670),
                "cell": (1, 1, 1, 1),
            },
        ],
    },
    # Creche: 39 chars cluster at the Githyanki creche proper around
    # x~1340, z~-790. Two outlier chars at (~75, ~41) live at the monastery
    # entry trigger (Githyanki patrol that ambushes the player). We skip
    # those for the creche map -- they're not part of the creche worldspace.
    "creche": {
        "level": "CRE_Main_A",
        "zones": [
            {
                "title": "Creche Y'llek",
                "char_building": [None],
                "patch_building": [""],
                "world_bbox_filter": (1100, -1100, 1500, -500),
            },
        ],
    },
}


# Most apworld kill entries are tagged `Kill-S_<level>_<creature>_<UUID>`
# (the S_ prefix is Larian's convention for spawned characters), but the
# Thaniel: Kill Mom / Kill Dad entries break the pattern -- they're
# `Kill-UNI_TWN_LiftingTheCurse_<Mom|Dad>Shadow_<UUID>`. Accept any prefix
# before the trailing UUID so future apworld additions in either style get
# picked up automatically.
KILL_RE = re.compile(r"^Kill-(.+?)_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def load_apworld_indexes(apworld: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Returns ({char_uuid: ap_location_name}, {ap_location_name: region_slug})."""
    src = (apworld / "bg3_locations.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    uuid_to_name: dict[str, str] = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.List)):
            continue
        for elt in n.value.elts:
            if not (isinstance(elt, ast.List) and len(elt.elts) >= 2):
                continue
            try:
                row = ast.literal_eval(elt)
            except Exception:
                continue
            if not (isinstance(row[0], str) and KILL_RE.match(row[0])):
                continue
            uid = KILL_RE.match(row[0]).group(2)
            rv = row[1]
            if isinstance(rv, list):
                if not rv:
                    continue
                name = rv[0]
            else:
                name = rv
            uuid_to_name.setdefault(uid, str(name))

    src2 = (apworld / "locationids.py").read_text(encoding="utf-8")
    tree2 = ast.parse(src2)
    name_to_region: dict[str, str] = {}
    for n in ast.walk(tree2):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "LOCATION_NAME_ID_REGION":
                    for ap_name, _lid, region in ast.literal_eval(n.value):
                        name_to_region[ap_name] = region
    return uuid_to_name, name_to_region


def matches_building(value: str | None, want: list) -> bool:
    """Building-tag matcher. `want` is a list of accepted values where
    None matches a char/patch with empty/missing building, and strings
    match the literal building UUID."""
    norm = value if value else None
    for w in want:
        if w is None and norm is None:
            return True
        if isinstance(w, str) and w == "":
            if norm is None:
                return True
        elif isinstance(w, str) and norm == w:
            return True
    return False


def patch_bbox(p: dict) -> tuple[float, float, float, float]:
    return (p["world_x"] - p["world_w"] / 2,
            p["world_z"] - p["world_h"] / 2,
            p["world_x"] + p["world_w"] / 2,
            p["world_z"] + p["world_h"] / 2)


def render_zone(zone_world_bbox: tuple[float, float, float, float],
                patches: list[dict], vt_index: dict, dds_cache: Path,
                canvas_size: tuple[int, int]) -> Image.Image:
    """Render the patches into a single-zone canvas.

    Pipeline per patch:
      1. Optionally crop the source DDS to a generous padded bbox before
         resize, to keep intermediate images bounded (mega-patches are
         500+ megapixels; resizing the full thing is wasteful and
         hole-fill is gigabytes of RAM).
      2. Hole-fill the (possibly-cropped) source.
      3. Resize to its full canvas-pixel footprint.
      4. Clip the *resized* image to the canvas-visible rectangle and
         alpha_composite at a non-negative TL.

    Step 4 is the key bit. alpha_composite silently drops blits whose
    destination TL lands at large negative coords -- which happens any
    time a patch extends well outside the zone bbox (e.g. CRE_Main_A's
    1135x1172 floor-0 backdrop). By clipping the resized image to the
    on-canvas portion and pasting at non-negative coords we get the
    visible part of every patch, regardless of how far it overflows.

    Step 1 keeps the resize-then-clip math from blowing up for those
    same mega-patches. We crop with a padding factor so the resized
    image still contains pixels just outside the bbox -- needed so
    detail patches that sit on the edge (e.g. monastery building tiles
    extending slightly past the char-derived bbox) don't get sheared.
    """
    x0, z0, x1, z1 = zone_world_bbox
    cw, ch = canvas_size
    world_w = x1 - x0
    world_h = z1 - z0
    # Uniform scale that fits zone bbox into canvas while preserving aspect.
    scale = min(cw / world_w, ch / world_h)
    # Center within the canvas.
    used_w = world_w * scale
    used_h = world_h * scale
    off_x = (cw - used_w) / 2
    off_y = (ch - used_h) / 2

    # World-space crop window for source-DDS pre-crop. Padded outward
    # from the canvas-visible region so the resized image still has the
    # surrounding tile context (matters for patches whose chars sit near
    # the bbox edges -- without padding the building or terrain gets
    # sheared right at the pin). The padding is intentionally larger
    # than just the canvas-overhang area; large enough that small detail
    # patches are never source-cropped at all.
    crop_pad_world = max(world_w, world_h) * 0.5
    crop_x0 = x0 - crop_pad_world
    crop_z0 = z0 - crop_pad_world
    crop_x1 = x1 + crop_pad_world
    crop_z1 = z1 + crop_pad_world

    canvas = Image.new("RGBA", (cw, ch), (24, 24, 30, 255))
    # Draw order: lower floors first, then higher floors, with exterior
    # (building="") patches drawn LAST on top of any same-floor interior
    # tiles. The exterior tile shows the natural worldmap view (roofs,
    # courtyard, terrain); interior patches are vertical-cutaway slices
    # of the building used for floor pickers. For a top-down overworld
    # map we want the exterior on top so the player sees the same shape
    # they see in the game's worldmap, not the floor-N interior layout.
    def sort_key(pp):
        is_exterior = 1 if not pp.get("building") else 0
        return (pp["floor"], is_exterior, pp["world_w"] * pp["world_h"])
    for p in sorted(patches, key=sort_key):
        gtex = vt_index.get(p["texture_uuid"], {}).get("gtex")
        if not gtex:
            continue
        dds = dds_cache / f"{gtex}_0.dds"
        if not dds.is_file():
            continue
        try:
            with Image.open(dds) as im:
                img = im.convert("RGBA")
        except Exception:
            continue

        p_sw_x = p["world_x"] - p["world_w"] / 2
        p_sw_z = p["world_z"] - p["world_h"] / 2
        p_ne_x = p["world_x"] + p["world_w"] / 2
        p_ne_z = p["world_z"] + p["world_h"] / 2

        # Source-DDS pre-crop window: intersection of the patch's bbox
        # with the padded zone bbox. Skip the patch entirely if it
        # doesn't reach the visible area even with the padding.
        ix0 = max(p_sw_x, crop_x0)
        iz0 = max(p_sw_z, crop_z0)
        ix1 = min(p_ne_x, crop_x1)
        iz1 = min(p_ne_z, crop_z1)
        if ix1 <= ix0 or iz1 <= iz0:
            continue

        # When the source crop covers ~the whole patch, skip the crop step
        # to keep the code path equivalent to the old "resize the whole
        # patch" behavior for small detail patches.
        sw, sh = img.size
        small_patch = (ix0 <= p_sw_x + 0.001 and iz0 <= p_sw_z + 0.001
                       and ix1 >= p_ne_x - 0.001 and iz1 >= p_ne_z - 0.001)
        if small_patch:
            piece = img
            piece_world_w = p["world_w"]
            piece_world_h = p["world_h"]
            piece_sw_x = p_sw_x
            piece_ne_z = p_ne_z
        else:
            # Crop the DDS to the padded-bbox intersection. DDS pixel-Y is
            # top-down, world Z is bottom-up: image y=0 maps to world z=p_ne_z.
            src_left   = int((ix0 - p_sw_x) / p["world_w"] * sw)
            src_right  = int((ix1 - p_sw_x) / p["world_w"] * sw)
            src_top    = int((p_ne_z - iz1) / p["world_h"] * sh)
            src_bottom = int((p_ne_z - iz0) / p["world_h"] * sh)
            src_left   = max(0, min(sw - 1, src_left))
            src_right  = max(src_left + 1, min(sw, src_right))
            src_top    = max(0, min(sh - 1, src_top))
            src_bottom = max(src_top + 1, min(sh, src_bottom))
            piece = img.crop((src_left, src_top, src_right, src_bottom))
            piece_world_w = ix1 - ix0
            piece_world_h = iz1 - iz0
            piece_sw_x = ix0
            piece_ne_z = iz1

        piece = fill_interior_holes(piece)

        pw = max(1, int(round(piece_world_w * scale)))
        ph = max(1, int(round(piece_world_h * scale)))
        piece = piece.resize((pw, ph), Image.LANCZOS)

        # Place the resized piece on the canvas. tlx/tly are the SW-x +
        # NE-z corner of the piece in canvas pixel coords (Y-flipped).
        tlx = off_x + (piece_sw_x - x0) * scale
        tly = off_y + used_h - (piece_ne_z - z0) * scale

        # Clip the resized piece to the canvas-visible rectangle so the
        # final blit lands at non-negative coords. alpha_composite drops
        # blits silently when its dest is negative, so we MUST do this
        # clip ourselves rather than passing through a negative TL.
        vx0 = max(0, int(round(tlx)))
        vy0 = max(0, int(round(tly)))
        vx1 = min(cw, int(round(tlx)) + pw)
        vy1 = min(ch, int(round(tly)) + ph)
        if vx1 <= vx0 or vy1 <= vy0:
            continue
        sx0 = vx0 - int(round(tlx))
        sy0 = vy0 - int(round(tly))
        sx1 = sx0 + (vx1 - vx0)
        sy1 = sy0 + (vy1 - vy0)
        visible = piece.crop((sx0, sy0, sx1, sy1))
        canvas.alpha_composite(visible, (vx0, vy0))
    return canvas, (off_x, off_y, scale)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("region", help="apworld region slug, e.g. goblin_camp")
    p.add_argument("--apworld", type=Path, required=True,
                   help="Path to the BG3 apworld checkout's worlds/bg3 dir.")
    p.add_argument("--dds-cache", type=Path,
                   default=Path(os.environ.get("BG3_TEXTURE_CACHE", "")
                                 or (TOOLS / "_texture_work" / "extracted")),
                   help="Path to the extracted Granite VT DDS root "
                        "(contains <hash>_0.dds files). Defaults to "
                        "tools/_texture_work/extracted/ or $BG3_TEXTURE_CACHE.")
    args = p.parse_args(argv)

    dds_cache: Path = args.dds_cache

    if args.region not in REGION_DEFINITIONS:
        print(f"[err] no zone definition for region {args.region!r}")
        return 1
    rd = REGION_DEFINITIONS[args.region]
    level = rd["level"]

    cache = json.loads((TOOLS / "bg3_npc_data.json").read_text(encoding="utf-8"))
    overrides = P.load_overrides(PACK)
    # Inject synthetic chars + apply position/building overrides on the
    # loaded cache. Source: tools/pin_overrides.json. In-memory mutations
    # only -- never written back to bg3_npc_data.json.
    P.apply_overrides_to_cache(cache, overrides, verbose=True)
    vt_index = json.loads((TOOLS / "vt_index.json").read_text(encoding="utf-8"))
    uuid_to_name, name_to_region = load_apworld_indexes(args.apworld)

    # Set of levels referenced by any zone (default level + per-zone overrides).
    needed_levels = {level} | {z.get("level", level) for z in rd["zones"]}

    # Collect chars in this region across all levels referenced by zones.
    # Zone-level filtering happens in the per-zone loop below.
    region_chars: list[tuple[str, dict]] = []  # (ap_name, char)
    for uuid, c in cache["characters"].items():
        if c.get("level") not in needed_levels or not c.get("position"):
            continue
        name = uuid_to_name.get(uuid)
        if name is None or name_to_region.get(name) != args.region:
            continue
        region_chars.append((name, c))
    print(f"[info] {args.region}: {len(region_chars)} kill chars across {sorted(needed_levels)}")

    zones_runtime = []
    for z in rd["zones"]:
        z_level = z.get("level", level)
        chars = [(n, c) for (n, c) in region_chars
                 if c.get("level") == z_level
                    and matches_building(c.get("building"), z["char_building"])]
        # Optional spatial filter for zones whose chars share a building tag
        # (e.g. all <ext>) but live in distinct world areas. Two clusters
        # of <ext> chars need bbox-based separation; building tag alone
        # isn't enough.
        bbox_filter = z.get("world_bbox_filter")
        if bbox_filter is not None:
            fx0, fz0, fx1, fz1 = bbox_filter
            chars = [(n, c) for (n, c) in chars
                     if fx0 <= c["position"][0] <= fx1
                        and fz0 <= c["position"][2] <= fz1]
        xs = [c["position"][0] for _, c in chars]
        zs = [c["position"][2] for _, c in chars]
        if not xs:
            print(f"[warn] zone {z['title']!r} has 0 chars; skipping")
            continue
        bbox_override = z.get("world_bbox_override")
        if bbox_override is not None:
            zone_bbox = tuple(bbox_override)
        else:
            x0, x1 = min(xs), max(xs)
            z0, z1 = min(zs), max(zs)
            pad_w = max(1.0, x1 - x0) * ZONE_PAD_FRAC
            pad_h = max(1.0, z1 - z0) * ZONE_PAD_FRAC
            zone_bbox = (x0 - pad_w, z0 - pad_h, x1 + pad_w, z1 + pad_h)

        patches = [pp for pp in cache["patches"][z_level]
                   if matches_building(pp["building"], z["patch_building"])]
        # Optional patch-floor filter. Use when two patches share the
        # same building tag and overlap spatially (e.g. Nautiloid's
        # floor-0 lower deck vs floor-1 helm, both tagged "Nautiloid"
        # and covering each other's xz area). Default = any floor.
        floor_filter = z.get("patch_floors")
        if floor_filter is not None:
            patches = [pp for pp in patches if pp["floor"] in floor_filter]
        # Optional explicit exclusion of specific patches by UUID -- for
        # patches that are out-of-date or otherwise should not render.
        skip_uuids = set(z.get("patch_skip_uuids", []) or [])
        if skip_uuids:
            patches = [pp for pp in patches if pp.get("uuid") not in skip_uuids]
        # Restrict to patches that overlap this zone's bbox.
        def overlaps(p, bb=zone_bbox):
            pbb = patch_bbox(p)
            return not (pbb[2] < bb[0] or pbb[0] > bb[2] or pbb[3] < bb[1] or pbb[1] > bb[3])
        patches = [pp for pp in patches if overlaps(pp)]
        print(f"  zone {z['title']:20s} {len(chars):>3d} chars, {len(patches):>3d} patches, "
              f"bbox x[{zone_bbox[0]:.0f},{zone_bbox[2]:.0f}] z[{zone_bbox[1]:.0f},{zone_bbox[3]:.0f}]")
        zones_runtime.append({"def": z, "chars": chars, "patches": patches, "bbox": zone_bbox})

    if not zones_runtime:
        print(f"[err] no zones with chars")
        return 1

    # Layout: zones placed in a grid above a bottom quest panel. If a zone
    # carries a `cell` tuple it's (col, row, colspan, rowspan); otherwise
    # it gets the default 1-zone-per-column row=0 layout (preserves the
    # behavior of goblin_camp / grymforge / blighted_village / creche
    # which haven't migrated to cells). Grid dimensions = max extent
    # across all zones' cells.
    quest_h = int(round(CANVAS_H * QUEST_PANEL_FRAC))
    zones_area_h = CANVAS_H - quest_h
    final = Image.new("RGBA", (CANVAS_W, CANVAS_H), (18, 18, 22, 255))
    final_draw = ImageDraw.Draw(final)

    # Resolve each zone's cell, then derive the grid dimensions.
    resolved_cells: list[tuple[int, int, int, int]] = []
    for i, zr in enumerate(zones_runtime):
        cell = zr["def"].get("cell")
        if cell is None:
            cell = (i, 0, 1, 1)
        resolved_cells.append(tuple(cell))
    n_cols = max(c[0] + c[2] for c in resolved_cells)
    n_rows = max(c[1] + c[3] for c in resolved_cells)
    # Per-cell pixel sizes (equal split with gaps between cells).
    cell_w = (CANVAS_W - (n_cols - 1) * ZONE_GAP_PX) // n_cols
    cell_h = (zones_area_h - (n_rows - 1) * ZONE_GAP_PX) // n_rows

    # Per-zone projection metadata for projections.json. Each zone records
    # the world bbox actually rendered (post auto-compute / override) and
    # the canvas rect it occupies. Pack-time pin computation re-projects
    # chars through the same math.
    zone_projs: list[dict] = []

    for zr, (col, row, colspan, rowspan) in zip(zones_runtime, resolved_cells):
        origin_x = col * (cell_w + ZONE_GAP_PX)
        origin_y = row * (cell_h + ZONE_GAP_PX) + ZONE_LABEL_HEIGHT
        zone_w_px = cell_w * colspan + (colspan - 1) * ZONE_GAP_PX
        zone_h_px = cell_h * rowspan + (rowspan - 1) * ZONE_GAP_PX - ZONE_LABEL_HEIGHT

        sub_canvas, _off_xy_scale = render_zone(
            zr["bbox"], zr["patches"], vt_index, dds_cache, (zone_w_px, zone_h_px),
        )
        final.alpha_composite(sub_canvas, (origin_x, origin_y))

        # Label bar sits at the cell's top edge.
        label_top = row * (cell_h + ZONE_GAP_PX)
        final_draw.rectangle(
            (origin_x, label_top, origin_x + zone_w_px - 1,
             label_top + ZONE_LABEL_HEIGHT - 1),
            fill=(40, 40, 50, 255),
        )
        final_draw.text((origin_x + 4, label_top + 1), zr["def"]["title"],
                        fill=(255, 255, 255, 255))

        zone_level = zr["def"].get("level", level)
        zone_proj: dict = {
            "title": zr["def"]["title"],
            "level": zone_level,
            "char_building": list(zr["def"]["char_building"]),
            "patch_building": list(zr["def"]["patch_building"]),
            "world_bbox": [float(v) for v in zr["bbox"]],
            "canvas_rect": [origin_x, origin_y, zone_w_px, zone_h_px],
        }
        if zr["def"].get("patch_floors") is not None:
            zone_proj["patch_floors"] = list(zr["def"]["patch_floors"])
        # world_bbox_filter restricts which chars route to this zone when
        # multiple zones share the same (level, char_building) -- e.g.
        # last_light's three zones all accept building=null but live at
        # different z positions on SCL_Main_A. Pin computation needs this
        # to disambiguate, so persist it.
        if zr["def"].get("world_bbox_filter") is not None:
            zone_proj["world_bbox_filter"] = [
                float(v) for v in zr["def"]["world_bbox_filter"]
            ]
        zone_projs.append(zone_proj)

    # Paint the quest panel strip at the bottom of the canvas.
    quest_top = CANVAS_H - quest_h
    final_draw.rectangle((0, quest_top, CANVAS_W - 1, CANVAS_H - 1),
                         fill=(28, 24, 24, 255))
    final_draw.line((0, quest_top, CANVAS_W - 1, quest_top),
                    fill=(255, 255, 255, 200), width=1)
    final_draw.text((10, quest_top + 6), "Quests",
                    fill=(255, 255, 255, 220))

    out_png = PACK / "images" / "maps" / f"{args.region}.png"
    final.save(out_png)
    print(f"[done] wrote {out_png}")

    region_proj = {
        "canvas": [CANVAS_W, CANVAS_H],
        "zones": zone_projs,
    }
    proj_path = P.upsert_region_projection(PACK, args.region, region_proj)
    print(f"[done] updated {proj_path} ({len(zone_projs)} zones in '{args.region}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
