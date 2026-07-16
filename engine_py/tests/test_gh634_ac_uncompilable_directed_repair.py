"""RED tests for GH634 — E_SPEC_AC_UNCOMPILABLE directed-repair pre-stage.

Spec: SHARED/memory/Decisions/2026-07-12_GH634_ac_uncompilable_directed_repair_spec.md

AC1-AC11 each map to one test. AC1/AC2/AC3/AC4/AC6/AC8 FAIL pre-GREEN because
the enforce branch of `_verify_spec_ac_dsl` (workflows/phase_45_spec.py) does
NOT yet call `attempt_directed_repair` — it returns the terminal directly.
AC5/AC7/AC9/AC10/AC11 are guard/regression ACs and may already PASS
pre-GREEN — that is expected (see report).

Deferred-import discipline (§1q-extension D1CF5FDF / §1q 81F97F3D): the UUT
`_verify_spec_ac_dsl` already exists, but we still defer the import inside
each test body to match sibling-test style and avoid any top-level coupling.
No module-level sys.path manipulation, no `from conftest import ...` — the
conftest-import-time singleton already puts engine_py root + workflows/ on
sys.path.

Stub-passability guard (§1l/7AD3D393): we NEVER patch `_verify_spec_ac_dsl`
itself (the UUT). We only patch its collaborators
`phase_45_spec.attempt_directed_repair` / `phase_45_spec._directed_repair_enabled`
(imported INTO the phase_45_spec module namespace) plus telemetry — exactly
mirroring how the 5 existing directed_repair call-sites are tested elsewhere.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from contracts import StepResult  # already exists — safe top-level import


# ─── event-log capture helpers (mirror test_phase_45_ac_dsl_wiring_GH517A2.py) ─


class _CaptureEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type, payload, run_id):
        self.events.append((event_type, payload, run_id))


class _StubRunCtx:
    def __init__(self, event_log):
        self.event_log = event_log
        self.run_id = "gh634-test-run"


def _patch_telemetry(monkeypatch, log: _CaptureEventLog | None = None):
    import telemetry_ctx  # deferred — reached via workflows/ on sys.path

    stub = _StubRunCtx(log) if log is not None else _StubRunCtx(_CaptureEventLog())
    monkeypatch.setattr(telemetry_ctx, "get_current_run", lambda: stub)


def _ctx(**org_config_extra) -> SimpleNamespace:
    return SimpleNamespace(org_config={"complexity": "SIMPLE", **org_config_extra})


def _prev(spec_path: Path, cycle: int = 1, **extra) -> StepResult:
    data: dict = {"spec_path": str(spec_path), "cycle": cycle, **extra}
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


# Uncompilable spec: check block references an unknown check type — one
# REJECTed AC (AC1). Identical fixture to the sibling GH517A2 test file.
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


def _make_spy(monkeypatch, return_value):
    """Patch phase_45_spec.attempt_directed_repair; returns a mutable capture dict."""
    import phase_45_spec

    captured: dict = {"called": 0, "kwargs": None}

    def _spy(**kwargs):
        captured["called"] += 1
        captured["kwargs"] = kwargs
        return return_value

    monkeypatch.setattr(phase_45_spec, "attempt_directed_repair", _spy)
    return captured


def _run_enforce(tmp_path, monkeypatch, ctx=None):
    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "1")
    monkeypatch.setenv("HAL_DIRECTED_REPAIR", "1")
    _patch_telemetry(monkeypatch)
    f = _write(tmp_path, "spec_uncompilable_enforce.md", _UNCOMPILABLE_SPEC)
    return _verify_spec_ac_dsl(ctx or _ctx(), _prev(f)), f


# ─── AC1 — attempt_directed_repair invoked once with expected kwargs ───────


def test_ac1_attempt_directed_repair_invoked_once_with_expected_kwargs(
    tmp_path, monkeypatch
) -> None:
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=False, attempts=2, final=None)
    )
    ctx = _ctx()
    r, f = _run_enforce(tmp_path, monkeypatch, ctx=ctx)

    assert captured["called"] == 1, (
        f"expected attempt_directed_repair called exactly once, got {captured['called']}"
    )
    kwargs = captured["kwargs"]
    assert kwargs is not None
    assert kwargs.get("gate") == "spec_ac_dsl", f"expected gate='spec_ac_dsl', got {kwargs!r}"
    assert kwargs.get("artifact_path") == str(f), (
        f"expected artifact_path={str(f)!r}, got {kwargs.get('artifact_path')!r}"
    )
    assert kwargs.get("repair_step_name") == "repair_spec_ac_dsl", (
        f"expected repair_step_name='repair_spec_ac_dsl', got {kwargs!r}"
    )
    assert kwargs.get("ctx") is ctx, f"expected ctx to be the SAME object, got {kwargs.get('ctx')!r}"
    # sanity: the terminal error_code is still what we expect on exhaustion
    assert r.status == "error"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE"


# ─── AC2 — findings kwarg has one entry per REJECTed AC, evidence format ───


def test_ac2_findings_kwarg_has_one_entry_per_rejected_ac_with_evidence(
    tmp_path, monkeypatch
) -> None:
    import ac_dsl  # deferred — to compute the expected reason independently
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=False, attempts=2, final=None)
    )
    r, f = _run_enforce(tmp_path, monkeypatch)

    assert captured["called"] == 1, "attempt_directed_repair was never invoked"
    findings = captured["kwargs"]["findings"]

    expected_report = ac_dsl.admit(f.read_text(encoding="utf-8-sig"))
    expected_rejects = {
        ac_id: info["reason"]
        for ac_id, info in expected_report.per_ac.items()
        if info.get("status") == "REJECT"
    }
    assert expected_rejects, "fixture setup bug: expected at least one REJECT AC"
    assert len(findings) == len(expected_rejects), (
        f"expected {len(expected_rejects)} findings entries, got {len(findings)}: {findings!r}"
    )
    got_evidence = {finding["evidence"] for finding in findings}
    expected_evidence = {f"{ac_id}: {reason}" for ac_id, reason in expected_rejects.items()}
    assert got_evidence == expected_evidence, (
        f"expected evidence set {expected_evidence!r}, got {got_evidence!r}"
    )


# ─── AC3 — convergence: returns rr.final, not the terminal ─────────────────


def test_ac3_convergence_returns_final_step_result_not_terminal(
    tmp_path, monkeypatch
) -> None:
    from lib.directed_repair import DirectedRepairResult  # deferred

    final = StepResult(
        status="ok", data={"marker": "repaired"}, duration_ms=0, step_name="repaired",
    )
    _make_spy(monkeypatch, DirectedRepairResult(converged=True, attempts=1, final=final))
    r, _f = _run_enforce(tmp_path, monkeypatch)

    assert r is final, f"expected the exact converged final StepResult back, got {r!r}"
    assert r.status == "ok"
    assert r.error_code != "E_SPEC_AC_UNCOMPILABLE"


# ─── AC4 — exhaustion: unchanged terminal preserved ────────────────────────


def test_ac4_exhaustion_returns_unchanged_terminal_with_findings_structured(
    tmp_path, monkeypatch
) -> None:
    from lib.directed_repair import DirectedRepairResult  # deferred

    _make_spy(monkeypatch, DirectedRepairResult(converged=False, attempts=2, final=None))
    r, _f = _run_enforce(tmp_path, monkeypatch)

    assert r.status == "error"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE"
    assert getattr(r, "terminal_error_code", None) in (None, "E_SPEC_AC_UNCOMPILABLE") or (
        r.metadata.get("terminal_error_code") == "E_SPEC_AC_UNCOMPILABLE"
        if hasattr(r, "metadata")
        else True
    )
    findings = r.data["findings_structured"]
    assert findings, f"expected non-empty findings_structured, got {findings!r}"


# ─── AC5 — §1ab re-entry: repair disabled -> not called, terminal returned ─


def test_ac5_directed_repair_disabled_skips_repair_returns_terminal(
    tmp_path, monkeypatch
) -> None:
    import phase_45_spec  # deferred

    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=True, attempts=1, final=None)
    )
    monkeypatch.setattr(phase_45_spec, "_directed_repair_enabled", lambda ctx: False)

    r, _f = _run_enforce(tmp_path, monkeypatch)

    assert captured["called"] == 0, (
        f"expected attempt_directed_repair NOT called when repair disabled, "
        f"got called {captured['called']} time(s)"
    )
    assert r.status == "error"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE"


# ─── AC6 — max_attempts kwarg == _repair_cap(cfg) ──────────────────────────


def test_ac6_max_attempts_kwarg_equals_repair_cap(tmp_path, monkeypatch) -> None:
    from lib.directed_repair import DirectedRepairResult, _repair_cap  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=False, attempts=2, final=None)
    )
    ctx = _ctx()
    _run_enforce(tmp_path, monkeypatch, ctx=ctx)

    assert captured["called"] == 1, "attempt_directed_repair was never invoked"
    expected_cap = _repair_cap(ctx.org_config)
    assert captured["kwargs"].get("max_attempts") == expected_cap, (
        f"expected max_attempts=={expected_cap}, got {captured['kwargs'].get('max_attempts')!r}"
    )


# ─── AC7 — §1g single-source: no second loop / no revise_counter added ─────


def test_ac7_no_second_repair_loop_or_revise_counter_added() -> None:
    """Deterministic guard AC — reads the source of `_verify_spec_ac_dsl` and
    asserts it contains no new `while`/`for attempt` loop and no
    `revise_counter` reference. May already PASS pre-GREEN (the current body
    has neither) — that is expected for this guard AC."""
    import inspect

    from phase_45_spec import _verify_spec_ac_dsl  # deferred

    src = inspect.getsource(_verify_spec_ac_dsl)
    assert "revise_counter" not in src, (
        "GH634 must NOT reference revise_counter (single-source §1g violation)"
    )
    assert "for attempt" not in src, (
        "GH634 must NOT introduce a second repair loop (`for attempt ...`)"
    )
    assert "while " not in src, (
        "GH634 must NOT introduce a second repair loop (`while ...`)"
    )


# ─── AC8 — rerun_gate is callable, re-invokes _verify_spec_ac_dsl ──────────


def test_ac8_rerun_gate_reinvokes_verify_spec_ac_dsl(tmp_path, monkeypatch) -> None:
    import phase_45_spec  # deferred
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=False, attempts=2, final=None)
    )

    real_verify = phase_45_spec._verify_spec_ac_dsl
    rerun_calls: list[bool] = []

    def _tracking_verify(ctx, prev):
        rerun_calls.append(True)
        return real_verify(ctx, prev)

    _run_enforce(tmp_path, monkeypatch)

    assert captured["called"] == 1, "attempt_directed_repair was never invoked"
    rerun_gate = captured["kwargs"].get("rerun_gate")
    assert callable(rerun_gate), f"expected rerun_gate to be callable, got {rerun_gate!r}"

    # Swap in the tracking wrapper AFTER capture, then invoke the captured
    # closure — since `rerun_gate` closes over the module-level name
    # `_verify_spec_ac_dsl`, patching the module attribute makes the closure
    # call our tracker on invocation.
    monkeypatch.setattr(phase_45_spec, "_verify_spec_ac_dsl", _tracking_verify)
    rerun_gate()
    assert rerun_calls, "expected rerun_gate() to re-invoke _verify_spec_ac_dsl"


# ─── AC9 — regression: enforce OFF -> ok, repair NOT called ────────────────


def test_ac9_enforce_off_regression_ok_and_repair_not_called(tmp_path, monkeypatch) -> None:
    from phase_45_spec import _verify_spec_ac_dsl  # deferred
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=True, attempts=1, final=None)
    )
    _patch_telemetry(monkeypatch)
    f = _write(tmp_path, "spec_uncompilable_warn.md", _UNCOMPILABLE_SPEC)

    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok", f"expected warn-only 'ok', got {r.status!r}"
    assert captured["called"] == 0, (
        f"expected attempt_directed_repair NOT called with enforce off, "
        f"got called {captured['called']} time(s)"
    )


# ─── AC10 — regression: HAL_AC_DSL_GATE kill-switch off -> no repair ───────


def test_ac10_gate_kill_switch_off_no_repair_no_crash(tmp_path, monkeypatch) -> None:
    from phase_45_spec import _verify_spec_ac_dsl  # deferred
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=True, attempts=1, final=None)
    )
    monkeypatch.setenv("HAL_AC_DSL_GATE", "0")
    monkeypatch.setenv("HAL_AC_DSL_GATE_ENFORCE", "1")
    _patch_telemetry(monkeypatch)
    f = _write(tmp_path, "spec_uncompilable_killswitch.md", _UNCOMPILABLE_SPEC)

    r = _verify_spec_ac_dsl(_ctx(), _prev(f))

    assert r.status == "ok"
    assert captured["called"] == 0, (
        f"expected attempt_directed_repair NOT called with HAL_AC_DSL_GATE=0, "
        f"got called {captured['called']} time(s)"
    )


# ─── AC11 — directed_repair_skip config -> terminal, repair NOT called ─────


def test_ac11_directed_repair_skip_config_falls_to_terminal_not_called(
    tmp_path, monkeypatch
) -> None:
    from lib.directed_repair import DirectedRepairResult  # deferred

    captured = _make_spy(
        monkeypatch, DirectedRepairResult(converged=True, attempts=1, final=None)
    )
    ctx = _ctx(directed_repair_skip=True)
    r, _f = _run_enforce(tmp_path, monkeypatch, ctx=ctx)

    assert captured["called"] == 0, (
        f"expected attempt_directed_repair NOT called with directed_repair_skip=True, "
        f"got called {captured['called']} time(s)"
    )
    assert r.status == "error"
    assert r.error_code == "E_SPEC_AC_UNCOMPILABLE"
