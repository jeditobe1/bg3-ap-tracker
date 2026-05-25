"""Generate the BG3 PopTracker pack from the apworld source of truth.

Reads `LOCATION_NAME_ID_REGION` from the apworld's locationids.py and
`UserDefinedFights.valid_keys` from options.py via AST (no apworld imports
needed) and emits the JSON, Lua, and PNG artifacts the pack consumes. The
hand-authored files (manifest.json, layouts/standard.json, scripts/init.lua,
scripts/autotracking.lua, items/items.json, the item icons) are left untouched;
the generator owns everything under:

  - locations/locations.json
  - maps/maps.json
  - layouts/regions.json
  - scripts/autotracking_generated.lua
  - images/maps/*.png

CLI:
    python tools/generate_pack.py --apworld <path-to-apworld>/worlds/bg3 [--check]

The --check flag validates the apworld data and emits nothing.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Slug -> human-readable display name. Slugs come from the apworld's
# LOCATION_NAME_ID_REGION. Display names are first-pass; P6 will replace with
# maintainer-confirmed canonical names.
REGION_DISPLAY_NAMES: dict[str, str] = {
    "tutorial":         "Nautiloid",
    "beach":            "Ravaged Beach",
    "crypt":            "Dank Crypt",
    "grove":            "Druid Grove",
    "blighted_village": "Blighted Village",
    "goblin_camp":      "Goblin Camp",
    "waukeen":          "Waukeen's Rest",
    "hag":              "Riverside",
    "underdark":        "Underdark",
    "grymforge":        "Grymforge",
    "monastery":        "Rosymorn Monastery",
    "creche":           "Creche Y'llek",
    "east_act2":        "Shadow-Cursed Lands (East)",
    "west_act2":        "Shadow-Cursed Lands (West)",
    "last_light":       "Last Light Inn",
    "moonrise":         "Moonrise Towers",
    "shar_gauntlet":    "Gauntlet of Shar",
    "mindflayer":       "Mindflayer Colony",
}

# Region tab groupings by act. The outer regions_block tabbed layout uses
# these as its top-level tabs ("Prologue", "Act 1", ...), with the slugs
# of each act becoming the inner per-region tabs. Order within each list
# is the inner tab order.
#
# Act 3 has no entries yet -- apworld regions.py:160-165 has them commented
# out (design doc P8). We keep the tab so the structure is stable when
# Act 3 lands; the stub layout below renders an explanatory message.
ACT_GROUPS: dict[str, list[str]] = {
    "Prologue": ["tutorial"],
    "Act 1": [
        "beach", "crypt", "grove", "blighted_village", "goblin_camp",
        "waukeen", "hag", "underdark", "grymforge", "monastery", "creche",
    ],
    "Act 2": [
        "east_act2", "west_act2", "last_light", "moonrise",
        "shar_gauntlet", "mindflayer",
    ],
    "Act 3": [],
}

# Overworld maps -- composited from BG3 WorldMap patches via
# texture_bank_scratch/build_overworld.py. These are pin-less backdrop maps
# that go at the front of each act's tab list to give the player an "where
# am I in the world" anchor before drilling into a specific region.
# Each entry is (act_name, map_slug, display_name).
ACT_OVERWORLDS: list[tuple[str, str, str]] = [
    ("Act 1", "act1_overworld", "Overview"),
    ("Act 2", "act2_overworld", "Overview"),
]

# Flattened in act order for data-emitting passes (locations.json, maps.json,
# autotracker section codes) that don't care about the act hierarchy. Kept
# derived rather than hand-maintained so adding a region only requires one
# edit (in ACT_GROUPS).
REGION_ORDER: list[str] = [slug for slugs in ACT_GROUPS.values() for slug in slugs]

# Cumulative Level Fragment count required to enter each region, derived from
# regions.py:98-157 by taking the minimum count over all paths into the region.
# Goal-conditional gates from regions.py are NOT encoded here -- pool-membership
# filtering in Lua handles those (Act 2 region IDs aren't in a Halsin slot's pool).
# Statsanity gates on Hag are ignored (option unsupported, treated as off).
# Which Goal stage codes (from items.json Goal progressive) allow this region
# to be visible. Tutorial + Act 1 base regions are always visible (any goal);
# omitting a region from this map means "no goal restriction".
# Apworld reference: locations.py:115-187 region inclusion by goal.
REGION_GOAL_VISIBILITY: dict[str, list[str]] = {
    # Act 1 underdark/mountain cluster: visible for goals other than Halsin.
    "underdark":     ["goal_wwargaz", "goal_act1udf", "goal_myrkul", "goal_act2udf"],
    "grymforge":     ["goal_wwargaz", "goal_act1udf", "goal_myrkul", "goal_act2udf"],
    "monastery":     ["goal_wwargaz", "goal_act1udf", "goal_myrkul", "goal_act2udf"],
    "creche":        ["goal_wwargaz", "goal_act1udf", "goal_myrkul", "goal_act2udf"],
    # Act 2: visible only for Act 2-tier goals.
    "east_act2":     ["goal_myrkul", "goal_act2udf"],
    "west_act2":     ["goal_myrkul", "goal_act2udf"],
    "last_light":    ["goal_myrkul", "goal_act2udf"],
    "moonrise":      ["goal_myrkul", "goal_act2udf"],
    "shar_gauntlet": ["goal_myrkul", "goal_act2udf"],
    "mindflayer":    ["goal_myrkul", "goal_act2udf"],
}

REGION_ACCESS_GATE: dict[str, int] = {
    "tutorial":          0,
    "beach":             1,
    "crypt":             1,
    "grove":             3,
    "blighted_village":  3,
    "waukeen":           6,
    "goblin_camp":       8,
    "hag":              10,
    "underdark":        10,
    "grymforge":        14,
    "monastery":        18,
    "creche":           18,
    "east_act2":        22,
    "west_act2":        26,
    "last_light":       26,
    "moonrise":         26,
    "shar_gauntlet":    26,
    "mindflayer":       30,
}

# Hand-curated item ID -> tracker code mapping for toggle-type items.
# Consumables (Level Fragment, stat boosts, equipment, filler, traps) are resolved
# at handler time via integer-range logic in autotracking.lua; only toggles need
# explicit entries here.
AP_ITEM_TOGGLE_IDS: dict[int, str] = {
    2: "boots_of_speed",
    3: "shadow_lantern",
    4: "spear_of_night",
}

# Regions that belong to Act 1 vs Act 2 for the UDF goal-progress block.
# Apworld semantics: Act 1 UDF requires only the Act 1 fights; Act 2 UDF
# requires all 16 fights (Act 1 + Act 2).
ACT1_REGIONS: set[str] = {
    "tutorial", "beach", "crypt", "grove", "blighted_village", "goblin_camp",
    "waukeen", "hag", "underdark", "grymforge", "monastery", "creche",
}
ACT2_REGIONS: set[str] = {
    "east_act2", "west_act2", "last_light", "moonrise", "shar_gauntlet", "mindflayer",
}

# Map dimensions for the per-region placeholder PNGs. Fixed across regions so
# kill pins projected from BG3 world coordinates (via tools/npc_pins.json) and
# quest pins on the fallback grid share a consistent canvas.
MAP_WIDTH = 480
MAP_HEIGHT = 320
LABEL_HEIGHT = 28       # px reserved at the top for the region name
PADDING = 16            # px border around grid pins
PIN_SLOT = 28           # px per pin in the fallback grid

# Quest pins (id < 10000) lack BG3 character UUIDs so they can't be projected;
# they pack into a strip across the bottom of the map ("quest panel" area).
# Kill pins (id >= 10000) with a projection take precedence; un-projected
# kills fall back to a grid in the top-left corner.
QUEST_GRID_COLS = 8
QUEST_PANEL_HEIGHT_FRAC = 0.20   # bottom 20% of the map is the quest panel

# Per-map pixel dimensions. Maps not listed here use (MAP_WIDTH, MAP_HEIGHT).
# Multi-zone composites are 960x640; the composer in
# texture_bank_scratch/build_multi_zone_region.py emits at that resolution.
MAP_DIMS_OVERRIDES: dict[str, tuple[int, int]] = {
    "tutorial": (960, 640),
    "goblin_camp": (960, 640),
    "blighted_village": (960, 640),
    "grymforge": (960, 640),
    "monastery": (960, 640),
    "creche": (960, 640),
    # Act 2 regions are all built by build_multi_zone_region.py at the
    # 960x640 multi-zone canvas, even the single-zone ones.
    "last_light": (960, 640),
    "east_act2": (960, 640),
    "mindflayer": (960, 640),
    "shar_gauntlet": (960, 640),
    "moonrise": (960, 640),
    "west_act2": (960, 640),
}

# Per-map location_size override. Higher-resolution maps get larger pin
# sizes so pins look the same on screen across resolutions.
MAP_SIZE_OVERRIDES: dict[str, int] = {
    "tutorial": 16,
    "goblin_camp": 16,
    "blighted_village": 16,
    "grymforge": 16,
    "monastery": 16,
    "creche": 16,
    "last_light": 16,
    "east_act2": 16,
    "mindflayer": 16,
    "shar_gauntlet": 16,
    "moonrise": 16,
    "west_act2": 16,
}

# Distinct-ish background colors per region. Cycled deterministically through
# REGION_ORDER so neighboring tabs look different. Tones lean dark so white text
# is readable.
REGION_PALETTE: list[tuple[int, int, int]] = [
    (58,  42,  32),   # tutorial      - dark brown
    (40,  62,  78),   # beach         - dusk blue
    (45,  34,  52),   # crypt         - violet
    (44,  72,  46),   # grove         - moss
    (84,  64,  44),   # blighted_v    - warm tan
    (72,  44,  40),   # goblin_camp   - dried blood
    (60,  58,  44),   # waukeen       - olive
    (52,  64,  72),   # hag           - swamp slate
    (32,  44,  56),   # underdark     - deep navy
    (56,  48,  44),   # grymforge     - forge stone
    (66,  62,  56),   # monastery     - sandstone
    (44,  56,  68),   # creche        - cool steel
    (38,  48,  44),   # east_act2     - shadow green
    (44,  38,  48),   # west_act2     - shadow purple
    (60,  56,  72),   # last_light    - lantern violet
    (54,  44,  60),   # moonrise      - bruised purple
    (40,  42,  52),   # shar_gauntlet - night
    (62,  46,  56),   # mindflayer    - eldritch mauve
]


def parse_location_name_id_region(apworld_path: Path) -> list[tuple[str, int, str]]:
    """AST-parse LOCATION_NAME_ID_REGION from locationids.py. Returns list of (name, id, slug)."""
    src = (apworld_path / "locationids.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "LOCATION_NAME_ID_REGION":
                    raw = ast.literal_eval(node.value)
                    return [(entry[0], int(entry[1]), entry[2]) for entry in raw]
    raise RuntimeError("LOCATION_NAME_ID_REGION not found in locationids.py")


def parse_equipment_rarities(apworld_path: Path) -> dict[int, int]:
    """AST-parse the EQUIPMENT list from equipment.py and return AP item id -> rarity tier.

    The apworld assigns AP IDs to equipment via items.py:70 as
    `index + 1000` over the EQUIPMENT list, so AP id 1000 = EQUIPMENT[0],
    1001 = EQUIPMENT[1], and so on. Each EQUIPMENT entry is
    [name, uuid, rarity_tier]; we only need the rarity_tier here (0..3).
    """
    src = (apworld_path / "equipment.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "EQUIPMENT":
                    raw = ast.literal_eval(node.value)
                    return {1000 + idx: int(entry[2]) for idx, entry in enumerate(raw)}
    raise RuntimeError("EQUIPMENT not found in equipment.py")


def parse_user_defined_fights(apworld_path: Path) -> list[str]:
    """AST-parse UserDefinedFights.valid_keys from options.py.
    Returns the ordered list of fight names."""
    src = (apworld_path / "options.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "UserDefinedFights":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "valid_keys":
                            return list(ast.literal_eval(stmt.value))
    raise RuntimeError("UserDefinedFights.valid_keys not found in options.py")


def resolve_udf_to_locations(
    fights: list[str],
    locations: list[tuple[str, int, str]],
) -> list[tuple[str, str, int, str]]:
    """Match each UDF fight name to its AP kill-location entry.

    Returns list of (fight_name, ap_location_name, ap_location_id, region_slug),
    in the same order as `fights`.

    Match rule: the location's AP id must be >= 10000 (kill-range) AND the
    location name must end with the fight name, optionally preceded by extra
    words after "Kill " (e.g. "Village: Kill Well Spider Queen" matches the
    fight "Spider Queen"). Exactly one match per fight is required; ambiguous
    or unmatched fights raise a hard error so the apworld and the tracker
    don't silently drift apart.
    """
    resolved: list[tuple[str, str, int, str]] = []
    for fight in fights:
        pattern = re.compile(
            r":\s*(?:Kill|Defeat)\s+(?:.* )?" + re.escape(fight) + r"\s*$"
        )
        matches = [
            (name, lid, slug)
            for name, lid, slug in locations
            if lid >= 10000 and pattern.search(name)
        ]
        if len(matches) == 0:
            raise RuntimeError(f"UDF fight {fight!r}: no matching AP kill location")
        if len(matches) > 1:
            raise RuntimeError(
                f"UDF fight {fight!r}: ambiguous matches "
                + ", ".join(f"{n!r} (id={i})" for n, i, _ in matches)
            )
        name, lid, slug = matches[0]
        resolved.append((fight, name, lid, slug))
    return resolved


def validate(locations: list[tuple[str, int, str]]) -> list[str]:
    """Returns a list of error strings. Empty list = OK.

    Note: name collisions within a region are NOT errors — the apworld legitimately
    has duplicate names (e.g. a boss kill that is both a quest-step check <10000 and
    a kill check >=10000). The generator disambiguates them via #2/#3 suffixes when
    emitting section codes.
    """
    errors: list[str] = []
    seen_ids: dict[int, str] = {}

    for name, lid, slug in locations:
        if not name:
            errors.append(f"empty location name for id {lid}")
        if slug not in REGION_DISPLAY_NAMES:
            errors.append(f"unmapped region slug {slug!r} (location {name!r} id={lid}). "
                          f"Add to REGION_DISPLAY_NAMES or update REGION_ORDER.")
        if lid in seen_ids:
            errors.append(f"duplicate ID {lid}: {seen_ids[lid]!r} vs {name!r}")
        seen_ids[lid] = name
        if lid <= 0:
            errors.append(f"non-positive ID {lid} for {name!r}")

    # Every region we order must have data (else the empty tab is confusing).
    by_region: dict[str, int] = {}
    for _, _, slug in locations:
        by_region[slug] = by_region.get(slug, 0) + 1
    for slug in REGION_ORDER:
        if slug not in by_region:
            errors.append(f"region {slug!r} is in REGION_ORDER but has no locations")
    # Slugs that exist in the data but aren't in REGION_ORDER are warnings, not errors.
    for slug in by_region:
        if slug not in REGION_ORDER:
            errors.append(f"region {slug!r} has data but isn't in REGION_ORDER (would not display)")
    return errors


def group_by_region(locations: list[tuple[str, int, str]]) -> dict[str, list[tuple[str, int]]]:
    """Group locations by region slug, preserving input order. Disambiguates
    duplicate names within a region by appending ' #2'/'#3'/... to repeats."""
    by_region: dict[str, list[tuple[str, int]]] = {slug: [] for slug in REGION_ORDER}
    seen_per_region: dict[str, dict[str, int]] = {}
    for name, lid, slug in locations:
        counts = seen_per_region.setdefault(slug, {})
        counts[name] = counts.get(name, 0) + 1
        display = name if counts[name] == 1 else f"{name} #{counts[name]}"
        by_region.setdefault(slug, []).append((display, lid))
    return by_region


def grid_pin_position(idx: int) -> tuple[int, int]:
    """Top-left grid coord for an un-projected kill fallback."""
    col = idx % QUEST_GRID_COLS
    row = idx // QUEST_GRID_COLS
    x = PADDING + col * PIN_SLOT + PIN_SLOT // 2
    y = PADDING + LABEL_HEIGHT + row * PIN_SLOT + PIN_SLOT // 2
    return (x, y)


def quest_grid_pin_position(idx: int, map_dims: tuple[int, int],
                            location_size: int) -> tuple[int, int]:
    """Pin coord for a quest, placed in the bottom strip ("quest panel") of
    the map. Slot size = 2 * location_size so pins sit just-not-touching;
    grid is sized to fit a few rows max within QUEST_PANEL_HEIGHT_FRAC of
    the canvas height (we don't auto-grow the panel per region so densest
    region's worth of pins is the budget every region gets).

    Pins flow row-major across the panel. Wider maps get more columns
    automatically; pin density looks consistent across resolutions because
    slot scales with the pin size.
    """
    w, h = map_dims
    panel_h = int(round(h * QUEST_PANEL_HEIGHT_FRAC))
    panel_top = h - panel_h
    slot = max(12, location_size * 2)
    pad = max(6, slot // 2)
    cols = max(4, (w - 2 * pad) // slot)
    col = idx % cols
    row = idx // cols
    x = pad + col * slot + slot // 2
    y = panel_top + pad // 2 + row * slot + slot // 2
    return (x, y)


def load_npc_pins(pack_root: Path) -> dict[str, dict[str, dict]]:
    """Load tools/npc_pins.json if present. Returns {region_slug: {ap_name: {x,y,level}}}.
    Returns empty dict if the projection file is missing (soft-fail: every pin
    falls back to the grid layout)."""
    pins_path = pack_root / "tools" / "npc_pins.json"
    if not pins_path.exists():
        return {}
    try:
        data = json.loads(pins_path.read_text(encoding="utf-8"))
        return data.get("pins", {})
    except Exception as e:
        print(f"[warn] failed to load npc_pins.json: {e}")
        return {}


def load_overworld_pins(pack_root: Path) -> dict[str, dict]:
    """Load tools/overworld_pins.json if present. Returns
    {overworld_slug: {map_dims, world_bbox, regions: {region_slug: {x,y,...}}}}.
    Emitted by texture_bank_scratch/build_overworld.py --overworld-slug X."""
    p = pack_root / "tools" / "overworld_pins.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("overworlds", {})
    except Exception as e:
        print(f"[warn] failed to load overworld_pins.json: {e}")
        return {}


# Reverse-map ACT_OVERWORLDS so we can look up "act1_overworld" for a given
# region without iterating. {region_slug: overworld_slug}.
def _act_overworld_for_region() -> dict[str, str]:
    out = {}
    for act_name, slugs in ACT_GROUPS.items():
        ov = next((o for a, o, _ in ACT_OVERWORLDS if a == act_name), None)
        if ov:
            for s in slugs:
                out[s] = ov
    return out


def render_map_png(slug: str, color: tuple[int, int, int], out: Path,
                   force: bool = False) -> None:
    """Write a placeholder PNG: solid colored background with the region name
    in the top label band and a darker quest-panel strip at the bottom so
    the quest pins have a visible separator. Pins are rendered by PopTracker
    itself from map_locations -- this image is just the backdrop.

    Leaves existing PNGs alone by default (composited textures shouldn't be
    overwritten on regen). Pass force=True to redraw all placeholders.
    """
    if out.is_file() and not force:
        return
    w, h = MAP_DIMS_OVERRIDES.get(slug, (MAP_WIDTH, MAP_HEIGHT))
    img = Image.new("RGBA", (w, h), color + (255,))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        font_panel = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
        font_panel = font
    # Region name in the top label band.
    label = REGION_DISPLAY_NAMES[slug]
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((w - tw) // 2, (LABEL_HEIGHT - th) // 2), label,
              fill=(255, 255, 255, 255), font=font)
    # Quest panel: darker strip across the bottom with a "Quests" caption.
    panel_h = int(round(h * QUEST_PANEL_HEIGHT_FRAC))
    panel_top = h - panel_h
    panel_color = tuple(max(0, c - 30) for c in color) + (255,)
    draw.rectangle((0, panel_top, w - 1, h - 1), fill=panel_color)
    draw.line((0, panel_top, w - 1, panel_top),
              fill=(255, 255, 255, 200), width=1)
    draw.text((6, panel_top + 4), "Quests",
              fill=(255, 255, 255, 220), font=font_panel)
    img.save(out)


def sanitize_for_code_path(name: str) -> str:
    """PopTracker uses '/' as the separator in section codes (@location/section).
    Replace any literal '/' in AP location names with ' - ' so the code path
    stays unambiguous. Other characters are fine."""
    return name.replace("/", " - ")


def emit_locations_json(
    by_region: dict[str, list[tuple[str, int]]],
    npc_pins: dict[str, dict[str, dict]],
    overworld_pins: dict[str, dict] | None = None,
) -> list:
    """Return the locations.json payload as a Python list.

    Structure: one top-level entry per region (grouping only). Each region has
    `children`, one per AP location. Each child carries the `map_locations`
    pin and a SINGLE section whose name equals the AP location name. The
    duplicate-name pattern (location name == section name) matches what ALttP
    and other AP packs do, and gives UT the exact-match leaf section name it
    needs to auto-route checks without a `poptracker_name_mapping` dict.

    Why not `item_count: 1` directly on the child: PopTracker requires the
    `sections` array to render any checkable items; `item_count` is a section
    attribute, not a location attribute. Putting it on the location silently
    produces zero checks on the map -- spent an evening rediscovering that.

    Section codes resolved at runtime: `@<region-display>/<ap-name>/<ap-name>`.

    Pin coordinates:
    - Kill locations (id >= 10000) use real BG3 NPC positions from
      tools/npc_pins.json where available, emitted by the scratch-side
      build_multi_zone_region.py / build_region_map.py compositors.
    - Quest locations (id < 10000) project into the bottom quest-panel
      strip on the same per-region map.
    - Un-projected kills fall back to a grid layout (rare in practice).
    """
    overworld_pins = overworld_pins or {}
    region_to_overworld = _act_overworld_for_region()
    payload = []
    for slug in REGION_ORDER:
        entries = by_region.get(slug, [])
        display = REGION_DISPLAY_NAMES[slug]
        region_pins = npc_pins.get(slug, {})
        map_dims = MAP_DIMS_OVERRIDES.get(slug, (MAP_WIDTH, MAP_HEIGHT))
        map_size = MAP_SIZE_OVERRIDES.get(slug, 8)
        children = []
        kill_grid_idx = 0
        quest_grid_idx = 0
        for name, lid in entries:
            safe_name = sanitize_for_code_path(name)
            # Toggle-driven visibility: each AP location's ID range determines
            # which sanity toggle controls its visibility.
            #  - IDs < 10000 are quest checkpoints; gated by the Questsanity toggle.
            #  - IDs >= 10000 are creature kill checks; gated by the Killsanity toggle.
            sanity_code = "questsanity" if lid < 10000 else "killsanity"
            section: dict = {"name": safe_name}
            child: dict = {
                "name": safe_name,
                "sections": [section],
                "visibility_rules": [sanity_code],
            }
            if lid >= 10000:
                # Kill -> area map at the NPC's real position (or fallback grid).
                # group_by_region appends ' #2'/'#3' to disambiguate duplicate
                # display names within a region; the npc_pins.json is keyed by
                # the base AP name, so strip the suffix before lookup.
                base_name = re.sub(r" #\d+$", "", name)
                projection = region_pins.get(name) or region_pins.get(base_name)
                if projection is not None:
                    x, y = projection["x"], projection["y"]
                else:
                    x, y = grid_pin_position(kill_grid_idx)
                    kill_grid_idx += 1
            else:
                # Quest -> same map, gridded into the bottom "quest panel"
                # strip so quests live next to the area view without
                # crowding the texture.
                x, y = quest_grid_pin_position(quest_grid_idx, map_dims, map_size)
                quest_grid_idx += 1
                section["chest_unopened_img"] = "images/items/quest_marker_unopened.png"
                section["chest_opened_img"] = "images/items/quest_marker_opened.png"
            child["map_locations"] = [{"map": slug, "x": x, "y": y}]
            children.append(child)
        region_entry: dict = {
            "name": display,
            "children": children,
        }
        # If this region has a centroid on its act's overworld map, add a
        # synthetic "Overview" child that carries the overworld pin and
        # refs all sibling checks. We deliberately do NOT put map_locations
        # + sections directly on the parent grouping node: Universal
        # Tracker's location walker uses an if/elif at TrackerClient.py
        # load_pack (a node with both map_locations and children gets
        # appended to map_locs and its children are silently skipped), so
        # the per-region map (beach/crypt/etc.) loses all its kill+quest
        # pins. Nautiloid is the only region currently rendering correctly
        # in UT because it has no overworld centroid and therefore no
        # parent-level map_locations to trigger the bug. The synthetic
        # child sidesteps it -- parent stays a pure grouping node and the
        # overworld pin lives on a node that has map_locations but no
        # children of its own, which both PopTracker and UT walk
        # correctly.
        ov_slug = region_to_overworld.get(slug)
        if ov_slug and ov_slug in overworld_pins:
            ov_data = overworld_pins[ov_slug]
            reg_coords = ov_data.get("regions", {}).get(slug)
            if reg_coords:
                overview_child = {
                    "name": f"{display} (Overview)",
                    # Refs to each sibling check so clicking the overview
                    # pin still pops up the full check list. Each ref
                    # needs an explicit `name` so PopTracker can
                    # distinguish them -- without it every ref registers
                    # under the parent's path with an empty name,
                    # producing "duplicate location section" warnings at
                    # load (one per ref beyond the first). BT/ALttP
                    # reference packs follow the same `{ref, name}`
                    # pattern.
                    "sections": [
                        {"ref": f"{display}/{child['name']}/{child['name']}",
                         "name": child["name"]}
                        for child in children
                    ],
                    "map_locations": [{
                        "map": ov_slug,
                        "x": reg_coords["x"],
                        "y": reg_coords["y"],
                    }],
                }
                children.insert(0, overview_child)
        gate = REGION_ACCESS_GATE.get(slug, 0)
        if gate > 0:
            # Children inherit parent access_rules; PopTracker auto-recomputes
            # reachability when the player's level_fragment count crosses the
            # threshold.
            region_entry["access_rules"] = [f"level_fragment:{gate}"]
        goals = REGION_GOAL_VISIBILITY.get(slug)
        if goals:
            # OR-list: visible if any of these Goal stage codes is currently
            # provided (i.e. the Goal item is at one of those stages).
            region_entry["visibility_rules"] = list(goals)
        payload.append(region_entry)
    return payload


def emit_maps_json(pack_root: Path) -> list:
    """Both per-region maps, per-region quest maps, and act-level overworld
    maps live in maps.json. Overworld maps have no checks of their own;
    they're navigation backdrops. Quest maps host the gridded quest pins
    so the area maps stay uncluttered.
    """
    # PopTracker renders pins at `location_size` pixels in canvas-pixel
    # space, then scales the canvas to fit the viewport. A bigger canvas
    # shrinks more on-screen, making same-`location_size` pins look
    # smaller. Derive location_size from the actual PNG height so pins
    # stay visually consistent across maps: ref = multi-zone submap
    # height 640 px -> location_size 16 (1/40 of height). Apply same
    # ratio to overworlds.
    SIZE_PER_PX = 1.0 / 40.0
    def derive_size(slug: str, fallback: int) -> int:
        png = pack_root / "images" / "maps" / f"{slug}.png"
        if not png.is_file():
            return fallback
        try:
            with Image.open(png) as im:
                h = im.size[1]
            return max(8, int(round(h * SIZE_PER_PX)))
        except Exception:
            return fallback

    region_maps = [
        {
            "name": slug,
            "location_size": MAP_SIZE_OVERRIDES.get(slug, 8),
            "location_border_thickness": 2,
            "img": f"images/maps/{slug}.png",
        }
        for slug in REGION_ORDER
    ]
    # Border thickness scales with location_size so the visible border
    # ratio stays roughly consistent across maps. Reference: submaps
    # with size=16 get border=2 (12.5%); overworlds at size=50/73 need
    # border=6/9 to keep the same ratio after PopTracker fits the
    # bigger canvas to the viewport.
    overworld_maps = []
    for _, slug, _ in ACT_OVERWORLDS:
        size = derive_size(slug, 32)
        overworld_maps.append({
            "name": slug,
            "location_size": size,
            "location_border_thickness": max(2, size // 8),
            "img": f"images/maps/{slug}.png",
        })
    return region_maps + overworld_maps


def udf_toggle_code(fight: str) -> str:
    """Stable tracker code for a UDF fight toggle item.
    Lowercase, alphanumerics + underscore only, prefixed with 'udf_'."""
    slug = re.sub(r"[^a-z0-9]+", "_", fight.lower()).strip("_")
    return f"udf_{slug}"


def emit_regions_layout() -> dict:
    """Nested tabbed layout: outer tabs = acts, inner tabs = regions in that act.

    Empty acts (no apworld regions yet, e.g. Act 3) get a `group` stub with
    a header. PopTracker has no plain text widget, so an empty-content group
    is the simplest way to communicate "tab exists but nothing in it yet".
    """
    # Pre-index overworld slug per act so we can prepend the Overview tab.
    overworld_by_act = {act: (slug, title) for act, slug, title in ACT_OVERWORLDS}

    outer_tabs = []
    for act_name, slugs in ACT_GROUPS.items():
        if slugs:
            inner_tabs = []
            ov = overworld_by_act.get(act_name)
            if ov is not None:
                ov_slug, ov_title = ov
                inner_tabs.append({
                    "title": ov_title,
                    "content": {"type": "map", "maps": [ov_slug]},
                })
            # Per region: a single map widget. The map PNG is composited
            # with an area section (kill pins on real texture) PLUS a
            # quest panel section (quest pins on a labeled grid) baked
            # into one image -- single map keeps it compatible with UT
            # (which only renders one map per tab).
            for slug in slugs:
                inner_tabs.append({
                    "title": REGION_DISPLAY_NAMES[slug],
                    "content": {"type": "map", "maps": [slug]},
                })
            content = {"type": "tabbed", "tabs": inner_tabs}
        else:
            content = {
                "type": "group",
                "header": f"{act_name} regions are not yet supported by the BG3 apworld.",
            }
        outer_tabs.append({"title": act_name, "content": content})
    return {
        "regions_block": {
            "type": "tabbed",
            "tabs": outer_tabs,
        }
    }


RARITY_CODES = {
    0: "equipment_common",
    1: "equipment_uncommon",
    2: "equipment_rare",
    3: "equipment_very_rare",
}


def emit_autotracking_generated(
    by_region: dict[str, list[tuple[str, int]]],
    udf_resolved: list[tuple[str, str, int, str]],
    equipment_rarities: dict[int, int],
) -> str:
    """Return the contents of scripts/autotracking_generated.lua as a string."""
    lines: list[str] = [
        "-- AUTO-GENERATED by tools/generate_pack.py",
        "-- DO NOT EDIT BY HAND",
        "",
        "AP_ITEM_ID_TO_CODE = {",
    ]
    for lid in sorted(AP_ITEM_TOGGLE_IDS):
        lines.append(f"    [{lid}] = {AP_ITEM_TOGGLE_IDS[lid]!r},")
    lines.append("}")
    lines.append("")
    lines.append("AP_LOCATION_ID_TO_SECTION = {")
    pairs: list[tuple[int, str]] = []
    region_codes: list[str] = []
    for slug in REGION_ORDER:
        region_display = REGION_DISPLAY_NAMES[slug]
        for name, lid in by_region.get(slug, []):
            # Section code path: @<region-display>/<ap-name>/<ap-name>.
            # The location name and its single section name are intentionally
            # identical (matches ALttP/SM/Banjo-Tooie convention). UT keys off
            # the section leaf, which equals the AP location name, so no
            # `poptracker_name_mapping` table is needed on the apworld side.
            safe_name = sanitize_for_code_path(name)
            code = f"@{region_display}/{safe_name}/{safe_name}"
            pairs.append((lid, code))
            region_codes.append(code)
    for lid, code in sorted(pairs):
        lines.append(f"    [{lid}] = {code!r},")
    lines.append("}")
    lines.append("")
    # UDF kill-location id -> toggle item code. The Lua autotracker flips the
    # toggle on whenever the matching AP location id is checked, so the
    # progression row in the items grid mirrors goal-progress completion.
    lines.append("AP_UDF_LOCATION_ID_TO_TOGGLE = {")
    for _fight, _ap_name, lid, _slug in udf_resolved:
        lines.append(f"    [{lid}] = {udf_toggle_code(_fight)!r},")
    lines.append("}")
    lines.append("")
    # Equipment AP id -> per-rarity counter code. The apworld assigns IDs
    # 1000+index over EQUIPMENT in equipment.py, where each entry carries a
    # rarity tier 0..3. Lua's code_for_item routes equipment-range IDs into
    # the right rarity bucket via this table.
    lines.append("AP_EQUIPMENT_ID_TO_RARITY_CODE = {")
    for ap_id in sorted(equipment_rarities):
        tier = equipment_rarities[ap_id]
        lines.append(f"    [{ap_id}] = {RARITY_CODES[tier]!r},")
    lines.append("}")
    lines.append("")
    lines.append("REGION_SECTION_CODES = {")
    for code in region_codes:
        lines.append(f"    {code!r},")
    lines.append("}")
    lines.append("")
    # End with a newline.
    return "\n".join(lines) + "\n"


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    pack_root = here.parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apworld", type=Path, required=True,
                   help="Path to the BG3 apworld directory (containing locationids.py, items.py, ...).")
    p.add_argument("--pack-root", type=Path, default=pack_root,
                   help=f"Pack root to write into (default: {pack_root}).")
    p.add_argument("--check", action="store_true",
                   help="Validate apworld data only; do not write any files.")
    p.add_argument("--force-maps", action="store_true",
                   help="Overwrite existing region map PNGs with fresh placeholders. "
                        "Default behavior preserves textured/hand-built maps.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    locations = parse_location_name_id_region(args.apworld)
    errors = validate(locations)
    if errors:
        print("[FAIL] Validation errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[OK] Parsed {len(locations)} locations across {len({s for _, _, s in locations})} regions.")

    by_region = group_by_region(locations)
    for slug in REGION_ORDER:
        n = len(by_region.get(slug, []))
        print(f"     {slug:18s} {n:4d} locations")

    udf_fights = parse_user_defined_fights(args.apworld)
    udf_resolved = resolve_udf_to_locations(udf_fights, locations)
    n_act1 = sum(1 for _, _, _, slug in udf_resolved if slug in ACT1_REGIONS)
    n_act2 = sum(1 for _, _, _, slug in udf_resolved if slug in ACT2_REGIONS)
    print(f"[OK] UDF: {len(udf_resolved)} fights resolved ({n_act1} Act 1 + {n_act2} Act 2).")
    for fight, name, lid, slug in udf_resolved:
        print(f"     {fight:24s} -> {name} (id={lid}, {slug})")

    equipment_rarities = parse_equipment_rarities(args.apworld)
    rarity_counts = {tier: 0 for tier in RARITY_CODES}
    for tier in equipment_rarities.values():
        rarity_counts[tier] += 1
    print(f"[OK] Equipment: {len(equipment_rarities)} items, "
          + ", ".join(f"{rarity_counts[t]} {RARITY_CODES[t]}" for t in RARITY_CODES))

    if args.check:
        print("[OK] --check passed; no files written.")
        return 0

    pack_root: Path = args.pack_root

    npc_pins = load_npc_pins(pack_root)
    overworld_pins = load_overworld_pins(pack_root)
    projected_total = sum(len(p) for p in npc_pins.values())
    print(f"[OK] NPC pins loaded: {projected_total} projections across {len(npc_pins)} regions"
          if npc_pins else "[OK] No npc_pins.json -- all pins use grid layout")

    # 1. locations/locations.json
    write_json(pack_root / "locations" / "locations.json",
               emit_locations_json(by_region, npc_pins, overworld_pins))
    print(f"[OK] Wrote locations/locations.json ({len(locations)} sections)")

    # 2. maps/maps.json
    write_json(pack_root / "maps" / "maps.json", emit_maps_json(pack_root))
    print(f"[OK] Wrote maps/maps.json ({len(REGION_ORDER)} region + "
          f"{len(ACT_OVERWORLDS)} overworld maps)")

    # 3. layouts/regions.json
    write_json(pack_root / "layouts" / "regions.json", emit_regions_layout())
    print(f"[OK] Wrote layouts/regions.json")

    # 4. scripts/autotracking_generated.lua
    lua = emit_autotracking_generated(by_region, udf_resolved, equipment_rarities)
    out_lua = pack_root / "scripts" / "autotracking_generated.lua"
    out_lua.parent.mkdir(parents=True, exist_ok=True)
    out_lua.write_text(lua, encoding="utf-8", newline="\n")
    print(f"[OK] Wrote scripts/autotracking_generated.lua")

    # 5. images/maps/<slug>.png -- placeholders only for regions that don't
    #    already have a real texture. Hand-built / texture-composited PNGs
    #    are preserved across regen unless --force-maps is set.
    maps_dir = pack_root / "images" / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    kept = 0
    for idx, slug in enumerate(REGION_ORDER):
        color = REGION_PALETTE[idx % len(REGION_PALETTE)]
        path = maps_dir / f"{slug}.png"
        existed = path.is_file()
        render_map_png(slug, color, path, force=args.force_maps)
        if existed and not args.force_maps:
            kept += 1
        else:
            written += 1
    # Drop now-stale <slug>_quests.png files from a previous layout iteration.
    for slug in REGION_ORDER:
        stale = maps_dir / f"{slug}_quests.png"
        if stale.exists():
            stale.unlink()
    print(f"[OK] Map PNGs: wrote {written} placeholder(s), preserved {kept} existing")

    # Clean up leftover P4-interim UDF artifacts if they exist from a prior run.
    for stale in [
        pack_root / "layouts" / "udf.json",
        pack_root / "images" / "maps" / "udf.png",
    ]:
        if stale.exists():
            stale.unlink()
            print(f"[OK] Removed stale {stale.relative_to(pack_root)}")

    print("[DONE] Pack files regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
