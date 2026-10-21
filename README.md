# Baldur's Gate 3 Archipelago — PopTracker Pack

A [PopTracker](https://github.com/black-sliver/PopTracker) pack for the [Baldur's Gate 3 Archipelago](https://github.com/zane31415/ArchipelagoBG3) randomizer.

Items received from the multiworld auto-track. Locations the slot has checked auto-mark via the AP autotracker. Layout: status bar + items grid + one tab per BG3 region with the region's locations as pins. Visibility filters by Goal / Killsanity / Questsanity / Containersanity toggles (set automatically from slot_data when AP is connected; user-settable for offline planning).

## Status

**0.9.1-alpha (draft for review).** Wired to BG3 apworld v0.8.0, which its author still calls a beta. 0.9.0 added Act 3; 0.9.1 adds Containersanity. For the stable apworld v0.7.2, use tracker 0.8.1; for v0.6.3, tracker 0.7.1. Tracks all items, all 1530 locations across 31 regions plus the 10,575 Containersanity checks, with reachability colored by Level Fragment count plus per-region BlockEntrances gate items, and visibility filtered by the slot's goal + sanity options. The items grid includes a 30-toggle goal-progress row (Halsin rescue + 29 UDF fights) and a region-locking row for the 29 entrance gates + 1 Progressive Moonlight Towers counter.

Containersanity's 10,575 container checks are tracked as clustered map pins — see [Containersanity](#containersanity) and [Known limitations](#known-limitations).

Also works as the [Universal Tracker](https://github.com/FarisTheAncient/Archipelago/tree/tracker) map tab — UT prompts for the pack zip the first time it loads a BG3 slot.

Region maps render real BG3 worldmap textures composited from the game atlas. Each region has its own map with kill pins projected from in-world NPC positions, plus per-act overworld maps with region-callout pins. Multi-zone regions (Goblin Camp, Grymforge, Blighted Village, Monastery, Creche, Nautiloid, Last Light Inn, Moonrise Towers, West Act 2) are split into named sub-cells (e.g. monastery cliff trail vs interior; Reithwin Town vs Town Basement vs HoH Morgue).

## Compatibility

- **BG3 apworld**: v0.7.2 (also works with v0.7.1, minus that release's eight removed kills and renames). For apworld v0.6.3, use tracker 0.7.1. apworld v0.7.0 split Underwell out of Blighted Village, Inside Goblin Camp out of Goblin Camp and Zhentarim Basement out of Waukeen's Rest, and v0.7.1 split the Mason's Guild out of West Act 2, the Last Light Basement out of Last Light Inn, and added a Moonrise Rooftop; the pack folds each back into its parent tab (they are the same physical space, already drawn as a labelled zone on the parent's map) while keeping the child's own access rules on its checks. Older apworlds will mis-bucket equipment (the v0.7.0 equipment table dropped two duplicate entries, shifting ~426 AP item ids).
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

1. Make sure the BG3 apworld (v0.7.2) is in UT's `custom_worlds/` directory.
2. Launch UT and connect to your BG3 slot as normal. The first time it loads a BG3 slot, UT pops a file-picker asking for the pack zip — point it at `bg3-poptracker-<version>+release.zip` from the Releases page (no need to unzip). The path is remembered for subsequent launches.
3. To change the path later, edit `bg3_options.ut_pack_path` in `host.yaml` (UT only prompts once; clearing the value back to `""` makes it re-prompt on next launch).

## Connecting to AP

Click the **AP** button in the toolbar. Enter your AP server host/port, the slot name from your YAML, and password (if any). PopTracker connects directly to the AP websocket — no client-side relay needed.

On connect the pack:
- Caches `slot_data` and sets Goal / Killsanity / Questsanity / Containersanity badges from it.
- Resets all item counters and location states.
- Bulk-marks every location in `Archipelago.CheckedLocations` as cleared.
- Prints a one-line summary to the console.

## What's tracked

**Randomizer Options** (PopTracker gear-icon → "Randomizer Options" popup):
- **Goal** — 7-stage progressive (left-click = next, right-click = previous): Rescue Halsin / Kill Inquisitor Wwargaz / Act 1 UDF / Kill Myrkul / Act 2 UDF / Kill the Netherbrain / Act 3 UDF.
- **Killsanity** — toggle for creature-kill checks (IDs ≥ 10000): left-click ON, right-click OFF, middle-click flips.
- **Questsanity** — toggle for quest-update checks (IDs < 10000): same click semantics.
- **Containersanity** — toggle for container checks (IDs ≥ 100000). Set from slot_data: on only at the option's top value (`i_suppose_if_you_insist`), which is the only value that creates container checks.
- **Show Clutter** — display-only toggle that hides crates, barrels, vases, sacks and other clutter containers (about 7,500 of the 10,575). It does not change logic.

These drive what locations are visible. When AP is connected, the values are set automatically from `slot_data` and **manual clicks revert** until you disconnect (so they always reflect the slot's actual configuration). In offline / non-AP mode they're freely user-editable for planning.

**Items grid** (4 rows):

| Code | Type | Source |
| --- | --- | --- |
| `level_fragment` | counter 0–78 | Progression — every level-up |
| `boots_of_speed` | toggle | Progression |
| `shadow_lantern` | toggle | Progression |
| `spear_of_night` | toggle | Progression |
| `stat_boost` | counter | Aggregate of all 30 stat-boost item variants |
| `filler` | counter | Aggregate of filler (Lockpick, Supply Pack, Gold, etc.) |
| `trap` | counter | Aggregate of all trap variants (IDs 7000–7006) |
| `equipment_pre_halsin` / `equipment_act1` / `equipment_act2` / `equipment_act3` | 4 counters | Per-act-gate equipment received (pre-Halsin / Act 1 / Act 2 / Act 3 per the apworld's filter-level convention in `items.py:36`). The generator AST-reads `EQUIPMENT` from the apworld's `equipment.py` and emits an AP-id → act-gate-code map. |
| `udf_rescue_halsin` + `udf_<fight>` × 29 | 30 toggles | Goal-progress row. `udf_rescue_halsin` flips on the Halsin rescue (driven by AP location id 114, the apworld event paired with `Victory_Halsin`); the 29 `udf_<fight>` toggles flip on the matching kill locations from `UserDefinedFights.valid_keys`. Each toggle has its own NPC portrait icon (sourced from BG3's `Portraits/` DDS files) — see `tools/extract_icons.py PORTRAIT_ICON_TARGETS`. |
| `gate_<entrance>` × 29 + `gate_progressive_moonlight_towers` | 29 toggles + 1 counter (0–3) | Region-locking row (apworld v0.6.0+ `BlockEntrances` option). Each toggle flips on when the matching entrance-gate progression item is received (AP IDs 100–113 and 115–129); the counter ticks per Progressive Moonlight Towers item (AP id 114, max 3). The Lua autotracker auto-enables gates that aren't in the slot's pool (either because BlockEntrances is off, or because the player's goal stage skips them) so per-region access_rules pass correctly. |

**Locations** are organized into 35 tabs grouped by act (Nautiloid → Endgame), one per physical area. Each location is a clickable pin on a per-region map rendered from BG3's own minimap textures. Pins color by reachability: green = the region is reachable with the player's current Level Fragment count AND any required BlockEntrances gate items have been received; red = not yet. The access rules per region mirror the apworld's `regions.py` gates. Clicking a pin opens a popup with the section name verbatim from the apworld; left-click marks cleared, right-click reverts.

**Region-locking row** (apworld v0.6.0+ BlockEntrances) is two rows of the items grid: 29 entrance gate toggles (Nautiloid Control Panel through Endgame) plus one progressive counter for Moonlight Towers (1 unlocks Moonrise Towers, 2 the rooftop, 3 the Mindflayer Colony). When the slot has BlockEntrances off, the autotracker auto-enables every gate on connect so per-region access_rules referencing them pass trivially -- only the Level Fragment threshold gates pins for those slots. When BlockEntrances is on, gates outside the player's goal-pool (e.g. all Act 2 gates on a Rescue-Halsin slot) get the same treatment, and the in-pool gates flip on as the items arrive.

**Goal-progress row** lives in the items grid as a third row of 30 toggle items: the Halsin rescue (`udf_rescue_halsin`, fires on AP location id 114 which the apworld pairs with `Victory_Halsin`) plus one toggle per fight in the apworld's `UserDefinedFights.valid_keys`. Each toggle flips on when the corresponding AP location is checked (driven by the same AP event that updates the kill / quest section on the region tab). The full row matches `bg3_client.act1bosses + act2bosses` — the set of bosses/objectives the client tracks for any of the primary goals plus User Defined Fights. Toggles light up as you complete fights regardless of your goal — on non-applicable goals they sit greyed in the row as a passive "haven't done this yet" indicator. PopTracker doesn't support hiding layout widgets at runtime, so the row is always visible; the visual style follows the existing progression-item convention (greyed = off, bright = on).

**Visibility filtering** (driven by the Randomizer Options popup):
- Goal stage controls which regions are visible (e.g. Halsin hides Act 1 underdark + Act 2; Wwargaz adds Act 1 underdark; Kill Myrkul / Act 2 UDF show everything).
- Killsanity ON shows kill locations (IDs ≥ 10000); OFF hides them.
- Questsanity ON shows quest locations (IDs < 10000); OFF hides them.
- Containersanity ON shows container locations (IDs ≥ 100000); OFF hides them. Show Clutter OFF hides the clutter ones.

Pin shapes tell the check types apart: kills are squares, quests trapezoids, containers diamonds. (Universal Tracker draws every pin as a square.)

### Containersanity

Container checks come straight from the apworld's own container table, with its positions and logic regions. Most containers sit within a pin's width of another, so each container pin is a **cluster**: one pin per group of nearby containers (at most 25), one popup entry per container, named with its AP location name. A pin never mixes containers with different logic, so its colour is accurate.

Cellars, basements and other sub-levels sit far from their area on the game's maps, so they get their own zones. Large ones join their area's tab; small ones go on four **Interiors** tabs (Act 1, Act 2, Act 3 and Lower City), one labelled cell per interior. An Interiors pin keeps the gating and goal visibility of the area it belongs to.

## Useful PopTracker shortcuts

- **F11** (or Ctrl+H): toggle "hide cleared + hide unreachable locations" globally. Useful for sweeping noise off the maps once you've cleared a chunk.
- **F5**: reload pack from disk (after editing).
- **Right-click** any item to undo / decrement.

## Known limitations / planned work

- **A few sub-cells render dim** — some regions with sparse Granite virtual-texture coverage in the source game (e.g. cliff-trail strips) composite to a darker backdrop. The pins still place correctly.
- **Region display names are first-pass** — some sub-cell labels (e.g. "Town Basement", "HoH Morgue") are working names; a maintainer naming pass is planned.
- **PopTracker tabs are not visibility-aware** — when a goal hides a region's contents, the tab itself stays visible (just empty). PopTracker doesn't support hiding tabs at runtime per Lua state.
- **Five Act 3 bosses are placed approximately** — Gortash, Orin, Raphael, Cazador and Korrilla are global characters the game stores once, at a holding spot in an Act 1 or Act 2 level, and moves in by script, so there is no real Act 3 position to read. Each is pinned at the centre of the encounter it belongs to (Wyrm's Rock, the Temple of Bhaal, the House of Hope, Cazador's ritual room).
- **Lower City Central's small zone renders black** — the game has no minimap art at that spot. Its five pins are still placed and clickable.
- **Act 3 zone names are working names**, inferred from kill names and building tags; a naming pass is planned.
- **Act 3 overview:** areas that map doesn't show (the Astral Plane, Rivington, Wyrm's Crossing, the Iron Throne and three far-flung interiors) sit in a column in its right-hand margin rather than at a location.
- **Act 3 questsanity and BlockEntrances are untested upstream** — the apworld author notes both may not be playable yet in v0.8.0.
- **Containersanity is untested against a live seed.** The pack's container data matches the apworld's, but nobody has played a containersanity slot with it yet.
- **4 containers have no map position** — islands of one or two that sit away from every zone. They are in their tab's bottom panel.
- **Dense spots stay crowded**, Lower City's main zone most of all. In PopTracker, zooming in spreads pins apart; in Universal Tracker it doesn't.
- **Interiors tabs show even with Containersanity off** (empty). Zone names on them are working names taken from the game's level names.
- **Overview pins don't count containers.** A Lower City overview listing ~2,700 containers would be unusable, so an overview pin's colour reflects kills and quests only.
- **`characters_in_logic` is not applied** — apworld v0.7.0 can drop 55 character-specific locations when a companion is excluded. The pack still displays them; on a slot that excludes a tagged companion those checks are unobtainable.
- **Halsin rescue toggle on killsanity-only slots** — `udf_rescue_halsin` auto-fires via AP location id 114 (a quest location). On slots without questsanity the apworld never sends a location check for the rescue (the `Victory_Halsin` event has no AP id of its own), so the toggle stays off until manually clicked. A sanity-independent AP location id for the rescue would need an apworld change.
- **Equipment is bucketed by act gate, not per-item** — equipment shows as 4 act-gate counters (pre-Halsin / Act 1 / Act 2 / Act 3). Per-item visibility plus an `X of Y` denominator would need `add_act1a_treasure` / `add_act2_treasure` and a derived `expected_equipment_count` in slot_data; deferred until the apworld exposes those. The apworld currently exposes act gate (not rarity) on equipment, so per-rarity counters are not possible without scraping rarity from BG3 game data.
- **Trap variants are aggregated** — all 7 trap types collapse into a single `trap` counter. Per-trap-type counters were removed to keep the items grid focused on tracking that's relevant to most seeds (trap-disabled seeds simply leave the counter at 0).
- **Statsanity unsupported** — the apworld's statsanity option is hidden / unimplemented; the tracker treats it as off (stat-boost items are still counted, just not gated).

## Maintainer guide

Most maintenance happens by re-running `tools/generate_pack.py` after an apworld change. For deeper edits (region maps, NPC cache, texture pipeline, release zip, icon re-extraction) see **[MAINTAINER_GUIDE.md](MAINTAINER_GUIDE.md)** for the full pipeline.

### Common updates

- **Regenerate after an apworld change**: `python tools/generate_pack.py --apworld "<path to>/ArchipelagoBG3/worlds/bg3"`. The generator owns `locations/locations.json`, `maps/maps.json`, `layouts/regions.json`, `scripts/autotracking_generated.lua`, and placeholder `images/maps/<region>.png` files (do not hand-edit). Use `--check` first for a dry run.
- **New region slugs**: edit `REGION_DISPLAY_NAMES`, `REGION_ORDER`, `REGION_ACCESS_GATE`, `REGION_GOAL_VISIBILITY` in `tools/generate_pack.py`. The generator fails loudly on an unmapped slug.
- **Item ID / equipment / UDF / sanity changes**: see MAINTAINER_GUIDE.md for the per-case checklist.
- **Re-extract item / portrait icons**: see `tools/extract_icons.py` and MAINTAINER_GUIDE.md. Needs prior `Divine.exe extract-package` of `Icons.pak`, `Shared.pak`, and `Gustav_Textures.pak`.

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
