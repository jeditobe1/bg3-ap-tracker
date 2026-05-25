"""Projection helper for the BG3 PopTracker pack.

`tools/projections.json` is the source of truth for each map's world->canvas
transform: per-region (multi-zone supported) and per-overworld. It is
emitted by external (maintainer-side) rendering tooling and read at
pack-build time. Pack-time pin computation in tools/generate_pack.py
consumes it, together with tools/pin_overrides.json +
tools/bg3_npc_data.json, to assign canvas coordinates to every AP
location's pin.

The two committed JSON artifacts make pin addition cheap for future
contributors: they don't need the DDS cache to add a pin for a new AP
location, only the npc_data + projections + overrides.

JSON-key conventions for both projections.json and pin_overrides.json:
- Keys starting with "_" (e.g. "_doc", "_why") are metadata for human
  readers and are stripped from anything the projection code consumes.
- Values matching None/"" (empty building tag) follow the patch-tag
  convention: `null` in JSON is the canonical exterior marker, but
  empty-string `""` is also accepted on input for robustness.

Schemas live in the two JSON files' "_doc" entries.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Iterable

try:
    from declutter import declutter_pins  # when imported by code in tools/
except ImportError:  # when imported by scratch scripts via sys.path
    from .declutter import declutter_pins  # type: ignore


# ---------- Loaders ----------

def load_overrides(pack_root: Path) -> dict:
    """Load tools/pin_overrides.json. Strips `_doc`/`_why` keys; canonicalizes
    empty-string buildings to None."""
    p = pack_root / "tools" / "pin_overrides.json"
    if not p.exists():
        return _empty_overrides()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return _normalize_overrides(raw)


def load_projections(pack_root: Path) -> dict:
    """Load tools/projections.json. Returns `{"regions": {...}, "overworlds": {...}}`.
    Strips metadata keys."""
    p = pack_root / "tools" / "projections.json"
    if not p.exists():
        return {"regions": {}, "overworlds": {}}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return _normalize_projections(raw)


def save_projections(pack_root: Path, projections: dict) -> Path:
    """Write tools/projections.json. Atomic-ish: write to .tmp then rename."""
    p = pack_root / "tools" / "projections.json"
    tmp = p.with_suffix(".json.tmp")
    payload = _strip_internal(projections)
    payload.setdefault("regions", {})
    payload.setdefault("overworlds", {})
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def upsert_region_projection(pack_root: Path, slug: str, region_proj: dict) -> Path:
    """Read projections.json, replace the entry under regions[slug], write back."""
    proj = load_projections(pack_root)
    proj.setdefault("regions", {})[slug] = region_proj
    return save_projections(pack_root, proj)


def upsert_overworld_projection(pack_root: Path, slug: str,
                                overworld_proj: dict) -> Path:
    proj = load_projections(pack_root)
    proj.setdefault("overworlds", {})[slug] = overworld_proj
    return save_projections(pack_root, proj)


# ---------- NPC cache transforms ----------

def apply_overrides_to_cache(npc_cache: dict, overrides: dict,
                             verbose: bool = False) -> dict:
    """In-place: inject synthetic chars, apply char position + building
    overrides. Returns the same dict for chaining. `verbose` prints per-op
    summaries (matching the legacy renderer output)."""
    chars = npc_cache.setdefault("characters", {})

    for uuid, info in (overrides.get("synthetic_chars") or {}).items():
        if uuid in chars:
            if verbose:
                print(f"[warn] synthetic uuid {uuid[:8]} now in cache; remove the override")
            continue
        chars[uuid] = dict(info)
        if verbose:
            pos = info.get("position", [0, 0, 0])
            print(f"[ok] synthetic {uuid[:8]} ({info.get('name','?')}) "
                  f"@ {pos[0]},{pos[2]} on {info.get('level','?')}")

    for uuid, entry in (overrides.get("char_position_overrides") or {}).items():
        c = chars.get(uuid)
        if c is None:
            if verbose:
                print(f"[warn] position override uuid {uuid[:8]} not in cache")
            continue
        ox, oz = entry["position"]
        existing = c.get("position") or [0.0, 0.0, 0.0]
        c["position"] = [ox, existing[1] if len(existing) > 1 else 0.0, oz]
        if verbose:
            print(f"[ok] position override {uuid[:8]} -> ({ox}, {oz})")

    for uuid, entry in (overrides.get("char_building_overrides") or {}).items():
        c = chars.get(uuid)
        if c is None:
            if verbose:
                print(f"[warn] building override uuid {uuid[:8]} not in cache")
            continue
        old = c.get("building")
        c["building"] = entry["building"]
        if verbose:
            print(f"[ok] building override {uuid[:8]} {old!r} -> {entry['building']!r}")

    return npc_cache


def apply_region_offset(slug: str, x: float, z: float,
                        overrides: dict) -> tuple[float, float]:
    """Apply per-region world-coord offset (if any) to a char position.
    Returns the offset (x, z). Caller passes the result into projection."""
    entry = (overrides.get("region_world_offsets") or {}).get(slug)
    if entry is None:
        return x, z
    dx, dz = entry["offset"]
    return x + dx, z + dz


# ---------- Projection math ----------

def matches_building(value, want: list) -> bool:
    """Building-tag matcher. `want` is a list where:
      - None or empty-string matches a char/patch with empty/missing building
      - the wildcard "*" matches anything (used by single-zone regions
        whose only filter is the apworld region tag, not the building)
      - other strings match the literal building tag
    """
    norm = value if value else None
    for w in want:
        if w == "*":
            return True
        wnorm = w if w else None
        if wnorm is None and norm is None:
            return True
        if isinstance(wnorm, str) and norm == wnorm:
            return True
    return False


def project_world_xz_in_zone(wx: float, wz: float, zone: dict) -> tuple[int, int]:
    """Project a world (x, z) into the canvas using a single zone's transform.
    Assumes wx, wz fall within the zone's world_bbox; clamps if outside."""
    wx0, wz0, wx1, wz1 = zone["world_bbox"]
    cx0, cy0, cw, ch = zone["canvas_rect"]
    world_w = max(1e-6, wx1 - wx0)
    world_h = max(1e-6, wz1 - wz0)
    # Uniform fit-with-letterboxing within canvas_rect, matching render_zone.
    scale = min(cw / world_w, ch / world_h)
    used_w = world_w * scale
    used_h = world_h * scale
    off_x = (cw - used_w) / 2
    off_y = (ch - used_h) / 2
    # Y flip: +Z is north, +y is screen-down.
    local_x = off_x + (wx - wx0) * scale
    local_y = off_y + used_h - (wz - wz0) * scale
    px = max(cx0, min(cx0 + cw, int(round(cx0 + local_x))))
    py = max(cy0, min(cy0 + ch, int(round(cy0 + local_y))))
    return px, py


def find_zone(region_proj: dict, level: str, building,
              x: float | None = None, z: float | None = None) -> dict | None:
    """First zone in the region that matches the char. Match criteria:
      - level matches zone.level
      - building matches zone.char_building (per matches_building)
      - if (x, z) are supplied AND the zone carries world_bbox_filter,
        the position must fall inside it

    Returns None if no zone matches. Zone order in `region_proj['zones']`
    determines precedence -- callers should arrange tighter filters first.
    """
    norm_b = building if building else None
    for zn in region_proj.get("zones", []):
        if zn.get("level") != level:
            continue
        if not matches_building(norm_b, zn.get("char_building", [])):
            continue
        bbox_filter = zn.get("world_bbox_filter")
        if bbox_filter is not None and x is not None and z is not None:
            fx0, fz0, fx1, fz1 = bbox_filter
            if not (fx0 <= x <= fx1 and fz0 <= z <= fz1):
                continue
        return zn
    return None


def project_char_to_canvas(slug: str, region_proj: dict, char: dict,
                           overrides: dict | None = None
                           ) -> tuple[int, int, str] | None:
    """Project a char in the given region. Returns (px, py, level) or None.
    Applies region_world_offsets[slug] before projection. The char dict
    must already have any per-uuid overrides applied (use apply_overrides_to_cache
    first)."""
    if not char.get("position"):
        return None
    level = char.get("level")
    if not level:
        return None
    wx = char["position"][0]
    wz = char["position"][2] if len(char["position"]) >= 3 else char["position"][1]
    if overrides is not None:
        wx, wz = apply_region_offset(slug, wx, wz, overrides)
    zone = find_zone(region_proj, level, char.get("building"), wx, wz)
    if zone is None:
        return None
    px, py = project_world_xz_in_zone(wx, wz, zone)
    return px, py, level


def project_overworld_world(overworld_proj: dict,
                            wx: float, wz: float) -> tuple[int, int, bool]:
    """Project a world (x, z) onto an overworld map. Returns (px, py, offscreen).
    Clamps off-canvas to the nearest edge (with a small inset) so off-bbox
    pins still appear as edge callouts."""
    cw, ch = overworld_proj["canvas"]
    wx0, wz0, wx1, wz1 = overworld_proj["world_bbox"]
    scale = cw / max(1e-6, wx1 - wx0)
    px = (wx - wx0) * scale
    py = ch - (wz - wz0) * scale  # +Z north => top
    offscreen = (px < 0 or px > cw or py < 0 or py > ch)
    inset = 8
    px = max(inset, min(cw - inset, int(round(px))))
    py = max(inset, min(ch - inset, int(round(py))))
    return px, py, offscreen


# ---------- High-level pin computation ----------

def compute_region_pins(slug: str, region_proj: dict,
                        chars_by_ap_name: dict[str, dict],
                        overrides: dict | None = None,
                        declutter_min_dist: int | None = None,
                        ) -> dict[str, dict]:
    """Compute canvas coords for every AP location in a region.

    `chars_by_ap_name` maps AP location name to a cached char dict
    (must have already gone through apply_overrides_to_cache for
    per-uuid overrides). `declutter_min_dist` enables per-zone declutter
    in screen-space when set. Returns
    `{ap_name: {"x": px, "y": py, "level": <engine level>}}`.
    """
    by_zone: dict[int, dict[str, dict]] = {}
    zone_bounds: dict[int, tuple[int, int, int, int]] = {}
    out: dict[str, dict] = {}

    for ap_name, char in chars_by_ap_name.items():
        if not char.get("position") or not char.get("level"):
            continue
        wx = char["position"][0]
        wz = char["position"][2] if len(char["position"]) >= 3 else char["position"][1]
        if overrides is not None:
            wx, wz = apply_region_offset(slug, wx, wz, overrides)
        zone = find_zone(region_proj, char["level"], char.get("building"), wx, wz)
        if zone is None:
            continue
        px, py = project_world_xz_in_zone(wx, wz, zone)
        zidx = id(zone)
        cx0, cy0, cw, ch = zone["canvas_rect"]
        zone_bounds[zidx] = (cx0, cy0, cx0 + cw, cy0 + ch)
        by_zone.setdefault(zidx, {})[ap_name] = {
            "x": px, "y": py, "_level": char["level"],
        }

    for zidx, pins in by_zone.items():
        if declutter_min_dist:
            cx0, cy0, cx1, cy1 = zone_bounds[zidx]
            bounds = {n: zone_bounds[zidx] for n in pins}
            pins = declutter_pins(pins, min_dist=declutter_min_dist,
                                  max_iters=200, bounds=bounds)
        for ap_name, coords in pins.items():
            out[ap_name] = {
                "level": coords.get("_level"),
                "x": coords["x"],
                "y": coords["y"],
            }
    return out


# ---------- Internal helpers ----------

def _strip_internal(obj):
    """Drop dict keys starting with '_' recursively, preserving lists/scalars."""
    if isinstance(obj, dict):
        return {k: _strip_internal(v) for k, v in obj.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(obj, list):
        return [_strip_internal(v) for v in obj]
    return obj


def _normalize_overrides(raw: dict) -> dict:
    """Strip _doc/_why; flatten the {uuid: {position, _why}} shape into
    {uuid: position} where the downstream code expects direct values."""
    stripped = _strip_internal(raw)
    return {
        "synthetic_chars": stripped.get("synthetic_chars") or {},
        "char_position_overrides": stripped.get("char_position_overrides") or {},
        "char_building_overrides": stripped.get("char_building_overrides") or {},
        "region_world_offsets": stripped.get("region_world_offsets") or {},
        "overworld_centroid_filters": stripped.get("overworld_centroid_filters") or {},
        "overworld_pin_overrides": stripped.get("overworld_pin_overrides") or {},
    }


def _normalize_projections(raw: dict) -> dict:
    stripped = _strip_internal(raw)
    stripped.setdefault("regions", {})
    stripped.setdefault("overworlds", {})
    return stripped


def _empty_overrides() -> dict:
    return {
        "synthetic_chars": {},
        "char_position_overrides": {},
        "char_building_overrides": {},
        "region_world_offsets": {},
        "overworld_centroid_filters": {},
        "overworld_pin_overrides": {},
    }
