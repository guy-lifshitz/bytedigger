"""bd#71 — the default backend starts producing observations.

Spec: `docs/decisions/2026-08-05-bd71-agent-sdk-observations.md`.

Class: an observation discarded along the way. Not "there is no producer" (bd#68) and not
"the emitter is one-sided" (bd#61), but a third form: the evidence PASSES through the code and
is thrown away. The loop `agent_sdk.py:407-427` iterates the whole SDK stream — where both
`ToolUseBlock.name` and `AssistantMessage.model` live — and keeps only
`ResultMessage`.

Exposure: `_DEFAULT_BACKEND == "agent-sdk"`, i.e. this is the default production
path, and on it, after bd#70, ALL three L3 requirements are silent.

The fake SDK and the call form reuse the template of
`test_gh1157_agent_sdk_retry.py` — the same async-generator `query()`, the same
queue of behaviours. The UUT is not mocked: the real `agent_sdk_backend` is run.
"""
from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass

import pytest


@dataclass
class _ResultMessage:
    result: str | None = None
    usage: dict | None = None
    session_id: str | None = None
    is_error: bool = False


class _ToolUseBlock:
    def __init__(self, name):
        self.type = "tool_use"
        self.name = name
        self.id = "t1"
        self.input = {}


class _AssistantMessage:
    def __init__(self, content, model=None):
        self.content = content
        self.model = model


def _install_fake_sdk(monkeypatch, *, scripts):
    """`scripts` — a list of scenarios by attempt: each a list of messages."""
    queue = list(scripts)

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.kwargs = kwargs

    async def query(*, prompt, options):
        item = queue.pop(0) if queue else []
        if isinstance(item, dict) and "raise_after" in item:
            for m in item["raise_after"]:
                yield m
            raise item["exc"]
        for m in item:
            yield m

    mod = types.ModuleType("claude_agent_sdk")
    mod.query = query
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.ResultMessage = _ResultMessage
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "seed.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return root


def _backend():
    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    return agent_sdk


def _run(monkeypatch, tmp_path, *, scripts, model="claude-sonnet"):
    _install_fake_sdk(monkeypatch, scripts=scripts)
    root = _repo(tmp_path)
    return _backend().agent_sdk_backend(
        prompt="p", timeout_sec=10, step_name="s1",
        model=model, extra_data={"workspace_root": str(root)},
        allowed_tools=None, run_ctx=None, hard_gate=False, gate_label=None,
        straggler_cfg=None, idle_timeout_sec=None,
    )


def _ok(tools=(), model=None):
    """One successful run: an assistant message with blocks + a result."""
    return [
        _AssistantMessage([_ToolUseBlock(t) for t in tools], model=model),
        _ResultMessage(result="done"),
    ]


# ─── AC1/AC2: the tools, and both sides ──────────────────────────────────

def test_ac1_tool_names_reach_data(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, scripts=[_ok(tools=("Read", "Bash"))])

    assert isinstance(res.data, dict), f"got {res.data!r}"
    assert res.data.get("observed_tools") == ["Bash", "Read"], (
        f"tool names must reach data; got "
        f"{res.data.get('observed_tools')!r}"
    )


def test_ac2_no_tool_blocks_yields_empty_not_missing(monkeypatch, tmp_path):
    """NEGATIVE LEG: an accumulator that always writes the same thing is as inert
    as its absence (the bd#61 lesson)."""
    res = _run(monkeypatch, tmp_path, scripts=[_ok(tools=())])

    assert res.data.get("observed_tools") == []


# ─── AC3/AC4: the model, and it is the OBSERVED one, not the requested one ─

def test_ac3_model_from_the_stream_reaches_data(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, scripts=[_ok(model="claude-sonnet")])

    assert res.data.get("observed_model") == "claude-sonnet"


def test_ac4_observed_model_differs_from_requested_and_wins(monkeypatch, tmp_path):
    """NEGATIVE LEG (D3). Recording the requested one would mean comparing
    the pin with itself — R3.3 could never fail."""
    res = _run(monkeypatch, tmp_path, model="claude-opus",
               scripts=[_ok(model="claude-haiku")])

    assert res.data.get("observed_model") == "claude-haiku", (
        "the OBSERVED model must land in data, not the requested one"
    )


# ─── AC5: the end-to-end link evidence -> verdict ────────────────────────

def test_ac5_stream_evidence_reaches_the_l3_verdicts(monkeypatch, tmp_path):
    from bytedigger_engine.conformance import attest, bd_l3, tokens  # noqa: PLC0415

    res = _run(monkeypatch, tmp_path, model="sonnet",
               scripts=[_ok(tools=("Bash",), model="haiku")])

    report = bd_l3.check_bd_l3([{
        "type": attest.EVENT_TYPE,
        "payload": {"step_name": "s1", "model_requested": "sonnet",
                    "observed_model": res.data.get("observed_model"),
                    "declared_capabilities": ["Read"],
                    "observed_tools": res.data.get("observed_tools")},
    }])
    assert report.labels["verdict:R3.6"] == tokens.REQUIREMENT_FAILED, "escape"
    assert report.labels["verdict:R3.3"] == tokens.REQUIREMENT_FAILED, "model drift"


# ─── AC6: the retry does not leak ────────────────────────────────────────

class _Overloaded(Exception):
    status_code = 529


def test_ac6_observations_do_not_leak_across_attempts(monkeypatch, tmp_path):
    """The first attempt OBSERVES Bash/haiku and fails retryably (529), the second
    observes Read/sonnet and succeeds.

    The retry is triggered deterministically rather than by coincidence: a skip
    here would mean an AC that excuses itself and checks nothing.
    """
    monkeypatch.setenv("HAL_OUTAGE_PROBE", "0")
    res = _run(monkeypatch, tmp_path, scripts=[
        {"raise_after": [_AssistantMessage([_ToolUseBlock("Bash")], model="haiku")],
         "exc": _Overloaded("overloaded")},
        _ok(tools=("Read",), model="sonnet"),
    ])

    assert res.status == "ok", (
        f"the retry should have reached success; got {res.status!r} / "
        f"{res.error_code!r}"
    )
    assert res.data.get("observed_tools") == ["Read"], (
        f"the first attempt's observations leaked: {res.data.get('observed_tools')!r}"
    )
    assert res.data.get("observed_model") == "sonnet"


# ─── AC7: THE GATE — all registered backends ─────────────────────────────

def test_ac7_every_registered_backend_writes_or_is_declared():
    """The gate covers ALL backends, not only the one touched.

    The absence of such a gate produced three inert lots in a row; fixing where
    one looked and not placing a gate over the rest is the same form once again.
    """
    import inspect  # noqa: PLC0415
    import pathlib  # noqa: PLC0415

    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    ref_dir = pathlib.Path(inspect.getfile(_backend())).parent
    writing, silent = set(), set()
    for path in sorted(ref_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8")
        if '"observed_tools"' in src and '"observed_model"' in src:
            writing.add(path.stem)
        else:
            silent.add(path.stem)

    assert "agent_sdk" in writing, "the default backend must write both fields"
    undeclared = sorted(silent - set(bd_l3.SILENT_BACKENDS))
    assert not undeclared, (
        f"a backend writes no observations and is not declared: {undeclared!r}"
    )
    stale = sorted(set(bd_l3.SILENT_BACKENDS) & writing)
    assert not stale, (
        f"a backend has started writing but is still listed as silent: {stale!r}"
    )


def test_ac8_public_surface_equals_dunder_all():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
