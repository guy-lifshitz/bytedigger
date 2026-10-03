# bd#218 step 3 — preflight runs the inventory-lint tests when the diff adds git / read_text / subprocess calls to the engine (add-only, no LLM)

**Status: r2 (amended after gate r1)** · **Tier:** 2 (one production file, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `preflight.Run.step_siblings` — the one place preflight runs sibling tests; the lint set is added to its list, nothing new in `STEPS`.
**Source:** bd#218 ladder, MGR 2026-10-03 (PR #227 and #230 went red in CI on the inventory lint; the cheap script is the local run of those tests).

## §1 Problem (measured)

PR #230 added one `read_text` call in `workflows/phase_5_implement.py`. Four tests failed in CI only: `test_bd150_class_i_inventory::test_ac10`, `test_bd152_output_digest::test_ac11`, `test_bd206_class_m_sites::test_ac8`, `test_bd94_engine_owned_paths::test_ac9`. Cause: the new call site had no entry in `conformance/class_i_inventory.json`. The §1a sibling list is hand-written and missed these four; PR #227 hit the same class. A local run of the four files would have caught it (the six-file set below measured 30 s).

## §2 Design — ADD-ONLY (r2: trigger simplified after gate r1)

No step is added, removed or reordered; the receipt schema and `STEPS` stay as they are. No LLM. The change is inside `step_siblings`.

- Constant `INVENTORY_LINT_TESTS` = six files, as `engine_py/tests/<name>.py`: `test_bd94_engine_owned_paths`, `test_bd150_class_i_inventory`, `test_bd152_output_digest`, `test_bd206_class_m_sites`, `test_bd89_p3c_deterministic_synthesize_report`, `test_bd89_p3b1b_ii_aggregation_helper` (the last two hold the real-tree tree-scan and class-I guards; gate r1 finding 2).
- **Trigger (r2):** `self._changed()` (the existing, already-keyed changed-file set; tracked changes since merge-base plus untracked) contains a `.py` file under `engine_py/bytedigger_engine/` (see the path rule below). No token list and no diff parsing: gate r1 finding 1 showed any hand-written list of call spellings drifts from the lints' own sets (read_bytes, json.load, os.walk, _git_read, ...), which is the defect this rung exists to remove. Cost of the broader rule, measured on this tree: the six files run in 30 s (230 tests); they run only when engine code changed.
- Trigger path rule exactly: a changed path `p` with `p.startswith("engine_py/bytedigger_engine/")` and `p.endswith(".py")`. (`tests/` lives at `engine_py/tests/`, outside that prefix. Conformance files under the prefix do trigger: editing a lint can break the others.)
- `step_siblings`: when triggered, the lint files that exist in the tree and are not already in `sibling_tests` (compared after `os.path.normpath`) are appended to the list to run; they go through the same `_run_tests`, known-reds tolerance and pin logic as any sibling. The OK detail gets `; inventory-lint: N file(s)` where N = number appended, only when N > 0. When not triggered, or N = 0, behaviour and detail are byte-identical to today (including `no siblings`).
- Implementation constraint (gate r1 finding 3): no new git call and no new file-system walk. The only data source is `self._changed()`; the new code adds no `read_text`/`subprocess`/`ls-files`/`status`/`add`/`--name-only` call, so no inventory entry is needed.
- Failure: `_changed()` raising keeps today's behaviour for `siblings` (it fails closed with the existing internal-error red); the trigger adds no new failure path.
- Repos without `engine_py/` (every non-bytedigger target repo) never trigger; a lint file missing from the tree is skipped.
- The engine-side producer (`run_engine_preflight`) runs only syntax/stub/facts, so it is unaffected.
- A pure file deletion is not in `_changed()` (`--diff-filter=d`), so it does not trigger; stated, not handled.

### Out of scope
`STEPS`, receipt schema, `check_ladder.py`, the engine producer, other lints, the inventory and the lint tests, token matching of any kind.

### Limit stated
The rung runs on every engine `.py` change, not only on new call sites, so it is a 30-second tax per such preflight; in exchange it cannot miss a spelling. CI stays the backstop for a pure deletion.

## §3 Acceptance criteria

All in a tmp git repo with `engine_py/bytedigger_engine/` and `engine_py/tests/`, stub lint files named like the real ones (passing or failing), run through `run_preflight` (CLI path) so the receipt is the observable.

- **AC1** A change (tracked edit) to an engine `.py` that adds `p.read_text()`, `subprocess.run(`, a `"git"` argv, `read_bytes(`, `_git_read(` or `os.walk(` (parametrized) with a failing lint stub → `siblings` red, detail names the lint file; passing stubs → `ok`, detail ends `inventory-lint: 6 file(s)`. An untracked new engine `.py` triggers the same.
- **AC2** An engine `.py` change with NO such call (e.g. a plain assignment) also triggers (the rule is path-based), shown with a failing stub → red.
- **AC3** A removal-only edit of an engine `.py` triggers.
- **AC4** Only `engine_py/tests/…` changed, or only a non-`.py` file under the engine path, or a file outside `engine_py/` → NOT triggered: a failing stub does not turn `siblings` red; with empty `sibling_tests` the detail is exactly `no siblings`; with a non-empty `sibling_tests` the detail has no `inventory-lint` suffix.
- **AC5** A lint file listed in `sibling_tests` (also spelled `./engine_py/tests/…`) runs once; N counts only the appended ones; N = 0 → no suffix.
- **AC6** A repo without `engine_py/`, and lint files missing from the tree → no trigger/skip, no crash.
- **AC7** `known-reds` tolerance applies to a failing lint test like any sibling.
- **AC8** `run_engine_preflight` in the same repo: no lint run (recorded `subprocess.run` calls contain no pytest), receipt unchanged.
- **AC9** (reachability, §1y) Point = trigger in `step_siblings`; Host = `run_preflight`; Test = AC1 through the real CLI entry point with the receipt read from disk.
- **AC10** A change committed on the branch since merge-base (not in the worktree) triggers.

## §4 Files
In scope: `engine_py/bytedigger_engine/preflight.py`, new `engine_py/tests/test_bd218_s3_inventory_lint_rung.py`, `CHANGELOG.md`.
NOT in scope: everything else, notably `conformance/*`, the four lint tests, `check_ladder.py`, `workflows/`.
Sibling audit (§1a): `test_bd164_preflight.py`, `test_bd141_check_ladder.py`, `test_bd218_s1_preflight_rung.py`, `test_bd218_s2_receipt_producer.py`, plus the grep hits `test_bd90_postfix_test_scope.py` and `test_phase_5_C844CC77_root_sibling_discovery.py`, plus the six inventory-lint files themselves, which scan `preflight.py` (tree-scan) — the new code adds no call site, see §2 constraint; if an inventory entry turns out needed, that is a spec defect.

## §5 Provenance
Removes nothing. Introduced because: bd#218 ladder (cheap script before LLM/CI); protects against: CI-only red on inventory lint after a new engine call site; no removal planned.
