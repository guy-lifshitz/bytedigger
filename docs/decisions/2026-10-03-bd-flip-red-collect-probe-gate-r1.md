# Gate r1: bd flip HAL_RED_COLLECT_PROBE_ENFORCE (spec r1 + RED at HEAD)

**Audited:** `docs/decisions/2026-10-03-bd-flip-red-collect-probe.md` (r1),
`engine_py/tests/test_bd_flip_red_collect_probe.py`, the migrated siblings
`engine_py/tests/test_gh542_collect_probe.py` (AC5, L155) and `engine_py/tests/test_bd59_enforcement_map.py` (AC6, L108-109),
`scripts/flip_horizon_ledger.json`, `scripts/flip_horizon.py`, both read sites in
`workflows/phase_5_implement.py` (batch `_collect_red_lint_findings` L3807-3827, legacy `_verify_red_lint_rules_legacy`
L3966-3985), the batch tail of `_verify_red_lint_rules` (L4354-4423), `_red_collect_probe` (L3570-3592),
`lib/recoverable_gate.py`, `_recoverable_policy.py`, `config_provider.py`, `conformance/bd_l2.py`, `flags_catalog.py:333-338`.
The audit is static only. No tests were run.

## Step 1: spec-internal consistency

- The literal tokens `HAL_RED_COLLECT_PROBE_ENFORCE`, `HAL_RED_COLLECT_PROBE_GATE`, `BD_RED_COLLECT_PROBE_ENFORCE`,
  `E_RED_COLLECT_PROBE`, `red_collect_probe_check`, `gate_disabled`, `gate_enabled`, `_collect_red_lint_findings`,
  `_verify_red_lint_rules_legacy` and `ENFORCEMENT["R2.1"]["enforced_by_default"]` are spelled the same in the spec,
  the RED file and production. There is no drift.
- §1.2 places the stale `flip-by:2026-07-24` comment only at the legacy site. That is correct: L3972 has it, and the
  batch site has no comment.
- **Contradiction:** AC8 says "every other assertion unchanged" in `test_bd59_enforcement_map.py`. That cannot hold.
  `test_ac4` and `test_ac5` in the same file use R2.1 as the "declared-not-enforced" exemplar (finding 1).
- **Accuracy:** the front-matter says "Adds no LLM call". In production `HAL_DIRECTED_REPAIR` defaults ON
  (`lib/directed_repair.py:118-129`). A batch whose only code is `E_RED_COLLECT_PROBE` gets
  `_dr_gate="red_lint_preflight"` and calls `attempt_directed_repair` (a cheap-model LLM call) BEFORE the GH602
  delta-retry (L4369-4381). Before the flip that finding never entered the batch (finding 6).
- The header says "AC7(c) wording corrected after RED". This is a post-freeze edit, wording only. It is acceptable but
  belongs in a changelog line.

## Step 1.5: rule-overlap simulation (post-GREEN semantics)

Batch path order: suite-safety, stub, 1q, collect-probe, fixture-schema, Rule P, then semgrep, then the batch tail
(directed repair, which conftest turns off, then delta-retry, which is ON by default).

| Case | Path | ENFORCE resolved | Probe | First match / outcome | Expected | OK |
|---|---|---|---|---|---|---|
| AC2 | batch | unset -> on | violation | batch=[COLLECT_PROBE], eligible, `gated_step_result` retry slot: error, rec=True, code=codes[0] | E_RED_COLLECT_PROBE, rec True, 1 event enforced=True | yes |
| AC2 | legacy | unset -> on | violation | suite/stub/1q clean, so the block return at L3979 fires | same | yes |
| AC3 | batch | "0" -> off | violation | event enforced=False, empty batch, semgrep result | != COLLECT_PROBE | yes |
| AC4 | batch | on | clean (rc 0) | no batch entry | enforced True, n=0 | yes |
| AC5 | batch | n/a | not run | gate_disabled(GATE) | no probe event | yes |
| AC6a | batch | "false" -> on | violation | as AC2 | COLLECT_PROBE | yes |
| AC6b | batch | BD_="0" -> off (`_aliased_env_get`) | violation | as AC3 | enforced False | yes |

The first-matching branch is correct in every row. Note on AC2 (batch): `recoverable=True` is produced by
`RecoverableGateMixin` (retry slot, attempts < cap 2), NOT by the finding's own `recoverable` field. It holds only
while `HAL_RED_PREFLIGHT_DELTA_RETRY` is on, gate_attempts < 2, and no non-eligible co-finding (E_RED_LINT_F1) is in
the batch (finding 5).

## Step 2: §1 vs §2 cross-check

- §1.1 catalog: AC1. §1.2 both reads: AC2 (batch + legacy) and AC6 (batch only). §1.2 comment: AC7(c).
  §1.3 static map: AC9 + AC8. §1.4 ledger: AC7(a)(b). §1.5 authorized edits: AC8.
- Terminal paths. Batch block: AC2. Legacy block return (`recoverable=True`): AC2-legacy. Warn-only: AC3/AC6b (batch
  only). Off: AC5 (batch only).
- **Gap (MINOR):** the legacy read site is covered only on the default leg. A GREEN that uses `gate_enabled` at the
  batch site but a raw `os.environ.get(...) != "0"` (alias-blind) or an ad-hoc predicate at the legacy site passes
  every AC. AC7(c) checks only for the absence of `flip-by`. Add legacy variants of AC3 and AC6 (finding 7).
- Branches the flip makes newly reachable for this code with no AC: the cap leg (`E_RED_LINT_FAIL_CAP2`,
  recoverable=False) and the `HAL_RED_PREFLIGHT_DELTA_RETRY=0` leg (recoverable=False on the batch path, while the
  legacy path stays True). Both are pre-existing mechanics, so this is advisory (finding 5).

## Step 3: RED adequacy

Expected state at the RED head (static simulation, `flag()` semantics, ENFORCE unset):
- **Fails at assert time:** AC1 (`kind == "flag"`). AC2-batch (status `ok` with semgrep present, or the
  `E_RED_LINT_SEMGREP_MISSING` code). AC2-legacy (same reasoning; the gate_disabled precondition passes). AC4
  (enforced False). AC6a (`flag("false")` is False). AC7 (b): ledger key present (L3). AC7 (c): the slice
  L3967-3982 contains `flip-by` at L3972. AC9 (False).
- **Passes at RED (regression shields):** AC3, AC5, AC6b. They still discriminate after GREEN. The spec and docstring
  do not label them (finding 8).
- AC7(c) slice check: no line in `_verify_red_lint_rules_legacy` before L3967 contains `HAL_RED_COLLECT_PROBE_GATE`.
  The end-search hits `E_RED_COLLECT_PROBE` first at L3982 (`HAL_RED_...` and `..._PROBE_ENFORCE` do not contain the
  substring `E_RED_COLLECT_PROBE`). The GH535 token at L3987 is outside the slice. Correct.
- No collect-time failure: every module-level import resolves today (`bytedigger_engine.contracts`).
- No stub-passability: the real `_verify_red_lint_rules` runs, with a real `pytest --co` subprocess on a real
  tmp file. Only `_emit_safe` is wrapped. Directed repair is off via conftest
  (`_hal_directed_repair_default_off`), so no LLM spawn happens.
- Sibling edits at HEAD were verified: gh542 L155 sets `=0`, and bd59 L109 pins R2.1 `is True`.

## Step 4: reachability (§1y)

- Point `phase_5_implement.py:3811` (`_cp_enforce`, batch). Host `_collect_red_lint_findings`, reached via
  `_verify_red_lint_rules` L4236. Tests: AC2-batch, AC3, AC4, AC6a/b.
- Point `phase_5_implement.py:3970` + return L3979-3983 (legacy). Host `_verify_red_lint_rules_legacy`, reached via
  L4219-4223 under `HAL_RED_LINT_PREFLIGHT_BATCH=0`. Test: AC2-legacy (asserts that the legacy route was taken).
- Point `flags_catalog.py:333-338`. Host `FLAGS`. Tests: AC1, AC7(a), AC9.
- Point `bd_l2.py:77-81`. Host `ENFORCEMENT`, consumed by `check_bd_l2` L292-300 labels. Tests: AC9, bd59 AC6.
  The label consumer is what breaks bd59 AC4/AC5 (finding 1).
- Point `flip_horizon_ledger.json:3`. Host `flip_horizon.check`. Test: AC7(b).
- `get_config()` resolves to `_DefaultConfigProvider` in tests. A host-injected provider (HAL `hal_config_provider`)
  is not exercised.

## Adversarial edges (not covered by the §2 AC table)

1. **Interpreter divergence (finding 3).** `_red_collect_probe` runs `sys.executable -m pytest --co`. The RED runner
   resolves the project's pytest via `interpreter.resolve_pytest_runner(git_cwd)` and falls back to `python3`
   (L2638-2648, GH1626 "one canonical resolver"). With an isolated engine venv (the documented `pipx install
   bytedigger-engine`, npm/README.md:12) the probe fails in two ways: (a) pytest is missing, because it sits only in
   the `[test]` extra (pyproject.toml:35), so `No module named pytest` exits rc 1, which is not FileNotFoundError, so
   it counts as a violation; (b) host deps that live only in the project venv raise ModuleNotFoundError. Both hard-block
   every Python RED. That is a load error counted as a rejection, which bd_l2 `_r21`'s own doctrine forbids.
2. **rc 5 / no tests collected:** a RED path that is a `conftest.py` or helper module is an explicit initpath with 0
   tests. It exits rc 5 and is now a blocking violation that an LLM rewrite cannot fix.
3. **rc 4 / missing path:** a stale `red_test_paths` entry is reported as E_RED_COLLECT_PROBE (a RED-content fault)
   instead of an infra error (see the C76F6F3C sibling in finding 2).
4. **Slow collection:** the probe does not pin `--rootdir`, unlike `_collect_probe_argv` (GH714, 82 s measured walk).
   A slow but valid collection over 60 s becomes "collect-probe timeout" and blocks.
5. **Cap burn:** for any of edges 1-4, the delta-retry spends 2 RED re-writes (plus a directed-repair call in prod)
   before `E_RED_LINT_FAIL_CAP2`, which is terminal.
6. **Mixed batch with E_RED_LINT_F1:** `_preflight_retry_eligible` is False, so the result is recoverable=False even
   though the spec promises recoverable=True.
7. **Class-I injection activated:** the probe tail `[-400:]` is class I-deferred (bd#192,
   `class_i_inventory.json:181`). Warn-only kept it out of every prompt. Default-ON forwards it into the RED retry
   (`forwarded_data["findings"]`) and into the directed-repair findings.
8. **Minimal provider:** the read sites use `hasattr(_cp_cfg, "flag") else False`. GREEN has to pick the fallback for
   a provider without `gate_enabled`. If it keeps `else False`, the default stays OFF there. Untested.
9. **Precedence decoy:** `HAL_…=1` together with `BD_…=0` must enforce (HAL_ wins). Untested, but low risk since
   `_aliased_env_get` already handles it.

VERDICT: REJECTED

## Findings

1. **MAJOR: the bd59 sibling migration is incomplete.** `test_bd59_enforcement_map.py::test_ac4_three_enforcement_states_are_distinguishable`
   (L74-86) and `::test_ac5_a_disabled_consequence_never_reads_as_enforced` (L89-97) use `enforcement:R2.1` as the
   declared-off state. After §1.3, `check_bd_l2` labels R2.1 `enforced`, the same as R2.2. AC4 then gets a set of
   size 2, and AC5's `!=` fails. Both go red at GREEN, which contradicts AC8's "every other assertion unchanged".
   Remedy: authorize both edits and switch the exemplar to R2.6 (still `declared-not-enforced`). Fix the stale text at
   L5-9 and L95 in the same edit, and extend AC8.
2. **MAJOR: the §1a sibling sweep is incomplete. It was selected by token, not by call path.** The 38-file sweep
   picked files that name the flag. These callers of `_verify_red_lint_rules` do not name it and break once ENFORCE
   defaults ON, because their fixtures are non-collectable:
   - `test_gh595_red_lint_preflight_batch.py`: `_DOUBLE_VIOLATION_CONTENT` and `_CLEAN_CONTENT` import the
     nonexistent `helper_mod`/`side_dep_mod`. AC1 L155 (`preflight_error_codes == [STUB, 1Q]`), AC5 L274 (`== [1Q]`),
     AC8 L361 (`codes == [STUB, 1Q]`), AC7 half-2 L332 (expects SEMGREP_MISSING, gets COLLECT_PROBE), and AC3 L216
     (expects `ok` when semgrep is present) all fail.
   - `test_phase_5_implement_C76F6F3C.py::test_verify_red_lint_rules_fails_loud_when_semgrep_missing` (L234-264):
     the path `foo.py` does not exist, pytest exits rc 4, and the result is COLLECT_PROBE instead of SEMGREP_MISSING.
     `::test_verify_red_lint_rules_handles_semgrep_internal_error` (L309-353): the module-wide `subprocess.run` stub
     (rc 2) also feeds the probe, which yields a violation, `status=error` instead of `ok`.
   - Checked and unaffected: gh602 (codes[0] and eligibility unchanged), p1a, gh501, 9AB32375 AC8.
   Remedy: pin `HAL_RED_COLLECT_PROBE_ENFORCE=0` (with a comment) in each listed test and add those files to
   `authorized-test-edits`. Redo the sweep over all 15 files that call `_verify_red_lint_rules`/`_collect_red_lint_findings`
   and record that list in §3.
3. **MAJOR: the shadow evidence does not cover the population whose default changes.** See edge 1. The 229/0-FP data
   comes from HAL host runs. Default-ON ships to PyPI/pipx consumers, where the probe interpreter (`sys.executable`)
   differs from the RED runner's interpreter. The result is a systematic false positive class: "No module named
   pytest", or a host-dep ModuleNotFoundError. The spec lists `_red_collect_probe` as NOT in scope and does not
   discuss this. Remedy, either (a) or (b):
   (a) Bring the probe's argv into scope. Reuse `_collect_probe_argv` / `interpreter.resolve_pytest_runner`, map
   pytest-unavailable, rc 4 and rc 5 to `skip_reason` (not a violation), and add ACs for "pytest absent leads to a
   skip, not a block".
   (b) Show measured evidence that the probe interpreter equals the runner interpreter on every supported install
   route, and record the residual risk in §3.
   Without (a) or (b), default-ON can brick OSS builds behind 2 burnt retries.
4. **MINOR:** "Adds no LLM call" is inaccurate. A directed-repair LLM call fires on the batch path in prod (default
   ON), plus up to 2 RED re-writes. Correct the front-matter and the inbox note.
5. **MINOR:** `recoverable=True` holds on the batch path only via GH602 (delta-retry on, attempts < 2, no F1
   co-finding). State this in §3. Consider one AC on the cap leg (`E_RED_LINT_FAIL_CAP2`) for a persistently
   non-collectable RED.
6. **MINOR:** the class-I bd#192 probe tail now reaches LLM prompts (edge 7). Record it in §3 and link bd#192.
7. **MINOR:** add legacy-path variants of AC3 (`=0`) and AC6 (`"false"`, `BD_=0`), so that a GREEN that only half
   migrates the legacy read is caught.
8. **MINOR:** label the shields. The RED-proof should expect AC1, AC2 (both), AC4, AC6a, AC7 and AC9 failing, and
   AC3, AC5 and AC6b passing.
9. **NIT:** state the `hasattr` fallback for providers without `gate_enabled` (edge 8). Since `gate_enabled` is in the
   Protocol, the fallback should be True or dropped.
10. **NIT:** AC7 loads `flip_horizon.py` via `spec_from_file_location` inside the test body. That is fine for
    Option-D (sister precedent), but it would trip `E_RED_1Q_EXEC_IMPORT` if this file were ever routed through the
    engine RED-lint.

Confirmed OK: the ledger line stays through RED (`flip_horizon_ledger.json:3`, until 2026-10-17, so a GREEN slip past
that date makes the guard go LAPSED). AC7 goes through `check()` with a fixed date. The string `"false"` and the
`BD_` alias cases are present. The bd_l2 static map change agrees with the catalog via AC9. The gh602 eligible code
list already contains `E_RED_COLLECT_PROBE`.

GREEN may NOT proceed until findings 1-3 are resolved (r2).
