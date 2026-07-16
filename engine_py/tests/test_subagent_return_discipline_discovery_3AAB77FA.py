"""RED tests for agreement 3AAB77FA — subagent-return-discipline (SLICE-2: discovery phase).

Spec: SHARED/memory/Decisions/2026-06-12_3AAB77FA_discovery_return_discipline_spec.md
Parent SYSTEMATIC: FD2592D9 (chokepoint — subagent raw_response bloats in-session servicer context).

Problem: the discovery subagent returns the full doc body as raw_response, bloating the
in-session servicer context.  Fix: instruct it to Write to the pre-assigned doc_path and
return only a ≤200-token status; _write_discovery_doc reads doc_path as source of truth,
failing open to raw_response.

All 11 tests MUST fail against current production code:
  AC1  — _output_schema_block("SIMPLE", doc_path): TypeError (takes 1 positional arg today).
  AC2  — _output_schema_block("FEATURE", doc_path): TypeError (same).
  AC3  — _output_schema_block with either complexity: TypeError + old phrasing present today.
  AC4  — _build_discovery_prompt SIMPLE: doc_path absent from prompt today (no-arg schema).
  AC5  — _build_discovery_prompt FEATURE: doc_path absent from prompt today.
  AC6  — _invoke_discovery_llm: allowed_tools=["Read","Grep","Glob"] today → "Write" missing.
  AC7  — _invoke_discovery_llm: no unlink before dispatch today → pre-existing file survives.
  AC8  — _write_discovery_doc: raw_response used unconditionally → short status persisted, not file body.
  AC9  — _write_discovery_doc fail-open: raw_response used today → passes already (regression guard).
  AC10 — _write_discovery_doc: discovery_writer_return_source never emitted today.
  AC11 — _resolve_discovery_source: does not exist today → callable() assertion fails.

§1q extension (D1CF5FDF): _resolve_discovery_source and _emit_safe do NOT exist at import time.
  Access both via getattr INSIDE test bodies; never import at top level.
  _emit_safe monkeypatched via monkeypatch.setattr(disc, "_emit_safe", ..., raising=False)
  so the file COLLECTS and tests FAIL at assert/runtime, never at collection.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

# Stable, always-exist imports only — no _resolve_discovery_source / _emit_safe at top level.
import phase_1_discovery as disc  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_1_discovery import (  # noqa: E402
    FEATURE_DOC_RELPATH,
    SIMPLE_DOC_RELPATH,
)


# ─── shared helpers ────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, complexity: str = "FEATURE") -> WorkflowContext:
    """Build a minimal WorkflowContext for direct step-function calls."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "complexity": complexity},
        question="Add foo to bar",
        session_id="test-session-3AAB77FA",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(doc_path: Path, raw_response: str, complexity: str = "FEATURE") -> StepResult:
    """Build a StepResult shaped like the discovery-LLM step output."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "complexity": complexity,
            "prompt": "test prompt",
        },
        duration_ms=0,
        step_name="invoke_discovery_llm",
    )


# A realistic full discovery doc body (FEATURE).
_DISCOVERY_BODY = (
    "## Context\n"
    "Full discovery doc written to file by the subagent.\n\n"
    "## Requirements\n"
    "Implement the foo feature.\n\n"
    "## In Scope\n"
    "- The foo widget.\n\n"
    "## Existing Dispatch/Coordination Mechanisms\n"
    "none found\n\n"
    "## Out of Scope\n"
    "- Bar integration.\n\n"
    "## Open Questions\n"
    "- None.\n"
)

# Short status returned by the subagent under the new protocol.
_SHORT_STATUS = "Wrote discovery doc to /tmp/research/discovery.md. Done."


# ─── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_output_schema_block_simple_with_doc_path(tmp_path):
    """AC1: _output_schema_block("SIMPLE", doc_path) contains doc_path AND "Write"
    AND ("≤200" or "200-token") AND ("Do NOT echo the spec body" or "not echo").

    Fails today: _output_schema_block() takes 1 positional arg (complexity only)
    → TypeError when called with 2 args.
    """
    sentinel = str(tmp_path / "specs" / "build-spec.md")

    # Call inside test body — TypeError is the assert-time RED failure for current code.
    out = disc._output_schema_block("SIMPLE", sentinel)

    assert sentinel in out, (
        f"_output_schema_block('SIMPLE', doc_path) must embed the assigned doc_path "
        f"{sentinel!r} in the returned string. Got: {out[:200]!r}"
    )
    assert "Write" in out, (
        "_output_schema_block('SIMPLE', doc_path) must contain 'Write' tool instruction."
    )
    assert "≤200" in out or "200-token" in out, (
        "_output_schema_block('SIMPLE', doc_path) must contain '≤200' or '200-token' budget."
    )
    assert "Do NOT echo the spec body" in out or "not echo" in out or "NOT echo" in out, (
        "_output_schema_block('SIMPLE', doc_path) must contain a 'Do NOT echo' instruction."
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_output_schema_block_feature_with_doc_path(tmp_path):
    """AC2: _output_schema_block("FEATURE", doc_path) contains doc_path AND "Write"
    AND ("≤200" or "200-token") AND ("Do NOT echo the discovery body" or "not echo").

    Fails today: _output_schema_block() takes 1 positional arg → TypeError.
    """
    sentinel = str(tmp_path / "research" / "discovery.md")

    out = disc._output_schema_block("FEATURE", sentinel)

    assert sentinel in out, (
        f"_output_schema_block('FEATURE', doc_path) must embed the assigned doc_path "
        f"{sentinel!r}. Got: {out[:200]!r}"
    )
    assert "Write" in out, (
        "_output_schema_block('FEATURE', doc_path) must contain 'Write' tool instruction."
    )
    assert "≤200" in out or "200-token" in out, (
        "_output_schema_block('FEATURE', doc_path) must contain '≤200' or '200-token' budget."
    )
    assert "Do NOT echo the discovery body" in out or "not echo" in out or "NOT echo" in out, (
        "_output_schema_block('FEATURE', doc_path) must contain a 'Do NOT echo' instruction."
    )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_output_schema_block_landmark_and_no_old_contradiction(tmp_path):
    """AC3: both branches still begin with "OUTPUT — " AND neither contains the bare
    old contradiction "your response IS the file content".

    Fails today: _output_schema_block() takes 1 arg → TypeError (primary RED failure).
    Even if signature were patched, old phrases ARE present today.
    """
    simple_sentinel = str(tmp_path / "specs" / "build-spec.md")
    feature_sentinel = str(tmp_path / "research" / "discovery.md")

    out_simple = disc._output_schema_block("SIMPLE", simple_sentinel)
    out_feature = disc._output_schema_block("FEATURE", feature_sentinel)

    # Landmark preservation (§2.1).
    assert "OUTPUT — " in out_simple, (
        "SIMPLE branch must still begin with or contain 'OUTPUT — ' landmark."
    )
    assert "OUTPUT — " in out_feature, (
        "FEATURE branch must still begin with or contain 'OUTPUT — ' landmark."
    )

    # Old bare contradiction removed.
    assert "your response IS the file content" not in out_simple, (
        "SIMPLE branch must NOT contain old 'your response IS the file content' phrase."
    )
    assert "your response IS the file content" not in out_feature, (
        "FEATURE branch must NOT contain old 'your response IS the file content' phrase."
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_build_discovery_prompt_simple_doc_path_in_data_and_prompt(tmp_path):
    """AC4: _build_discovery_prompt (SIMPLE) returns data['doc_path'] ==
    str(scratchpad/SIMPLE_DOC_RELPATH) AND that path string appears in data['prompt'].

    Fails today: _output_schema_block() is called with no doc_path arg → prompt does NOT
    contain the assigned path → 'path in prompt' assertion fails.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, complexity="SIMPLE")

    result = disc._build_discovery_prompt(ctx, None)

    assert result.status == "ok", (
        f"_build_discovery_prompt (SIMPLE) failed: {result.error!r}"
    )
    expected_doc_path = str(scratchpad / SIMPLE_DOC_RELPATH)
    assert result.data["doc_path"] == expected_doc_path, (
        f"data['doc_path'] expected {expected_doc_path!r}, got {result.data.get('doc_path')!r}"
    )
    prompt = result.data.get("prompt", "")
    assert expected_doc_path in prompt, (
        f"The assembled SIMPLE prompt must contain the assigned doc_path {expected_doc_path!r}. "
        f"Today _output_schema_block() takes no path arg → path never embedded. "
        f"Prompt tail: {prompt[-300:]!r}"
    )


# ─── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_build_discovery_prompt_feature_doc_path_in_data_and_prompt(tmp_path):
    """AC5: _build_discovery_prompt (FEATURE) returns data['doc_path'] ==
    str(scratchpad/FEATURE_DOC_RELPATH) AND that path string appears in data['prompt'].

    Fails today: same reason as AC4 — _output_schema_block() never embeds the path.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, complexity="FEATURE")

    result = disc._build_discovery_prompt(ctx, None)

    assert result.status == "ok", (
        f"_build_discovery_prompt (FEATURE) failed: {result.error!r}"
    )
    expected_doc_path = str(scratchpad / FEATURE_DOC_RELPATH)
    assert result.data["doc_path"] == expected_doc_path, (
        f"data['doc_path'] expected {expected_doc_path!r}, got {result.data.get('doc_path')!r}"
    )
    prompt = result.data.get("prompt", "")
    assert expected_doc_path in prompt, (
        f"The assembled FEATURE prompt must contain the assigned doc_path {expected_doc_path!r}. "
        f"Today _output_schema_block() takes no path arg → path never embedded. "
        f"Prompt tail: {prompt[-300:]!r}"
    )


# ─── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_invoke_discovery_llm_passes_write_in_allowed_tools(tmp_path, monkeypatch):
    """AC6: _invoke_discovery_llm passes allowed_tools containing "Write" to
    invoke_llm_subprocess.

    Fails today: allowed_tools=["Read","Grep","Glob"] — "Write" is absent.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / FEATURE_DOC_RELPATH

    captured_kwargs: list[dict] = []

    def _fake_invoke(**kwargs):
        captured_kwargs.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": "OK status",
                "doc_path": str(doc_path),
                "complexity": "FEATURE",
                "response_bytes": 9,
                "command": ["claude", "-p"],
            },
            duration_ms=0,
            step_name="invoke_discovery_llm",
        )

    monkeypatch.setattr(disc, "invoke_llm_subprocess", _fake_invoke)

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = StepResult(
        status="ok",
        data={
            "prompt": "test prompt",
            "doc_path": str(doc_path),
            "complexity": "FEATURE",
        },
        duration_ms=0,
        step_name="build_discovery_prompt",
    )

    disc._invoke_discovery_llm(ctx, prev)

    assert len(captured_kwargs) >= 1, (
        "_invoke_discovery_llm did not call invoke_llm_subprocess at all."
    )
    actual_tools = captured_kwargs[0].get("allowed_tools", "__MISSING__")
    assert actual_tools != "__MISSING__", (
        "_invoke_discovery_llm called invoke_llm_subprocess but passed no allowed_tools kwarg."
    )
    assert "Write" in actual_tools, (
        f"_invoke_discovery_llm must pass 'Write' in allowed_tools. "
        f"Today it passes {actual_tools!r} — 'Write' is absent (§2.3)."
    )


# ─── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_invoke_discovery_llm_unlinks_doc_path_before_dispatch(tmp_path, monkeypatch):
    """AC7: _invoke_discovery_llm unlinks a pre-existing doc_path before dispatch.
    File is absent after invoke when the patched LLM does not recreate it.

    Fails today: no unlink in _invoke_discovery_llm → pre-existing file survives.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / FEATURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-create the doc_path file (simulates a stale doc from a prior run).
    doc_path.write_text("stale content from previous run", encoding="utf-8")
    assert doc_path.exists(), "pre-condition: file must exist before invoke"

    def _fake_invoke_no_write(**kwargs):
        # Deliberately does NOT write to doc_path (mirrors the unlink-then-no-write scenario).
        return StepResult(
            status="ok",
            data={
                "raw_response": "short status only",
                "doc_path": str(doc_path),
                "complexity": "FEATURE",
                "response_bytes": 17,
                "command": ["claude", "-p"],
            },
            duration_ms=0,
            step_name="invoke_discovery_llm",
        )

    monkeypatch.setattr(disc, "invoke_llm_subprocess", _fake_invoke_no_write)

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = StepResult(
        status="ok",
        data={
            "prompt": "test prompt",
            "doc_path": str(doc_path),
            "complexity": "FEATURE",
        },
        duration_ms=0,
        step_name="build_discovery_prompt",
    )

    disc._invoke_discovery_llm(ctx, prev)

    assert not doc_path.exists(), (
        f"AC7: _invoke_discovery_llm must unlink doc_path before dispatch so a "
        f"non-writing LLM stub leaves the file absent. "
        f"Today no unlink exists → stale file survives → fail-open logic cannot "
        f"distinguish 'this dispatch wrote it' from 'leftover from prior run'. "
        f"File still exists at: {doc_path}"
    )


# ─── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_write_discovery_doc_uses_file_body_over_short_raw_response(tmp_path):
    """AC8: when doc_path file exists with full doc body AND raw_response is a SHORT
    status, persisted doc_path == file body (not the status).

    Fails today: _write_discovery_doc reads raw_response unconditionally (line 365).
    The pre-written file is never consulted → canonical == short status.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / FEATURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-write the full discovery doc (the subagent wrote it via Write tool).
    doc_path.write_text(_DISCOVERY_BODY, encoding="utf-8")

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = _make_prev(doc_path, _SHORT_STATUS, complexity="FEATURE")

    result = disc._write_discovery_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_discovery_doc failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    assert _SHORT_STATUS not in persisted, (
        f"AC8: persisted doc must NOT be the short raw_response status. "
        f"Today _write_discovery_doc writes raw_response unconditionally. "
        f"Persisted head: {persisted[:100]!r}"
    )
    assert "Full discovery doc written to file by the subagent" in persisted, (
        f"AC8: persisted doc must contain the file body content. Got: {persisted[:200]!r}"
    )


# ─── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_write_discovery_doc_fallback_to_raw_response_when_file_absent(tmp_path):
    """AC9: when doc_path file is ABSENT, persisted doc_path == raw_response
    (fail-open preserved).

    Today: raw_response always used → this passes today (regression guard).
    Must keep passing post-GREEN to validate the fallback chain.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / FEATURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    # doc_path deliberately NOT written.

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = _make_prev(doc_path, _DISCOVERY_BODY, complexity="FEATURE")

    result = disc._write_discovery_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_discovery_doc failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    assert "Full discovery doc written to file by the subagent" in persisted, (
        f"AC9: fallback path must persist raw_response content. Got: {persisted[:200]!r}"
    )


# ─── AC10 ──────────────────────────────────────────────────────────────────────


def test_ac10_write_discovery_doc_emits_discovery_writer_return_source(tmp_path, monkeypatch):
    """AC10: _write_discovery_doc emits 'discovery_writer_return_source' with
    source='worker_file' (file-present) and source='raw_response_fallback' (file-absent).

    Fails today: _emit_safe("discovery_writer_return_source", ...) does not exist in
    _write_discovery_doc — no such event is ever emitted.

    Uses monkeypatch.setattr(disc, "_emit_safe", capture, raising=False) so the file
    COLLECTS even though _emit_safe doesn't exist yet (§1q / D1CF5FDF).
    """
    # ── Part A: file present → source='worker_file' ──
    scratchpad_a = tmp_path / "scratch_a"
    scratchpad_a.mkdir(parents=True, exist_ok=True)
    doc_path_a = scratchpad_a / FEATURE_DOC_RELPATH
    doc_path_a.parent.mkdir(parents=True, exist_ok=True)
    doc_path_a.write_text(_DISCOVERY_BODY, encoding="utf-8")

    captured_a: list[tuple[str, dict]] = []

    def _capture_a(event_type, payload):
        captured_a.append((event_type, payload))

    monkeypatch.setattr(disc, "_emit_safe", _capture_a, raising=False)

    ctx_a = _make_ctx(scratchpad_a, complexity="FEATURE")
    prev_a = _make_prev(doc_path_a, _SHORT_STATUS, complexity="FEATURE")
    result_a = disc._write_discovery_doc(ctx_a, prev_a)

    assert result_a.status == "ok", (
        f"_write_discovery_doc (file present) failed: {result_a.error!r}"
    )
    source_events_a = [
        (et, p) for et, p in captured_a if et == "discovery_writer_return_source"
    ]
    assert len(source_events_a) == 1, (
        f"AC10a: expected exactly 1 'discovery_writer_return_source' event (file present), "
        f"got {len(source_events_a)}. All captured: {[e[0] for e in captured_a]}"
    )
    assert source_events_a[0][1].get("source") == "worker_file", (
        f"AC10a: source must be 'worker_file' when doc_path file exists; "
        f"got {source_events_a[0][1]!r}"
    )

    # ── Part B: file absent → source='raw_response_fallback' ──
    scratchpad_b = tmp_path / "scratch_b"
    scratchpad_b.mkdir(parents=True, exist_ok=True)
    doc_path_b = scratchpad_b / FEATURE_DOC_RELPATH
    doc_path_b.parent.mkdir(parents=True, exist_ok=True)
    # doc_path_b deliberately NOT written.

    captured_b: list[tuple[str, dict]] = []

    def _capture_b(event_type, payload):
        captured_b.append((event_type, payload))

    monkeypatch.setattr(disc, "_emit_safe", _capture_b, raising=False)

    ctx_b = _make_ctx(scratchpad_b, complexity="FEATURE")
    prev_b = _make_prev(doc_path_b, _DISCOVERY_BODY, complexity="FEATURE")
    result_b = disc._write_discovery_doc(ctx_b, prev_b)

    assert result_b.status == "ok", (
        f"_write_discovery_doc (file absent) failed: {result_b.error!r}"
    )
    source_events_b = [
        (et, p) for et, p in captured_b if et == "discovery_writer_return_source"
    ]
    assert len(source_events_b) == 1, (
        f"AC10b: expected exactly 1 'discovery_writer_return_source' event (file absent), "
        f"got {len(source_events_b)}. All captured: {[e[0] for e in captured_b]}"
    )
    assert source_events_b[0][1].get("source") == "raw_response_fallback", (
        f"AC10b: source must be 'raw_response_fallback' when doc_path absent; "
        f"got {source_events_b[0][1]!r}"
    )


# ─── AC11 ──────────────────────────────────────────────────────────────────────


def test_ac11_resolve_discovery_source_helper_exists_and_returns_fallback_for_absent_path(tmp_path):
    """AC11: _resolve_discovery_source is a named callable helper (§1aa forcing-fn).
    Returns (raw, 'raw_response_fallback') for an absent doc_path.

    Fails today: _resolve_discovery_source does not exist in phase_1_discovery.
    callable() assertion fails (getattr returns None).
    """
    # Access via getattr INSIDE test body (§1q / D1CF5FDF: never import at top level).
    helper = getattr(disc, "_resolve_discovery_source", None)

    assert callable(helper), (
        "phase_1_discovery._resolve_discovery_source does not exist or is not callable. "
        "3AAB77FA §1aa requires this named helper to be added."
    )

    # Only reached post-GREEN when helper exists.
    absent_path = tmp_path / "nonexistent" / "research" / "discovery.md"
    result = helper(absent_path, "fallback body")

    assert isinstance(result, tuple) and len(result) == 2, (
        f"_resolve_discovery_source must return a 2-tuple (raw, source); got {result!r}"
    )
    raw, source = result
    assert source == "raw_response_fallback", (
        f"_resolve_discovery_source(<absent-path>, ...) second element must be "
        f"'raw_response_fallback'; got {source!r}"
    )
    assert raw == "fallback body", (
        f"_resolve_discovery_source(<absent-path>, 'fallback body') first element must be "
        f"'fallback body'; got {raw!r}"
    )
