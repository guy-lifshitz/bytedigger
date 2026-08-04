"""Agreement D352C2D1 — assert LoopStepContract migration of phase_5_implement.

These tests fail until the migration ships. They lock the post-migration
public surface (build_validation_loop_contract) and behavioral shift
(gate_on_validation no longer returns E_VALIDATION_RETRY on cycle<cap).

Mirrors agreement 350956CC (phase_45_spec_lite migration) test file at
tests/test_phase_45_spec_lite_loop_migration.py — same five-test shape:
  1. contract shape factory
  2. workflow has the composite loop step + 6 GREEN steps
  3. cycle-1 FAIL no longer returns E_VALIDATION_RETRY
  4. cycle-cap FAIL still terminal abort (regression guard)
  5. composite step iterates via LoopRunner cycle-1 FAIL → cycle-2 PASS
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

import pytest  # noqa: E402

from bytedigger_engine.contracts import (  # noqa: E402
    LoopStepContract,
    StepContract,
    StepResult,
    WorkflowContext,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    GREEN_LOG_RELPATH,
    MAX_VALIDATION_CYCLES,
    SPEC_DOC_RELPATH,
    VERDICT_FAIL,
    VERDICT_PASS,
    _gate_on_validation,
    phase_5_implement_workflow,
)

_LOOP_BACKEND_NAME = "d352c2d1-loop-validator"


@pytest.fixture(autouse=True)
def _reset_backends_loop():
    """§1i: restore _BACKENDS singleton after every test."""
    yield
    reset_backends()


# ─── helpers (mirrored from tests/test_pipeline_recovery.py) ─────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    # 25e75663 R4: drop legacy llm_command= key (silently ignored by _resolve_model).
    org_extra.pop("llm_command", None)
    org = {"scratchpad_dir": str(scratchpad), "model": "sonnet", **org_extra}
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
    """γ cleanup 8.5 (A4461B8F): leaves tests/test_stub.py as untracked so
    _commit_red_tests can discover it via git_diff. .gitignore excludes __pycache__
    to prevent cycle-2 from picking up .pyc artifacts as dirty test files."""
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
    # Untracked test file for _commit_red_tests to discover via git_diff
    test_stub = repo / "tests" / "test_stub.py"
    test_stub.parent.mkdir(parents=True, exist_ok=True)
    test_stub.write_text("def test_stub(): assert False\n")
    return repo


def seed_spec(scratchpad: Path) -> None:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## US1\nAdd foo\n")


def cycle_aware_validator_stub(verdicts: list[str], git_cwd: str | None = None) -> list[str]:
    """Legacy subprocess stub — kept for reference but no longer used via llm_command=.

    25e75663: replaced by _CycleAwareBackend registered via register_backend.
    """
    import re as _re
    import pathlib as _pathlib
    code = (
        "import sys, re, os, pathlib\n"
        "p = sys.stdin.read()\n"
        f"verdicts = {verdicts!r}\n"
        f"git_cwd = {git_cwd!r}\n"
        "if 'Opus validator' in p:\n"
        "    m = re.search(r'build-red-output-cycle-(\\d+)\\.log', p)\n"
        "    cycle_idx = int(m.group(1)) - 1 if m else 0\n"
        "    cycle_idx = min(cycle_idx, len(verdicts) - 1)\n"
        "    sys.stdout.write(f'Verdict: {verdicts[cycle_idx]}\\n')\n"
        "elif 'GREEN worker' in p:\n"
        "    sys.stdout.write('GREEN COMPLETE — all 1 tests passing. Files modified: [x]\\n')\n"
        "else:\n"
        "    if git_cwd:\n"
        "        m = re.search(r'## REVISION \\(cycle (\\d+)', p)\n"
        "        cycle_num = int(m.group(1)) if m else 1\n"
        "        test_file = pathlib.Path(git_cwd) / 'tests' / f'test_cycle{cycle_num}.py'\n"
        "        test_file.parent.mkdir(parents=True, exist_ok=True)\n"
        "        test_file.write_text(f'def test_cycle{cycle_num}(): assert False\\n')\n"
        "    sys.stdout.write('RED COMPLETE — 1 tests written, all failing. Files: [x]\\n\\n' + p)\n"
    )
    return ["python3", "-c", code]


class _CycleAwareBackend:
    """25e75663 R2: registered backend replacing cycle_aware_validator_stub.

    Implements the same branching logic as the python3 -c stub, returning
    canned StepResult objects. Behavioral contract unchanged.
    """
    def __init__(self, verdicts: list[str], git_cwd: str | None = None):
        import re
        self._verdicts = verdicts
        self._git_cwd = git_cwd
        self._re = re

    def __call__(self, **kw) -> StepResult:
        import re
        import pathlib
        prompt = kw.get("prompt", "")
        if "Opus validator" in prompt:
            m = re.search(r"build-red-output-cycle-(\d+)\.log", prompt)
            cycle_idx = int(m.group(1)) - 1 if m else 0
            cycle_idx = min(cycle_idx, len(self._verdicts) - 1)
            raw = f"Verdict: {self._verdicts[cycle_idx]}\n"
        elif "GREEN worker" in prompt:
            raw = "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n"
        else:
            if self._git_cwd:
                m = re.search(r"## REVISION \(cycle (\d+)", prompt)
                cycle_num = int(m.group(1)) if m else 1
                test_file = pathlib.Path(self._git_cwd) / "tests" / f"test_cycle{cycle_num}.py"
                test_file.parent.mkdir(parents=True, exist_ok=True)
                test_file.write_text(f"def test_cycle{cycle_num}(): assert False\n")
            raw = f"RED COMPLETE — 1 tests written, all failing. Files: [x]\n\n{prompt}"
        data: dict = {
            "raw_response": raw,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
            "tokens_out": None,
            "tokens_in": None,
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_red_llm"),
            error=None,
            error_code=None,
            recoverable=True,
        )


_EXPECTED_BODY_NAMES = [
    "build_red_prompt",
    "invoke_red_llm",
    "write_red_artifact",
    "commit_red_tests",
    "verify_red_fails_mechanically",
    "verify_red_lint_rules",
    "build_validation_prompt",
    "check_red_executable",
    "invoke_validation_llm",
    "write_validation_doc",
    "verify_validation_citations",
    "gate_on_validation",
]

_EXPECTED_GREEN_NAMES = [
    "build_green_prompt",
    "cwd_preflight",
    "invoke_green_llm",
    "check_green_token_budget",
    "write_green_artifact",
    "verify_green_lint_rules",
    "verify_security_lint",
    "verify_green_passing",
    "verify_green_typecheck",
    "commit_green_code",
    "green_watchdog",
]


# ─── Test 1: build_validation_loop_contract returns the expected shape ──────


def test_build_validation_loop_contract_shape():
    """D352C2D1: phase_5_implement exposes a LoopStepContract describing the
    cycle-2 RED→validation loop. max_iterations=MAX_VALIDATION_CYCLES (=2);
    until_marker=PASS on data['verdict']; body wraps the 11 cyclic steps."""
    from bytedigger_engine.workflows.phase_5_implement import build_validation_loop_contract  # noqa: F401

    contract = build_validation_loop_contract()
    assert isinstance(contract, LoopStepContract)
    assert contract.name == "phase_5_validation_cycle"
    assert contract.max_iterations == MAX_VALIDATION_CYCLES
    assert contract.until_marker == VERDICT_PASS
    assert contract.marker_field == "verdict"
    assert isinstance(contract.body, list)
    assert len(contract.body) == 12
    body_names = [s.name for s in contract.body]
    assert body_names == _EXPECTED_BODY_NAMES, (
        f"expected body steps {_EXPECTED_BODY_NAMES!r}, got {body_names!r}"
    )
    for s in contract.body:
        assert isinstance(s, StepContract), (
            f"body element {s!r} is not StepContract"
        )


# ─── Test 2: workflow has the composite loop step + 6 GREEN steps ────────────


def test_workflow_uses_composite_loop_step():
    """D352C2D1 + 4C0056FA + 34AEB235: post-migration phase_5_implement_workflow has 11
    top-level steps: composite validation_cycle_loop followed by 10 GREEN steps
    in their original order. (Sprint 95D3E5F6 Step 3 added verify_green_lint_rules
    + verify_green_passing; 4C0056FA added commit_green_code; 34AEB235 added
    verify_green_typecheck between verify_green_passing and commit_green_code.)"""
    wf = phase_5_implement_workflow()
    assert wf.name == "phase_5_implement"
    step_names = [s.name for s in wf.steps]
    assert len(wf.steps) == 12, (
        f"expected 12 top-level steps (1 composite + 11 GREEN), "
        f"got {len(wf.steps)}: {step_names!r}"
    )
    assert step_names[0] == "validation_cycle_loop", (
        f"expected first step 'validation_cycle_loop', got {step_names[0]!r}"
    )
    assert step_names[1:] == _EXPECTED_GREEN_NAMES, (
        f"expected GREEN tail {_EXPECTED_GREEN_NAMES!r}, got {step_names[1:]!r}"
    )


# ─── Test 3: cycle-1 FAIL no longer returns E_VALIDATION_RETRY ───────────────


def test_gate_on_validation_cycle1_fail_returns_ok_for_loop_iteration():
    """D352C2D1: after migration, _gate_on_validation on cycle 1 with FAIL
    returns status='ok' with verdict=FAIL, cycle=2 incremented for the next
    iteration's _build_red_prompt, and findings threaded. The previous
    E_VALIDATION_RETRY path is removed because LoopRunner now drives
    iteration."""
    prev = StepResult(
        status="ok",
        data={
            "verdict": VERDICT_FAIL,
            "validation_doc_path": "/tmp/x.md",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/r.log",
            "cycle": 1,
            "validation_raw": "## Verdict\nFAIL\n\nFinding: missing edge case test.\n",
        },
        duration_ms=0,
        step_name="verify_validation_citations",
    )
    result = _gate_on_validation(None, prev)
    assert result.status == "ok", (
        f"expected ok on cycle-1 FAIL post-migration, got status={result.status!r}, "
        f"error_code={result.error_code!r}, error={result.error!r}"
    )
    assert result.error_code is None, (
        f"expected no error_code (E_VALIDATION_RETRY removed), got {result.error_code!r}"
    )
    assert isinstance(result.data, dict)
    assert result.data.get("verdict") == VERDICT_FAIL
    assert result.data.get("cycle") == 2, (
        f"expected cycle incremented to 2 for next iteration, got {result.data.get('cycle')!r}"
    )
    assert result.data.get("findings"), (
        f"expected findings threaded for cycle-2 prompt, got data={result.data!r}"
    )


# ─── Test 4: cycle-cap FAIL still terminal (regression guard) ────────────────


def test_gate_on_validation_cycle2_fail_still_terminal_abort():
    """D352C2D1: cycle-2 FAIL path UNCHANGED — still returns E_VALIDATION_FAILED
    recoverable=False. This is the cap-reached terminal abort."""
    prev = StepResult(
        status="ok",
        data={
            "verdict": VERDICT_FAIL,
            "validation_doc_path": "/tmp/x.md",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/r.log",
            "cycle": 2,
            "validation_raw": "still bad",
        },
        duration_ms=0,
        step_name="verify_validation_citations",
    )
    result = _gate_on_validation(None, prev)
    assert result.status == "error", (
        f"expected error on cycle-2 FAIL (terminal abort), got {result.status!r}"
    )
    assert result.error_code == "E_VALIDATION_FAILED", (
        f"expected E_VALIDATION_FAILED, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable=False (terminal), got {result.recoverable!r}"
    )


# ─── Test 5: composite step iterates via LoopRunner cycle-1 FAIL → cycle-2 PASS


def test_composite_step_iterates_via_LoopRunner_cycle1_fail_cycle2_pass(tmp_path, monkeypatch):
    """D352C2D1: the composite validation_cycle_loop step runs the 11 inner
    body steps twice when cycle 1 returns FAIL and cycle 2 returns PASS.

    25e75663 R2: llm_command= subprocess stub replaced with _CycleAwareBackend
    registered via register_backend + HAL_RUNNER_BACKEND env. Contract unchanged.
    """
    from bytedigger_engine.workflows.phase_5_implement import _validation_cycle_loop_execute  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)

    backend = _CycleAwareBackend(["FAIL", "PASS"], git_cwd=str(repo))
    register_backend(_LOOP_BACKEND_NAME, backend, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _LOOP_BACKEND_NAME)

    # telemetry_ctx must be set (some body steps emit observability events).
    from bytedigger_engine import telemetry_ctx  # noqa: PLC0415
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-loop-direct", step_name="validation_cycle_loop",
        phase="phase_5_implement",
    )
    try:
        ctx = make_ctx(
            scratchpad,
            git_cwd=str(repo),
        )
        result = _validation_cycle_loop_execute(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"expected ok on cycle-1 FAIL → cycle-2 PASS, got status={result.status!r}, "
        f"error_code={result.error_code!r}, error={result.error!r}"
    )
    # LoopRunner re-ran body twice — cycle-2 artifacts present
    assert (scratchpad / "tests/build-red-output.log").exists()
    assert (scratchpad / "tests/build-red-output-cycle-2.log").exists()
    assert (scratchpad / "reviews/build-opus-validation.md").exists()
    assert (scratchpad / "reviews/build-validation-cycle-2.md").exists()

    metadata = result.metadata or {}
    assert metadata.get("iterations") == 2, (
        f"expected iterations=2 (cycle-1 FAIL then cycle-2 PASS), got "
        f"metadata={metadata!r}"
    )
    assert metadata.get("terminated_by") == "marker", (
        f"expected terminated_by='marker' (PASS marker tripped LoopRunner), got "
        f"metadata={metadata!r}"
    )
    # Composite step name preserved (replace() in the composite execute)
    assert result.step_name == "validation_cycle_loop", (
        f"expected step_name='validation_cycle_loop', got {result.step_name!r}"
    )
