"""bd#101 — fresh session by default; warm resume only for listed writer steps.

Spec: `docs/decisions/2026-10-02-bd101-fresh-default-writer-resume.md` (AC1-AC10).

Resume tests drive the REAL agent-sdk backend through `invoke_llm_subprocess`; only
the SDK's `query` is replaced and it records the `resume` each call asked for.
Chokepoint tests use spy backends. AC10 is a pure AST scan of the engine package.
"""
from __future__ import annotations

import ast
import inspect
import subprocess
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

ENGINE_PKG = Path(llm_subprocess.__file__).parent

_SPEC_FAMILIES = frozenset({
    "invoke_red_llm",
    "invoke_green_llm",
    "invoke_green_llm_retry",
    "invoke_fix_llm",
    "invoke_fix_llm_retry",
    "invoke_spec_llm",
})
_WRITER_PARAMS = sorted(_SPEC_FAMILIES) + ["repair_spec_lint"]


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


def _table() -> frozenset:
    """The production writer table, or a clear failure if it is not built yet."""
    table = getattr(llm_subprocess, "_WARM_RESUME_STEPS", None)
    if table is None:
        pytest.fail("llm_subprocess._WARM_RESUME_STEPS is missing (bd#101 not implemented)")
    return table


def _prefixes() -> tuple:
    prefixes = getattr(llm_subprocess, "_WARM_RESUME_STEP_PREFIXES", None)
    if prefixes is None:
        pytest.fail("llm_subprocess._WARM_RESUME_STEP_PREFIXES is missing (bd#101 not implemented)")
    return prefixes


@pytest.fixture
def sdk(monkeypatch, tmp_path):
    """Real agent-sdk backend; `query` records each call's `resume`."""
    import claude_agent_sdk  # noqa: PLC0415

    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    resumes: list[object] = []

    async def _query(*, prompt, options):
        resumes.append(options.resume)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id=f"sess-{len(resumes)}", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")
    agent_sdk.register()
    monkeypatch.chdir(_repo(tmp_path))
    telemetry_ctx.set_current_run(
        event_log=EventLog(tmp_path / "events.jsonl"), run_id="run-bd101", step_name="s",
        phase="p",
    )
    return resumes


def _call(step_name, *, hard_gate=False, fresh_session=None, role=None):
    kwargs = {}
    if fresh_session is not None:
        kwargs["fresh_session"] = fresh_session
    if role is not None:
        kwargs["role"] = role
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=10, step_name=step_name,
        hard_gate=hard_gate, gate_label="g", allowed_tools=["Read"],
        backend="agent-sdk", idle_timeout_sec=0, **kwargs,
    )
    assert res.status == "ok", (res.error_code, res.error)
    return res


def _register_warm_spy(name):
    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _spy, manifest_source="git_diff",
                     capabilities={"tool_allowlist", "warm_resume"}, overwrite=True)
    return seen


def _dispatch(backend, step_name, *, stable_prefix="", role=None, hard_gate=False):
    kwargs = {"role": role} if role is not None else {}
    return invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name=step_name, hard_gate=hard_gate,
        gate_label="g", allowed_tools=["Read"], backend=backend, idle_timeout_sec=0,
        stable_prefix=stable_prefix, **kwargs,
    )


# --- AC1 ---------------------------------------------------------------------


def test_ac1_unlisted_non_gate_is_fresh(sdk):
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    _call("invoke_new_judge")
    _call("invoke_new_judge")
    assert sdk == [None, None]
    assert not agent_sdk._SESSION_CACHE


# --- AC2 ---------------------------------------------------------------------


def test_ac2_table_is_exactly_the_spec_families():
    assert frozenset(_table()) == _SPEC_FAMILIES
    assert tuple(_prefixes()) == ("repair_",)


@pytest.mark.parametrize("step_name", _WRITER_PARAMS)
def test_ac2_listed_writers_resume(sdk, step_name):
    _call(step_name)
    _call(step_name)
    assert sdk == [None, "sess-1"]


# --- AC3 / AC4 / AC5 ---------------------------------------------------------


def test_ac3_explicit_fresh_beats_the_table(sdk):
    _call("invoke_green_llm")
    _call("invoke_green_llm", fresh_session=True)
    assert sdk == [None, None]


def test_ac4_judge_role_beats_the_table(sdk):
    _call("invoke_fix_llm", role="judge")
    _call("invoke_fix_llm", role="judge")
    assert sdk == [None, None]


def test_ac5_hard_gate_beats_the_table(sdk):
    _call("invoke_green_llm", hard_gate=True)
    _call("invoke_green_llm", hard_gate=True)
    assert sdk == [None, None]


# --- AC6 ---------------------------------------------------------------------


def test_ac6_dotted_family_shares_the_listed_session(sdk):
    _call("invoke_green_llm.a")
    _call("invoke_green_llm.b")
    assert sdk == [None, "sess-1"]


# --- AC7 ---------------------------------------------------------------------


@pytest.mark.parametrize("stable_prefix", ["", "p"])
def test_ac7_chokepoint_value_both_branches(stable_prefix):
    seen = _register_warm_spy("bd101-warm")
    _dispatch("bd101-warm", "s", stable_prefix=stable_prefix)
    _dispatch("bd101-warm", "invoke_green_llm", stable_prefix=stable_prefix)
    _dispatch("bd101-warm", "repair_x", stable_prefix=stable_prefix)
    _dispatch("bd101-warm", "invoke_green_llm", stable_prefix=stable_prefix, role="judge")
    assert [c["fresh_session"] for c in seen] == [True, False, False, True]


@pytest.mark.parametrize("stable_prefix", ["", "p"])
def test_ac7_hard_gate_term_alone_forces_fresh_on_a_listed_worker_step(stable_prefix):
    """role="worker" so the judge term cannot be what makes this fresh."""
    seen = _register_warm_spy("bd101-warm")
    _dispatch("bd101-warm", "invoke_green_llm", stable_prefix=stable_prefix, role="worker",
              hard_gate=True)
    assert [c["fresh_session"] for c in seen] == [True]


@pytest.mark.parametrize("stable_prefix", ["", "p"])
def test_ac7_backend_without_warm_resume_gets_no_fresh_session_key(stable_prefix):
    seen: list[dict] = []

    def _plain(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend("bd101-plain", _plain, manifest_source="git_diff",
                     capabilities={"tool_allowlist"}, overwrite=True)
    res = _dispatch("bd101-plain", "s", stable_prefix=stable_prefix)
    assert res.status == "ok", res.error
    assert len(seen) == 1 and "fresh_session" not in seen[0]


# --- AC8 ---------------------------------------------------------------------


def _hung(**kwargs):
    return StepResult(status="error", data={"hang_attempts": 1}, duration_ms=1,
                      step_name=kwargs["step_name"], error="hung",
                      error_code="E_LLM_API_TIMEOUT")


@pytest.mark.parametrize("step_name, role, expected", [
    ("s", None, True),
    ("invoke_fix_llm", None, False),
    ("invoke_fix_llm", "judge", True),
])
def test_ac8_fallback_forwards_the_effective_value(monkeypatch, step_name, role, expected):
    monkeypatch.delenv("HAL_AGENT_SDK_HANG_FALLBACK", raising=False)
    register_backend("agent-sdk", _hung, manifest_source="harness_tool_record",
                     capabilities={"tool_allowlist"}, overwrite=True)
    seen = _register_warm_spy("claude-subprocess")
    res = _dispatch("agent-sdk", step_name, role=role)
    assert res.status == "ok", res.error
    assert [c["fresh_session"] for c in seen] == [expected]


# --- AC9 ---------------------------------------------------------------------


def test_ac9_backend_without_warm_resume_is_always_fresh(sdk):
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    register_backend("agent-sdk", agent_sdk.agent_sdk_backend, manifest_source="git_diff",
                     capabilities={"tool_allowlist"}, overwrite=True)
    _call("invoke_green_llm")
    _call("invoke_green_llm")
    assert sdk == [None, None]


def test_ac9_agent_sdk_backend_fresh_session_defaults_to_true():
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    param = inspect.signature(agent_sdk.agent_sdk_backend).parameters["fresh_session"]
    assert param.default is True


# --- AC12 --------------------------------------------------------------------


def test_ac12_changelog_unreleased_changed_mentions_the_opt_in():
    import re

    from helpers.changelog import require_entry, blocks

    text = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text()
    section = require_entry(text, re.compile(r"bd#101\b"))
    changed = blocks(section, "Changed")
    assert len(changed) == 1, "exactly one '### Changed' in the section carrying the bd#101 entry"
    assert any("bd#101" in ln and "_WARM_RESUME_STEPS" in ln for ln in changed[0].splitlines())


# --- AC10 --------------------------------------------------------------------


def _engine_trees():
    for path in sorted(ENGINE_PKG.rglob("*.py")):
        yield path, ast.parse(path.read_text())


def _llm_calls():
    """Yield (path, call node, kwargs-by-name) for every invoke_llm_subprocess call."""
    for path, tree in _engine_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "invoke_llm_subprocess":
                yield path, node, {k.arg: k.value for k in node.keywords if k.arg}


def _const(node, value=None, *, kind=None):
    if not isinstance(node, ast.Constant):
        return False
    if kind is not None and not isinstance(node.value, kind):
        return False
    return value is None or node.value == value


def _step_literal(kwargs):
    node = kwargs.get("step_name")
    if node is not None and _const(node, kind=str):
        return node.value
    return None


def test_ac10_every_table_entry_is_a_real_step_name_literal():
    table = _table()
    used = {s for _, _, kw in _llm_calls() if (s := _step_literal(kw)) is not None}
    assert used, "the scan must see invoke_llm_subprocess calls"
    assert sorted(set(table) - used) == [], "stale _WARM_RESUME_STEPS entries"


def test_ac10_every_repair_step_name_literal_has_a_listed_prefix():
    prefixes = tuple(_prefixes())
    literals: list[tuple[str, str]] = []
    for path, tree in _engine_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "repair_step_name" and _const(kw.value, kind=str):
                        literals.append((path.name, kw.value.value))
    assert literals, "the scan must see repair_step_name literals"
    assert [x for x in literals if not x[1].startswith(prefixes)] == []


def test_ac10_no_gate_uses_a_listed_step_name():
    table = _table()
    offenders = [
        f"{path.name}:{node.lineno} {_step_literal(kw)}"
        for path, node, kw in _llm_calls()
        if _const(kw.get("hard_gate"), True) and _step_literal(kw) in table
    ]
    assert offenders == []


def test_ac10_no_judge_or_fresh_call_uses_a_listed_step_name():
    table = _table()
    offenders = [
        f"{path.name}:{node.lineno} {_step_literal(kw)}"
        for path, node, kw in _llm_calls()
        if (_const(kw.get("role"), "judge") or _const(kw.get("fresh_session"), True))
        and _step_literal(kw) in table
    ]
    assert offenders == []
