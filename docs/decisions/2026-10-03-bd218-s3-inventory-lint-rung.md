# bd#218 step 3 — preflight runs the inventory-lint tests when the diff adds git / read_text / subprocess calls to the engine (add-only, no LLM)

**Status: r1 (draft for the Opus gate)** · **Tier:** 2 (one production file, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `preflight.Run.step_siblings` — the one place preflight runs sibling tests; the lint set is added to its list, nothing new in `STEPS`.
**Source:** bd#218 ladder, MGR 2026-10-03 (PR #227 and #230 went red in CI on the inventory lint; the cheap script is the local run of those tests).

## §1 Problem (measured)

PR #230 added one `read_text` call in `workflows/phase_5_implement.py`. Four tests failed in CI only: `test_bd150_class_i_inventory::test_ac10`, `test_bd152_output_digest::test_ac11`, `test_bd206_class_m_sites::test_ac8`, `test_bd94_engine_owned_paths::test_ac9`. Cause: the new call site had no entry in `conformance/class_i_inventory.json`. The §1a sibling list is hand-written and missed these four; PR #227 hit the same class. A 10-second local run of the four files would have caught it.

## §2 Design — ADD-ONLY

No step is added, removed or reordered; the receipt schema and `STEPS` stay as they are. No LLM. The change is inside `step_siblings`.

- Constant `INVENTORY_LINT_TESTS` = the four files above, as `engine_py/tests/<name>.py`.
- `_adds_engine_call_sites()`: runs `git diff -U0 <merge_base> -- engine_py/bytedigger_engine` (tracked changes plus untracked `.py` files under that path, as `_changed` sees them) and looks at ADDED lines of `.py` files only. A line triggers when it matches `\b(read_text|subprocess|Popen|git_read|_git_text|_git)\b` or contains the literal `"git"`. Removed lines and context never trigger. Test files and other paths never trigger.
- `step_siblings`: when triggered, the lint files that exist in the tree and are not already in `sibling_tests` are appended to the list of tests to run, so they go through the same `_run_tests`, known-reds tolerance and pin logic as any sibling. The OK detail gets the suffix `inventory-lint: N file(s)`. When not triggered, behaviour and detail are byte-identical to today (including `no siblings`).
- Repos without `engine_py/` (every non-bytedigger target repo) never trigger; a lint file missing from the tree is skipped.
- The engine-side producer (`run_engine_preflight`) runs only syntax/stub/facts, so it is unaffected.

### Out of scope
`STEPS`, receipt schema, `check_ladder.py`, the engine producer, other lints, any change to the inventory or its tests, making the trigger smarter (no AST, no regex-negation polish).

### Limit stated
The trigger is a plain text match on added lines; a new call spelled in some other way is not seen, and CI stays the backstop. The step only turns a known CI-red class into a local red.

## §3 Acceptance criteria

All in a tmp git repo with `engine_py/bytedigger_engine/` and `engine_py/tests/`, a stub lint test file named like a real one (passing or failing), run through `run_preflight` (CLI path) so the receipt is the observable.

- **AC1** Diff adds `x = p.read_text()` in an engine file; a failing lint stub exists → `siblings` is `red`, detail names the lint file; a passing stub → `ok`, detail contains `inventory-lint: `.
- **AC2** Same for an added `subprocess.run(` line, and **AC3** for an added line containing the literal `"git"`.
- **AC4** Diff adds only a comment-free unrelated line, or the call is added in `engine_py/tests/…`, or in a non-`.py` file → lint files are NOT run (a failing stub does not turn `siblings` red; detail is exactly the pre-change `no siblings`).
- **AC5** Diff only REMOVES a `read_text` line → not triggered.
- **AC6** A lint file already listed in `sibling_tests` runs once (no duplicate); a repo without `engine_py/` and the lint files missing → no trigger, no crash.
- **AC7** `known-reds` tolerance applies: a failing lint test listed in known-reds is tolerated like any sibling.
- **AC8** `run_engine_preflight` in the same repo: no lint run (no pytest subprocess), receipt unchanged.
- **AC9** (reachability, §1y) Point = trigger in `step_siblings`; Host = `run_preflight`; Test = AC1 through the real CLI function with the receipt read from disk.

## §4 Files
In scope: `engine_py/bytedigger_engine/preflight.py`, new `engine_py/tests/test_bd218_s3_inventory_lint_rung.py`, `CHANGELOG.md`.
NOT in scope: everything else, notably `conformance/*`, the four lint tests, `check_ladder.py`, `workflows/`.
Sibling audit (§1a): `test_bd164_preflight.py`, `test_bd141_check_ladder.py`, `test_bd218_s1_preflight_rung.py`, `test_bd218_s2_receipt_producer.py`, plus `grep -l "step_siblings\|sibling_tests" engine_py/tests`, plus the four inventory-lint files themselves (preflight.py is scanned by them: the new code must not add an unkeyed `read_text`/`subprocess`/git call — reuse `_git`/`_git_text`/`_run_tests`; if an inventory entry is needed, that is a spec defect before freeze).

## §5 Provenance
Removes nothing. Introduced because: bd#218 ladder (cheap script before LLM/CI); protects against: CI-only red on inventory lint after a new engine call site; no removal planned.
