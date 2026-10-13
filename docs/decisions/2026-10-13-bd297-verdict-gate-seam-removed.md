---
red_tests:
  - engine_py/tests/test_bd297_verdict_seam_removed.py
sibling_tests:
  - engine_py/tests/test_bd297_verdict_seam_removed_fences.py
  - engine_py/tests/test_bd65_window_d_port.py
  - tests/test_bd89_p3d_flag_owner_lint.py
  - engine_py/tests/test_tier_gate_repo_root_portability.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_5_implement.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - engine_py/tests/test_bd296_verdict_gate_lint_permanent_warn.py
  - engine_py/tests/test_bd296_verdict_gate_lint_fences.py
  - engine_py/tests/test_bd91_pregreen_strict_and.py
  - engine_py/tests/test_GH1674_injection_missing.py
  - CHANGELOG.md
---
# bd#297: delete the dead verdict-gate lint seam at the phase 5 acceptance step

Slice of #89. Closes #297. Refs #89, #296. Base: origin/main @ 7e2e531. Decision (b) from the issue.

## 1. Problem

`_gate_on_validation` (phase_5_implement.py) calls `_run_verdict_gate` (an alias of
`verdict_gate.run_gate`) on the validator doc after acceptance and emits `verdict_gate_lint_warn`
on a non-zero rc. The phase 5 validator writes `Verdict: PASS|FAIL`; the lint's token is
`VERDICT: APPROVED`, so `run_gate` returns `NOT_APPROVED` (rc 0) for every real validator doc:
805 runs, 425 validation docs, 0 reachable. Making it reachable would change the validator's output
contract or warn UNANCHORED on every run. #89: remove layers before porting logic.

## 2. Change

1. `phase_5_implement.py`: delete the `from bytedigger_engine.verdict_gate import run_gate as
   _run_verdict_gate` import and the whole lint block (comment, `gate_enabled(...)` guard, the
   `try/except`, the `_emit_safe("verdict_gate_lint_warn", ...)`) from `_gate_on_validation`.
   The returned `StepResult(status="ok", ...)` is untouched. Drop an import only if it becomes unused.
2. Tests that live only for the seam: delete
   `engine_py/tests/test_bd296_verdict_gate_lint_permanent_warn.py` and
   `engine_py/tests/test_bd296_verdict_gate_lint_fences.py` (they pin the warn event at the seam).
   In `test_bd91_pregreen_strict_and.py` and `test_GH1674_injection_missing.py` remove the
   `monkeypatch.setenv("HAL_VERDICT_GATE_LINT", "0")` pins (and the sentence in the GH1674 docstring
   that explains them, and the bd91 comment on the line above the pin that exists only for it) only;
   nothing else in those files changes.
3. `flags_catalog.py`: the `HAL_VERDICT_GATE_LINT` entry **stays** (kind gate, default "1"): `run_gate`
   itself reads that env name and the host audit-gate hook uses it. Measured deviation from the issue
   text ("catalog entries that exist only for this seam"): there is no event catalog and no other config
   entry that exists only for the seam. Only its description changes: no mention of the phase_5
   seam or of `verdict_gate_lint_warn`; say that `=0` disables the lint in `verdict_gate.run_gate`
   (host audit-gate hook).
4. `CHANGELOG.md`: one entry under Unreleased, same style as the bd#296 entry.
5. Not touched: `verdict_gate.py`, `audit_gate.py`, `verdict_verify.py`, `doctor.py`, the host hook.

## 3. Acceptance

- AC1 (RED). `phase_5_implement` has no attribute `_run_verdict_gate`.
- AC2 (RED). No file under `engine_py/bytedigger_engine` contains `verdict_gate_lint_warn`, and
  `workflows/phase_5_implement.py` contains neither `_run_verdict_gate` nor `HAL_VERDICT_GATE_LINT`
  nor an import of `bytedigger_engine.verdict_gate`.
- AC3 (RED). Real `_gate_on_validation` with a passed validator whose doc is `VERDICT: APPROVED` with no
  anchor block (UNANCHORED on main): `status == "ok"`, the event log has no event whose type contains
  `verdict_gate`, and `bytedigger_engine.verdict_gate.run_gate` is never called (spy that raises on call,
  so a renamed event with the call kept does not pass).
- AC4 (RED). `HAL_VERDICT_GATE_LINT` description in the catalog contains neither `phase_5`, nor
  `acceptance seam`, nor `verdict_gate_lint_warn`; it still mentions `=0` and `run_gate`; entry still
  `kind == "gate"`, `default == "1"`.
- AC5 (fence, passes before and after). Same step with a doc ending `Verdict: PASS`: `status == "ok"`,
  `data["verdict"]` as before, no `verdict_gate*` event.
- AC6 (fence). Same step with a doc ending `Verdict: FAIL`: rejected exactly as on main (same status,
  error_code `E_VALIDATION_FAILED` or the reroute result the existing harness yields), no `verdict_gate*` event.
- AC7 (fence). `verdict_gate.run_gate(doc, repo_root, {})` on the UNANCHORED doc returns
  `(1, {"result": "UNANCHORED", ...})`; on a `Verdict: PASS` doc `(0, {"result": "NOT_APPROVED", ...})`;
  with `{"HAL_VERDICT_GATE_LINT": "0"}` `(0, {"result": "SKIP"})`. The lint tool is unchanged.
- AC8 (scan, GREEN review, issue reference case 4). `grep -rn "_run_verdict_gate\|verdict_gate_lint_warn"`
  over the repo returns hits only under `docs/decisions`, the CHANGELOG, and the bd#297 test files
  `engine_py/tests/test_bd297_*` (they name the removed symbols to pin their absence). Known leftover, not
  removed here: prose in `verdict_gate.py:5-6` and `test_tier_gate_repo_root_portability.py:6` still says the
  phase_5 seam imports the module; `run_gate` stays live through the external host hook and the doctor probe.

## 4. Test plan

RED file holds only tests that fail today (AC1-AC4); fences (AC5-AC7) live in the sibling file, because
lot-preflight rejects a passing test inside a RED file. AC3/AC5/AC6 drive the real step with a recording
event log; copy the harness idiom from the bd#296 fences file before it is deleted. Local runs are
targeted; the full suite is read from CI on the PR.
