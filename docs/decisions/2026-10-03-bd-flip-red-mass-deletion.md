# bd: HAL_RED_MASS_DELETION_ENFORCE defaults ON (kill-switch kept)

**Status:** r2 spec (folds gate r1, see ...-gate-r1.md) · **Class:** SYSTEMATIC · **Chokepoint:** the one read site,
`_commit_red_tests` in `workflows/phase_5_implement.py` (`_mdl_enforce`), and the one catalog entry.
**Enforcement layer (Principle C):** deterministic gate in the engine (`E_RED_MASS_DELETION`,
recoverable=False) plus the horizon guard from bd#221 (the overdue `flip-by` token and its ledger line
are removed together; `scripts/flip_horizon.py --check` stays green). **Adds no LLM call.**
**Depends on:** bd#221 (guard + ledger). **Source:** wave-2 principle 5; hal#1892.

**Introduced because (provenance):** hal#282 (OFI 2726ed01, 2026-05-22): a RED agent deleted 2407 lines
(77%) of an out-of-scope test file; `commit_red_tests` committed it across two cycles and it was
auto-pushed to origin/main before GREEN ran. The gate (kill-switch `HAL_RED_MASS_DELETION_GATE`, pragma
escape `# red-mass-deletion: allow`, threshold `HAL_RED_MASS_DELETION_MAX_LINES`=120) shipped warn-only
with `flip-by:2026-07-24`. **Protected against:** a real incident. **Why now / what changes:** the horizon
passed 71 days ago and the evidence is green: 232 runs of `red_mass_deletion_check` in 805 host build
runs (events through 2026-10-01), 0 violations, so 0 false positives. Nothing is removed: the flag becomes
a kill-switch (`=0` restores warn-only), the pragma escape and the threshold stay.

## §1 Decision

1. `flags_catalog.FLAGS["HAL_RED_MASS_DELETION_ENFORCE"]` becomes `kind: "gate"`, `default: "1"`. Description:
   "Kill-switch: =0 returns RED mass-deletion to warn-only; default ON hard-blocks (E_RED_MASS_DELETION,
   recoverable=False). Flipped ON 2026-10-03 after 232 clean shadow runs, Refs #282." It must contain no
   `flip-by|kill-by|retire-by` token.
2. In `_commit_red_tests` the read becomes `get_config().gate_enabled("HAL_RED_MASS_DELETION_ENFORCE")`
   (enabled unless exactly `"0"`). The stale `flip-by:2026-07-24` comment lines at the read site are
   rewritten without a token. No other behavior changes.
3. `scripts/flip_horizon_ledger.json`: the `HAL_RED_MASS_DELETION_ENFORCE` line STAYS through spec and RED (so the base guard test stays green) and is deleted in GREEN together with the catalog token.
4. `authorized-test-edits:` `engine_py/tests/test_gh282_red_mass_deletion.py`,
   `engine_py/tests/test_gh1600_red_tests_in_existing_file.py` (siblings that pinned default-OFF, §1a).

## §2 Acceptance checks (real `_commit_red_tests` on a tmp git repo, as the sibling tests do)

- **AC1** catalog entry: kind `gate`, default `"1"`, description names the `=0` kill-switch, contains no horizon token.
- **AC2 (production side-effect)** env unset, RED deletes 200 of 300 lines of a pre-existing test file, no pragma → status `error`, `error_code == "E_RED_MASS_DELETION"`, `recoverable is False`, and the event `red_mass_deletion_blocked` is emitted.
- **AC3** `HAL_RED_MASS_DELETION_ENFORCE=0` with the same fixture → no `E_RED_MASS_DELETION`, `red_mass_deletion_check` emitted with `enforced: false` and `violations_n >= 1` (warn-only restored).
- **AC4** env unset, same deletion but the post-RED file carries `# red-mass-deletion: allow` → not blocked by this gate (pragma escape unchanged).
- **AC5** env unset, deletion below the threshold (e.g. 10 lines) → not blocked (no false positive), event shows `enforced: true`, `violations_n == 0`.
- **AC6** `HAL_RED_MASS_DELETION_GATE=0` with the AC2 fixture → gate fully off, no check event.
- **AC7** `flip_horizon.check(FLAGS, ledger, today)` reports no problem line naming `HAL_RED_MASS_DELETION_ENFORCE`, and the key is absent from `scripts/flip_horizon_ledger.json` (asserted on the key and problem lines, not the whole-guard exit code, which goes red from 2026-10-18 because of the sibling flag's entry). Also asserts the `_commit_red_tests` source text contains no `flip-by:`.
- **AC9** `HAL_RED_MASS_DELETION_ENFORCE="false"` with the AC2 fixture still enforces (enforced true, `E_RED_MASS_DELETION`).
- **AC10** the alias `BD_RED_MASS_DELETION_ENFORCE=0` restores warn-only (same observable as AC3).
- **AC8 (sibling migration)** in the two authorized files the tests that relied on default-OFF now set `HAL_RED_MASS_DELETION_ENFORCE=0` explicitly (gh282 AC6; gh1600 AC8d and AC10, whose discriminating shape needs GH282 not to fire first), and the catalog-kind assertion expects `"gate"`; every other assertion is unchanged.

## §3 Edge cases

Sibling sweep (§1a, done): grep over engine_py/scripts/docs plus a scoped run with the flip applied of 10 other `_commit_red_tests` callers and 36 flag/rollout test files found no other reliance on default-OFF; `test_gh282_pragma_escape.py` sets `=1` explicitly (unaffected). ERROR_CODES.md text does not mention ENFORCE. Release note / PR body mention the `=0` kill-switch.

`ENFORCE=1` still enforces (gate_enabled is true for any value but `"0"`). A value like `"false"` is enabled — same contract as every other kill-switch gate in the catalog.

## §4 Files in scope / NOT in scope

In scope: `flags_catalog.py` (one entry), `workflows/phase_5_implement.py` (one read + comment), the ledger line,
the two authorized sibling test files, one new test file `engine_py/tests/test_bd_flip_red_mass_deletion.py`.
**NOT in scope:** `HAL_RED_COLLECT_PROBE_ENFORCE` (next PR), any other flag, `_red_mass_deletion_violations`,
the threshold, the pragma, the host repo.
