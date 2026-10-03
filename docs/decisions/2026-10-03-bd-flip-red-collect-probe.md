# bd: HAL_RED_COLLECT_PROBE_ENFORCE defaults ON (kill-switch kept)

**Status:** r3 spec (folds gate r1 and r2: see ...-collect-probe-gate-r1.md, -r2.md) · **Class:** SYSTEMATIC · **Chokepoint:** the two read sites of the
flag in `workflows/phase_5_implement.py` (`_collect_red_lint_findings`, `_verify_red_lint_rules_legacy`),
the one catalog entry and the one enforcement-map row (`conformance/bd_l2.py` R2.1).
**Enforcement layer (Principle C):** deterministic gate in the engine (`E_RED_COLLECT_PROBE`,
recoverable=True) plus the horizon guard from bd#221 (the overdue `flip-by` token and the ledger line are
removed together). **Adds no new LLM call site** (on the batch path the existing directed-repair step, on by default, may spend one cheap-model call when a probe finding enters the batch; that call site is pre-existing). Note for the inbox: `recoverable=True` holds only while delta-retry is on, fewer than 2 attempts are used and no `E_RED_LINT_F1` finding is in the same batch; a retry (an LLM re-write
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
5. (gate r1 F3) `_red_collect_probe` treats a probe whose output contains `No module named pytest` (the engine
   interpreter lacks pytest, e.g. a pipx install without the `[test]` extra) as a SKIP (`skip_reason="pytest_unavailable"`),
   not a violation. Reason: the default flips from warn to block, and the shadow data (229 runs) came from host
   runs where the engine interpreter has pytest. All other non-zero exits stay violations.
6. `authorized-test-edits:` `engine_py/tests/test_gh542_collect_probe.py` (AC5 pinned default-OFF, needs
   `=0`), `engine_py/tests/test_bd59_enforcement_map.py` (AC6 R2.1 pin flips to True; AC4 and AC5 use R2.1 as the
   "exists but off" example and switch to R2.6), and, for fixtures that cannot be collected by pytest and now hit
   the default-ON probe (gate r1 F2), `engine_py/tests/test_gh595_red_lint_preflight_batch.py` and
   `engine_py/tests/test_phase_5_implement_C76F6F3C.py` (set `HAL_RED_COLLECT_PROBE_ENFORCE=0` in the affected tests;
   no assertion changes), plus (gate r2 F1) `engine_py/tests/test_phase5_bounded_run_BFEC3E71.py::TestBehaviorBFEC3E71::test_verify_red_lint_rules_timeout`
   (patches `bounded_run` to rc 124 for every call, which now also hits the probe; pin `=0`, no assertion change).

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
  `HAL_RED_COLLECT_PROBE_ENFORCE`; the key is absent from the ledger JSON; the source slice of
  `_verify_red_lint_rules_legacy` from the first `HAL_RED_COLLECT_PROBE_GATE` line to the `E_RED_COLLECT_PROBE` return
  contains no `flip-by` (the same function holds an unrelated GH535 token elsewhere, out of scope).
- **AC8 (sibling migration)** `test_gh542_collect_probe.py` AC5 sets `=0`; `test_bd59_enforcement_map.py` AC6
  expects R2.1 `enforced_by_default is True`; every other assertion unchanged.
- **AC10 (gate r1 F3)** `_red_collect_probe` with `sys.executable` replaced by an interpreter without pytest
  (or `bounded_run` patched to return rc 1 with `No module named pytest`) returns `([], "pytest_unavailable")`;
  an ordinary collection error still returns a violation.
- **AC11 (gate r1 F7)** legacy path (`HAL_RED_LINT_PREFLIGHT_BATCH=0`): `=0`, `"false"` and `BD_...=0` behave as on the batch path.
- **AC10b (gate r2)** negative: rc 1 with `No module named 'pytest_mock'` is still a violation (the skip matches
  the `pytest` module exactly, not a prefix, not any rc 1).
- **AC8 scope** every other assertion in the migrated files is unchanged, including bd59 AC4/AC5 (exemplar R2.1 to R2.6)
  and the env pins in gh595, C76F6F3C and BFEC3E71.
- **AC9** `bd_l2.ENFORCEMENT["R2.1"]["enforced_by_default"] is True` and agrees with the catalog
  (`FLAGS[flag]["default"] != "0"` or kind gate with default `"1"`).

## §3 Edge cases / sweep

Complete caller sweep (gate r2, 16 files, verdicts): breaks and migrated: gh542, gh595, C76F6F3C, BFEC3E71; bd59 (not a caller, static map). Unaffected: bd166 (all `.test.ts`, probe skipped), gh1017 (extra finding sorts after 1Q, assertions use `!=`/`in`), gh891 (filters by `rule`), bd61 (events only), gh1245 and test_only_verify_gates (short-circuit in test_only mode), bd150 (string only), gh602, p1a, gh501, 9AB32375. (Redone after gate r1 by caller, not by flag name): all test files calling `_verify_red_lint_rules`/`_collect_red_lint_findings`. Default-OFF reliance: `test_gh542` AC5, bd59 AC4/AC5/AC6, `test_gh595` (AC1, AC3, AC5, AC7, AC8) and `test_phase_5_implement_C76F6F3C` (semgrep-missing, semgrep-internal-error); unaffected: gh602, p1a, gh501, 9AB32375. AC6 of bd59
pins the static map (§1.3). `test_gh602` (retry eligibility lists the code) and `test_gh542` AC4 (sets `=1`)
are unaffected. ERROR_CODES.md does not mention ENFORCE. `docs/decisions/2026-08-04-bd59-enforcement-map.md`
D5 ("no flag flipped by this lot") is historical and stays. A value like `"false"` or an empty string
enforces, same contract as every kill-switch gate. Release note / PR body mention the `=0` kill-switch.

## §4 Files in scope / NOT in scope

In scope: `flags_catalog.py` (one entry), `workflows/phase_5_implement.py` (two reads + one comment + the pytest-unavailable skip in `_red_collect_probe`),
`conformance/bd_l2.py` (one value), the ledger line, the five authorized sibling test files (§1.6), one new test file
`engine_py/tests/test_bd_flip_red_collect_probe.py`.
**NOT in scope:** the rest of `_red_collect_probe` (command, interpreter choice, 400-char tail), the timeout flag, the GATE flag, any other flag, the host repo.

**Residual risk accepted (gate r2 F2, interpreter choice):** the probe runs pytest with the engine's interpreter, while the RED runner resolves the project's pytest. Missing pytest is handled (§1.5). A RED that imports a dependency installed only in the project's environment, on an engine installed in an isolated environment (pipx), would be blocked by the probe and burn the two RED retries (`E_RED_LINT_FAIL_CAP2`). Not changed here (interpreter choice stays out of scope; follow-up: reuse `interpreter.resolve_pytest_runner` in the probe). Evidence for the host: 229 runs, 0 false positives. Mitigation: `HAL_RED_COLLECT_PROBE_ENFORCE=0` (named in the PR body and release note).

**Known/advisory (not done here):** the probe's output tail reaching the RED retry prompt is class I-deferred (bd#192); GREEN must use the plain `get_config().gate_enabled(...)` of §1.2 at both read sites; keeping a `hasattr(...) else False` fallback is NOT allowed (it would silently stay warn-only).
