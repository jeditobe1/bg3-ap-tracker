# Baldur's Gate 3 Archipelago — PopTracker Pack

A [PopTracker](https://github.com/black-sliver/PopTracker) pack for the [Baldur's Gate 3 Archipelago](https://github.com/zane31415/ArchipelagoBG3) randomizer.

Items received from the multiworld auto-track. Locations the slot has checked auto-mark via the AP autotracker. Layout: status bar + items grid + one tab per BG3 region with the region's locations as pins. Visibility filters by Goal / Killsanity / Questsanity toggles (set automatically from slot_data when AP is connected; user-settable for offline planning).

## Status

**0.5.1.** Wired to BG3 apworld v0.4.5+. Tracks all items, all 937 locations across 18 regions, with reachability colored by Level Fragment count and visibility filtered by the slot's goal + sanity options. The items grid includes a 16-toggle UDF goal-progress row driven by the boss-kill location IDs.

Also works as the [Universal Tracker](https://github.com/FarisTheAncient/Archipelago/tree/tracker) map tab — UT prompts for the pack zip the first time it loads a BG3 slot.

Region maps render real BG3 worldmap textures composited from the game atlas. Each region has its own map with kill pins projected from in-world NPC positions, plus per-act overworld maps with region-callout pins. Multi-zone regions (Goblin Camp, Grymforge, Blighted Village, Monastery, Creche, Nautiloid, Last Light Inn, Moonrise Towers, West Act 2) are split into named sub-cells (e.g. monastery cliff trail vs interior; Reithwin Town vs Town Basement vs HoH Morgue).

## Compatibility

- **BG3 apworld**: v0.4.5 or later. The pack maps to the v0.4.5 region layout (Last Light Inn was split out of West Act 2 at that release).
- **PopTracker**: any recent version. Tested against PopTracker 0.31.x.
- **Universal Tracker**: any recent version. The apworld carries `tracker_world` + YAML-free re-gen support, so UT picks up the map tab and regenerates the world from slot_data without needing the player's YAML on disk.

## Install — PopTracker

1. Install PopTracker (https://github.com/black-sliver/PopTracker/releases) — portable build is fine.
2. Grab the latest `bg3-poptracker-<version>+release.zip` from this repo's Releases page and drop it (no need to unzip) into PopTracker's `packs/` directory. PopTracker reads pack zips natively. For maintainer-style live editing, junction the source tree instead:
   ```
   mklink /J "<PopTracker install>\packs\bg3-poptracker" "<this repo>"
   ```
3. Launch PopTracker (use `poptracker.exe --console` if you want stdout in a console window). File → Load Pack → **Baldur's Gate 3 Archipelago** → variant **Standard**.

## Install — Universal Tracker

1. Make sure the BG3 apworld (v0.4.5+) is in UT's `custom_worlds/` directory.
2. Launch UT and connect to your BG3 slot as normal. The first time it loads a BG3 slot, UT pops a file-picker asking for the pack zip — point it at `bg3-poptracker-<version>+release.zip` from the Releases page (no need to unzip). The path is remembered for subsequent launches.
3. To change the path later, edit `bg3_options.ut_pack_path` in `host.yaml` (UT only prompts once; clearing the value back to `""` makes it re-prompt on next launch).

## Connecting to AP

Click the **AP** button in the toolbar. Enter your AP server host/port, the slot name from your YAML, and password (if any). PopTracker connects directly to the AP websocket — no client-side relay needed.

On connect the pack:
- Caches `slot_data` and sets Goal / Killsanity / Questsanity badges from it.
- Resets all item counters and location states.
- Bulk-marks every location in `Archipelago.CheckedLocations` as cleared.
- Prints a one-line summary to the console.

## What's tracked

**Randomizer Options** (PopTracker gear-icon → "Randomizer Options" popup):
- **Goal** — 5-stage progressive (left-click = next, right-click = previous): Rescue Halsin / Kill Inquisitor Wwargaz / Act 1 UDF / Kill Myrkul / Act 2 UDF.
- **Killsanity** — toggle for creature-kill checks (IDs ≥ 10000): left-click ON, right-click OFF, middle-click flips.
- **Questsanity** — toggle for quest-update checks (IDs < 10000): same click semantics.

These drive what locations are visible. When AP is connected, the values are set automatically from `slot_data` and **manual clicks revert** until you disconnect (so they always reflect the slot's actual configuration). In offline / non-AP mode they're freely user-editable for planning.

**Items grid** (4 rows):

| Code | Type | Source |
| --- | --- | --- |
| `level_fragment` | counter 0–30 | Progression — every level-up |
| `boots_of_speed` | toggle | Progression |
| `shadow_lantern` | toggle | Progression |
| `spear_of_night` | toggle | Progression |
| `stat_boost` | counter | Aggregate of all 30 stat-boost item variants |
| `filler` | counter | Aggregate of filler (Lockpick, Supply Pack, Gold, etc.) |
| `trap` | counter | Aggregate of all trap variants (IDs 7000–7006) |
| `equipment_common` / `equipment_uncommon` / `equipment_rare` / `equipment_very_rare` | 4 counters | Per-rarity equipment received. The generator AST-reads `EQUIPMENT` from the apworld's `equipment.py` and emits an AP-id → rarity-code map. |
| `udf_<fight>` × 16 | toggles | Driven by the matching kill location; UDF goal-progress row. Each fight has its own NPC portrait icon (sourced from BG3's `Portraits/` DDS files) — see `tools/extract_icons.py PORTRAIT_ICON_TARGETS`. |

**Locations** are organized into 18 tabs, one per region (Nautiloid → Mindflayer Colony). Each location is a clickable pin on a placeholder per-region map. Pins color by reachability: green = the region is reachable with the player's current Level Fragment count; red = not yet (the access threshold per region matches the apworld's `regions.py` gates). Clicking a pin opens a popup with the section name verbatim from the apworld; left-click marks cleared, right-click reverts.

**User Defined Fights goal-progress row** lives in the items grid as a third row of 16 toggle items, one per fight from the apworld's `UserDefinedFights.valid_keys`. Each toggle flips on when the corresponding boss kill location is checked (driven by the same AP event that updates the kill section on the region tab). They light up as you complete fights regardless of your goal — on non-UDF goals they sit greyed in the row as a passive "haven't killed this named boss yet" indicator. PopTracker doesn't support hiding layout widgets at runtime, so the row is always visible; the visual style follows the existing progression-item convention (greyed = off, bright = on).

**Visibility filtering** (driven by the Randomizer Options popup):
- Goal stage controls which regions are visible (e.g. Halsin hides Act 1 underdark + Act 2; Wwargaz adds Act 1 underdark; Kill Myrkul / Act 2 UDF show everything).
- Killsanity ON shows kill locations (IDs ≥ 10000); OFF hides them.
- Questsanity ON shows quest locations (IDs < 10000); OFF hides them.

## Useful PopTracker shortcuts

- **F11** (or Ctrl+H): toggle "hide cleared + hide unreachable locations" globally. Useful for sweeping noise off the maps once you've cleared a chunk.
- **F5**: reload pack from disk (after editing).
- **Right-click** any item to undo / decrement.

## Known limitations / planned work

- **A few sub-cells render dim** — some regions with sparse Granite virtual-texture coverage in the source game (e.g. cliff-trail strips) composite to a darker backdrop. The pins still place correctly.
- **Region display names are first-pass** — some sub-cell labels (e.g. "Town Basement", "HoH Morgue") are working names; a maintainer naming pass is planned.
- **PopTracker tabs are not visibility-aware** — when a goal hides a region's contents, the tab itself stays visible (just empty). PopTracker doesn't support hiding tabs at runtime per Lua state.
- **Thaniel: Kill Mom / Kill Dad fall to a grid position** — these two quest-spawned kills aren't in the NPC position cache, so they grid into the bottom of the West Act 2 map. Cosmetic; the locations themselves track correctly.
- **Equipment is bucketed by rarity, not per-item** — equipment shows as 4 rarity counters (common / uncommon / rare / very rare). Per-item visibility plus an `X of Y` denominator would need `add_act1a_treasure` / `add_act2_treasure` and a derived `expected_equipment_count` in slot_data; deferred until the apworld exposes those.
- **Trap variants are aggregated** — all 7 trap types collapse into a single `trap` counter. Per-trap-type counters were removed to keep the items grid focused on tracking that's relevant to most seeds (trap-disabled seeds simply leave the counter at 0).
- **Statsanity unsupported** — the apworld's statsanity option is hidden / unimplemented; the tracker treats it as off (stat-boost items are still counted, just not gated).

## Maintainer guide

### Regenerating after an apworld change

The pack is partially generated from the BG3 apworld's Python source. Run `tools/generate_pack.py` to rebuild:

```
python tools/generate_pack.py --apworld "<path to>/ArchipelagoBG3/worlds/bg3"
```

The generator owns these files (do not edit by hand):

- `locations/locations.json`
- `maps/maps.json`
- `layouts/regions.json`
- `scripts/autotracking_generated.lua`
- `images/maps/<region>.png`

It reads `LOCATION_NAME_ID_REGION` from the apworld's `locationids.py` via AST (no apworld imports). Run with `--check` first to validate the data without writing files.

Hand-authored files that the generator does NOT touch:

- `manifest.json`
- `items/items.json`
- `layouts/standard.json`
- `scripts/init.lua`
- `scripts/autotracking.lua`
- `images/items/*.png`

If the apworld adds new region slugs, edit `REGION_DISPLAY_NAMES`, `REGION_ORDER`, `REGION_ACCESS_GATE`, and `REGION_GOAL_VISIBILITY` in `tools/generate_pack.py` — the generator will fail loudly with `unmapped region slug 'X'` if you forget the first.

If item IDs change in items.py:
- New toggle items: add to `AP_ITEM_TOGGLE_IDS` in `tools/generate_pack.py`.
- New ID ranges for consumables (stat boosts, equipment, filler, traps): update `code_for_item` in `scripts/autotracking.lua`.
- New sanity options: add a toggle item to `items/items.json` and emit `visibility_rules: ["<new_sanity_code>"]` per section from the generator.
- New equipment in `equipment.py`: the generator picks them up automatically via the EQUIPMENT AST pass; only the 4 rarity buckets (`equipment_common` / `equipment_uncommon` / `equipment_rare` / `equipment_very_rare`) need to stay in items.json. New rarity tiers (>3) would need a corresponding entry in `RARITY_CODES` in `tools/generate_pack.py`.

If the apworld changes `UserDefinedFights.valid_keys` in `options.py`:
- The generator's `resolve_udf_to_locations` will fail loudly (unmatched / ambiguous fight) and refuse to write. Fix the apworld side or add a name to the matching logic.
- Hand-add a matching `udf_<slug>` toggle entry to `items/items.json` (the toggle code follows `re.sub(r"[^a-z0-9]+", "_", fight.lower()).strip("_")`).
- Add the new code to the appropriate row in `layouts/standard.json`.

### Re-extracting item icons from BG3

If you want to retarget icons (e.g. swap which BG3 item provides the placeholder for a Trap, or change one of the UDF boss portraits), see `tools/extract_icons.py`. It needs prior `Divine.exe extract-package` runs of:

- `Icons.pak` — atlas `.dds` files
- `Shared.pak` — `Icons_Items*.lsx` UV maps **and** the individual portrait `.dds` files under `Mods/Shared/GUI/Assets/Portraits/`
- `Gustav_Textures.pak` — campaign-specific NPC portraits under `Mods/Gustav*/GUI/Assets/Portraits/` (needed for 13 of the 16 UDF portraits)

Then run with all four roots:

```
python tools/extract_icons.py \
  --icons-root "<extracted Icons.pak>" \
  --shared-gui-root "<extracted Shared.pak>/Public/Shared/GUI" \
  --ap-mod-root "<BG3ArchipelagoMod mod folder>" \
  --portraits-shared-root "<extracted Shared.pak>" \
  --portraits-gustav-root "<extracted Gustav_Textures.pak>"
```

The UDF portrait mapping lives in `PORTRAIT_ICON_TARGETS` — to swap a boss icon, update the DDS path next to its `udf_*` code there.

## Repo layout

```
bg3-poptracker/
├── manifest.json
├── items/                # hand-authored item definitions
├── layouts/              # standard.json hand-authored; regions.json generated
├── locations/            # generated
├── maps/                 # generated
├── scripts/
│   ├── init.lua          # hand-authored entry
│   ├── autotracking.lua  # hand-authored handlers
│   └── autotracking_generated.lua  # generated ID maps
├── images/
│   ├── items/            # icons extracted from BG3
│   └── maps/             # generated per-region placeholders
└── tools/
    ├── generate_pack.py  # apworld -> generated pack files
    └── extract_icons.py  # BG3 atlases -> images/items/*.png
```

## Reporting issues

- **Tracker-side bugs** (pin position, region grouping, visibility filtering, UDF logic, autotracking handler errors): file in this repo's [Issues](https://github.com/jeditobe1/bg3-ap-tracker/issues).
- **AP-side bugs** (location not awarded in-game, wrong item received, slot_data field missing): report to the upstream project per their own instructions:
  - [BG3 Archipelago apworld](https://github.com/zane31415/ArchipelagoBG3) — the multiworld/randomizer logic.
  - [BG3 Archipelago mod](https://github.com/zane31415/BG3ArchipelagoMod) — the in-game Script Extender mod that talks to the AP client.

  The upstream maintainer primarily takes feedback via Discord (see the upstream READMEs for the current channel link); GitHub issues are not the main intake path there.

## License

MIT — see [LICENSE](LICENSE). The upstream [BG3 Archipelago apworld](https://github.com/zane31415/ArchipelagoBG3) is MIT, and [PopTracker](https://github.com/black-sliver/PopTracker) is GPLv3 with a plugin license addendum that permits MIT plugins; this pack qualifies as a plugin (loaded data + Lua, not compiled into PopTracker).

## References

- BG3 Archipelago apworld: https://github.com/zane31415/ArchipelagoBG3
- PopTracker: https://github.com/black-sliver/PopTracker
- PopTracker pack format: PopTracker `doc/PACKS.md`, `doc/AUTOTRACKING.md`
- Reference packs that informed the design: ALttP (StripesOO7), Super Metroid (Cyb3RGER), Banjo-Tooie (Ozone31)
