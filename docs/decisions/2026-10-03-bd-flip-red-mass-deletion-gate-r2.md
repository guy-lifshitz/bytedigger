# Gate r2: bd flip HAL_RED_MASS_DELETION_ENFORCE (spec r2 + RED at HEAD)

**Audited:** `docs/decisions/2026-10-03-bd-flip-red-mass-deletion.md` (r2),
`engine_py/tests/test_bd_flip_red_mass_deletion.py`, migrated siblings
`engine_py/tests/test_gh282_red_mass_deletion.py` and `engine_py/tests/test_gh1600_red_tests_in_existing_file.py`,
`scripts/flip_horizon_ledger.json`, `scripts/flip_horizon.py`, the read site
`workflows/phase_5_implement.py:2399-2435`, `flags_catalog.py:387-392`, `config_provider.py:21-58`.
The audit is static only. No tests were run.

## r1 finding ledger

| r1 | Status | Evidence |
|---|---|---|
| F1 MAJOR ledger line removed before GREEN | **RESOLVED** | `scripts/flip_horizon_ledger.json:5` carries `HAL_RED_MASS_DELETION_ENFORCE` (until 2026-10-17). Spec §1.3 says it stays through RED and is deleted in GREEN. `test_bd_flip_horizon_guard.py` AC7 and AC8 stay green at RED: overdue token plus ledger entry means no UNCOVERED and no STALE result. |
| F2 MAJOR sibling sweep incomplete | **RESOLVED** | Spec §3 records the sweep: 10 `_commit_red_tests` callers, 36 flag/rollout files, ERROR_CODES.md text, and `test_gh282_pragma_escape.py` (`=1` explicit). I checked this independently. Every test reference to the ENFORCE token sets it explicitly, except the clean-RED test (gh282 AC10, 0 deletions). The gh1600 fixtures delete fewer than 120 lines. |
| F3 MINOR `"false"` and BD_ alias tests | **RESOLVED** | AC9 `test_ac9_enforce_false_string_still_enforces`, AC10 `test_ac10_bd_alias_enforce_zero_restores_warn_only` |
| F4 MINOR no `flip-by:` at read site | **RESOLVED** | AC7(c) `inspect.getsource(_commit_red_tests)` |
| F5 MINOR AC7 via `check()` not exit code | **RESOLVED** | AC7 calls `check(FLAGS, ledger, date(2026,10,3))` with a fixed date and asserts on problem lines and key absence |
| F6 advisory remedy-less block message | Open, advisory | Not recorded as a follow-up in the spec (see finding 4) |
| F7 NIT `_clean_env` aliases | **RESOLVED** | The test file deletes `BD_`/`BYTEDIGGER_` aliases on lines 34-40 |
| F8 NIT release note | **PARTIAL** | §3 names the `=0` kill-switch. It does not mention the transitional re-entry stop (see finding 2) |

## Step 1: spec-internal consistency

- The literal tokens `HAL_RED_MASS_DELETION_ENFORCE`, `HAL_RED_MASS_DELETION_GATE`, `HAL_RED_MASS_DELETION_MAX_LINES`, `E_RED_MASS_DELETION`, `red_mass_deletion_check`, `red_mass_deletion_blocked`, `# red-mass-deletion: allow` and `gate_enabled` are spelled the same in the spec, the RED file and production. There is no drift.
- The AC ordering (AC1-7, AC9, AC10, AC8) is unusual but not contradictory.
- The RED module docstring says "AC1..AC7" and leaves out AC9 and AC10 (NIT, finding 3).

## Step 1.5: rule-overlap simulation (read-site dispatcher, lines 2399-2435, post-GREEN semantics)

| Case | GATE | ENFORCE resolved | violations / exempted | First match | Expected | OK |
|---|---|---|---|---|---|---|
| AC2 | unset->on | unset->on | 1/0 | block return | E_RED_MASS_DELETION, rec=False, blocked event | yes |
| AC3 | on | "0"->off | 1/0 | falls to D1 (E_RED_TESTS_IN_EXISTING_FILE, mass-deletion branch) | != E_RED_MASS_DELETION, enforced False | yes |
| AC4 | on | on | 0/1 | no block; D1 skips because exempted | enforced True, exempted 1 | yes |
| AC5 | on | on | 0/0 (10 lines) | D1 refuses with its own code | enforced True, violations 0 | yes |
| AC6 | "0"->off | n/a | n/a | gate skipped, no event | no check event | yes |
| AC9 | on | "false"->on | 1/0 | block | E_RED_MASS_DELETION | yes |
| AC10 | on | BD_="0"->off via `_aliased_env_get` | 1/0 | D1 | enforced False | yes |

GH282 runs before D1 (line 2436 comes before line 2439), so the first-matching branch is correct in every row.

## Step 2: §1 decision vs §2 ACs cross-check

- §1.1 catalog kind/default/description/no token: AC1.
- §1.2 read becomes `gate_enabled`: AC2, AC9, AC10 together exclude `flag()`, raw `os.environ`, and ad-hoc `"false"` predicates. The comment rewrite is covered by AC7(c).
- §1.3 ledger deletion in GREEN: AC7(a)(b).
- §1.4 sibling migration: AC8 (already landed in the earlier RED commit and verified at gh282 L267, gh1600 L763/L926, and the gh282 catalog kind `"gate"` at L436).
- Terminal/error path (block return, `recoverable=False`): AC2. Warn-only path: AC3/AC10. Off path: AC6. Pragma path: AC4. Below-threshold path: AC5.
- No AC lacks a producing path, and every branch has an AC.

## Step 3: RED adequacy

Expected state at the RED head (static simulation):
- **Fails at assert time:** AC1 (`kind == "flag"`), AC2 (status is `error`, but the code is E_RED_TESTS_IN_EXISTING_FILE because D1 fires under warn-only), AC4 (enforced False), AC5 (enforced False), AC7 (b) ledger key present and (c) `flip-by:` at L2403, AC9 (`flag("…")` with "false" is False).
- **Passes at RED (regression shields):** AC3, AC6, AC10. Current behavior already satisfies them. They are still discriminating post-GREEN: AC10 kills a GREEN that bypasses the aliases, and AC3/AC6 kill a GREEN that drops the kill-switches. The spec does not label them as shields (finding 1).
- All imports resolve today (`_commit_red_tests`, `WorkflowContext`, `flags_catalog`, `scripts/flip_horizon.py` via spec_from_file_location), so there is no collect-time failure.
- No stub-passability: the real `_commit_red_tests` runs on a real tmp git repo. Only the telemetry sink `_emit_safe` is replaced, and `prev` is a data carrier. The UUT is not mocked.

## Step 4: reachability (§1y)

- Point `phase_5_implement.py:2401` (`_mdl_enforce` read), Host `_commit_red_tests`, Test AC2/3/4/5/9/10, which call `_commit_red_tests(ctx, prev)` directly with `git_cwd` set to the tmp repo. `get_config()` resolves to `_DefaultConfigProvider` (no conftest override found), so the env/alias semantics under test are the production ones.
- Point `phase_5_implement.py:2424-2435` (block return plus blocked event), Host `_commit_red_tests`, Test AC2/AC9.
- Point `flags_catalog.py:387-392`, Host the `FLAGS` dict, Test AC1 and AC7(a).
- Point `scripts/flip_horizon_ledger.json:5`, Host the ledger loaded by `flip_horizon.check`, Test AC7(b).

## Adversarial edges (not covered by the §2 AC table)

1. **Precedence decoy:** `HAL_RED_MASS_DELETION_ENFORCE=1` with `BD_RED_MASS_DELETION_ENFORCE=0` should enforce (HAL_ wins). A GREEN that ORs the aliases would pass every AC and still be wrong. It is untested here, but `_aliased_env_get` already implements this, so the risk is low.
2. **`BYTEDIGGER_` alias:** only `BD_` is exercised.
3. **Empty-string value:** `HAL_RED_MASS_DELETION_ENFORCE=""` was warn-only before and now enforces (`"" != "0"`). This silently changes behavior for anyone who "cleared" the var with an empty assignment. It is the same contract as other gates, but it is not documented in §3.
4. **authorized-test-edits is not an escape for GH282:** a spec that legitimately authorizes a large rewrite of an existing test file (at least 120 lines and at least 50%) now hard-blocks with `recoverable=False` unless the post-RED file carries the pragma. This semantic predates the flip but only now has teeth. Shadow data (0/232) makes it low risk.
5. **Cross-deploy re-entry:** a run whose cycle-1 RED commit landed a mass deletion under warn-only and that re-enters at cycle 2 or later after the flip diffs against the frozen pre-RED SHA, so it now stops with a non-recoverable error. This is the r1 F8 "transitional re-entry stop".
6. **Ledger horizon timing:** this flag's own ledger entry expires 2026-10-17. If GREEN slips past that date, `test_bd_flip_horizon_guard.py::test_ac7_real_catalog_real_ledger_real_today_clean` goes red because of this flag (LAPSED), not only because of the sibling flag.
7. **Ambient-env leakage in gh1600:** `test_gh1600_red_tests_in_existing_file.py` has no autouse `_clean_env`. An ambient `HAL_RED_MASS_DELETION_GATE=0` or a `BD_` alias in a developer shell would change AC8a/AC8b outcomes. This is pre-existing and outside scope.

VERDICT: APPROVED

## Findings

1. **MINOR** AC3, AC6 and AC10 pass at the RED head (regression shields). The spec and the RED docstring should say so. The RED-proof record should expect exactly AC1, AC2, AC4, AC5, AC7 and AC9 failing, and AC3, AC6 and AC10 passing. These tests should not later be mistaken for a vacuous RED or a premature GREEN.
2. **MINOR** r1 F8 is only partly folded in. The release note / PR body should also name the cross-deploy re-entry stop (edge 5) and the empty-string behavior change (edge 3), next to the `=0` kill-switch.
3. **NIT** In the RED module docstring, "AC1..AC7" is stale. It should read "AC1..AC7, AC9, AC10".
4. **NIT** r1 F6 (the block message names no remedy: pragma or `=0`) should be recorded as an explicit follow-up issue so it is not lost.
5. **MINOR (advisory)** Consider adding a precedence test (edge 1) in GREEN-adjacent hardening. It does not block, because `_aliased_env_get` already enforces HAL_-wins and GREEN only swaps `flag()` for `gate_enabled()`.

No MAJOR findings. GREEN may proceed.
