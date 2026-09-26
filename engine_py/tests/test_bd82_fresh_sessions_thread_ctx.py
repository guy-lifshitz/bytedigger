"""bd#82 PR2 — gates and reviews start fresh sessions; step context crosses
thread pools (port of HAL #1898).

Spec: `docs/decisions/2026-09-26-bd82-pr2-fresh-sessions-thread-ctx.md`.

Fresh sessions drive the REAL agent-sdk backend through the chokepoint; only the
SDK's `query` is replaced, and it records the `resume` each call asked for.
Thread context drives real pool threads, the real satisfaction pool, and a real
EventLog.
"""
from __future__ import annotations

import ast
import concurrent.futures
import subprocess
import threading
import types
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)

WORKFLOWS = Path(llm_subprocess.__file__).parent / "workflows"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    agent_sdk._SESSION_CACHE.clear()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    agent_sdk._SESSION_CACHE.clear()
    reset_backends()


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "commit", "--allow-empty", "-qm", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    return root


# ─── fresh sessions ───────────────────────────────────────────────────────────


@pytest.fixture
def sdk(monkeypatch, tmp_path):
    """Real agent-sdk backend; `query` records each call's `resume`."""
    import claude_agent_sdk  # noqa: PLC0415

    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    resumes: list[object] = []

    async def _query(*, prompt, options):
        resumes.append(options.resume)
        if prompt == "fail":
            raise ValueError("non-retryable failure")
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=f"sess-{len(resumes)}", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")  # the error path must not reach the network
    agent_sdk.register()
    monkeypatch.chdir(_repo(tmp_path))
    telemetry_ctx.set_current_run(
        event_log=EventLog(tmp_path / "events.jsonl"), run_id="run-bd82", step_name="s",
        phase="p",
    )
    return resumes


def _call(step_name="invoke_review_llm", *, hard_gate=False, fresh_session=None, model="opus",
          stable_prefix="", ok=True):
    kwargs = {} if fresh_session is None else {"fresh_session": fresh_session}
    if stable_prefix:
        kwargs["stable_prefix"] = stable_prefix
    res = invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=10, step_name=step_name,
        hard_gate=hard_gate, gate_label="g", allowed_tools=["Read"],
        backend="agent-sdk", idle_timeout_sec=0, **kwargs,
    )
    if ok:
        assert res.status == "ok", (res.error_code, res.error)
    return res


def test_f1_worker_still_resumes(sdk):
    """NEGATIVE LEG: a non-gate call without fresh_session keeps warm resume."""
    _call(step_name="invoke_fix_llm")
    _call(step_name="invoke_fix_llm")
    assert sdk == [None, "sess-1"]


def test_f2_hard_gate_never_resumes(sdk):
    _call(hard_gate=True)
    _call(hard_gate=True)
    assert sdk == [None, None], "a gate must not read its own earlier transcript"


def test_f3_phase45_gate_does_not_leak_into_phase6_review(sdk):
    """Phase 4.5's gated `invoke_review_llm`, then phase 6's (non-gate, fresh):
    the same cache key, and still no shared session."""
    _call(hard_gate=True)
    _call(fresh_session=True)
    assert sdk == [None, None]


def test_f4_fresh_session_is_not_offered_to_later_calls(sdk):
    """A fresh session is neither read nor written: a later warm call on the same
    key must not resume it."""
    _call(hard_gate=True)
    _call()
    assert sdk == [None, None]


def test_f5_explicit_fresh_worker(sdk):
    _call()
    _call(fresh_session=True)
    assert sdk == [None, None]


def test_f2b_hard_gate_with_stable_prefix_never_resumes(sdk):
    """Production gates pass a stable_prefix — the other dispatch branch."""
    _call(hard_gate=True, stable_prefix="p")
    _call(hard_gate=True, stable_prefix="p")
    assert sdk == [None, None]


def test_f4b_fresh_call_leaves_the_warm_session_alone(sdk):
    """warm → fresh → warm: the fresh call neither replaces nor evicts the
    worker's session, even when it fails."""
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    _call()
    cached = dict(agent_sdk._SESSION_CACHE)
    _call(fresh_session=True)
    res = invoke_llm_subprocess(
        prompt="fail", model="opus", timeout_sec=10, step_name="invoke_review_llm",
        allowed_tools=["Read"], backend="agent-sdk", idle_timeout_sec=0, fresh_session=True,
    )
    assert res.status == "error"
    assert agent_sdk._SESSION_CACHE == cached
    _call()
    assert sdk == [None, None, None, "sess-1"]


def test_f6_phase6_review_asks_for_a_fresh_session():
    """The phase 6 reviewer dispatches with fresh_session=True."""
    from bytedigger_engine.workflows.phase_6_review import _invoke_review_llm  # noqa: PLC0415

    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend("claude-subprocess", _spy, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)
    prev = StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "doc_path": "doc.md", "spec_path": "spec.md", "red_log_path": "red.log",
        "green_log_path": "green.log", "prompt": "review this", "stable_prefix": "review",
    })

    _invoke_review_llm(types.SimpleNamespace(org_config={}), prev)

    assert len(seen) == 1
    assert seen[0].get("fresh_session") is True


def _register_warm_spy(name):
    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _spy, manifest_source="git_diff",
                     capabilities={"tool_allowlist", "warm_resume"}, overwrite=True)
    return seen


@pytest.mark.parametrize("stable_prefix", ["", "p"])
def test_f6b_chokepoint_resolves_fresh_for_gates(stable_prefix):
    """The `fresh_session or hard_gate` rule lives in the chokepoint: any
    warm_resume backend gets fresh_session=True for a gate, on both branches."""
    seen = _register_warm_spy("bd82-warm")
    invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="s", hard_gate=True, gate_label="g",
        allowed_tools=["Read"], backend="bd82-warm", idle_timeout_sec=0,
        stable_prefix=stable_prefix,
    )
    invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="s", allowed_tools=["Read"],
        backend="bd82-warm", idle_timeout_sec=0, stable_prefix=stable_prefix,
    )
    assert [c["fresh_session"] for c in seen] == [True, False]


def test_f6c_decorrelated_verifier_asks_for_a_fresh_session():
    """The decorrelated verifier is a judge too — it must not read its earlier verdict."""
    from bytedigger_engine.workflows.phase_6_review import _invoke_decorr_llm  # noqa: PLC0415

    seen = _register_warm_spy("claude-subprocess")
    prev = StepResult(status="ok", duration_ms=0, step_name="build_decorr_prompt",
                      data={"prompt": "verify"})
    _invoke_decorr_llm(types.SimpleNamespace(org_config={}), prev)
    assert len(seen) == 1 and seen[0].get("fresh_session") is True


def test_f2c_agent_sdk_gate_fresh_even_without_the_capability(sdk):
    """Defence in depth: a registration that omits warm_resume never forwards
    fresh_session, yet the backend itself treats a hard gate as fresh."""
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    register_backend("agent-sdk", agent_sdk.agent_sdk_backend, manifest_source="git_diff",
                     capabilities={"tool_allowlist"}, overwrite=True)
    _call(hard_gate=True)
    _call(hard_gate=True)
    assert sdk == [None, None]


def test_f6d_hang_fallback_forwards_the_effective_value():
    """The GH1169 re-dispatch is a dispatch: a gate stays fresh on the fallback target."""
    def _hung(**kwargs):
        return StepResult(status="error", data={"hang_attempts": 1}, duration_ms=1,
                          step_name=kwargs["step_name"], error="hung",
                          error_code="E_LLM_API_TIMEOUT")

    register_backend("agent-sdk", _hung, manifest_source="harness_tool_record",
                     capabilities={"tool_allowlist"}, overwrite=True)
    seen = _register_warm_spy("claude-subprocess")
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="s", hard_gate=True, gate_label="g",
        allowed_tools=["Read"], backend="agent-sdk", idle_timeout_sec=0,
    )
    assert res.status == "ok", res.error
    assert [c["fresh_session"] for c in seen] == [True]


def test_f7_backend_without_warm_resume_never_receives_the_kwarg():
    """Third-party compat: a strict-signature backend keeps working."""
    calls: list[str] = []

    def _strict(*, prompt, model, timeout_sec, step_name, extra_data, allowed_tools,
                run_ctx, hard_gate, gate_label, straggler_cfg, idle_timeout_sec):
        calls.append(step_name)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1, step_name=step_name)

    register_backend("bd82-strict", _strict, manifest_source="git_diff",
                     capabilities={"tool_allowlist"})
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="s", hard_gate=True,
        gate_label="g", allowed_tools=["Read"], backend="bd82-strict", idle_timeout_sec=0,
        fresh_session=True,
    )
    assert res.status == "ok", res.error
    assert calls == ["s"]


def test_f8_agent_sdk_declares_warm_resume():
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    agent_sdk.register()
    assert "warm_resume" in llm_subprocess._BACKEND_CAPABILITIES["agent-sdk"]


# ─── run_with_current_run ─────────────────────────────────────────────────────


def _parent(log=None, *, run_id="run-A", step_name="parent_step", cycle=1):
    telemetry_ctx.set_current_run(
        event_log=log, run_id=run_id, step_name=step_name, phase="review", tier="opus",
        cycle=cycle,
    )
    return telemetry_ctx.get_current_run()


def _fields(ctx):
    if ctx is None:
        return None
    return (ctx.event_log, ctx.run_id, ctx.step_name, ctx.phase, ctx.tier, ctx.cycle)


def _in_pool(prev, fn, n=3, **kw):
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(telemetry_ctx.run_with_current_run, prev, fn, **kw) for _ in range(n)]
    return [f.result() for f in futures]


def test_t1_workers_see_every_field(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    prev = _parent(log, cycle=1)
    seen = _in_pool(prev, lambda: _fields(telemetry_ctx.get_current_run()))
    assert seen == [(log, "run-A", "parent_step", "review", "opus", 1)] * 3


def test_t3_retry_worker_sees_retry_cycle():
    """§1ab in-phase retry: the parent re-publishes with set_current_run_from at
    cycle 2; workers see cycle 2, not the first entry's."""
    _parent(cycle=1)
    telemetry_ctx.set_current_run(event_log=None, run_id="run-A", step_name="parent_step",
                                  phase="review", tier="opus", cycle=2)
    telemetry_ctx.set_current_run_from(telemetry_ctx.get_current_run(), step_name="retry_step")
    prev = telemetry_ctx.get_current_run()
    seen = _in_pool(prev, lambda: telemetry_ctx.get_current_run().cycle)
    assert seen == [2, 2, 2]


def test_t3b_resume_from_a_non_main_thread():
    """§1ab resume: the parent context lives in a non-main thread (durable resume
    replays there); its pool workers still see it."""
    out: list = []

    def _resumed_parent():
        prev = _parent(run_id="run-resumed")
        out.extend(_in_pool(prev, lambda: telemetry_ctx.get_current_run().run_id))

    t = threading.Thread(target=_resumed_parent)
    t.start()
    t.join()
    assert out == ["run-resumed"] * 3


def test_t4_no_residue_on_reused_thread_after_exception_or_none():
    prev = _parent()

    def _boom():
        raise RuntimeError("worker failed")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        # a foreign task dirties the only pool thread without clearing
        pool.submit(lambda: telemetry_ctx.set_current_run(
            event_log=None, run_id="foreign", step_name="x")).result()
        none_seen = pool.submit(
            telemetry_ctx.run_with_current_run, None, telemetry_ctx.get_current_run).result()
        failed = pool.submit(telemetry_ctx.run_with_current_run, prev, _boom)
        with pytest.raises(RuntimeError):
            failed.result()
        after = pool.submit(telemetry_ctx.get_current_run).result()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(telemetry_ctx.run_with_current_run, prev, lambda: None).result()
        after_success = pool.submit(telemetry_ctx.get_current_run).result()

    assert after_success is None, "the slot is cleared after a successful fn too"
    assert none_seen is None, "prev=None must not inherit a foreign leftover"
    assert after is None, "the slot is cleared even when fn raises"


def test_t5_prev_and_fn_are_positional_only():
    prev = _parent()

    def _callee(*, prev, fn, step_name):
        return (prev, fn, step_name)

    got = telemetry_ctx.run_with_current_run(prev, _callee, prev="p", fn="f", step_name="s")
    assert got == ("p", "f", "s")


def test_t7_two_concurrent_runs_stay_isolated():
    seen: dict[str, list] = {"A": [], "B": []}
    barrier = threading.Barrier(2, timeout=10)

    def _drive(label):
        prev = _parent(run_id=f"run-{label}")
        barrier.wait()
        seen[label].extend(_in_pool(prev, lambda: telemetry_ctx.get_current_run().run_id))

    threads = [threading.Thread(target=_drive, args=(x,)) for x in "AB"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"A": ["run-A"] * 3, "B": ["run-B"] * 3}


# ─── the satisfaction pool ────────────────────────────────────────────────────


def test_t6_satisfaction_workers_carry_the_step_context(tmp_path):
    from bytedigger_engine.workflows.phase_6_review import (  # noqa: PLC0415
        _run_satisfaction_evaluators_parallel,
    )

    log = EventLog(tmp_path / "e.jsonl")
    _parent(log, run_id="run-sat", step_name="satisfaction", cycle=2)
    seen: list = []
    lock = threading.Lock()

    def _spy(**kwargs):
        ctx = kwargs["run_ctx"]
        assert "fresh_session" not in kwargs, "capability-gated, not signature-sniffed"
        with lock:
            seen.append((kwargs["step_name"], ctx.run_id if ctx else None,
                         ctx.phase if ctx else None, ctx.cycle if ctx else None,
                         ctx.step_name if ctx else None))
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend("claude-subprocess", _spy, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist"}, overwrite=True)

    results = _run_satisfaction_evaluators_parallel("p", "opus", 5, {}, n=3)

    assert [r.status for r in results] == ["ok"] * 3
    # One registered step name for every evaluator: gates are fresh, so a
    # per-worker name would isolate nothing and would split the step's name.
    assert {r.step_name for r in results} == {"invoke_satisfaction_llm"}
    # the slot keeps the parent's step name
    assert seen == [("invoke_satisfaction_llm", "run-sat", "review", 2, "satisfaction")] * 3
    resolved = [e for e in log.read_all() if e["event_type"] == "runner_backend_resolved"]
    assert len(resolved) == 3, "each worker emits into the parent's run"
    assert {e["run_id"] for e in resolved} == {"run-sat"}
    assert telemetry_ctx.get_current_run().run_id == "run-sat", "parent context untouched"


# ─── enforcement pin ──────────────────────────────────────────────────────────


def _is_wrapper(node):
    return (isinstance(node, ast.Attribute) and node.attr == "run_with_current_run"
            and isinstance(node.value, ast.Name) and node.value.id == "telemetry_ctx")


def test_t9_every_pool_dispatch_in_workflows_goes_through_the_wrapper():
    offenders: list[str] = []
    submits = 0
    for path in sorted(WORKFLOWS.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "submit":
                submits += 1
                if not node.args or not _is_wrapper(node.args[0]):
                    offenders.append(f"{path.name}:{node.lineno} submit without wrapper")
                elif len(node.args) < 2 or (
                    isinstance(node.args[1], ast.Constant) and node.args[1].value is None
                ):
                    offenders.append(f"{path.name}:{node.lineno} wrapper given a literal None")
            pool_map = name == "map" and isinstance(func, ast.Attribute) and any(
                word in ast.unparse(func.value).lower() for word in ("executor", "pool"))
            if pool_map or name in ("Thread", "Timer", "run_in_executor", "to_thread"):
                offenders.append(f"{path.name}:{node.lineno} {name} bypasses the wrapper")
    assert submits >= 1, "the scan must see the satisfaction pool"
    assert offenders == []
