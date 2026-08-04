"""RED tests for 8FFC0E05 — move green_watchdog after commit_green_code.

Spec: SHARED/memory/Decisions/2026-05-17_8FFC0E05_watchdog_after_commit_spec.md

Pre-GREEN expected state: 4F / 4P
  FAIL: AC1, AC2, AC3, AC8
    - AC1: steps[-1] is currently "commit_green_code", not "green_watchdog"
    - AC2: steps[-2] is currently "verify_green_passing", not "commit_green_code"
    - AC3: step_names list has "green_watchdog" at index 4, not index 8
    - AC8: pre-swap, watchdog fires at position 4 and halts engine before
           commit_green_code ever starts — no commit_green_code events exist
  PASS: AC4, AC5, AC6, AC7
    - AC4: 10 steps both before and after swap (no add/remove)
    - AC5: watchdog RetryPolicy parameters unchanged by swap
    - AC6: watchdog skip_on_error unchanged by swap
    - AC7: engine still surfaces E_GREEN_WATCHDOG with recoverable=False
           (pre-swap: watchdog at pos 4 fires on wall-clock, same result)

Fixture notes:
  - fake_invoke_green_llm_blowup_with_budget_breach: NEW local fake, not reused
    from F96D3539_integration.py. The F96D3539 fake (a) uses tokens_out=100
    (spec requires 25_001), and (b) does NOT forward red_commit_sha — which
    commit_green_code requires post-swap. This local fake forwards all of
    prev.data and overrides duration_ms / tokens_out / raw_response.
  - Real EventLog, real WorkflowEngine — no mocks of engine internals.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).parent

import pytest  # noqa: E402

from bytedigger_engine.contracts import RetryPolicy, StepContract, StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import SPEC_DOC_RELPATH, phase_5_implement_workflow  # noqa: E402

_8FFC0E05_BACKEND_NAME = "8ffc0e05-red-validator"


@pytest.fixture(autouse=True)
def _reset_backends_8ffc0e05():
    """§1i: restore _BACKENDS singleton after every test."""
    yield
    reset_backends()

# ─── helpers (self-contained — do NOT import from sibling test files) ─────────

_EXPECTED_STEP_NAMES = [
    "validation_cycle_loop",
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


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    # 25e75663 R4: drop legacy llm_command= key (silently ignored by _resolve_model).
    org_extra.pop("llm_command", None)
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
        session_id="s-8ffc0e05",
        persona="hal",
        framework=None,
        domain=None,
    )


def minimal_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one initial commit so _commit_red_tests can discover
    tests/test_stub.py via git-diff (untracked file)."""
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
    test_stub = repo / "tests" / "test_stub.py"
    test_stub.parent.mkdir(parents=True, exist_ok=True)
    test_stub.write_text("def test_stub(): assert False\n")
    return repo


def seed_spec(scratchpad: Path) -> None:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## US1\nAdd foo\n")


def red_validator_stub() -> list[str]:
    """Legacy subprocess stub — kept for reference but no longer passed via llm_command=.

    25e75663: replaced by _RedValidatorBackend registered via register_backend.
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

    Returns canned RED COMPLETE or PASS verdict depending on prompt content.
    Behavioral contract unchanged.
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


def fake_invoke_green_llm_blowup_with_budget_breach(_ctx, prev) -> StepResult:
    """Fake invoke_green_llm returning duration=2_400_000ms AND tokens_out=25_001.

    Trips BOTH wall-clock watchdog (2_400_000ms > 2*900s*1000=1_800_000ms) and
    token watchdog (25_001 >= 5*5000=25_000). Wall-clock check fires first.

    Unlike the F96D3539 fake:
      - tokens_out=25_001 (spec requirement; F96D3539 used 100)
      - Forwards ALL of prev.data so red_commit_sha is available to
        commit_green_code post-swap. F96D3539 fake explicitly listed keys
        and omitted red_commit_sha, which would cause E_MISSING_RED_BOUNDARY
        in commit_green_code after the swap.
      - raw_response includes GREEN COMPLETE marker so write_green_artifact
        succeeds and does not trigger a retry call.

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
            "tokens_out": 25_001,
            "raw_response": (
                "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n"
            ),
            # 4C03CCED Ship 1C: manifest fields required by commit_green_code
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )


def workflow_with_fake_green() -> object:
    """Real phase_5_implement workflow with invoke_green_llm replaced by budget-breach fake."""
    wf = phase_5_implement_workflow()
    new_steps = []
    for s in wf.steps:
        if s.name == "invoke_green_llm":
            new_steps.append(
                StepContract(
                    name="invoke_green_llm",
                    execute=fake_invoke_green_llm_blowup_with_budget_breach,
                )
            )
        else:
            new_steps.append(s)
    return replace(wf, steps=new_steps)


# ─── AC1-AC6: workflow introspection tests ────────────────────────────────────


def test_ac1_last_step_is_green_watchdog():
    """AC1: phase_5_implement_workflow().steps[-1].name == "green_watchdog".

    FAILS pre-swap: steps[-1] is currently "commit_green_code".
    """
    wf = phase_5_implement_workflow()
    assert wf.steps[-1].name == "green_watchdog", (
        f"Expected last step 'green_watchdog', got {wf.steps[-1].name!r}. "
        f"Full order: {[s.name for s in wf.steps]}"
    )


def test_ac2_second_to_last_step_is_commit_green_code():
    """AC2: phase_5_implement_workflow().steps[-2].name == "commit_green_code".

    FAILS pre-swap: steps[-2] is currently "verify_green_passing".
    """
    wf = phase_5_implement_workflow()
    assert wf.steps[-2].name == "commit_green_code", (
        f"Expected second-to-last step 'commit_green_code', got {wf.steps[-2].name!r}. "
        f"Full order: {[s.name for s in wf.steps]}"
    )


def test_ac3_full_step_names_exact_order():
    """AC3: step_names list exactly equals the post-swap canonical order.

    FAILS pre-swap: green_watchdog is at index 4, not index 8.
    """
    wf = phase_5_implement_workflow()
    actual = [s.name for s in wf.steps]
    assert actual == _EXPECTED_STEP_NAMES, (
        f"Step order mismatch.\n  expected: {_EXPECTED_STEP_NAMES}\n  got:      {actual}"
    )


def test_ac4_step_count_is_ten():
    """AC4: len(phase_5_implement_workflow().steps) == 11 (post-34AEB235).

    PASSES pre-swap: swap does not add or remove steps.
    """
    wf = phase_5_implement_workflow()
    assert len(wf.steps) == 12, (
        f"Expected 12 steps, got {len(wf.steps)}: {[s.name for s in wf.steps]}"
    )


def test_ac5_green_watchdog_retry_policy():
    """AC5: green_watchdog step has RetryPolicy(max_retries=2, initial_delay_sec=30.0,
    backoff="exponential") and timeout_sec=10.0.

    Asserted via closure introspection — step() closes over retries and timeout_sec
    as cell variables. StepContract does not expose them as public fields, but they
    are addressable through the execute closure's __code__.co_freevars / __closure__
    cell contents.

    PASSES pre-swap: RetryPolicy parameters are unchanged by the swap.
    """
    wf = phase_5_implement_workflow()
    watchdog_step = next(s for s in wf.steps if s.name == "green_watchdog")

    execute_fn = watchdog_step.execute
    # step() factory creates _execute as a closure; free vars are the step() params.
    freevars = execute_fn.__code__.co_freevars  # tuple of closed-over var names
    cells = execute_fn.__closure__             # tuple of cell objects

    assert freevars is not None and cells is not None, (
        "green_watchdog.execute is not a closure — step() factory may have changed shape"
    )

    cell_map = dict(zip(freevars, [c.cell_contents for c in cells]))

    # Verify retries is a RetryPolicy with the expected parameters.
    assert "retries" in cell_map, (
        f"'retries' not found in closure free vars. Available: {list(freevars)}"
    )
    retries = cell_map["retries"]
    assert isinstance(retries, RetryPolicy), (
        f"Expected RetryPolicy, got {type(retries)}"
    )
    assert retries.max_retries == 2, (
        f"max_retries: expected 2, got {retries.max_retries}"
    )
    assert retries.initial_delay_sec == 30.0, (
        f"initial_delay_sec: expected 30.0, got {retries.initial_delay_sec}"
    )
    assert retries.backoff == "exponential", (
        f"backoff: expected 'exponential', got {retries.backoff!r}"
    )

    # Verify timeout_sec=10.0
    assert "timeout_sec" in cell_map, (
        f"'timeout_sec' not found in closure free vars. Available: {list(freevars)}"
    )
    assert cell_map["timeout_sec"] == 10.0, (
        f"timeout_sec: expected 10.0, got {cell_map['timeout_sec']}"
    )


def test_ac6_green_watchdog_skip_on_error_false():
    """AC6: green_watchdog step skip_on_error is False.

    PASSES pre-swap: skip_on_error is unchanged by the swap.
    """
    wf = phase_5_implement_workflow()
    watchdog_step = next(s for s in wf.steps if s.name == "green_watchdog")
    assert watchdog_step.skip_on_error is False, (
        f"Expected skip_on_error=False, got {watchdog_step.skip_on_error!r}"
    )


# ─── AC7+AC8: integration smoke tests ────────────────────────────────────────


def test_ac7_engine_surfaces_green_watchdog_error(tmp_path, monkeypatch):
    """AC7: engine returns status='escalate', error_code='E_GREEN_WATCHDOG_ESCALATE', recoverable=False.

    25e75663 R2: llm_command= stub replaced with registered backend.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _8FFC0E05_BACKEND_NAME)
    register_backend(_8FFC0E05_BACKEND_NAME, _RedValidatorBackend(),
                     manifest_source="harness_tool_record", overwrite=True)

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events_ac7.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", workflow_with_fake_green())

    result, _ = eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r-8ffc0e05-ac7",
    )

    assert result.status == "escalate", (
        f"Expected status='escalate', got {result.status!r}"
    )
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE", (
        f"Expected error_code='E_GREEN_WATCHDOG_ESCALATE', got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"Expected recoverable=False, got {result.recoverable!r}"
    )


def test_ac8_commit_green_code_precedes_green_watchdog_in_event_log(tmp_path, monkeypatch):
    """AC8: event log shows commit_green_code started+finished(ok) BEFORE green_watchdog started.

    25e75663 R2: llm_command= stub replaced with registered backend.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", _8FFC0E05_BACKEND_NAME)
    register_backend(_8FFC0E05_BACKEND_NAME, _RedValidatorBackend(),
                     manifest_source="harness_tool_record", overwrite=True)

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events_ac8.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p5", workflow_with_fake_green())

    eng.execute(
        "p5",
        make_ctx(scratchpad, git_cwd=str(repo)),
        run_id="r-8ffc0e05-ac8",
    )

    events = log.read_all()
    started_events = [e for e in events if e["event_type"] == "step_started"]
    finished_events = [e for e in events if e["event_type"] == "step_finished"]

    started_names = [e["payload"]["step_name"] for e in started_events]
    finished_by_name = {e["payload"]["step_name"]: e for e in finished_events}

    # commit_green_code must have started.
    assert "commit_green_code" in started_names, (
        f"commit_green_code never started. Started steps: {started_names}"
    )

    # commit_green_code must have finished with ok.
    assert "commit_green_code" in finished_by_name, (
        f"commit_green_code never finished. Finished steps: {list(finished_by_name.keys())}"
    )
    commit_status = finished_by_name["commit_green_code"]["payload"]["status"]
    assert commit_status == "ok", (
        f"commit_green_code finished with status={commit_status!r}, expected 'ok'"
    )

    # green_watchdog must have started.
    assert "green_watchdog" in started_names, (
        f"green_watchdog never started. Started steps: {started_names}"
    )

    # commit_green_code step_started PRECEDES green_watchdog step_started (by index).
    commit_idx = started_names.index("commit_green_code")
    watchdog_idx = started_names.index("green_watchdog")
    assert commit_idx < watchdog_idx, (
        f"commit_green_code (idx={commit_idx}) must precede green_watchdog (idx={watchdog_idx}) "
        f"in started events. Full started order: {started_names}"
    )

    # green_watchdog finished with error.
    assert "green_watchdog" in finished_by_name, (
        f"green_watchdog never finished. Finished steps: {list(finished_by_name.keys())}"
    )
    watchdog_status = finished_by_name["green_watchdog"]["payload"]["status"]
    assert watchdog_status == "escalate", (
        f"green_watchdog finished with status={watchdog_status!r}, expected 'escalate'"
    )

    # No steps started AFTER green_watchdog (engine halted on watchdog error).
    # Find the global index of green_watchdog's step_started in the full events list.
    watchdog_started_event = next(
        e for e in events
        if e["event_type"] == "step_started" and e["payload"]["step_name"] == "green_watchdog"
    )
    watchdog_global_idx = events.index(watchdog_started_event)
    post_watchdog_started = [
        e for e in events[watchdog_global_idx + 1 :]
        if e["event_type"] == "step_started"
    ]
    assert post_watchdog_started == [], (
        f"Engine continued past green_watchdog. Steps started after watchdog: "
        f"{[e['payload']['step_name'] for e in post_watchdog_started]}"
    )
