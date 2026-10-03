# Gate r1: bd flip HAL_RED_MASS_DELETION_ENFORCE (spec r1 + RED e85fdb7)

**Verdict: REJECTED** (2 MAJOR, 2 MINOR, 2 NIT, 1 advisory). Recorded by the orchestrator from the gate
agent's summary (the agent's own Write was blocked by a session hook).

- **F1 MAJOR** The spec commit removed the `HAL_RED_MASS_DELETION_ENFORCE` line from
  `scripts/flip_horizon_ledger.json` before GREEN, so `test_bd_flip_horizon_guard.py` AC7 is red at the RED
  head (flag UNCOVERED). Fix: restore the ledger line at RED; delete it in GREEN together with the catalog token.
- **F2 MAJOR** Sibling sweep (rules §1a) incomplete: other `_commit_red_tests` callers, error-code/CHANGELOG
  text and kind-vs-catalog lints were not audited. Fix: run the sweep, list hits in `authorized-test-edits:`.
- **F3 MINOR** Missing tests: `ENFORCE="false"` still enforces; `BD_RED_MASS_DELETION_ENFORCE=0` alias restores warn-only.
- **F4 MINOR** Missing source assertion that `_commit_red_tests` text has no `flip-by:` token.
- **F5 MINOR** AC7 should call `flip_horizon.check(FLAGS, ledger, today)` and assert no problem names this flag
  and the key is absent from the ledger, not the whole-guard exit code (goes red from 2026-10-18 on the sibling flag).
- **F6 advisory** Block message names no remedy (pragma / `=0`). Out of scope, follow-up.
- **F7 NIT** New test `_clean_env` must also delete `BD_`/`BYTEDIGGER_` aliases.
- **F8 NIT** Release note / PR body mention the transitional re-entry stop and the `=0` kill-switch.

Also: no deadlock risk (`recoverable=False` ends the run once); trivial-GREEN attacks are caught except ad-hoc
predicates (closed by F3).
