# Gate r2: bd flip HAL_RED_COLLECT_PROBE_ENFORCE (spec r2 + RED at HEAD of bd-flip-collect-probe)

**Audited:** `docs/decisions/2026-10-03-bd-flip-red-collect-probe.md` (r2), checked against
`docs/decisions/2026-10-03-bd-flip-red-collect-probe-gate-r1.md`.
**RED files:** `engine_py/tests/test_bd_flip_red_collect_probe.py` (new) and the migrated siblings
`test_gh542_collect_probe.py`, `test_bd59_enforcement_map.py`, `test_gh595_red_lint_preflight_batch.py` and
`test_phase_5_implement_C76F6F3C.py`.
**Production context (read-only):** `workflows/phase_5_implement.py` (`_red_collect_probe` L3570-3592,
`_collect_red_lint_findings` L3716-3846, legacy L3966-3985, `_verify_red_lint_rules` L4162-4423, `_collect_probe_argv`
L2654), `lib/interpreter.py:153` and `conformance/bd_l2.py`.
I did not run any tests. Production is still at the RED state: both read sites use `flag(...)`, and L3972 still has the
token.

## Round-1 findings: status

| r1 | Status | Evidence |
|---|---|---|
| F1 bd59 AC4/AC5 | **Resolved** (one NIT is left) | `test_bd59_enforcement_map.py:80,94` now use R2.6. R2.6 is `enforced_by_default: False`, so its label is `declared-not-enforced`. R2.5 is in `AWAITING_PRODUCER`, so it stays absent. The three labels remain distinct. Spec §1.6 authorizes the edit. The module docstring at L8 still says R2.1's "enforcement flag defaults to 0" (finding 4). |
| F2 caller-based sweep | **NOT resolved** | My own caller sweep finds 16 test files. The spec records 8 of them, and one unrecorded caller breaks at GREEN (finding 1). |
| F3 interpreter divergence | **Partially resolved** | The pytest-missing half is fixed by §1.5 and AC10. The host-dependency half (the engine `sys.executable` lacks the project's deps) is neither fixed nor recorded as residual risk (finding 2). |
| F4 LLM call wording | Resolved | The front-matter names the pre-existing directed-repair call site. |
| F5 recoverable conditions | Resolved | The front-matter "Note for the inbox" lists delta-retry on, attempts < 2 and no F1 co-finding. There is no cap-leg AC, which is acceptable as advisory. |
| F6 class-I probe tail | Resolved | §4 Known/advisory cites bd#192. |
| F7 legacy variants | Resolved | AC11 has three tests: `=0`, `"false"` and `BD_=0`. Each asserts that the legacy route was actually taken. |
| F8 shield labels | Resolved | The docstring L15-16 and the comment at L273-274 label the shields. |
| F9 hasattr fallback | Resolved (wording NIT) | §1.2 mandates `get_config().gate_enabled(...)` with no `hasattr`. The §4 sentence about a "`hasattr` fallback ... existing contract" is vague, but it does not contradict §1.2. |
| F10 | Unchanged NIT | `spec_from_file_location` is still in the AC7 test body. That is acceptable for Option-D. |

## Step 1: spec-internal consistency

- The literal tokens `HAL_RED_COLLECT_PROBE_ENFORCE`, `HAL_RED_COLLECT_PROBE_GATE`, `BD_RED_COLLECT_PROBE_ENFORCE`,
  `E_RED_COLLECT_PROBE`, `red_collect_probe_check`, `pytest_unavailable`, `gate_enabled`, `_red_collect_probe`,
  `_collect_red_lint_findings` and `_verify_red_lint_rules_legacy` are spelled the same in the spec, the RED file and
  production. There is no drift.
- `skip_reason="pytest_unavailable"` (§1.5) reuses the existing FileNotFoundError skip literal at L3586, so it is
  consistent with production.
- **Inconsistency (MINOR):** AC8 still reads "every other assertion unchanged". It names only gh542 AC5 and bd59 AC6. But
  §1.6 authorizes changing the bd59 AC4/AC5 exemplar from R2.1 to R2.6 and adding env pins in gh595 and C76F6F3C. AC8
  does not cover those edits (finding 3).
- **Inconsistency (MINOR):** §3 says the sweep covered "all test files calling `_verify_red_lint_rules`/
  `_collect_red_lint_findings`". The enumerated list does not cover all of them (finding 1).
- The new file's docstring L3 says "AC1..AC7, AC9", but the file also holds AC10 and AC11 (NIT, finding 4).

## Step 1.5: rule-overlap simulation (post-GREEN semantics)

Batch order: suite-safety, stub, 1q, collect-probe, fixture-schema, Rule P, semgrep, then the tail (directed repair,
which conftest turns off, then GH602 delta-retry).

| Case | ENFORCE | Probe result | First match / outcome | Expected | OK |
|---|---|---|---|---|---|
| AC2 batch | unset -> on | violation (real rc 2) | batch=[COLLECT_PROBE], eligible, retry slot: error, rec=True | as AC | yes |
| AC2 legacy | on | violation | suite/stub/1q clean, return at L3979 | as AC | yes |
| AC3 / AC6b | off | violation | event enforced=False, empty batch, semgrep terminal | != COLLECT_PROBE | yes |
| AC4 | on | rc 0 | no entry, n=0 | as AC | yes |
| AC5 | n/a | not run | gate_disabled(GATE) | as AC | yes |
| AC6a / AC11-false | "false" -> on | violation | as AC2 (batch / legacy) | as AC | yes |
| AC11 `=0` / `BD_=0` | off | violation | legacy event enforced=False, falls through | as AC | yes |
| AC10 skip | n/a | rc 1 + `No module named pytest` | `([], "pytest_unavailable")`, no batch entry | as AC | yes (after GREEN) |
| AC10 shield | n/a | rc 2 + ImportError text | 1 violation, `""` | as AC | yes |
| BFEC3E71 `test_verify_red_lint_rules_timeout` | on (default) | `bounded_run` patched to rc 124, so the probe returns `["collect-probe timeout"]` | batch=[COLLECT_PROBE], so the result is `E_RED_COLLECT_PROBE` | test asserts `E_RED_LINT_TIMEOUT` | **NO, breaks at GREEN** |

## Step 2: §1 vs §2 cross-check

- §1.1 maps to AC1. §1.2 (both reads) maps to AC2, AC3, AC6 and AC11. §1.2 (comment) maps to AC7(c). §1.3 maps to AC9
  and AC8. §1.4 maps to AC7(a)(b). §1.5 maps to AC10. §1.6 maps to AC8 (incomplete, finding 3).
- Terminal and skip branches. Batch block: AC2. Legacy block: AC2-legacy. Warn-only: AC3, AC6b and AC11. Off: AC5. New
  pytest-unavailable skip: AC10 plus the ordinary-error shield. Every §1 producing path has an AC, and every AC has a
  producing §1 path.
- AC10 is unit-level on `_red_collect_probe`. The full-path consequence (a skip produces an empty violation list and no
  block) follows by construction from L3817/L3978 (`if _cp_violations and _cp_enforce`). That is acceptable.

## Step 3: RED adequacy

- **Fail at assert time at the RED head:** AC1 (`kind == "flag"`). AC2 batch and legacy (enforce resolves False). AC4
  (enforced False). AC6a and AC11-false (`flag("false")` is False). AC7(b) (the ledger key is present). AC7(c) (L3972
  is in the slice). AC9 (False). AC10-skip (the current code turns rc 1 into a violation).
- **Shields that pass at RED and still discriminate:** AC3, AC5, AC6b, AC10-ordinary and AC11 `=0`/`BD_=0`. They are
  labelled.
- No collect-time failure: every module-level import (`pytest`, `bytedigger_engine.contracts`) exists.
- No stub-passability: the real `_verify_red_lint_rules` runs a real `pytest --co` subprocess. AC10 patches only
  `bounded_run`, which is the subprocess seam, not the unit under test. `bounded_run` is a module-level import in
  `phase_5_implement.py:127`, so `patch.object(p5, "bounded_run")` intercepts the L3581 call.
- **Over-breadth of the AC10 skip:**
  - A GREEN that matches the bare `"No module named"` is caught by AC2 (both legs), gh542 AC1 and gh542 AC4. Their real
    output carries `No module named 'nonexistent_gh542_module'`.
  - A GREEN that matches `"pytest"` anywhere, or `rc == 1`, or a regex like `No module named '?pytest` is NOT caught.
    The only negative shield uses rc 2 and text without "No module named". A RED that imports a missing pytest plugin
    (`import pytest_mock` gives `No module named 'pytest_mock'`) is a genuine RED fault, but such a GREEN would SKIP it
    (finding 5, MINOR).
- **Sibling migrations, verified at HEAD:**
  - gh542 L155 sets `=0`. bd59 L80/L94/L109 are correct.
  - gh595 sets `=0` in AC1, AC3, AC5, AC7 and AC8. AC2, AC4, AC6, AC9, AC11 and AC12 stay correct under the default
    ON: codes[0] or the subset assertions are unchanged, and AC4 is legacy with stub first.
  - C76F6F3C sets `=0` at L245 and L340.

## Step 4: reachability (§1y)

- Point `phase_5_implement.py:3811` (batch read). Host `_collect_red_lint_findings`, reached via `_verify_red_lint_rules`
  L4236. Tests: AC2-batch, AC3, AC4, AC6a and AC6b.
- Point `:3970` plus the return at L3979-3983 (legacy). Host `_verify_red_lint_rules_legacy`, reached via L4219-4223
  under `HAL_RED_LINT_PREFLIGHT_BATCH=0`. Tests: AC2-legacy and the three AC11 tests. Each asserts the legacy route
  through `gate_disabled(HAL_RED_LINT_PREFLIGHT_BATCH)`.
- Point `:3580-3592` (the new skip branch). Host `_red_collect_probe`, called from L3809 and L3968. Tests: the AC10 pair.
- Point `flags_catalog` entry. Tests: AC1, AC7(a) and AC9. Point `bd_l2.py:77-81`. Tests: AC9 and bd59 AC6. Point
  `flip_horizon_ledger.json`. Test: AC7(b).

## Adversarial edges (not covered by the §2 AC table)

1. **A process-wide `bounded_run` stub in a sibling (regression shield).** Any test that monkeypatches
   `p5.bounded_run` to a uniform non-zero rc (here rc 124) now feeds the probe. The probe turns that rc into a violation
   that blocks by default. Concrete breakage: `test_phase5_bounded_run_BFEC3E71.py::TestBehaviorBFEC3E71::test_verify_red_lint_rules_timeout`
   (L467-499) expects `E_RED_LINT_TIMEOUT` and will get `E_RED_COLLECT_PROBE` (finding 1).
2. **Host dependency missing from the engine interpreter.** This applies to a pipx/isolated engine that runs a project
   whose RED imports a third-party dependency that exists only in the project venv. `sys.executable -m pytest --co`
   raises `ModuleNotFoundError: No module named 'requests'`. That is a quoted name, so it is not a skip, and it is
   correctly treated as a violation under §1.5. The result is a hard block, 2 burnt RED rewrites and then
   `E_RED_LINT_FAIL_CAP2`. The project-aware resolver already exists (`interpreter.resolve_pytest_runner`,
   `_collect_probe_argv` L2654) but stays out of scope (finding 2).
3. **Decoy pytest-plugin import.** A RED with `import pytest_mock` (missing) must stay a violation. No shield pins this
   (finding 5).
4. **Decoy source echo.** A RED whose collection error output reproduces a source line containing the literal
   `No module named pytest` (for example a SyntaxError next to that comment) would be skipped by a substring match.
   This is low-probability, and the event still records `skip_reason`. Advisory.
5. **`enforced_by_default: True` overstates R2.1 when pytest is unavailable.** On a pytest-less engine the gate is
   always `pytest_unavailable` (skip), so `bd_l2` reports R2.1 as enforced while it never blocks. Advisory: the event
   carries `skip_reason`, so the state is observable.

VERDICT: REJECTED

## Findings

1. **MAJOR (r1 F2 not resolved): the caller-based sweep is incomplete, and one unrecorded caller breaks at GREEN.**
   - My sweep counts 16 test files that call `_verify_red_lint_rules` or `_collect_red_lint_findings`: bd166, gh1017,
     gh501, test_only_verify_gates, gh891, C76F6F3C, bd_flip, 9AB32375, gh595, bd61, gh1245, p1a, BFEC3E71, gh602,
     gh542, plus bd150 (string-only).
   - Spec §3 records gh542, bd59 (not a caller), gh595, C76F6F3C, gh602, p1a, gh501 and 9AB32375.
   - Not recorded: bd166, gh1017, gh891, bd61, gh1245, test_only_verify_gates, bd150 and **BFEC3E71**.
   - The breakage is `test_phase5_bounded_run_BFEC3E71.py::TestBehaviorBFEC3E71::test_verify_red_lint_rules_timeout`
     (L467-499). It does `monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())`, which also hits
     `_red_collect_probe` (L3581). Under default ON, the result is `batch=[E_RED_COLLECT_PROBE]`, routed through the
     GH602 retry slot with `error_code=codes[0]="E_RED_COLLECT_PROBE"`. The assertion `error_code == "E_RED_LINT_TIMEOUT"`
     fails. Today the probe is warn-only, the batch is empty and `skip_reason="timeout"` returns `E_RED_LINT_TIMEOUT`.
   - Remedy: add `HAL_RED_COLLECT_PROBE_ENFORCE=0` (with a comment) to that test and list BFEC3E71 in §1.6
     `authorized-test-edits`. Then record the full 16-file list in §3 with a one-word verdict each. My verdicts for the
     unrecorded files:
     - bd166: all `.test.ts`, so the probe is skipped. AC28's `.py` file filters by code.
     - gh1017: the extra COLLECT_PROBE sorts after 1Q, and the assertions use `!=` or `in`.
     - gh891: filters by `rule`.
     - bd61: asserts on events only.
     - gh1245: short-circuits in test_only mode.
     - test_only_verify_gates: same short-circuit.
     - bd150: string only.
2. **MAJOR (r1 F3 partially resolved): the second false-positive class is neither fixed nor recorded.**
   - r1 F3 named two failure modes of `sys.executable` vs the project interpreter: (a) pytest missing and (b) host
     dependencies that live only in the project venv. It required either a fix or measured evidence plus a recorded
     residual risk.
   - r2 fixes (a) only, through §1.5 and AC10. It keeps "interpreter choice" explicitly NOT in scope (§4), and it does
     not discuss (b) anywhere. The §1.5 justification ("shadow data came from host runs where the engine interpreter
     has pytest") covers (a) only. It does not show that the engine interpreter had the project's deps in those 229
     runs.
   - Remedy, either (i) or (ii):
     (i) Bring the interpreter into scope. Use `interpreter.resolve_pytest_runner(git_cwd)` (fallback
     `sys.executable -m pytest`), as `_collect_probe_argv` already does, and add one AC.
     (ii) Add a §3 "Residual risk" entry. It must state that default-ON blocks REDs that import project-venv-only deps
     on isolated engine installs. It must give the measured evidence (or the explicit acceptance) and name the `=0`
     kill-switch as the mitigation in the release note.
   - (ii) alone is enough for the gate, because it turns a silent hazard into a declared decision.
3. **MINOR:** AC8 text still says "every other assertion unchanged" and lists only gh542 AC5 and bd59 AC6. Extend it to
   bd59 AC4/AC5 (exemplar R2.1 to R2.6), the gh595 and C76F6F3C env pins, and the BFEC3E71 pin from finding 1.
4. **NIT:** Fix three stale texts. The bd59 docstring L8 ("the enforcement flag defaults to 0" for R2.1). The new file's
   docstring L3 ("AC1..AC7, AC9" omits AC10 and AC11). The §4 `hasattr` sentence: state plainly that both reads call
   `gate_enabled` without a `hasattr` guard, since `gate_enabled` is in the Protocol.
5. **MINOR:** The AC10 negative shield does not pin the decoys that matter. Add one shield: rc 1 (or rc 2) with
   `ModuleNotFoundError: No module named 'pytest_mock'` must still return 1 violation and `""`. This catches a GREEN
   that uses `"pytest" in out`, `rc == 1` or a `'?pytest` regex. Optionally state in §1.5 that the match is the
   unquoted `-m` loader message.
6. **Advisory:** `bd_l2` R2.1 reads `enforced` even on hosts where the probe always skips as `pytest_unavailable`
   (edge 5). This is observable through `skip_reason`, so no action is needed for this lot.

Confirmed OK: F1, F4, F5, F6, F7, F8 and F9 are folded. Every new RED test is non-vacuous and fails at assert time
where intended. The ledger line is still present (it is deleted in GREEN). The gh595 and C76F6F3C migrations are
correct and minimal.

GREEN may NOT proceed until findings 1 and 2 are resolved (r3: a spec edit plus the BFEC3E71 env pin).
