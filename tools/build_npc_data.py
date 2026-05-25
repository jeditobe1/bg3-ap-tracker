"""Build the bg3_npc_data.json cache used by the pin projector.

One-time slow pass over an extracted BG3 install. Walks every level's
`Characters/_merged.lsf` for character placements with world positions,
and every `WorldMap/MiniMap.lsf` for the world-to-pixel transform. Cross-
references against the kill UUIDs the apworld's `bg3_locations.py` lists
(only those are interesting for the tracker) and writes a compact JSON
cache the pin projector can re-read cheaply.

Prereqs:
- BG3 install at the expected Steam path (or pass --bg3-data-dir explicitly).
- LSLib's Divine.exe at the path passed via --divine.
- A working temp directory (default $TEMP/bg3_npc_cache) where the .pak
  files are extracted once and the LSF -> LSX conversions are cached.

CLI:
    python tools/build_npc_data.py \
        --apworld <path>/ArchipelagoBG3/worlds/bg3 \
        --divine "<...>/Divine.exe" \
        [--bg3-data-dir "<...>/Baldurs Gate 3/Data"] \
        [--work-dir "<...>/bg3_npc_cache"] \
        [--out tools/bg3_npc_data.json]

Output schema:
    {
      "characters": {
        "<uuid>": {
          "name": "<S_..._scene_name>",
          "level": "<WLD_Main_A | SCL_Main_A | ...>",
          "position": [x, y, z],
          "icon": "<portrait_uuid-<EQP_..._>(Icon_TYPE)>" or null,
          "found_in": "<Gustav|GustavDev>/<Globals|Levels>/<level>",
          "building": "<BuildingUUID e.g. Underdark>" or null,
          "floor": int or null,
          "patch": "<patch_uuid>" or null
        }, ...
      },
      "levels": {
        "<level_name>": {
          "world_x": float, "world_z": float,
          "world_w": float, "world_h": float,
          "pixel_w": int, "pixel_h": int,
          "found_in": "<Gustav|GustavDev>/<Globals|Levels>/<level>"
        }, ...
      },
      "patches": {
        "<level_name>": [
          {
            "uuid": "<patch_uuid>",
            "building": "<BuildingUUID or empty>",
            "floor": int,
            "world_x": float, "world_z": float,   // CENTER of world bbox
            "world_w": float, "world_h": float,
            "pixel_w": int, "pixel_h": int,
            "texture_uuid": "<TextureFilePath UUID or empty>",
            "render_patches": bool,                // bank-rendered flag
            "trigger_id": "<patch's TriggerId>",
            "trigger_status": "trigger | nav_portal | orphan"
            // "trigger"    -- TriggerId referenced in Triggers/_merged
            //                 (live playable trigger zone)
            // "nav_portal" -- referenced in Ai/navigationPortals (region
            //                 transition view / loading-screen context)
            // "orphan"     -- no external reference. Note: orphans can
            //                 still back live worldspaces; we just lack
            //                 a metadata link. Use as a hint, not a filter.
          }, ...
        ], ...
      },
      "stats": { ... }
    }

The cache is intended to be committed. Regenerate after BG3 patches.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# Apworld bg3_locations.py rows look like:
#   ["Kill-S_GOB_DrunkGoblin_0c3404d4-af6f-4c3c-8873-101a79cc4d86", ["Gobs: Kill Crusher"], 0]
# The first column is a BG3 in-game flag name; for kill flags it's
# "Kill-S_<scene_name>_<8-4-4-4-12 UUID>".
KILL_FLAG_RE = re.compile(
    r"^Kill-(S_.*?)_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

# .pak files to fully extract for character + worldmap data.
SOURCE_PAKS = ["Gustav.pak"]
# Extras to also pull (smaller, may have additional Characters/_merged.lsf):
SOURCE_PAKS_EXTRA = ["GustavX.pak", "Patch8_HotFix8.pak"]


def parse_kill_uuids(apworld_path: Path) -> dict[str, tuple[str, list[str]]]:
    """Walk bg3_locations.py and return {uuid: (scene_name, [ap_location_names])}.

    Reads the apworld via AST so we don't import any AP runtime.
    """
    src = (apworld_path / "bg3_locations.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, tuple[str, list[str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # apworld convention: top-level list literals like LOCATIONS = [ ... ]
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not (isinstance(elt, ast.List) and len(elt.elts) >= 2):
                continue
            try:
                row = ast.literal_eval(elt)
            except Exception:
                continue
            if not (isinstance(row, list) and len(row) >= 2 and isinstance(row[0], str)):
                continue
            m = KILL_FLAG_RE.match(row[0])
            if not m:
                continue
            scene_name, uuid = m.group(1), m.group(2)
            ap_locs = row[1] if isinstance(row[1], list) else [row[1]]
            out[uuid] = (scene_name, [str(s) for s in ap_locs])
    return out


def run_divine(divine: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(divine), *args], check=False, capture_output=True, text=True
    )


def extract_pak(divine: Path, pak: Path, dest: Path) -> None:
    """Extract a full .pak to `dest` if not already present."""
    if dest.exists() and any(dest.iterdir()):
        print(f"[skip] {pak.name} already extracted at {dest}")
        return
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[extract] {pak.name} -> {dest}")
    r = run_divine(
        divine, "--action", "extract-package",
        "--source", str(pak), "--destination", str(dest), "--game", "bg3",
    )
    if r.returncode != 0:
        raise RuntimeError(f"Divine extract failed for {pak}: {r.stderr or r.stdout}")


def convert_lsf_dir(divine: Path, root: Path, glob: str, lsx_cache: Path) -> list[tuple[Path, Path]]:
    """Convert every matching LSF file under `root` to a sibling LSX in `lsx_cache`.

    Returns list of (original_lsf, output_lsx). Skips files already converted.
    """
    out: list[tuple[Path, Path]] = []
    lsx_cache.mkdir(parents=True, exist_ok=True)
    for lsf in root.rglob(glob):
        rel = lsf.relative_to(root).as_posix().replace("/", "__")
        lsx = lsx_cache / (rel + ".lsx")
        if not lsx.exists():
            r = run_divine(
                divine, "--action", "convert-resource",
                "--source", str(lsf), "--destination", str(lsx), "--game", "bg3",
            )
            if r.returncode != 0:
                print(f"[warn] convert failed for {lsf.name}: {r.stderr.strip()[:120]}")
                continue
        out.append((lsf, lsx))
    return out


# Per-character record extraction. Each Characters/_merged.lsx is a long XML;
# we slice it by GameObject node boundaries and pull a few fields per char.
GAMEOBJ_RE = re.compile(r'<node id="GameObjects">')
ATTR_RE = {
    "MapKey":       re.compile(r'id="MapKey"[^>]*value="([^"]+)"'),
    "Name":         re.compile(r'id="Name" type="LSString" value="([^"]+)"'),
    "CombatName":   re.compile(r'id="CombatName"[^>]*value="([^"]+)"'),
    "LevelName":    re.compile(r'id="LevelName" type="FixedString" value="([^"]+)"'),
    "Icon":         re.compile(r'id="Icon"[^>]*value="([^"]+)"'),
    "Stats":        re.compile(r'id="Stats"[^>]*value="([^"]+)"'),
    "Position":     re.compile(r'id="Position" type="fvec3" value="([^"]+)"'),
}


def iter_characters(lsx_path: Path):
    """Yield dicts of character fields from a Characters/_merged.lsx."""
    try:
        src = lsx_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[warn] read failed for {lsx_path}: {e}")
        return
    boundaries = [m.start() for m in GAMEOBJ_RE.finditer(src)]
    boundaries.append(len(src))
    for i in range(len(boundaries) - 1):
        chunk = src[boundaries[i]:boundaries[i + 1]]
        rec = {}
        for key, rx in ATTR_RE.items():
            m = rx.search(chunk)
            if m:
                rec[key] = m.group(1)
        if "MapKey" in rec:
            yield rec


def parse_minimap_lsx(lsx_path: Path) -> dict | None:
    """Parse a WorldMap/MiniMap.lsx and return the top-level world transform."""
    try:
        src = lsx_path.read_text(encoding="utf-8")
    except Exception:
        return None
    # Pull the WorldMapMetaData root node only (first one).
    fields = {}
    for k in ("Width", "Height", "WorldWidth", "WorldHeight", "WorldX", "WorldZ"):
        m = re.search(rf'id="{k}"[^>]*value="([^"]+)"', src)
        if m:
            fields[k] = m.group(1)
    if len(fields) < 6:
        return None
    try:
        return {
            "world_x": float(fields["WorldX"]),
            "world_z": float(fields["WorldZ"]),
            "world_w": float(fields["WorldWidth"]),
            "world_h": float(fields["WorldHeight"]),
            "pixel_w": int(fields["Width"]),
            "pixel_h": int(fields["Height"]),
        }
    except ValueError:
        return None


def parse_patch_lsx(lsx_path: Path) -> dict | None:
    """Parse a WorldMap/<uuid>.lsx patch file and return its metadata.

    Each patch defines a sub-region of its level's minimap with its own
    world bbox, BuildingUUID (e.g. 'Underdark'), Floor, RenderPatches flag,
    TriggerId (linking it to a trigger entity for the live worldspace),
    and TextureFilePath UUID (resolvable via the texture-bank format).
    The patch's filename UUID also matches TriggerId in the data; we use
    the filename as the canonical patch identifier.

    Note on coords: WorldX/WorldZ are the bbox CENTER (not the SW corner).
    Empirically confirmed by overlaying character spawn positions: with the
    centered convention 9/9 helm-fight pins land inside the rendered patches;
    with the SW-corner convention 0/9 do. Patch consumers must use the same
    convention.
    """
    try:
        src = lsx_path.read_text(encoding="utf-8")
    except Exception:
        return None
    fields = {}
    for k in ("Width", "Height", "WorldWidth", "WorldHeight", "WorldX", "WorldZ",
              "BuildingUUID", "Floor", "TextureFilePath", "RenderPatches", "TriggerId"):
        m = re.search(rf'id="{k}"[^>]*value="([^"]*)"', src)
        if m:
            fields[k] = m.group(1)
    if "WorldWidth" not in fields:
        return None
    try:
        return {
            "uuid": lsx_path.stem.replace(".lsf", ""),
            "building": fields.get("BuildingUUID", ""),
            "floor": int(fields.get("Floor", 0)),
            "world_x": float(fields["WorldX"]),
            "world_z": float(fields["WorldZ"]),
            "world_w": float(fields["WorldWidth"]),
            "world_h": float(fields["WorldHeight"]),
            "pixel_w": int(fields["Width"]),
            "pixel_h": int(fields["Height"]),
            "texture_uuid": fields.get("TextureFilePath", ""),
            # Bank-flag for whether the engine renders this patch's minimap.
            # Note: some live worldspaces have RenderPatches=False (the
            # minimap is hidden but the area is still played), and some
            # rendered patches have orphan triggers. Use both signals.
            "render_patches": fields.get("RenderPatches", "True") == "True",
            # Links this patch to a Trigger entity in the level. Cross-ref
            # against the level's Triggers/_merged for liveness.
            "trigger_id": fields.get("TriggerId", ""),
        }
    except (ValueError, KeyError):
        return None


# Gameplay files that reference WorldMap patch TriggerIds. Each patch
# stamps a TriggerId GUID into its LSF; we cross-ref against these to
# determine the patch's role in the live worldspace.
#
# Empirical signals discovered on TUT_Avernus_C (4 patches):
#   - patch0 Avernus     : TriggerId in Ai/navigationPortals  -> region transition
#   - patch1 Upper       : TriggerId in Triggers/_merged       -> live trigger zone
#   - patch2 BackHole    : TriggerId in Triggers/_merged       -> live trigger zone
#                          (but RenderPatches=False -- minimap hidden, area still played)
#   - patch3 Lower       : not present anywhere external       -> orphan or implicit
# In a level where every patch is a navigation portal but no triggers
# reference its UUID, we still want to render it: it just means BG3
# didn't author a per-patch FloorTrigger for that area.
TRIGGER_REF_SOURCES = {
    "trigger":    "Triggers/_merged.lsf",
    "nav_portal": "Ai/navigationPortals.lsf",
}


def collect_patch_trigger_status(level_extract_dirs: list[Path], lsx_cache: Path,
                                 divine: Path, patch_uuids: set[str]) -> dict[str, str]:
    """For every TriggerId in `patch_uuids`, find where it's referenced in
    this level's gameplay data. Returns {trigger_id: "trigger" | "nav_portal"
    | "orphan"}. Multiple matching dirs (Honour mode, Gustav, GustavDev) are
    unioned; the strongest signal wins (trigger > nav_portal > orphan).

    Cache key per converted file is `<absolute path hash>.lsx` to avoid the
    cross-mod collision the earlier rel-based key produced.
    """
    import hashlib

    status: dict[str, str] = {uid: "orphan" for uid in patch_uuids}
    rank = {"trigger": 2, "nav_portal": 1, "orphan": 0}

    for level_dir in level_extract_dirs:
        for label, rel in TRIGGER_REF_SOURCES.items():
            lsf = level_dir / rel
            if not lsf.is_file():
                continue
            # Hash the absolute path so different paks/mods don't collide.
            key = hashlib.md5(str(lsf).encode()).hexdigest()[:16]
            lsx = lsx_cache / f"trigger_refs_{key}.lsx"
            if not lsx.exists():
                r = run_divine(divine, "--action", "convert-resource",
                               "--source", str(lsf), "--destination", str(lsx),
                               "--game", "bg3")
                if r.returncode != 0:
                    continue
            try:
                src = lsx.read_text(encoding="utf-8")
            except Exception:
                continue
            for uid in patch_uuids:
                if uid in src and rank[label] > rank[status[uid]]:
                    status[uid] = label
    return status


def find_level_dirs(extract_root: Path, level_name: str) -> list[Path]:
    """Return every `<pak>/Mods/<Mod>/Levels/<level_name>/` dir across all
    extracted paks. Multiple mods can ship the same level (Honour mode,
    GustavDev, Gustav), and they each contribute different gameplay files —
    union them rather than picking just the first.
    """
    hits: list[Path] = []
    for pak_dir in sorted(extract_root.iterdir()):
        for cand in pak_dir.rglob(level_name):
            if cand.is_dir() and cand.parent.name == "Levels":
                hits.append(cand)
    return hits


def assign_building_to_position(x: float, z: float, patches: list[dict]) -> dict | None:
    """Find the smallest patch containing world position (x, z). Returns the
    patch dict or None if none contains it. 'Smallest' = smallest world area;
    more-specific patches win over broader ones (e.g. an interior patch wins
    over its parent outdoor patch when overlapping).

    WorldX/WorldZ are bbox CENTER (see parse_patch_lsx note); apply centered
    bounds when testing containment.

    Limitation: characters at the same XZ on different floors of a stacked
    building (e.g. Underdark has six floors at the same XZ) will all match
    here; we pick the smallest-area one, which is typically the most-specific
    floor. Y disambiguation isn't available because patches don't carry an
    altitude. Downstream consumers that need true floor assignment should
    cross-reference Triggers/_merged for floor-bounded zones.
    """
    candidates = []
    for patch in patches:
        x0 = patch["world_x"] - patch["world_w"] / 2
        x1 = patch["world_x"] + patch["world_w"] / 2
        z0 = patch["world_z"] - patch["world_h"] / 2
        z1 = patch["world_z"] + patch["world_h"] / 2
        if x0 <= x <= x1 and z0 <= z <= z1:
            area = patch["world_w"] * patch["world_h"]
            candidates.append((area, patch))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])  # smallest area first
    return candidates[0][1]


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    pack_root = here.parent
    default_temp = Path(os.environ.get("TEMP", "/tmp")) / "bg3_npc_cache"

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apworld", type=Path, required=True,
                   help="Path to the BG3 apworld dir (containing bg3_locations.py).")
    p.add_argument("--divine", type=Path, required=True,
                   help="Path to LSLib Divine.exe.")
    p.add_argument("--bg3-data-dir", type=Path,
                   default=Path(r"C:/Program Files (x86)/Steam/steamapps/common/Baldurs Gate 3/Data"),
                   help="Path to the BG3 install's Data dir (containing Gustav.pak etc.).")
    p.add_argument("--work-dir", type=Path, default=default_temp,
                   help=f"Scratch dir for pak extracts + LSF->LSX cache (default: {default_temp}).")
    p.add_argument("--out", type=Path, default=here / "bg3_npc_data.json",
                   help=f"Output JSON path (default: {here / 'bg3_npc_data.json'}).")
    p.add_argument("--all-characters", action="store_true",
                   help="Index every character with a Position, not just kill-UUID matches. "
                        "Useful for debugging the cache; output is much larger.")
    args = p.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.divine.exists():
        print(f"[fail] Divine.exe not found at {args.divine}")
        return 1
    if not args.bg3_data_dir.exists():
        print(f"[fail] BG3 Data dir not found at {args.bg3_data_dir}")
        return 1
    if not (args.apworld / "bg3_locations.py").exists():
        print(f"[fail] apworld bg3_locations.py not found under {args.apworld}")
        return 1

    print(f"[info] apworld    = {args.apworld}")
    print(f"[info] divine     = {args.divine}")
    print(f"[info] bg3 data   = {args.bg3_data_dir}")
    print(f"[info] work dir   = {args.work_dir}")
    print(f"[info] output     = {args.out}")

    # 1. Inventory kill UUIDs from the apworld.
    kill_uuids = parse_kill_uuids(args.apworld)
    print(f"[ok] apworld kill UUIDs: {len(kill_uuids)}")
    interesting_uuids = set(kill_uuids) if not args.all_characters else None

    # 2. Extract source paks (lazy - skip if already extracted).
    extract_root = args.work_dir / "extract"
    for pak_name in SOURCE_PAKS + SOURCE_PAKS_EXTRA:
        pak = args.bg3_data_dir / pak_name
        if not pak.exists():
            print(f"[warn] {pak_name} not found, skipping")
            continue
        dest = extract_root / pak_name.replace(".pak", "")
        extract_pak(args.divine, pak, dest)

    # 3. Convert all Characters/_merged.lsf, WorldMap/MiniMap.lsf, and
    #    WorldMap/<patch>.lsf files. The patch files carry per-Building
    #    sub-region metadata (BuildingUUID, Floor, world bbox, texture UUID)
    #    that we use to assign each character to a building and that will
    #    feed the eventual real-texture map rendering.
    lsx_cache = args.work_dir / "lsx_cache"
    char_lsx: list[tuple[Path, Path]] = []
    minimap_lsx: list[tuple[Path, Path]] = []
    patch_lsx: list[tuple[Path, Path]] = []
    for d in sorted(extract_root.iterdir()) if extract_root.exists() else []:
        print(f"[info] converting LSF -> LSX under {d.name}/")
        char_lsx += convert_lsf_dir(args.divine, d, "Characters/_merged.lsf", lsx_cache)
        minimap_lsx += convert_lsf_dir(args.divine, d, "WorldMap/MiniMap.lsf", lsx_cache)
        # Patch files are everything else under WorldMap/ that isn't MiniMap.
        all_worldmap = convert_lsf_dir(args.divine, d, "WorldMap/*.lsf", lsx_cache)
        patch_lsx += [(lsf, lsx) for lsf, lsx in all_worldmap if lsf.name != "MiniMap.lsf"]
    print(f"[ok] converted {len(char_lsx)} character files, {len(minimap_lsx)} minimaps, {len(patch_lsx)} patches")

    # 4. Walk patches and build per-level patches list. We need this before
    #    walking characters so we can assign each character to a Building.
    patches_by_level: dict[str, list[dict]] = {}
    for lsf_path, lsx_path in patch_lsx:
        try:
            rel = lsf_path.relative_to(extract_root)
            parts = rel.parts
            # Path: <pak>/<Mods|Public>/<Mod>/<Globals|Levels>/<LEVEL>/WorldMap/<patch>.lsf
            level_name = parts[-3]
        except (ValueError, IndexError):
            continue
        patch = parse_patch_lsx(lsx_path)
        if patch is None:
            continue
        patches_by_level.setdefault(level_name, []).append(patch)
    total_patches = sum(len(v) for v in patches_by_level.values())
    print(f"[ok] patches: {total_patches} parsed across {len(patches_by_level)} levels")

    # 5. Walk characters, collect those whose MapKey is in our interest set
    #    and assign each to a Building via the level's patches.
    characters: dict[str, dict] = {}
    char_count_total = 0
    char_count_kept = 0
    building_hits = 0
    for lsf_path, lsx_path in char_lsx:
        try:
            rel = lsf_path.relative_to(extract_root)
            found_in = "/".join(rel.parts[:-2])  # drop Characters/_merged.lsf
        except ValueError:
            found_in = lsx_path.name
        for rec in iter_characters(lsx_path):
            char_count_total += 1
            uuid = rec.get("MapKey")
            if not uuid:
                continue
            if interesting_uuids is not None and uuid not in interesting_uuids:
                continue
            pos_raw = rec.get("Position")
            if not pos_raw:
                continue
            try:
                x, y, z = (float(v) for v in pos_raw.split())
            except ValueError:
                continue
            if uuid in characters:
                continue
            level_name = rec.get("LevelName") or ""
            patch = assign_building_to_position(x, z, patches_by_level.get(level_name, []))
            if patch is not None:
                building_hits += 1
            characters[uuid] = {
                "name": rec.get("Name") or rec.get("CombatName") or "",
                "level": level_name,
                "position": [x, y, z],
                "icon": rec.get("Icon"),
                "found_in": found_in,
                "building": patch["building"] if patch else None,
                "floor": patch["floor"] if patch else None,
                "patch": patch["uuid"] if patch else None,
            }
            char_count_kept += 1
    print(f"[ok] characters: walked {char_count_total}, kept {char_count_kept}, building-assigned {building_hits}")

    # 6a. For each level's patches, find each patch's TriggerId role in the
    #     live worldspace -- "trigger" (in Triggers/_merged), "nav_portal"
    #     (in Ai/navigationPortals), or "orphan" (no external reference).
    #     This is a per-patch annotation, not a per-level aggregate, because
    #     each patch independently represents a worldspace facet.
    trigger_status_hits = 0
    for level_name, level_patches in patches_by_level.items():
        if not level_patches:
            continue
        level_dirs = find_level_dirs(extract_root, level_name)
        if not level_dirs:
            continue
        patch_ids = {p["trigger_id"] for p in level_patches if p.get("trigger_id")}
        status_map = collect_patch_trigger_status(level_dirs, lsx_cache, args.divine, patch_ids)
        for p in level_patches:
            p["trigger_status"] = status_map.get(p.get("trigger_id", ""), "orphan")
            if p["trigger_status"] != "orphan":
                trigger_status_hits += 1
    print(f"[ok] trigger-status annotated: {trigger_status_hits} non-orphan patches across {len(patches_by_level)} levels")

    # 6. Walk minimaps, build per-level transform table.
    levels: dict[str, dict] = {}
    for lsf_path, lsx_path in minimap_lsx:
        try:
            rel = lsf_path.relative_to(extract_root)
            parts = rel.parts
            # Path shape: <pak>/<Mods|Public>/<Mod>/<Globals|Levels>/<LEVEL>/WorldMap/MiniMap.lsf
            level_name = parts[-3]
            found_in = "/".join(parts[:-2])
        except (ValueError, IndexError):
            continue
        meta = parse_minimap_lsx(lsx_path)
        if meta is None:
            continue
        # If a level appears in both Gustav and GustavDev, prefer Gustav (earlier
        # in iteration order); skip duplicates.
        if level_name in levels:
            continue
        meta["found_in"] = found_in
        levels[level_name] = meta
    print(f"[ok] levels: indexed {len(levels)}")

    # 7. Coverage stats.
    matched_uuids = set(characters) & set(kill_uuids)
    with_position = sum(1 for u in matched_uuids if characters[u]["position"])
    with_building = sum(1 for u in matched_uuids if characters[u].get("building"))
    print(f"[ok] kill-UUID coverage: {len(matched_uuids)}/{len(kill_uuids)} matched, "
          f"{with_position} with position, {with_building} with building")
    if matched_uuids:
        sample = next(iter(matched_uuids))
        s = characters[sample]
        print(f"[sample] uuid={sample}")
        print(f"         name={s['name']}, level={s['level']}, building={s['building']}, "
              f"floor={s['floor']}, position={s['position']}")

    # 8. Write.
    trigger_breakdown = {"trigger": 0, "nav_portal": 0, "orphan": 0}
    for level_patches in patches_by_level.values():
        for p in level_patches:
            trigger_breakdown[p.get("trigger_status", "orphan")] += 1
    out_payload = {
        "characters": characters,
        "levels": levels,
        "patches": patches_by_level,
        "stats": {
            "kill_uuids_in_apworld": len(kill_uuids),
            "kill_uuids_matched": len(matched_uuids),
            "kill_uuids_with_position": with_position,
            "kill_uuids_with_building": with_building,
            "levels_indexed": len(levels),
            "patches_indexed": total_patches,
            "characters_walked": char_count_total,
            "patches_by_trigger_status": trigger_breakdown,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_payload, indent=2, sort_keys=True), encoding="utf-8")
    size_kb = args.out.stat().st_size / 1024
    print(f"[done] wrote {args.out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
