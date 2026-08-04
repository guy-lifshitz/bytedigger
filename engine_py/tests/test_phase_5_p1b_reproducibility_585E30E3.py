"""RED tests for 585E30E3 P1b: 5-run reproducibility gate.

AC1-AC8: unit tests for reproducibility.py (new module — does not exist yet).
AC9-AC12: integration tests for _verify_green_passing wiring.

All 12 tests MUST FAIL pre-GREEN (module `reproducibility` does not exist).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.reproducibility import verify_count_reproducible, _REPRODUCIBILITY_RUNS, _pin_pytest_collection  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402


# ─── ctx builder (mirrors F9F7E4FD sibling) ───────────────────────────────────


def _make_ctx_with_cwd(git_cwd: str) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd},
        question="Add foo to bar",
        session_id="test-585E30E3-p1b",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list) -> StepResult:
    return StepResult(
        status="ok",
        data={"red_test_paths": red_test_paths},
        duration_ms=0,
        step_name="prev",
    )


# ─── AC1: module importable, _REPRODUCIBILITY_RUNS == 5 ──────────────────────


def test_ac1_module_importable_and_constant():
    assert _REPRODUCIBILITY_RUNS == 5


# ─── AC2: run_fn called exactly n_runs times (default 5) ─────────────────────


def test_ac2_run_fn_called_five_times():
    call_log: list[tuple] = []

    class _FakeResult:
        n_passed = 3
        n_failed = 0

    def _fake_run(argv, git_cwd, *, timeout=120):
        call_log.append((argv, git_cwd, timeout))
        return _FakeResult()

    verify_count_reproducible(_fake_run, ["pytest", "t.py"], "/tmp")
    assert len(call_log) == 5


# ─── AC3: identical counts → (True, [(p,f)]*5) ───────────────────────────────


def test_ac3_identical_counts_reproducible():
    class _FakeResult:
        def __init__(self, p, f):
            self.n_passed = p
            self.n_failed = f

    def _stable_run(argv, git_cwd, *, timeout=120):
        return _FakeResult(10, 0)

    is_rep, counts = verify_count_reproducible(_stable_run, ["pytest", "t.py"], "/tmp")
    assert is_rep is True
    assert counts == [(10, 0)] * 5


# ─── AC4: differing counts → (False, all 5 recorded) ────────────────────────


def test_ac4_differing_counts_not_reproducible():
    run_index = [0]

    class _FakeResult:
        def __init__(self, idx):
            self.n_passed = 10 if idx != 2 else 9
            self.n_failed = 0

    def _unstable_run(argv, git_cwd, *, timeout=120):
        idx = run_index[0]
        run_index[0] += 1
        return _FakeResult(idx)

    is_rep, counts = verify_count_reproducible(_unstable_run, ["pytest", "t.py"], "/tmp")
    assert is_rep is False
    assert len(counts) == 5
    # run 2 (index 2) has different n_passed
    assert counts[2] != counts[0]


# ─── AC5: run_fn raises on one run → (-1,-1) slot, still 5 entries ───────────


def test_ac5_run_fn_raises_records_sentinel():
    run_index = [0]

    class _FakeResult:
        n_passed = 5
        n_failed = 0

    def _raising_run(argv, git_cwd, *, timeout=120):
        idx = run_index[0]
        run_index[0] += 1
        if idx == 1:
            raise RuntimeError("test runner crashed")
        return _FakeResult()

    is_rep, counts = verify_count_reproducible(_raising_run, ["pytest", "t.py"], "/tmp")
    assert is_rep is False
    assert len(counts) == 5
    assert counts[1] == (-1, -1)


# ─── AC6: n_runs override respected ──────────────────────────────────────────


def test_ac6_n_runs_override():
    call_log: list[int] = []

    class _FakeResult:
        n_passed = 2
        n_failed = 0

    def _counting_run(argv, git_cwd, *, timeout=120):
        call_log.append(1)
        return _FakeResult()

    is_rep, counts = verify_count_reproducible(
        _counting_run, ["pytest", "t.py"], "/tmp", n_runs=3
    )
    assert len(call_log) == 3
    assert len(counts) == 3
    assert is_rep is True


# ─── AC7: _pin_pytest_collection inserts flag; idempotent ────────────────────


def test_ac7_pin_pytest_collection_inserts_and_idempotent():
    argv_in = ["python", "-m", "pytest", "t.py"]
    result = _pin_pytest_collection(argv_in)
    assert "-p" in result
    assert "no:cacheprovider" in result
    # idempotent
    result2 = _pin_pytest_collection(result)
    assert result2.count("no:cacheprovider") == 1


# ─── AC8: non-pytest argv unchanged ──────────────────────────────────────────


def test_ac8_non_pytest_argv_unchanged():
    argv_in = ["bun", "test", "foo.test.ts"]
    result = _pin_pytest_collection(argv_in)
    assert result == argv_in


# ─── AC9-AC12: _verify_green_passing wiring ──────────────────────────────────


def test_ac9_reproducible_py_group_ok_path_emits_verdict(tmp_path, monkeypatch):
    """Reproducible py group preserves status=='ok'; emits green_reproducibility_verdict."""
    emitted: list[tuple] = []

    class _FakeResult:
        exit_code = 0
        n_passed = 7
        n_failed = 0

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return _FakeResult()

    def _fake_infer(paths, git_cwd=None):
        return {"groups": [{"kind": "py", "argv": ["python", "-m", "pytest"] + paths, "paths": paths}]}

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_foo.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "ok"
    verdict_events = [e for e in emitted if e[0] == "green_reproducibility_verdict"]
    assert len(verdict_events) == 1
    assert verdict_events[0][1]["is_reproducible"] is True


def test_ac10_non_reproducible_py_group_returns_error_before_trust_loop(tmp_path, monkeypatch):
    """Non-reproducible py group → error StepResult; green_test_outcome NOT emitted."""
    emitted: list[tuple] = []
    run_index = [0]

    class _UnstableResult:
        exit_code = 0
        n_failed = 0

        def __init__(self, idx):
            self.n_passed = 10 if idx != 2 else 9

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        idx = run_index[0]
        run_index[0] += 1
        return _UnstableResult(idx)

    def _fake_infer(paths, git_cwd=None):
        return {"groups": [{"kind": "py", "argv": ["python", "-m", "pytest"] + paths, "paths": paths}]}

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_foo.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_GREEN_NOT_REPRODUCIBLE"
    assert result.recoverable is True
    trust_events = [e for e in emitted if e[0] == "green_test_outcome"]
    assert len(trust_events) == 0


def test_ac11_non_py_group_skips_reproducibility(tmp_path, monkeypatch):
    """ts-only group: no reproducibility check, no E_GREEN_NOT_REPRODUCIBLE, existing path intact."""
    emitted: list[tuple] = []

    class _FakeTsResult:
        exit_code = 0
        n_passed = 4
        n_failed = 0

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return _FakeTsResult()

    def _fake_infer(paths, git_cwd=None):
        return {"groups": [{"kind": "ts", "argv": ["bun", "test"] + paths, "paths": paths}]}

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_foo.test.ts"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "ok"
    reproducibility_events = [e for e in emitted if e[0] == "green_reproducibility_verdict"]
    assert len(reproducibility_events) == 0
    error_codes = [
        getattr(result, "error_code", None)
    ]
    assert "E_GREEN_NOT_REPRODUCIBLE" not in error_codes


def test_ac12_green_reproducibility_verdict_payload_shape(tmp_path, monkeypatch):
    """green_reproducibility_verdict payload has exact keys; n_runs==5, phase==5."""
    emitted: list[tuple] = []

    class _FakeResult:
        exit_code = 0
        n_passed = 3
        n_failed = 0

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return _FakeResult()

    def _fake_infer(paths, git_cwd=None):
        return {"groups": [{"kind": "py", "argv": ["python", "-m", "pytest"] + paths, "paths": paths}]}

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_foo.py"])

    phase_5_implement._verify_green_passing(ctx, prev)

    verdict_events = [e for e in emitted if e[0] == "green_reproducibility_verdict"]
    assert len(verdict_events) == 1
    payload = verdict_events[0][1]
    expected_keys = {"group", "n_runs", "is_reproducible", "counts", "phase"}
    assert set(payload.keys()) == expected_keys
    assert payload["n_runs"] == 5
    assert payload["phase"] == 5


def test_ac10b_binary_missing_defers_to_runner_missing(tmp_path, monkeypatch):
    """Binary missing during P1b gate → E_GREEN_TEST_RUNNER_MISSING, not E_GREEN_NOT_REPRODUCIBLE."""
    emitted: list[tuple] = []

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        raise FileNotFoundError("python: command not found")

    def _fake_infer(paths, git_cwd=None):
        return {"groups": [{"kind": "py", "argv": ["python", "-m", "pytest"] + paths, "paths": paths}]}

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_foo.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.error_code == "E_GREEN_TEST_RUNNER_MISSING"
    assert result.recoverable is False


# ─── AC5b: all runs raise → (False, [(-1,-1)]*5) ────────────────────────────


def test_ac5b_all_runs_raise_not_reproducible():
    def _always_raises(argv, git_cwd, *, timeout=120):
        raise RuntimeError("test runner crashed")

    is_rep, counts = verify_count_reproducible(_always_raises, ["pytest", "t.py"], "/tmp")
    assert is_rep is False
    assert counts == [(-1, -1)] * 5
