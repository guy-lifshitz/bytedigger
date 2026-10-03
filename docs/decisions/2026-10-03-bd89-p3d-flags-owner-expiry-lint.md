# bd#89 P3d: flags carry `owner` + `expires`, dead flag deleted, deterministic CI expiry lint

**Status: FROZEN r1** · **Tier:** 3 (engine data `.py` + new CI script, Option D) · **Class:** PROCESS (row 7 of bd#89; binding coordinator answer, issue #89 comment "Coordinator answer on the P3 questions", item 2) ·
**Base:** `origin/main` `1e391ed`. **Template:** `2026-10-02-bd89-p3b1b-ii-aggregation-helper.md`.
**Chokepoint:** `FLAGS` in `engine_py/bytedigger_engine/flags_catalog.py`. It is the single source of every `HAL_*` token; the new lint reads only that dict.
**Side of the seam (decision 2026-07-26 §7.1):** engine data + root `scripts/` + CI + docs. No orchestrator-flow md, no gate script, no TS. No `workflows/phase_6_*` / `phase_7_*` files (other slices).

## §0 Scope size

- 1 data file edited: `flags_catalog.py` (1 entry deleted, `owner`/`expires` added to the rollout entries).
- 1 new script: `scripts/flag_expiry_lint.py` (stdlib only).
- 1 CI edit: a step in the `manifests` job of `.github/workflows/ci.yml` (it runs root `tests/`; the `pytest` job runs `engine_py/tests` with `working-directory: engine_py`, so a root-level script and test belong to `manifests`).
- 1 doc section (`CONTRIBUTING.md`, "Flag lifecycle") and CHANGELOG. `docs/configuration.md` is NOT touched (owned by p3b2).
- 1 RED file: `tests/test_bd89_p3d_flag_expiry_lint.py` (root `tests/`, plus one CI step line running it).
- No behaviour change in any engine path. Zero edits under `workflows/`.

## §1 Problem (measured on `1e391ed`)

- `FLAGS` has 110 entries. Entries carry only `kind`, `default`, `module`, `description`. No `owner`, no `expires`.
- 19 entries carry a `flip-by:` / `retire-by:` date in free description text. 17 of the `flip-by` dates are already past (2026-07-24 .. 2026-08-16). Nothing checks them: `ci.yml`, `ci-heartbeat.yml`, `clean-room.yml` have no expiry step. The pattern is a kill-switch nobody flips.
- `HAL_ORPHAN_CALLSITE_ENFORCE` (`flags_catalog.py:231`) has no reader anywhere in the repo (`grep -rn ORPHAN_CALLSITE_ENFORCE` hits only the catalog entry). Its module path `SYSTEM/cli/build/orphan-callsite-lint.py` does not exist here. It is a dead flag. Its sibling `HAL_ORPHAN_CALLSITE_GATE` has the same missing module and is NOT in scope (not named by the coordinator; follow-up).
- `conformance/bd_l2.py` `ENFORCEMENT` (`:76-95`) lists `HAL_RED_COLLECT_PROBE_ENFORCE`, `HAL_STUB_PASSABILITY_GATE`, `HAL_BASELINE_DELTA_ENFORCE`. The deleted flag is not listed there. Nothing to change in `bd_l2.py`.
- Root `tests/` runs in the `manifests` job (per-file steps); `engine_py/tests` runs in the `pytest` job.

## §2 Design

Binding decision (coordinator): (a) delete the dead flag; (b) keep every other expired default-0 `*_ENFORCE` flag and its enforce path with ZERO behaviour change (flipping ON is wave 2, needs data); (c) add `owner` + `expires` to rollout entries, new `expires` = **2026-11-02** (30 days after 2026-10-03) for those whose date is past; (d) deterministic CI lint; (e) `HAL_SIBLING_AUDIT_GATE` stays a default-1 kill-switch but carries `owner` + `expires`.

- **op1, delete.** Remove the `HAL_ORPHAN_CALLSITE_ENFORCE` entry from `FLAGS`. No other file mentions it.
- **op2, data.** Add `"owner": "guy-lifshitz"` and `"expires": "<YYYY-MM-DD>"` to every entry in the **rollout set** (defined in op3). No repo CODEOWNERS exists, and `guy-lifshitz` is the repo owner handle. `expires`: `2026-11-02` for each entry whose description date (`flip-by:`/`retire-by:`) is before 2026-11-02, or that has no date (`HAL_SIBLING_AUDIT_GATE`); keep the stated date when it is later (`HAL_AC_DSL_GATE_ENFORCE`, `HAL_SPEC_DEFECT_REROUTE`: `2027-01-15`). Descriptions and `default` values are unchanged; no `default` is flipped.
- **op3, the lint.** New `scripts/flag_expiry_lint.py` with named pure helpers (§1aa):
  - `load_flags(path) -> dict` loads `flags_catalog.py` by file path via `importlib.util` (no package import, no env read) and returns `FLAGS`.
  - `is_rollout(name, entry) -> bool`: true when `entry` is a dict, its `kind` is `flag` or `gate`, and (`name` ends with `_ENFORCE`, or its `description` contains `flip-by:` or `retire-by:`, or it has an `owner` or `expires` key). `HAL_SIBLING_AUDIT_GATE` is in the set by an explicit constant `_EXTRA_ROLLOUT = {"HAL_SIBLING_AUDIT_GATE"}`.
  - `check_entry(name, entry, today) -> list[str]` returns violation strings, each starting with the flag name. Violations: entry not a dict; `owner` missing, not a str, or blank; `expires` missing, not a str, not a valid `YYYY-MM-DD` date (`datetime.date.fromisoformat` after a strict 10-char regex); `expires < today`.
  - `lint(flags, today) -> list[str]`: for each rollout entry, `check_entry`; a non-dict entry whose name ends with `_ENFORCE` is reported (degrade, never raise). Sorted output.
  - `main(argv) -> int`: `--catalog PATH` (default: `engine_py/bytedigger_engine/flags_catalog.py` relative to the script), `--today YYYY-MM-DD` (default `date.today()`; a malformed value exits 2). Prints one line per violation to stderr, `OK: N rollout flags` to stdout, exit 0 clean, 1 on violations, 2 on usage or an unreadable/unloadable catalog (a message, no traceback).
- **op4, CI.** In the `manifests` job add a step `flag expiry lint (bd#89 P3d)` running `python3 scripts/flag_expiry_lint.py`, and a step `python -m pytest tests/test_bd89_p3d_flag_expiry_lint.py -q`.
- **op5, docs.** `CONTRIBUTING.md`: short "Flag lifecycle" section: what the rollout set is, the required fields, how to renew (move `expires` out, with a reason in the PR) and that a default-0 `*_ENFORCE` flag needs `owner` + `expires` or CI fails. CHANGELOG `[Unreleased]`: a Removed bullet (dead flag), an Added bullet (lint + fields).

## §3 Acceptance criteria. RED file: `tests/test_bd89_p3d_flag_expiry_lint.py`

Real files on `tmp_path` for synthetic catalogs; real repo catalog for the live ACs. No mocks (§1l). The script is imported by path (`importlib`), and the CLI ACs run it with `subprocess` and `sys.executable`.

- **AC1** `HAL_ORPHAN_CALLSITE_ENFORCE` is not in the live `FLAGS`, and the token appears in no tracked text file under `engine_py/` or `scripts/` outside CHANGELOG and `docs/decisions/`.
- **AC2** `HAL_ORPHAN_CALLSITE_GATE`, `HAL_SIBLING_AUDIT_GATE` and every other remaining `*_ENFORCE` name present on `1e391ed` is still in `FLAGS` with its `default` unchanged. The test pins a literal `{name: default}` table of the 18 remaining default-bearing rollout entries (zero behaviour change).
- **AC3** Every rollout entry of the live catalog (by `is_rollout`) has a non-blank `owner` and an ISO `expires`, and no `expires` is before 2026-11-02 minus 30 days, i.e. none is before 2026-10-03.
- **AC4** `HAL_SIBLING_AUDIT_GATE` has `default == "1"`, `kind == "gate"`, `owner` and `expires`; `HAL_AC_DSL_GATE_ENFORCE` and `HAL_SPEC_DEFECT_REROUTE` have `expires == "2027-01-15"`; each previously expired entry has `expires == "2026-11-02"`.
- **AC5** CLI on the live catalog with `--today 2026-10-03` exits 0 and prints `OK:`.
- **AC6** CLI with `--today 2026-11-03` exits 1 and names at least the `HAL_SIBLING_AUDIT_GATE` entry on stderr (expiry works; boundary: `--today 2026-11-02` exits 0, `expires == today` is not expired).
- **AC7** Synthetic catalog: a rollout entry missing `owner` is reported by name; missing `expires` reported by name; blank owner reported; both exit 1.
- **AC8** Synthetic catalog: a NEW `HAL_FOO_ENFORCE` (kind `flag`, default `"0"`) without owner/expires fails by name; the same entry with valid owner and future expires passes (the "new default-0 enforce flag" rule).
- **AC9** Degrade: entries that are `None`, a list, a str, a dict with `expires: 5` (int), `expires: "2026-13-45"`, `expires: "soon"`, and `owner: ["x"]` each produce a violation line starting with the flag name, with exit 1, and never raise or traceback (run via subprocess; stderr has no `Traceback`).
- **AC10** A non-rollout entry (kind `path`, no flip-by text, no `_ENFORCE`) without owner/expires is NOT reported (no false positives). A kind `flag` entry whose description contains `flip-by:` is in the rollout set even without the `_ENFORCE` suffix.
- **AC11** Unreadable catalog path and a catalog with a syntax error both exit 2 with a one-line message and no `Traceback`. `--today garbage` exits 2.
- **AC12** `.github/workflows/ci.yml`: the `manifests` job contains a step running `scripts/flag_expiry_lint.py` and a step running `tests/test_bd89_p3d_flag_expiry_lint.py` (the job that runs root `tests/`, not the `pytest` job).
- **AC13** (GUARD) Output of `--today 2026-10-03` on the live catalog is stable: two runs yield identical stdout. `compileall` of the script passes. `cyrillic-prose-lint.py` exits 0.
- **AC14** (GUARD) `CONTRIBUTING.md` contains the section heading `Flag lifecycle`, and CHANGELOG `[Unreleased]` mentions `flag_expiry_lint` and `HAL_ORPHAN_CALLSITE_ENFORCE`.

### §1w op <-> AC map

op1 -> AC1, AC2 · op2 -> AC2, AC3, AC4 · op3 -> AC5-AC11, AC13 · op4 -> AC12 · op5 -> AC14.

### §3 expected-red summary

Before GREEN: AC1, AC3-AC12, AC14 are red (script absent, fields absent, flag present, CI step absent). AC2 and AC13's compile/cyrillic part are green or error only on the missing script; the RED author makes AC2 a pure catalog read, so it is green. A GUARD that is red before GREEN for a reason other than the missing script is a RED bug.

## §4 Out of scope (§1v)

- Flipping any `*_ENFORCE` default or removing any enforce path (wave 2, needs data).
- `HAL_ORPHAN_CALLSITE_GATE` (dead module path too; follow-up for the PR body).
- `bd_l2.py` `ENFORCEMENT` (flag not listed; unchanged).
- `docs/configuration.md`, `phase_6_*`, `phase_7_*`, md flow, gate scripts, TS.
- Auto-renewal or a date-bump tool.

## §5 Scope list (§1a sibling-test audit)

The RED author greps `engine_py/tests` and `tests` for: `HAL_ORPHAN_CALLSITE_ENFORCE`, `flags_catalog` + key-set or length pins of catalog entries, and any test asserting the exact `FLAGS` count (110). Measured on HEAD: zero hits for the deleted token outside the catalog; no entry-shape pins found. Rules:
1. A test that pins `len(FLAGS)` or the key set of an entry: update to 109 / allow the optional keys `owner`, `expires`.
2. Anything mentioning the deleted flag: retarget to a live flag.
3. Baseline: record the pass count of `tests/` and of the flags-catalog consumers (`engine_py/tests/test_gh446*`, `test_bd59_enforcement_map.py`, `test_GH681_cite_prelint_enforce.py`, `test_spec_lint_batch_wiring_GH559.py`, `test_phase_45_ac_dsl_wiring_GH517A2.py`, `test_gh1740_corpus_scope_derived.py`) before and after.

## §6 Verify scope (§1r)

`python3 -m pytest tests/test_bd89_p3d_flag_expiry_lint.py -q` · the §5.3 engine files from `engine_py/` · `python3 scripts/flag_expiry_lint.py --today 2026-10-03` · `python3 -m compileall -q scripts engine_py/bytedigger_engine/flags_catalog.py` · `python3 cyrillic-prose-lint.py` · `python3 -m bytedigger_engine.error_codes --check` (unchanged).

## §7 Errata r2 (gate r1 REJECTED on F1; fixes below, re-gate as r2)

The RED file runs on every CI push (op4), so it must not pin migration-time snapshots that break on a documented lifecycle action (renewal, new flag).

- **F1a.** AC2 pins the per-name `default` table only. No "stray `*_ENFORCE`" check: a new flag must not break the test.
- **F1b.** AC3 asserts `ROLLOUT_NAMES <= rollout` (superset), plus owner / ISO `expires` / floor checks for every rollout entry. The floor is 2026-10-03.
- **F1c.** AC6 runs on a synthetic catalog with one rollout entry `expires: "2026-11-02"`: `--today 2026-11-02` exits 0, `--today 2026-11-03` exits 1 with a line starting with the entry name. The live catalog is checked only date-independently: `--today 2999-01-01` exits 1.
- **F1d (Option 1).** AC4 relaxes exact dates to `expires >= "2026-11-02"` for the renewed set and `>= "2027-01-15"` for `HAL_AC_DSL_GATE_ENFORCE` / `HAL_SPEC_DEFECT_REROUTE`. The orchestrator checks the exact values by diff at GREEN.
- **F2.** AC2 pins 19 names: the 18 rollout entries plus `HAL_ORPHAN_CALLSITE_GATE`. AC3 floor is 2026-10-03.
- **F3.** AC11 adds: a catalog without `FLAGS`, one with `FLAGS = []`, one that raises at import. Each exits 2 with one line and no `Traceback`. (`FLAGS = []` is not a dict: exit 2.)
- **F4.** op3 `lint()` reports any non-dict entry whose name is in `_EXTRA_ROLLOUT` or ends with `_ENFORCE`. AC9's synthetic catalog adds `HAL_SIBLING_AUDIT_GATE: None`.
- **A1.** GREEN verify: `git diff` of `flags_catalog.py` shows exactly one removed entry and, on the rollout entries, only added `owner` / `expires` lines.
- **A2.** CONTRIBUTING states that `expires` is compared to the CI runner's UTC date and an entry is valid through the `expires` day.
- **A3.** `HAL_ORPHAN_CALLSITE_GATE` and a possibly effect-free `HAL_CORPUS_PARITY_ENFORCE` go to PR-body Follow-ups.

## §8 Errata r3 (MGR/Guy 2026-10-03: removal stop until the lot-assume audit; provenance rule). Supersedes op1, AC1, and the "Removed" wording

- **No removal in this PR.** `HAL_ORPHAN_CALLSITE_ENFORCE` STAYS in `FLAGS`. op1 is void. It gets `owner`, `expires` (`2026-11-02`) and a provenance note that says "no reader in this repo, removal pending lot-assume audit". The lint must pass with it present. Nothing in the PR removes a flag, an enforce branch or a gate. The removal is a PR-body Follow-up.
- **AC1 (replaced).** `HAL_ORPHAN_CALLSITE_ENFORCE` IS in `FLAGS`, in the rollout set, with owner and expires, and its `provenance` contains `removal pending lot-assume audit`. The catalog has exactly the 110 entries of base `1e391ed` (no entry removed).
- **AC2.** The default table now pins 20 names: the 19 rollout entries (18 plus the orphan flag, `HAL_ORPHAN_CALLSITE_ENFORCE` default `"0"`) plus `HAL_ORPHAN_CALLSITE_GATE`. `ROLLOUT_NAMES` includes the orphan flag; AC4's renewed set includes it.
- **Provenance field.** Every rollout entry carries `"provenance": "<one line>"`: `introduced: <issue/PR> - what it protected against - why it stays / what it becomes`. The lint requires a non-blank str `provenance` for rollout entries (violation by flag name, same degrade rules as owner/expires). AC7/AC9 cover a missing or non-str `provenance`; AC3 asserts presence for every live rollout entry.
- **Provenance of the orphan flag (measured).** Introduced in HAL commit 3ddf98a15 `feat(564): orphan-callsite-lint`, PR #578 (GH564): a deterministic gate for "script without call-site", warn-only, flip-by 2026-08-07. The reader lives in the HAL repo (`SYSTEM/cli/build/orphan-callsite-lint.py:189`, `=1` makes orphans exit 1); this repo only mirrors the catalog entry, and no code here reads it. It is not deleted: the check exists elsewhere, so removal here waits for the audit.
- **Other provenance lines.** The RED/GREEN authors derive each line from the entry's own description (GH/issue numbers, flip-by text). Where the description names no issue, the line says `introduced: no provenance found in this repo`.
- **op5 / AC14.** CHANGELOG has an Added bullet only (no Removed bullet); it names `flag_expiry_lint`. The doc may still mention the orphan flag as the follow-up.
- **A1 (GREEN diff).** `git diff` of `flags_catalog.py` shows zero removed entries; on rollout entries only added `owner`, `expires`, `provenance` lines.

## §9 Errata r4 (MGR/Guy 2026-10-03, audit hal#2320 section 6). Supersedes §8's "flag stays"; keeps §8's `provenance` field

- **Delete only the dead flag.** op1 and AC1 are restored: `HAL_ORPHAN_CALLSITE_ENFORCE` is removed from `FLAGS` (catalog 109 entries) and appears in no tracked text file under `engine_py/` or `scripts/` outside CHANGELOG and `docs/decisions/`. Provenance: introduced in HAL #578 / GH564 (commit 3ddf98a15, `orphan-callsite-lint`, warn-only, flip-by 2026-08-07); its only reader is HAL `SYSTEM/cli/build/orphan-callsite-lint.py:189`; this repo has none. The check lives on in HAL, so nothing is lost here.
- **Every other flag is untouched.** No flag, enforce branch or gate is removed, including the incident-born lints (cite-prelint GH681, known-reds GH1164, baseline-delta 585E30E3). `owner` + `expires` + `provenance` on the remaining 19 rollout entries, as in §2/§8. AC2 pins the 19 rollout defaults plus `HAL_ORPHAN_CALLSITE_GATE` (20 names minus the deleted one = 19 rollout + GATE, i.e. the §7 table). `ROLLOUT_NAMES` excludes the deleted flag. AC4's renewed set excludes it.
- **AC14.** CHANGELOG has a Removed bullet naming `HAL_ORPHAN_CALLSITE_ENFORCE` and an Added bullet naming `flag_expiry_lint`.
- **A1.** `git diff` of `flags_catalog.py`: exactly one removed entry; on the rollout entries only added `owner`, `expires`, `provenance` lines.
- **PR body.** Per-flag proposal table (flag / incident link if any / turn on or delete / data needed) is a proposal for Guy, no code. PR opens as DRAFT with a HOLD note.
