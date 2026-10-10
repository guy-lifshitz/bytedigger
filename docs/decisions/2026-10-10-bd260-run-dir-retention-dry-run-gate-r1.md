# bd#260 gate review r1: run directory retention, dry-run first release

Scope: spec `2026-10-10-bd260-run-dir-retention-dry-run.md` (DRAFT r1) and RED file `engine_py/tests/test_bd260_run_retention_dry_run.py` (29 tests, 0 passed / 29 failed pre-GREEN; the bd#93 and bd#203 sibling suites pass 46/16 on the base).

## 1. Audit

**Spec facts against the code.** All hold on `48dd7de`.
- `event_log.py` defines `_RUN_ID_RE = ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` and `run_scoped_log_path(run_id, root=None)`, which takes its default root from `config_provider.run_log_root()`; the new routine can reuse the pattern and sit next to it.
- `config_provider.run_log_root()` is a free function with a provider-method-or-default fallback; `int_value(env_var, default)` exists both as a provider method and as a free function and lets a non-integer raise `ValueError`. The spec's "bad setting is an error handled by op3" is therefore a real code path: the routine must catch the `ValueError` itself.
- `run.py` L335 computes `_resolved_run_id = args.run_id or uuid...` before the `--list` branch (L356) and before the run directory is created (L347). So the spec's call site (right after L335, guarded by `args.workflow` and not `--list`) runs before `<root>/<run_id>/` exists for a fresh id, and the id can name an already existing (resumed) directory.
- `event_log.py` today contains no `rmtree`, `unlink`, `rmdir`, `remove`, `rename`, `replace` or `truncate` as a name or attribute. The only textual hits are in a docstring/comment (`worktree remove --force`, `tmp+rename dance`), which an `ast` walk ignores. AC11 is therefore green on the base apart from the missing function, and the guard is valid.
- `flags_catalog.py`: `HAL_RUN_LOG_ROOT` is the neighbouring entry. `docs/configuration.md` has the run log root row (L313) the new row goes next to.
- `scripts/flip_horizon.py`: only tokens whose date is before `--today` need a ledger entry; the ledger limit of 45 days applies to ledger entries, not to the token. `flip-by:2026-11-10` is 31 days after 2026-10-10, so with `--today 2026-10-10` the lint exits 0 with no ledger entry. After 2026-11-10 the unfixed lint (real clock) will report `UNCOVERED` until deletion lands or a ledger entry is added; that is the intended forcing function. AC12 pins the date window against the fixed day 2026-10-10, so the test itself does not rot.
- `scripts/flag_owner_lint.py`: the rollout set requires `kind` in (`flag`, `gate`). The new flag is kind `int`, so the lint does not examine it and exits 0 regardless. Owner and provenance are checked only by the RED test itself (`test_ac12_flag_catalogued`), which is sufficient. AC12's wording "flag_owner_lint exits 0" is true but vacuous for this entry; harmless.
- No literal-token drift: `HAL_RUN_LOG_RETENTION_DAYS`, `run_retention_dry_run`, `run_log_retention_days`, the two report strings and the `skipped` string are identical in spec and RED. (Op2 signature is `run_retention_dry_run`; the heading of section 3 says `run_retention_dry_run` too.)

**Rule overlap (candidate filter).** Simulated the five rules in order for each AC fixture. Symlink-to-old-dir: rule 1 rejects (not a real directory) before age. `.hidden` and `a b`: rule 2 rejects. `open1`: rules 1-2 pass, rule 3 rejects by name. Exact-threshold directory: rules 1-3 pass, rule 4 rejects (age not strictly greater). Each AC lands on the intended rule; no fixture is rejected by an earlier rule than intended in a way that hides a later one, except the symlink cases, where the AC4 positive control (`old1`) shows the routine runs at all.

**Spec section 3 vs section 4.** Producing paths: age setting (AC5, AC6, AC12), candidate filter (AC1-AC4, AC10, AC11), report and degrade (AC5-AC7, AC9), call site (AC8, AC9), switch and docs (AC12). Every path has an AC. Exception: the part of op4 that passes `run_id=_resolved_run_id` has no AC that would fail if it were omitted (finding 1).

**RED adequacy.** Real directories and files under `tmp_path` backdated with `os.utime`; the unit is called directly with explicit `root` and `now`, and through the real `run.py` in a subprocess. No double of the unit under test, no sleeps, no network. New names are touched only inside test bodies, so failures are assert-time, not collect-time. Tests that would hold on a stub returning `[]` (the AC5 zero case, the AC6 missing / file / relative root cases, AC7 silent case, AC8 zero and `--list`) each begin with a positive control or assert a populated result, so they fail pre-GREEN for the right reason and cannot be passed by a stub. Wrong-GREEN probing:
- GREEN that deletes: AC1, AC4 (`_tree` before and after, including symlink flags), AC8, AC9 and AC11 all catch it.
- GREEN that follows symlinks (`is_dir()` instead of `is_dir()` plus not `is_symlink()`): AC4 fails because `lnk` would be listed.
- GREEN that lists the starting run: AC1 (`open1`, 200 days old) fails.
- GREEN that ignores `0`: AC5 fails (both the HAL_ and BD_ spelling).
- GREEN that raises on `abc` or `-5`: AC6 fails; one that stays silent fails the exactly-one-`skipped`-line check.
- GREEN that uses `>=` at the threshold: AC3 fails; one that reads only the directory's own mtime: AC2 fails.
- GREEN that prints to stdout, or prints the capped count instead of the total: AC7 fails.
- GREEN that calls the routine for `--list`, or ignores `0` in the CLI: AC8 fails.
- GREEN that omits `run_id` at the call site: nothing fails (finding 1).

**Reachability.** Point (prod line) to host to test path: the report and the listing are produced in `run_retention_dry_run` (new, `event_log.py`), reached directly by AC1-AC7 and AC10 and through `run.py` `main()` at the line after L335 by AC8 and AC9. The setting read is reached via `config_provider.run_log_retention_days` from the same function.

**Dry-run scope.** AC11 walks the whole module AST, so a deletion anywhere in `event_log.py` is caught, and AC1/AC4/AC8/AC9 additionally compare the on-disk tree before and after through the real path. Deletion in some other module is covered only by AC8/AC9's "all three directories still exist" (and run.py, config_provider are touched by the spec). Adequate for this release; see finding 5 for the guard's blind spots.

**"Open run never a candidate".** Pinned by name at unit level (AC1) and by recent modification (AC2). Not pinned at the call site (finding 1).

## 2. Adversarial edges (not covered by an AC)

1. Resumed run through the CLI: `--run-id X` where `<root>/X` already exists and is older than the threshold. The call site must pass `run_id`; AC8 only uses a fresh id, so omitting the argument is undetected. This is the one that matters for the deletion release.
2. Future-dated entry (clock skew, `now` earlier than the newest mtime). Spec adversarial edge 3 and op2 rule 4 promise it is never listed, but no test sets it; a GREEN that uses `abs(now - mtime)` passes the whole RED.
3. A dangling symlink or an unreadable file as a direct child of an otherwise old run directory ("read without following symlinks", "OSError on an entry"). Nothing pins whether the entry is skipped, the directory is kept, or the whole scan aborts with partial results.
4. An entry that vanishes between listing and stat (a concurrent run or cleaner). Same question: skip the entry or abort the scan.
5. A run directory with no children (age is its own mtime), and a deep file newer than every direct child (spec says direct children only; a recursive GREEN passes the RED).
6. A candidate that is old on a case-insensitive filesystem but differs from the starting id only by case (#261, declared out of scope; noted so that the follow-up deletion PR inherits it).
7. Missing root: op2 says empty list, op3 says an unreadable root prints `run retention skipped`. For a first-ever run (root does not exist yet) it should be silent, but the spec does not say which, and AC6 asserts neither.
8. `--run-id` of an invalid shape (e.g. `a/b`) reaching the routine: harmless for the name comparison, untested.

## 3. Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: FAIL
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS

## 4. Findings

1. MAJOR, op4 and AC8: the rule "the starting run is never a candidate" is only pinned on the unit (`run_id="open1"`). At the call site, `run_retention_dry_run(run_id=_resolved_run_id)` can be reduced to `run_retention_dry_run()` and every RED test still passes, because `test_ac8_*` uses the fresh id `R260`, which does not exist yet when the routine runs (the call precedes the `mkdir` at L347) and so can never be a candidate. A resumed run whose directory is older than the threshold would then be listed as removable, which is exactly the defect the deletion release must not inherit. Fix: add a CLI test with a pre-existing, backdated `<root>/<id>` directory (leave it free of event-log content that would confuse the echo workflow, or use an empty directory) passed as `--run-id`, asserting that it is absent from the stderr list and that the count excludes it, while another old directory is still reported. Make the spec's AC8 say so.
2. MINOR, spec op2/op3 and AC6: missing root is described both as "gives an empty list" and as one case of "unreadable root" that prints `run retention skipped`. Decide: a missing root should be silent (normal on a first run); AC6 should assert it (no `run retention` text at all) so a GREEN cannot print noise on every first run. Today the test accepts either.
3. MINOR, spec op3 "an OSError on an entry": state whether the entry is skipped (preferred, so one bad entry does not hide the others and a concurrent removal does not abort the scan) or the scan aborts with what was found. Add one test (a dangling symlink child, or an unreadable child) once decided (edges 3 and 4).
4. MINOR, spec section 6 edge 3 claims a future clock is covered by AC3/op2 rule 4; no test exercises it. Add a candidate-shaped directory whose newest time is after `now` and assert it is not listed (edge 2).
5. MINOR, AC11 blind spots: the guard looks for the eight names only. `shutil.move`, `os.system("rm ...")`, `subprocess`, `send2trash`, or opening a file for writing would not trip it. AC8/AC9 still compare the tree through the real path, so this is advisory; consider also forbidding `shutil`, `subprocess` and `os.system` references in the routine's body. Note the guard also forbids any `str.replace` in `event_log.py`, so GREEN must not use it in the new function.
6. MINOR, AC12: `flag_owner_lint.py` skips `kind: "int"` entries, so its exit 0 says nothing about this flag; the direct owner/provenance assertions in `test_ac12_flag_catalogued` carry the weight, which is fine. After 2026-11-10 the repository's un-dated flip-horizon check will fail for this flag until the deletion PR lands or a time-boxed ledger entry is added; that is intended, and the 45-day window in the test is evaluated against the fixed day, so the test will not rot.
7. MINOR, test determinism: `_cli_root` ages directories against `time.time()` (120 days against a 90-day threshold, 1 day for fresh), so there is no flakiness from the clock; the unit tests use a fixed `NOW`. No action.

One MAJOR finding (the call site is not pinned). GREEN as specced would otherwise make the 29 tests pass and leave the bd#93 and bd#203 suites green.

VERDICT: REJECT

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd260-run-dir-retention-dry-run.md sha256:793fd4b94d316485bb423b3d7b02384e781d6517fa1439c983341ad4c7348036
red: engine_py/tests/test_bd260_run_retention_dry_run.py sha256:a822693f0727c9faecaa47d254e752e6336e6c1e0e9bda2de3bed2d5c5682c9f
-->
