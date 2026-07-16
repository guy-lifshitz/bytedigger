"""RED tests for phase_5_integration_canary workflow (agreement 06452D44).

Hard-gate that fails the build when no events.jsonl entry matches the current
run_id + event_type (default agent_spawn). Closes the deployment-coverage gap
exposed by build forge-1777578916 (51 tests passed, 0 events fired in prod).

These tests MUST FAIL today: workflow does not exist yet — register_all is
missing the entry, the import statement at module top will ImportError.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
import workflows  # noqa: E402


def _make_ctx(*, run_id_in_org: str | None = None, **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext. run_id resolution rides on org_config
    because contracts.WorkflowContext is frozen and has no run_id attribute —
    the canary must read integration_canary_run_id from org_config (or surface
    E_CANARY_BAD_CONFIG when neither org_config nor telemetry_ctx provides one).
    """
    org: dict = dict(org_extra)
    if run_id_in_org is not None:
        org["integration_canary_run_id"] = run_id_in_org
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id="canary-test",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _run_canary(ctx: WorkflowContext):
    """Execute the workflow's single step directly so we get the StepResult."""
    from phase_5_integration_canary import phase_5_integration_canary_workflow  # noqa: E402

    wf = phase_5_integration_canary_workflow()
    assert wf.steps, "workflow must have at least one step"
    return wf.steps[0].execute(ctx, None)


# ─── 1. registry ──────────────────────────────────────────────────────────────


def test_canary_workflow_registered_with_engine():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_5_integration_canary" in eng.registered()


# ─── 2. opt-in semantics ──────────────────────────────────────────────────────


def test_canary_skips_when_not_required(tmp_path):
    """Default off — workflow must early-return ok+skipped without touching disk."""
    events_path = tmp_path / "build-events.jsonl"  # intentionally not created
    ctx = _make_ctx(
        run_id_in_org="rid-skip",
        integration_canary_events_path=str(events_path),
        # integration_canary_required omitted → defaults False
    )
    result = _run_canary(ctx)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert isinstance(result.data, dict)
    assert result.data.get("skipped") is True


# ─── 3. happy path ────────────────────────────────────────────────────────────


def test_canary_passes_when_matching_event_present(tmp_path):
    events_path = tmp_path / "build-events.jsonl"
    _write_events(
        events_path,
        [
            {
                "ts": "2026-05-01T12:00:00.000Z",
                "run_id": "rid-pass",
                "event_type": "agent_spawn",
                "payload": {"subagent_type": "haiku", "parent_skill": "build", "description": "x"},
            }
        ],
    )
    ctx = _make_ctx(
        run_id_in_org="rid-pass",
        integration_canary_required=True,
        integration_canary_events_path=str(events_path),
    )
    result = _run_canary(ctx)

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert isinstance(result.data, dict)
    assert result.data.get("skipped") is False
    assert result.data.get("matched_count", 0) >= 1


# ─── 4. miss: wrong run_id ────────────────────────────────────────────────────


def test_canary_fails_when_no_matching_event(tmp_path):
    events_path = tmp_path / "build-events.jsonl"
    _write_events(
        events_path,
        [
            {
                "ts": "2026-05-01T12:00:00.000Z",
                "run_id": "OTHER-RUN",
                "event_type": "agent_spawn",
                "payload": {"subagent_type": "haiku"},
            }
        ],
    )
    ctx = _make_ctx(
        run_id_in_org="rid-current",
        integration_canary_required=True,
        integration_canary_events_path=str(events_path),
    )
    result = _run_canary(ctx)

    assert result.status == "error", f"expected error, got {result.status}"
    assert result.error_code == "E_CANARY_NO_MATCH", (
        f"expected E_CANARY_NO_MATCH, got {result.error_code!r}"
    )


# ─── 5. miss: wrong event_type ────────────────────────────────────────────────


def test_canary_fails_when_event_type_mismatches(tmp_path):
    events_path = tmp_path / "build-events.jsonl"
    _write_events(
        events_path,
        [
            {
                "ts": "2026-05-01T12:00:00.000Z",
                "run_id": "rid-typematch",
                "event_type": "something_else",
                "payload": {"subagent_type": "haiku"},
            }
        ],
    )
    ctx = _make_ctx(
        run_id_in_org="rid-typematch",
        integration_canary_required=True,
        integration_canary_events_path=str(events_path),
        # default event_type = agent_spawn
    )
    result = _run_canary(ctx)

    assert result.status == "error"
    assert result.error_code == "E_CANARY_NO_MATCH"


# ─── 6. miss: missing required payload key ───────────────────────────────────


def test_canary_validates_required_payload_keys(tmp_path):
    events_path = tmp_path / "build-events.jsonl"
    _write_events(
        events_path,
        [
            {
                "ts": "2026-05-01T12:00:00.000Z",
                "run_id": "rid-payload",
                "event_type": "agent_spawn",
                # parent_skill MISSING
                "payload": {"subagent_type": "haiku", "description": "x"},
            }
        ],
    )
    ctx = _make_ctx(
        run_id_in_org="rid-payload",
        integration_canary_required=True,
        integration_canary_events_path=str(events_path),
        integration_canary_required_payload_keys=["parent_skill"],
    )
    result = _run_canary(ctx)

    assert result.status == "error"
    assert result.error_code == "E_CANARY_NO_MATCH"


# ─── 7. infra: events file missing ───────────────────────────────────────────


def test_canary_errors_when_events_path_missing(tmp_path):
    missing_path = tmp_path / "does-not-exist" / "events.jsonl"
    ctx = _make_ctx(
        run_id_in_org="rid-missing",
        integration_canary_required=True,
        integration_canary_events_path=str(missing_path),
    )
    result = _run_canary(ctx)

    assert result.status == "error"
    assert result.error_code == "E_CANARY_EVENTS_MISSING"


# ─── 8. config: run_id unresolvable ──────────────────────────────────────────


def test_canary_errors_when_run_id_unresolvable(tmp_path):
    """No integration_canary_run_id in org_config AND no current run via
    telemetry_ctx → E_CANARY_BAD_CONFIG. WorkflowContext has no run_id field,
    so the only sources are org_config or the engine-scoped telemetry slot."""
    import telemetry_ctx  # noqa: E402

    telemetry_ctx.clear_current_run()  # ensure no ambient run leaks in

    events_path = tmp_path / "build-events.jsonl"
    _write_events(events_path, [])  # path exists; isolate the run_id failure

    ctx = _make_ctx(
        # no run_id_in_org — deliberately omitted
        integration_canary_required=True,
        integration_canary_events_path=str(events_path),
    )
    result = _run_canary(ctx)

    assert result.status == "error"
    assert result.error_code == "E_CANARY_BAD_CONFIG", (
        f"expected E_CANARY_BAD_CONFIG, got {result.error_code!r}"
    )
