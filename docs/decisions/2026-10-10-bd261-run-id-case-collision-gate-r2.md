# bd#261 gate review r2: spec and RED for the run id case collision guard

Scope: spec `docs/decisions/2026-10-10-bd261-run-id-case-collision.md` and RED `engine_py/tests/test_bd261_run_id_case_collision.py` on `27bce6b`. Method: read spec, RED, r1 report; re-checked the tree-scan key convention against `tree_scan_lint.py` and the inventory. No command run by me.

## r1 findings: closed or not

1. Tree-scan key (MAJOR): CLOSED. Spec op6/AC12 and RED `KEY` now say `event_log.py::_on_disk_spelling::os.listdir#0`; `os.listdir` is in `_RESOLVED_KINDS` (L39) and the inventory uses the dotted form (`error_codes.py::harvest_codes::os.walk#0`).
2. Vanished entry (MAJOR): spec closed (op1 branch (f), AC3, new test `test_ac3_listing_without_match_returns_path_sim`), BUT the new test is itself broken, see new finding 1.
3. tree_scan_lint diff (MINOR): CLOSED, stated as orchestrator check in AC12.
4. No listing when id absent (MINOR): CLOSED in `test_ac3_no_false_positives_sim` (listdir_calls cleared, then asserted empty after the absent-id loop). Real-FS twin does not count, acceptable.
5. Non-directory entry (MINOR): CLOSED, op1 says any entry counts.
6. AC8 weakness (MINOR): CLOSED by rewording to smoke, AC10 is the structural proof.

## Step 1: spec-internal consistency

Tokens agree across spec and RED: `HAL_RUN_ID_CASE_GUARD`, `E_RUN_ID_CASE_COLLISION`, `RunIdCaseCollision`, `_on_disk_spelling`, `run_id_case_guard_enabled`, key `os.listdir#0`. Spec header still says "DRAFT r1" (cosmetic). No drift.

## Step 1.5: rule-overlap simulation

op1 branches (a) lstat not-found, (b) lstat other OSError, (c) listing fails, (d) exact entry, (e) fold entry, (f) neither. Order exact-before-fold pinned by AC4 sim (`R1`,`r1` both listed). (a) AC3/AC6, (b) AC6, (c) AC6, (d) AC9/AC4, (e) AC1/2/5/7/8, (f) AC3 new test (but see finding 1). Switch-off precedes everything (AC5).

## Step 2: spec vs RED cross-check

Every op has an AC; every error/degrade branch has a producing path. Section 6 claims now match tests, except the (f) test cannot pass (finding 1).

## Step 3: RED adequacy

Assert-time failure, positive controls, shim fidelity as in r1 (shim replaces only `event_log.os`, does not decide outcomes). One defect:

- `test_ac3_listing_without_match_returns_path_sim`: it calls `_guard_is_live(sim, tmp_path)`, which registers `ctl-root` and runs `_refused("r1", ctl)`; that listing is appended to `sim.listdir_calls` (the shim records every registered-root listing). The test then asserts `sim.listdir_calls == [root]`, but after a correct GREEN the list is `[ctl-root, root]`. The assertion fails against a correct implementation, so GREEN can never satisfy it. Fix: `sim.listdir_calls.clear()` after `_guard_is_live` (as `test_ac3_no_false_positives_sim` already does before its count), or assert `sim.listdir_calls[-1:] == [root]` / `root in ...`.

Wrong-GREEN check: raising on branch (f) is now caught (once the assertion is fixed); the others as in r1.

## Step 4: reachability

Unchanged from r1: Point `event_log.py::run_scoped_log_path` -> Hosts `run.py` (`except Exception`) and `semantic_verifier._run_event_log` (`except ValueError`) -> tests AC7 real/sim and direct unit calls. Reachable.

## Adversarial edges (not already an AC)

1. Non-ASCII entry whose `str.lower()` equals the id (Kelvin sign U+212A vs `k`): on APFS it folds, refusing is right; on case-sensitive FS lstat for the id fails first. One-line spec note advisable, not blocking.
2. Dangling symlink named like the id: `lstat` (not `stat`) sees it, so it counts as present; a GREEN using `Path.exists` would differ. Spec says "any entry counts"; no test pins the symlink or file kind (sim cannot distinguish). Advisory.
3. Two entries in the listing differing from the id only by case but neither exact (`R1`, `r1` listed, id `R1` handled by (d); id `rR`-style cases not relevant). With listing order dependence on a folding FS this cannot occur; on exact FS lstat of the missing spelling fails first. No gap.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: FAIL
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS

## Findings

1. MAJOR (RED `test_ac3_listing_without_match_returns_path_sim`, final assert): `sim.listdir_calls == [root]` is polluted by the `_guard_is_live` control listing of `ctl-root`; the test fails on a correct GREEN. Add `sim.listdir_calls.clear()` after `_guard_is_live(...)` before the call under test (or loosen the assertion).
2. MINOR (spec header): still labelled "DRAFT r1"; update on freeze.
3. MINOR (edge 2): symlink/file entry kind is stated in spec but unpinned by a test; advisory.

VERDICT: REJECTED
