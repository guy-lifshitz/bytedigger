"""RED - bd#109: the run identity records the backend each role routes to.

Spec: docs/decisions/2026-10-11-bd109-adapter-identity-roles.md.

Every test drives the REAL WorkflowEngine.execute() over a one-step workflow
with a real EventLog and reads `run_identity` back from the log file. Nothing
patches or calls `_resolve_adapter_identity` directly. The only patches are the
ones the spec names: a wrapper around `llm_subprocess._resolve_backend` that
delegates to the real function (AC8) and a replacement of
`llm_subprocess._ROLES` (AC10).

conftest.py already puts the engine_py root on sys.path (the canonical seam);
no sys.path manipulation here.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, register_backend, reset_backends

_GLOBAL = "HAL_RUNNER_BACKEND"
_JUDGE = "HAL_RUNNER_BACKEND_JUDGE"
_WORKER = "HAL_RUNNER_BACKEND_WORKER"
_AUDITOR = "HAL_RUNNER_BACKEND_AUDITOR"


# --- helpers -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """No ambient selector, credential or cwd leaks into a case."""
    monkeypatch.chdir(tmp_path)
    for name in ("RUNNER_BACKEND", "RUNNER_BACKEND_JUDGE", "RUNNER_BACKEND_WORKER",
                 "RUNNER_BACKEND_AUDITOR"):
        for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + name, raising=False)
    for var in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _apply_env(monkeypatch, case: dict) -> None:
    """Set (str) or delete (None) each variable of ``case``."""
    for var, val in case.items():
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)


def _make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


def _ok_step(name: str) -> StepContract:
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _engine(log: EventLog) -> WorkflowEngine:
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[_ok_step("s1")]))
    return eng


def _run(tmp_path, name: str = "e.jsonl", run_id: str = "r1"):
    """One real execute(); returns (result, log)."""
    log = EventLog(path=tmp_path / name)
    result, _ctx = _engine(log).execute("wf", _make_ctx(), run_id=run_id)
    return result, log


def _events(log: EventLog, event_type: str) -> list:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _payload(log: EventLog, occurrence: int = 0) -> dict:
    evs = _events(log, "run_identity")
    assert len(evs) > occurrence, f"expected run_identity #{occurrence}, got {len(evs)}"
    # Read back from the file, not from memory: json round trip keeps key order.
    raw = [json.loads(ln) for ln in log.path.read_text().splitlines() if ln.strip()]
    ri = [e for e in raw if e["event_type"] == "run_identity"]
    return ri[occurrence]["payload"]


def _identity(tmp_path, name: str = "e.jsonl") -> dict:
    result, log = _run(tmp_path, name)
    assert result.status == "ok"
    return _payload(log)["adapter_identity"]


def _pair(backend: str, source: str) -> dict:
    return {"backend": backend, "source": source}


REFERENCE = {_GLOBAL: None, _JUDGE: "anthropic-api", _WORKER: "claude-in-session"}
REFERENCE_IDENTITY = {
    "backend": "agent-sdk", "source": "default",
    "roles": {"judge": _pair("anthropic-api", "env-role"),
              "worker": _pair("claude-in-session", "env-role")},
}


# --- AC1 ---------------------------------------------------------------------


def test_ac1_reference_case_whole_mapping(tmp_path, monkeypatch):
    """Kills: identity without `roles` (today), roles under the wrong source,
    top level overwritten by a role value."""
    _apply_env(monkeypatch, REFERENCE)
    identity = _identity(tmp_path)
    assert identity == REFERENCE_IDENTITY
    assert list(identity["roles"]) == ["judge", "worker"]


# --- AC2 / AC3 / AC4 / AC5 ---------------------------------------------------


def test_ac2_global_only_is_repeated_per_role(tmp_path, monkeypatch):
    """Kills: roles omitted when no role variable is set; role entry reporting
    source env-role or default instead of env."""
    _apply_env(monkeypatch, {_GLOBAL: "anthropic-api", _JUDGE: None, _WORKER: None})
    identity = _identity(tmp_path)
    assert identity["backend"] == "anthropic-api" and identity["source"] == "env"
    assert identity["roles"] == {"judge": _pair("anthropic-api", "env"),
                                 "worker": _pair("anthropic-api", "env")}


def test_ac3_nothing_set_everything_default(tmp_path, monkeypatch):
    """Kills: a role entry that invents a value when nothing is configured."""
    _apply_env(monkeypatch, {_GLOBAL: None, _JUDGE: None, _WORKER: None})
    identity = _identity(tmp_path)
    assert identity["backend"] == "agent-sdk" and identity["source"] == "default"
    assert identity["roles"] == {"judge": _pair("agent-sdk", "default"),
                                 "worker": _pair("agent-sdk", "default")}


def test_ac4a_mixed_global_and_judge(tmp_path, monkeypatch):
    """Kills: role entries all sharing one value; role var leaking into worker."""
    _apply_env(monkeypatch, {_GLOBAL: "claude-subprocess", _JUDGE: "anthropic-api", _WORKER: None})
    identity = _identity(tmp_path)
    assert identity["backend"] == "claude-subprocess" and identity["source"] == "env"
    assert identity["roles"]["judge"] == _pair("anthropic-api", "env-role")
    assert identity["roles"]["worker"] == _pair("claude-subprocess", "env")


def test_ac4b_worker_only(tmp_path, monkeypatch):
    """Kills: top level taking the worker value; judge not falling to default."""
    _apply_env(monkeypatch, {_GLOBAL: None, _JUDGE: None, _WORKER: "claude-in-session"})
    identity = _identity(tmp_path)
    assert identity["backend"] == "agent-sdk" and identity["source"] == "default"
    assert identity["roles"]["worker"] == _pair("claude-in-session", "env-role")
    assert identity["roles"]["judge"] == _pair("agent-sdk", "default")


def test_ac5_blank_falls_through_and_padded_is_stripped(tmp_path, monkeypatch):
    """Kills: a re-implemented resolver that does not strip / treats blank as set."""
    _apply_env(monkeypatch, {_GLOBAL: "claude-subprocess", _JUDGE: "   ",
                             _WORKER: "  claude-in-session  "})
    identity = _identity(tmp_path)
    assert identity["roles"]["judge"] == _pair("claude-subprocess", "env")
    assert identity["roles"]["worker"] == _pair("claude-in-session", "env-role")


# --- AC6 ---------------------------------------------------------------------

_AC6_CASES = [
    REFERENCE,
    {_GLOBAL: "anthropic-api", _JUDGE: None, _WORKER: None},
    {_GLOBAL: None, _JUDGE: None, _WORKER: None},
    {_GLOBAL: "claude-subprocess", _JUDGE: "anthropic-api", _WORKER: None},
    {_GLOBAL: None, _JUDGE: None, _WORKER: "claude-in-session"},
]


@pytest.mark.parametrize("case", _AC6_CASES, ids=["ac1", "ac2", "ac3", "ac4a", "ac4b"])
def test_ac6_top_level_unchanged_and_additive_only(tmp_path, monkeypatch, case):
    """Kills: roles inserted before backend/source, extra keys, a changed
    top-level pair, extra run_identity payload keys."""
    _apply_env(monkeypatch, case)
    result, log = _run(tmp_path)
    assert result.status == "ok"
    payload = _payload(log)
    assert set(payload) == {"engine_version", "adapter_identity"}
    identity = payload["adapter_identity"]
    assert list(identity) == ["backend", "source", "roles"]
    env = {k: v for k, v in case.items() if v is not None}
    assert (identity["backend"], identity["source"]) == llm_subprocess._resolve_backend(None, env)


# --- AC7 ---------------------------------------------------------------------


def _spy(name: str) -> list:
    calls: list = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities={"tool_allowlist"},
                     overwrite=True)
    return calls


def _call(role: str) -> None:
    invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="bd109", hard_gate=False,
        gate_label="g", allowed_tools=["Read"], idle_timeout_sec=0, role=role,
    )


@pytest.mark.parametrize("case, source", [
    ({_GLOBAL: None, _JUDGE: "bd109-judge", _WORKER: "bd109-worker"}, "env-role"),
    ({_GLOBAL: "bd109-global", _JUDGE: None, _WORKER: None}, "env"),
], ids=["per-role", "global-only"])
def test_ac7_identity_matches_the_runner_backend_resolved_of_real_calls(
        tmp_path, monkeypatch, case, source):
    """Kills: an identity built by a private rule that diverges from the one
    the calls use (different env snapshot, own precedence, own stripping)."""
    for n in ("bd109-judge", "bd109-worker", "bd109-global"):
        _spy(n)
    _apply_env(monkeypatch, case)

    call_log = EventLog(tmp_path / "calls.jsonl")
    telemetry_ctx.set_current_run(event_log=call_log, run_id="run-c", step_name="s", phase="p")
    _call("judge")
    _call("worker")
    telemetry_ctx.clear_current_run()
    resolved = {e["payload"]["role"]: {"backend": e["payload"]["backend"],
                                       "source": e["payload"]["source"]}
                for e in call_log.read_all() if e["event_type"] == "runner_backend_resolved"}
    assert set(resolved) == {"judge", "worker"}
    assert all(v["source"] == source for v in resolved.values())

    identity = _identity(tmp_path, "run.jsonl")
    assert identity["roles"]["judge"] == resolved["judge"]
    assert identity["roles"]["worker"] == resolved["worker"]


# --- AC8 ---------------------------------------------------------------------


def _wrapper(raises_for):
    real = llm_subprocess._resolve_backend

    def _wrapped(kwarg, env, role=None):
        if role is not None and raises_for(role):
            raise RuntimeError(f"boom-{role}")
        return real(kwarg, env, role=role)

    return _wrapped


def test_ac8a_one_role_failing_is_omitted_and_logged(tmp_path, monkeypatch, caplog):
    """Kills: an unguarded loop (execute() dies), a guard that drops every role,
    a silent skip with no warning, a placeholder entry for the failed role."""
    _apply_env(monkeypatch, REFERENCE)
    monkeypatch.setattr(llm_subprocess, "_resolve_backend", _wrapper(lambda r: r == "judge"))
    with caplog.at_level(logging.WARNING):
        result, log = _run(tmp_path)
    assert result.status == "ok"
    assert len(_events(log, "run_identity")) == 1
    identity = _payload(log)["adapter_identity"]
    assert (identity["backend"], identity["source"]) == ("agent-sdk", "default")
    assert identity["roles"] == {"worker": _pair("claude-in-session", "env-role")}
    named = [r for r in caplog.records
             if r.levelno == logging.WARNING and "judge" in r.getMessage()]
    assert len(named) == 1
    assert "RuntimeError" in named[0].getMessage()


def test_ac8b_every_role_failing_leaves_empty_roles(tmp_path, monkeypatch):
    """Kills: `roles` key missing on degrade, or run_identity not emitted."""
    _apply_env(monkeypatch, REFERENCE)
    monkeypatch.setattr(llm_subprocess, "_resolve_backend", _wrapper(lambda r: True))
    result, log = _run(tmp_path)
    assert result.status == "ok"
    assert len(_events(log, "run_identity")) == 1
    identity = _payload(log)["adapter_identity"]
    assert identity["roles"] == {}
    assert (identity["backend"], identity["source"]) == ("agent-sdk", "default")


# --- AC9 ---------------------------------------------------------------------


def test_ac9_identical_under_api_key_subscription_and_neither(tmp_path, monkeypatch):
    """Kills: an identity that reads or branches on credentials."""
    seen = []
    creds = [
        {"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": None},
        {"ANTHROPIC_API_KEY": None, "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test"},
        {"ANTHROPIC_API_KEY": None, "CLAUDE_CODE_OAUTH_TOKEN": None},
    ]
    for i, cred in enumerate(creds):
        _apply_env(monkeypatch, REFERENCE)
        _apply_env(monkeypatch, cred)
        seen.append(_identity(tmp_path, f"c{i}.jsonl"))
    assert seen[0] == seen[1] == seen[2] == REFERENCE_IDENTITY


# --- AC10 --------------------------------------------------------------------


def test_ac10_roles_come_from_the_router_list_at_call_time(tmp_path, monkeypatch):
    """Kills: a hard-coded role list in the engine; a role list read at import."""
    _apply_env(monkeypatch, {_GLOBAL: None, _JUDGE: None, _WORKER: None,
                             _AUDITOR: "claude-in-session"})
    monkeypatch.setattr(llm_subprocess, "_ROLES", ("judge", "worker", "auditor"))
    identity = _identity(tmp_path, "three.jsonl")
    assert list(identity["roles"]) == ["judge", "worker", "auditor"]
    assert identity["roles"]["auditor"] == _pair("claude-in-session", "env-role")

    monkeypatch.setattr(llm_subprocess, "_ROLES", ("worker",))
    identity = _identity(tmp_path, "one.jsonl")
    assert identity["roles"] == {"worker": _pair("agent-sdk", "default")}


# --- AC11 --------------------------------------------------------------------


def test_ac11_resolving_identity_is_read_only(tmp_path, monkeypatch):
    """Kills: an identity builder that emits runner_backend_resolved, calls a
    backend, or mutates the environment."""
    _apply_env(monkeypatch, {_GLOBAL: None, _JUDGE: None, _WORKER: None})
    result, base_log = _run(tmp_path, "base.jsonl")
    assert result.status == "ok"
    base_types = [e["event_type"] for e in base_log.read_all()]

    _apply_env(monkeypatch, REFERENCE)
    env_before = dict(os.environ)
    result, log = _run(tmp_path, "roles.jsonl")
    env_after = dict(os.environ)
    assert result.status == "ok"
    assert _payload(log)["adapter_identity"]["roles"] == REFERENCE_IDENTITY["roles"]
    types = [e["event_type"] for e in log.read_all()]
    assert "runner_backend_resolved" not in types
    assert types == base_types
    assert env_before == env_after


# --- AC12 --------------------------------------------------------------------


def test_ac12_identity_is_live_per_execute(tmp_path, monkeypatch):
    """Kills: a value cached at import or at engine construction."""
    log = EventLog(path=tmp_path / "two.jsonl")
    eng = _engine(log)
    _apply_env(monkeypatch, {_GLOBAL: None, _JUDGE: "anthropic-api", _WORKER: None})
    assert eng.execute("wf", _make_ctx(), run_id="r1")[0].status == "ok"
    _apply_env(monkeypatch, {_GLOBAL: "claude-subprocess", _JUDGE: None,
                             _WORKER: "claude-in-session"})
    assert eng.execute("wf", _make_ctx(), run_id="r2")[0].status == "ok"
    first = _payload(log, 0)["adapter_identity"]
    second = _payload(log, 1)["adapter_identity"]
    assert first["roles"] == {"judge": _pair("anthropic-api", "env-role"),
                              "worker": _pair("agent-sdk", "default")}
    assert second["roles"] == {"judge": _pair("claude-subprocess", "env"),
                               "worker": _pair("claude-in-session", "env-role")}


# --- AC13 --------------------------------------------------------------------

_SPEC_MD = Path(__file__).resolve().parent.parent / "bytedigger_engine" / "conformance" / "EMISSIONS_SPEC.md"


def _entries(marker: str) -> list:
    """Every top-level entry starting with `- **<marker>**`, ending at the next
    `- **AC-` entry or `## ` heading."""
    lines = _SPEC_MD.read_text().splitlines()
    out, cur = [], None
    for ln in lines:
        starts = ln.startswith("- **AC-") or ln.startswith("## ")
        if starts:
            if cur is not None:
                out.append("\n".join(cur))
                cur = None
            if ln.startswith(f"- **{marker}**"):
                cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        out.append("\n".join(cur))
    return out


def test_ac13_emissions_spec_documents_roles():
    """Kills: shipping the field without normative text; a pointer-less AC-E2b;
    AC-E2d overwritten. The roles entry is the new AC-E2e, placed after AC-E2d
    and before section 5; AC-E2d (per-execute() quantification) stays intact."""
    e2e = _entries("AC-E2e")
    assert len(e2e) == 1, "expected exactly one AC-E2e entry"
    for token in ("roles", "judge", "worker", "env-role", "env", "default"):
        assert token in e2e[0], f"AC-E2e entry lacks {token!r}"

    text = _SPEC_MD.read_text()
    assert text.index("- **AC-E2d**") < text.index("- **AC-E2e**") < text.index("## 5.")

    e2d = _entries("AC-E2d")
    assert len(e2d) == 1
    assert e2d[0].startswith(
        "- **AC-E2d** **Quantified over the `execute()` calls of one engine instance**")
    assert "roles" not in e2d[0]

    e2b = _entries("AC-E2b")
    assert len(e2b) == 1
    assert "AC-E2e" in e2b[0]
