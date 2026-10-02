---
issue: 211
status: DRAFT
class: SYSTEMATIC — nothing compares the package version with the release tag it ships as
chokepoint: scripts/version_parity.py (the single owner of version declarations) + the `manifests` CI job
tier: TIER 2
---

# bd#211 — release 1.1.1 and guard the package version against release tags

## §1 Problem

Tag `v1.1.0` → 56fbbfa, where every version declaration says `0.2.0`. A host that pins bd by tag and
requires `[project].version == tag[1:]` (HAL `bd-conformance.py`, `E_BD_ENGINE_VERSION_MISMATCH`)
refuses the tree. `version_parity.py --check` only compares the declarations with each other, so no
gate ever compared the canonical version with a release tag.

## §2 Change

1. **Bump**: `python3 scripts/version_parity.py --write 1.1.1` (all registered declarations, incl. the
   pointer pin). No hand edits.
2. **New mode** `version_parity.py --check-release [--tag TAG]` (in the existing mutually exclusive mode
   group; `--tag` without `--check-release` → argparse error, exit 2). Collects every problem, prints
   one line each, exit 1; else prints `OK: release version <C>` and exits 0. `C` = canonical
   (`_read_canonical`; failure → `<path>: <reason>`, exit 1).
   - **Tag floor**: `git -C <root> tag -l "v*"`. Non-zero exit or git not runnable →
     `git tags: unreadable`. Tags matching `^v\d+\.\d+\.\d+$` are compared as integer triples (never
     lexically); others are ignored. Newest tag `vN` with `N > C` →
     `git tags: newest release tag vN is ahead of C`. Zero matching tags is fine.
   - **`--tag TAG`**: not `^v\d+\.\d+\.\d+$` → `--tag: malformed TAG`; `TAG[1:] != C` →
     `--tag: TAG does not match C`.
   - `--check` and `--write` behaviour is unchanged.
3. **CI** (`.github/workflows/ci.yml`): `on.push.tags: ["v*"]`; `manifests` checkout gets
   `fetch-depth: 0` (tags present); new step runs `--check-release`, adding `--tag <ref_name>` when the
   ref is a `v*` tag; new step runs `pytest tests/test_version_release_guard.py`.
4. **Docs**: CHANGELOG `[Unreleased]` → `### Fixed` bullet (bd#211); the header's "version together as
   `0.x`" prose is corrected (plugin, npm and engine version together; `v1.1.0` carried `0.2.0`
   declarations and is superseded by 1.1.1). CONTRIBUTING "Releasing" gains the `--check-release --tag`
   step before tagging.

## §3 ACs (RED: `tests/test_version_release_guard.py`, subprocess-only via `test_version_parity._run`)

- **AC1 (§1l side effect)** real repo: `--check-release --root REPO_ROOT` exits 0. RED today
  (`v1.1.0` > `0.2.0`). Reddens if the bump is reverted.
- **AC2** tmp repo (git init, all declarations agree at C, no tags) → 0, stdout has `OK: release version C`.
- **AC3** the incident: C=`0.2.0`, tag `v1.1.0` → 1, names `v1.1.0` and `0.2.0`.
- **AC4** integer compare: C=`0.9.0`, tag `v0.10.0` → 1 (a lexical compare passes → reddens).
- **AC5** tags `v0.1.0` and `v0.2.0` with C=`0.2.0` → 0 (equal is fine); non-semver tags `v9.0`,
  `v9.0.0-rc1`, `release-9.0.0` alongside them are ignored → still 0.
- **AC6** `--tag v0.2.0` with C=`0.2.0` → 0; `--tag v0.2.1` → 1 (names both); `--tag 0.2.0` → 1 `malformed`.
- **AC7** root not a git repo → 1, `git tags: unreadable`.
- **AC8** `--tag v1.0.0` without `--check-release` → 2; `--check-release --write 1.0.0` → 2.
- **AC9** ci.yml: `on.push.tags` contains `v*`; `manifests` checkout `fetch-depth: 0`; a `manifests` step
  runs `version_parity.py --check-release` and references the tag ref name; a step runs pytest on this file.
- **AC10** problems are all reported: C=`0.2.0`, tag `v1.1.0`, `--tag v3.0.0` → 1, stdout has both lines.

## §4 Not in scope (§1v)

- A CHANGELOG `[1.1.1]` section: 9 engine tests (`test_bd101`, `test_bd116`, `test_bd117b`, `test_bd119`,
  `test_bd131`, `test_bd141_p5/p6_*`) require their entries under `[Unreleased]`, so cutting a section breaks
  them. Follow-up issue; this PR does not touch those tests.
- PyPI/npm publishing (no publish workflow exists); the tag itself (maintainer).

## §5 Scope

`scripts/version_parity.py`, `tests/test_version_release_guard.py` (new), `.github/workflows/ci.yml`,
the 6 declaration files (script-written), `CHANGELOG.md`, `CONTRIBUTING.md`, this spec.
Siblings: `tests/test_version_parity.py`, `tests/test_version_parity_pin.py`, `tests/test_ci_main_heartbeat.py`.
