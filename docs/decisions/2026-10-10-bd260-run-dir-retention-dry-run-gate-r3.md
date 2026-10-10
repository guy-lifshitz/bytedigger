# bd#260 gate review r3: tree-scan inventory delta (commit 8bec2d5)

Scope: only the delta since the r2-approved head (`2ea3a98`): two entries in `engine_py/bytedigger_engine/conformance/tree_scan_inventory.json` and the spec amendment (section 7, front matter). The r2 verdict on the spec and RED file is not reopened. Method: read the lint, the inventory, the two functions and the call site; no command was run, so "nothing else in the inventory touched" is judged by reading, not by diff.

## (a) Is `not-a-gate` the truthful class?

Yes. `tree_scan_lint.py` defines exactly two classes (`VALID_CLASSES`, L57): `filters` (the enclosing scope references a member of `FILTER_NAMES`) and `not-a-gate` ("the read set never feeds a gate, a commit or a hash", L10).

- `filters` does not fit and would be wrong: neither function references a filter name, so `check()` would report "filters without filter call" (L360-364). There is no third class.
- `_newest_mtime` (`event_log.py:104-112`) returns a float used only for an age comparison at L157.
- `run_retention_dry_run` (L115-173) uses the listing to build `found`, writes a capped stderr report, and returns the list. The only caller (`run.py:337`) discards the return value. Nothing is staged, committed, hashed, deleted, or used to pass or fail the run. The module has no deletion call (guarded by AC11).
- Neither function is a `git add` site, so the "git-add must filter" rule (L365-369) does not apply.

## (b) Re-evaluation when deletion lands

Both notes say it in plain words ("REVISIT this class when real deletion lands (the follow-up to bd#260)"); the second adds "a deleting scan is a gate". The spec amendment says the same.

Honest assessment of enforcement: the revisit is mostly a prose promise.
- The lint key is `file::function::kind#ordinal`. Adding `shutil.rmtree` (or any deletion) inside these functions leaves both keys unchanged, so the lint will not fail and will not ask for a new class.
- The real tripwire is AC11 (module-wide AST guard against `rmtree`, `unlink`, `rmdir`, `remove`, `rename`, `shutil`, ...). The deletion PR cannot pass CI without editing or replacing that guard, which forces a reviewer to open the spec; the spec's section 7 and the inventory notes are what that reviewer will find. Also the `flip-by:2026-11-10` flag date is checked by `flip_horizon.py`. So it is discoverable, but not mechanically tied to the inventory class.

## (c) Keys

- `event_log.py` has exactly two tree-scan calls: `directory.iterdir()` at L107 (scope `_newest_mtime`) and `base.iterdir()` at L148 (scope `run_retention_dry_run`). Each is the first and only `iterdir` in its scope, so `#0` is correct for both. `sorted(base.iterdir(), ...)` is one call.
- The relpath is `event_log.py` because the lint scans from the package root and the file sits directly in it; this matches existing top-level keys (`flags_catalog.py::...`).
- No stale key: both keys have a matching site. No over-broad key: nothing else in `event_log.py` matches a tree-scan kind (`rglob|glob|iterdir|os.walk|os.scandir|os.listdir` grep: only those two lines).
- The neighbouring entries visible at L23-28 and L1-22 look unchanged. Not verified by diff.

## (d) Spec amendment

- Front matter: `paths` gains the inventory file; `sibling_tests` gains bd94, bd89 and bd243 suites. Consistent with the CI failures and section 7.
- AC1-AC13 (including AC8b) are all still present with the same ids and wording scope as described in the r2 report; section 7 states "No RED test changes". No AC is removed or weakened.
- Status line reads DRAFT r3, fine.

## (e) Adversarial edges (only where they affect the delta or the class)

- Symlinked run dir: rejected by the `lstat` check at L153 before `_newest_mtime` is reached, so the second `iterdir` never runs on a symlink target. Non-directory entries: same rule.
- Unreadable directory or one that vanishes: `os.lstat`/`iterdir` raise `OSError` in `_newest_mtime`, caught by the per-entry `except OSError` (L159); the entry is skipped. An unreadable or failing root `iterdir` is caught by the outer `except Exception` (L161) and gives one `run retention skipped` line. Both stay report-only and non-fatal. The classification holds.
- Not blocking: between the `lstat` at L153 and `_newest_mtime`'s `iterdir`, a directory could be swapped for a symlink; today this only reads child mtimes (report-only). The deletion release must re-check with `lstat` immediately before acting. Advisory.

## Findings

1. MINOR (`event_log.py:107`, `:148`; inventory L26-27): the "revisit when deletion lands" is a prose note, not enforced by the lint (keys do not change when a function starts deleting). Mitigated by AC11 and the flip-by date; the deletion PR should explicitly re-review these two entries.
2. MINOR (spec AC13): AC13 still names only the bd#93 and bd#203 suites although `sibling_tests` now lists five. Not a weakening, but the guard wording could be widened to "all suites in `sibling_tests`".
3. MINOR (advisory, `event_log.py:153` vs `:107`): swap-to-symlink race between the type check and the child listing; read-only today, must be closed before deletion.

No MAJOR findings. The class is the only truthful one available, the keys match what the lint computes, the spec change is consistent, and the listing paths stay report-only and non-fatal.

VERDICT: APPROVE

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd260-run-dir-retention-dry-run.md sha256:dff8302392cb6e713a7fca49cb8b417b536015b617492f728044cd91931f742d
red: engine_py/tests/test_bd260_run_retention_dry_run.py sha256:739ebf725a198184281ad3b01daa67a88aba52d02125a5107c0a59086cf5cc12
-->
