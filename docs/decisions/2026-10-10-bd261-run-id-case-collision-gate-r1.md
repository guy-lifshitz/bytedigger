# bd#261 gate review r1: spec and RED for the run id case collision guard

Scope: spec `docs/decisions/2026-10-10-bd261-run-id-case-collision.md` (DRAFT r1) and RED `engine_py/tests/test_bd261_run_id_case_collision.py` on `89c35cc`. Method: read the spec, the RED file, `event_log.py`, `config_provider.py`, `run.py`, the bd#93/#203 callers, `tree_scan_lint.py`, the inventory and the flag scripts. No command was run by me. Preflight facts taken as given: RED is 28 fail / 1 skip on base, eight sibling suites pass.

## Step 1: spec-internal consistency

Names agree everywhere in the spec and the RED: `HAL_RUN_ID_CASE_GUARD`, `E_RUN_ID_CASE_COLLISION`, `RunIdCaseCollision`, `_on_disk_spelling`, `run_id_case_guard_enabled`. Spec facts against code, all true on this tree: `run_scoped_log_path` is at `event_log.py:86` and its docstring says "Pure path math, no filesystem I/O" (to be reworded); `run.py:348-353` catches `Exception` and writes `event log disabled: {e}`; `semantic_verifier.py:80-84` catches `ValueError` and logs a warning, then falls back to the default log; `_RUN_ID_RE` accepts both cases; `gate_enabled` is "enabled unless exactly 0" with aliases.

One drift, and it is fatal for AC12 (finding 1): the spec names the tree-scan key `event_log.py::_on_disk_spelling::listdir#0`. The lint builds keys as `rel::qual::kind#ordinal` with `kind` taken from `_classify` (`tree_scan_lint.py:193-205, 317`). For `os.listdir(...)` the resolved kind is the dotted name `os.listdir` (member of `_RESOLVED_KINDS`, L39), so the key the lint will compute is `event_log.py::_on_disk_spelling::os.listdir#0`. The inventory already follows this convention for the dotted kinds (`error_codes.py::harvest_codes::os.walk#0`). The bare `listdir` form is only used for attribute kinds (`iterdir`, `rglob`, `glob`), which is where the bd#260 keys come from. `listdir` is not an attribute kind and not a helper kind.

## Step 1.5: rule-overlap simulation

op1 is a small dispatcher: (a) `lstat` fails not-found, (b) `lstat` fails other OSError, (c) lstat hits, listing fails, (d) exact entry, (e) case-fold entry, (f) neither.

- (a) returns path, no listing: AC3 (missing root, empty root, only `R2`), AC6 missing root. Covered for the result; the "no listing" half is not asserted (finding 4).
- (b) AC6 lstat test. Covered.
- (c) AC6 listdir test. Covered.
- (d) exact first: AC9 (sim and real), AC4 (sim and real). With `R1` and `r1` both listed, id `r1` must hit (d) before (e); a GREEN that takes the first fold match (`R1`) would raise and fail AC4 sim. Order is pinned.
- (e) AC1, AC2, AC5 other values, AC7, AC8.
- (f) lstat hits but the listing has no matching entry: no test (finding 2).

## Step 2: section 2 against section 3

The spec has no separate section 2 for paths; the design (section 3) ops map onto ACs in section 5. Every op has an AC except op1's branch (f) and the "no listing when the id is absent" sentence of op1 (findings 2 and 4). Error path op2 has AC1/AC7/AC10; degrade op3 has AC6. Section 6 edge 3 claims "a vanished entry" is covered by AC6, but AC6 tests a `PermissionError` and an lstat `OSError`, not a vanished entry. That claim is not backed by a test.

## Step 3: RED adequacy

- Fails at assert time, not collection: new names are only touched inside test bodies; preflight confirms 28 fail. The one skip is the real-FS Linux-only twin (`test_ac4_case_sensitive_fs_unchanged_real`), which runs on Linux and skips on macOS; on macOS the other real twins run (APFS folds, probe confirmed in the spec).
- Positive controls (`_guard_is_live`, `_refused` before the degrade/kill-switch calls) make each "returns the path" test fail pre-GREEN, so none passes only because the guard is missing.
- Sim shim fidelity: `_SimOs` replaces `event_log.os` only. It overrides `listdir` and `lstat` for registered roots and everything else falls through to the real `os`. `lstat` folds case when `fold=True`, matches exactly when not; `listdir` returns the stored spelling; a missing entry raises `FileNotFoundError`; an unregistered path goes to the real filesystem. It does not call the unit, does not decide the outcome, and does not touch `run_scoped_log_path`. The unit is the real function. Not vacuous. Consequence worth knowing: a GREEN that uses `Path.exists`, `Path.lstat`, `Path.iterdir` or `os.scandir` bypasses the shim and fails AC1 sim. That is consistent with the spec text (`os.lstat`, `os.listdir`) and with the inventory key, so it pins the design rather than over-constraining.
- Wrong-GREEN check:
  - always rejects when the exact directory exists: fails AC9 (sim and real) and AC1's own `R1` accept line.
  - lowercases the id in the path: fails AC1 (`R1` must return `root/R1/events.jsonl`).
  - first case-fold match without an exact check: fails AC4 sim.
  - lists on every call with the guard on: passes everything except the kill-switch assertion. It is not caught for the guard-on, id-absent case (finding 4).
  - raises when lstat hits and the listing has no match: passes everything (finding 2).
  - no `try/except` around `listdir` or `lstat`: fails the two AC6 tests.
  - reads `os.environ` in the function instead of the provider: fails AC10.
  - skips `lstat` and only lists: fails the AC6 lstat test.
- AC7: real form runs `run.py` in a subprocess against a folding FS, asserts exit 0, the code on stderr, and the log bytes unchanged. `run_retention_dry_run` runs first and prints nothing for a fresh directory. Sim form calls `sv._run_event_log` with `r261`, `HAL_RUN_LOG_ROOT` set, asserts fallback to the default path and exactly one warning carrying the code. Both hold against the code read above.
- AC9 holds: exact spelling present is "mine", twice in a row, in sim and real.
- AC10 against names in the module: the current module has no `rmtree`, `unlink`, `rmdir`, `remove`, `rename`, `replace`, `truncate`, `move`, `system` or `Popen` as a Name or Attribute (the only "removed" is inside a string at L167, `os.open` at L236 is not in the banned set, and `open` is only banned inside the two named functions). A GREEN built as `os.lstat`, `os.listdir`, `str.lower` and a `for` loop needs none of them. The "no `str.replace`" note is correct to include.
- AC11: `retire-by:` is a token both `flag_owner_lint.py` (L32) and `flip_horizon.py` (L4) accept; 2026-11-10 is 31 days out, inside the 45-day bound. The provider-less free function test patches `config_provider.get_config`, which matches how `run_log_retention_days` is written. Only the free function is tested, not the provider method itself; acceptable since the free function delegates to it.
- AC12: see finding 1. As written the test also cannot pass with any GREEN that satisfies AC1 sim.

## Step 4: reachability

Point: `event_log.py::run_scoped_log_path` (new check, before `return base / run_id / "events.jsonl"` at L98). Hosts: `run.py:348` (inside `try ... except Exception`, L352) and `semantic_verifier.py:82` (`except ValueError`, L83). Test path: AC7 real runs `run.py` end to end; AC7 sim runs `_run_event_log`; AC1/AC2/AC9 call the unit directly. Reachable. The helper `_on_disk_spelling` is reached only through the unit; the listing site is exercised by every sim test.

## Adversarial edges (not already an AC)

1. lstat hits but the listing has no entry equal to the id in either spelling (entry removed between the two calls). Spec says "return as today"; nothing tests it. A GREEN that raises with `existing=None` passes the whole RED.
2. The colliding entry is a regular file or a symlink, not a directory (`R1` as a file; `lstat` hits anyway). The spec says "directory" in the message but does not say what happens. Undefined; either refusal or acceptance is defensible, but one must be chosen and pinned.
3. Guard on, id absent from a large root: the spec says no listing happens; a GREEN that lists first and compares passes. Cost, not correctness.
4. Non-ASCII entries in the root whose `str.lower()` equals the id (for example `K`, U+212A Kelvin sign, lowers to `k`). The spec relies on "ids are ASCII" to use `lower`, but the entries are not. On APFS that spelling really does fold onto `k`, so refusing is right there; on a case-sensitive FS the lstat for the id fails first, so no false positive. Fine, but a one-line spec note would close the question.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: FAIL
- AC13: PASS

## Findings

1. MAJOR (spec op6, AC12, RED `KEY` at line 430): the tree-scan key is wrong. The lint will compute `event_log.py::_on_disk_spelling::os.listdir#0`. With the spec's `...::listdir#0` the inventory entry never matches a real site: `test_ac12` asserts the inventory has the wrong key (so it needs the wrong key present), and `check()` reports the real site unclassified and the wrong key stale, so `check(...) == []` fails. Both requirements cannot hold at once. `from os import listdir` does not help: bindings resolve it to `os.listdir` too, and it would bypass the sim shim and fail AC1 sim. Fix: change the key to `event_log.py::_on_disk_spelling::os.listdir#0` in the spec (op6, AC12) and in the RED (`KEY`).
2. MAJOR (spec op1 branch "no entry matches either way", section 6 edge 3): the branch has no test, and section 6 claims AC6 covers "a vanished entry" when AC6 does not. Add an AC (or extend AC6) and a sim test: lstat hits for `r1`, listing returns `[]` or `["R2"]`, expect the path returned and no raise. Otherwise correct the claim in section 6.
3. MINOR (spec AC12 "tree_scan_lint.py is not modified (no diff against the base)"): no test asserts it. It is checkable by the orchestrator with a diff; either say so in the spec or drop the sentence from the AC.
4. MINOR (spec op1 "Does not exist: no listing", RED AC3): the sim records `listdir_calls` but AC3 never asserts it is empty for the absent-id cases. A GREEN that lists on every guard-on call passes. Add `assert sim.listdir_calls == []` after the loop over `empty`, `only_r2`, `missing` in `test_ac3_no_false_positives_sim`, before `present` is used.
5. MINOR (spec edge 2 above): say what happens when the colliding entry is not a directory, or state that any entry kind counts.
6. MINOR (RED AC8): equal outcome with and without the key only proves determinism; a GREEN that never reads the key is the intended one, so this is a weak guard, not a defect.

VERDICT: REJECTED
