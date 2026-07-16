"""RED tests for GH747 — phase_4.5 spec-gate pre-flight batch aggregator.

Frozen spec: SHARED/memory/Decisions/2026-07-13_GH747_phase45_preflight_gate_batch_spec.md

UUT (does NOT exist yet — every test FAILS pre-GREEN):
  - `_collect_spec_gate_findings(ctx, prev, spec_path, hal_root, cfg, git_cwd)`
    -> list[dict], a named helper (§1aa) in workflows/phase_45_spec.py.
  - `_verify_spec_preflight_batch(ctx, prev) -> StepResult`, a new step in
    workflows/phase_45_spec.py that runs the spec_lint + spec_cite_lint
    drivers in ONE pass and dispatches a single combined directed-repair call.

§1q/D1CF5FDF non-collectable-RED discipline: the UUT symbols do not exist
yet. This file imports the MODULE at top level (`from workflows import
phase_45_spec` — the module itself already exists) and references the
not-yet-existing attributes (`phase_45_spec._verify_spec_preflight_batch`,
`phase_45_spec._collect_spec_gate_findings`) INSIDE each test body, so the
file COLLECTS cleanly and every test FAILS at call/assert time (AttributeError
or assertion), never at collection time. No module-level sys.path.insert /
`from conftest import` — sys.path wiring is provided by tests/conftest.py's
conftest-import-time singleton (§1q / 81F97F3D gate).

§1i: no singleton/time-dependent resources. AC7 (re-entry) pre-stages the
"post-repair clean spec" state deterministically (swap the bounded_run mock
between the two direct calls) instead of racing on any timing/lock.

§1l stub-passability: the UUT (`_verify_spec_preflight_batch` /
`_collect_spec_gate_findings`) is NEVER patched or mocked. Only INFRA
collaborators are patched: `bounded_run`, `attempt_directed_repair`,
`_emit_safe`, `_directed_repair_enabled`. Every assertion is on a real
returned value / real captured payload / real mock call-kwargs.
"""
from __future__ import annotations

import json
import types
from unittest.mock import patch

from workflows import phase_45_spec
from contracts import StepResult


# ─── shared fixtures / helpers ──────────────────────────────────────────────


class FakeCtx:
    """Minimal WorkflowContext stand-in (mirrors sibling spec_cite/spec_lint
    RED tests' FakeCtx pattern)."""

    def __init__(self, org_config: dict | None = None) -> None:
        self.org_config = org_config or {}


def _make_prev(spec_path: str, **extra) -> StepResult:
    return StepResult(
        status="ok",
        data={"cycle": 1, "spec_path": spec_path, **extra},
        duration_ms=0,
        step_name="verify_spec_scope_inverse",
    )


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_LINT_STDOUT_VIOLATION = "spec.md:10: missing reachability trace for foo()\n"
_CITE_JSON_VIOLATION = json.dumps({
    "findings": [
        {"status": "unresolved_symbol", "symbol": "bogus_symbol_xyz", "file": "bar.py"},
    ]
})
_CITE_JSON_CLEAN = json.dumps({"findings": []})


def _dispatch_bounded_run(lint_rc: int, cite_rc: int, lint_stdout: str = "", cite_stdout: str = ""):
    """argv-dispatching side_effect for the patched `bounded_run` — routes on
    whether the invoked driver path/argv contains the lint_spec.py or the
    spec-cite-lint.py driver, mirroring how `_verify_spec_lint` /
    `_verify_spec_cite_lint` each invoke bounded_run with their own driver
    argv (L1668 / L1856)."""

    def _fake(cmd, **kwargs):
        argv_str = " ".join(str(c) for c in cmd)
        if "lint_spec" in argv_str:
            return _FakeProc(lint_rc, stdout=lint_stdout)
        if "spec-cite-lint" in argv_str:
            return _FakeProc(cite_rc, stdout=cite_stdout)
        raise AssertionError(f"unexpected bounded_run argv: {cmd!r}")

    return _fake


def _capture_emit_factory():
    captured: list[tuple] = []

    def _capture(event_type, payload, severity="info"):
        captured.append((event_type, payload, severity))

    return captured, _capture


# ─── AC1 ─────────────────────────────────────────────────────────────────


def test_ac1_collect_spec_gate_findings_returns_both_gates_in_one_call(tmp_path) -> None:
    """AC1: with a spec that fails BOTH drivers, _collect_spec_gate_findings
    returns a list containing >=1 rule=='spec-lint' finding AND >=1
    rule=='spec-cite-lint' finding — both collected in one call, no early
    return between the two driver invocations."""
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    with patch.object(
        phase_45_spec,
        "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=1,
            lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_VIOLATION,
        ),
    ):
        findings = phase_45_spec._collect_spec_gate_findings(
            ctx, prev, str(spec_path), str(tmp_path), ctx.org_config, str(tmp_path),
        )

    assert isinstance(findings, list), f"expected list, got {type(findings)!r}"
    rules = [f.get("rule") for f in findings]
    assert "spec-lint" in rules, f"expected a spec-lint finding, got rules={rules!r}"
    assert "spec-cite-lint" in rules, f"expected a spec-cite-lint finding, got rules={rules!r}"


# ─── AC2 ─────────────────────────────────────────────────────────────────


def test_ac2_directed_repair_dispatched_exactly_once_with_combined_findings(
    tmp_path, monkeypatch
) -> None:
    """AC2: same double-violation input, flag ON, repair enabled ->
    _verify_spec_preflight_batch dispatches EXACTLY ONE attempt_directed_repair
    call with gate=='spec_preflight_batch' whose findings kwarg contains BOTH
    E_SPEC_LINT_FAIL and E_SPEC_CITE_LINT_FAIL error codes."""
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=False, final=None, attempts=2, exhausted_reason="model_unavailable")

    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=1,
            lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_VIOLATION,
        ),
    ), patch.object(phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair):
        phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert len(calls) == 1, f"expected exactly 1 directed-repair call, got {len(calls)}"
    assert calls[0].get("gate") == "spec_preflight_batch", (
        f"expected gate='spec_preflight_batch', got {calls[0].get('gate')!r}"
    )
    codes = {f.get("error_code") for f in calls[0].get("findings", [])}
    assert {"E_SPEC_LINT_FAIL", "E_SPEC_CITE_LINT_FAIL"}.issubset(codes), (
        f"expected both error codes in combined findings, got {codes!r}"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────


def test_ac3_single_batch_event_with_both_gates_fired(tmp_path, monkeypatch) -> None:
    """AC3: same double-violation input -> exactly one
    'spec_preflight_gate_batch' event, gates_fired == {'spec-lint',
    'spec-cite-lint'} (set-equal), codes contains both error codes,
    findings_n>=2."""
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "0")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    captured, _capture = _capture_emit_factory()
    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=1,
            lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_VIOLATION,
        ),
    ), patch.object(phase_45_spec, "_emit_safe", side_effect=_capture):
        phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    batch_events = [c for c in captured if c[0] == "spec_preflight_gate_batch"]
    assert len(batch_events) == 1, (
        f"expected exactly 1 'spec_preflight_gate_batch' event, got {captured!r}"
    )
    payload = batch_events[0][1]
    assert set(payload.get("gates_fired", [])) == {"spec-lint", "spec-cite-lint"}, (
        f"expected gates_fired=={{'spec-lint','spec-cite-lint'}}, got {payload!r}"
    )
    assert {"E_SPEC_LINT_FAIL", "E_SPEC_CITE_LINT_FAIL"}.issubset(set(payload.get("codes", []))), (
        f"expected both codes in payload, got {payload!r}"
    )
    assert payload.get("findings_n", 0) >= 2, f"expected findings_n>=2, got {payload!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────


def test_ac4_flag_off_is_noop_passthrough_zero_repair(tmp_path, monkeypatch) -> None:
    """AC4: flag OFF (default, HAL_SPEC_PREFLIGHT_BATCH unset) ->
    status=='ok', emits 'gate_disabled' (NOT 'spec_preflight_gate_batch'),
    and dispatches ZERO attempt_directed_repair — legacy staged steps 8&9
    remain the enforcement path."""
    monkeypatch.delenv("HAL_SPEC_PREFLIGHT_BATCH", raising=False)
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    captured, _capture = _capture_emit_factory()
    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=True, final=None, attempts=1, exhausted_reason=None)

    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=1,
            lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_VIOLATION,
        ),
    ), patch.object(phase_45_spec, "_emit_safe", side_effect=_capture), patch.object(
        phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair,
    ):
        result = phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert result.status == "ok", f"expected status=='ok', got {result.status!r}"
    assert isinstance(result.data, dict), f"expected result.data to be a dict, got {type(result.data)!r}"
    names = [c[0] for c in captured]
    assert "gate_disabled" in names, f"expected 'gate_disabled' event, got {names!r}"
    assert "spec_preflight_gate_batch" not in names, (
        f"expected NO 'spec_preflight_gate_batch' event when flag OFF, got {names!r}"
    )
    assert len(calls) == 0, f"expected ZERO directed-repair dispatch when flag OFF, got {len(calls)}"


# ─── AC5 ─────────────────────────────────────────────────────────────────


def test_ac5_clean_spec_flag_on_zero_repair_zero_findings(tmp_path, monkeypatch) -> None:
    """AC5: clean spec (both drivers rc==0), flag ON -> status=='ok', ZERO
    attempt_directed_repair dispatch, event findings_n==0 / severity 'info'."""
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    captured, _capture = _capture_emit_factory()
    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=True, final=None, attempts=1, exhausted_reason=None)

    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(lint_rc=0, cite_rc=0, cite_stdout=_CITE_JSON_CLEAN),
    ), patch.object(phase_45_spec, "_emit_safe", side_effect=_capture), patch.object(
        phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair,
    ):
        result = phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert result.status == "ok", f"expected status=='ok', got {result.status!r}; error={result.error!r}"
    assert isinstance(result.data, dict), f"expected result.data to be a dict, got {type(result.data)!r}"
    assert len(calls) == 0, f"expected ZERO directed-repair dispatch on clean spec, got {len(calls)}"
    batch_events = [c for c in captured if c[0] == "spec_preflight_gate_batch"]
    assert batch_events, f"expected a 'spec_preflight_gate_batch' event, got {captured!r}"
    payload, severity = batch_events[0][1], batch_events[0][2]
    assert payload.get("findings_n") == 0, f"expected findings_n==0, got {payload!r}"
    assert severity == "info", f"expected severity=='info', got {severity!r}"


# ─── AC6 ─────────────────────────────────────────────────────────────────


def test_ac6_single_gate_violation_dispatches_only_that_code(tmp_path, monkeypatch) -> None:
    """AC6: only spec_lint rc==1 (cite rc==0), flag ON -> one dispatch,
    gate=='spec_preflight_batch', findings contain ONLY E_SPEC_LINT_FAIL,
    gates_fired==['spec-lint'] (batch still fires correctly at N==1)."""
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    calls: list[dict] = []

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=False, final=None, attempts=2, exhausted_reason="model_unavailable")

    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=0, lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_CLEAN,
        ),
    ), patch.object(phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair):
        phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert len(calls) == 1, f"expected exactly 1 directed-repair call, got {len(calls)}"
    assert calls[0].get("gate") == "spec_preflight_batch", f"got {calls[0].get('gate')!r}"
    codes = {f.get("error_code") for f in calls[0].get("findings", [])}
    assert codes == {"E_SPEC_LINT_FAIL"}, f"expected ONLY E_SPEC_LINT_FAIL, got {codes!r}"
    rules = {f.get("rule") for f in calls[0].get("findings", [])}
    assert rules == {"spec-lint"}, f"expected gates_fired == {{'spec-lint'}}, got {rules!r}"


# ─── AC7 ─────────────────────────────────────────────────────────────────


def test_ac7_reentry_after_convergence_dispatches_zero_additional_repair(
    tmp_path, monkeypatch
) -> None:
    """AC7 (§1ab re-entry, pre-staged deterministically per §1i — NEVER a
    timing race): first pass over a violating spec converges via a mocked
    attempt_directed_repair (1 dispatch). Re-invoking
    _verify_spec_preflight_batch a SECOND time over the SAME ctx/prev, with
    the driver state pre-staged clean (both rc==0, simulating the
    already-repaired spec) -> status=='ok', ZERO additional repair dispatch
    (cumulative calls stays at 1; idempotent re-entry, no double-charge)."""
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# spec\n", encoding="utf-8")
    ctx = FakeCtx()
    prev = _make_prev(str(spec_path))

    calls: list[dict] = []
    final_ok = StepResult(status="ok", data={"repaired": True}, duration_ms=0, step_name="verify_spec_preflight_batch")

    def _fake_repair(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(converged=True, final=final_ok, attempts=1, exhausted_reason=None)

    # First pass: violating spec, mocked convergent repair -> 1 dispatch.
    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(
            lint_rc=1, cite_rc=1, lint_stdout=_LINT_STDOUT_VIOLATION, cite_stdout=_CITE_JSON_VIOLATION,
        ),
    ), patch.object(phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair):
        first_result = phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert first_result.status == "ok", f"expected first pass status=='ok', got {first_result.status!r}"
    assert len(calls) == 1, f"expected 1 dispatch after first pass, got {len(calls)}"

    # Second pass, SAME ctx/prev: pre-staged clean driver state (post-repair).
    with patch.object(
        phase_45_spec, "bounded_run",
        side_effect=_dispatch_bounded_run(lint_rc=0, cite_rc=0, cite_stdout=_CITE_JSON_CLEAN),
    ), patch.object(phase_45_spec, "attempt_directed_repair", side_effect=_fake_repair):
        second_result = phase_45_spec._verify_spec_preflight_batch(ctx, prev)

    assert second_result.status == "ok", f"expected second pass status=='ok', got {second_result.status!r}"
    assert len(calls) == 1, (
        f"expected ZERO additional repair dispatch on re-entry (cumulative stays at 1), got {len(calls)}"
    )
