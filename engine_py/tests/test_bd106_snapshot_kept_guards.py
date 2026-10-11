"""bd#106 guards - behaviour that must not change.

Spec: `docs/decisions/2026-10-11-bd106-skip-snapshot-read-only.md`.

Pins unchanged behaviour: a call that can write keeps the git snapshot and its
manifest (AC2, AC2b, AC4), and `restricted_roles.skip_read_only_snapshot: false`
(or any non-boolean value) restores the unconditional snapshot (AC3, AC3b).
These pass today and must still pass after the change. The failing cases live
in `test_bd106_skip_snapshot_read_only.py`.

Real side effects: a REAL dirty temporary git tree; git launches are counted by
wrapping `pydantic_openai._REAL_POPEN` and delegating to the real Popen; only
`claude_agent_sdk.query` is replaced; the switch lives in a REAL models file.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, reset_backends
from bytedigger_engine.lib.reference_backends import agent_sdk, pydantic_openai

_STEP = "bd106"
_READ_ONLY = ["Read", "Grep", "Glob"]
_SNAPSHOT_KINDS = {"diff", "status", "ls-files", "hash-object", "rev-parse:HEAD"}
_WRITTEN = "written_by_worker.txt"


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


def _models(monkeypatch, tmp_path, value):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"restricted_roles": {"skip_read_only_snapshot": value}}))
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


def _fake_query(monkeypatch, root, *, write=False):
    import claude_agent_sdk  # noqa: PLC0415

    async def _query(*, prompt, options):
        if write:
            (root / _WRITTEN).write_text("by the worker\n")
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sess-1", result="ok",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", _query)


def _call(monkeypatch, root, tools, *, write=False):
    agent_sdk.register()
    _fake_query(monkeypatch, root, write=write)
    launches = _count_git(monkeypatch)
    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=10, step_name=_STEP,
        extra_data={"workspace_root": str(root)}, allowed_tools=tools,
        backend="agent-sdk", idle_timeout_sec=0,
    )
    assert res.status == "ok", res.error
    return res, _kinds(launches)


def _assert_snapshotted(kinds):
    assert _SNAPSHOT_KINDS & set(kinds), kinds
    assert "diff" in kinds and "hash-object" in kinds, kinds


# --- guards ------------------------------------------------------------------


def test_ac2_write_tool_lists_exactly_the_written_file(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    res, kinds = _call(monkeypatch, root, ["Read", "Write"], write=True)
    assert res.data["worker_written_paths"] == [_WRITTEN]
    _assert_snapshotted(kinds)


@pytest.mark.parametrize("tools", [["Read", "Bash(git:*)"], ["Edit"]])
def test_ac2b_patterned_bash_and_edit_count_as_can_write(monkeypatch, tmp_path, tools):
    root = _repo(tmp_path)
    res, kinds = _call(monkeypatch, root, tools)
    _assert_snapshotted(kinds)


def test_ac3_switch_false_restores_the_snapshot(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    _models(monkeypatch, tmp_path, False)
    res, kinds = _call(monkeypatch, root, _READ_ONLY)
    _assert_snapshotted(kinds)


def test_ac3b_non_boolean_switch_value_keeps_the_snapshot(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    _models(monkeypatch, tmp_path, "yes")
    res, kinds = _call(monkeypatch, root, _READ_ONLY)
    _assert_snapshotted(kinds)


@pytest.mark.parametrize("tools", [None, []])
def test_ac4_unrestricted_or_empty_tools_keep_the_snapshot(monkeypatch, tmp_path, tools):
    root = _repo(tmp_path)
    res, kinds = _call(monkeypatch, root, tools)
    _assert_snapshotted(kinds)
