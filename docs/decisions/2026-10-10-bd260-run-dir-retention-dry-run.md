---
red_tests: [engine_py/tests/test_bd260_run_retention_dry_run.py]
sibling_tests: [engine_py/tests/test_bd93_run_scoped_event_log.py, engine_py/tests/test_bd203_semantic_verifier_run_log.py, engine_py/tests/test_bd94_engine_owned_paths.py, engine_py/tests/test_bd89_p3c_deterministic_synthesize_report.py, engine_py/tests/test_bd243_green_entry_guard.py]
paths: [engine_py/bytedigger_engine/event_log.py, engine_py/bytedigger_engine/config_provider.py, engine_py/bytedigger_engine/run.py, engine_py/bytedigger_engine/flags_catalog.py, docs/configuration.md, CHANGELOG.md, engine_py/bytedigger_engine/conformance/tree_scan_inventory.json]
---
# bd#260: run directory retention, first release is a dry-run

**Status: DRAFT r3** · **Tier:** 2 (engine `.py`: spec, RED, gate, GREEN) · **Class:** BUG (follow-up of bd#93 / #203)
**Base:** `origin/main` `48dd7de`.
**Chokepoint:** one function `run_retention_dry_run(...)` in `engine_py/bytedigger_engine/event_log.py`, next to `run_scoped_log_path`, called from the one place in `run.py` that resolves the run id for an executing invocation. The age is read in one place, `config_provider.run_log_retention_days()`.

## 1. Problem (measured on `48dd7de`)

- Every executing run creates `<run_log_root>/<run_id>/` (`run.py`, `_resolved_log.parent.mkdir`). Nothing in the tree removes such a directory: `event_log.py` has no deletion call, and no other module touches the root. The root grows without bound.
- Reference case: run directories older than the retention age plus one fresh, still-open run. Actual: all are kept forever. Expected (final state, a later release): the old ones are removed on the next run start; the open one and anything newer than the threshold stay.

## 2. Scope of this release

This PR is the **dry-run only**: the routine finds what it would remove and reports it. It deletes nothing, ever (no `rmtree`, `unlink`, `rmdir`, `remove`, `rename`). Turning deletion on is a separate follow-up PR. Run id case collision (#261) is out of scope.

## 3. Design

- **op1, age setting.** `config_provider` gets `run_log_retention_days()` (provider method plus a free function, same shape as `run_log_root`): reads the env variable `HAL_RUN_LOG_RETENTION_DAYS` (`BD_` / `BYTEDIGGER_` aliases through the existing int read), default `90` days. `0` disables the routine entirely: no directory is listed and nothing is reported. A non-integer or negative value is an error handled by op3 (the routine reports it and does nothing). A provider without the method gets the same default of 90.
- **op2, candidates.** `run_retention_dry_run(run_id=None, root=None, now=None) -> list[Path]` (`root` defaults to `run_log_root()`, `now` (epoch seconds, a float) to the current time, `run_id` is the run that is starting). It lists the entries directly under `root`; an entry is a candidate only if all of these hold:
  1. it is a real directory: not a symlink, not a regular file, not anything else;
  2. its name is a valid run id (the existing run-id pattern) and its resolved parent is the resolved `root` (nothing outside `root`);
  3. its name is not `run_id` (the starting run is open by definition);
  4. its age is strictly greater than the threshold, where age is `now` minus the newest modification time among the directory itself and its direct children (read without following symlinks). A directory whose newest time is in the future, or exactly at the threshold, or newer, is not a candidate.
  The returned list is sorted by name. A missing root gives `[]` and no output at all. A root that exists but is not a directory, or is not absolute, gives `[]` and one `run retention skipped` line. An entry that is unreadable or vanishes mid-scan is skipped alone and the scan continues (a directory with a dangling-symlink child is still examined, the child is ignored).
- **op3, report and degrade.** When the list is not empty, the routine writes to stderr one summary line `run retention (dry-run): N run directories older than D days would be removed; nothing deleted` followed by one line per candidate name (at most 20 lines, then `... and M more`). When it is empty or the setting is `0`, it writes nothing. A failure of the whole routine (bad setting, unreadable root, root not a directory or not absolute) is caught: one stderr line `run retention skipped: <reason>` and `[]`. A failure on a single entry (`OSError`) skips that entry silently and the scan goes on. It never raises, so it never stops a run. It makes no network call and no model call, so it behaves the same on a subscription and on API tokens.
- **op4, call site.** In `run.py`, right after `_resolved_run_id` is computed, for an invocation that executes a workflow (`args.workflow` set, not `--list`): `run_retention_dry_run(run_id=_resolved_run_id)`; the `run_id` argument must be passed, so a resumed run whose directory already exists and is old is not a candidate. Independent of `--event-log` and of the host override (the root can be full even when this run logs elsewhere). `--list` and `--status-run` invocations do not call it. Nothing else in `run.py` changes.
- **op5, switch and docs.** `flags_catalog.FLAGS["HAL_RUN_LOG_RETENTION_DAYS"]`: kind `int`, default `"90"`, module `event_log.py`, `owner: "guy-lifshitz"`, `provenance: "introduced: bd#260 - run directories were never removed - dry-run first release (0 disables), deletion arrives in a follow-up"`, and the description carries `flip-by:2026-11-10` (the date by which dry-run becomes real deletion). One row in `docs/configuration.md` next to the run log root row, and one bullet in the existing `### Added` block of CHANGELOG `[Unreleased]`.

Out of scope: the deletion itself, a dry-run switch separate from the age setting, run id case collision (#261), the bd#203 helper in the semantic verifier (untouched), any other consumer of the root.

## 4. Acceptance criteria. RED file: `engine_py/tests/test_bd260_run_retention_dry_run.py`

Real path: real directories and files under `tmp_path`, backdated with `os.utime`; `run_retention_dry_run` is called directly with an explicit `root` and `now`, and through the real CLI (`run.py`, subprocess, the `echo` workflow with `--neutral`, as in the bd#93 tests) for the call-site cases. No doubles of the unit under test, no network, no sleeps, no model. `HAL_RUN_LOG_RETENTION_DAYS` is set per test; the default case unsets it.

- **AC1** Root holds `old1`, `old2` (newest mtime 120 days before `now`), `fresh` (1 day), `open1` (200 days old but passed as `run_id`), with default setting: the result is exactly `[old1, old2]` as paths under root; every directory and every file still exists afterwards (same tree before and after).
- **AC2** Age comes from the newest file inside: a directory whose own mtime is 200 days old but which contains `events.jsonl` touched 1 day ago is not a candidate; the same directory with all files 200 days old is.
- **AC3** Boundary: with `HAL_RUN_LOG_RETENTION_DAYS=30`, a directory aged exactly 30 days is not a candidate, one aged 30 days plus 1 second is. A directory whose newest mtime is after `now` is not a candidate.
- **AC4** Not candidates even when old: a symlink to an old real directory, a symlink to a directory outside root, a regular file named like a run id, and a directory whose name is not a valid run id (`.hidden`, `a b`). A symlink's target outside root is untouched and not reported. A directory with a dangling-symlink child is still examined (old: listed) and does not stop the scan of the other entries.
- **AC5** `HAL_RUN_LOG_RETENTION_DAYS=0`: the result is `[]`, nothing is written to stderr, with old directories present. The alias `BD_RUN_LOG_RETENTION_DAYS=0` has the same effect.
- **AC6** Degrade, never raise: value `abc`, value `-5`, a root that is a regular file, a relative root each return `[]` and print exactly one `run retention skipped` line to stderr; a missing root returns `[]` and prints nothing to stderr.
- **AC7** Report: with two candidates, stderr has the summary line naming the count `2` and the threshold, then the two names; with 25 candidates, the output lists 20 names and `... and 5 more`; with no candidates, stderr is empty.
- **AC8** CLI, call site: a real invocation of `run.py` with `--workflow echo --neutral --run-id R260`, `HAL_RUN_LOG_ROOT` set to a root holding two 120-day-old directories and one fresh one: exit code 0, stderr contains the summary line for 2 directories, all three directories still exist, and `<root>/R260/events.jsonl` exists as before. The same invocation with `HAL_RUN_LOG_RETENTION_DAYS=0` has no `run retention` text on stderr. A `--list` invocation prints no `run retention` text.
- **AC8b** CLI, resumed run: `<root>/R260b/` pre-exists with all files 120 days old, plus one other 120-day-old directory; `--run-id R260b`: exit 0, the summary counts exactly 1 directory, names only the other one, and both directories still exist.
- **AC9** CLI, degrade: the same real invocation with `HAL_RUN_LOG_RETENTION_DAYS=abc` still exits 0 and runs the workflow, with one `run retention skipped` line on stderr.
- **AC10** Both billing modes: AC1's listing is identical with `ANTHROPIC_API_KEY` set and unset (the second run with the variable removed from the environment); the routine never reads either.
- **AC11** (GUARD) Parsing `event_log.py` with `ast`: there is no call or attribute reference named `rmtree`, `unlink`, `rmdir`, `remove`, `removedirs`, `rename`, `replace`, `truncate`, `move`, `system` or `Popen` anywhere in the module, no import of `shutil` or `subprocess`, and `run_retention_dry_run` exists in it.
- **AC12** (GUARD) `config_provider.run_log_retention_days` exists and returns `90` with the variable unset; `flags_catalog.FLAGS["HAL_RUN_LOG_RETENTION_DAYS"]` has kind `int`, a non-blank `owner`, a `provenance` starting with `introduced:`, and a `flip-by:` date within 45 days of 2026-10-10; `scripts/flag_owner_lint.py` and `scripts/flip_horizon.py --check --today 2026-10-10` exit 0; `docs/configuration.md` mentions the variable; CHANGELOG `[Unreleased]` mentions it in its `Added` block (shared helper `from helpers.changelog import read_changelog, require_entry`; no `Unreleased`+`.index(` line, no top-section pin).
- **AC13** (GUARD) The bd#93 and bd#203 suites listed in `sibling_tests` stay green unmodified.

## 5. op <-> AC map

op1: AC5, AC6, AC12 · op2: AC1-AC4, AC10, AC11 · op3: AC5-AC7, AC9 · op4: AC8, AC8b, AC9 · op5: AC12. Existing tests: AC13.

## 6. Adversarial edges

1. The run that is starting, and any run still writing, must never be listed: the starting id is excluded by name, an active run by recent modification (AC1, AC2, AC8b).
2. A symlink in the root must not lead the routine, now or in the deletion release, to a target outside the root (AC4).
3. The age boundary and a clock set into the future must not list anything (AC3, op2 rule 4).
4. A bad setting or an unreadable root must not stop a run (AC4 dangling child, AC6, AC9).
5. The routine in this release must be unable to delete: guarded on the whole module source, not on one function (AC11).
6. Many directories must not flood the terminal: output is capped (AC7).

## 7. Amendment after r2

CI caught the tree-scan lint (bd#94): the two directory listings in `event_log.py` (`_newest_mtime`, `run_retention_dry_run`) were not classified in `engine_py/bytedigger_engine/conformance/tree_scan_inventory.json`. The sibling audit had missed the bd#94, bd#89 and bd#243 suites. Change: two inventory entries, class `not-a-gate` (the scan only lists candidates for the dry-run report; it deletes and decides nothing), each noting that the class must be revisited when real deletion lands; the three suites join `sibling_tests`. No RED test changes.
