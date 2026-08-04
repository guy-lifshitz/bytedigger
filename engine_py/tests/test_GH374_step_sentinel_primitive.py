"""RED tests for GH374 Part B — resume_sentinel StepContract primitive.

Spec: SHARED/memory/Decisions/2026-07-07_GH374_restart_governor_spec.md §2.4-§2.6, §3 (B1-B10).

Covers:
  B1  StepContract.resume_sentinel field (default False)
  B2  lib/step_sentinel.py write_step_sentinel/read_step_sentinel + alias identity
  B3  pre-seeded sentinel on a flagged step short-circuits execute()
  B4  flagged step returning ok writes a sentinel under <scratchpad>/resume/
  B5  unflagged step ignores a pre-seeded sentinel (always executes)
  B6  Design-A retry: cycle-2 does not reuse the cycle-1 sentinel
  B7  flagged step returning error never writes a sentinel
  B8  structural: exactly the 3 named LLM steps are flagged True
  B9  org_config disable flag suppresses both read and write
  B10 structural: core_manifest.json lists both new lib modules

§1q-ext (D1CF5FDF): `lib.step_sentinel` does not exist yet — every import of it
is deferred INSIDE the test function body so the file still COLLECTS under
pytest. Top-level imports below touch only symbols that already exist today.

No UUT mocking (stub-passability): all sentinel/engine behavior is exercised
via real WorkflowEngine runs against synthetic StepContracts with plain
counters — never `patch`/`patch.object` on step_sentinel, StepContract, or
engine internals.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402


def _make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH374 sentinel test",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── B1 ─────────────────────────────────────────────────────────────────────


def test_b1_step_contract_has_resume_sentinel_default_false():
    """B1: StepContract exposes resume_sentinel, defaulting to False.

    At RED: the field does not exist → AttributeError on the getattr below.
    """
    sc = StepContract(name="x", execute=lambda ctx, prev: prev)
    assert getattr(sc, "resume_sentinel") is False, (
        "resume_sentinel must default to False"
    )


# ─── B2 ─────────────────────────────────────────────────────────────────────


def test_b2_lib_step_sentinel_exposes_functions():
    """B2: lib/step_sentinel.py exposes write/read_step_sentinel with the
    BC45C403 signatures.

    At RED: `import bytedigger_engine.lib.step_sentinel` fails (module absent) → ImportError.
    """
    from bytedigger_engine.lib import step_sentinel  # noqa: PLC0415 — deferred, module does not exist at RED

    assert callable(step_sentinel.write_step_sentinel)
    assert callable(step_sentinel.read_step_sentinel)


# ─── B3 ─────────────────────────────────────────────────────────────────────


def test_b3_preseeded_sentinel_short_circuits_flagged_step(tmp_path):
    """B3: engine run of a 1-step flagged workflow with a pre-seeded sentinel
    (written via write_step_sentinel using the engine's own naming) must NOT
    invoke execute(); the final result is 'ok' with the cached data, and a
    step_sentinel_resumed event is emitted.

    At RED: `import bytedigger_engine.lib.step_sentinel` fails inside the body → ImportError → FAIL.
    """
    from bytedigger_engine.lib import step_sentinel  # noqa: PLC0415
    from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB3"
    cached_data = {"answer": 42}
    step_sentinel.write_step_sentinel(
        scratch, "flagged_step", 1, cached_data, run_id, workflow_name="wf_b3"
    )
    assert (scratch / "resume" / resume_sentinel_name("flagged_step", 1, run_id, workflow_name="wf_b3")).exists()

    calls: list = []

    def _execute(ctx, prev):
        calls.append(1)
        return StepResult(status="ok", data={"answer": -1}, duration_ms=0, step_name="flagged_step")

    workflow = WorkflowDefinition(
        name="wf_b3",
        steps=[StepContract(name="flagged_step", execute=_execute, resume_sentinel=True)],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b3", workflow)
    result, _ = eng.execute("wf_b3", _make_ctx(scratch), run_id=run_id)

    assert calls == [], "execute() must NOT run when a valid sentinel is cached"
    assert result.status == "ok"
    assert result.data == cached_data, f"expected cached data, got {result.data!r}"
    events = [e["event_type"] for e in log.read_all()]
    assert "step_sentinel_resumed" in events, f"missing step_sentinel_resumed; events={events}"


# ─── B4 ─────────────────────────────────────────────────────────────────────


def test_b4_flagged_step_ok_writes_sentinel(tmp_path):
    """B4: a flagged step that returns ok writes a sentinel file under
    <scratchpad>/resume/ containing result.data.

    At RED: `import bytedigger_engine.lib.step_sentinel` fails inside the body → ImportError → FAIL.
    """
    from bytedigger_engine.lib import step_sentinel  # noqa: PLC0415
    from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB4"

    def _execute(ctx, prev):
        return StepResult(status="ok", data={"wrote": True}, duration_ms=0, step_name="flagged_step")

    workflow = WorkflowDefinition(
        name="wf_b4",
        steps=[StepContract(name="flagged_step", execute=_execute, resume_sentinel=True)],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b4", workflow)
    eng.execute("wf_b4", _make_ctx(scratch), run_id=run_id)

    sentinel_file = scratch / "resume" / resume_sentinel_name("flagged_step", 1, run_id, workflow_name="wf_b4")
    assert sentinel_file.exists(), f"expected sentinel at {sentinel_file}"
    assert json.loads(sentinel_file.read_text()) == {"wrote": True}


# ─── B5 ─────────────────────────────────────────────────────────────────────


def test_b5_unflagged_step_ignores_preseeded_sentinel(tmp_path):
    """B5: an UNflagged step always executes, even with a pre-seeded sentinel,
    and produces no sentinel read/write events.

    At RED: `import bytedigger_engine.lib.step_sentinel` fails inside the body → ImportError → FAIL.
    """
    from bytedigger_engine.lib import step_sentinel  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB5"
    step_sentinel.write_step_sentinel(scratch, "plain_step", 1, {"cached": True}, run_id)

    calls: list = []

    def _execute(ctx, prev):
        calls.append(1)
        return StepResult(status="ok", data={"fresh": True}, duration_ms=0, step_name="plain_step")

    workflow = WorkflowDefinition(
        name="wf_b5",
        steps=[StepContract(name="plain_step", execute=_execute)],  # resume_sentinel defaults False
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b5", workflow)
    result, _ = eng.execute("wf_b5", _make_ctx(scratch), run_id=run_id)

    assert calls == [1], "unflagged step must execute regardless of a cached sentinel"
    assert result.data == {"fresh": True}
    events = [e["event_type"] for e in log.read_all()]
    assert "step_sentinel_resumed" not in events
    assert "step_sentinel_written" not in events


# ─── B6 ─────────────────────────────────────────────────────────────────────


def test_b6_retry_cycle2_does_not_reuse_cycle1_sentinel(tmp_path):
    """B6: Design-A retry recursion — a flagged step that requests a retry
    (recoverable=True, retry_from_step=0, cycle_count=1) once, then succeeds
    on the re-run, must NOT reuse the cycle-1 sentinel: execute() runs again
    at cycle 2, and a *_c2_* sentinel is written (distinct from cycle 1).

    At RED: `StepContract(..., resume_sentinel=True)` raises TypeError
    (unexpected keyword — field does not exist yet) → FAIL.
    """
    from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # existing module, safe at call time

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB6"
    calls: list = []

    def _execute(ctx, prev):
        calls.append(1)
        if len(calls) == 1:
            return StepResult(
                status="error", data={"retry_from_step": 0, "cycle_count": 1},
                duration_ms=0, step_name="retry_step", error="retry me",
                error_code="E_RETRY", recoverable=True,
            )
        return StepResult(status="ok", data={"final": True}, duration_ms=0, step_name="retry_step")

    workflow = WorkflowDefinition(
        name="wf_b6",
        steps=[StepContract(name="retry_step", execute=_execute, resume_sentinel=True)],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b6", workflow)
    result, _ = eng.execute("wf_b6", _make_ctx(scratch), run_id=run_id)

    assert calls == [1, 1], "execute() must run again at cycle 2, not be short-circuited"
    assert result.status == "ok"
    cycle2_sentinel = scratch / "resume" / resume_sentinel_name("retry_step", 2, run_id, workflow_name="wf_b6")
    assert cycle2_sentinel.exists(), f"expected a cycle-2 sentinel at {cycle2_sentinel}"
    cycle1_sentinel = scratch / "resume" / resume_sentinel_name("retry_step", 1, run_id, workflow_name="wf_b6")
    assert not cycle1_sentinel.exists() or json.loads(cycle1_sentinel.read_text()) != {"final": True}, (
        "cycle-2 result must not be recorded under the cycle-1 sentinel key"
    )


# ─── B7 ─────────────────────────────────────────────────────────────────────


def test_b7_flagged_step_error_writes_no_sentinel(tmp_path):
    """B7: a flagged step returning status='error' (no retry request) must
    NOT write a sentinel file.

    At RED: `StepContract(..., resume_sentinel=True)` raises TypeError → FAIL.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB7"

    def _execute(ctx, prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="failing_step",
            error="boom", error_code="E_BOOM", recoverable=False,
        )

    workflow = WorkflowDefinition(
        name="wf_b7",
        steps=[StepContract(name="failing_step", execute=_execute, resume_sentinel=True)],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b7", workflow)
    eng.execute("wf_b7", _make_ctx(scratch), run_id=run_id)

    resume_dir = scratch / "resume"
    assert not resume_dir.exists() or not any(resume_dir.iterdir()), (
        f"no sentinel must be written on error; found {list(resume_dir.iterdir()) if resume_dir.exists() else []}"
    )


# ─── B8 ─────────────────────────────────────────────────────────────────────


def test_b8_exactly_three_llm_steps_flagged():
    """B8: phase_45_spec_workflow/phase_5_implement_workflow/phase_4_architect_workflow
    have resume_sentinel==True on exactly invoke_spec_llm/invoke_green_llm/
    invoke_architect_llm/invoke_review_llm (the last flipped by GH557), and
    False everywhere else. Loop-body steps (invoke_red_llm/invoke_validation_llm,
    GH557) live in LoopStepContract.body, not wf.steps — pinned in
    test_gh557_resume_seam_flips.py, not here.

    At RED: `.resume_sentinel` does not exist on any StepContract → AttributeError.
    """
    from bytedigger_engine.workflows.phase_45_spec import phase_45_spec_workflow  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_4_architect import phase_4_architect_workflow  # noqa: PLC0415

    expected_true = {"invoke_spec_llm", "invoke_green_llm", "invoke_architect_llm", "invoke_review_llm"}
    flagged: set = set()
    seen: set = set()
    for wf in (phase_45_spec_workflow(), phase_5_implement_workflow(), phase_4_architect_workflow()):
        for step in wf.steps:
            seen.add(step.name)
            if getattr(step, "resume_sentinel"):
                flagged.add(step.name)

    assert flagged == expected_true, f"expected exactly {expected_true} flagged, got {flagged}"
    assert expected_true <= seen, f"expected steps missing from workflows: {expected_true - seen}"


# ─── B9 ─────────────────────────────────────────────────────────────────────


def test_b9_disable_flag_suppresses_read_and_write(tmp_path):
    """B9: org_config={"step_sentinel": {"disable": True}} — a flagged step
    with a pre-seeded sentinel still executes (no read short-circuit), and
    the pre-seeded sentinel is left untouched (no write).

    At RED: `import bytedigger_engine.lib.step_sentinel` fails inside the body → ImportError → FAIL.
    """
    from bytedigger_engine.lib import step_sentinel  # noqa: PLC0415
    from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: PLC0415

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runB9"
    preseed = {"marker": "preseed"}
    step_sentinel.write_step_sentinel(scratch, "flagged_step", 1, preseed, run_id)

    calls: list = []

    def _execute(ctx, prev):
        calls.append(1)
        return StepResult(status="ok", data={"marker": "fresh"}, duration_ms=0, step_name="flagged_step")

    workflow = WorkflowDefinition(
        name="wf_b9",
        steps=[StepContract(name="flagged_step", execute=_execute, resume_sentinel=True)],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_b9", workflow)
    result, _ = eng.execute(
        "wf_b9",
        _make_ctx(scratch, step_sentinel={"disable": True}),
        run_id=run_id,
    )

    assert calls == [1], "disable flag must not short-circuit execute()"
    assert result.data == {"marker": "fresh"}
    sentinel_file = scratch / "resume" / resume_sentinel_name("flagged_step", 1, run_id)
    assert json.loads(sentinel_file.read_text()) == preseed, (
        "disable flag must prevent overwriting the pre-seeded sentinel"
    )


# ─── B10 ────────────────────────────────────────────────────────────────────


def test_b10_core_manifest_lists_both_new_lib_modules():
    """B10 (structural half): core_manifest.json must list both new libs.

    The deterministic core-boundary-lint.py run over the manifest is executed
    by the orchestrator (this RED agent has no Bash) — this test only checks
    the manifest entries the lint would need to see.

    At RED: neither entry is present yet → FAIL.
    """
    manifest_path = ENGINE_ROOT / "core_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    modules = manifest["core_modules"]
    assert "lib/restart_governor.py" in modules, "restart_governor.py missing from core_manifest.json"
    assert "lib/step_sentinel.py" in modules, "step_sentinel.py missing from core_manifest.json"
