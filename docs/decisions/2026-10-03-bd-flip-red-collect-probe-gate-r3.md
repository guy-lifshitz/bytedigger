# Gate r3: bd flip HAL_RED_COLLECT_PROBE_ENFORCE (spec r3 + RED at HEAD of bd-flip-collect-probe)

**Audited:** `docs/decisions/2026-10-03-bd-flip-red-collect-probe.md` (r3), checked against
`docs/decisions/2026-10-03-bd-flip-red-collect-probe-gate-r2.md`.
**RED files:** `engine_py/tests/test_bd_flip_red_collect_probe.py`, plus the sibling pins in `test_gh542_collect_probe.py`,
`test_bd59_enforcement_map.py`, `test_gh595_red_lint_preflight_batch.py`, `test_phase_5_implement_C76F6F3C.py` and
`test_phase5_bounded_run_BFEC3E71.py`.
**Production is still at the RED state:** `phase_5_implement.py:3811` and `:3970` still use `flag(...)`, the old
comment is still at `:3971`, and `_red_collect_probe` (L3570-3592) has no pytest-unavailable branch beyond the
FileNotFoundError case. I did not run any tests.

## Round-2 findings: status

| r2 | Status | Evidence |
|---|---|---|
| F1 sweep + BFEC3E71 | **Resolved** | `test_phase5_bounded_run_BFEC3E71.py:471-472` sets `HAL_RED_COLLECT_PROBE_ENFORCE=0` with a comment and changes no assertion. §1.6 authorizes this exact test node. §3 records the full sweep with a verdict for each file. |
| F2 interpreter choice | **Resolved as accepted residual risk** (non-blocking) | §4 has a "Residual risk accepted" paragraph. It names the failure mode (a dependency that exists only in the project venv, on an isolated or pipx engine, ends in `E_RED_LINT_FAIL_CAP2`). It also gives the host evidence (229 runs, 0 false positives), the mitigation (the `=0` kill-switch in the PR body and release note) and a follow-up (`interpreter.resolve_pytest_runner`). This is remedy (ii) from r2, and it turns a silent hazard into a declared decision. **Judgment:** acceptable as documented. The host evidence does not cover isolated installs, so this is an explicit acceptance, not a measured safety claim. The blast radius is bounded: the error is recoverable, it costs at most 2 RED rewrites, and a one-variable kill-switch turns it off. The follow-up has no issue number yet (finding 3). |
| F3 AC8 wording | Resolved | A new bullet, "AC8 scope", extends "unchanged" to bd59 AC4/AC5 and to the gh595, C76F6F3C and BFEC3E71 pins. Its placement is cosmetic (NIT, finding 5). |
| F4 stale texts | **Mostly resolved** | bd59 docstring L7-10 now reads "on by default since the flip; `=0` is the kill-switch" and uses R2.6 as the off example. The new file's docstring L3-4 lists AC10, AC10b and AC11. Still open: the §4 `hasattr` sentence is unchanged (finding 2). The new file's shield list at L16-17 omits AC10b, and L5-6 names only gh542 and bd59 for AC8 (NIT, finding 5). |
| F5 AC10 decoy shield | Resolved | Spec AC10b plus `test_ac10b_no_module_named_pytest_mock_is_still_a_violation` (L273-281). It feeds rc 1 with `No module named 'pytest_mock'` and expects 1 violation and `""`. |
| F6 advisory | Unchanged | No action needed. |

## Step 1: spec-internal consistency

- The literal tokens (`HAL_RED_COLLECT_PROBE_ENFORCE`, `_GATE`, `BD_RED_COLLECT_PROBE_ENFORCE`, `E_RED_COLLECT_PROBE`,
  `red_collect_probe_check`, `pytest_unavailable`, `gate_enabled`, `E_RED_LINT_FAIL_CAP2`,
  `resolve_pytest_runner`) are spelled the same in the spec, the RED files and production. There is no drift.
- **MINOR, finding 1:** §4 "In scope" still says "the two authorized sibling test files". §1.6 authorizes five:
  gh542, bd59, gh595, C76F6F3C and BFEC3E71. This is the §1v files-in-scope list, so it should match §1.6.
- **MINOR, finding 2:** The §4 Known/advisory line ("`hasattr` fallback for providers without `gate_enabled` is the
  existing config-provider contract") still reads as permission to keep a `hasattr(...) else False` guard. §1.2
  says the reads become plain `get_config().gate_enabled(...)`. If GREEN copies the current
  `if hasattr(_cp_cfg, "flag") else False` shape, it fails toward warn-only on a provider without `gate_enabled`. No
  RED test catches that, because the real provider has the method. The GREEN prompt should treat §1.2 as binding.
- §3 still contains the superseded "Earlier text:" sentence after the r2 sweep. It is redundant but does not
  contradict anything.

## Step 1.5: rule-overlap simulation (post-GREEN)

The r2 table still holds for AC2, AC3, AC4, AC5, AC6, AC10 and AC11. These rows are new or changed:

| Case | ENFORCE | Probe | Outcome | Expected | OK |
|---|---|---|---|---|---|
| AC10b | n/a | rc 1, `No module named 'pytest_mock'` | Matched against an exact unquoted `-m` loader message, so it is not a skip. Result: 1 violation, `""`. | as AC | yes |
| AC10b vs. a bad GREEN | n/a | same | `"pytest" in out` would skip. So would `rc == 1`. So would the regex `No module named '?pytest`. | the test fails | discriminates |
| BFEC3E71 timeout | `=0` | rc 124, giving `["collect-probe timeout"]` | event enforced=False, empty batch, semgrep rc 124, `E_RED_LINT_TIMEOUT` | as test | yes |

## Step 2: §1 vs §2 cross-check

- §1.1 maps to AC1. §1.2 maps to AC2, AC3, AC6, AC11 and AC7(c). §1.3 maps to AC9 and AC8. §1.4 maps to AC7(a)(b).
  §1.5 maps to AC10 and AC10b. §1.6 maps to AC8 plus "AC8 scope".
- Every terminal or skip branch has an AC. The block has AC2 on both paths. Warn-only has AC3, AC6 alias and AC11.
  Gate-off has AC5. The pytest-unavailable skip has AC10, and the non-skip guard has AC10 ordinary plus AC10b.
- Every AC has a producing §1 path. There is no mismatch.

## Step 3: RED adequacy

- These tests fail at assert time at the RED head: AC1, AC2 (both paths), AC4, AC6 `"false"`, AC7(b)(c), AC9,
  AC10 skip, and AC11 `"false"`.
- These shields pass at RED and still discriminate after GREEN: AC3, AC5, AC6 alias, AC10 ordinary, AC10b and the
  AC11 warn-only variants. AC10b passes at RED because rc 1 is a violation today. It is correctly a regression shield.
- Nothing fails at collect time. Only `pytest` and `bytedigger_engine.contracts` are imported at module level.
- Nothing is stub-passable. The behavioural tests drive the real `_verify_red_lint_rules` and the real legacy path
  with a real `pytest --co` subprocess. AC10 and AC10b patch only `bounded_run`, which is the subprocess seam, not the
  unit under test.
- **Sweep re-check:** I grepped `_verify_red_lint_rules|_collect_red_lint_findings|_red_collect_probe` across the
  repo. It finds exactly 16 test files: bd_flip, BFEC3E71, C76F6F3C, gh595, gh542, p1a, test_only_verify_gates, gh891,
  gh602, gh501, gh1245, gh1017, bd61, bd166, bd150 and 9AB32375. That is the same set as §3, which also lists bd59 as
  a non-caller.
  - Seven more files match only the step-name string `"verify_red_lint_rules"`: gh348, 5325B280, gh1626d, GH897,
    DC6BD331, 7C4D70ED and 4238DD14. They are not callers.
  - I spot-checked the files that patch `bounded_run` without naming the host (gh1095, A11B364A, gh1034). They call
    `_verify_red_fails_mechanically` or `_commit_red_tests`, so they do not reach the probe.
  - No unrecorded caller remains.

## Step 4: reachability (§1y)

The reachability chain is unchanged from r2:

- Point `:3811`. Host `_collect_red_lint_findings`, reached via `_verify_red_lint_rules`. Tests: AC2-batch, AC3, AC4
  and AC6.
- Point `:3970-3983`. Host `_verify_red_lint_rules_legacy`, reached under `HAL_RED_LINT_PREFLIGHT_BATCH=0`. Tests:
  AC2-legacy and AC11. Each asserts that the legacy route was taken.
- Point `:3580-3592` (the new skip). Host `_red_collect_probe`, called from L3809 and L3968. Tests: AC10 and AC10b.
- Catalog, `bd_l2.py:77-81` and the ledger are covered by AC1, AC7 and AC9.

## Adversarial edges (not covered by the §2 AC table)

1. **A GREEN that keeps the `hasattr` fallback.** `hasattr(_cp_cfg, "gate_enabled") else False` passes every RED and
   fails open to warn-only on providers that lack the method. No test pins this. Mitigation: the GREEN prompt treats
   §1.2 as binding, and the §4 sentence gets fixed (finding 2).
2. **The unquoted loader message for a different `-m` target.** This is not reachable, because the argv is hard-coded
   to `-m pytest`. Advisory only.
3. Other edges carry over from r2 as advisories: the decoy source echo (r2 edge 4) and the `enforced_by_default`
   overstatement on pytest-less hosts (r2 edge 5).

VERDICT: APPROVED

## Findings

1. **MINOR:** In §4, "the two authorized sibling test files" should be five, matching §1.6: gh542, bd59, gh595,
   C76F6F3C and BFEC3E71.
2. **MINOR:** The §4 `hasattr` sentence is still ambiguous (r2 F4 remainder). State plainly that both reads call
   `get_config().gate_enabled(...)` with no `hasattr` guard. The GREEN prompt must enforce §1.2 (adversarial edge 1).
3. **MINOR (advisory, F2):** The residual-risk acceptance is sufficient for this gate. File the follow-up
   (`resolve_pytest_runner` in the probe) as a tracked issue, and cite its number in the PR body next to the `=0`
   kill-switch, so the acceptance does not become orphaned.
4. **NIT:** Delete the superseded "Earlier text:" sentence in §3.
5. **NIT:** Three cosmetic text fixes:
   - In the new file's docstring, add AC10b to the shield list at L16-17. At L5-6, name the full AC8 migration set.
   - The spec has two AC8 bullets ("AC8" and "AC8 scope").
   - The AC order in §2 is not sequential.

None of the findings is MAJOR. r2 F1 is resolved, and the sweep is independently re-verified at 16 caller files.
r2 F2 is resolved by an explicit, bounded and mitigated residual-risk acceptance. GREEN may proceed.
