"""Integration test for F96D3539 — green_watchdog must halt the engine.

Companion to test_phase_5_implement_F96D3539.py (4 unit tests on isolated
step.execute()). The unit tests pin the watchdog's StepResult, but cannot
prove that recoverable=False on E_GREEN_WATCHDOG actually causes
WorkflowEngine.execute() to halt without firing downstream steps.

Reviewer (2026-05-02 F96D3539 ship): "engine.py code path was hand-verified
(line 205, not skip_on_error) but no test pins it. Risk: if engine.py
recoverable semantics change, watchdog silently degrades to advisory."

This test pins the integration: with a fake invoke_green_llm returning
duration_ms=2_400_000 (trips the 2x wall-clock multiplier on the default
900s timeout), the engine MUST emit step_finished green_watchdog with
status=error AND must NOT emit step_started for any downstream step.

Pattern mirrors test_pipeline_recovery.py: minimal git repo, seeded spec,
llm_command stub for the RED + validator phases. The invoke_green_llm step
is swapped at workflow-build time for a fake that yields the duration we
want — the simplest way to exercise the watchdog deterministically without
running a 40-minute fake LLM.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).parent

import pytest  # noqa: E402

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import SPEC_DOC_RELPATH, phase_5_implement_workflow  # noqa: E402

_F96D3539_BACKEND_NAME = "f96d3539-red-validator"


@pytest.fixture(autouse=True)
def _reset_backends_f96d3539():
    """§1i: restore _BACKENDS singleton after every test."""
    register_backend(
        _F96D3539_BACKEND_NAME,
        _RedValidatorBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    yield
    reset_backends()


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    # 25e75663 R4: llm_command= key silently ignored by _resolve_model; removed.
    # Use model="sonnet" so _resolve_model falls back to default (backend controls output).
    org_extra.pop("llm_command", None)  # drop legacy key if callers still pass it
    org = {
        "scratchpad_dir": str(scratchpad),
        "verify_green_delta_enforce": False,
        "model": "sonnet",
        **org_extra,
    }
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
    _commit_red_tests can discover it via git_diff."""
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


def red_validator_stub() -> list[str]:
    """Stub command: RED step writes a RED report, validator emits PASS verdict.

    25e75663: kept as a helper but no longer passed via llm_command=. Callers
    now register a backend that implements the same branching logic.
    """
    code = (
        "import sys\n"
        "p = sys.stdin.read()\n"
        "if 'Opus validator' in p:\n"
        "    sys.stdout.write('Verdict: PASS\\n')\n"
        "else:\n"
        "    sys.stdout.write('RED COMPLETE — 1 tests written, all failing. Files: [x]\\n\\n' + p)\n"
    )
    return ["python3", "-c", code]


class _RedValidatorBackend:
    """25e75663 R2: registered backend replacing the red_validator_stub subprocess.

    Returns canned RED output or PASS verdict depending on prompt content,
    mirroring the original python3 -c stub logic. Behavioral contract unchanged.
    """
    def __call__(self, **kw) -> StepResult:
        prompt = kw.get("prompt", "")
        if "Opus validator" in prompt:
            raw = "Verdict: PASS\n"
        else:
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


def fake_invoke_green_llm_blowup(_ctx, prev) -> StepResult:
    """Fake invoke_green_llm that returns a duration tripping the wall watchdog.

    Default GREEN timeout = 900s. Watchdog wall multiplier = 2 → trips at 1800s.
    Returning 2_400_000 ms (2400s) puts us comfortably over the threshold.
    Tokens kept low so only the wall-clock branch fires.

    8FFC0E05 augmentation: forward ALL of prev.data (including red_commit_sha set
    by commit_red_tests) instead of explicitly listing keys. Post-swap,
    commit_green_code runs before green_watchdog and requires red_commit_sha to be
    present in prev.data. The old explicit-key dict omitted red_commit_sha, which
    would cause E_MISSING_RED_BOUNDARY and halt before watchdog could fire.

    GH297: mirrors a real GREEN worker flipping the RED-written test(s) to
    passing before returning, so red-verify saw failures but verify_green_passing
    sees a clean pass (chain flows through to green_watchdog).
    """
    base_data = dict(prev.data) if isinstance(prev.data, dict) else {}
    git_cwd = (_ctx.org_config or {}).get("git_cwd") if getattr(_ctx, "org_config", None) else None
    if git_cwd:
        tests_dir = Path(git_cwd) / "tests"
        if tests_dir.is_dir():
            import re as _re
            for f in tests_dir.glob("test_*.py"):
                m = _re.search(r"def (test_\w+)\(", f.read_text())
                fn_name = m.group(1) if m else "test_stub"
                f.write_text(f"def {fn_name}(): assert True\n")
    return StepResult(
        status="ok",
        data={
            **base_data,
            "duration_ms": 2_400_000,
            "tokens_out": 100,
            "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n",
            # 4C03CCED Ship 1C: manifest fields required by commit_green_code
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )


def workflow_with_fake_green():
    """Real phase_5_implement workflow with invoke_green_llm swapped for the fake."""
    wf = phase_5_implement_workflow()
    new_steps = []
    for step in wf.steps:
        if step.name == "invoke_green_llm":
            new_steps.append(StepContract(name="invoke_green_llm", execute=fake_invoke_green_llm_blowup))
        else:
            new_steps.append(step)
    return replace(wf, steps=new_steps)


def test_green_watchdog_halts_engine_on_wall_blowup(tmp_path, monkeypatch):
    """Integration: 2_400_000 ms duration must HALT the engine.

    25e75663 R2: llm_command= stub replaced with registered backend via
    HAL_RUNNER_BACKEND env. Behavioral contract unchanged.

    Asserts:
      1. Engine returns status=escalate, error_code=E_GREEN_WATCHDOG_ESCALATE, recoverable=False.
      2. step_finished event present for green_watchdog with status=escalate.
      3. NO step_started events after green_watchdog — engine halted.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _F96D3539_BACKEND_NAME)
    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", workflow_with_fake_green())

    result, _ = eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r-watchdog-int",
    )

    # 1. Engine surfaces watchdog error verbatim.
    assert result.status == "escalate"
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE"
    assert result.recoverable is False

    events = log.read_all()
    started = [e for e in events if e["event_type"] == "step_started"]
    finished = [e for e in events if e["event_type"] == "step_finished"]
    started_names = [e["payload"]["step_name"] for e in started]
    finished_by_name = {e["payload"]["step_name"]: e for e in finished}

    # 2. green_watchdog ran and finished as error.
    assert "green_watchdog" in started_names
    assert "green_watchdog" in finished_by_name
    assert finished_by_name["green_watchdog"]["payload"]["status"] == "escalate"

    # 3. Halt invariant — green_watchdog is now the terminal step (8FFC0E05).
    # Post-swap, commit_green_code RUNS before watchdog observes budget breach,
    # preserving the GREEN ship across watchdog hard-fail. Nothing follows watchdog.
    if "green_watchdog" in started_names:
        wd_idx = started_names.index("green_watchdog")
        post_watchdog = [n for i, n in enumerate(started_names) if i > wd_idx and n != "green_watchdog"]
        assert post_watchdog == [], (
            f"Engine continued past green_watchdog. Post-watchdog: {post_watchdog}"
        )
