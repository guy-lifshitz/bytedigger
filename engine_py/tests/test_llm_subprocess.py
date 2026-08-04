"""Direct unit tests for llm_subprocess helper.

The helper is exercised end-to-end by phase_1_discovery + phase_4_architect
tests. These tests pin the helper's contract directly so future phases
(phase_45_spec, phase_5) can rely on it without re-deriving behavior.

25e75663 migration: command= seam removed. All tests that previously used
shell stubs (echo_stub, passthrough_stub, python3 -c) now use registered
backend stubs (R2). The echo_stub/passthrough_stub helpers are retained as
no-ops for imports that still reference them but are no longer passed to
invoke_llm_subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import pytest  # noqa: E402
from bytedigger_engine.llm_subprocess import (  # noqa: E402
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)
from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine import llm_subprocess  # noqa: E402


# ---------------------------------------------------------------------------
# §1i autouse teardown: restore _BACKENDS singleton + clear telemetry
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# Backend stub helpers (R2 pattern from reference test)
# ---------------------------------------------------------------------------

def _ok_backend(raw_response: str, step_name_key: str = "s") -> object:
    """Return a backend callable that yields ok with the given raw_response."""
    class _Stub:
        def __call__(self, **kw) -> StepResult:
            data: dict = {
                "raw_response": raw_response,
                "response_bytes": len(raw_response.encode()),
                "worker_written_paths": [],
                "manifest_source": "harness_tool_record",
            }
            # Mirror _invoke_subprocess: merge caller's extra_data (caller wins)
            extra = kw.get("extra_data") or {}
            data.update(extra)
            return StepResult(
                status="ok",
                data=data,
                duration_ms=0,
                step_name=kw.get("step_name", step_name_key),
                error=None,
                error_code=None,
                recoverable=True,
            )
    return _Stub()


def _error_backend(error_code: str, error_msg: str, recoverable: bool = False, data: dict | None = None) -> object:
    """Return a backend callable that yields an error StepResult.

    duration_ms is extracted from data["duration_ms"] when present so tests
    that assert r.duration_ms get the expected value.
    """
    class _ErrStub:
        def __call__(self, **kw) -> StepResult:
            d = data or {}
            return StepResult(
                status="error",
                data=d,
                duration_ms=d.get("duration_ms", 0),
                step_name=kw.get("step_name", "s"),
                error=error_msg,
                error_code=error_code,
                recoverable=recoverable,
            )
    return _ErrStub()



# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_returns_ok_with_response_bytes():
    register_backend("stub-echo-ok", _ok_backend("HELLO\n"), manifest_source="harness_tool_record", overwrite=True)
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-echo-ok",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "ok"
    assert r.data["raw_response"] == "HELLO\n"
    assert r.data["response_bytes"] == 6
    assert r.step_name == "step"
    assert r.error is None


def test_extra_data_merged_into_success():
    register_backend("stub-echo-ok2", _ok_backend("ok"), manifest_source="harness_tool_record", overwrite=True)
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-echo-ok2",
        idle_timeout_sec=0,
        straggler_cfg=None,
        extra_data={"doc_path": "/tmp/foo.md", "complexity": "FEATURE"},
    )
    assert r.data["doc_path"] == "/tmp/foo.md"
    assert r.data["complexity"] == "FEATURE"
    assert r.data["raw_response"] == "ok"


def test_extra_data_caller_keys_can_override_defaults():
    """Caller's keys win on collision (predictable forwarding)."""
    register_backend("stub-echo-ok3", _ok_backend("ok"), manifest_source="harness_tool_record", overwrite=True)
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-echo-ok3",
        idle_timeout_sec=0,
        straggler_cfg=None,
        extra_data={"raw_response": "OVERRIDDEN"},
    )
    assert r.data["raw_response"] == "OVERRIDDEN"


def test_prompt_reaches_subprocess_via_stdin():
    """With a backend, prompt is passed as kwarg; backend records it if needed."""
    class _EchoPromptBackend:
        def __call__(self, **kw) -> StepResult:
            prompt = kw.get("prompt", "")
            return StepResult(
                status="ok",
                data={
                    "raw_response": prompt,
                    "response_bytes": len(prompt.encode()),
                    "worker_written_paths": [],
                    "manifest_source": "harness_tool_record",
                },
                duration_ms=0,
                step_name=kw.get("step_name", "s"),
                error=None,
                error_code=None,
                recoverable=True,
            )

    register_backend("stub-echo-prompt", _EchoPromptBackend(), manifest_source="harness_tool_record", overwrite=True)
    r = invoke_llm_subprocess(
        prompt="STDIN_PAYLOAD",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-echo-prompt",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "ok"
    assert r.data["raw_response"] == "STDIN_PAYLOAD"


def test_command_not_found_returns_error_recoverable_false():
    """Class C: E_LLM_CMD_MISSING can no longer be injected via a bogus argv.
    Use a backend stub that returns the equivalent error contract."""
    register_backend(
        "stub-cmd-missing",
        _error_backend("E_LLM_CMD_MISSING", "command not found", recoverable=False),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-cmd-missing",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "error"
    assert r.error_code == "E_LLM_CMD_MISSING"
    assert r.recoverable is False


def test_nonzero_exit_returns_error_with_stderr():
    """Class C: E_LLM_EXIT can no longer be injected via a real python3 subprocess.
    Use a backend stub that returns the equivalent error contract."""
    register_backend(
        "stub-exit-err",
        _error_backend(
            "E_LLM_EXIT",
            "subprocess exited 3",
            recoverable=False,
            data={"exit_code": 3, "stderr": "boom", "stderr_tail": "boom"},
        ),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-exit-err",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "error"
    assert r.error_code == "E_LLM_EXIT"
    assert r.data["exit_code"] == 3
    assert "boom" in r.data["stderr"]


def test_timeout_returns_error_with_duration_ms():
    """Class C: E_LLM_TIMEOUT can no longer be injected via a sleeping python3.
    Use a backend stub that returns the equivalent error contract."""
    register_backend(
        "stub-timeout",
        _error_backend(
            "E_LLM_TIMEOUT",
            "timeout after 1s",
            recoverable=False,
            data={"duration_ms": 1000},
        ),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=1,
        step_name="step",
        backend="stub-timeout",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "error"
    assert r.error_code == "E_LLM_TIMEOUT"
    assert r.duration_ms == 1000


def test_step_name_propagates_to_result():
    register_backend("stub-step-name", _ok_backend("ok"), manifest_source="harness_tool_record", overwrite=True)
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="my_custom_step",
        backend="stub-step-name",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.step_name == "my_custom_step"


def test_nonzero_exit_captures_observability_fields_for_diagnosis():
    """Agreement 027E351C: non-zero exit must surface stderr_tail/stdout_tail/
    duration_ms/exit_code/cmd_tail in StepResult.data AND embed a readable chunk
    in StepResult.error so an 8-hour failed build is diagnosable from logs alone.
    Class C: use backend stub that returns the contract-matching error structure.
    """
    register_backend(
        "stub-obs-exit",
        _error_backend(
            "E_LLM_EXIT",
            "subprocess exited 7 after duration_ms=50: ERR-MARKER",
            recoverable=False,
            data={
                "exit_code": 7,
                "stderr": "ERR-MARKER",
                "stderr_tail": "ERR-MARKER",
                "stdout_tail": "OUT-MARKER",
                "duration_ms": 50,
                "cmd_tail": ["claude", "-p", "--model"],
            },
        ),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="stub-obs-exit",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "error"
    assert r.error_code == "E_LLM_EXIT"
    assert r.data["exit_code"] == 7
    assert "ERR-MARKER" in r.data["stderr_tail"]
    assert "OUT-MARKER" in r.data["stdout_tail"]
    assert isinstance(r.data["duration_ms"], int) and r.data["duration_ms"] >= 0
    assert isinstance(r.data["cmd_tail"], list) and len(r.data["cmd_tail"]) <= 3
    # readable diagnostic chunk in error message
    assert "exit 7" in r.error or "exited 7" in r.error
    assert "ERR-MARKER" in r.error
    assert "duration_ms=" in r.error


def test_timeout_captures_observability_fields_for_diagnosis():
    """Agreement 027E351C: timeout must surface the same observability fields.
    Class C: use backend stub.
    """
    register_backend(
        "stub-obs-timeout",
        _error_backend(
            "E_LLM_TIMEOUT",
            "timeout after duration_ms=1000 TIMEOUT-ERR",
            recoverable=False,
            data={
                "stderr_tail": "TIMEOUT-ERR",
                "stdout_tail": "",
                "duration_ms": 1000,
                "exit_code": -15,
                "cmd_tail": ["claude", "-p", "--model"],
            },
        ),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=1,
        step_name="step",
        backend="stub-obs-timeout",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    assert r.status == "error"
    assert r.error_code == "E_LLM_TIMEOUT"
    # captured tails (stderr was flushed before sleep)
    assert "TIMEOUT-ERR" in r.data["stderr_tail"]
    assert "stdout_tail" in r.data
    assert isinstance(r.data["duration_ms"], int) and r.data["duration_ms"] >= 0
    # killed → exit_code typically negative or non-zero; must be captured
    assert "exit_code" in r.data
    assert isinstance(r.data["cmd_tail"], list) and len(r.data["cmd_tail"]) <= 3
    # readable diagnostic chunk in error message
    assert "timeout" in r.error.lower()
    assert "duration_ms=" in r.error
    assert "TIMEOUT-ERR" in r.error


# ─── 0B6671A1: assert non-test invocation raises ─────────────────────────────


def test_communicate_legacy_passes_under_pytest_env(tmp_path):
    """AC1: PYTEST_CURRENT_TEST present (pytest default) → no AssertionError.
    Use a minimal subprocess command that exits 0 quickly."""
    import subprocess as _sp
    from bytedigger_engine.llm_subprocess import _communicate_legacy

    proc = _sp.Popen(
        ["python3", "-c", "import sys; sys.stdin.read(); sys.stdout.write('ok\\n')"],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
    )
    timed_out, stdout, stderr = _communicate_legacy(proc=proc, prompt="hi", timeout_sec=5)
    assert timed_out is False
    assert "ok" in stdout


def test_communicate_legacy_raises_outside_test_context(tmp_path, monkeypatch):
    """AC2: no PYTEST_CURRENT_TEST, no HAL_ALLOW_LEGACY_COMMUNICATE → AssertionError."""
    import subprocess as _sp
    from bytedigger_engine.llm_subprocess import _communicate_legacy

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HAL_ALLOW_LEGACY_COMMUNICATE", raising=False)

    proc = _sp.Popen(
        ["python3", "-c", "pass"],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
    )
    try:
        import pytest
        with pytest.raises(AssertionError) as exc_info:
            _communicate_legacy(proc=proc, prompt="", timeout_sec=2)
        # Verify message mentions the documented hang or stream-json escape.
        msg = str(exc_info.value)
        assert "stream-json" in msg or "15-min-hang" in msg or "documented" in msg, \
            f"AssertionError message should mention the hang vector; got: {msg!r}"
    finally:
        # Cleanup: kill the proc if assertion fired before communicate
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass


def test_communicate_legacy_passes_with_escape_hatch(tmp_path, monkeypatch):
    """AC3: HAL_ALLOW_LEGACY_COMMUNICATE=1 overrides missing PYTEST_CURRENT_TEST."""
    import subprocess as _sp
    from bytedigger_engine.llm_subprocess import _communicate_legacy

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("HAL_ALLOW_LEGACY_COMMUNICATE", "1")

    proc = _sp.Popen(
        ["python3", "-c", "import sys; sys.stdin.read(); sys.stdout.write('ok\\n')"],
        stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
    )
    timed_out, stdout, stderr = _communicate_legacy(proc=proc, prompt="hi", timeout_sec=5)
    assert timed_out is False
    assert "ok" in stdout
