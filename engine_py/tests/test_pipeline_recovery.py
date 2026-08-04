"""Tests for Design A pipeline recovery — workflow-level RED-validation retry.

Decree: SHARED/memory/Decisions/2026-04-26_pipeline-recovery-design.md (Design A, cap N=2).

Three layers exercised:
  1. Engine — `_execute_steps(start_step=...)` partial replay; retry hook on
     `E_VALIDATION_RETRY` recoverable error.
  2. Phase 5 RED prompt builder — accepts cycle/findings via initial_data and
     appends REVISION section; per-cycle artifact paths.
  3. Phase 5 gate — returns E_VALIDATION_RETRY (recoverable=True) on FAIL when
     cycle_count < 2; falls back to terminal E_VALIDATION_FAILED at cycle 2.

Mock validator drives FAIL/PASS sequencing per cycle by inspecting prompt
content (number of "## REVISION" markers ≈ cycle index).

25e75663 migration (R2+R4): cycle_aware_validator_stub replaced by a registered
backend callable; llm_command= key removed from org_config.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    GREEN_LOG_RELPATH,
    SPEC_DOC_RELPATH,
    phase_5_implement_workflow,
)


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "verify_green_delta_enforce": False, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def minimal_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit so commit_red_tests has a valid HEAD.

    γ cleanup 8.5 (A4461B8F): leaves tests/test_stub.py as untracked so
    _commit_red_tests can discover it via git_diff. .gitignore excludes __pycache__
    to prevent cycle-2 from picking up .pyc artifacts as dirty test files.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "placeholder.py").write_text("# placeholder\n")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    subprocess.run(["git", "add", "placeholder.py", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    # Untracked test file for _commit_red_tests to discover via git_diff.
    test_stub = repo / "tests" / "test_stub.py"
    test_stub.parent.mkdir(parents=True, exist_ok=True)
    test_stub.write_text("def test_stub(): assert False\n")
    return repo


def seed_spec(scratchpad: Path) -> None:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## US1\nAdd foo\n")


@pytest.fixture(autouse=True)
def _reset_backends_pr():
    """§1i: restore _BACKENDS singleton after every test."""
    yield
    reset_backends()


class _CycleAwareBackend:
    """Registered backend that mimics the old cycle_aware_validator_stub shell command.

    Branches on prompt content (same logic as the former python3 -c snippet):
    - 'Opus validator' in prompt → return cycle-indexed verdict
    - 'GREEN worker' in prompt  → return GREEN COMPLETE
    - else (RED prompt)         → write fresh test file to git_cwd, return RED COMPLETE
    """

    def __init__(self, verdicts: list[str], git_cwd: str | None = None) -> None:
        self._verdicts = verdicts
        self._git_cwd = git_cwd

    def __call__(self, **kw) -> StepResult:
        prompt = kw.get("prompt", "")
        if "Opus validator" in prompt:
            m = re.search(r"build-red-output-cycle-(\d+)\.log", prompt)
            cycle_idx = int(m.group(1)) - 1 if m else 0
            cycle_idx = min(cycle_idx, len(self._verdicts) - 1)
            raw = f"Verdict: {self._verdicts[cycle_idx]}\n"
        elif "GREEN worker" in prompt:
            # GH297: mirror a real GREEN worker flipping the RED-written tests
            # to passing, so red-verify sees failures but verify_green_passing
            # sees a clean pass — chain flows through cleanly.
            if self._git_cwd:
                tests_dir = Path(self._git_cwd) / "tests"
                if tests_dir.is_dir():
                    for f in tests_dir.glob("test_*.py"):
                        m3 = re.search(r"def (test_\w+)\(", f.read_text())
                        fn_name = m3.group(1) if m3 else "test_stub"
                        f.write_text(f"def {fn_name}(): assert True\n")
            raw = "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n"
        else:
            # RED step: write a unique test file per cycle so _commit_red_tests
            # finds dirty files via git_diff.
            if self._git_cwd:
                m2 = re.search(r"## REVISION \(cycle (\d+)", prompt)
                cycle_num = int(m2.group(1)) if m2 else 1
                test_file = Path(self._git_cwd) / "tests" / f"test_cycle{cycle_num}.py"
                test_file.parent.mkdir(parents=True, exist_ok=True)
                test_file.write_text(f"def test_cycle{cycle_num}(): assert False\n")
            raw = "RED COMPLETE — 1 tests written, all failing. Files: [x]\n\n" + prompt
        data: dict = {
            "raw_response": raw,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


def cycle_aware_validator_stub(verdicts: list[str], git_cwd: str | None = None) -> None:
    """Register a cycle-aware backend as claude-subprocess (R2: replaces old shell stub).

    Callers previously stored the return value of this function in llm_command= and
    passed it to make_ctx. After migration, this helper registers the backend directly
    (no return value needed); callers drop the llm_command= kwarg from make_ctx.
    """
    register_backend(
        "claude-subprocess",
        _CycleAwareBackend(verdicts, git_cwd),
        manifest_source="harness_tool_record",
        overwrite=True,
    )


# ─── 1. Cycle-1 PASS path — no behavior change baseline ─────────────────────


def test_cycle1_pass_no_retry(tmp_path):
    """When validator PASSes on cycle 1: legacy single-shot artifacts only,
    no cycle-2 artifacts, no iteration_* events, GREEN runs, pipeline succeeds."""
    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", phase_5_implement_workflow())

    # GH297: pass git_cwd so the GREEN-worker branch can flip the RED-written
    # test(s) to passing before verify_green_passing runs.
    cycle_aware_validator_stub(["PASS"], git_cwd=str(repo))
    result, _ = eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r1",
    )
    # GH297: GREEN worker flips RED tests to passing — pipeline succeeds.
    assert result.status == "ok"
    # Cycle-1 uses legacy single-shot paths; cycle-2 paths absent
    assert (scratchpad / "tests/build-red-output.log").exists()
    assert (scratchpad / "reviews/build-opus-validation.md").exists()
    assert not (scratchpad / "tests/build-red-output-cycle-2.log").exists()
    assert not (scratchpad / "reviews/build-validation-cycle-2.md").exists()
    # GREEN ran
    assert (scratchpad / GREEN_LOG_RELPATH).exists()
    # No iteration_* events (single-pass)
    events = log.read_all()
    iteration_events = [e for e in events if e["event_type"].startswith("iteration_")]
    assert iteration_events == []


# ─── 2. Cycle-1 FAIL → cycle-2 PASS — retry recovers ────────────────────────


def test_cycle1_fail_cycle2_pass_recovers(tmp_path):
    """Validator FAILs on cycle 1 → LoopRunner re-iterates body → PASS on
    cycle 2 → GREEN runs → pipeline succeeds. Cycle-2 RED prompt contains
    REVISION section with cycle-1 findings.

    D352C2D1: pre-migration the engine emitted ``iteration_started`` and
    ``iteration_finished`` events around the retry. LoopRunner replaces that
    mechanism and does NOT emit those events — the cap+marker semantics now
    live in the LoopStepContract. The recovery is asserted by:
      - per-cycle artifacts exist (proves the body ran twice)
      - cycle-2 RED log carries the REVISION block (proves findings threaded)
      - GREEN ran (proves the composite step terminated cleanly with PASS)
      - no iteration_* events emitted (regression guard for the migration)
    """
    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", phase_5_implement_workflow())

    cycle_aware_validator_stub(["FAIL", "PASS"], git_cwd=str(repo))
    result, _ = eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r2",
    )
    # GH297: fixtures now pass verify_green_passing cleanly — pipeline succeeds.
    assert result.status == "ok"
    # Cycle-1 at legacy paths, cycle-2 at versioned paths — both present
    assert (scratchpad / "tests/build-red-output.log").exists()
    assert (scratchpad / "tests/build-red-output-cycle-2.log").exists()
    assert (scratchpad / "reviews/build-opus-validation.md").exists()
    assert (scratchpad / "reviews/build-validation-cycle-2.md").exists()
    # Cycle-2 RED log contains REVISION section
    cycle2_red = (scratchpad / "tests/build-red-output-cycle-2.log").read_text()
    assert "## REVISION" in cycle2_red
    # GREEN ran
    assert (scratchpad / GREEN_LOG_RELPATH).exists()
    # D352C2D1: engine retry-hook iteration_* events no longer emitted —
    # LoopRunner drives iteration silently from the engine's POV.
    events = log.read_all()
    started = [e for e in events if e["event_type"] == "iteration_started"]
    finished = [e for e in events if e["event_type"] == "iteration_finished"]
    assert started == [], f"unexpected iteration_started post-D352C2D1: {started!r}"
    assert finished == [], f"unexpected iteration_finished post-D352C2D1: {finished!r}"


# ─── 3. Cycle-1 FAIL → cycle-2 FAIL → terminal abort ────────────────────────


def test_cycle1_fail_cycle2_fail_terminal_abort(tmp_path):
    """Validator FAILs both cycles → pipeline aborts with E_VALIDATION_FAILED,
    recoverable=False. Both cycle artifacts present. No third cycle."""
    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", phase_5_implement_workflow())

    cycle_aware_validator_stub(["FAIL", "FAIL"], git_cwd=str(repo))
    result, _ = eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r3",
    )
    assert result.status == "error"
    assert result.error_code == "E_VALIDATION_FAILED"
    assert result.recoverable is False
    # Cycle-1 at legacy paths, cycle-2 at versioned paths — both present
    assert (scratchpad / "tests/build-red-output.log").exists()
    assert (scratchpad / "tests/build-red-output-cycle-2.log").exists()
    assert (scratchpad / "reviews/build-opus-validation.md").exists()
    assert (scratchpad / "reviews/build-validation-cycle-2.md").exists()
    # No cycle-3 artifact — cap respected
    assert not (scratchpad / "tests/build-red-output-cycle-3.log").exists()
    # GREEN never ran
    assert not (scratchpad / GREEN_LOG_RELPATH).exists()


# ─── 4. Engine `start_step` parameter ───────────────────────────────────────


def _trace_step(name: str, trace: list):
    def _run(_ctx, prev):
        trace.append(name)
        return StepResult(status="ok", data={"name": name, "prev": prev}, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def test_start_step_skips_earlier_steps():
    """When `_execute_steps` is called with start_step=2, only steps[2:]
    execute; steps 0 and 1 are skipped."""
    trace: list = []
    workflow = WorkflowDefinition(
        name="trace",
        steps=[
            _trace_step("s0", trace),
            _trace_step("s1", trace),
            _trace_step("s2", trace),
            _trace_step("s3", trace),
        ],
    )
    eng = WorkflowEngine()
    ctx = WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )
    # Direct internal call — start_step is a private engine param, not public API
    result = eng._execute_steps(workflow, ctx, run_id="rid", start_step=2)
    assert result.status == "ok"
    assert result.step_name == "s3"
    assert trace == ["s2", "s3"]


def test_start_step_default_zero_runs_all():
    """Backward-compat: default start_step=0 runs every step."""
    trace: list = []
    workflow = WorkflowDefinition(
        name="trace",
        steps=[_trace_step("s0", trace), _trace_step("s1", trace)],
    )
    eng = WorkflowEngine()
    ctx = WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )
    result = eng._execute_steps(workflow, ctx, run_id="rid")
    assert result.status == "ok"
    assert trace == ["s0", "s1"]


# ─── 5. iteration_started/iteration_finished event shape ────────────────────


def test_iteration_event_shape_round_trip(tmp_path):
    """`iteration_started` payload carries cycle_number + findings_summary;
    `iteration_finished` carries cycle_number + verdict. Replay through
    derive_state preserves them as `unknown_events` (forward-compat) but the
    raw events round-trip via read_all()."""
    log = EventLog(tmp_path / "events.jsonl")
    log.append(
        "iteration_started",
        {"cycle_number": 2, "findings_summary": "Missing edge cases"},
        run_id="r",
    )
    log.append(
        "iteration_finished",
        {"cycle_number": 2, "verdict": "PASS"},
        run_id="r",
    )
    events = log.read_all()
    assert len(events) == 2
    assert events[0]["event_type"] == "iteration_started"
    assert events[0]["payload"]["cycle_number"] == 2
    assert events[0]["payload"]["findings_summary"] == "Missing edge cases"
    assert events[1]["event_type"] == "iteration_finished"
    assert events[1]["payload"]["cycle_number"] == 2
    assert events[1]["payload"]["verdict"] == "PASS"
