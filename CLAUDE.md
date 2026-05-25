# Repo conventions for Claude agents

Auto-loaded into every Claude session opened against this repo (including any `git worktree`). The rules below are hard project policy.

## Maintainer PII tokens

The maintainer's real first and last name MUST NEVER appear as literal strings in any tracked file in this repository — including comments, code identifiers, regex patterns, fixtures, configuration, commit messages, issue bodies, release notes, or this file.

- The only allowed location for the tokens on disk is the gitignored `.env` at the pack root, read by `tools/build_release.py` via `BG3_PRIVACY_EXTRA`.
- Before any commit, grep staged content for the tokens (their values are in `.env`).
- A regex pattern that contains the literal token is itself a leak, regardless of framing.

If a token is discovered in tracked content, stop and surface to the user. Do not extend or relocate any existing regex.

## Author identity in git

Every commit's author and committer must be the GitHub noreply identity already configured in `git config user.email`. Never override with `-c user.email=` or any personal email. `Co-Authored-By:` lines for Claude use `noreply@anthropic.com`.

## Worktrees

`git worktree` checkouts inherit this CLAUDE.md and are bound by the same rules. A worktree branch that has the PII tokens in any reachable commit must be rebased onto clean history before merging back. The gitignored `.env` does not propagate to a new worktree — copy it in.

## Build and generator entry points

- `tools/build_release.py` is the only release-zip entry point. It applies the inclusion/exclusion list and the privacy check. Don't bypass it.
- `tools/generate_pack.py` owns `locations/locations.json`, `maps/maps.json`, `layouts/regions.json`, `scripts/autotracking_generated.lua`, and generated `images/maps/<slug>.png`. Hand-edits between regenerations get clobbered. Use `--check` to dry-run.
- Manifest version (`manifest.json` → `package_version`) is hand-edited on release. The build script appends a `+release` SemVer build-metadata suffix to the manifest that ships inside the zip.

## External tooling out of scope

Maintainer-side tooling that lives outside this repo is out-of-scope for tracked content. Do not reference external script names, directory names, or absolute filesystem paths in any tracked file.

## When in doubt — or when a violation is discovered — surface to the user

Two things go straight to the user, never handled silently:

1. **Questions / ambiguity**: any question about scope, intent, or interpretation of these rules — or any ambiguity in a privacy or identity decision. Do not guess.
2. **Violations**: any actual violation of these rules discovered anywhere — in the working tree, in a commit on any branch (including worktree branches), in a tag, in a release artifact, in an issue or PR body, in agent memory, or in a CLAUDE.md. Surface immediately. Do not attempt to self-remediate (no force-pushes, no history rewrites, no regex edits) without explicit user approval.
