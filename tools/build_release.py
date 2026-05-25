"""Build a release zip of the BG3 PopTracker pack.

Output: <pack-root>/build/bg3-poptracker-<package_version>.zip

The zip excludes maintainer-only paths that PopTracker doesn't need at
runtime: tools/, build/, .git, __pycache__, *.pyc, and any other
gitignored cruft. The result drops directly into PopTracker's packs/
directory and works without unzipping (PopTracker reads pack zips
natively, as does Universal Tracker).

Run from the pack root:
    python tools/build_release.py
or explicitly:
    python tools/build_release.py --out custom/path.zip
"""

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent.parent

# Directory names anywhere in the path that exclude an entry. Compared
# case-sensitively against each path component.
SKIP_DIR_NAMES = {
    "tools",          # maintainer scripts + NPC cache + projection JSONs
    "build",          # release output, don't recursively include
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".vscode",
    ".idea",
    ".claude",        # local agent state; gitignored and not pack content
}

# File patterns to skip (suffix or exact name).
SKIP_SUFFIXES = {".pyc", ".swp"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", ".gitignore"}


def should_include(path: Path) -> bool:
    """Decide whether `path` (relative to PACK_ROOT) should be in the zip."""
    parts = path.parts
    if any(part in SKIP_DIR_NAMES for part in parts):
        return False
    if path.suffix in SKIP_SUFFIXES:
        return False
    if path.name in SKIP_NAMES:
        return False
    return True


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the build script can pick up local
    maintainer config (chiefly BG3_PRIVACY_EXTRA -- the extra regex
    tokens used by the privacy sanity check) without anyone having to
    set shell env vars by hand each session. Format: one KEY=VALUE per
    line, blank lines and `#` comments ignored, no quoting. Existing
    process env wins over .env so an explicit `KEY=... python ...`
    invocation still overrides.

    The file is gitignored on purpose. Don't move it into a tracked
    path; the whole point is that its contents (which may include
    maintainer-name regex tokens) never enter version control.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def read_manifest() -> dict:
    return json.loads((PACK_ROOT / "manifest.json").read_text(encoding="utf-8"))


def release_version(base_version: str) -> str:
    """Append a semver build-metadata suffix so the released zip is
    distinguishable from a dev junction sharing the same package_version
    on disk. PopTracker treats this as a different version string in
    the pack menu (the suffix shows up next to the name), eliminating
    the "duplicate listing" effect when both are in search paths.

    Idempotent: passing an already-suffixed version returns it unchanged.
    """
    if "+release" in base_version:
        return base_version
    return f"{base_version}+release"


def main() -> int:
    # Auto-load maintainer config from a gitignored .env at the pack
    # root. Most relevant key today is BG3_PRIVACY_EXTRA (see the
    # privacy-check section below). Loader is a no-op if the file
    # doesn't exist, so first-time checkouts still work.
    _load_dotenv(PACK_ROOT / ".env")

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=None,
                   help="Override output zip path. Default: "
                        "build/bg3-poptracker-<version>.zip")
    args = p.parse_args()

    manifest = read_manifest()
    base_version = manifest["package_version"]
    rel_version = release_version(base_version)
    out = args.out or (PACK_ROOT / "build" / f"bg3-poptracker-{rel_version}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Inventory + filter
    files: list[Path] = []
    for p in PACK_ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(PACK_ROOT)
        if not should_include(rel):
            continue
        files.append(rel)
    files.sort()

    # Privacy sanity check on text files. Catches drive-letter paths
    # that may have crept in via copy-paste. The build script intentionally
    # does NOT hardcode any personal-name tokens -- doing so would itself
    # be a leak.
    #
    # The base pattern (drive-letter + /Tools/BG3 path fragments) is the
    # safe baseline that every fresh checkout gets out of the box -- no
    # .env required. It catches the most common leak class (absolute
    # paths from copy-pasted shell output) without anyone having to
    # configure local secrets just to build a release.
    #
    # BG3_PRIVACY_EXTRA is opt-in: maintainers with additional PII
    # concerns (e.g. a real-name token they want to belt-and-suspenders
    # block from ever entering the zip) set it in a gitignored .env at
    # the pack root. The token list lives ONLY in that .env -- it must
    # never be written into a tracked file, including this one. A
    # contributor without a .env still gets the baseline check.
    base_pattern = r"D:\\|C:\\Users|/Tools/BG3"
    extra = os.environ.get("BG3_PRIVACY_EXTRA", "").strip()
    if extra:
        base_pattern = f"{base_pattern}|{extra}"
    pii_re = re.compile(base_pattern, re.IGNORECASE)
    pii_hits: list[str] = []
    text_exts = {".json", ".lua", ".md", ".py", ".yaml", ".yml", ".txt"}
    for rel in files:
        if rel.suffix.lower() not in text_exts:
            continue
        try:
            content = (PACK_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if pii_re.search(content):
            pii_hits.append(str(rel))
    if pii_hits:
        print("[err] privacy check failed -- file(s) contain local paths "
              "or BG3_PRIVACY_EXTRA tokens:")
        for h in pii_hits:
            print(f"  {h}")
        print("[err] refusing to write release zip. Clean these and re-run.")
        return 1

    # Write the zip with content at the ZIP ROOT (no top-level dir).
    # Both PopTracker and Universal Tracker open these zips:
    #   - PopTracker auto-strips a single top-level directory if present
    #     (PopTracker/src/core/pack.cpp:122) -- tolerates either layout.
    #   - Universal Tracker (worlds/tracker/TrackerClient.py
    #     load_json_zip) opens paths literally relative to the zip root
    #     with no directory-prefix handling. So `images/maps/foo.png`
    #     in maps.json must resolve to `images/maps/foo.png` at the
    #     zip root.
    # Flat-root is the compatible layout for both.
    total_bytes = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            src = PACK_ROOT / rel
            arc = rel.as_posix()
            if arc == "manifest.json":
                # Patch the manifest going into the zip so the released
                # version carries the +release semver-build-metadata
                # suffix while the on-disk manifest (used by the dev
                # junction) keeps the base version.
                patched = dict(manifest)
                patched["package_version"] = rel_version
                payload = json.dumps(patched, indent=4) + "\n"
                z.writestr(arc, payload)
                total_bytes += len(payload.encode("utf-8"))
                continue
            z.write(src, arc)
            total_bytes += src.stat().st_size

    out_kb = out.stat().st_size / 1024
    print(f"[done] wrote {out}  ({len(files)} files, "
          f"{total_bytes / 1024:.0f} KB uncompressed, {out_kb:.0f} KB zipped)")
    print(f"[manifest] package_version={rel_version} (on-disk: {base_version})")
    # Top-level summary by section
    from collections import Counter
    by_top = Counter()
    for rel in files:
        top = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        by_top[top] += 1
    print("[contents]")
    for top, n in sorted(by_top.items()):
        print(f"  {top:14s} {n:>4d} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
