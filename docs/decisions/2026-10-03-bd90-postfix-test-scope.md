# bd#90: the post-fix test scope is never empty

**Status: r2 (pre-gate), MICRO.** Scope cut by Guy 2026-10-03: the part of bd#90 about review findings filtered by citation format (withhold by suspect rate) is DROPPED: it is wording/format polish, not a cheap deterministic check. Satisfaction FAIL -> fix loop already exists (bd#85, cap 2); no change.

| | |
|---|---|
| **Tier** | MICRO. One function in `workflows/phase_6_review.py`, one test file, one line in `docs/events.md`. |
| **Class** | SYSTEMATIC. Chokepoint: `_run_pytest_post_fix` scope builder. |
| **LLM calls added** | None. |

## Problem (on `1e391ed`)

`_run_pytest_post_fix` reads `prev.data["red_test_paths"]`, which phase 6 never sets, and falls back to the fix manifest filtered to test files. A fix that touches only source files has an empty scope, the step emits `post_fix_pytest_skipped(no_test_scope)` and the fix lands untested.

## Design

The scope is the ordered, de-duplicated union of:
1. RED paths: `prev.data["red_test_paths"]` when it is a list, else the persisted `integrity/red-test-paths.txt` under the scratchpad (`phase_5_implement._read_red_test_paths`).
2. Manifest test files (as today).
3. Sibling tests: for each changed non-test source file in the manifest, tracked test files (`git ls-files`, filtered by `_is_test_py_path`) in the same directory or whose basename contains the source stem. At most 50, sorted.

Only paths that exist on disk under the git cwd are kept. Before invoking pytest emit `post_fix_pytest_scope` `{n_red, n_manifest, n_sibling, n_total}` (add to `docs/events.md`). An empty scope still degrades as today (`post_fix_pytest_skipped(no_test_scope)`, status ok). Timeout, baseline-delta gate and error codes are unchanged.

## Acceptance (RED)

- AC1: manifest has only `pkg/mod.py`; `integrity/red-test-paths.txt` lists a RED test that exists; pytest argv includes that RED path and `post_fix_pytest_scope.n_red >= 1`.
- AC2: a fix touching `pkg/mod.py` pulls in tracked `tests/test_mod_extra.py` (stem match) and a `test_*.py` in the same directory; both counted in `n_sibling`; more than 50 candidates are capped at 50.
- AC3: no RED file, no manifest tests, no siblings: status ok, `post_fix_pytest_skipped(no_test_scope)`, pytest not invoked.
- AC4: a RED path missing on disk is not passed to pytest.
- AC5: `prev.data["red_test_paths"]` (list) takes precedence over the persisted file.
- AC6 (existing, stay green): `test_phase_6_post_fix_pytest_gate_7A940850.py`, `test_bd85_retry_budgets.py`.

## Not touched

Review aggregation, quote verification, withhold, verdict logic, satisfaction gate, retry budgets.
