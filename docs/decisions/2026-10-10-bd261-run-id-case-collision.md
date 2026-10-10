---
red_tests: [engine_py/tests/test_bd261_run_id_case_collision.py]
sibling_tests: [engine_py/tests/test_bd93_run_scoped_event_log.py, engine_py/tests/test_bd203_semantic_verifier_run_log.py, engine_py/tests/test_bd260_run_retention_dry_run.py, engine_py/tests/test_bd94_engine_owned_paths.py, engine_py/tests/test_bd89_p3c_deterministic_synthesize_report.py, engine_py/tests/test_bd243_green_entry_guard.py, engine_py/tests/test_9AB32375_config_provider.py, tests/test_bd89_p3d_flag_owner_lint.py]
paths: [engine_py/bytedigger_engine/event_log.py, engine_py/bytedigger_engine/config_provider.py, engine_py/bytedigger_engine/flags_catalog.py, engine_py/bytedigger_engine/conformance/tree_scan_inventory.json, docs/configuration.md, CHANGELOG.md]
---
# bd#261: run ids that differ only by case must not share a run directory

**Status: DRAFT r1** · **Tier:** 2 (engine `.py`: spec, RED, gate, GREEN) · **Class:** BUG (split out of #203, from the #93 review)
**Base:** `origin/main` `d78089c`.
**Chokepoint:** one check inside `run_scoped_log_path` in `engine_py/bytedigger_engine/event_log.py`, the only place a run id becomes a path (callers: `run.py`, the bd#203 helper in `semantic_verifier.py`; neither gets its own check). The switch is read in one place, `config_provider.run_id_case_guard_enabled()`.

## 1. Problem (measured on `d78089c`, macOS APFS)

- `run_scoped_log_path("R1", root)` and `run_scoped_log_path("r1", root)` return two different `Path` values, but on a case-insensitive filesystem both name one directory: after `<root>/R1/` is created, `<root>/r1/` exists and `os.listdir(root)` shows only `R1`. Two runs with those ids append to one `events.jsonl`; replay and the phase 8 archive then mix their events. Reproduced on this machine before writing this spec.
- The pattern `_RUN_ID_RE` accepts both letter cases, and nothing compares an id to the directories already present.

## 2. Decision: reject, do not normalise

Normalising (fold every id to lower case in the path) is refused:
1. Existing run directories keep their on-disk case (`R1`, `9f3c...`, `A...`). A lowered path would orphan every resumed run whose id has an upper-case letter on a case-sensitive filesystem (Linux CI, containers) and silently start a second log for it.
2. The bd#93 contract (`run_scoped_log_path(id, root) == root / id / "events.jsonl"`, tests AC3, AC7) and the bd#260 retention routine both rely on the directory name being the id as given. Normalising changes all of them.
3. The ids in the wild are uuid hex (lower case) or short caller-chosen names; no id has to change if the second one of a clashing pair is simply refused.

Rejecting only a **real alias** is also what makes it safe without a flag day: the check fires only when the directory for this id already exists under a *different spelling*. A new id, a resumed id (exact spelling present), and a case-sensitive filesystem where `R1` and `r1` really are two directories all behave exactly as today.

Shadow first (Guy's principle 4) does not fit a corruption guard: a warn-only mode lets two runs write one log, which is the defect. The mitigation for "ids in the wild may depend on it" is the kill switch (below), and the narrow trigger above.

## 3. Design

- **op1, the check.** In `run_scoped_log_path`, after the existing id and root validation and before returning, when the guard is enabled: `lstat` of `<root>/<run_id>`.
  - Does not exist (including a missing root): no collision, return as today. No listing.
  - Exists: list the entries of `<root>` once (`os.listdir`, in a small private helper `_on_disk_spelling(base, run_id) -> str | None`). If an entry equals `run_id` exactly: it is this run's own directory, return as today. Otherwise, if an entry equals `run_id` ignoring case (ids are ASCII, so `str.lower`), that entry is the colliding spelling: raise `RunIdCaseCollision`. No entry matches either way (e.g. the listing raced a removal): return as today.
  - The function stays free of writes: it creates nothing, changes nothing. (The bd#93 docstring "no filesystem I/O" is updated to "read-only".)
- **op2, the error.** `class RunIdCaseCollision(ValueError)` in `event_log.py`, so both existing callers, which already catch `ValueError` and degrade (`run.py`: `event log disabled: ...`, the run goes on without the implicit log; `semantic_verifier`: warning, falls back to the default log), keep degrading and nothing crashes. Message: `E_RUN_ID_CASE_COLLISION: run id 'r1' differs only by case from existing run directory 'R1' under <root>; use the original spelling or a different id (set HAL_RUN_ID_CASE_GUARD=0 to disable this check)`. Attributes `.code == "E_RUN_ID_CASE_COLLISION"`, `.run_id`, `.existing`.
- **op3, degrade.** A listing or `lstat` failure other than "does not exist" (`OSError`: permission, I/O) never raises: the check is skipped for that call (fail-open: nothing is written anywhere, the result is the old behaviour). No network call, no model call, no read of any credential or billing variable, so it is identical on a subscription and on API tokens.
- **op4, the switch.** `config_provider`: provider method `run_id_case_guard_enabled()` (default provider: `self.gate_enabled("HAL_RUN_ID_CASE_GUARD")`, i.e. on unless the value is exactly `0`; `BD_`/`BYTEDIGGER_` aliases honoured) plus a free function of the same name that tolerates a provider without the method (neutral default: enabled). `event_log.py` imports the free function; it stays env-free. With `HAL_RUN_ID_CASE_GUARD=0` the function returns what it returned before this lot, byte for byte, and lists nothing.
- **op5, catalog and docs.** `flags_catalog.FLAGS["HAL_RUN_ID_CASE_GUARD"]`: kind `gate`, default `"1"`, module `event_log.py`, `owner: "guy-lifshitz"`, `provenance: "introduced: bd#261 - run ids differing only by case shared one run directory on case-insensitive filesystems - guard on by default, =0 restores the old behaviour"`, and the description carries `retire-by:2026-11-10` (the switch is for one release). One row in `docs/configuration.md` after the retention row, one bullet in the `### Added` block of CHANGELOG `[Unreleased]`.
- **op6, tree-scan inventory (bd#94 lint).** The helper's `os.listdir` is a new tree-scan site, key `event_log.py::_on_disk_spelling::listdir#0`. Entry in `tree_scan_inventory.json`, class `not-a-gate`, note: it lists the direct children of the run log root, a directory outside every worktree and outside the git tree, only to compare spellings of one run id; its result decides one `ValueError`, never a gate, a commit or a hash. The lint itself is untouched.

Residual window, stated: two runs with clashing ids that both pass the check in the same instant, before either directory exists, are not caught (the check is read-then-create, and creation is the caller's `mkdir`). The second one to start after the first directory exists is caught. Closing the window would need an atomic reservation in `run.py`; out of scope for this one-place fix.

Out of scope: normalisation, an atomic reservation of the id, changes to `run.py` or `semantic_verifier.py`, the retention routine (bd#260), a case check on ids passed through `--event-log` (no id becomes a path there).

## 4. Acceptance criteria. RED file: `engine_py/tests/test_bd261_run_id_case_collision.py`

Real path: real directories under `tmp_path`. Collision cases need a filesystem that folds case: a fixture probes it (create `tmp_path/Probe`, test `tmp_path/pROBE`) and the real-filesystem tests are skipped where it does not fold; their twins, marked `sim`, run everywhere by making `os.listdir` and `os.lstat` as seen from `event_log` behave as a folding filesystem (a stand-in for the filesystem, not for the unit). The one-line reference case (AC1) therefore always runs in one of the two forms, and on macOS in both.

- **AC1** Reference case. `<root>/R1/` exists (created by the test). `run_scoped_log_path("r1", root)` raises `RunIdCaseCollision` (a `ValueError`), with `.code == "E_RUN_ID_CASE_COLLISION"`, `.existing == "R1"`, and the message naming both spellings and `HAL_RUN_ID_CASE_GUARD`. `run_scoped_log_path("R1", root)` still returns `<root>/R1/events.jsonl`. The tree under `root` is the same before and after (nothing created).
- **AC2** Both directions and mixed case: with `Run-Ab` present, `run-ab`, `RUN-AB` and `rUN-aB` are refused, naming `Run-Ab`; `Run-Ab` is accepted.
- **AC3** No false positives: with no directory for the id (empty root, missing root, only `R2` present) `run_scoped_log_path("R1", root)` returns the path and creates nothing; after `R1/` exists, an unrelated id `R10` and a prefix `R` are accepted.
- **AC4** Case-sensitive filesystem behaviour is unchanged: when the filesystem does not fold case (probe says so, or sim with exact listing semantics) and both `R1/` and `r1/` exist as two real directories, both ids are accepted, each to its own path.
- **AC5** Kill switch: `HAL_RUN_ID_CASE_GUARD=0` (and the `BD_RUN_ID_CASE_GUARD=0` alias) with `R1/` present: `run_scoped_log_path("r1", root)` returns `<root>/r1/events.jsonl` and `os.listdir` is never called (counted). Any other value (`1`, `` , `off`) keeps the guard on.
- **AC6** Degrade: when `os.listdir` raises `PermissionError`, or `os.lstat` raises an `OSError` other than not-found, the call returns the path (no raise, no output on stderr or stdout). A root that does not exist returns the path.
- **AC7** Callers degrade, not fail. Real-filesystem form (skipped where the filesystem does not fold case): `run.py --workflow echo --neutral --run-id r261` in a subprocess, `HAL_RUN_LOG_ROOT` holding a directory `R261/` with one event line: exit 0, stderr contains `E_RUN_ID_CASE_COLLISION`, and `R261/events.jsonl` is byte-identical afterwards. Sim form (runs everywhere): the bd#203 helper in `semantic_verifier.py` called with a session id `r261` against the same layout falls back to the default log and logs one warning naming the code.
- **AC8** Both billing modes: AC1's outcome is identical with `ANTHROPIC_API_KEY` set and with it removed from the environment; the check never reads it.
- **AC9** Resumed run, own directory: with `R1/events.jsonl` present, a second `run_scoped_log_path("R1", root)` returns it (the existing bd#93/#203 flow: `run.py` then the verifier helper for the same id both resolve and both succeed).
- **AC10** (GUARD) `event_log.py` parsed with `ast`: `RunIdCaseCollision` subclasses `ValueError`; `os.environ`, `getenv`, `mkdir`, `makedirs`, `open`, `write_text` are not referenced inside `run_scoped_log_path` or `_on_disk_spelling`; the bd#260 no-deletion guard names (`rmtree`, `unlink`, `rmdir`, `remove`, `removedirs`, `rename`, `replace`, `truncate`, `move`, `system`, `Popen`) still appear nowhere in the module (the new code must not use `str.replace`).
- **AC11** (GUARD) `config_provider.run_id_case_guard_enabled` exists, returns `True` with the variable unset and `False` for `0`; a provider object without the method yields `True` from the free function; `flags_catalog.FLAGS["HAL_RUN_ID_CASE_GUARD"]` has kind `gate`, a non-blank `owner`, a `provenance` starting with `introduced:`, and a `retire-by:` date within 45 days of 2026-10-10; `scripts/flag_owner_lint.py` and `scripts/flip_horizon.py --check --today 2026-10-10` exit 0; `docs/configuration.md` mentions the variable; CHANGELOG `[Unreleased]` mentions it in `Added` (helper `from helpers.changelog import read_changelog, require_entry`, as in bd#260).
- **AC12** (GUARD) The tree-scan lint `check()` on the real package tree reports no unclassified site and no stale entry, and `event_log.py::_on_disk_spelling::listdir#0` is in the inventory with class `not-a-gate` and a non-blank note. `tree_scan_lint.py` is not modified (no diff against the base).
- **AC13** (GUARD) The sibling suites in `sibling_tests` stay green unmodified.

## 5. op <-> AC map

op1: AC1-AC4, AC9 · op2: AC1, AC7, AC10 · op3: AC6, AC8 · op4: AC5, AC11 · op5: AC11 · op6: AC12. Existing tests: AC13.

## 6. Adversarial edges

1. The same id resolved twice by two callers of one run (`run.py`, then the semantic-verifier helper) must never refuse itself: exact spelling present means "mine" (AC9).
2. A case-sensitive filesystem that legitimately holds `R1` and `r1` must not start refusing either (AC4); the guard targets aliasing, not spelling.
3. A permission error or a vanished entry must not turn a log-path lookup into a crash (AC6; callers also already degrade on `ValueError`, AC7).
4. The switch must restore the old behaviour exactly and cost nothing: no listing at all (AC5).
5. The check must not grow into a second source of truth for what a valid id is: it runs after the existing validation and adds one refusal, nothing else (AC10, AC13).
6. A new directory listing must not weaken the bd#94 lint: it is classified, the lint file is untouched (AC12).
