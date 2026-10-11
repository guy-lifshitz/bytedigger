"""bd#106 - agent-sdk does not snapshot git state for a call that cannot write.

Spec: `docs/decisions/2026-10-11-bd106-skip-snapshot-read-only.md`.

Real side effects, not argument echoes:
  - a REAL temporary git tree: a seed commit, a tracked file committed then
    modified, and several untracked files (a dirty worktree).
  - git launches are counted by wrapping the real `Popen` that `_git_run` uses
    (`pydantic_openai._REAL_POPEN`); the wrapper records argv and DELEGATES to
    the real Popen, so the real tree is really queried. The snapshot functions
    themselves are not mocked.
  - only `claude_agent_sdk.query` is replaced.
  - the switch lives in a REAL models config file.
Every call goes through `invoke_llm_subprocess(backend="agent-sdk")`, except the
timeout case (AC1b), which calls `agent_sdk_backend` directly so the timeout
result is observable without the chokepoint's retry / fallback.

Every test here FAILS today (snapshot is unconditional): AC1, AC1b, AC3b
(absent key, malformed file), AC5. The unchanged-behaviour guards live in
`test_bd106_snapshot_kept_guards.py`.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, reset_backends
from bytedigger_engine.lib.reference_backends import agent_sdk, pydantic_openai

_STEP = "bd106"
_READ_ONLY = ["Read", "Grep", "Glob"]
_GUARD = "rev-parse:--is-inside-work-tree"


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
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
    """A real dirty git tree: tracked-modified + several untracked files."""
    root = tmp_path / "repo"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    (root / "tracked.txt").write_text("v1\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True, capture_output=True)
    (root / "tracked.txt").write_text("v2 modified\n")
    for i in range(4):
        (root / f"untracked_{i}.txt").write_text(f"dirty {i}\n")
    return root


def _models(monkeypatch, tmp_path, kind):
    path = tmp_path / "models.json"
    if kind == "absent":
        path.write_text(json.dumps({"claude": {}, "restricted_roles": {}}))
    elif kind == "malformed":
        path.write_text("{not json")
    else:
        raise AssertionError(kind)
    monkeypatch.setattr(config_provider, "models_config_path", lambda: path)


def _count_git(monkeypatch):
    """Wrap the real Popen `_git_run` uses; record argv, delegate to it."""
    launches: list[list[str]] = []
    real = pydantic_openai._REAL_POPEN

    def _popen(argv, *a, **kw):
        launches.append([str(x) for x in argv])
        return real(argv, *a, **kw)

    monkeypatch.setattr(pydantic_openai, "_REAL_POPEN", _popen)
    return launches


def _kinds(launches):
    out = []
    for argv in launches:
        assert argv[0] == "git", argv
        rest = argv[3:] if argv[1] == "-C" else argv[1:]
        sub = rest[0]
        out.append(f"{sub}:{rest[1]}" if sub == "rev-parse" else sub)
    return out


def _fake_query(monkeypatch, *, hang=False):
    import claude_agent_sdk  # noqa: PLC0415

    async def _query(*, prompt, options):
        if hang:
            await asyncio.sleep(60)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)


def _call(monkeypatch, root, tools):
    agent_sdk.register()
    _fake_query(monkeypatch)
    launches = _count_git(monkeypatch)
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=10, step_name=_STEP,
        extra_data={"workspace_root": str(root)}, allowed_tools=tools,
        backend="agent-sdk", idle_timeout_sec=0,
    )
    assert res.status == "ok", res.error
    return res, _kinds(launches)


def _assert_skipped(res, kinds):
    assert kinds == [_GUARD], kinds
    assert res.data["worker_written_paths"] == []
    assert res.data["manifest_source"] == "git_diff"


# --- AC1 / AC1b --------------------------------------------------------------


def test_ac1_read_only_call_takes_no_snapshot(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    res, kinds = _call(monkeypatch, root, _READ_ONLY)
    _assert_skipped(res, kinds)


def test_ac1b_read_only_call_timeout_takes_no_snapshot(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    _fake_query(monkeypatch, hang=True)
    launches = _count_git(monkeypatch)
    res = agent_sdk.agent_sdk_backend(
        prompt="p", model="opus", timeout_sec=1, step_name=_STEP,
        extra_data={"workspace_root": str(root)}, allowed_tools=_READ_ONLY,
        idle_timeout_sec=0,
    )
    assert res.status == "error"
    assert res.error_code == "E_LLM_API_TIMEOUT", (res.error_code, res.error)
    assert _kinds(launches) == [_GUARD]
    assert res.data["worker_written_paths"] == []
    assert res.data["timed_out"] is True


# --- AC3b --------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["absent", "malformed"])
def test_ac3b_absent_key_or_malformed_file_skips(monkeypatch, tmp_path, kind):
    root = _repo(tmp_path)
    _models(monkeypatch, tmp_path, kind)
    res, kinds = _call(monkeypatch, root, _READ_ONLY)
    _assert_skipped(res, kinds)


# --- AC5 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "env", [{"CLAUDE_CODE_OAUTH_TOKEN": "oauth-bd106"}, {"ANTHROPIC_API_KEY": "sk-bd106"}],
    ids=["subscription", "api-token"],
)
def test_ac5_same_outcome_on_subscription_and_api_token(monkeypatch, tmp_path, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    root = _repo(tmp_path)
    res, kinds = _call(monkeypatch, root, _READ_ONLY)
    _assert_skipped(res, kinds)
