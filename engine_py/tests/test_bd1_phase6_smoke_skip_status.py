"""RED tests — bd#1: phase_6_smoke must not report `ok` for a check it never ran.

`_run_smoke` has two branches that return before the smoke script is ever
executed: the script is absent from this tree (the universal case for every
non-upstream user — the script ships in the upstream HALForge tree, not here),
and `zsh` is not installed. Both currently return `status="ok"`, so a phase
that structurally did not run is indistinguishable from one that passed.

`"skip"` is already a legal `StepResult.status` (`contracts.py:87`) and is
already handled as a non-failure end to end — `run.py:245` exits 0 on it,
`run.py:221` records no error code, `lib/phase_sentinel.py:50` caches it,
`engine.py:483` does not interrupt the chain on it. So this is a change of
report, not of outcome: nothing downstream reddens.

AC1  script absent          → status=="skip", data["skipped"]=="smoke_script_absent"
AC2  zsh absent             → status=="skip", data["skipped"]=="zsh_unavailable"
AC3  ran and passed         → status=="ok" (negative control: a real pass is
                              still a pass, so a GREEN cannot satisfy AC1/AC2
                              by returning "skip" unconditionally)
AC4  ran and failed         → status=="error" (second negative control: a real
                              failure must not be downgraded to a skip)
AC5  the skip survives the engine end to end — the workflow's final status is
     "skip", carries no error_code, and does not set the escalate flag
AC6  "skip" is in the StepResult status vocabulary and run.py treats it as a
     success exit
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_6_smoke import phase_6_smoke_workflow  # noqa: E402
from bytedigger_engine.workflows import phase_6_smoke as _p6s  # noqa: E402

_SMOKE_REL = "USER/skills/HALForge/tests/phase-6-signal-smoke.sh"


def _ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"current_worktree_path": str(tmp_path)},
        question="",
        session_id="test-bd1",
        persona="hal",
        framework=None,
        domain=None,
    )


def _engine() -> WorkflowEngine:
    eng = WorkflowEngine()
    eng.register("phase_6_smoke", phase_6_smoke_workflow())
    return eng


def _install_script(tmp_path: Path) -> Path:
    script = tmp_path / _SMOKE_REL
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/zsh\necho STUB\n")
    return script


def _stub_zsh_present(monkeypatch) -> None:
    monkeypatch.setattr(_p6s, "shutil", types.SimpleNamespace(which=lambda _: "/bin/zsh"))


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_absent_script_reports_skip_not_ok(tmp_path):
    """AC1: the branch every outside user takes reports `skip`, not `ok`.

    Pre-GREEN FAIL: phase_6_smoke.py:65 returns status="ok".
    """
    result, _ = _engine().execute("phase_6_smoke", _ctx(tmp_path))

    assert result.status == "skip", (
        f"expected status=='skip' for a phase whose subject is absent (the "
        f"smoke script ships in the upstream tree, not this one); actual "
        f"{result.status!r}. `ok` reports a pass for a check that never ran."
    )
    assert result.data.get("skipped") == "smoke_script_absent", (
        f"expected data['skipped']=='smoke_script_absent'; actual {result.data!r}"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

def test_ac2_absent_zsh_reports_skip_not_ok(tmp_path, monkeypatch):
    """AC2: the zsh-absent branch reports `skip` too — same argument verbatim.

    bd#102 deliberately kept `zsh` out of `HOST_TOOLS` so this degradation path
    would stay visible (docs/host-requirements.md). Reporting it as `ok`
    defeats that from the other side.

    Pre-GREEN FAIL: phase_6_smoke.py:73 returns status="ok".
    """
    _install_script(tmp_path)

    calls = []

    def fake_bounded_run(*args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="STATUS=PASS\n")

    monkeypatch.setattr(_p6s, "bounded_run", fake_bounded_run)
    monkeypatch.setattr(_p6s, "shutil", types.SimpleNamespace(which=lambda _: None))

    result, _ = _engine().execute("phase_6_smoke", _ctx(tmp_path))

    assert result.status == "skip", (
        f"expected status=='skip' when zsh is unavailable; actual {result.status!r}"
    )
    assert result.data.get("skipped") == "zsh_unavailable", (
        f"expected data['skipped']=='zsh_unavailable'; actual {result.data!r}"
    )
    assert calls == [], (
        f"bounded_run must not be spawned when zsh is absent; called {len(calls)} time(s)"
    )


# ── AC3: negative control — a real pass stays `ok` ───────────────────────────

def test_ac3_executed_and_passing_smoke_still_reports_ok(tmp_path, monkeypatch):
    """AC3: a smoke run that actually happened and passed is still `ok`.

    Without this, a GREEN could satisfy AC1 and AC2 by returning "skip" from
    every path, and "no phase ever reports a false pass" would be true only
    because no phase ever reports a pass.

    Pre-GREEN: PASSES already — pinned so the fix cannot regress it.
    """
    _install_script(tmp_path)
    _stub_zsh_present(monkeypatch)
    monkeypatch.setattr(
        _p6s, "bounded_run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="PASS: t1\nPASS: t2\nSTATUS=PASS\n",
        ),
    )

    result, _ = _engine().execute("phase_6_smoke", _ctx(tmp_path))

    assert result.status == "ok", (
        f"expected status=='ok' for a smoke run that executed and passed; "
        f"actual {result.status!r}"
    )
    assert result.data.get("skipped") is None, (
        f"expected no 'skipped' marker on an executed run; actual {result.data!r}"
    )
    assert result.data.get("passed") == 2, f"actual {result.data!r}"


# ── AC4: negative control — a real failure stays `error` ─────────────────────

def test_ac4_executed_and_failing_smoke_still_reports_error(tmp_path, monkeypatch):
    """AC4: a smoke run that executed and failed is still `error`, not `skip`.

    Pre-GREEN: PASSES already — pinned so the fix cannot launder a failure
    into a skip, which would be a strictly worse version of this same bug.
    """
    _install_script(tmp_path)
    _stub_zsh_present(monkeypatch)
    monkeypatch.setattr(
        _p6s, "bounded_run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="PASS: t1\nFAIL: t2 -- boom\nSTATUS=FAIL\n",
        ),
    )

    result, _ = _engine().execute("phase_6_smoke", _ctx(tmp_path))

    assert result.status == "error", (
        f"expected status=='error' for an executed, failing smoke run; actual "
        f"{result.status!r}"
    )
    assert result.error_code == "E_SMOKE_FAILED", f"actual {result.error_code!r}"


# ── AC5: the skip survives the engine, and is not an error ───────────────────

def test_ac5_engine_final_result_is_skip_without_error_code(tmp_path):
    """AC5 (§1l — the production side-effect, not a unit return): the value a
    caller of the workflow actually sees. `engine.execute` must hand back
    status=="skip" with no error_code and no escalation, i.e. the run is a
    success that is visibly not a pass.

    Pre-GREEN FAIL: the engine faithfully returns the step's "ok".
    """
    result, _ctx_out = _engine().execute("phase_6_smoke", _ctx(tmp_path))

    assert result.status == "skip", f"actual {result.status!r}"
    assert result.error_code is None, (
        f"a skip is not an error — expected error_code None; actual "
        f"{result.error_code!r}"
    )
    assert result.error is None, f"actual {result.error!r}"
    assert result.status in ("ok", "skip"), (
        "the run must still be a success by run.py's own predicate — the fix "
        "changes what is reported, not whether the pipeline passes"
    )


# ── AC6: the vocabulary and the exit mapping already support this ────────────

def test_ac6_skip_is_a_legal_status_and_a_success_exit():
    """AC6: `skip` needs no new sentinel — it is already in the StepResult
    vocabulary, and `run.py` already maps it to exit 0.

    Pre-GREEN: PASSES already. It is here because the fix's whole safety
    argument rests on these two facts; if either ever changes, AC1/AC2 stop
    being a report-only change and this test says so at the point of breakage
    rather than in production.
    """
    StepResult(status="skip", data=None, duration_ms=0, step_name="probe")

    with pytest.raises(ValueError):
        StepResult(status="nonsense", data=None, duration_ms=0, step_name="probe")

    run_py = (HERE.parent / "bytedigger_engine" / "run.py").read_text(encoding="utf-8")
    assert 'return 0 if result.status in ("ok", "skip") else 1' in run_py, (
        "expected run.py to exit 0 for a skip; if this mapping changed, the "
        "phase_6_smoke skip would start failing outside users' pipelines"
    )
