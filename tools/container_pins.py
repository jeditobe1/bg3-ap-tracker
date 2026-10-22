"""Containersanity locations for the pack, built from the apworld's containers.py.

containers.py is the apworld's hand-maintained table of every openable container:
instance uuid, level, position, category and the flags that decide which rows are
locations. It imports only `math`, so it is loaded directly (in isolation, under a
private module name) and its own select(), world_xz(), logic_region() and
location_name() are used as-is. Re-implementing its display polygons and per-level
transforms here would drift from the apworld's location names.

There are ~10,500 container checks, and on a region map most of them sit within a
pin's width of another, so each map pin is a CLUSTER of nearby containers, one
section per container. Universal Tracker shapes the rules:
  - a section's name must equal its AP location name exactly (UT matches checks by
    section name), so one section per container -- no `item_count` aggregation;
  - UT keys pins by exact (x, y), so no two pins on a map may share a pixel.

A cluster never mixes logic regions (so its pin colour is truthful), and never
mixes clutter with chests/bags (so "Show clutter" can hide clutter pins whole).
Clusters are capped at CLUSTER_CAP containers; bigger groups are split and their
pins fanned out around the group's centre.

Containers whose position falls outside every zone of their tab's map go to the
map's bottom panel, after the quest pins, grouped by the level they are stored in.
"""

from __future__ import annotations

import importlib.util
import math
import re
from pathlib import Path

CLUSTER_CAP = 25
LOOT = ("chest", "bag")   # category class 1; "clutter" is class 2
# One icon and one pin look for every container, whatever its category or
# however many a pin holds, so a container pin always reads as "container".
ICON = "container_chest"
KIND_LABEL = {"loot": "Chests & bags", "clutter": "Clutter"}
# PopTracker pin shapes, one per check type: kills keep the default rect,
# quests are trapezoids (generate_pack.py), containers are diamonds. UT ignores
# shape and draws squares. PopTracker has no circle shape.
SHAPE = "diamond"
PIN_FRAC = 0.75   # container pins are 75% of a kill pin, at every cluster size


def load_container_module(apworld_path: Path):
    """Import the apworld's containers.py on its own, without the rest of the world."""
    src = apworld_path / "containers.py"
    spec = importlib.util.spec_from_file_location("_bg3_apworld_containers", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def container_rows(apworld_path: Path) -> list[dict]:
    """Every selected container row as a dict the pack can use."""
    C = load_container_module(apworld_path)
    out = []
    for row in C.select():
        x, z = C.world_xz(row)
        out.append({
            "id": 100000 + row[0],
            "name": C.location_name(row),
            "region": row[5],
            "logic": C.logic_region(row),
            "level": row[3],
            "category": row[12],
            "kind": "loot" if row[12] in LOOT else "clutter",
            "prefix": C.display_region(row),
            "x": x,
            "z": z,
        })
    return out


def _zone_for(zones: list[dict], x: float, z: float) -> dict | None:
    """The zone a container belongs to: one whose routing box (world_bbox_filter)
    holds it, else one whose framing box does; the smallest frame wins, so a
    magnified inset beats the wider view it sits inside."""
    def area(zn):
        b = zn["world_bbox"]
        return (b[2] - b[0]) * (b[3] - b[1])

    def holds(b):
        return b[0] <= x <= b[2] and b[1] <= z <= b[3]

    routed = [zn for zn in zones if zn.get("world_bbox_filter") and holds(zn["world_bbox_filter"])]
    framed = [zn for zn in zones if holds(zn["world_bbox"])]
    pick = routed or framed
    return min(pick, key=area) if pick else None


def zone_for_row(zones: list[dict], row: dict) -> dict | None:
    """_zone_for, limited to the zones this row may use: a zone that names a
    `source_tab` (an Interiors zone) only takes containers from that tab."""
    tab = row.get("rule_tab")
    usable = [zn for zn in zones if zn.get("source_tab") in (None, tab)]
    return _zone_for(usable, row["x"], row["z"])


def _project(x: float, z: float, zone: dict) -> tuple[float, float]:
    """World (x, z) -> canvas px, unclamped (same fit as project.py)."""
    wx0, wz0, wx1, wz1 = zone["world_bbox"]
    cx0, cy0, cw, ch = zone["canvas_rect"]
    ww, wh = max(1e-6, wx1 - wx0), max(1e-6, wz1 - wz0)
    s = min(cw / ww, ch / wh)
    ox, oy = (cw - ww * s) / 2, (ch - wh * s) / 2
    px = cx0 + ox + (x - wx0) * s
    py = cy0 + oy + wh * s - (z - wz0) * s
    # Clamp into the zone's own rect, as project.py does for kills: a point
    # routed to a zone by its filter box can sit just outside its frame.
    return min(max(px, cx0), cx0 + cw), min(max(py, cy0), cy0 + ch)


def _leader_clusters(points: list[tuple[float, float, dict]], radius: float) -> list[list]:
    """Greedy clustering: seed at the densest remaining point, take everything
    within `radius`, repeat. Deterministic for a given input order."""
    left = sorted(points, key=lambda p: (p[0], p[1], p[2]["id"]))
    r2 = radius * radius
    out = []
    while left:
        best_i, best_n = 0, -1
        for i, p in enumerate(left):
            n = sum(1 for q in left if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= r2)
            if n > best_n:
                best_i, best_n = i, n
        sx, sy = left[best_i][0], left[best_i][1]
        mem = [q for q in left if (sx - q[0]) ** 2 + (sy - q[1]) ** 2 <= r2]
        ids = {q[2]["id"] for q in mem}
        left = [q for q in left if q[2]["id"] not in ids]
        out.append(mem)
    return out


def _chunks(members: list, cap: int) -> list[list]:
    """Split an oversized cluster into <= cap pieces, west-to-east so each
    piece stays spatially coherent."""
    if len(members) <= cap:
        return [members]
    ordered = sorted(members, key=lambda q: (q[0], q[1], q[2]["id"]))
    n = math.ceil(len(ordered) / cap)
    size = math.ceil(len(ordered) / n)
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def _free_pixel(x: int, y: int, used: set, w: int, h: int) -> tuple[int, int]:
    """The nearest in-bounds pixel to (x, y) that no other pin uses."""
    for d in range(0, max(w, h)):
        for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
            c = (x + dx, y + dy)
            if 1 <= c[0] <= w - 2 and 1 <= c[1] <= h - 2 and c not in used:
                return c
    raise RuntimeError("no free pixel on map")


def _panel_slots(n: int, quests: int, dims, pin: int, quest_slot_pos) -> list[tuple[int, int]]:
    """Pixel slots for n unmapped-container pins in the map's bottom panel,
    starting on the row after the quest pins. The quest grid's spacing is
    used while it fits; if it doesn't, the spacing shrinks toward the small
    container pin's width (+2 px) so every pin stays inside the map."""
    if n == 0:
        return []
    w, h = dims
    last_quest_y = quest_slot_pos(quests - 1, dims, pin)[1] if quests else None
    first = quest_slot_pos(0, dims, pin)
    quest_slot = max(12, pin * 2)
    top = (last_quest_y + quest_slot // 2) if last_quest_y is not None else first[1] - quest_slot // 2
    left, right, bottom = first[0] - quest_slot // 2, w - (first[0] - quest_slot // 2), h - 2
    slot = quest_slot
    small = max(6, int(round(pin * PIN_FRAC)))
    while slot > small + 2:
        cols = max(1, (right - left) // slot)
        rows = max(0, (bottom - top) // slot)
        if cols * rows >= n:
            break
        slot -= 1
    cols = max(1, (right - left) // slot)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        out.append((left + c * slot + slot // 2, top + r * slot + slot // 2))
    return out


def build_container_children(
    tab_rows: dict[str, list[dict]],
    projections: dict,
    map_dims_for,
    map_size_for,
    rule_for,
    quest_slot_pos,
    first_free_quest_slot: dict[str, int],
    used_xy: dict[str, set],
    sanitize,
    extra_zones: dict[str, list[dict]] | None = None,
) -> tuple[dict[str, list[dict]], list[tuple[int, str, str, str]], dict]:
    """Return ({tab: [location child, ...]}, [(ap_id, tab, child_name, section_name)], stats).

    `rule_for(logic_slug, own_tab_slug)` gives a pin's access_rule string (or
    None); a row's own tab is its "rule_tab" (it differs from `tab` on an
    Interiors tab). `used_xy[map]` holds pixel coords already taken on each
    map (kills, quests) and is updated in place.

    A zone may carry "_map": it is drawn on another map than the tab's own (a
    tab split over two maps, see generate_pack.MAP_SPLITS). Pins in it go on
    that map; the bottom panel always stays on the tab's own map.
    """
    extra_zones = extra_zones or {}
    children: dict[str, list[dict]] = {}
    id_map: list[tuple[int, str, str, str]] = []
    stats = {"placed": 0, "panel": 0, "pins": 0, "panel_pins": 0, "max": 0}

    for tab, rows in tab_rows.items():
        zones = projections.get("regions", {}).get(tab, {}).get("zones", [])
        zones = zones + extra_zones.get(tab, [])
        pin = map_size_for(tab)
        # Keys carry the container's own tab (rule_tab) so an Interiors pin
        # never mixes containers that get different rules.
        groups: dict[tuple, list] = {}     # (logic, rule_tab, kind, map) -> [(px, py, row)]
        panel: dict[tuple, list] = {}      # (logic, rule_tab, kind, level) -> [row]
        for r in rows:
            rt = r.get("rule_tab", tab)
            zn = zone_for_row(zones, {**r, "rule_tab": rt})
            if zn is None:
                panel.setdefault((r["logic"], rt, r["kind"], r["level"]), []).append(r)
                continue
            px, py = _project(r["x"], r["z"], zn)
            groups.setdefault((r["logic"], rt, r["kind"], zn.get("_map", tab)), []).append((px, py, r))

        pins: list[tuple[tuple, int, int, list[dict], bool]] = []
        for key in sorted(groups):
            for cl in _leader_clusters(groups[key], pin):
                pieces = _chunks(cl, CLUSTER_CAP)
                cx = sum(q[0] for q in cl) / len(cl)
                cy = sum(q[1] for q in cl) / len(cl)
                for i, piece in enumerate(pieces):
                    if len(pieces) == 1:
                        x = sum(q[0] for q in piece) / len(piece)
                        y = sum(q[1] for q in piece) / len(piece)
                    else:
                        # Fan split pieces out around the group's centre so
                        # their pins don't sit on one another.
                        a = 2 * math.pi * i / len(pieces)
                        x = cx + 1.25 * pin * math.cos(a)
                        y = cy + 1.25 * pin * math.sin(a)
                    pins.append((key, int(round(x)), int(round(y)), [q[2] for q in piece], False))

        dims = map_dims_for(tab)
        pieces = [((key[0], key[1], key[2], tab), [q[2] for q in piece])
                  for key in sorted(panel)
                  for piece in _chunks([(0, 0, r) for r in sorted(panel[key], key=lambda r: r["id"])],
                                       CLUSTER_CAP)]
        for (key, members), (x, y) in zip(pieces, _panel_slots(
                len(pieces), first_free_quest_slot.get(tab, 0), dims, pin, quest_slot_pos)):
            pins.append((key, x, y, members, True))

        out = []
        name_count: dict[str, int] = {}
        for (logic, rule_tab, kind, mp), x, y, members, in_panel in pins:
            w, h = map_dims_for(mp)
            used = used_xy.setdefault(mp, set())
            x = max(1, min(w - 2, x))
            y = max(1, min(h - 2, y))
            x, y = _free_pixel(x, y, used, w, h)   # UT keys pins by exact pixel
            used.add((x, y))
            prefixes = [m["prefix"] for m in members]
            prefix = max(sorted(set(prefixes)), key=prefixes.count)
            base = f"{prefix} — {KIND_LABEL[kind]}"
            if in_panel:
                base += f" (unmapped: {members[0]['level']})"
            name_count[base] = name_count.get(base, 0) + 1
            name = f"{base} #{name_count[base]}"
            sections = []
            for m in sorted(members, key=lambda m: m["name"]):
                sname = sanitize(m["name"])
                sections.append({
                    "name": sname,
                    "chest_unopened_img": f"images/items/{ICON}.png",
                    "chest_opened_img": f"images/items/{ICON}_opened.png",
                })
                id_map.append((m["id"], tab, name, sname))
            size = max(6, int(round(map_size_for(mp) * PIN_FRAC)))
            child = {
                "name": name,
                "sections": sections,
                # Visible in the Map View's "All" or "Containers" view.
                "visibility_rules": [f"{base},{v}" for v in ("view_all", "view_containers")
                                     for base in (["containersanity"] if kind == "loot"
                                                  else ["containersanity,show_clutter"])],
                "map_locations": [{"map": mp, "x": x, "y": y, "size": size, "shape": SHAPE}],
            }
            rule = rule_for(logic, rule_tab)
            if rule:
                child["access_rules"] = [rule]
            out.append(child)
            stats["pins"] += 1
            stats["max"] = max(stats["max"], len(members))
            if in_panel:
                stats["panel"] += len(members)
                stats["panel_pins"] += 1
            else:
                stats["placed"] += len(members)
        children[tab] = out
    return children, id_map, stats
