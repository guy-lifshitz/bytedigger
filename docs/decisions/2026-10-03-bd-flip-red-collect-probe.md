# bd: HAL_RED_COLLECT_PROBE_ENFORCE defaults ON (kill-switch kept)

**Status:** r1 spec (frozen before RED) · **Class:** SYSTEMATIC · **Chokepoint:** the two read sites of the
flag in `workflows/phase_5_implement.py` (`_collect_red_lint_findings`, `_verify_red_lint_rules_legacy`),
the one catalog entry and the one enforcement-map row (`conformance/bd_l2.py` R2.1).
**Enforcement layer (Principle C):** deterministic gate in the engine (`E_RED_COLLECT_PROBE`,
recoverable=True) plus the horizon guard from bd#221 (the overdue `flip-by` token and the ledger line are
removed together). **Adds no LLM call.** Note for the inbox: `recoverable=True` means a retry (an LLM re-write
of the RED) happens only when the RED genuinely fails to collect.
**Depends on:** bd#221 (guard + ledger), stacked after the MASS_DELETION flip (bd#223).
**Source:** wave-2 principle 5; hal#1892.

**Introduced because (provenance):** hal#542 (class §1q, D1CF5FDF): a non-collectable RED (top-level import
of a not-yet-existing GREEN symbol) hung `red_runtime` for about 30 minutes. The gate (kill-switch
`HAL_RED_COLLECT_PROBE_GATE`, `pytest --co` probe) shipped warn-only with `flip-by:2026-07-24`.
**Protected against:** a real hang. **Why now / what changes:** the horizon passed 71 days ago and the
evidence is green: 229 runs of `red_collect_probe_check` in 805 host build runs, 1 violation which was a
genuine `ModuleNotFoundError` at collection (true positive), 0 false positives. Nothing is removed: the flag
becomes a kill-switch (`=0` restores warn-only).

## §1 Decision

1. `flags_catalog.FLAGS["HAL_RED_COLLECT_PROBE_ENFORCE"]` becomes `kind: "gate"`, `default: "1"`. Description:
   "Kill-switch: =0 returns the collect-probe to warn-only; default ON hard-blocks non-collectable RED
   (E_RED_COLLECT_PROBE, recoverable=True). Flipped ON 2026-10-03 after 229 shadow runs (1 true positive,
   0 false), Refs #542." No `flip-by|kill-by|retire-by` token.
2. Both read sites become `get_config().gate_enabled("HAL_RED_COLLECT_PROBE_ENFORCE")` (enabled unless exactly
   `"0"`). The stale `flip-by:2026-07-24` comment at the legacy site is rewritten without a token.
3. `conformance/bd_l2.py` `ENFORCEMENT["R2.1"]["enforced_by_default"]` becomes `True` (the pinned measurement
   follows the catalog; `test_bd59_enforcement_map.py::test_ac6` demands exactly this decision).
4. `scripts/flip_horizon_ledger.json`: the `HAL_RED_COLLECT_PROBE_ENFORCE` line STAYS through spec and RED and
   is deleted in GREEN together with the catalog token.
5. `authorized-test-edits:` `engine_py/tests/test_gh542_collect_probe.py` (AC5 pinned default-OFF, needs
   `=0`), `engine_py/tests/test_bd59_enforcement_map.py` (AC6 R2.1 pin flips to True).

## §2 Acceptance checks (real `_verify_red_lint_rules` / legacy path on a non-collectable fixture)

- **AC1** catalog entry: kind `gate`, default `"1"`, description names the `=0` kill-switch, no horizon token.
- **AC2 (production side-effect)** env unset, non-collectable RED fixture → batch path returns status `error`
  with `error_code == "E_RED_COLLECT_PROBE"` and `recoverable is True`; event `red_collect_probe_check` has
  `enforced: true`, `violations_n >= 1`.
- **AC3** `HAL_RED_COLLECT_PROBE_ENFORCE=0` same fixture → no `E_RED_COLLECT_PROBE`, event `enforced: false`,
  `violations_n >= 1` (warn-only restored).
- **AC4** env unset, collectable RED fixture → not blocked, event `enforced: true`, `violations_n == 0`.
- **AC5** `HAL_RED_COLLECT_PROBE_GATE=0` → gate off, `gate_disabled` event, no reject (unchanged).
- **AC6** `HAL_RED_COLLECT_PROBE_ENFORCE="false"` still enforces; alias `BD_RED_COLLECT_PROBE_ENFORCE=0`
  restores warn-only.
- **AC7** `flip_horizon.check(FLAGS, ledger, date(2026,10,3))` reports no problem naming
  `HAL_RED_COLLECT_PROBE_ENFORCE`; the key is absent from the ledger JSON; the source of
  `_verify_red_lint_rules_legacy` contains no `flip-by` (that function holds only this flag's token).
- **AC8 (sibling migration)** `test_gh542_collect_probe.py` AC5 sets `=0`; `test_bd59_enforcement_map.py` AC6
  expects R2.1 `enforced_by_default is True`; every other assertion unchanged.
- **AC9** `bd_l2.ENFORCEMENT["R2.1"]["enforced_by_default"] is True` and agrees with the catalog
  (`FLAGS[flag]["default"] != "0"` or kind gate with default `"1"`).

## §3 Edge cases / sweep

Sibling sweep (§1a, done): grep over engine_py/scripts/docs plus a scoped run with the flip applied of 38
flag/rollout/collect-probe/bd_l2 test files: the only default-OFF reliance is `test_gh542` AC5; AC6 of bd59
pins the static map (§1.3). `test_gh602` (retry eligibility lists the code) and `test_gh542` AC4 (sets `=1`)
are unaffected. ERROR_CODES.md does not mention ENFORCE. `docs/decisions/2026-08-04-bd59-enforcement-map.md`
D5 ("no flag flipped by this lot") is historical and stays. A value like `"false"` or an empty string
enforces, same contract as every kill-switch gate. Release note / PR body mention the `=0` kill-switch.

## §4 Files in scope / NOT in scope

In scope: `flags_catalog.py` (one entry), `workflows/phase_5_implement.py` (two reads + one comment),
`conformance/bd_l2.py` (one value), the ledger line, the two authorized sibling test files, one new test file
`engine_py/tests/test_bd_flip_red_collect_probe.py`.
**NOT in scope:** `_red_collect_probe`, the timeout flag, the GATE flag, any other flag, the host repo.
