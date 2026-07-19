"""RED tests for GH780 — manifest accessor crashes on non-dict StepResult.data.

Spec: SHARED/memory/Decisions/2026-07-14_A90AB439_gh780_manifest_nondict_xdist_flake_spec.md
Covers AC1-AC7 (§3). AC8 lives in tests/test_engine.py per spec §5.

Do NOT mock the units under test (manifest_from_result, WorkflowEngine,
_manifest_paths_from_result). Only ambient-git infra (_git_changes_vs_head)
and the sentinel-cache read (maybe_read_sentinel) are patched, both via
module-attribute form (import engine; monkeypatch.setattr(engine, ...)).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from contracts import (
    WorkflowContext,
    StepResult,
    StepContract,
    WorkflowDefinition,
)
from engine import WorkflowEngine
import engine
from llm_subprocess import manifest_from_result, _ManifestMissingError


# ─── helpers (copied from test_engine.py per instructions — no cross-import) ─


def make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config=None,
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


def ok_step(name: str, payload):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


def _fake_git_sequence():
    """Returns a callable simulating a concurrent-xdist git delta: 1st call
    empty (pre-step baseline), 2nd call shows a foreign file appearing."""
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] == 1:
            return {}
        return {"SOME/foreign.py": "A"}

    return _fn


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_manifest_from_result_str_data_raises_manifest_missing():
    """AC1: manifest_from_result(StepResult(data='hi')) raises _ManifestMissingError
    (a ValueError subclass — engine's DEFER depends on this)."""
    bad = StepResult(status="ok", data="hi", duration_ms=0, step_name="s1")
    with pytest.raises(_ManifestMissingError) as excinfo:
        manifest_from_result(bad)
    assert isinstance(excinfo.value, ValueError)


# ─── AC2 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_data", [["a"], 42, ""])
def test_ac2_manifest_from_result_every_non_dict_raises_manifest_missing(bad_data):
    bad = StepResult(status="ok", data=bad_data, duration_ms=0, step_name="s1")
    with pytest.raises(_ManifestMissingError):
        manifest_from_result(bad)


# ─── AC3 (regression guard — may legitimately PASS today, 4C03CCED) ──────────


def test_ac3_valid_dict_manifest_still_returns_paths_and_source():
    good = StepResult(
        status="ok",
        data={"worker_written_paths": ["a/b.py"], "manifest_source": "harness_tool_record"},
        duration_ms=0,
        step_name="s1",
    )
    paths, source = manifest_from_result(good)
    assert paths == ["a/b.py"]
    assert source == "harness_tool_record"


# ─── AC4 — GH780 regression ───────────────────────────────────────────────────


def test_ac4_execute_with_nondict_data_and_git_delta_emits_scan_source(monkeypatch):
    monkeypatch.setattr(engine, "_git_changes_vs_head", _fake_git_sequence())
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))

    result, _ = eng.execute("solo", make_ctx(), run_id="rid-ac4")
    assert result.status == "ok"

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1, f"expected 1 files_touched event, got {len(touched)}"
    payload = touched[0][1]
    assert payload["source"] == "scan"
    assert payload["paths"] == ["SOME/foreign.py"]


# ─── AC5 — valid manifest preserved ───────────────────────────────────────────


def test_ac5_execute_with_valid_manifest_dict_and_git_delta_emits_manifest_source(monkeypatch):
    monkeypatch.setattr(engine, "_git_changes_vs_head", _fake_git_sequence())
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    manifest_data = {
        "worker_written_paths": ["SOME/foreign.py"],
        "manifest_source": "harness_tool_record",
    }
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", manifest_data)]))

    result, _ = eng.execute("solo", make_ctx(), run_id="rid-ac5")
    assert result.status == "ok"

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    payload = touched[0][1]
    assert payload["source"] == "manifest"
    assert payload["paths"] == ["SOME/foreign.py"]
    assert payload["n_scan_only"] == 0


# ─── AC6 — §1o consumer-dispatch invariant (source-grep; may legitimately PASS) ─


def test_ac6_all_manifest_from_result_call_sites_catch_manifest_missing():
    engine_py_dir = Path(__file__).parent.parent
    files = [
        engine_py_dir / "workflows" / "phase_5_implement.py",
        engine_py_dir / "workflows" / "phase_6_review.py",
    ]
    call_sites = 0
    for f in files:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            if "manifest_from_result(" in line:
                call_sites += 1
                window = "\n".join(lines[i + 1 : i + 6])
                assert "_ManifestError" in window, (
                    f"{f}:{i + 1} manifest_from_result( call site lacks "
                    "'except _ManifestError' within 5 lines"
                )
    assert call_sites >= 4, f"expected >=4 manifest_from_result( call sites, found {call_sites}"


# ─── AC7 — re-entry / cached-sentinel path (§1ab) ────────────────────────────


def test_ac7_cached_sentinel_result_with_nondict_data_and_git_delta_no_raise(monkeypatch):
    monkeypatch.setattr(engine, "_git_changes_vs_head", _fake_git_sequence())

    def fake_read_sentinel(context, step, cycle, run_id, emit, workflow_name=None):
        return StepResult(status="ok", data="hi", duration_ms=0, step_name=step.name)

    monkeypatch.setattr(engine, "maybe_read_sentinel", fake_read_sentinel)

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", None)]))

    result, _ = eng.execute("solo", make_ctx(), run_id="rid-ac7")
    assert result.status == "ok"

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    assert touched[0][1]["source"] == "scan"
