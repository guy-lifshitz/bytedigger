"""bd#82 PR1 — the tool allowlist is enforced on every backend, and the gate floor
is checked in the chokepoint before dispatch.

Spec: `docs/decisions/2026-09-26-bd82-pr1-tool-restriction.md`.

Real side effects, not argument echoes:
  - agent-sdk: the backend's options go through the REAL installed
    `claude_agent_sdk` transport, which turns them into the CLI argv the SDK
    would spawn (AC1). Only `query` is replaced, so nothing is spawned.
  - pydantic: the real backend runs; the fake Agent calls every tool it was
    given, and the test looks at the disk (AC8).
  - chokepoint events land in a real `EventLog` file (AC12, AC14).
Fake backends are used only to see what the chokepoint dispatches.
"""
from __future__ import annotations

import io
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()
    for name in list(sys.modules):
        root = name.split(".")[0]
        if root in ("pydantic_ai", "anthropic") and getattr(sys.modules[name], "__file__", None) is None:
            sys.modules.pop(name, None)  # only the fakes this file installed


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


def _event_log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd82", step_name="s", phase="p")
    return log


def _types(log):
    return [e["event_type"] for e in log.read_all()]


# ─── agent-sdk ────────────────────────────────────────────────────────────────


def _agent_sdk_capture(monkeypatch, *, reject_stderr=False):
    """Patch only `query` on the REAL SDK; record every options object built."""
    import claude_agent_sdk  # noqa: PLC0415

    seen: list[object] = []
    real_options = claude_agent_sdk.ClaudeAgentOptions

    if reject_stderr:
        def _options(**kwargs):
            if "stderr" in kwargs:
                raise TypeError("unexpected keyword argument 'stderr'")
            return real_options(**kwargs)

        monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", _options)

    async def _query(*, prompt, options):
        seen.append(options)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    return seen


def _run_agent_sdk(tmp_path, allowed_tools):
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    return agent_sdk.agent_sdk_backend(
        prompt="p", model="opus", timeout_sec=10, step_name="bd82",
        extra_data={"workspace_root": str(_repo(tmp_path))},
        allowed_tools=allowed_tools, run_ctx=None, hard_gate=False, gate_label=None,
        straggler_cfg=None, idle_timeout_sec=None,
    )


def _sdk_argv(options):
    from claude_agent_sdk._internal.transport.subprocess_cli import (  # noqa: PLC0415
        SubprocessCLITransport,
    )

    import dataclasses  # noqa: PLC0415

    # The transport resolves the CLI binary on connect(); name one so the argv
    # can be built without spawning anything.
    options = dataclasses.replace(options, cli_path="/nonexistent/claude")
    return SubprocessCLITransport(prompt="p", options=options)._build_command()


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def test_ac1_agent_sdk_read_only_judge_argv_has_no_write_path(monkeypatch, tmp_path):
    """The argv the real SDK would spawn for a ["Read"] role: only Read is
    available, and the permission mode denies anything not pre-approved."""
    seen = _agent_sdk_capture(monkeypatch)
    res = _run_agent_sdk(tmp_path, ["Read"])

    assert res.status == "ok", res.error
    argv = _sdk_argv(seen[0])
    assert _flag(argv, "--tools") == "Read", argv
    assert _flag(argv, "--permission-mode") == "dontAsk", argv
    assert "bypassPermissions" not in argv, argv


def test_ac2_agent_sdk_patterned_entry_available_by_base_name(monkeypatch, tmp_path):
    seen = _agent_sdk_capture(monkeypatch)
    tools = ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"]
    _run_agent_sdk(tmp_path, tools)

    opts = seen[0]
    assert opts.tools == ["Read", "Grep", "Glob", "Bash"]
    assert opts.allowed_tools == tools, "approved entries stay verbatim (pattern kept)"
    assert opts.permission_mode == "dontAsk"


def test_ac20_agent_sdk_empty_list_is_no_tools_not_unrestricted(monkeypatch, tmp_path):
    seen = _agent_sdk_capture(monkeypatch)
    _run_agent_sdk(tmp_path, [])

    opts = seen[0]
    assert opts.tools == [], "[] must mean no tools, never unrestricted"
    assert opts.permission_mode == "dontAsk"


def test_ac6d_agent_sdk_duplicate_base_names_collapse(monkeypatch, tmp_path):
    seen = _agent_sdk_capture(monkeypatch)
    _run_agent_sdk(tmp_path, ["Bash(a:*)", "Read", "Bash(b:*)"])
    assert seen[0].tools == ["Bash", "Read"]


def test_ac3_agent_sdk_unrestricted_worker_unchanged(monkeypatch, tmp_path):
    """NEGATIVE LEG: allowed_tools=None keeps today's worker behavior."""
    seen = _agent_sdk_capture(monkeypatch)
    _run_agent_sdk(tmp_path, None)

    opts = seen[0]
    assert opts.tools is None
    assert opts.permission_mode == "bypassPermissions"


def test_ac4_agent_sdk_old_sdk_fallback_keeps_restriction(monkeypatch, tmp_path):
    """The TypeError fallback (SDK without `stderr`) must not be a way back to bypass."""
    seen = _agent_sdk_capture(monkeypatch, reject_stderr=True)
    res = _run_agent_sdk(tmp_path, ["Read"])

    assert res.status == "ok", res.error
    opts = seen[0]
    assert opts.tools == ["Read"]
    assert opts.permission_mode == "dontAsk"


def test_ac5_agent_sdk_declares_tool_allowlist():
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    agent_sdk.register()
    assert "tool_allowlist" in llm_subprocess._BACKEND_CAPABILITIES["agent-sdk"]
    assert llm_subprocess._capability_enforcement("agent-sdk") == "runtime-allowlist"


# ─── claude-subprocess ────────────────────────────────────────────────────────


def _capture_popen():
    captured: list[list[str]] = []
    event = (
        '{"type":"result","subtype":"success","result":"OK",'
        '"usage":{"input_tokens":1,"output_tokens":1,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.001,"duration_ms":100}\n'
    )

    def _side_effect(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(event)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(event, ""))
        return proc

    return captured, _side_effect


def _subprocess_argv(allowed_tools):
    captured, side = _capture_popen()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi", model="claude-3-haiku-20240307", timeout_sec=10,
            step_name="bd82", allowed_tools=allowed_tools, idle_timeout_sec=0,
            backend="claude-subprocess",
        )
    assert len(captured) == 1
    return captured[0]


def test_ac6_subprocess_narrows_available_set():
    argv = _subprocess_argv(["Read", "Bash(x:*)"])
    assert _flag(argv, "--tools") == "Read,Bash", argv
    assert _flag(argv, "--allowed-tools") == "Read Bash(x:*)", "approve list unchanged"


def test_ac6b_subprocess_empty_list_disables_all_tools():
    argv = _subprocess_argv([])
    assert "--tools" in argv and _flag(argv, "--tools") == "", argv


def test_ac6c_subprocess_unrestricted_has_no_tools_flag():
    """NEGATIVE LEG."""
    assert "--tools" not in _subprocess_argv(None)


# ─── pydantic ─────────────────────────────────────────────────────────────────


class _ToolCallingAgent:
    """Fake pydantic_ai.Agent whose run calls every registered tool with a write."""

    last = None

    def __init__(self, model, tools=None, **kwargs):
        self.tools = list(tools or [])
        _ToolCallingAgent.last = self

    def tool_plain(self, fn=None, **kwargs):
        def _wrap(f):
            self.tools.append(f)
            return f
        return _wrap(fn) if fn is not None else _wrap

    def run_sync(self, prompt, usage_limits=None, **kwargs):
        for tool in self.tools:
            name = tool.__name__
            if name == "write_file":
                tool("escaped.txt", "x")
            elif name == "edit_file":
                tool("seed.txt", "a", "b")
            elif name in ("bash", "run_tests"):
                tool("echo ran > ran_by_bash.txt")
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        return types.SimpleNamespace(output="done", data="done", usage=lambda: usage)


def _install_fake_pydantic_anthropic(monkeypatch, tmp_path):
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    mod = types.ModuleType("pydantic_ai")
    mod.Agent = _ToolCallingAgent
    mod.UsageLimits = lambda request_limit=None, **kw: types.SimpleNamespace(request_limit=request_limit)
    models = types.ModuleType("pydantic_ai.models")
    models_anthropic = types.ModuleType("pydantic_ai.models.anthropic")
    models_anthropic.AnthropicModel = lambda name, provider=None, **kw: types.SimpleNamespace(name=name)
    providers = types.ModuleType("pydantic_ai.providers")
    providers_anthropic = types.ModuleType("pydantic_ai.providers.anthropic")
    providers_anthropic.AnthropicProvider = lambda **kw: types.SimpleNamespace(**kw)
    anthropic = types.ModuleType("anthropic")
    anthropic.AsyncAnthropic = lambda **kw: types.SimpleNamespace(**kw)
    for name, m in (
        ("pydantic_ai", mod), ("pydantic_ai.models", models),
        ("pydantic_ai.models.anthropic", models_anthropic),
        ("pydantic_ai.providers", providers),
        ("pydantic_ai.providers.anthropic", providers_anthropic),
        ("anthropic", anthropic),
    ):
        monkeypatch.setitem(sys.modules, name, m)
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "tok", "refreshToken": "r",
        "expiresAt": int((time.time() + 100_000) * 1000),
    }}))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds))


def _install_fake_pydantic_openai(monkeypatch):
    mod = types.ModuleType("pydantic_ai")
    mod.Agent = _ToolCallingAgent
    mod.UsageLimits = lambda request_limit=None, **kw: types.SimpleNamespace(request_limit=request_limit)
    models = types.ModuleType("pydantic_ai.models")
    models_openai = types.ModuleType("pydantic_ai.models.openai")
    models_openai.OpenAIChatModel = lambda name, provider=None, **kw: types.SimpleNamespace(name=name)
    providers = types.ModuleType("pydantic_ai.providers")
    providers_azure = types.ModuleType("pydantic_ai.providers.azure")
    providers_azure.AzureProvider = lambda **kw: types.SimpleNamespace(**kw)
    for name, m in (
        ("pydantic_ai", mod), ("pydantic_ai.models", models),
        ("pydantic_ai.models.openai", models_openai),
        ("pydantic_ai.providers", providers),
        ("pydantic_ai.providers.azure", providers_azure),
    ):
        monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example")


def _run_pydantic(flavor, monkeypatch, tmp_path, allowed_tools):
    root = _repo(tmp_path)
    (root / "seed.txt").write_text("a\n")
    if flavor == "anthropic":
        _install_fake_pydantic_anthropic(monkeypatch, tmp_path)
        from bytedigger_engine.lib.reference_backends import pydantic_anthropic as be  # noqa: PLC0415
        fn = be.pydantic_anthropic_backend
    else:
        _install_fake_pydantic_openai(monkeypatch)
        from bytedigger_engine.lib.reference_backends import pydantic_openai as be  # noqa: PLC0415
        fn = be.pydantic_openai_backend
    res = fn(
        prompt="p", model="m", timeout_sec=30, step_name="bd82",
        extra_data={"workspace_root": str(root)}, allowed_tools=allowed_tools,
    )
    names = sorted(t.__name__ for t in _ToolCallingAgent.last.tools)
    return res, names, root


@pytest.mark.parametrize("flavor", ["anthropic", "openai"])
def test_ac7_pydantic_read_only_registers_no_tools(flavor, monkeypatch, tmp_path):
    res, names, _ = _run_pydantic(flavor, monkeypatch, tmp_path, ["Read"])
    assert res.status == "ok", res.error
    assert names == [], names


@pytest.mark.parametrize("flavor", ["anthropic", "openai"])
def test_ac8_pydantic_read_only_cannot_touch_disk(flavor, monkeypatch, tmp_path):
    """Side effect: the model calls every tool it has — nothing lands on disk."""
    monkeypatch.setenv("HAL_AGENTIC_BASH_UNRESTRICTED", "1")  # so a leaked bash would write
    _, _, root = _run_pydantic(flavor, monkeypatch, tmp_path, ["Read", "Grep"])
    assert not (root / "escaped.txt").exists()
    assert not (root / "ran_by_bash.txt").exists()
    assert (root / "seed.txt").read_text() == "a\n"


@pytest.mark.parametrize("flavor", ["anthropic", "openai"])
@pytest.mark.parametrize(
    ("allowed", "expected"),
    [
        (["Read", "Write"], ["write_file"]),
        (["Edit"], ["edit_file"]),
        (["Bash"], ["bash", "run_tests"]),
        (["Bash(graphify-shim.sh:*)"], []),
        (["Write(src/*)", "Edit(src/*)"], []),
        ([], []),
        (None, ["bash", "edit_file", "run_tests", "write_file"]),
    ],
)
def test_ac9_pydantic_registers_exactly_the_policy(flavor, allowed, expected, monkeypatch, tmp_path):
    _, names, _ = _run_pydantic(flavor, monkeypatch, tmp_path, allowed)
    assert names == expected


@pytest.mark.parametrize("flavor", ["anthropic", "openai"])
def test_ac9b_pydantic_write_policy_writes(flavor, monkeypatch, tmp_path):
    """POSITIVE LEG of AC8: an allowed Write does reach the disk."""
    _, _, root = _run_pydantic(flavor, monkeypatch, tmp_path, ["Write"])
    assert (root / "escaped.txt").read_text() == "x"


@pytest.mark.parametrize("flavor", ["anthropic", "openai"])
def test_ac9c_pydantic_bash_policy_runs(flavor, monkeypatch, tmp_path):
    """POSITIVE LEG of AC8's bash check, so it cannot pass vacuously."""
    monkeypatch.setenv("HAL_AGENTIC_BASH_UNRESTRICTED", "1")
    _, _, root = _run_pydantic(flavor, monkeypatch, tmp_path, ["Bash"])
    assert (root / "ran_by_bash.txt").exists()


def test_ac10_pydantic_backends_declare_tool_allowlist(monkeypatch, tmp_path):
    _install_fake_pydantic_anthropic(monkeypatch, tmp_path)
    from bytedigger_engine.lib.reference_backends import (  # noqa: PLC0415
        pydantic_anthropic,
        pydantic_openai,
    )

    pydantic_anthropic.register()
    pydantic_openai.register()
    for name in ("pydantic-anthropic", "pydantic-openai"):
        assert "tool_allowlist" in llm_subprocess._BACKEND_CAPABILITIES[name], name


def test_ac11_anthropic_api_declares_no_tools_not_allowlist():
    from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: PLC0415

    anthropic_api.register()
    caps = llm_subprocess._BACKEND_CAPABILITIES["anthropic-api"]
    assert "no_tools" in caps
    assert "tool_allowlist" not in caps, "bd#10: a text-only backend must not claim the allowlist"
    assert llm_subprocess._capability_enforcement("anthropic-api") == "no-tools"


# ─── chokepoint: refusal before dispatch ─────────────────────────────────────


def _register_spy(name, capabilities=None):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    if capabilities is None:
        register_backend(name, _impl, manifest_source="git_diff")
    else:
        register_backend(name, _impl, manifest_source="git_diff", capabilities=capabilities)
    return calls


def _invoke(backend, *, hard_gate, allowed_tools, model="opus"):
    return invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=5, step_name="bd82",
        hard_gate=hard_gate, gate_label="g", allowed_tools=allowed_tools,
        backend=backend, idle_timeout_sec=0,
    )


def test_ac12_gate_on_unenforcing_backend_refused_before_dispatch(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy("bd82-plain")  # third party, default capabilities

    res = _invoke("bd82-plain", hard_gate=True, allowed_tools=["Read"])

    assert res.status == "error"
    assert res.error_code == "E_TOOL_RESTRICTION_UNSUPPORTED"
    assert res.recoverable is False
    assert calls == [], "the backend must not be called"
    assert "tool_restriction_refused" in _types(log)


def test_ac12b_enforcement_read_from_registry_not_name(tmp_path):
    """bd#10: a stub registered over claude-subprocess without the capability is
    refused — no name-keyed exemption."""
    calls = _register_stub_over("claude-subprocess")
    res = _invoke("claude-subprocess", hard_gate=True, allowed_tools=["Read"])
    assert res.error_code == "E_TOOL_RESTRICTION_UNSUPPORTED"
    assert calls == []


def _register_stub_over(name):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="harness_tool_record", overwrite=True)
    return calls


def _req_files(tmp_path):
    return sorted((tmp_path / "req").rglob("*.req.json"))


def test_ac13_in_session_gate_with_tool_list_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    monkeypatch.delenv("HAL_IN_SESSION_ENFORCES_TOOLS", raising=False)
    log = _event_log(tmp_path)

    res = _invoke("claude-in-session", hard_gate=True, allowed_tools=["Read"])

    assert res.error_code == "E_TOOL_RESTRICTION_UNSUPPORTED"
    assert _req_files(tmp_path) == [], "no request may be handed to the servicer"
    assert "tool_restriction_refused" in _types(log)


def test_ac13b_in_session_opt_in_declaration(monkeypatch, tmp_path):
    """The servicer declares enforcement: the gate passes the tool check and the
    request (carrying the tool list) is handed over."""
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", "1")
    _event_log(tmp_path)

    assert llm_subprocess._capability_enforcement("claude-in-session") == "runtime-allowlist"
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=1, step_name="bd82", hard_gate=True,
        gate_label="g", allowed_tools=["Read"], backend="claude-in-session", idle_timeout_sec=0,
    )

    assert res.error_code != "E_TOOL_RESTRICTION_UNSUPPORTED"
    assert len(_req_files(tmp_path)) == 1


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_ac13c_in_session_opt_in_only_exact_one(value, monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", value)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    _event_log(tmp_path)
    assert llm_subprocess._capability_enforcement("claude-in-session") == "not-enforced"
    res = _invoke("claude-in-session", hard_gate=True, allowed_tools=["Read"])
    assert res.error_code == "E_TOOL_RESTRICTION_UNSUPPORTED"


def test_ac14_worker_on_unenforcing_backend_dispatched_and_visible(tmp_path):
    """Third-party compat: a non-gate call still runs, and the gap is on record."""
    log = _event_log(tmp_path)
    calls = _register_spy("bd82-plain")

    res = _invoke("bd82-plain", hard_gate=False, allowed_tools=["Read"])

    assert res.status == "ok", res.error
    assert len(calls) == 1
    assert "tool_restriction_not_enforced" in _types(log)


@pytest.mark.parametrize("case", ["enforcing", "no_tool_list"])
def test_ac14b_not_enforced_event_absent_when_nothing_is_lost(case, tmp_path):
    log = _event_log(tmp_path)
    caps = {"tool_allowlist"} if case == "enforcing" else None
    _register_spy("bd82-x", capabilities=caps)
    tools = ["Read"] if case == "enforcing" else None

    res = _invoke("bd82-x", hard_gate=False, allowed_tools=tools)

    assert res.status == "ok", res.error
    assert "tool_restriction_not_enforced" not in _types(log)


def test_ac15_gate_without_tool_list_dispatched():
    """NEGATIVE LEG: nothing to restrict → nothing to refuse."""
    calls = _register_spy("bd82-plain")
    res = _invoke("bd82-plain", hard_gate=True, allowed_tools=None)
    assert res.status == "ok", res.error
    assert len(calls) == 1


def test_ac16_gate_on_no_tools_backend_dispatched():
    calls = _register_spy("bd82-text", capabilities={"no_tools"})
    res = _invoke("bd82-text", hard_gate=True, allowed_tools=["Read"])
    assert res.status == "ok", res.error
    assert len(calls) == 1


# ─── chokepoint: gate floor before dispatch ──────────────────────────────────


def test_ac17_floor_enforced_on_non_subprocess_backend(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy("bd82-enf", capabilities={"tool_allowlist"})

    res = _invoke("bd82-enf", hard_gate=True, allowed_tools=["Read"], model="sonnet")

    assert res.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert calls == []
    assert "hard_gate_refused" in _types(log)


def test_ac18_floor_enforced_on_agent_sdk_before_query(monkeypatch, tmp_path):
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    seen = _agent_sdk_capture(monkeypatch)
    agent_sdk.register()
    monkeypatch.chdir(_repo(tmp_path))

    res = _invoke("agent-sdk", hard_gate=True, allowed_tools=["Read"], model="sonnet")

    assert res.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert seen == [], "the SDK must never be queried"


def test_ac19_floor_passes_opus_gate():
    """NEGATIVE LEG."""
    calls = _register_spy("bd82-enf", capabilities={"tool_allowlist"})
    res = _invoke("bd82-enf", hard_gate=True, allowed_tools=["Read"], model="opus")
    assert res.status == "ok", res.error
    assert len(calls) == 1


def test_ac22_floor_enforced_on_in_session_before_request(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", "1")
    log = _event_log(tmp_path)

    res = _invoke("claude-in-session", hard_gate=True, allowed_tools=["Read"], model="sonnet")

    assert res.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert _req_files(tmp_path) == []
    assert "hard_gate_refused" in _types(log)


def test_ac23_floor_checked_before_tool_restriction(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy("bd82-plain")

    res = _invoke("bd82-plain", hard_gate=True, allowed_tools=["Read"], model="sonnet")

    assert res.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert "tool_restriction_refused" not in _types(log)
    assert calls == []


def test_ac24_error_code_registered():
    from pathlib import Path  # noqa: PLC0415

    from bytedigger_engine import error_codes  # noqa: PLC0415

    assert "E_TOOL_RESTRICTION_UNSUPPORTED" in error_codes.ERROR_CODES
    pkg = Path(error_codes.__file__).parent
    for doc in (pkg / "ERROR_CODES.md", pkg.parent / "ERROR_CODES.md"):
        assert "`E_TOOL_RESTRICTION_UNSUPPORTED`" in doc.read_text(), doc
