# bd#90: the post-fix test scope is never empty

**Status: r3 (gate r1 REJECT: F1/F2 MAJOR, F3-F6 MINOR, all folded), MICRO.** Scope cut by Guy 2026-10-03: the part of bd#90 about review findings filtered by citation format (withhold by suspect rate) is DROPPED: it is wording/format polish, not a cheap deterministic check. Satisfaction FAIL -> fix loop already exists (bd#85, cap 2); no change.

| | |
|---|---|
| **Tier** | MICRO. One function in `workflows/phase_6_review.py`, one test file, one line in `docs/events.md`. |
| **Class** | SYSTEMATIC. Chokepoint: `_run_pytest_post_fix` scope builder. |
| **LLM calls added** | None. |

## Problem (on `1e391ed`)

`_run_pytest_post_fix` reads `prev.data["red_test_paths"]`, which phase 6 never sets, and falls back to the fix manifest filtered to test files. A fix that touches only source files has an empty scope, the step emits `post_fix_pytest_skipped(no_test_scope)` and the fix lands untested.

## Design (r3, gate r1 F1-F6 folded)

The scope is built by one helper, `_post_fix_test_scope(prev_data, scratchpad, git_cwd, manifest)`, returning `(paths, counts)`.

**Legs and precedence (provenance: `4961254A` — scope follows a bounded channel so ambient dirt cannot slip in; its test `test_4961254A_commit_manifest_inversion.py::test_ac8_red_test_paths_takes_priority_over_manifest` stays green and its meaning is kept).**
1. `prev.data["red_test_paths"]` is a list (even empty): RED leg = that list. The manifest is NOT consulted (priority kept, as today). The persisted file is NOT read.
2. `red_test_paths` absent/None (the only production case, phase 6 never sets it): RED leg = persisted `integrity/red-test-paths.txt` under the scratchpad (`phase_5_implement._read_red_test_paths`), PLUS manifest test files (as today's fallback).
3. Sibling leg (both cases): for each changed non-test source file in the manifest (`.py` only), tracked test files from `git ls-files` (filtered by `_is_test_py_path`) that live in the same directory, or whose basename contains the source stem. The same-directory rule is skipped for root-level sources; stems shorter than 4 characters or `__init__` produce no stem match.

**Normalisation.** Every path is stripped, made repo-relative, and dropped if it escapes the git cwd or does not exist on disk. De-duplication is by the normalised spelling, first occurrence wins. Order: RED leg, manifest tests, siblings (sorted).

**Cap.** At most 50 siblings, applied after de-duplication against the other legs. RED and manifest paths are never dropped by the cap.

**Counts.** `n_red` = RED-leg paths kept; `n_manifest` = manifest tests kept and not already counted in the RED leg; `n_sibling` = siblings kept and not already in the scope; `n_total` = len(final scope).

**Event.** Before invoking pytest emit `post_fix_pytest_scope` `{n_red, n_manifest, n_sibling, n_total}` and document it in `docs/events.md` (new row, with the existing `post_fix_pytest_*` events as neighbours).

**Degrade, never error.** `git ls-files` failure: siblings empty, no error. Empty final scope: `post_fix_pytest_skipped(no_test_scope)`, status ok, pytest not invoked. Timeout, baseline-delta gate and error codes unchanged.

## Acceptance (RED)

- AC1: `red_test_paths` absent; manifest has only `pkg/mod.py`; persisted file lists an existing RED test: argv includes that RED path, `n_red >= 1`.
- AC2: a fix touching `pkg/mod.py` pulls in tracked `tests/test_mod_extra.py` (stem match) and a same-directory `test_*.py`, counted in `n_sibling`; over 50 candidates are capped at 50 sorted.
- AC3: nothing in any leg: status ok, `no_test_scope` skip, pytest not invoked.
- AC4: a RED path missing on disk is not passed to pytest.
- AC5: `prev.data["red_test_paths"]` (list) wins over the persisted file, and the manifest test files are NOT in scope in that case.
- AC6: `red_test_paths` absent: manifest test file `tests/test_touched.py` plus the persisted RED path plus a sibling are all in argv; a path present in both RED and manifest appears once; `n_manifest` excludes the duplicate; `n_total` equals the number of test paths in argv.
- AC7: the cap never drops RED/manifest paths (RED + manifest + 60 sibling candidates: both legs present, siblings = 50).
- AC8: a persisted RED path with surrounding whitespace is normalised; one escaping the git cwd (`../x/test_a.py`) is dropped.
- AC9: `git ls-files` failing leaves siblings empty and the step still runs the other legs.
- AC10 (existing, stay green): `test_phase_6_post_fix_pytest_gate_7A940850.py`, `test_bd85_retry_budgets.py`, `test_4961254A_commit_manifest_inversion.py`.

## Not touched

Review aggregation, quote verification, withhold, verdict logic, satisfaction gate, retry budgets.
