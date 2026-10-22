"""Plan the map zones that Containersanity needs, from the apworld's container positions.

The region maps were framed around kills. Containers sit in many more places --
cellars, basements and sub-levels stored at their own far-off coordinates -- so a
third of them fell outside every zone. This script reads every selected container
(through tools/container_pins.py), routes it against each tab's current zones, and
writes tools/container_zones.json, which render_multi_zone_map.py applies on top of
its REGION_DEFINITIONS:

  include      {tab: {zone title: [x0, z0, x1, z1]}}
               Widen an existing zone's frame to take in nearby containers: an
               island whose union with the zone's frame is at most NEAR_GROWTH
               times the frame's area and pushes it out by no more than
               NEAR_REACH_FRAC of its longer side (so a small room keeps its
               scale), and containers routed to the zone by its filter box but
               sitting outside its frame.
  define       {tab: region definition}
               Single-zone tabs drawn by render_region_map.py (Beach, Crypt, ...)
               are taken over by the multi-zone renderer, starting from the frame
               they last rendered with. Always, even when no zone changes, so
               every tab with containers renders the same way each time.
  extra_zones  {tab: [zone definition, ...]}
               A far island with at least OWN_MIN containers becomes a new zone on
               its own tab, in a new right-hand column.
  annex        {annex slug: region definition}
               Smaller far islands (at least ANNEX_MIN containers) become zones on
               an Interiors tab: one per act, plus one just for Lower City.
               Each zone records its "source_tab", which container_pins.py uses
               so a container only routes into an interior of its own tab.

Islands below ANNEX_MIN stay in the map's bottom panel.

Re-run this whenever the apworld's container table or a region's zones change, then
re-render the affected maps and regenerate the pack:

    python tools/plan_container_zones.py --apworld <path>/worlds/bg3
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PACK = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import container_pins as CP          # noqa: E402
import generate_pack as G            # noqa: E402
import render_multi_zone_map as R    # noqa: E402

ISLAND_GAP = 40.0      # world units: containers closer than this share an island
NEAR_GROWTH = 3.0      # max frame-area growth for folding an island into a zone
NEAR_REACH_FRAC = 0.25 # ... and it may push the frame out by at most this share of
NEAR_REACH_MIN = 30.0  # the zone's longer side, or this many world units if more
OWN_MIN = 60           # a far island this big gets a zone on its own tab
ANNEX_MIN = 3          # smaller than this stays in the bottom panel
PAD = 15.0             # world units of padding around an island's containers
MIN_SIDE = 24.0        # smallest zone frame side, so a cupboard isn't zoomed to fill a cell
FILTER_PAD = 6.0       # routing box = island extent +- this

ANNEX_TABS = {
    "Act 1": "act1_interiors",
    "Act 2": "act2_interiors",
    "Act 3": "act3_interiors",
}
LOWER_CITY_ANNEX = "lower_city_interiors"
LOWER_CITY_TABS = {"lower_city_east"}

# Titles for zones whose level name doesn't read well. Keyed by (tab, level).
TITLE_OVERRIDES = {
    ("rivington", "BGO_WyrmsCrossing_C"): "Rivington",
    ("grove", "WLD_RangerCamp_I"): "Emerald Grove",
    ("grove", "WLD_DruidSubs_B"): "Grove Interiors",
    ("iron_throne", "CTY_DockWarehouseBasement_A"): "Dock Warehouse",
    ("cazador", "CTY_CazSideRooms_A"): "Side Rooms",
    ("last_light", "SCL_VillageSubs_E"): "Inn Cellar",
    ("blighted_village", "WLD_VillageSubs_C"): "Village Cellars",
}


def level_title(level: str) -> str:
    """CTY_Thumbo_Basement_A -> 'Thumbo Basement'."""
    s = re.sub(r"^(CTY|BGH|BGO|WLD|SCL|CRE|END|IRN|INT|PLT_BGO|LT_CTY)_", "", level, flags=re.I)
    s = re.sub(r"(_(A|B|C|D|E|F|G|H|I|ART|Art|Backup|Push))+$", "", s)
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def patch_bbox(p):
    return (p["world_x"] - p["world_w"] / 2, p["world_z"] - p["world_h"] / 2,
            p["world_x"] + p["world_w"] / 2, p["world_z"] + p["world_h"] / 2)


def area(b):
    return max(1.0, b[2] - b[0]) * max(1.0, b[3] - b[1])


def union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def extent(rows, pad=0.0):
    xs = [r["x"] for r in rows]
    zs = [r["z"] for r in rows]
    return (min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)


def frame_for(rows):
    """Padded frame for a new zone, at least MIN_SIDE on each side."""
    x0, z0, x1, z1 = extent(rows)
    px = max(PAD, 0.15 * (x1 - x0))
    pz = max(PAD, 0.15 * (z1 - z0))
    x0, x1, z0, z1 = x0 - px, x1 + px, z0 - pz, z1 + pz
    if x1 - x0 < MIN_SIDE:
        c = (x0 + x1) / 2
        x0, x1 = c - MIN_SIDE / 2, c + MIN_SIDE / 2
    if z1 - z0 < MIN_SIDE:
        c = (z0 + z1) / 2
        z0, z1 = c - MIN_SIDE / 2, c + MIN_SIDE / 2
    return [round(v, 1) for v in (x0, z0, x1, z1)]


def islands(rows):
    left = sorted(rows, key=lambda r: r["id"])
    out = []
    while left:
        grp = [left.pop(0)]
        i = 0
        while i < len(grp):
            g = grp[i]
            near = [q for q in left if abs(q["x"] - g["x"]) <= ISLAND_GAP and abs(q["z"] - g["z"]) <= ISLAND_GAP]
            if near:
                ids = {q["id"] for q in near}
                left = [q for q in left if q["id"] not in ids]
                grp.extend(near)
            i += 1
        out.append(grp)
    return sorted(out, key=lambda g: (-len(g), g[0]["id"]))


def best_root(rows, patches_by_level, fallback, prefer=()):
    """The root level whose minimap patches cover the most of these containers.

    The tab's own levels (`prefer`) win whenever they cover any of them: the
    root levels share one coordinate range, so an Act 1 cellar can sit under
    an unrelated Act 3 patch by coincidence."""
    for lv in prefer:
        bbs = [patch_bbox(p) for p in patches_by_level.get(lv, [])]
        n = sum(1 for r in rows if any(b[0] <= r["x"] <= b[2] and b[1] <= r["z"] <= b[3] for b in bbs))
        if n:
            return lv, n
    best, best_n = fallback, 0
    for lv, patches in patches_by_level.items():
        bbs = [patch_bbox(p) for p in patches]
        n = sum(1 for r in rows if any(b[0] <= r["x"] <= b[2] and b[1] <= r["z"] <= b[3] for b in bbs))
        if n > best_n:
            best, best_n = lv, n
    return best, best_n


def act_of(tab):
    for act, slugs in G.ACT_GROUPS.items():
        if tab in slugs:
            return act
    raise KeyError(tab)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apworld", type=Path, required=True)
    args = p.parse_args(argv)

    rows = CP.container_rows(args.apworld)
    proj = json.loads((TOOLS / "projections.json").read_text(encoding="utf-8"))["regions"]
    patches_by_level = json.loads((TOOLS / "bg3_npc_data.json").read_text(encoding="utf-8"))["patches"]
    by_tab: dict[str, list] = {}
    for r in rows:
        by_tab.setdefault(G.MERGED_INTO.get(r["region"], r["region"]), []).append(r)

    plan = {"include": {}, "define": {}, "extra_zones": {}, "annex": {}}
    report = Counter()
    for tab in G.REGION_ORDER:
        trows = by_tab.get(tab)
        if not trows or tab not in proj:
            continue
        rd = R.REGION_DEFINITIONS.get(tab)
        if rd is None:
            # A render_region_map.py tab: start from the frame it renders today.
            z = proj[tab]["zones"][0]
            base = z.get("world_bbox_base", z["world_bbox"])
            rd = {"level": z["level"], "zones": [{
                "title": G.REGION_DISPLAY_NAMES[tab], "char_building": ["*"], "patch_building": ["*"],
                "world_bbox_override": [round(v, 1) for v in base]}]}
            plan["define"][tab] = rd
            zones_now = [{"title": rd["zones"][0]["title"], "level": z["level"],
                          "world_bbox": list(base)}]
        else:
            # Frames before any widening this plan applied: the override, or a
            # kill-derived zone's frame as the renderer computed it. Using the
            # widened frames would make the next run drop its own widening.
            maps = [(tab, rd)]
            if tab in G.MAP_SPLITS and G.MAP_SPLITS[tab][0] in R.REGION_DEFINITIONS:
                sec = G.MAP_SPLITS[tab][0]
                maps.append((sec, R.REGION_DEFINITIONS[sec]))
            zones_now = []
            for mp, mrd in maps:
                rendered = {z["title"]: z for z in proj.get(mp, {"zones": []})["zones"]}
                for zd in mrd["zones"]:
                    rz = rendered.get(zd["title"])
                    if rz is None and not zd.get("world_bbox_override"):
                        print(f"[warn] {mp}: zone {zd['title']!r} has no frame (not rendered, "
                              f"no override); skipped")
                        continue
                    bb = zd.get("world_bbox_override") or rz.get("world_bbox_base", rz["world_bbox"])
                    zn = {"title": zd["title"], "level": zd.get("level", mrd["level"]),
                          "world_bbox": list(bb), "_map": mp}
                    if zd.get("world_bbox_filter"):
                        zn["world_bbox_filter"] = list(zd["world_bbox_filter"])
                    zones_now.append(zn)

        include: dict[str, tuple] = {}
        off = []
        for r in trows:
            zn = CP._zone_for(zones_now, r["x"], r["z"])
            if zn is None:
                off.append(r)
                continue
            b = zn["world_bbox"]
            if not (b[0] <= r["x"] <= b[2] and b[1] <= r["z"] <= b[3]):
                pt = (r["x"] - PAD, r["z"] - PAD, r["x"] + PAD, r["z"] + PAD)
                include[(zn.get("_map", tab), zn["title"])] = union(include.get((zn.get("_map", tab), zn["title"]), pt), pt)
                report["clamped"] += 1

        extra, col = [], []
        for isl in islands(off):
            lv, covered = best_root(isl, patches_by_level, rd["level"],
                                    prefer=list(dict.fromkeys(z["level"] for z in zones_now)))
            ib = extent(isl, PAD)
            def reach_ok(z):
                b = z["world_bbox"]
                reach = max(b[0] - ib[0], ib[2] - b[2], b[1] - ib[1], ib[3] - b[3], 0.0)
                return reach <= max(NEAR_REACH_MIN, NEAR_REACH_FRAC * max(b[2] - b[0], b[3] - b[1]))
            cands = [(area(union(z["world_bbox"], ib)) / area(z["world_bbox"]), z)
                     for z in zones_now if z["level"] == lv and reach_ok(z)]
            growth, zbest = min(cands, key=lambda c: c[0]) if cands else (math.inf, None)
            if growth <= NEAR_GROWTH:
                include[(zbest.get("_map", tab), zbest["title"])] = union(include.get((zbest.get("_map", tab), zbest["title"]), ib), ib)
                report["near"] += len(isl)
                continue
            if len(isl) < ANNEX_MIN:
                report["panel"] += len(isl)
                continue
            dom = Counter(r["level"] for r in isl).most_common(1)[0][0]
            prefix = Counter(r["prefix"] for r in isl).most_common(1)[0][0]
            title = TITLE_OVERRIDES.get((tab, dom))
            if title is None:
                title = prefix if prefix not in (G.REGION_DISPLAY_NAMES[tab], "Lower City East",
                                                  "Lower City Central", "Lower City West") else level_title(dom)
                if title in ("Main", ""):   # an island on the root map itself
                    title = prefix
            zone = {"title": title, "level": lv, "char_building": ["*"], "patch_building": ["*"],
                    "world_bbox_override": frame_for(isl),
                    "world_bbox_filter": [round(v, 1) for v in extent(isl, FILTER_PAD)],
                    "source_tab": tab, "_containers": len(isl), "_covered": covered}
            if len(isl) >= OWN_MIN and tab not in LOWER_CITY_TABS:
                extra.append(zone)
                report["own"] += len(isl)
            else:
                annex = LOWER_CITY_ANNEX if tab in LOWER_CITY_TABS else ANNEX_TABS[act_of(tab)]
                plan["annex"].setdefault(annex, {"level": lv, "auto_grid": True, "zones": []})["zones"].append(zone)
                report["annex"] += len(isl)

        for (mp, title), b in include.items():
            plan["include"].setdefault(mp, {})[title] = [round(v, 1) for v in b]
        if extra:
            plan["extra_zones"][tab] = extra

    plan["include"] = dict(sorted(plan["include"].items()))

    # Unique titles within each annex.
    for adef in plan["annex"].values():
        seen = Counter()
        for z in adef["zones"]:
            seen[z["title"]] += 1
            if seen[z["title"]] > 1:
                z["title"] = f"{z['title']} {seen[z['title']]}"

    out = TOOLS / "container_zones.json"
    out.write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    print(f"[done] wrote {out.relative_to(PACK)}")
    print(f"  containers: {report['clamped']} re-framed (clamped), {report['near']} folded into a "
          f"nearby zone, {report['own']} in {sum(len(v) for v in plan['extra_zones'].values())} new "
          f"tab zones, {report['annex']} in {sum(len(a['zones']) for a in plan['annex'].values())} "
          f"interior zones, {report['panel']} left in panels")
    print(f"  zones widened on {len(plan['include'])} tabs; single-zone tabs taken over: "
          f"{sorted(plan['define'])}; interiors tabs: "
          + ", ".join(f"{k} ({len(v['zones'])})" for k, v in plan["annex"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
