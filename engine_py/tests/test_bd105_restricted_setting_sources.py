"""bd#105 - a restricted call with a patterned approve entry can run with its
setting sources isolated.

Spec: `docs/decisions/2026-10-11-bd105-restricted-setting-sources.md`.

Real side effects, not argument echoes:
  - agent-sdk: the options the backend passes go through the REAL installed
    `claude_agent_sdk` transport, which builds the CLI argv the SDK would spawn.
    Only `query` is replaced, so nothing is spawned.
  - claude-subprocess: only `subprocess.Popen` is replaced; the argv it receives
    is the real one.
  - the switch lives in a REAL models config file; settings files are REAL
    files under a temporary user dir (CLAUDE_CONFIG_DIR) and project dir.
  - decision events land in a real `EventLog` file.
Every call goes through `invoke_llm_subprocess`. Fake backends are used only to
see what the chokepoint dispatches.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)

_SECRET = "sk-bd105-SECRET-7f3a91c2"
_STEP = "bd105"
_BACKENDS_UNDER_TEST = ["agent-sdk", "claude-subprocess"]
_PATTERNED = ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"]
_EVENT_NAMES = (
    "setting_sources_isolated",
    "setting_sources_isolation_skipped",
    "setting_sources_config_error",
)

# Literal pins of today's output (spec, measured before the change).
_SUB_PREFIX = [
    "claude", "-p", "--model", "claude-3-haiku-20240307", "--output-format",
    "stream-json", "--verbose", "--forward-subagent-text",
]
_SUB_PATTERNED = _SUB_PREFIX + [
    "--allowed-tools", "Read Grep Glob Bash(graphify-shim.sh:*)",
    "--tools", "Read,Grep,Glob,Bash",
]
_SUB_READ = _SUB_PREFIX + ["--allowed-tools", "Read", "--tools", "Read"]
_SDK_PATTERNED = [
    '/nonexistent/claude', '--output-format', 'stream-json', '--verbose',
    '--system-prompt', '', '--tools', 'Read,Grep,Glob,Bash', '--allowedTools',
    'Read,Grep,Glob,Bash(graphify-shim.sh:*)', '--model', 'opus',
    '--permission-mode', 'dontAsk', '--input-format', 'stream-json',
]
_SDK_READ = [
    '/nonexistent/claude', '--output-format', 'stream-json', '--verbose',
    '--system-prompt', '', '--tools', 'Read', '--allowedTools', 'Read',
    '--model', 'opus', '--permission-mode', 'dontAsk', '--input-format',
    'stream-json',
]
_SUB_ISOLATION_TAIL = ["--setting-sources", "", "--strict-mcp-config"]
_SDK_ISOLATION_TOKENS = ("--setting-sources=", "--strict-mcp-config")


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    # Deterministic environment: no credentials, no gate overrides, a clean user
    # settings dir, and a models config that does not exist unless a test writes one.
    for var in (
        "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
        "HAL_FORWARD_SUBAGENT_TEXT", "HAL_AGENT_SDK_HANG_FALLBACK",
    ):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: tmp_path / "no-models.json")
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# --- helpers -----------------------------------------------------------------


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
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd105", step_name="s", phase="p")
    return log


def _events(log, name):
    return [e["payload"] for e in log.read_all() if e["event_type"] == name]


def _decision_events(log):
    return [e for e in log.read_all() if e["event_type"] in _EVENT_NAMES]


def _models(monkeypatch, tmp_path, kind, value=None):
    """Install a REAL models config behind the config seam."""
    path = tmp_path / "models.json"
    if kind == "value":
        path.write_text(json.dumps({"restricted_roles": {"setting_sources": value}}))
    elif kind == "other_key":
        path.write_text(json.dumps({"claude": {}, "restricted_roles": {}}))
    elif kind == "malformed":
        path.write_text("{not json")
    elif kind == "list":
        path.write_text("[]")
    elif kind != "missing":
        raise AssertionError(kind)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: path)


def _isolated(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, "value", "isolated")


def _settings_path(monkeypatch, root, where):
    if where == "user":
        return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    name = "settings.json" if where == "project" else "settings.local.json"
    return root / ".claude" / name


def _put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


def _sdk_argv(options):
    from claude_agent_sdk._internal.transport.subprocess_cli import (  # noqa: PLC0415
        SubprocessCLITransport,
    )

    options = dataclasses.replace(options, cli_path="/nonexistent/claude")
    return SubprocessCLITransport(prompt="p", options=options)._build_command()


def _agent_sdk_capture(monkeypatch):
    """Patch only `query` on the REAL SDK; record every options object built."""
    import claude_agent_sdk  # noqa: PLC0415

    seen: list[object] = []

    async def _query(*, prompt, options):
        seen.append(options)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    return seen


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


def _run(backend, tools, monkeypatch, root):
    """One real dispatch. Returns (argv, sdk_options_or_None)."""
    if backend == "agent-sdk":
        from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

        agent_sdk.register()
        seen = _agent_sdk_capture(monkeypatch)
        res = invoke_llm_subprocess(
            prompt="p", model="opus", timeout_sec=10, step_name=_STEP,
            extra_data={"workspace_root": str(root)}, allowed_tools=tools,
            backend="agent-sdk", idle_timeout_sec=0,
        )
        assert res.status == "ok", res.error
        assert len(seen) == 1
        return _sdk_argv(seen[0]), seen[0]
    captured, side = _capture_popen()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        res = invoke_llm_subprocess(
            prompt="hi", model="claude-3-haiku-20240307", timeout_sec=10,
            step_name=_STEP, extra_data={"workspace_root": str(root)},
            allowed_tools=tools, backend="claude-subprocess", idle_timeout_sec=0,
        )
    assert res.status == "ok", res.error
    assert len(captured) == 1
    return captured[0], None


def _pin(backend, tools):
    patterned = tools == _PATTERNED
    if backend == "agent-sdk":
        return list(_SDK_PATTERNED if patterned else _SDK_READ)
    return list(_SUB_PATTERNED if patterned else _SUB_READ)


def _assert_not_isolated(backend, tools, argv):
    assert argv == _pin(backend, tools), argv


def _assert_isolated(backend, tools, argv, opts):
    if backend == "agent-sdk":
        for token in _SDK_ISOLATION_TOKENS:
            assert token in argv, argv
        rest = [a for a in argv if a not in _SDK_ISOLATION_TOKENS]
        assert rest == _pin(backend, tools), argv
        assert opts.setting_sources == []
    else:
        assert argv == _pin(backend, tools) + _SUB_ISOLATION_TAIL, argv


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


# --- AC1 / AC2 ---------------------------------------------------------------


def test_ac1_agent_sdk_patterned_isolated_argv_and_options(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    root = _repo(tmp_path)

    argv, opts = _run("agent-sdk", _PATTERNED, monkeypatch, root)

    assert "--setting-sources=" in argv, argv
    assert "--strict-mcp-config" in argv, argv
    assert _flag(argv, "--tools") == "Read,Grep,Glob,Bash", argv
    assert _flag(argv, "--allowedTools") == "Read,Grep,Glob,Bash(graphify-shim.sh:*)", argv
    assert _flag(argv, "--permission-mode") == "dontAsk", argv
    assert opts.setting_sources == []
    _assert_isolated("agent-sdk", _PATTERNED, argv, opts)
    events = _events(log, "setting_sources_isolated")
    assert len(events) == 1, _decision_events(log)
    assert {k: events[0][k] for k in ("backend", "step_name")} == {
        "backend": "agent-sdk", "step_name": _STEP,
    }


def test_ac2_subprocess_patterned_isolated_argv(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    root = _repo(tmp_path)

    argv, _ = _run("claude-subprocess", _PATTERNED, monkeypatch, root)

    assert argv == _SUB_PATTERNED + ["--setting-sources", "", "--strict-mcp-config"], argv
    events = _events(log, "setting_sources_isolated")
    assert len(events) == 1, _decision_events(log)
    assert {k: events[0][k] for k in ("backend", "step_name")} == {
        "backend": "claude-subprocess", "step_name": _STEP,
    }


# --- AC3 / AC4 / AC5 ---------------------------------------------------------


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("other_key", None),
        ("value", "inherit"),
        ("missing", None),
        ("malformed", None),
        ("list", None),
    ],
)
def test_ac3_patterned_not_isolated_without_switch_matches_pins(
    backend, kind, value, monkeypatch, tmp_path,
):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, kind, value)
    root = _repo(tmp_path)

    argv, _ = _run(backend, _PATTERNED, monkeypatch, root)

    _assert_not_isolated(backend, _PATTERNED, argv)
    assert _decision_events(log) == []

    # Contrast leg: the same call with the switch on is isolated.
    _isolated(monkeypatch, tmp_path)
    argv2, opts2 = _run(backend, _PATTERNED, monkeypatch, root)
    _assert_isolated(backend, _PATTERNED, argv2, opts2)
    assert len(_events(log, "setting_sources_isolated")) == 1, _decision_events(log)


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize("value", ["isolated", "bogus", 5])
def test_ac4_plain_tool_list_never_isolated(backend, value, monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, "value", value)
    root = _repo(tmp_path)

    argv, _ = _run(backend, ["Read"], monkeypatch, root)

    _assert_not_isolated(backend, ["Read"], argv)
    assert _decision_events(log) == []

    # Contrast leg: with the valid switch on, a patterned list IS isolated.
    _isolated(monkeypatch, tmp_path)
    argv2, opts2 = _run(backend, _PATTERNED, monkeypatch, root)
    _assert_isolated(backend, _PATTERNED, argv2, opts2)
    assert len(_events(log, "setting_sources_isolated")) == 1, _decision_events(log)


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize(("value", "shown"), [("bogus", "'bogus'"), (5, "5")])
def test_ac5_bad_config_value_one_error_event_and_inherit(
    backend, value, shown, monkeypatch, tmp_path,
):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, "value", value)
    root = _repo(tmp_path)

    argv, _ = _run(backend, _PATTERNED, monkeypatch, root)

    _assert_not_isolated(backend, _PATTERNED, argv)
    events = _events(log, "setting_sources_config_error")
    assert len(events) == 1, _decision_events(log)
    payload = events[0]
    assert payload["step_name"] == _STEP
    assert payload["value"] == shown
    assert payload["expected"] == ["inherit", "isolated"]


# --- AC6 ---------------------------------------------------------------------

_AUTH_CASES = [
    ("user_apiKeyHelper", "user", {"apiKeyHelper": f"echo {_SECRET}"}, ["user:apiKeyHelper"]),
    (
        "project_env_api_key", "project",
        {"env": {"ANTHROPIC_API_KEY": _SECRET}}, ["project:ANTHROPIC_API_KEY"],
    ),
    ("local_unparsable", "local", "{not json " + _SECRET, ["local:<unreadable>"]),
    ("project_json_list", "project", [_SECRET], ["project:<unreadable>"]),
]


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize(
    ("where", "content", "keys"),
    [c[1:] for c in _AUTH_CASES],
    ids=[c[0] for c in _AUTH_CASES],
)
def test_ac6_auth_in_settings_blocks_isolation_without_leaking(
    backend, where, content, keys, monkeypatch, tmp_path,
):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    _put(_settings_path(monkeypatch, root, where), content)

    argv, _ = _run(backend, _PATTERNED, monkeypatch, root)

    _assert_not_isolated(backend, _PATTERNED, argv)
    events = _events(log, "setting_sources_isolation_skipped")
    assert len(events) == 1, _decision_events(log)
    payload = events[0]
    assert payload["reason"] == "auth_in_settings"
    assert payload["backend"] == backend
    assert payload["step_name"] == _STEP
    assert list(payload["keys"]) == keys
    assert _events(log, "setting_sources_isolated") == []
    assert _SECRET not in json.dumps(log.read_all())
    assert _SECRET not in " ".join(argv)


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize(
    "project_content",
    [
        {"permissions": {"allow": ["Bash(ls:*)"]}, "env": {"SOME_FLAG": _SECRET}},
        {"env": "not-an-object", "permissions": {"allow": ["Read"]}},
    ],
    ids=["allow_and_non_auth_env", "non_object_env"],
)
def test_ac6_settings_without_auth_do_not_block_isolation(
    backend, project_content, monkeypatch, tmp_path,
):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    _put(_settings_path(monkeypatch, root, "project"), project_content)
    _put(_settings_path(monkeypatch, root, "user"), {"permissions": {"allow": ["Bash(git:*)"]}})

    argv, opts = _run(backend, _PATTERNED, monkeypatch, root)

    _assert_isolated(backend, _PATTERNED, argv, opts)
    assert len(_events(log, "setting_sources_isolated")) == 1, _decision_events(log)
    assert _events(log, "setting_sources_isolation_skipped") == []


# --- AC7 / AC10: capability gating through registered backends ---------------


def _fake(name, capabilities, *, strict=False):
    calls: list[dict] = []

    def _result(step_name):
        return StepResult(status="ok", data={"text": "ok"}, duration_ms=1, step_name=step_name)

    if strict:
        def _impl(*, prompt, model, timeout_sec, step_name, extra_data, allowed_tools,
                  run_ctx, hard_gate, gate_label, straggler_cfg, idle_timeout_sec):
            calls.append({"step_name": step_name})
            return _result(step_name)
    else:
        def _impl(**kwargs):
            calls.append(kwargs)
            return _result(kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities=capabilities)
    return calls


def _invoke_fake(name, tools, root):
    return invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name=_STEP,
        extra_data={"workspace_root": str(root)}, allowed_tools=tools,
        backend=name, idle_timeout_sec=0,
    )


def test_ac7_backend_without_capability_dispatched_without_keyword(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    calls = _fake("bd105-plain", {"tool_allowlist"})

    res = _invoke_fake("bd105-plain", _PATTERNED, _repo(tmp_path))

    assert res.status == "ok", res.error
    assert len(calls) == 1
    assert "isolate_setting_sources" not in calls[0]
    events = _events(log, "setting_sources_isolation_skipped")
    assert len(events) == 1, _decision_events(log)
    assert events[0]["reason"] == "backend_unsupported"
    assert events[0]["backend"] == "bd105-plain"
    assert events[0]["step_name"] == _STEP
    assert list(events[0]["keys"]) == []
    assert _events(log, "setting_sources_isolated") == []


def test_ac10_capable_backend_receives_keyword_only_when_isolating(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    root = _repo(tmp_path)
    calls = _fake("bd105-cap", {"tool_allowlist", "setting_sources_isolation"})

    _isolated(monkeypatch, tmp_path)
    assert _invoke_fake("bd105-cap", _PATTERNED, root).status == "ok"
    assert calls[-1].get("isolate_setting_sources") is True
    assert len(_events(log, "setting_sources_isolated")) == 1

    assert _invoke_fake("bd105-cap", ["Read"], root).status == "ok"
    assert "isolate_setting_sources" not in calls[-1]

    _models(monkeypatch, tmp_path, "value", "inherit")
    assert _invoke_fake("bd105-cap", _PATTERNED, root).status == "ok"
    assert "isolate_setting_sources" not in calls[-1]

    _models(monkeypatch, tmp_path, "value", "bogus")
    assert _invoke_fake("bd105-cap", _PATTERNED, root).status == "ok"
    assert "isolate_setting_sources" not in calls[-1]


@pytest.mark.parametrize(
    ("kind", "value"),
    [("value", "isolated"), ("value", "inherit"), ("value", "bogus"), ("value", 5), ("missing", None)],
)
def test_ac10_strict_signature_backend_never_gets_new_keyword(kind, value, monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, kind, value)
    calls = _fake("bd105-strict", {"tool_allowlist"}, strict=True)
    root = _repo(tmp_path)

    res = _invoke_fake("bd105-strict", _PATTERNED, root)

    assert res.status == "ok", res.error
    assert len(calls) == 1

    # First-leg events per config value.
    skipped1 = _events(log, "setting_sources_isolation_skipped")
    errors1 = _events(log, "setting_sources_config_error")
    if value == "isolated":
        assert len(skipped1) == 1, _decision_events(log)
        assert skipped1[0]["reason"] == "backend_unsupported"
        assert errors1 == []
    elif value in ("bogus", 5):
        assert len(errors1) == 1, _decision_events(log)
        assert skipped1 == []
    else:
        assert _decision_events(log) == []
    assert _events(log, "setting_sources_isolated") == []

    # Contrast leg: with the switch on, the backend is still called, and one
    # more skip is recorded.
    _isolated(monkeypatch, tmp_path)
    res2 = _invoke_fake("bd105-strict", _PATTERNED, root)
    assert res2.status == "ok", res2.error
    assert len(calls) == 2
    skipped2 = _events(log, "setting_sources_isolation_skipped")
    assert len(skipped2) == len(skipped1) + 1, _decision_events(log)
    assert skipped2[-1]["reason"] == "backend_unsupported"


# --- AC8: credentials in the process environment -----------------------------


@pytest.mark.parametrize("backend", _BACKENDS_UNDER_TEST)
@pytest.mark.parametrize("env", [None, "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_ac8_process_environment_credentials_do_not_alter_isolation(
    backend, env, monkeypatch, tmp_path,
):
    log = _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    if env is not None:
        monkeypatch.setenv(env, _SECRET)
    root = _repo(tmp_path)

    argv, opts = _run(backend, _PATTERNED, monkeypatch, root)

    _assert_isolated(backend, _PATTERNED, argv, opts)
    events = _decision_events(log)
    assert [e["event_type"] for e in events] == ["setting_sources_isolated"], events
    assert _SECRET not in json.dumps(log.read_all())
    assert _SECRET not in " ".join(argv)


# --- AC9: the agent-sdk to claude-subprocess hang fallback -------------------


def test_ac9_hang_fallback_dispatch_is_isolated_too(monkeypatch, tmp_path):
    _event_log(tmp_path)
    _isolated(monkeypatch, tmp_path)
    root = _repo(tmp_path)

    def _hung(**kwargs):
        return StepResult(
            status="error", data={"hang_attempts": 1}, duration_ms=1,
            step_name=kwargs["step_name"], error="hung", error_code="E_LLM_API_TIMEOUT",
        )

    register_backend(
        "agent-sdk", _hung, manifest_source="harness_tool_record",
        capabilities={"tool_allowlist", "setting_sources_isolation"}, overwrite=True,
    )
    captured, side = _capture_popen()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        res = invoke_llm_subprocess(
            prompt="hi", model="claude-3-haiku-20240307", timeout_sec=10,
            step_name=_STEP, extra_data={"workspace_root": str(root)},
            allowed_tools=_PATTERNED, backend="agent-sdk", idle_timeout_sec=0,
        )

    assert res.status == "ok", res.error
    assert len(captured) == 1, "the fallback must have spawned the CLI once"
    assert captured[0] == _SUB_PATTERNED + ["--setting-sources", "", "--strict-mcp-config"], captured[0]


# --- AC11: the probe never returns values ------------------------------------


def test_ac11_settings_auth_keys_returns_names_only(monkeypatch, tmp_path):
    root = tmp_path / "proj"
    _put(root / ".claude" / "settings.json", {
        "apiKeyHelper": f"echo {_SECRET}",
        "env": {"ANTHROPIC_API_KEY": _SECRET, "UNRELATED": "x"},
    })
    _put(
        Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json",
        {"awsAuthRefresh": _SECRET},
    )

    keys = llm_subprocess._settings_auth_keys(root)

    names = sorted(keys)
    assert names == [
        "project:ANTHROPIC_API_KEY", "project:apiKeyHelper", "user:awsAuthRefresh",
    ], names
    assert _SECRET not in repr(keys)
    assert all(isinstance(n, str) and _SECRET not in n for n in names)
