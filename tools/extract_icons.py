"""Extract BG3 item icons from atlas DDS files into 64x64 PNGs for the PopTracker pack.

Prereqs:
- Pillow installed (`pip install Pillow`).
- LSLib's Divine.exe has already extracted the base-game Icons.pak and the
  Public/Shared/GUI/Icons_Items*.lsx metadata from Shared.pak. See ../SPIKE_NOTES.md
  for the exact commands.

Each output PNG is cropped from the source atlas at the UV rect described in the
matching .lsx file and resized to 64x64. The mapping from PopTracker code to
(MapKey, atlas) lives in BG3_ICON_TARGETS / AP_ICON_TARGETS below.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageEnhance

# Map each .lsx filename to the relative path of the DDS atlas it indexes,
# inside an extracted Icons.pak.
LSX_TO_RELATIVE_DDS = {
    "Icons_Items.lsx":   "Public/Shared/Assets/Textures/Icons/Icons_Items.dds",
    "Icons_Items_2.lsx": "Public/Shared/Assets/Textures/Icons/Icons_Items_2.dds",
    "Icons_Items_3.lsx": "Public/Shared/Assets/Textures/Icons/Icons_Items_3.dds",
    "Icons_Items_4.lsx": "Public/Shared/Assets/Textures/Icons/Icons_Items_4.dds",
    "Icons_Items_5.lsx": "Public/Shared/Assets/Textures/Icons/Icons_Items_5.dds",
    "Icons_Items_6.lsx": "Public/Shared/Assets/Textures/Icons/Icons_Items_6.dds",
}

# Tracker code -> (atlas MapKey, lsx file containing the UV).
# Multiple output codes may share the same MapKey (e.g. all traps).
BG3_ICON_TARGETS = [
    ("boots_of_speed",     "Item_ARM_BootsOfSpeed",                       "Icons_Items_2.lsx"),
    ("shadow_lantern",     "Item_WPN_Moonlantern_Quarterstaff_A_0",       "Icons_Items_3.lsx"),
    ("spear_of_night",     "Item_Quest_SCE_SeluniteSpear",                "Icons_Items_3.lsx"),
    ("stat_boost",         "Item_ARM_HeadbandOfIntellect",                "Icons_Items.lsx"),
    ("equipment",          "GEN_Armor",                                   "Icons_Items.lsx"),
    ("filler",             "Item_TOOL_GEN_ThievesTools_A_Closed_A",       "Icons_Items.lsx"),
    ("trap_monster",       "Item_DEC_GEN_StuffedHead_Owlbear_Poor_A",     "Icons_Items_6.lsx"),
    ("trap_bleeding",      "Item_WPN_GOB_WolfPens_BloodFang",             "Icons_Items_2.lsx"),
    ("trap_stun",          "Item_PUZ_Lathander_Shockwave_Trap_A",         "Icons_Items_2.lsx"),
    ("trap_confusion",     "Item_LOOT_GEN_Throwable_Grenade_Confusion_A", "Icons_Items_2.lsx"),
    ("trap_sussur",        "Item_Quest_FOR_SussurBark",                   "Icons_Items_2.lsx"),
    ("trap_clown",         "Item_UNI_WYR_Circus_ClownHammer",             "Icons_Items_2.lsx"),
    ("trap_overburdened",  "Item_Quest_CMB_BoulderThrowable",             "Icons_Items_2.lsx"),
    # P3 status badges (goal stages + sanity toggles).
    ("goal_halsin",        "Item_UNI_Druid_Helmet_Circlet",               "Icons_Items.lsx"),
    ("goal_wwargaz",       "Item_ARM_Breastplate_Githyanki",              "Icons_Items_2.lsx"),
    ("goal_act1udf",       "Item_ARM_Shar_Crown_A_Broken",                "Icons_Items.lsx"),
    ("goal_myrkul",        "Item_WPN_HUM_Flail_Myrkul_A_0",               "Icons_Items_3.lsx"),
    ("goal_act2udf",       "Item_ARM_Shar_Crown_A",                       "Icons_Items_2.lsx"),
    ("killsanity_on",      "Item_WPN_HUM_Spear_A_0",                      "Icons_Items.lsx"),
    ("questsanity_on",     "Item_BOOK_Wizards_Tome_Generic_A",            "Icons_Items_2.lsx"),
    # UDF: Spectator gets its own item-atlas icon (no individual NPC portrait
    # found). The other 15 UDF toggles get NPC portraits, see PORTRAIT_ICON_TARGETS.
    ("udf_spectator",      "Item_DEC_GEN_Spectator_Trophy_A",             "Icons_Items.lsx"),
    # Generic catch-all trap counter (aggregates all 7000-range AP trap IDs).
    ("trap",               "Item_PUZ_GEN_Trap_Spikes_Metal_A",            "Icons_Items_2.lsx"),
    # Base icon for the 4 rarity-tinted equipment counters; tinted variants
    # are derived below in TINTED_ICONS.
    ("equipment_base",     "GEN_Armor",                                   "Icons_Items.lsx"),
]

# Derived "_off" variants: produced by desaturating + dimming an existing icon.
# Tuple is (output code, source code -- must appear above in BG3_ICON_TARGETS).
DERIVED_OFF_ICONS = [
    ("killsanity_off",  "killsanity_on"),
    ("questsanity_off", "questsanity_on"),
]

# Color-tinted variants: take a base icon and apply a color cast so the
# four equipment-by-act-gate counters are visually distinct. Colors are
# legacy rarity-themed (grey/green/blue/purple) from when the buckets
# were misinterpreted as rarity tiers; the underlying apworld data is
# act-gate (pre-Halsin / Act 1 / Act 2 / Act 3 per items.py:36).
# Tuple is (output_code, source_code, "#RRGGBB" tint).
TINTED_ICONS = [
    ("equipment_pre_halsin", "equipment_base", "#bdbdbd"),  # grey
    ("equipment_act1",       "equipment_base", "#1eff00"),  # green
    ("equipment_act2",       "equipment_base", "#0070dd"),  # blue
    ("equipment_act3",       "equipment_base", "#a335ee"),  # purple
]

# AP-mod icons (level fragment uses the AP project logo from the mod's atlas).
AP_ICON_TARGETS = [
    ("level_fragment", "original-logo"),
]

# NPC portrait icons. The 16 UDF (User Defined Fights) boss toggles get their
# own portrait sourced from BG3's individual portrait DDS files. These live as
# discrete files (not atlas entries) in Shared.pak and Gustav_Textures.pak
# under Mods/<Mod>/GUI/Assets/Portraits/. Tuple is
# (output_code, portrait_dds_filename, source_pak_id) where source_pak_id is
# one of {"shared", "gustav"} (mapped to the corresponding --portraits-shared-root
# and --portraits-gustav-root CLI arguments below).
PORTRAIT_ICON_TARGETS = [
    # Primary goal target: Halsin (rescue, goal=0). Not in UserDefinedFights
    # valid_keys but tracked in bg3_client.act1bosses; the autotracker wires
    # AP location 114 to this toggle. Portrait DDS path is a TODO -- fill in
    # the Mods/Gustav/GUI/Assets/Portraits/<uuid>-S_DEN_Halsin_*.DDS path
    # when re-extracting; the placeholder image is currently a copy of
    # images/items/goal_halsin.png.
    # ("udf_rescue_halsin",      "Mods/Gustav/GUI/Assets/Portraits/<TBD>-S_DEN_Halsin_<Icon>.DDS",                "gustav"),
    # Act 1 UDF
    ("udf_auntie_ethel",         "Mods/Shared/GUI/Assets/Portraits/0797903d-f96a-2ad2-2760-2b840f3f01b4-_(Icon_Hag).DDS",                          "shared"),
    ("udf_spider_queen",         "Mods/Shared/GUI/Assets/Portraits/bb6176cf-33cb-c60d-531c-57744789c198-_(Icon_Spider_Queen).DDS",                 "shared"),
    ("udf_bulette",              "Mods/Shared/GUI/Assets/Portraits/202ec2de-7b26-b5cf-ce8e-41113c358c0e-_(Icon_Bulette).DDS",                      "shared"),
    ("udf_nere",                 "Mods/Gustav/GUI/Assets/Portraits/a2729572-2571-914c-cbdb-56f0ac74d296-UND_KC_Nere_(Icon_Drow_Male).DDS",         "gustav"),
    ("udf_grym",                 "Mods/Gustav/GUI/Assets/Portraits/ef16dbf5-42b9-0876-31f5-f909ae7a9f02-_(Icon_Adamantine_Golem).DDS",             "gustav"),
    ("udf_ch_r_ai_w_wargaz",     "Mods/GustavDev/GUI/Assets/Portraits/9ebd4091-508d-7abb-e5e2-4e921d2c8806-CRE_Inquisitor_(Icon_Githyanki_Male).DDS", "gustav"),
    # Spectator: use the Spectator Trophy item icon from Icons_Items.lsx via BG3_ICON_TARGETS;
    # entry for that one is below at "udf_spectator".
    # Act 2 UDF
    ("udf_shambling_mound",      "Mods/GustavDev/GUI/Assets/Portraits/f63abb14-894a-fc32-a6ea-477b966a37c9-_(Icon_ShamblingMound).DDS",            "gustav"),
    ("udf_cursed_kuo_toa_chief", "Mods/Gustav/GUI/Assets/Portraits/fc614174-510c-afcf-4865-a7a746a8f447-_(Icon_Kuotoa).DDS",                       "gustav"),
    ("udf_malus_thorm",          "Mods/GustavDev/GUI/Assets/Portraits/40beced7-8472-bc99-6e2c-009cf9a6d452-_(Icon_Surgeon).DDS",                   "gustav"),
    ("udf_gerringothe_thorm",    "Mods/GustavDev/GUI/Assets/Portraits/978f6704-53ae-05f5-6ecd-31b80747951a-SCL_TWN_TollCollector_(Icon_TollCollector).DDS", "gustav"),
    ("udf_thisobald_thorm",      "Mods/GustavDev/GUI/Assets/Portraits/50fee350-fc53-929d-5b05-128a32018e55-EQP_Brewer_Mace_(Icon_Brewer).DDS",     "gustav"),
    ("udf_ch_r_ai_tska_an",      "Mods/Gustav/GUI/Assets/Portraits/f0656351-448d-1421-fde6-ba2e9ade0a7f-_(Icon_Githyanki_Male).DDS",               "gustav"),
    ("udf_yurgir",               "Mods/GustavDev/GUI/Assets/Portraits/c3c1482a-ef9e-c05e-093e-3e75b6581b0e-EQP_Orthon_Crossbow_Shortsword_(Icon_Orthon).DDS", "gustav"),
    ("udf_balthazar",            "Mods/GustavDev/GUI/Assets/Portraits/5dbde3ef-2023-bbbf-b24a-d23e25575d8b-SHA_Necromancer_(Icon_Necromancer).DDS", "gustav"),
    ("udf_myrkul",               "Mods/GustavDev/GUI/Assets/Portraits/6a7ff3a3-e87a-1e36-986f-0513f5ec085a-EQP_Apostle_(Icon_ApostleOfMyrkul).DDS", "gustav"),
]


def load_uv_map(lsx_path: Path) -> dict[str, tuple[float, float, float, float]]:
    """Parse an atlas .lsx and return MapKey -> (U1, U2, V1, V2)."""
    tree = ET.parse(lsx_path)
    out: dict[str, tuple[float, float, float, float]] = {}
    for node in tree.iter("node"):
        if node.get("id") != "IconUV":
            continue
        attrs = {a.get("id"): a.get("value") for a in node.findall("attribute")}
        key = attrs.get("MapKey")
        if not key:
            continue
        out[key] = (
            float(attrs["U1"]),
            float(attrs["U2"]),
            float(attrs["V1"]),
            float(attrs["V2"]),
        )
    return out


def crop_icon(atlas: Image.Image, uv: tuple[float, float, float, float], size: int) -> Image.Image:
    u1, u2, v1, v2 = uv
    w, h = atlas.size
    box = (round(u1 * w), round(v1 * h), round(u2 * w), round(v2 * h))
    return atlas.crop(box).resize((size, size), Image.LANCZOS)


def parse_args(argv: list[str]) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    pack_root = here.parent
    default_output = pack_root / "images" / "items"

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--icons-root",
        type=Path,
        required=True,
        help="Path to the directory where Divine.exe extracted Icons.pak. "
             "Should contain Public/Shared/Assets/Textures/Icons/*.dds.",
    )
    p.add_argument(
        "--shared-gui-root",
        type=Path,
        required=True,
        help="Path to the directory containing Icons_Items*.lsx files extracted from Shared.pak. "
             "Typically '<work_dir>/Public/Shared/GUI'.",
    )
    p.add_argument(
        "--ap-mod-root",
        type=Path,
        required=True,
        help="Path to the BG3ArchipelagoMod's per-mod Public folder (the one containing GUI/apAtlas.lsx "
             "and Assets/Textures/Icons/apAtlas.dds).",
    )
    p.add_argument(
        "--portraits-shared-root",
        type=Path,
        default=None,
        help="Path to an extracted Shared.pak (root of the extracted tree; should contain "
             "Mods/Shared/GUI/Assets/Portraits/...). Required when PORTRAIT_ICON_TARGETS has "
             "source_pak_id 'shared' entries.",
    )
    p.add_argument(
        "--portraits-gustav-root",
        type=Path,
        default=None,
        help="Path to an extracted Gustav_Textures.pak (root of the extracted tree; should contain "
             "Mods/Gustav/GUI/Assets/Portraits/... and Mods/GustavDev/GUI/Assets/Portraits/...). "
             "Required when PORTRAIT_ICON_TARGETS has source_pak_id 'gustav' entries.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=default_output,
        help=f"Where to write the PNG icons (default: {default_output}).",
    )
    p.add_argument(
        "--size",
        type=int,
        default=64,
        help="Output icon edge length in pixels (default: 64).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    uv_maps: dict[str, dict[str, tuple[float, float, float, float]]] = {}
    atlases: dict[str, Image.Image] = {}

    # Base-game icons.
    for code, map_key, lsx_name in BG3_ICON_TARGETS:
        if lsx_name not in uv_maps:
            uv_maps[lsx_name] = load_uv_map(args.shared_gui_root / lsx_name)
            atlases[lsx_name] = Image.open(args.icons_root / LSX_TO_RELATIVE_DDS[lsx_name]).convert("RGBA")
        uv = uv_maps[lsx_name].get(map_key)
        if uv is None:
            print(f"[WARN] {code}: MapKey {map_key!r} not found in {lsx_name}")
            continue
        icon = crop_icon(atlases[lsx_name], uv, args.size)
        out_path = args.output_dir / f"{code}.png"
        icon.save(out_path)
        print(f"[OK] {code:24s} <- {lsx_name} : {map_key}")

    # AP-mod icons.
    ap_lsx = args.ap_mod_root / "GUI/apAtlas.lsx"
    ap_dds = args.ap_mod_root / "Assets/Textures/Icons/apAtlas.dds"
    ap_uv = load_uv_map(ap_lsx)
    ap_atlas = Image.open(ap_dds).convert("RGBA")
    for code, map_key in AP_ICON_TARGETS:
        uv = ap_uv.get(map_key)
        if uv is None:
            print(f"[WARN] {code}: MapKey {map_key!r} not found in apAtlas.lsx")
            continue
        icon = crop_icon(ap_atlas, uv, args.size)
        out_path = args.output_dir / f"{code}.png"
        icon.save(out_path)
        print(f"[OK] {code:24s} <- apAtlas.lsx : {map_key}")

    # NPC portrait DDS files (UDF goal-progress row). Each is a discrete .DDS
    # under Mods/<Mod>/GUI/Assets/Portraits/ in either Shared.pak or
    # Gustav_Textures.pak. Pillow's DDS plugin can read them directly.
    portrait_roots = {
        "shared": args.portraits_shared_root,
        "gustav": args.portraits_gustav_root,
    }
    for code, rel_path, source_id in PORTRAIT_ICON_TARGETS:
        root = portrait_roots.get(source_id)
        if root is None:
            print(f"[WARN] {code}: portrait source {source_id!r} root not provided, skipping")
            continue
        src = root / rel_path
        if not src.exists():
            print(f"[WARN] {code}: portrait {src} not found")
            continue
        icon = Image.open(src).convert("RGBA").resize((args.size, args.size), Image.LANCZOS)
        icon.save(args.output_dir / f"{code}.png")
        print(f"[OK] {code:24s} <- {Path(rel_path).name}")

    # Derived "_off" variants: desaturate + dim an existing icon. Used for
    # toggle items where ON/OFF need to be visually distinct.
    for code, source_code in DERIVED_OFF_ICONS:
        src_path = args.output_dir / f"{source_code}.png"
        if not src_path.exists():
            print(f"[WARN] {code}: source icon {source_code}.png not found, skipping")
            continue
        img = Image.open(src_path).convert("RGBA")
        img = ImageEnhance.Color(img).enhance(0.0)        # full desaturation
        img = ImageEnhance.Brightness(img).enhance(0.55)  # dim
        img.save(args.output_dir / f"{code}.png")
        print(f"[OK] {code:24s} <- desaturate({source_code})")

    # Rarity-tinted variants: desaturate the source then multiply by the tint
    # color. Preserves alpha so the icon silhouette stays intact.
    for code, source_code, tint_hex in TINTED_ICONS:
        src_path = args.output_dir / f"{source_code}.png"
        if not src_path.exists():
            print(f"[WARN] {code}: source icon {source_code}.png not found, skipping")
            continue
        src = Image.open(src_path).convert("RGBA")
        grey = ImageEnhance.Color(src).enhance(0.0)
        r, g, b, a = grey.split()
        tint = Image.new("RGB", grey.size, tint_hex)
        tr, tg, tb = tint.split()
        # multiply (greyscale * tint) per channel
        out_r = Image.eval(Image.merge("L", (r,)), lambda v: v)
        from PIL import ImageChops
        mr = ImageChops.multiply(r.convert("L"), tr)
        mg = ImageChops.multiply(g.convert("L"), tg)
        mb = ImageChops.multiply(b.convert("L"), tb)
        out = Image.merge("RGBA", (mr, mg, mb, a))
        out.save(args.output_dir / f"{code}.png")
        print(f"[OK] {code:24s} <- tint({source_code}, {tint_hex})")

    expected = ({code for code, _, _ in BG3_ICON_TARGETS}
                | {code for code, _ in AP_ICON_TARGETS}
                | {code for code, _ in DERIVED_OFF_ICONS}
                | {code for code, _, _ in PORTRAIT_ICON_TARGETS}
                | {code for code, _, _ in TINTED_ICONS})
    actual = {p.stem for p in args.output_dir.glob("*.png")}
    missing = expected - actual
    if missing:
        print(f"\n[FAIL] Missing icons: {sorted(missing)}")
        return 1
    print(f"\n[DONE] All {len(expected)} icons written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
