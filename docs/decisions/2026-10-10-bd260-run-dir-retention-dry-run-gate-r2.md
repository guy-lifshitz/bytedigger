# bd#260 gate review r2: run directory retention, dry-run first release

Scope: spec `2026-10-10-bd260-run-dir-retention-dry-run.md` (DRAFT r2) and RED file `engine_py/tests/test_bd260_run_retention_dry_run.py` (pre-GREEN: 32 failing, sibling suites green per preflight).

## 1. Round-1 findings, re-verified

1. MAJOR (call site not pinned): RESOLVED. Spec op4 now states that `run_id` must be passed, and adds AC8b. The RED test `test_ac8b_cli_resumed_run_is_not_a_candidate` pre-creates `<root>/R260b` (120 days old) plus `other` (120 days old), runs the real CLI with `--run-id R260b`, and asserts exit 0, a summary counting exactly 1 directory, `other` listed, `R260b` not listed, both directories still present. A call site that omits `run_id` lists `R260b` and the count becomes 2, so the test fails. The existing directory is accepted by the run (`mkdir(parents=True, exist_ok=True)` at run.py L347).
2. MINOR (missing root): RESOLVED. Spec op2/op3/AC6 say a missing root gives `[]` and no output; `test_ac6_degrades_never_raises[missing_root]` asserts empty stderr, and the other four cases assert exactly one `run retention skipped` line and nothing else.
3. MINOR (OSError per entry): RESOLVED. Spec op3 says an entry failure is skipped alone and the scan continues; AC4 `dangling_child` pins that a directory with a dangling-symlink child is still examined and listed. The vanishing-entry case has no test (advisory, see edges).
4. MINOR (future clock): RESOLVED. `test_ac3_future_mtime_is_not_a_candidate`.
5. MINOR (AC11 blind spots): RESOLVED. The guard now also forbids `move`, `system`, `Popen`, and imports of `shutil` / `subprocess`, module-wide.
6. MINOR (flag_owner_lint vacuous for kind int): accepted as before; the direct assertions in `test_ac12_flag_catalogued` carry it.
7. MINOR (determinism): no action needed.

## 2. Audit

**Step 1, literal-token drift.** `HAL_RUN_LOG_RETENTION_DAYS`, `run_retention_dry_run`, `run_log_retention_days`, the summary string and the `run retention skipped` string are identical in spec and RED. Cosmetic: the RED docstring still says "r1"; no effect.

**Step 1.5, rule overlap.** The five candidate rules were simulated in order for each fixture: symlinks stop at rule 1; `.hidden`, `a b` at rule 2; the starting run (`open1`, `R260b`) at rule 3; the exact-threshold and future-dated directories at rule 4; the old directories pass all rules. Each lands on the intended rule. The relative-root and file-root cases are stopped by the whole-routine checks before any entry rule.

**Step 2, section 3 vs section 4.** Every producing path (age setting, candidate filter, report and degrade, call site, switch and docs) has an AC, and each AC has a producing path. The `run_id` pass-through, the gap of round 1, is now covered by AC8b.

**Step 3, RED adequacy.** Real directories and files under a temporary path, backdated with `os.utime`; the unit is called directly and through the real CLI in a subprocess; no double of the unit under test. New names are only touched inside test bodies, so failure is at assert time. Tests that a stub returning `[]` could pass (zero setting, missing/file/relative root, silent report, CLI zero, `--list`) start with a positive control that fails pre-GREEN. Wrong-GREEN probing: deleting (AC1, AC4, AC8, AC9, AC11), following symlinks (AC4), listing the starting run (AC1, AC8b), ignoring `0` (AC5), raising on bad settings (AC6, AC9), `>=` at the threshold (AC3), own-mtime-only age (AC2), `abs()` on a future time (AC3), stdout instead of stderr or wrong count under the cap (AC7), calling for `--list` (AC8), omitting `run_id` (AC8b). All are caught.

**Step 4, reachability.** Point: listing and report in `run_retention_dry_run` (`event_log.py`); host: that function, called from `main()` in `run.py` right after the run id is resolved (L335); test path: AC1-AC7, AC10 directly, AC8, AC8b, AC9 through the CLI. The setting is read through `config_provider.run_log_retention_days`, reached from the same function.

## 3. Adversarial edges (not covered by an AC)

1. An entry that vanishes between listing and stat: the spec says skip, no test pins it (needs a race; advisory).
2. A run directory whose own mtime is old and whose only recent entry is a deep file (not a direct child): spec says direct children only, no test; a recursive GREEN would pass the RED. Advisory for the deletion release.
3. A directory with no children (age is its own mtime) and a directory with a future-dated child but an old own mtime: not tested.
4. Case-only difference from the starting id on a case-insensitive filesystem: declared out of scope (#261); the deletion follow-up must inherit it.

None of these blocks this release, which deletes nothing.

## 4. Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC8b: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS

## 5. Findings

No MAJOR findings. The round-1 MAJOR is resolved. MINOR (advisory): the RED docstring says "r1" (should say r2); the three edges above remain untested.

Anchors: sha256 values are not written by this audit (no way to compute them in this role); they are to be stamped after the file is written.

VERDICT: APPROVE

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd260-run-dir-retention-dry-run.md sha256:2eb29bd8c3912397c8c641954b64ba94e72da8a964f43336f1b1ffa03a5be115
red: engine_py/tests/test_bd260_run_retention_dry_run.py sha256:739ebf725a198184281ad3b01daa67a88aba52d02125a5107c0a59086cf5cc12
-->
