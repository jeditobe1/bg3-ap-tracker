"""Build the UUID -> texture-hash index used by the region renderers.

Walks the converted minimap merged bank LSX, cross-references the texture
UUIDs that appear in ``bg3_npc_data.json``'s patches, and writes
``tools/vt_index.json`` -- a ``{uuid: {gtex, name}}`` lookup table. The
region renderers (``render_region_map.py``, ``render_multi_zone_map.py``,
``render_overworld.py``) load this index to resolve each WorldMap patch's
``texture_uuid`` to a Granite-VT-extracted ``<hash>_0.dds`` file on disk.

Prerequisites:
- ``Divine.exe`` has converted ``[PAK]_Minimaps/_merged.lsf`` (from
  ``Gustav.pak``) to an LSX. Point ``--merged-lsx`` at the result.
- ``tools/bg3_npc_data.json`` exists (run ``build_npc_data.py`` first).

See ``MAINTAINER_GUIDE.md`` for the full pipeline.

Output:
- ``tools/vt_index.json`` (committed; ~65 KB).
- ``<work-dir>/needed_gtps.txt`` -- newline-separated GTexFileNames; a
  debugging aid for confirming which .gtp tile files need extracting from
  ``VirtualTextures.pak``.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
NPC_DATA = TOOLS / "bg3_npc_data.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--merged-lsx", type=Path, required=True,
                   help="Path to the LSX produced by `Divine.exe -g bg3 -a "
                        "convert-resource -s [PAK]_Minimaps/_merged.lsf -d ...`.")
    p.add_argument("--work-dir", type=Path, default=TOOLS / "_texture_work",
                   help="Working directory for debug outputs "
                        "(default: tools/_texture_work, gitignored).")
    p.add_argument("--out", type=Path, default=TOOLS / "vt_index.json",
                   help="Output JSON path (default: tools/vt_index.json).")
    args = p.parse_args(argv)

    merged_lsx: Path = args.merged_lsx
    work_dir: Path = args.work_dir
    out_path: Path = args.out

    if not merged_lsx.is_file():
        print(f"[err] missing {merged_lsx}")
        return 1
    if not NPC_DATA.is_file():
        print(f"[err] missing {NPC_DATA}")
        return 1

    cache = json.loads(NPC_DATA.read_text(encoding="utf-8"))
    needed: set[str] = set()
    for level, patches in cache["patches"].items():
        for p in patches:
            needed.add(p["texture_uuid"])
    print(f"[info] need {len(needed)} unique texture UUIDs from {len(cache['patches'])} levels")

    # Walk the merged .lsx. Each Resource node has ID + GTexFileName + Name.
    tree = ET.parse(merged_lsx)
    root = tree.getroot()
    out: dict[str, dict] = {}
    for res in root.iter("node"):
        if res.get("id") != "Resource":
            continue
        attrs = {a.get("id"): a.get("value") for a in res.findall("attribute")}
        uid = attrs.get("ID")
        if not uid or uid not in needed:
            continue
        out[uid] = {
            "gtex": attrs.get("GTexFileName"),
            "name": attrs.get("Name"),
        }

    missing = needed - set(out)
    print(f"[ok]  matched {len(out)} / {len(needed)} UUIDs")
    if missing:
        print(f"[warn] {len(missing)} UUIDs not in minimap bank (first 5: {sorted(missing)[:5]})")

    # The .gtp filename pattern is Albedo_Normal_Physical_<hex>_<gtex>.gtp .
    # We don't know which bank (<hex>) each hash lives in without scanning
    # the pak, but Divine -x supports glob; we'll glob on the hash and let
    # extract-package fill in the bank index.
    gtps = [info["gtex"] for info in out.values() if info["gtex"]]
    print(f"[ok]  {len(gtps)} unique .gtp tiles to extract")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, indent=2, sort_keys=True), encoding="utf-8"
    )

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "needed_gtps.txt").write_text(
        "\n".join(sorted(set(gtps))) + "\n", encoding="utf-8"
    )
    print(f"[done] wrote {out_path}")
    print(f"[done] wrote {work_dir / 'needed_gtps.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
