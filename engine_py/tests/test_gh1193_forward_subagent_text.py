"""RED tests for GH1193: --forward-subagent-text + bounded subagent-stream sink.

Frozen spec: SHARED/memory/Decisions/2026-07-26_C16F00DF_gh1193_forward_subagent_text_spec.md
Gate verdict (REJECTED, both Opus passes, required-changes list):
SHARED/memory/Decisions/2026-07-26_C16F00DF_gh1193_forward_subagent_text_build_tests.md

This file replaces the original 11-AC RED with the amended 21-AC (23 test
functions + 1 direct helper-unit test) spec. Amendments applied here, per the
gate's ordered "Required before GREEN" list:

  R1 (idle/timeout sink coverage)  -> AC14, AC15
  R2 (positive sink wiring)        -> AC12-PAIR, AC12-INERT
  B-M2 (real wire-format record)   -> AC9/AC9a/AC10/AC11/AC12/AC13/AC14/AC15/AC17
                                       rebuilt on nested {"type":"assistant",
                                       "parent_tool_use_id":..., "message":
                                       {"content":[...]}} events instead of
                                       flat records
  A-M5 (env routing)               -> AC4 narrowed to "0" only (canonical
                                       config_provider predicate, §2.3a)
  A-M4                             -> AC4a (truthy toggle is not silently
                                       disabling)
  R3 (parent_tool_use_id normalization, fail-closed) -> AC7c rebuilt on a
                                       genuinely KEY-LESS fixture; AC7d added
                                       (orphaned depth-1 write excluded)
  A-M7 (cmd_tail invariance)       -> AC16
  Minors                           -> AC18 (_forward_subagent_enabled
                                       importable), AC13 skipif root

Per §1q/D1CF5FDF: `subagent_forward_flags` (llm_provider.ProviderSpec field),
`_forward_subagent_enabled`, `_subagent_forward_flags`, `_write_subagent_sink`,
`_SUBAGENT_SINK_MAX_BYTES`, and `_manifest_eligible_events` do not exist in prod
yet. Those imports/attribute-accesses are deferred to inside each test body so
collection succeeds cleanly against current prod and only individual tests
FAIL at assert time (mirrors the existing sys.path-insert idiom already used
by sibling files in this directory, e.g. test_llm_subprocess_775D6752.py,
test_llm_subprocess_W1.py — not touched here, kept for minimum-diff risk).
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import (  # noqa: E402
    _written_paths_from_events,
    invoke_llm_subprocess,
    reset_backends,
)
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402

from bytedigger_engine.lib.llm_provider import (  # noqa: E402
    CLAUDE_PROVIDER,
    ProviderSpec,
    get_provider,
    register_provider,
    reset_providers,
)


# ---------------------------------------------------------------------------
# §1i autouse teardown — never rely on ambient env/registry state
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)
    monkeypatch.delenv("HAL_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("HAL_SUBAGENT_SINK_DIR", raising=False)
    monkeypatch.delenv("HAL_SUBAGENT_SINK_MAX_BYTES", raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()
    reset_providers()


# ─── Helpers ─────────────────────────────────────────────────────────────────


_RESULT_EVENT = (
    '{"type":"result","subtype":"success",'
    '"result":"OK",'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}\n'
)


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion (mirrors
    tests/test_llm_subprocess_W1.py::_FakeEventLog)."""

    def __init__(self):
        self.events: "list[tuple[str, dict, str]]" = []

    def append(self, event_type: str, payload: dict, run_id: "str | None" = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _capture_popen_from_argv(extra_stdout_lines: "list[str] | None" = None):
    """Return (captured_argv, side_effect) for patching subprocess.Popen.

    Mirrors tests/test_llm_subprocess_allowed_tools.py::_capture_popen_from_argv.
    """
    captured: "list[list[str]]" = []
    lines = "".join(extra_stdout_lines or []) + _RESULT_EVENT

    def _side_effect(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(lines)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(lines, ""))
        return proc

    return captured, _side_effect


def _agent_event(event_id: str, parent: "str | None") -> dict:
    """A depth-N assistant event whose content spawns another Agent (tool_use).

    ``parent`` is written explicitly (possibly None) — distinct from the
    genuinely key-less fixtures used for AC7c/§2.6 R3 below.
    """
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {
            "content": [
                {"type": "tool_use", "id": event_id, "name": "Agent", "input": {}}
            ]
        },
    }


def _write_event(write_id: str, parent: "str | None", file_path: str) -> dict:
    """An assistant event whose content is a Write tool_use (parent explicit)."""
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": write_id,
                    "name": "Write",
                    "input": {"file_path": file_path},
                }
            ]
        },
    }


def _keyless_agent_event(event_id: str) -> dict:
    """A ROOT assistant Agent-spawn event with NO 'parent_tool_use_id' key at
    all — mirrors real production streams and
    tests/test_4961254A_commit_manifest_inversion.py:103-160 fixtures, which
    is what today's engine actually emits (spec §2.6 gate R3)."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": event_id, "name": "Agent", "input": {}}
            ]
        },
    }


def _keyless_write_event(write_id: str, file_path: str) -> dict:
    """A ROOT Write event with NO 'parent_tool_use_id' key at all."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": write_id,
                    "name": "Write",
                    "input": {"file_path": file_path},
                }
            ]
        },
    }


def _forwarded_content_event(parent: "str | None", blocks: "list[dict]") -> dict:
    """The REAL forwarded wire shape (spec §2.3 'gate B-M2'): a nested
    assistant event whose message.content carries one or more text/thinking
    content blocks, each with its OWN 'type' ('text' or 'thinking') and
    payload under the matching key ('text' or 'thinking' respectively).
    """
    return {
        "type": "assistant",
        "parent_tool_use_id": parent,
        "message": {"content": blocks},
    }


def _keyless_content_event(blocks: "list[dict]") -> dict:
    """Same wire shape as ``_forwarded_content_event`` but with NO
    'parent_tool_use_id' key at all (root-level chatter as it actually
    appears in an unforwarded stream)."""
    return {
        "type": "assistant",
        "message": {"content": blocks},
    }


def _wire_events_for_sink() -> "list[dict]":
    """One subagent-attributed event (2 content blocks: text + thinking),
    one root event with parent explicitly None, one root event with the key
    absent entirely — exercises all three "not subagent-attributed" shapes
    the §2.3 filter (AC10) must drop.
    """
    return [
        _forwarded_content_event(
            "toolu_agent2",
            [
                {"type": "text", "text": "depth-2 chatter"},
                {"type": "thinking", "thinking": "depth-2 thinking"},
            ],
        ),
        _forwarded_content_event(
            None, [{"type": "text", "text": "root chatter — must be dropped"}]
        ),
        _keyless_content_event(
            [{"type": "text", "text": "no parent key at all — must be dropped"}]
        ),
    ]


def _sink_uut():
    """Resolve the real production sink helper — never stub it (§1l).

    Deferred to call time so this module collects cleanly pre-GREEN
    (§1q/D1CF5FDF): resolving via getattr + assert makes the absence itself
    fail at ASSERT time rather than at import/collect time.
    """
    fn = getattr(llm_subprocess, "_write_subagent_sink", None)
    assert callable(fn), (
        "llm_subprocess._write_subagent_sink is not defined (or not callable). "
        "Spec §2.3 requires a named, importable helper "
        "_write_subagent_sink(events, sink_dir, step_name). got: " + repr(fn)
    )
    return fn


# ─── AC1: ProviderSpec accepts subagent_forward_flags, defaulting to () ──────


def test_ac1_provider_spec_subagent_forward_flags_defaults_to_empty_tuple():
    """AC1 — spec §3: ProviderSpec accepts subagent_forward_flags, defaulting
    to () (existing stub constructions omitting it still build)."""
    stub = ProviderSpec(
        name="stub-ac1",
        build_argv=lambda model: ["stub-cli", model],
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"stub-model": 0},
        model_family=lambda model: "stub-model" if model else None,
        default_gate_floor="stub-model",
    )
    assert stub.subagent_forward_flags == ()


# ─── AC2: CLAUDE_PROVIDER declares the flag; stream_flags untouched ──────────


def test_ac2_claude_provider_declares_forward_subagent_text_flag():
    """AC2 — spec §3: CLAUDE_PROVIDER.subagent_forward_flags ==
    ("--forward-subagent-text",) and .stream_flags is UNCHANGED."""
    assert CLAUDE_PROVIDER.subagent_forward_flags == ("--forward-subagent-text",)
    assert CLAUDE_PROVIDER.stream_flags == ("--output-format", "stream-json", "--verbose")


# ─── AC3: default (toggle unset) → flag present exactly once, after stream flags ──


def test_ac3_flag_present_by_default_after_stream_flags(monkeypatch):
    """AC3 — spec §3/§2.2: default (toggle unset) → effective_command
    contains --forward-subagent-text exactly once, after the stream flags."""
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac3",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert argv.count("--forward-subagent-text") == 1, (
        f"expected --forward-subagent-text exactly once in argv, got argv={argv!r}"
    )
    idx_stream = argv.index("--output-format")
    idx_flag = argv.index("--forward-subagent-text")
    assert idx_flag > idx_stream, (
        f"--forward-subagent-text must come AFTER the stream flags; "
        f"stream at {idx_stream}, flag at {idx_flag}, argv={argv!r}"
    )


# ─── AC4: HAL_FORWARD_SUBAGENT_TEXT="0" disables; narrowed to "0" only ───────


def test_ac4_literal_zero_disables_flag(monkeypatch):
    """AC4 — spec §3/§2.3a: HAL_FORWARD_SUBAGENT_TEXT="0" (the canonical
    config_provider disable token) → flag absent from argv."""
    monkeypatch.setenv("HAL_FORWARD_SUBAGENT_TEXT", "0")
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac4",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" not in argv, (
        f"HAL_FORWARD_SUBAGENT_TEXT='0' must disable the flag; found it in "
        f"argv={argv!r}"
    )


@pytest.mark.parametrize("non_disable_value", ["false", "FALSE", "no", "No"])
def test_ac4_narrowed_false_and_no_do_not_disable(monkeypatch, non_disable_value):
    """AC4 (narrowed, spec §2.3a) — the accepted disable-value set narrows to
    "0" only. 'false'/'no' are NOT disable tokens under the canonical
    config_provider predicate ('enabled unless explicitly "0"'); a GREEN that
    reintroduces the old case-insensitive false/no set is wrong per the
    amended spec — the flag must stay PRESENT for these values."""
    monkeypatch.setenv("HAL_FORWARD_SUBAGENT_TEXT", non_disable_value)
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac4_narrowed",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" in argv, (
        f"HAL_FORWARD_SUBAGENT_TEXT={non_disable_value!r} is NOT a disable "
        f"token under the canonical predicate (only '0' is, spec §2.3a) — "
        f"the flag must remain present. argv={argv!r}"
    )


# ─── AC4a: explicit truthy value must NOT silently disable (gate A-M4) ──────


@pytest.mark.parametrize("truthy_value", ["1", "yes", "true"])
def test_ac4a_explicit_truthy_value_keeps_flag_present(monkeypatch, truthy_value):
    """AC4a (gate A-M4) — an operator explicitly setting
    HAL_FORWARD_SUBAGENT_TEXT to a truthy value (the exact thing typed to
    FORCE observability on during a hang) must NOT silently disable the
    flag. Blocks a GREEN implementing
    ``return env_opt(...) is None`` (enabled-when-unset, i.e. disabled on
    ANY explicit value including the operator's own "turn it on")."""
    monkeypatch.setenv("HAL_FORWARD_SUBAGENT_TEXT", truthy_value)
    captured, side = _capture_popen_from_argv()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac4a",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" in argv, (
        f"HAL_FORWARD_SUBAGENT_TEXT={truthy_value!r} is an explicit truthy "
        f"value and must NOT disable the flag (an 'env is None' predicate "
        f"would fail here — the operator's own enable-value silently "
        f"disabling is exactly the bug gate A-M4 blocks). argv={argv!r}"
    )


# ─── AC5: a provider declaring subagent_forward_flags=() never gets the flag ──


def test_ac5_provider_scope_empty_tuple_never_forwarded(monkeypatch):
    """AC5 — spec §3: a provider declaring subagent_forward_flags=() never
    receives the flag, even with the toggle ON (default)."""
    from bytedigger_engine.llm_subprocess import _subagent_forward_flags

    stub = ProviderSpec(
        name="stub-ac5",
        build_argv=lambda model: ["stub-cli", model],
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"x": 0},
        model_family=lambda model: None,
        default_gate_floor="x",
        subagent_forward_flags=(),
    )
    try:
        register_provider(stub)
        monkeypatch.setenv("HAL_LLM_PROVIDER", "stub-ac5")
        monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)  # toggle ON (default)

        assert get_provider().name == "stub-ac5"
        assert _subagent_forward_flags() == (), (
            "a provider declaring subagent_forward_flags=() must never receive "
            "the flag, even with the toggle enabled"
        )
    finally:
        reset_providers()


# ─── AC6: stream reader ingests parent_tool_use_id-bearing events without loss ──


def test_ac6_stream_reader_ingests_forwarded_events_without_loss():
    """AC6 — spec §3: _stream_read_events ingests forwarded events carrying
    parent_tool_use_id without loss or throw; they appear in returned events."""
    from bytedigger_engine.llm_subprocess import _stream_read_events

    depth2_event = _write_event("toolu_write2", "toolu_agent2", "depth2.txt")
    lines = json.dumps(depth2_event) + "\n" + _RESULT_EVENT

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = 0
    proc.stdout = io.StringIO(lines)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)

    timed_out, stdout, stderr, events, idle_aborted, cli_lingered = _stream_read_events(
        proc=proc, prompt="hi", timeout_sec=5, idle_timeout_sec=0
    )

    assert timed_out is False
    matches = [
        ev for ev in events
        if ev.get("type") == "assistant" and ev.get("parent_tool_use_id") == "toolu_agent2"
    ]
    assert len(matches) == 1, (
        f"forwarded subagent event with parent_tool_use_id was lost or duplicated; "
        f"events={events!r}"
    )
    assert matches[0] == depth2_event


# ─── AC7/AC7a/AC7b/AC7c/AC7d: manifest depth-1 ceiling (§2.6) ───────────────


def _depth_chain_events() -> "list[dict]":
    """root -> Agent(agent1) -> Write(depth1.txt) + Agent(agent2)
                  -> Write(depth2.txt) + Agent(agent3)
                       -> Write(depth3.txt)
    """
    return [
        _agent_event("toolu_agent1", None),
        _write_event("toolu_write1", "toolu_agent1", "depth1.txt"),
        _agent_event("toolu_agent2", "toolu_agent1"),
        _write_event("toolu_write2", "toolu_agent2", "depth2.txt"),
        _agent_event("toolu_agent3", "toolu_agent2"),
        _write_event("toolu_write3", "toolu_agent3", "depth3.txt"),
    ]


def test_ac7_depth2_write_excluded_root_and_depth1_included():
    """AC7 — spec §3/§2.6: a depth-2 Write is excluded from the manifest
    while root/depth-1 writes remain included."""
    events = _depth_chain_events()
    result = _written_paths_from_events(events)

    assert "depth1.txt" in result, "root/depth-1 write must still be included"
    assert "depth2.txt" not in result, (
        f"depth-2 write must be EXCLUDED from the manifest (§2.6 depth-1 ceiling); "
        f"got result={result!r}"
    )


def test_ac7a_depth1_write_included_guard_not_overfiltering():
    """AC7a — spec §3/§2.6: the depth guard must not over-filter and
    silently empty the manifest — depth-1 writes remain included."""
    events = _depth_chain_events()
    result = _written_paths_from_events(events)

    assert "depth1.txt" in result, (
        f"depth-1 write must remain included — the depth guard must not "
        f"over-filter and silently empty the manifest; result={result!r}"
    )


def test_ac7b_depth3_write_excluded_by_same_depth_rule():
    """AC7b — spec §3/§2.6: depth is derived, not hardcoded to "2" — a
    depth-3 write is excluded by the same rule that excludes depth-2."""
    events = _depth_chain_events()
    result = _written_paths_from_events(events)

    assert "depth3.txt" not in result, (
        f"depth-3 write must be excluded by the same depth-derived rule that "
        f"excludes depth-2; got result={result!r}"
    )


def test_ac7c_pre_flag_stream_no_parent_key_at_all_unchanged_output():
    """AC7c (rebuilt per gate R3/spec §2.6.1) — a GENUINELY key-less stream
    (no event carries a 'parent_tool_use_id' key at all — mirrors
    tests/test_4961254A_commit_manifest_inversion.py:103-160 and TODAY'S
    real production streams, per spec §2.6 gate R3) must be a no-op for the
    depth guard. The PREVIOUS AC7c (which explicitly set
    parent_tool_use_id=None on both events) could not catch a GREEN that
    reads absent-key as "not root": that GREEN empties the manifest on
    EVERY real run, since real streams never carry the key at all.
    """
    pre_flag_events = [
        _keyless_agent_event("toolu_agent1"),
        _keyless_write_event("toolu_write1", "depth1.txt"),
    ]
    assert "parent_tool_use_id" not in pre_flag_events[0], "fixture sanity: genuinely key-less"
    assert "parent_tool_use_id" not in pre_flag_events[1], "fixture sanity: genuinely key-less"
    result = _written_paths_from_events(pre_flag_events)
    assert result == ["depth1.txt"], (
        f"a stream with NO parent_tool_use_id key anywhere must be UNCHANGED "
        f"by the depth guard (root ⟺ key is None, absent, or a non-str, "
        f"spec §2.6.1); got {result!r}"
    )


def test_ac7d_orphaned_depth1_write_excluded_fail_closed():
    """AC7d (gate R3/§2.6.4) — a write whose parent_tool_use_id references a
    tool_use id that was NEVER seen as a root-spawned Agent/Task block (its
    spawn line was silently dropped, e.g. by the :2162 json.loads-failure
    'continue') must be EXCLUDED — fail-CLOSED. The guard may never WIDEN
    the manifest, since widening is exactly what reaches
    phase_6_review._partition_fix_surface's unlink().
    """
    events = [
        # No _agent_event("toolu_agent_missing", ...) exists in this list —
        # its spawn line was dropped, simulating an ancestor-loss scenario.
        _write_event("toolu_write_orphan", "toolu_agent_missing", "orphan.txt"),
    ]
    result = _written_paths_from_events(events)
    assert "orphan.txt" not in result, (
        f"a write whose parent id was never seen as a root-spawned tool_use "
        f"must be excluded (fail-closed) — widening here is exactly what "
        f"reaches unlink(); got result={result!r}"
    )


def test_ac7_manifest_eligible_events_symbol_reflects_depth1_ceiling():
    """Direct unit test of the new named helper _manifest_eligible_events
    (§1aa forcing function), anchored to AC7/AC7b (spec §2.6)."""
    from bytedigger_engine.llm_subprocess import _manifest_eligible_events

    events = _depth_chain_events()
    eligible = _manifest_eligible_events(events)

    eligible_write_files = set()
    for ev in eligible:
        content = ev.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Write":
                eligible_write_files.add(block["input"]["file_path"])

    assert eligible_write_files == {"depth1.txt"}, (
        f"_manifest_eligible_events must keep only root+depth-1 events; "
        f"got write files {eligible_write_files!r}"
    )


# ─── AC8: result parsing unaffected by interleaved forwarded events ──────────


def test_ac8_result_parsing_unaffected_by_interleaved_forwarded_events():
    """AC8 — spec §3: subtype=="success" parsing is unaffected by
    interleaved forwarded events (regression guard)."""
    depth2 = _write_event("toolu_write2", "toolu_agent2", "depth2.txt")
    events = [
        _agent_event("toolu_agent1", None),
        depth2,
        {"type": "result", "subtype": "success", "result": "final answer"},
    ]
    result_event = get_provider().parse_result(events)
    assert result_event is not None
    assert result_event.get("subtype") == "success"
    assert result_event.get("result") == "final answer"


# ─── AC9/AC9a/AC10/AC11/AC17: bounded subagent-stream sink (unit-level) ─────


def test_ac9_sink_writes_one_record_per_subagent_attributed_content_block():
    """AC9 — spec §3/§2.3 FILTER: one JSONL record per subagent-attributed
    CONTENT BLOCK (not per event) — the depth-2 event here has 2 blocks
    (text + thinking), so 2 records carrying parent_tool_use_id."""
    write_sink = _sink_uut()
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    events = _wire_events_for_sink()

    write_sink(events, sink_dir, "step_ac9")

    sink_path = Path(sink_dir) / "subagent-step_ac9.jsonl"
    assert sink_path.exists(), f"expected sink file at {sink_path}, dir contents={os.listdir(sink_dir)!r}"

    records = [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    subagent_records = [r for r in records if r.get("parent_tool_use_id") == "toolu_agent2"]
    assert len(subagent_records) == 2, (
        f"expected one JSONL record per subagent-attributed CONTENT BLOCK "
        f"(1 event x 2 blocks = 2 records); got records={records!r}"
    )


def test_ac9a_record_shape_per_block_type_and_thinking_payload():
    """AC9a (gate B-M2) — sink record shape matches the real wire format:
    one record per content block, {parent_tool_use_id, block_type, text}
    where block_type is the BLOCK's own "type" ("text"/"thinking") and the
    payload is read from "text" OR "thinking" — the thinking key is NOT
    "text"."""
    write_sink = _sink_uut()
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    events = [
        _forwarded_content_event(
            "toolu_agent2",
            [
                {"type": "text", "text": "depth-2 chatter"},
                {"type": "thinking", "thinking": "depth-2 thinking"},
            ],
        )
    ]

    write_sink(events, sink_dir, "step_ac9a")

    sink_path = Path(sink_dir) / "subagent-step_ac9a.jsonl"
    assert sink_path.exists()
    records = [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 2, f"expected 2 records (one per content block); got {records!r}"

    text_recs = [r for r in records if r.get("block_type") == "text"]
    thinking_recs = [r for r in records if r.get("block_type") == "thinking"]
    assert len(text_recs) == 1 and text_recs[0].get("text") == "depth-2 chatter", (
        f"text block must carry block_type='text' and payload from the 'text' "
        f"key; got records={records!r}"
    )
    assert len(thinking_recs) == 1 and thinking_recs[0].get("text") == "depth-2 thinking", (
        f"thinking block payload must be READ FROM the 'thinking' key (a "
        f"thinking block has no 'text' key) and stored under the record's "
        f"'text' field; got records={records!r}"
    )


def test_ac10_sink_drops_records_with_null_or_absent_parent_tool_use_id():
    """AC10 — spec §3/§2.3 FILTER: no record whose parent_tool_use_id is
    absent/null — covers both the explicit-None and the genuinely key-less
    shapes."""
    write_sink = _sink_uut()
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    events = _wire_events_for_sink()

    write_sink(events, sink_dir, "step_ac10")

    sink_path = Path(sink_dir) / "subagent-step_ac10.jsonl"
    assert sink_path.exists()
    records = [json.loads(line) for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    for rec in records:
        assert rec.get("parent_tool_use_id"), (
            f"sink must persist NO record whose parent_tool_use_id is absent/null; "
            f"found offending record={rec!r} in records={records!r}"
        )
    assert not any(r.get("text") == "root chatter — must be dropped" for r in records)
    assert not any(r.get("text") == "no parent key at all — must be dropped" for r in records)


def test_ac11_exceeding_byte_budget_stops_appends_and_writes_one_truncated_record(monkeypatch):
    """AC11 — spec §3/§2.3 THRESHOLD: exceeding
    _SUBAGENT_SINK_MAX_BYTES stops appends and writes exactly one terminal
    {"type":"truncated","dropped_events":N,"limit_bytes":L} record. Byte cap
    monkeypatched directly (§1i: deterministic, not a timing race)."""
    write_sink = _sink_uut()
    from bytedigger_engine import llm_subprocess as llm_subprocess_mod

    monkeypatch.setattr(llm_subprocess_mod, "_SUBAGENT_SINK_MAX_BYTES", 200)

    sink_dir = os.path.realpath(tempfile.mkdtemp())
    big_text = "x" * 100
    events = [
        _forwarded_content_event(f"toolu_agentN{i}", [{"type": "text", "text": big_text}])
        for i in range(10)
    ]

    write_sink(events, sink_dir, "step_ac11")

    sink_path = Path(sink_dir) / "subagent-step_ac11.jsonl"
    assert sink_path.exists()
    lines = [line for line in sink_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]

    truncated = [r for r in records if r.get("type") == "truncated"]
    assert len(truncated) == 1, (
        f"exceeding the byte budget must write EXACTLY ONE terminal truncated record; "
        f"got {len(truncated)} in records={records!r}"
    )
    assert "dropped_events" in truncated[0]
    assert "limit_bytes" in truncated[0]
    assert truncated[0] is records[-1], "the truncated record must be terminal (last in file)"
    assert len(records) < len(events) + 1, (
        "the sink must have STOPPED appending before writing all 10 events plus "
        "the terminal record — a silent full write means the cap did nothing"
    )


def test_ac17_env_override_of_byte_budget_is_honoured(monkeypatch):
    """AC17 — spec §3/§2.3a: HAL_SUBAGENT_SINK_MAX_BYTES env override is
    honoured (limit_bytes on the truncated record reflects it), routed
    through config_provider at call time (not a def-time default)."""
    write_sink = _sink_uut()
    monkeypatch.setenv("HAL_SUBAGENT_SINK_MAX_BYTES", "150")

    sink_dir = os.path.realpath(tempfile.mkdtemp())
    big_text = "x" * 100
    events = [
        _forwarded_content_event(f"toolu_agentN{i}", [{"type": "text", "text": big_text}])
        for i in range(10)
    ]

    write_sink(events, sink_dir, "step_ac17")

    sink_path = Path(sink_dir) / "subagent-step_ac17.jsonl"
    assert sink_path.exists()
    records = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    truncated = [r for r in records if r.get("type") == "truncated"]
    assert len(truncated) == 1, (
        f"HAL_SUBAGENT_SINK_MAX_BYTES=150 must trigger truncation; got records={records!r}"
    )
    assert truncated[0].get("limit_bytes") == 150, (
        f"limit_bytes must reflect the HAL_SUBAGENT_SINK_MAX_BYTES override "
        f"(150), not the 2 MiB default; got {truncated[0]!r}"
    )


# ─── AC12/AC12-PAIR/AC12-INERT: level gate + production wiring ──────────────


def test_ac12_sink_disabled_by_toggle_produces_no_sidecar_file(monkeypatch):
    """AC12 — spec §3/§2.3 LEVEL: HAL_FORWARD_SUBAGENT_TEXT=0 disables the
    flag AND the sink together (one switch, no split-brain)."""
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    monkeypatch.setenv("HAL_SUBAGENT_SINK_DIR", sink_dir)
    monkeypatch.setenv("HAL_FORWARD_SUBAGENT_TEXT", "0")

    depth2_tool = _write_event("toolu_write2", "toolu_agent2", "depth2.txt")
    text_event = _forwarded_content_event(
        "toolu_agent2", [{"type": "text", "text": "chatter"}]
    )
    captured, side = _capture_popen_from_argv(
        extra_stdout_lines=[json.dumps(depth2_tool) + "\n", json.dumps(text_event) + "\n"]
    )

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac12",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert not os.listdir(sink_dir), (
        f"HAL_FORWARD_SUBAGENT_TEXT=0 must disable the sink together with the "
        f"flag (one switch, no split-brain); found files={os.listdir(sink_dir)!r}"
    )


def test_ac12_pair_full_invoke_writes_sidecar_with_depth2_record(monkeypatch):
    """AC12-PAIR (gate R2) — THE ONLY AC proving the sink is wired into
    production at all: default toggle (ON) + HAL_SUBAGENT_SINK_DIR set ->
    a full invoke_llm_subprocess() call -> the sidecar EXISTS and CONTAINS
    the depth-2 record. A GREEN implementing a flawless
    _write_subagent_sink helper that is never called from _invoke_subprocess
    turns AC9-AC11/AC17 green while shipping zero new observable bytes —
    this AC closes that hole.
    """
    write_sink = _sink_uut()  # noqa: F841 (load-bearing existence check)
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    monkeypatch.setenv("HAL_SUBAGENT_SINK_DIR", sink_dir)
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)  # toggle default ON

    depth2 = _forwarded_content_event(
        "toolu_agent2", [{"type": "text", "text": "depth-2 chatter"}]
    )
    captured, side = _capture_popen_from_argv(
        extra_stdout_lines=[json.dumps(depth2) + "\n"]
    )

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        result = invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac12_pair",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert result.status == "ok", (
        f"precondition: fixture stream is a successful run; got status="
        f"{result.status!r} err={result.error!r}"
    )
    sink_path = Path(sink_dir) / "subagent-test_ac12_pair.jsonl"
    assert sink_path.exists(), (
        f"expected the production call site to have written a sidecar at "
        f"{sink_path}; dir contents={os.listdir(sink_dir)!r}"
    )
    records = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    depth2_records = [r for r in records if r.get("parent_tool_use_id") == "toolu_agent2"]
    assert depth2_records, (
        f"expected a depth-2 subagent record produced by a REAL "
        f"invoke_llm_subprocess call (not the helper called directly); "
        f"got records={records!r}"
    )
    assert any(r.get("text") == "depth-2 chatter" for r in depth2_records), (
        f"expected the record payload to carry the forwarded text; got "
        f"{depth2_records!r}"
    )


def test_ac12_inert_sink_dir_unset_writes_nothing_step_still_ok(monkeypatch):
    """AC12-INERT (gate A-M8) — HAL_SUBAGENT_SINK_DIR unset + toggle ON
    (default) -> nothing is written ANYWHERE (not cwd, not
    tempfile.gettempdir()) and the step still status="ok". §2.3's whole
    blast-radius argument rests on this inertness promise; nothing stops a
    naive GREEN from defaulting sink_dir to cwd or gettempdir() otherwise.
    """
    write_sink = _sink_uut()  # noqa: F841 (load-bearing existence check)
    monkeypatch.delenv("HAL_SUBAGENT_SINK_DIR", raising=False)
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)

    fake_tmp = os.path.realpath(tempfile.mkdtemp())
    fake_cwd = os.path.realpath(tempfile.mkdtemp())
    monkeypatch.setattr(tempfile, "gettempdir", lambda: fake_tmp)
    monkeypatch.chdir(fake_cwd)

    depth2 = _forwarded_content_event(
        "toolu_agent2", [{"type": "text", "text": "chatter"}]
    )
    captured, side = _capture_popen_from_argv(
        extra_stdout_lines=[json.dumps(depth2) + "\n"]
    )

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        result = invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=10,
            step_name="test_ac12_inert",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert result.status == "ok", (
        f"sink-dir-unset must not fail the step; got status={result.status!r} "
        f"err={result.error!r}"
    )
    assert os.listdir(fake_tmp) == [], (
        f"HAL_SUBAGENT_SINK_DIR unset must NOT fall back to "
        f"tempfile.gettempdir(); found files={os.listdir(fake_tmp)!r}"
    )
    assert os.listdir(fake_cwd) == [], (
        f"HAL_SUBAGENT_SINK_DIR unset must NOT fall back to cwd; found "
        f"files={os.listdir(fake_cwd)!r}"
    )


# ─── AC13: unwritable sink path does not fail the step (through the call site) ──


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory write-permission checks",
)
def test_ac13_unwritable_sink_path_does_not_change_step_result_status(monkeypatch):
    """AC13 — spec §3: an unwritable sink path does not change
    StepResult.status, asserted THROUGH invoke_llm_subprocess (not the
    helper alone) so a call-site os.makedirs cannot raise outside the
    helper's own best-effort guard."""
    write_sink = _sink_uut()  # noqa: F841 (load-bearing existence check)
    parent_dir = os.path.realpath(tempfile.mkdtemp())
    readonly_dir = os.path.join(parent_dir, "readonly_sink")
    os.mkdir(readonly_dir)
    os.chmod(readonly_dir, 0o500)  # read+execute, no write
    try:
        monkeypatch.setenv("HAL_SUBAGENT_SINK_DIR", readonly_dir)
        monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)

        depth2 = _forwarded_content_event(
            "toolu_agent2", [{"type": "text", "text": "chatter"}]
        )
        captured, side = _capture_popen_from_argv(
            extra_stdout_lines=[json.dumps(depth2) + "\n"]
        )

        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
            result = invoke_llm_subprocess(
                prompt="hi",
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="test_ac13",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )

        assert result.status == "ok", (
            f"an unwritable sink_dir must not fail the step (observability "
            f"must not break the pipeline); got status={result.status!r} "
            f"error={result.error!r}"
        )
    finally:
        os.chmod(readonly_dir, stat.S_IRWXU)


# ─── AC14/AC15: sink call site is BEFORE the error branches (gate R1) ──────


class _IdleThenSilentStdout:
    """Yields one line immediately, then blocks (simulating a subprocess
    that has stopped emitting events but hasn't EOF'd) until ``close()``.
    §1i: deterministic close-driven unblock, no sleep-vs-timeout race on
    cleanup — pattern mirrors tests/test_llm_subprocess_775D6752.py
    ``_TimedStdout``.
    """

    def __init__(self, line: str, block_sec: float = 30.0):
        self._line = line if line.endswith("\n") else line + "\n"
        self._delivered = False
        self._block_sec = block_sec
        self._closed = threading.Event()

    def readline(self):
        if not self._delivered:
            self._delivered = True
            return self._line
        end = time.monotonic() + self._block_sec
        while time.monotonic() < end and not self._closed.is_set():
            time.sleep(0.05)
        return ""

    def close(self):
        self._closed.set()


def test_ac14_idle_abort_branch_sidecar_has_prekill_subagent_records(monkeypatch):
    """AC14 (gate R1) — the idle-abort branch (E_LLM_NO_PROGRESS) with
    HAL_SUBAGENT_SINK_DIR set still writes the sidecar with the pre-kill
    subagent records. The GH1194 precedent
    (llm_subprocess.py:1443-1449) settled that visibility on this same
    function must not be conditional on success — a hang is exactly the
    #1169/#1177 scenario this ship was commissioned to solve.
    """
    write_sink = _sink_uut()  # noqa: F841 (load-bearing existence check)
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    monkeypatch.setenv("HAL_SUBAGENT_SINK_DIR", sink_dir)
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)

    depth2_line = json.dumps(
        _forwarded_content_event("toolu_agent2", [{"type": "text", "text": "pre-kill chatter"}])
    )
    stdout_obj = _IdleThenSilentStdout(depth2_line)

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    proc.stdout = stdout_obj
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=-9)
    proc.kill = MagicMock(side_effect=stdout_obj.close)
    proc.communicate = MagicMock(return_value=("", ""))

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=60,
            step_name="test_ac14",
            idle_timeout_sec=2,
            straggler_cfg=None,
        )

    assert result.error_code == "E_LLM_NO_PROGRESS", (
        f"precondition: idle-past-2s with no further events must abort idle; "
        f"got error_code={result.error_code!r} status={result.status!r}"
    )
    sink_path = Path(sink_dir) / "subagent-test_ac14.jsonl"
    assert sink_path.exists(), (
        f"gate R1: the sink must be written even on the idle-abort branch — "
        f"expected sidecar at {sink_path}; dir contents={os.listdir(sink_dir)!r}"
    )
    records = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(r.get("parent_tool_use_id") == "toolu_agent2" for r in records), (
        f"expected the pre-kill subagent record in the idle-abort sidecar; "
        f"got {records!r}"
    )


def test_ac15_timeout_branch_sidecar_has_prekill_subagent_records(monkeypatch, request):
    """AC15 (gate R1) — the outer-ceiling timeout branch (E_LLM_TIMEOUT)
    with HAL_SUBAGENT_SINK_DIR set still writes the sidecar with the
    pre-timeout subagent records. §1i: the blocking is released
    deterministically via a threading.Event finalizer (pattern from the
    GH1194 sibling suite, tests/test_gh1194_*.py test_ac14) — no
    sleep-vs-timeout race, and the blocking thread unblocks immediately at
    test end.
    """
    write_sink = _sink_uut()  # noqa: F841 (load-bearing existence check)
    sink_dir = os.path.realpath(tempfile.mkdtemp())
    monkeypatch.setenv("HAL_SUBAGENT_SINK_DIR", sink_dir)
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)

    depth2_line = json.dumps(
        _forwarded_content_event("toolu_agent3", [{"type": "text", "text": "outer-timeout chatter"}])
    )

    release = threading.Event()
    request.addfinalizer(release.set)

    class _BlockingStdout:
        def __init__(self) -> None:
            self._delivered = False

        def readline(self):
            if not self._delivered:
                self._delivered = True
                return depth2_line + "\n"
            release.wait(30)
            return ""

        def close(self):
            return None

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    proc.stdout = _BlockingStdout()
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=-9)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("", ""))

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="hi",
            model="claude-3-haiku-20240307",
            timeout_sec=2,
            step_name="test_ac15",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )

    assert result.error_code == "E_LLM_TIMEOUT", (
        f"precondition: blocked stdout past timeout_sec=2 must error "
        f"E_LLM_TIMEOUT; got error_code={result.error_code!r} status="
        f"{result.status!r}"
    )
    sink_path = Path(sink_dir) / "subagent-test_ac15.jsonl"
    assert sink_path.exists(), (
        f"gate R1: the sink must be written even on the outer-timeout "
        f"branch — expected sidecar at {sink_path}; dir contents="
        f"{os.listdir(sink_dir)!r}"
    )
    records = [
        json.loads(line)
        for line in sink_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(r.get("parent_tool_use_id") == "toolu_agent3" for r in records), (
        f"expected the pre-timeout subagent record in the sidecar; got "
        f"{records!r}"
    )


# ─── AC16: cmd_tail invariance (gate A-M7 / §2.5) ───────────────────────────


def test_ac16_flag_not_in_cmd_tail_redacted(monkeypatch):
    """AC16 (gate A-M7 / spec §2.5) — the flag is appended immediately after
    the stream flags (:1347), NOT at the end of assembly, so it must NOT
    leak into the redacted cmd_tail (n=3) that subprocess_spawned emits. An
    end-of-assembly append would rewrite cmd_tail and silently degrade
    existing observability (~8 exit/error events read it too)."""
    monkeypatch.delenv("HAL_FORWARD_SUBAGENT_TEXT", raising=False)
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r_ac16", step_name="s", phase="p")
    captured, side = _capture_popen_from_argv()
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
            invoke_llm_subprocess(
                prompt="hi",
                model="claude-3-haiku-20240307",
                timeout_sec=10,
                step_name="test_ac16",
                idle_timeout_sec=0,
                straggler_cfg=None,
                allowed_tools=["Bash"],
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert len(captured) == 1
    argv = captured[0]
    assert "--forward-subagent-text" in argv, (
        f"precondition (AC3 must hold): --forward-subagent-text must be "
        f"present in the full argv for this cmd_tail-leak check to be "
        f"meaningful; got argv={argv!r}"
    )

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned) == 1, "expected exactly one subprocess_spawned event"
    cmd_tail = spawned[0][1].get("cmd_tail")
    assert "--forward-subagent-text" not in (cmd_tail or []), (
        f"the flag must NOT leak into the redacted cmd_tail — an "
        f"end-of-assembly append would fail this (§2.5 positional "
        f"decision). got cmd_tail={cmd_tail!r}"
    )


# ─── AC18: _forward_subagent_enabled is an importable, callable symbol ─────


def test_ac18_forward_subagent_enabled_is_importable_symbol():
    """AC18 (gate minor / §1aa) — _forward_subagent_enabled exists as a
    named, importable, callable module-level symbol (the §1aa forcing
    function for the toggle predicate — no inline predicate inside control
    flow)."""
    from bytedigger_engine.llm_subprocess import _forward_subagent_enabled

    assert callable(_forward_subagent_enabled), (
        "§2.2/§1aa: _forward_subagent_enabled must be an importable, "
        "callable module-level symbol; got " + repr(_forward_subagent_enabled)
    )
