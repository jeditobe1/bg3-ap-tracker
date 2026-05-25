# How the pack is built

This guide explains the pack's build pipeline end-to-end: how raw BG3
game data turns into the per-region maps, pin coordinates, and release
zip that ship to PopTracker / Universal Tracker users. It is intended
for someone who wants to maintain the pack without access to the
original author's local scratch tree.

Read alongside the existing `README.md` "Maintainer guide" section,
which covers apworld-rename / item-id / icon workflows. This doc
covers the layer underneath that: where the map textures, pin
positions, and projection geometry come from.

> Most maintenance tasks (renames, new locations under existing
> regions, pin nudges, icon swaps) need **none** of the heavy
> texture-extraction pipeline. See "Maintenance workflows" below for
> the decision tree.

## Pipeline overview

```
BG3 install                                 .pak archives (Divine.exe)
    |                                                   |
    v                                                   v
.pak extraction                              Granite VT extraction
(via Divine.exe extract-package)             (via ConverterApp.exe -> DDS files)
    |                                                   |
    +-----------------------+---------------------------+
                            |
                            v
            tools/build_npc_data.py
                            |
                            v
              tools/bg3_npc_data.json
              tools/vt_index.json       (UUID -> texture hash lookup)
                            |
                            v
        per-region backdrop renderer (see "Region rendering")
                            |
                            v
              tools/projections.json
              images/maps/<slug>.png
                            |
                            v
       tools/pin_overrides.json  (hand-curated nudges)
                            |
                            v
             tools/generate_pack.py
                            |
                            v
     locations/locations.json + maps/maps.json + layouts/regions.json
     + scripts/autotracking_generated.lua
                            |
                            v
             tools/build_release.py
                            |
                            v
              build/bg3-poptracker-<version>+release.zip
```

The shipped pack repo carries the **outputs** of every stage in the
diagram: `bg3_npc_data.json`, `projections.json`, `pin_overrides.json`,
`images/maps/*.png`, the generated `locations/`, `maps/`, `layouts/`
files. Source DDS textures and the unpacked .pak trees are NOT
committed (bulky, re-derivable per the steps below).

## External tools

| Tool | Used for | Source |
| --- | --- | --- |
| LSLib `Divine.exe` | Extract `.pak` archives (Icons, Shared, Gustav, Gustav_Textures, VirtualTextures). Convert `.lsf` <-> `.lsx`. | [LSLib releases](https://github.com/Norbyte/lslib/releases) |
| LSLib `ConverterApp.exe` | Extract Granite virtual textures (BG3's streaming minimap format) to DDS. Divine.exe does NOT do this. | Same LSLib package as Divine.exe |
| Python 3.10+ with `Pillow`, `numpy`, `scipy` | All pack-side scripts. Install via `pip install -r tools/requirements.txt`. | PyPI |
| PopTracker | Runtime; loads the pack zip natively. | [PopTracker releases](https://github.com/black-sliver/PopTracker/releases) |

Set `LSLIB_DIR` (or pass `--divine` explicitly) so scripts can find
the LSLib install. None of the pack scripts hard-code an install
location.

## Stage 1 — One-time BG3 asset extraction

You only repeat these if BG3 ships a content patch that changes
characters, the world map, or texture data.

### 1a. Extract the .paks `build_npc_data.py` needs

`tools/build_npc_data.py` walks character and worldmap metadata. It
needs the following .paks unpacked to a working directory:

- `Gustav.pak`
- `GustavDev.pak`
- `Shared.pak`
- `Engine.pak` (if your install has it as a separate file; some patches roll it in)

Use Divine.exe `extract-package` (or let `build_npc_data.py
--work-dir <dir>` drive it; the script knows the .pak list).

### 1b. Extract the Virtual Texture data (only if you need to render new map backdrops)

BG3 stores minimap art as **Granite virtual textures**, not as plain
DDS files in a texture bank. Each WorldMap patch references a texture
by UUID; the UUID resolves through a `VirtualTextureBank` region
(inside the merged minimap LSF) to a content-hash `GTexFileName`,
which in turn maps to `.gts` / `.gtp` tile files inside
`VirtualTextures.pak`.

Workflow:

1. Unpack `VirtualTextures.pak` via Divine.exe `extract-package`. This
   produces 16 `Albedo_Normal_Physical_<hex>.gts` manifest files plus
   thousands of `.gtp` tile files. Expect ~76 GB on disk after unpack.
2. Launch ConverterApp.exe → **Virtual Textures** tab. For each of the
   16 `.gts` files: set `GTS Path` to the manifest, set `Output Path`
   to one shared `Extracted/` directory, click **Extract Tile Set**.
3. Result: one DDS per (texture-hash, channel) pair, named
   `<hash>_<channel>.dds`. We only use `_0` (Albedo); `_1` (Normal)
   and `_2` (Physical) can be deleted to reclaim ~33 GB. ConverterApp
   has no channel filter, so all three are produced regardless.

If disk space is tight, `_0.dds` is the only family the renderers
read; the others are safe to delete.

The renderer scripts find this cache via `--dds-cache <path>` (or
`BG3_TEXTURE_CACHE` env var). Default search path is
`tools/_texture_work/extracted/` (gitignored).

### 1c. Build the UUID → texture hash index

Convert the merged minimap manifest to LSX:

```
Divine.exe -g bg3 -a convert-resource \
    -s "<Gustav.pak extracted>/Public/GustavDev/Content/Generated/[PAK]_Minimaps/_merged.lsf" \
    -d <work-dir>/minimaps_merged.lsx
```

Then run the texture-index builder:

```
python tools/build_texture_index.py --merged-lsx <work-dir>/minimaps_merged.lsx
```

The result is `tools/vt_index.json` — a small (~65 KB) JSON
mapping each TextureFilePath UUID to its `GTexFileName` hash. The
file is committed to the repo so contributors who only want to tweak
existing regions don't need to repeat this stage. Together with the
`Extracted/<hash>_0.dds` cache, this is everything the backdrop
renderers need.

## Stage 2 — `bg3_npc_data.json` (the NPC + WorldMap cache)

`tools/build_npc_data.py` writes a compact JSON of every character
placement, every level's world-to-pixel transform, and every WorldMap
patch (including the `texture_uuid` that drives backdrop selection).
Output schema is documented in the script's docstring.

Run it after a BG3 patch, or whenever the apworld surfaces a new
character UUID you want to pin:

```
python tools/build_npc_data.py \
    --apworld "<path>/ArchipelagoBG3/worlds/bg3" \
    --divine  "<path>/Divine.exe" \
    [--bg3-data-dir "<Steam>/steamapps/common/Baldurs Gate 3/Data"] \
    [--work-dir <temp dir for extracted .paks>] \
    [--out tools/bg3_npc_data.json]
```

Outputs:

- `tools/bg3_npc_data.json` (~ MB; committed).

This file alone is enough to compute pin coordinates for any AP
location whose creature UUID is present. It is not enough to compute
**positions on a new region map** — for that you also need the
projection geometry written by the backdrop renderer.

## Stage 3 — Region rendering

A region map has two outputs:

1. A PNG backdrop at `images/maps/<slug>.png`, composited from BG3's
   own minimap textures.
2. A projection block in `tools/projections.json` recording the
   world<->canvas transform for that region (single-zone) or for each
   of its sub-zones (multi-zone).

Both are committed. The pack generator reads `projections.json` plus
`pin_overrides.json` plus `bg3_npc_data.json` at build time to compute
the final pin coordinates for every AP location.

Three renderer entry points live next to the other tools in `tools/`:

- **`render_region_map.py`** — single-zone region renderer. One
  continuous worldspace cluster per region (e.g. Druid Grove, Ravaged
  Beach). Canvas 480×320.
- **`render_multi_zone_map.py`** — the workhorse. Renders 2+
  worldspace clusters under a single apworld slug onto a 960×640
  canvas (e.g. Goblin Camp = camp + Sanctum; Last Light Inn = inn +
  defense beach + Meenlock cave). Configuration lives in a
  `REGION_DEFINITIONS` dict at the top of the script with one entry
  per multi-zone region.
- **`render_overworld.py`** — composes a whole engine level's
  exterior patches into one wide canvas for the per-act overview tabs.

All three load:
- `tools/bg3_npc_data.json` for char positions + WorldMap patches.
- `tools/vt_index.json` for UUID → texture-hash lookup.
- `<dds-cache>/<hash>_0.dds` for the actual texture pixels.

All three write:
- A PNG to `images/maps/<slug>.png`.
- A projection block to `tools/projections.json`.

They do NOT write pins directly; pin canvas-coords are computed at
pack-build time by `tools/generate_pack.py` (via `tools/project.py`)
so that pin fix-ups via `pin_overrides.json` work without re-rendering
the backdrop.

### The data model

- **World coordinates**: each engine level has its own origin. `(x, z)`
  are the floor plane; `y` is height and is ignored. Common levels:
  `WLD_Main_A` (Act 1 wilderness), `CRE_Main_A` (mountain pass +
  monastery + creche), `SCL_Main_A` (Act 2), `TUT_Avernus_C` (Prologue
  Nautiloid). Act 3 levels are not yet mapped.
- **WorldMap patches**: each is one DDS rectangle laid down on a level
  at world bbox **center** `(world_x, world_z)` with size
  `(world_w, world_h)`. Each carries an optional `building` tag and
  `floor` index, plus a `texture_uuid`.
- **Characters**: each AP kill-location maps to a real BG3 NPC by UUID,
  with `position [x, y, z]`, `level`, `building`, `name`. Quest-spawned
  characters are sometimes cached at their cutscene-trigger spawn
  rather than the fight location — those need
  `char_position_overrides` (see `pin_overrides.json`).

### Multi-zone configuration

A multi-zone region entry looks like:

```python
"<slug>": {
    "level": "<engine level>",
    "zones": [
        {
            "title": "Display title for this sub-cell",
            "char_building": [None, "BuildingTag", ...],  # chars to include
            "patch_building": ["", "BuildingTag", ...],    # patches to composite
            "patch_floors": [0, 1],                        # optional floor filter
            "world_bbox_filter": (x0, z0, x1, z1),         # optional pre-filter
            "world_bbox_override": (x0, z0, x1, z1),       # optional fixed bbox
            "cell": (col, row, colspan, rowspan),          # canvas grid placement
        },
        ...
    ],
}
```

- `char_building`: which `char.building` tags belong in this zone.
  `None` matches exterior chars (cached as empty string).
- `patch_building`: which `patch.building` tags get composited.
- `patch_floors`: restrict to specific floors when buildings stack.
- `world_bbox_filter`: include only chars/patches inside this bbox
  before auto-computing the canvas bbox. Use to exclude outliers.
- `world_bbox_override`: force a specific world bbox (skip
  auto-compute). Use when char positions are sparse and you want a
  specific zoom level.
- `cell`: grid placement on the 960×640 canvas. Default is side-by-side.

Canvas reserves 32 px for a per-zone title bar and 20% at the bottom
for the quest panel (rendered separately at pack time).

## Stage 4 — `pin_overrides.json` (hand-curated nudges)

`tools/pin_overrides.json` is the human-edited source of truth for pin
fix-ups. It has five sections (schema in the file's `_doc` key):

- `synthetic_chars` — AP kill-locations whose creature UUID is exposed
  by the apworld but is not in `bg3_npc_data.json` (typically
  quest-spawned creatures). Each entry supplies `name`, `level`,
  `position`, `building`. Injected into the cache at load time so the
  rest of the pipeline treats them like any cached char.
- `char_position_overrides` — per-character world-coord overrides for
  NPCs with bad cached spawn coords (cutscene spawn vs. fight
  position).
- `char_building_overrides` — building-tag overrides for chars whose
  cached building wouldn't route them to the intended zone after a
  position override.
- `region_world_offsets` — per-region systematic offset added to all
  char positions (useful when a whole patrol cluster is offset).
- `overworld_pin_overrides` — hand-positioned region pins on the act
  overview canvases when the auto-centroid doesn't make narrative
  sense.
- `overworld_centroid_filters` — which chars to compute a region's
  overview pin from (e.g. anchor Last Light Inn at the inn building,
  not the defense waves).

Pin fix-ups are cheap and don't require re-rendering anything. Edit
the JSON, re-run `generate_pack.py`, done.

## Stage 5 — `generate_pack.py` (the per-build step)

`tools/generate_pack.py` is the entry point for every regeneration. It
owns:

- `locations/locations.json`
- `maps/maps.json`
- `layouts/regions.json`
- `scripts/autotracking_generated.lua`
- `images/maps/<slug>.png` placeholders (only for slugs that don't
  already have a real backdrop)

It does NOT own:

- `manifest.json` (`package_version` is hand-bumped on release).
- `items/items.json`, `layouts/standard.json`, `scripts/init.lua`,
  `scripts/autotracking.lua`, `images/items/*.png` (all hand-authored).
- The texture pipeline outputs (`bg3_npc_data.json`,
  `projections.json`, real backdrop PNGs).

```
python tools/generate_pack.py --apworld "<path>/ArchipelagoBG3/worlds/bg3"
python tools/generate_pack.py --check    # dry-run validation
```

## Stage 6 — `build_release.py` (zip the pack)

`tools/build_release.py` packs everything PopTracker / UT need into a
release zip, excluding `tools/`, `build/`, `.git`, caches, and any
ignored cruft. It also applies a privacy regex check from `.env`
(gitignored) and refuses to emit a zip if any banned token appears in
tracked content.

```
python tools/build_release.py
# Output: build/bg3-poptracker-<package_version>+release.zip
```

The default privacy check catches drive-letter paths (a copy-paste
hazard) out of the box. Maintainers with additional PII concerns set
extra regex tokens via `BG3_PRIVACY_EXTRA` in a gitignored `.env`
at the pack root; first-time contributors don't need a `.env`.

## Maintenance workflows

The decision tree for "what do I have to run":

### "The apworld renamed a slug / added locations to an existing region"

Just regenerate:
```
python tools/generate_pack.py --apworld "<path>/ArchipelagoBG3/worlds/bg3"
```

If the slug is new (region didn't exist before), see "Adding a new
region" below. If the rename is cosmetic, edit `REGION_DISPLAY_NAMES`
in `generate_pack.py`.

### "A pin is in the wrong spot"

Edit `tools/pin_overrides.json`:
- Wrong position for one char → `char_position_overrides`.
- Char ended up in the wrong sub-zone → add `char_building_overrides`.
- AP location's creature isn't in the cache → add to
  `synthetic_chars`.

Re-run `generate_pack.py`. No texture pipeline involved.

### "BG3 patched; the NPC cache is stale"

Re-run `tools/build_npc_data.py` (Stage 2 above). This pulls the
latest character placements + WorldMap patches.

If a NEW texture UUID shows up that isn't covered by `vt_index.json`,
you'll also need to re-run Stage 1c (rebuild the texture index) and
possibly Stage 1b (re-extract VirtualTextures.pak if Larian added new
minimap art).

### "Re-extract item or portrait icons"

`tools/extract_icons.py` covers this; the existing README has the full
command. Needs prior Divine.exe extraction of `Icons.pak`,
`Shared.pak`, and `Gustav_Textures.pak`.

### "Add a new region's map"

**This is the heaviest workflow.** It requires the Granite VT
extraction (Stage 1b above) to be done at least once on your local
machine. Steps:

1. Confirm the region is in the apworld's `LOCATION_NAME_ID_REGION`.
2. Identify which engine level its chars live on (inspect
   `bg3_npc_data.json` for a few of its UUIDs).
3. For a single-zone region: run `tools/render_region_map.py` with
   the slug + level.
4. For a multi-zone region: add a `REGION_DEFINITIONS` entry to
   `tools/render_multi_zone_map.py` describing the sub-zones (see
   "Multi-zone configuration" above), then run it. The script writes
   both the backdrop PNG and the projection block.
5. Inspect the PNG. Common issues:
   - Blank cell: a patch in the bbox wasn't composited. Confirm
     `Image.MAX_IMAGE_PIXELS = None` is still set in the renderer (BG3
     has 500+ megapixel mega-patches that hit PIL's default bomb
     guard).
   - Pins outside the texture: char positions are outside the patch
     bbox; either filter them out via `world_bbox_filter` or pad the
     bbox.
   - Char in the wrong zone: add a `char_building_overrides` entry.
6. Wire the slug into `tools/generate_pack.py`:
   - `REGION_DISPLAY_NAMES[slug]`
   - `REGION_ORDER` (placement in the tab list)
   - `REGION_ACCESS_GATE[slug]` (Level Fragment count gate)
   - `REGION_GOAL_VISIBILITY[slug]` if goal-conditional
   - `MAP_DIMS_OVERRIDES` and `MAP_SIZE_OVERRIDES` if the canvas is
     non-default (multi-zone maps are 960×640 vs. the 480×320 default)
   - `REGION_PALETTE` entry
7. Re-run `generate_pack.py`.
8. Smoke-test in PopTracker.

If the act-overview map should also show the new region as a pin:
- Re-run `tools/render_overworld.py` for that act to refresh
  centroids.
- Add an `overworld_pin_overrides` entry if the auto-centroid lands
  off-canvas (e.g. for regions whose chars don't live on the overview
  level).

### "Re-render an existing region after a fix"

Same as adding, minus the new-slug wiring. Re-run whichever renderer
owns the region. The output PNG overwrites `images/maps/<slug>.png`
and the projection block updates in place.

## When to ask the original author

- **A region whose Granite VT coverage is genuinely sparse.** Some
  cliff-trail strips and parts of the Underdark composite dim because
  Larian's source coverage is sparse there; this is a game-data limit
  and not fixable in the pipeline.
- **Anything Act 3.** Until the apworld ships Act 3 locations there's
  nothing to render against, and the rendering work itself isn't yet
  built out for `CTY_Main_A` (Lower City) or the Astral-plane levels.
- **Anything that touches the privacy regex token list in `.env`.**
  That's per-maintainer config, not in scope for a pack contributor.

## Item / equipment / UDF / sanity changes (apworld side)

These previously lived in README. They cover the per-case checklist when
the upstream apworld renames or restructures things the pack pins on.

### Item IDs change in `items.py`

- New toggle items: add to `AP_ITEM_TOGGLE_IDS` in `tools/generate_pack.py`.
- New ID ranges for consumables (stat boosts, equipment, filler, traps):
  update `code_for_item` in `scripts/autotracking.lua`.
- New sanity options: add a toggle item to `items/items.json` and emit
  `visibility_rules: ["<new_sanity_code>"]` per section from the generator.
- New equipment in `equipment.py`: the generator picks them up
  automatically via the EQUIPMENT AST pass; only the 4 rarity buckets
  (`equipment_common` / `equipment_uncommon` / `equipment_rare` /
  `equipment_very_rare`) need to stay in items.json. New rarity tiers
  (>3) would need a corresponding entry in `RARITY_CODES` in
  `tools/generate_pack.py`.

### `UserDefinedFights.valid_keys` changes in `options.py`

- The generator's `resolve_udf_to_locations` will fail loudly
  (unmatched / ambiguous fight) and refuse to write. Fix the apworld
  side or add a name to the matching logic.
- Hand-add a matching `udf_<slug>` toggle entry to `items/items.json`
  (the toggle code follows `re.sub(r"[^a-z0-9]+", "_", fight.lower()).strip("_")`).
- Add the new code to the appropriate row in `layouts/standard.json`.

## Re-extracting item icons from BG3

To retarget icons (e.g. swap which BG3 item provides the placeholder
for a Trap, or change one of the UDF boss portraits), use
`tools/extract_icons.py`. It needs prior `Divine.exe extract-package`
runs of:

- `Icons.pak` — atlas `.dds` files
- `Shared.pak` — `Icons_Items*.lsx` UV maps **and** the individual
  portrait `.dds` files under `Mods/Shared/GUI/Assets/Portraits/`
- `Gustav_Textures.pak` — campaign-specific NPC portraits under
  `Mods/Gustav*/GUI/Assets/Portraits/` (needed for 13 of the 16 UDF
  portraits)

Then run with all four roots:

```
python tools/extract_icons.py \
  --icons-root "<extracted Icons.pak>" \
  --shared-gui-root "<extracted Shared.pak>/Public/Shared/GUI" \
  --ap-mod-root "<BG3ArchipelagoMod mod folder>" \
  --portraits-shared-root "<extracted Shared.pak>" \
  --portraits-gustav-root "<extracted Gustav_Textures.pak>"
```

The UDF portrait mapping lives in `PORTRAIT_ICON_TARGETS` — to swap a
boss icon, update the DDS path next to its `udf_*` code there.
