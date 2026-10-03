# bd#218 step 2 — the engine produces the phase-red receipt before the validation gate (add-only, no LLM)

**Status: r2 (amended after gate r1: empty scope, subdirectory cwd, sibling audit)** · **Tier:** 3 (two production files, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `preflight.run_engine_preflight` — the one function that runs the cheap deterministic
steps on explicit fields and writes the receipt. `run_preflight` (CLI) and the phase-5 call both go through
the same step runner and receipt writer. **Source:** bd#218, `2026-10-03-bd-ladder-map.md`; MGR 2026-10-03
(producer yes, no LLM); follows s1 (`2026-10-03-bd218-s1-preflight-rung.md`, PR #228).

## §1 Problem (measured on 154818e)

1. s1 reads the receipt, but no engine code writes one (`git grep run_preflight -- engine_py/bytedigger_engine/workflows`: 0 hits), and
   `commit_red_tests` moves HEAD right before the gate. In an engine-driven run the s1 status is therefore `stale` or `missing`,
   which is "no evidence" by the s1 reading rule. The shadow table (`ladder_table`) stays empty of `fresh`/`red` rows.
2. `run_preflight` cannot be called on the engine's spec: it needs front-matter `red_tests` and `paths`
   (`preflight.py:707-708`); the engine spec (`specs/build-spec.md`, LLM free text) has no front-matter. At the pre-GREEN point `prev.data`
   has `red_test_paths` and `red_commit_sha` but no production `paths` and no `tier`.

## §2 Design — ADD-ONLY

**The validation gate runs exactly once, whatever the producer does. Nothing is skipped, removed or reordered. No LLM, no classifier, no test run.**

### op1 `preflight.run_engine_preflight(top, red_tests, spec_text, *, base=None) -> PreflightResult`
- Runs only the steps `("syntax", "stub", "facts")`, in that order, on `fields = {"red_tests": red_tests, "paths": []}` (other fields `[]`), tier `""`,
  through the same step methods `run_preflight` uses. First red step stops the run; later steps are recorded `skipped`, as today.
- Not run, by design: `cite` (the engine spec is free text), `tier` (no MICRO in the engine), `scoped` and `siblings` (test runs; the engine already
  verifies RED mechanically before the gate, and a second run costs minutes), `prescreen` (no classifier). The receipt records this:
  `"producer": "engine"`, `"steps"` lists only the steps that ran or were skipped after a red, `"not_run": [...]`.
- `base`: when `None`, the first of `origin/main`, `main`, `HEAD` that `git rev-parse --verify` accepts. `merge_base` is computed from it; failure of all three returns a failure result and writes no receipt.
- Writes the same receipt file `receipt_path(top)` with schema 1 and `phase: "red"`, `state_hash` taken before the steps (so it equals the hash `verify_receipt` computes while the tree is unchanged: the receipt and `facts.md` live in the git dir, outside the work tree).
- `top` is the directory the caller gives (the resolved `git_cwd`, possibly a subdirectory). The producer resolves the real toplevel with `git rev-parse --show-toplevel` from it and runs every step and the state hash at that toplevel; each `red_tests` entry (relative to the given directory, or absolute) is rebased to a toplevel-relative path. Not a git work tree → failure, no receipt.
- Empty scope is not evidence: if `red_tests` is empty, not a list, or any listed entry is not an existing file after rebasing, the result is a failure and no receipt is written (old one removed). The rung then reads `missing`, never a vacuous `fresh`.
- Receipt fields: `spec` = the spec path when given, else `null`; `base` = the resolved ref name.
- Never raises; any exception becomes a failure result and no receipt (the previous receipt file is removed first, as in `run_preflight`, so a stale one cannot be mistaken for the new run).
- Refactor: the step loop and receipt writing of `run_preflight` move into one private helper that both functions call. `run_preflight` behaviour, its receipt bytes (no `producer`/`not_run` keys) and exit codes stay as they are (bd164 tests unchanged).

### op2 call in `_invoke_validation_llm`
Inside the s1 rung block, after the git cwd is resolved and found non-ambient, and before `preflight.receipt_rung`:
`preflight.run_engine_preflight(tree, prev.data.get("red_test_paths"), <text of prev.data["spec_path"], "" if absent or unreadable>)` (every argument is evaluated inside the fail-open try, so a missing key cannot raise out of the step), then the s1 rung reads the receipt as before.
- Config `preflight_rung`: `{"mode": "verify"}` (default) now also produces; `{"produce": false}` keeps s1 behaviour (read only); `{"mode": "off"}` does nothing, as in s1. Non-bool `produce` → `config-error`, as an unknown mode.
- Fail-open: a producer exception or failure result is swallowed; the s1 rung then reports whatever the receipt file says (`missing` after a failure, since the old file was removed). The gate runs once.
- Ambient cwd: no producer call (same rule as the s1 rung).
- Resume replays the cached step result: neither the producer nor the rung re-runs. Unchanged from s1.

### Out of scope
- Running tests, `cite`, `tier`, a classifier; any LLM call; skipping/replacing the gate; changing the reject-row record; new steps in the loop contract (the call lives inside the existing step, so step order, `StepContract` list and the order-asserting tests are untouched).

### Limit stated (so the table is not over-read)
An engine `fresh` receipt means syntax, stub and facts passed on the tree the gate judged. It does not mean the scoped tests or the siblings ran. A gate REJECT on `fresh` therefore shows the gate caught something these three steps did not, not something no script could. The gate-replacement PR must name which further scripts (or tests) it relies on.

## §3 Acceptance criteria

- **AC1** In a tmp git repo with a clean RED test file, `run_engine_preflight` writes a receipt (`phase: red`, `ok: true`, `producer: engine`, `steps` = syntax, stub, facts, `not_run` = cite, tier, scoped, siblings, prescreen); `receipt_rung("red", repo)` is `fresh`.
- **AC2** A RED file that mocks its own unit under test (stub_passability finding) → `ok: false`, `facts` skipped; `receipt_rung` is `red` with `red_step == "stub"`.
- **AC3** A RED `.py` file with a syntax error → `red_step == "syntax"`, `stub` and `facts` skipped.
- **AC4** Not a git work tree / no base resolvable → a failure result, no exception, no receipt file; a previously existing receipt is gone.
- **AC5** No subprocess whose command contains `pytest` or `bun test` is started (recorded `subprocess.run` calls).
- **AC6 (reachability)** `_invoke_validation_llm` in default mode, non-ambient `git_cwd` of a real repo, `prev.data["red_test_paths"]` set: the s1 `preflight_receipt` event has `status: "fresh"` (not `missing`), `extra_data["preflight"]["status"] == "fresh"`, gate called once. With a mocking RED file the event/record is `red` with `red_step: "stub"`, gate still called once.
- **AC7** `{"produce": false}` → producer not called, event `missing` on a repo without receipt; `{"mode": "off"}` → neither producer nor rung; non-bool `produce` → `config-error`, gate once.
- **AC8** Producer raising (patched `preflight.run_engine_preflight`) → no exception out of the step, rung runs, gate once.
- **AC9 (add-only)** All `invoke_llm_subprocess` kwargs, minus `extra_data["preflight"]`, are equal for produce on and `produce: false`, and for a `fresh` and a `red` receipt.
- **AC10** Ambient git cwd (no `git_cwd`) → producer not called, `ambient-skip`, gate once.
- **AC11** `run_preflight` is unchanged: `test_bd164_preflight.py`, `test_bd141_check_ladder.py`, `test_bd141_p4d_role_template_injections.py` and the other s1 sibling files pass without edits (p4d drives `_invoke_validation_llm` on a real repo with empty `red_test_paths`: the producer fails (empty scope), the rung reads `missing`, the assertions there do not look at preflight); `test_bd218_s1_preflight_rung.py` passes with one edit, its `_drive` helper stubs `preflight.run_engine_preflight` to a no-op (its tests pre-stage receipts the producer would delete); a CLI receipt has no `producer` key.
- **AC13** Empty scope: `red_tests` = `[]`, `None`, a non-list, or a path that is not a file → failure result, no receipt, old receipt gone; through `_invoke_validation_llm` with `red_test_paths` absent, `[]` and a nonexistent file the event is `missing` (never `fresh`), gate once, no exception.
- **AC14** Subdirectory `git_cwd`: repo with the RED file under `sub/`, `git_cwd = repo/sub`, paths relative to `sub`. A clean RED file → event `fresh`; a mocking RED file → event `red`, `red_step == "stub"`.
- **AC15** An internal failure (patched receipt writer raises) → failure result, no exception, no receipt file.
- **AC12** Receipt freshness: after the producer ran, `verify_receipt` is `fresh`; after any edit of a tracked file it is `stale`.

## §4 Files

In scope: `engine_py/tests/test_bd218_s1_preflight_rung.py` (the `_drive` stub only, already committed with the spec), `engine_py/bytedigger_engine/preflight.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py` (the rung block only), new `engine_py/tests/test_bd218_s2_receipt_producer.py`, `CHANGELOG.md`.
NOT in scope: `check_ladder.py`, `reject_log.py`, `reject_stats.py`, `workflows/engine.py`, `phases/`, the `StepContract` list, every order-asserting test.
Sibling-test audit (§1a): the s1 list (`test_bd218_s1_preflight_rung.py`, `test_bd141_check_ladder.py`, `test_bd164_preflight.py`, `test_bd92_per_cycle_artifacts.py`, `test_gh705_callsite_stable_prefix.py`, `test_phase_5_implement_A3398552.py`, `test_gh963_validation_execution_failure.py`, `test_llm_subprocess_allowed_tools.py`, `test_7C4D70ED_red_executability_check.py`, `test_phase_5_graphfirst_DA48BEAC.py`, `test_bd141_p4d_role_template_injections.py`, `test_bd139_single_reviewer.py`) plus `grep -l run_preflight engine_py/tests`.
The s1 tests whose harness has a real non-ambient repo (AC3/AC8/AC11/AC12 family) now see a producer run before the rung; audit them for the receipt they pre-stage being removed by the producer (the producer deletes the old receipt first). Resolved: `_drive` in `test_bd218_s1_preflight_rung.py` (my own merged file) stubs the producer. Other siblings: ambient (no `git_cwd`) → no producer call; the one exception found by gate r1 is `test_bd141_p4d_role_template_injections.py` (real repo, absolute `git_cwd`), handled by the empty-scope rule.

## §5 Provenance (Guy 2026-10-03)

Removes nothing. The three steps reused (`syntax`, `stub`, `facts`) were introduced by bd#164 (PR #186); the scoped/siblings test runs are deliberately not repeated because the engine verifies RED itself (`verify_red_fails_mechanically`).

## §6 Cost note

No LLM $. Added wall time: one `compile`/`bash -n`, one AST lint per RED file, one `facts_pack.collect` (git reads) per validation call. Measure on a real run before claiming a number; none is claimed here.
