"""RED tests for GH517 Ship A2 — wire `ac_dsl.admit()` (A1) into phase_45_spec.py
as a new warn-only step `_verify_spec_ac_dsl`.

AC1-AC12 each map to one test. All FAIL pre-GREEN because:
  - _verify_spec_ac_dsl does not exist in workflows/phase_45_spec.py
  - phase_45_spec_workflow().steps has no "verify_spec_ac_dsl" entry
  - flags_catalog.FLAGS lacks HAL_AC_DSL_GATE / HAL_AC_DSL_GATE_ENFORCE
  - error_codes.ERROR_CODES lacks E_SPEC_AC_UNCOMPILABLE

Deferred-import discipline (§1q-extension D1CF5FDF): the new symbol
`_verify_spec_ac_dsl` is imported INSIDE each test function body so the file
COLLECTS cleanly and fails at assert/call time rather than hanging
red_runtime with an ImportError at collection.

No module-level sys.path manipulation / conftest import (§1q / 81F97F3D) —
conftest.py's conftest-import-time singleton already puts engine_py root +
workflows/ on sys.path. No absolute host paths — all paths derive from
tmp_path / __file__ (A1 RCA-1).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from contracts import StepResult  # already exists — safe top-level import


# ─── event-log capture helpers (mirror test_gh290_gate_disabled_telemetry.py) ─


class _CaptureEventLog:
    """Records (event_type, payload, run_id) tuples — mirrors real event_log.append."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type, payload, run_id):
        self.events.append((event_type, payload, run_id))


class _StubRunCtx:
    def __init__(self, event_log):
        self.event_log = event_log
        self.run_id = "gh517a2-test-run"


def _payloads(log: _CaptureEventLog, event_type: str) -> list[dict]:
    return [payload for (etype, payload, _rid) in log.events if etype == event_type]


def _patch_telemetry(monkeypatch, log: _CaptureEventLog | None):
    import telemetry_ctx  # deferred — reached via workflows/ on sys.path

    stub = _StubRunCtx(log) if log is not None else None
    monkeypatch.setattr(telemetry_ctx, "get_current_run", lambda: stub)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(org_config={"complexity": "SIMPLE"})


def _ctx_no_repair() -> SimpleNamespace:
    """GH634 §4a: repair-disabled ctx for tests whose intent is the terminal
    E_SPEC_AC_UNCOMPILABLE path, not the new directed-repair pre-stage."""
    return SimpleNamespace(
        org_config={"complexity": "SIMPLE", "directed_repair_skip": True}
    )


def _prev(spec_path: Path, cycle: int = 1, **extra) -> StepResult:
    data: dict = {"spec_path": str(spec_path), "cycle": cycle, **extra}
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


# ─── fixture spec texts ─────────────────────────────────────────────────────

# Valid spec: 2 ACs, both with compilable AC-checks blocks.
_VALID_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | file exists |
| AC2 | trigger | event emitted |

### AC-checks
```yaml
AC1:
  type: file_contains
  path: foo.py
  expect: present
AC2:
  type: event_emitted
  stream: events.jsonl
  name: foo_done
  min_count: 1
```
"""

# Uncompilable spec: check block references an unknown check type.
_UNCOMPILABLE_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | does the thing |

### AC-checks
```yaml
AC1:
  type: totally_unknown_check_type
  foo: bar
```
"""

# No `### AC-checks` section at all (AC12).
_NO_AC_CHECKS_SECTION_SPEC = """\
# Test Spec

## §3 Acceptance Criteria

| AC | Input | Expected |
|---|---|---|
| AC1 | trigger | does the thing |
"""


# ─── AC1 — step registered between verify_spec_lint_batch and build_review_prompt


def test_ac1_verify_spec_ac_dsl_in_step_list_between_lint_batch_and_review() -> None:
    """AC1: phase_45_spec_workflow().steps has 'verify_spec_ac_dsl', positioned
    after 'verify_spec_lint_batch' and before 'build_review_prompt'.
    FAILS pre-GREEN: step absent from workflow list."""
    from phase_45_spec import phase_45_spec_workflow  # deferred

    names = [s.name for s in phase_45_spec_workflow().steps]

    assert "verify_spec_ac_dsl" in names, (
        f"'verify_spec_ac_dsl' missing from step list; got: {names}"
    )
    assert "verify_spec_lint_batch" in names, f"'verify_spec_lint_batch' missing: {names}"
    assert "build_review_prompt" in names, f"'build_review_prompt' missing: {names}"

    i_lint = names.index("verify_spec_lint_batch")
    i_dsl = names.index("verify_spec_ac_dsl")
    i_review = names.index("build_review_prompt")

    assert i_dsl > i_lint, (
        f"verify_spec_ac_dsl ({i_dsl}) must come after verify_spec_lint_batch ({i_lint})"
    )
    assert i_dsl < i_review, (
        f"verify_spec_ac_dsl ({i_dsl}) must come before build_review_prompt ({i_review})"
    )


# ─── AC2 — prev=None/no-data → error / E_MISSING_PREV_DATA ─────────────────


def test_ac2_prev_none_returns_error_missing_prev_data() -> None:
    """AC2: prev=None -> status='error', error_code=='E_MISSING_PREV_DATA'.
    FAILS pre-GREEN: function does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    r = _verify_spec_ac_dsl(_ctx(), None)

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_MISSING_PREV_DATA", (
        f"expected 'E_MISSING_PREV_DATA', got {r.error_code!r}"
    )


# ─── AC3 — HAL_AC_DSL_GATE=0 → PASS-through + gate_disabled event ──────────


def test_ac3_gate_disabled_env_kill_switch_emits_event(tmp_path, monkeypatch) -> None:
    """AC3: HAL_AC_DSL_GATE=0 -> status='ok' AND a 'gate_disabled' event with
    gate=='HAL_AC_DSL_GATE'. FAILS pre-GREEN: function does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    monkeypatch.setenv("HAL_AC_DSL_GATE", "0")
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_valid.md", _VALID_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"
    payloads = _payloads(log, "gate_disabled")
    assert len(payloads) == 1, f"expected 1 gate_disabled event, got {payloads!r}"
    assert payloads[0].get("gate") == "HAL_AC_DSL_GATE", (
        f"expected gate='HAL_AC_DSL_GATE', got {payloads[0]!r}"
    )


# ─── AC4 — valid spec → PASS + spec_ac_dsl_ok event with ac_count ──────────


def test_ac4_valid_spec_emits_spec_ac_dsl_ok_with_ac_count(tmp_path, monkeypatch) -> None:
    """AC4: spec with 2 compilable AC-checks -> status='ok' AND a
    'spec_ac_dsl_ok' event with ac_count==2. FAILS pre-GREEN: function
    does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_valid.md", _VALID_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected 'ok', got {r.status!r} (error_code={r.error_code!r})"
    payloads = _payloads(log, "spec_ac_dsl_ok")
    assert len(payloads) == 1, f"expected 1 spec_ac_dsl_ok event, got {payloads!r}"
    assert payloads[0].get("ac_count") == 2, (
        f"expected ac_count==2, got {payloads[0]!r}"
    )


# ─── AC5 — uncompilable spec, warn-only default → PASS-through + warn event ─


def test_ac5_uncompilable_spec_warn_only_passthrough_with_reasons(
    tmp_path, monkeypatch
) -> None:
    """AC5: uncompilable spec, defaults (enforce off) -> status='ok' AND a
    'spec_ac_dsl_warn' event with non-empty 'reasons'. FAILS pre-GREEN:
    function does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_uncompilable.md", _UNCOMPILABLE_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    payloads = _payloads(log, "spec_ac_dsl_warn")
    assert len(payloads) == 1, f"expected 1 spec_ac_dsl_warn event, got {payloads!r}"
    reasons = payloads[0].get("reasons") or []
    assert reasons, f"expected non-empty aggregated reasons, got {reasons!r}"
    assert any(":" in str(reason) for reason in reasons), (
        f"expected per-AC 'ACid: reason' formatted entries in aggregated "
        f"reasons, got {reasons!r}"
    )


# ─── AC6 — uncompilable spec + ENFORCE=1 → FAIL E_SPEC_AC_UNCOMPILABLE ─────


def test_ac6_uncompilable_spec_enforce_on_returns_error(tmp_path, monkeypatch) -> None:
    """AC6: uncompilable spec with HAL_AC_DSL_GATE_ENFORCE=1 -> status='error',
    error_code=='E_SPEC_AC_UNCOMPILABLE'. FAILS pre-GREEN: function does not
    exist (and enforce path unimplemented)."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "1")
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_uncompilable_enforce.md", _UNCOMPILABLE_SPEC)
    r = _verify_spec_ac_dsl(_ctx_no_repair(), _prev(f))

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE", (
        f"expected 'E_SPEC_AC_UNCOMPILABLE', got {r.error_code!r}"
    )


# ─── AC7 — enforce-FAIL result.data carries findings_structured shape ──────


def test_ac7_enforce_fail_forwarded_data_has_findings_structured(
    tmp_path, monkeypatch
) -> None:
    """AC7: same enforce-FAIL as AC6, StepResult.data['findings_structured']
    is a non-empty list whose entries carry 'line'/'rule_id'/'evidence' keys.
    FAILS pre-GREEN: function does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "1")
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_uncompilable_enforce2.md", _UNCOMPILABLE_SPEC)
    r = _verify_spec_ac_dsl(_ctx_no_repair(), _prev(f))

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    findings = r.data["findings_structured"]
    assert findings, f"expected non-empty findings_structured, got {findings!r}"
    for finding in findings:
        assert "line" in finding, f"finding missing 'line': {finding!r}"
        assert "rule_id" in finding, f"finding missing 'rule_id': {finding!r}"
        assert "evidence" in finding, f"finding missing 'evidence': {finding!r}"


# ─── AC8 — admit() raises → warn-only PASS-through + driver_error event ────


def test_ac8_admit_exception_warn_only_passthrough_driver_error(
    tmp_path, monkeypatch
) -> None:
    """AC8: admit() raises (monkeypatched), defaults (enforce off) ->
    status='ok' AND a 'spec_ac_dsl_driver_error' event with 'error'.
    FAILS pre-GREEN: function does not exist (never reaches the monkeypatch)."""
    import ac_dsl  # deferred, dependency of the UUT — allowed to monkeypatch
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    def _boom(_spec_text):
        raise RuntimeError("admit boom")

    monkeypatch.setattr(ac_dsl, "admit", _boom)

    f = _write(tmp_path, "spec_valid_for_driver_error.md", _VALID_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    payloads = _payloads(log, "spec_ac_dsl_driver_error")
    assert len(payloads) == 1, f"expected 1 spec_ac_dsl_driver_error event, got {payloads!r}"
    assert "error" in payloads[0], f"expected 'error' key, got {payloads[0]!r}"


# ─── AC9 — admit() raises + ENFORCE=1 → fail-closed E_SPEC_AC_UNCOMPILABLE ──


def test_ac9_admit_exception_enforce_on_fails_closed(tmp_path, monkeypatch) -> None:
    """AC9: admit() raises (monkeypatched) with HAL_AC_DSL_GATE_ENFORCE=1 ->
    status='error', error_code=='E_SPEC_AC_UNCOMPILABLE' (fail-closed, lesson
    #221). FAILS pre-GREEN: function does not exist."""
    import ac_dsl  # deferred
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "1")
    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    def _boom(_spec_text):
        raise RuntimeError("admit boom")

    monkeypatch.setattr(ac_dsl, "admit", _boom)

    f = _write(tmp_path, "spec_valid_for_driver_error_enforce.md", _VALID_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "error", f"expected 'error', got {r.status!r}"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE", (
        f"expected 'E_SPEC_AC_UNCOMPILABLE', got {r.error_code!r}"
    )


# ─── AC10 — flags_catalog registration ──────────────────────────────────────


def test_ac10_flags_catalog_has_gate_and_enforce_flags() -> None:
    """AC10: flags_catalog.FLAGS contains HAL_AC_DSL_GATE (kind='gate',
    default='1') and HAL_AC_DSL_GATE_ENFORCE (kind='flag', default='0').
    FAILS pre-GREEN: keys absent."""
    import flags_catalog  # deferred

    flags = flags_catalog.FLAGS

    assert "HAL_AC_DSL_GATE" in flags, (
        f"HAL_AC_DSL_GATE missing from FLAGS; got keys: {sorted(flags.keys())[:5]}..."
    )
    gate_entry = flags["HAL_AC_DSL_GATE"]
    assert gate_entry.get("kind") == "gate", f"expected kind='gate', got {gate_entry!r}"
    assert gate_entry.get("default") == "1", f"expected default='1', got {gate_entry!r}"

    assert "HAL_AC_DSL_GATE_ENFORCE" in flags, (
        f"HAL_AC_DSL_GATE_ENFORCE missing from FLAGS; got keys: "
        f"{sorted(flags.keys())[:5]}..."
    )
    enforce_entry = flags["HAL_AC_DSL_GATE_ENFORCE"]
    assert enforce_entry.get("kind") == "flag", f"expected kind='flag', got {enforce_entry!r}"
    assert enforce_entry.get("default") == "0", f"expected default='0', got {enforce_entry!r}"


# ─── AC11 — error_codes registration ────────────────────────────────────────


def test_ac11_error_codes_has_e_spec_ac_uncompilable() -> None:
    """AC11: error_codes.ERROR_CODES contains 'E_SPEC_AC_UNCOMPILABLE'.
    FAILS pre-GREEN: key absent."""
    import error_codes  # deferred

    assert "E_SPEC_AC_UNCOMPILABLE" in error_codes.ERROR_CODES, (
        f"E_SPEC_AC_UNCOMPILABLE missing from ERROR_CODES; got keys: "
        f"{sorted(error_codes.ERROR_CODES.keys())[:5]}..."
    )


# ─── AC12 — spec with no `### AC-checks` section → warn-only PASS-through ──


def test_ac12_no_ac_checks_section_warn_passthrough_with_reason(
    tmp_path, monkeypatch
) -> None:
    """AC12: spec with no '### AC-checks' section -> status='ok' (warn-only
    pass-through, rollout signal not a break) AND a 'spec_ac_dsl_warn' event
    whose reasons mention 'AC-checks section not found'. FAILS pre-GREEN:
    function does not exist."""
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    log = _CaptureEventLog()
    _patch_telemetry(monkeypatch, log)

    f = _write(tmp_path, "spec_no_ac_checks.md", _NO_AC_CHECKS_SECTION_SPEC)
    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r} (error_code={r.error_code!r})"
    payloads = _payloads(log, "spec_ac_dsl_warn")
    assert len(payloads) == 1, f"expected 1 spec_ac_dsl_warn event, got {payloads!r}"
    reasons = payloads[0].get("reasons") or []
    assert any("AC-checks section not found" in str(reason) for reason in reasons), (
        f"expected a reason mentioning 'AC-checks section not found', got {reasons!r}"
    )
