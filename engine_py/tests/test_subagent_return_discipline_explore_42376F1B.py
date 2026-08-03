"""RED tests for agreement 42376F1B — subagent-return-discipline (SLICE-3: explore phase).

Spec: SHARED/memory/Decisions/2026-06-12_42376F1B_explore_return_discipline_spec.md
Parent SYSTEMATIC: FD2592D9 (chokepoint — subagent raw_response bloats in-session servicer context).

Problem: the explore subagent returns the full exploration doc body as raw_response, bloating
the in-session servicer context.  Fix: instruct it to Write to the pre-assigned doc_path and
return only a ≤200-token status; _write_explore_doc reads doc_path as source of truth,
failing open to raw_response.  STATUS marker MUST be parsed from raw_response (text channel),
NOT from the file body.

All 12 tests MUST fail against current production code:
  AC1  — _explore_output_schema("FEATURE", doc_path): TypeError (takes 1 positional arg today).
  AC2  — _explore_output_schema("FEATURE", doc_path): TypeError + old phrasing present today.
  AC3  — _explore_output_schema with doc_path: TypeError; also old status-directive wording.
  AC4  — _build_explore_prompt: doc_path absent from prompt today (no-arg schema → path never embedded).
  AC5  — _invoke_explore_llm: allowed_tools=["Read","Grep","Glob","WebSearch","WebFetch"] today → "Write" absent.
  AC6  — _invoke_explore_llm: no unlink before dispatch today → pre-existing file survives.
  AC7  — _write_explore_doc: raw_response used unconditionally → short status written to file, not file body.
  AC8  — _write_explore_doc fail-open: raw_response always used → passes today (regression guard).
  AC9  — Marker-channel split: marker parsed from body today → DONE returned when it should be BLOCKED.
  AC10 — explore_writer_return_source never emitted today.
  AC11 — _resolve_explore_source: does not exist today → callable() assertion fails.
  AC12 — Passthrough preserved: passes today (regression guard; verifies unlink/resolver additions
         sit AFTER the passthrough guard after GREEN).

§1q extension (D1CF5FDF): _resolve_explore_source and the new _emit_safe overload do NOT exist
  at import time.  Access via getattr INSIDE test bodies; never import at top level.
  _emit_safe monkeypatched via monkeypatch.setattr(e, "_emit_safe", ..., raising=False)
  so the file COLLECTS and tests FAIL at assert/runtime, never at collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

# Stable, always-exist imports only — no _resolve_explore_source at top level.
from bytedigger_engine.workflows import phase_2_explore as e  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_2_explore import (  # noqa: E402
    EXPLORE_DOC_RELPATH,
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
        session_id="test-session-42376F1B",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(doc_path: Path, raw_response: str, complexity: str = "FEATURE") -> StepResult:
    """Build a StepResult shaped like the explore-LLM step output."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "complexity": complexity,
            "prompt": "test prompt",
        },
        duration_ms=0,
        step_name="invoke_explore_llm",
    )


# A realistic full exploration doc body (no STATUS line inside — it goes in text reply).
_EXPLORE_BODY = (
    "# Codebase Exploration\n\n"
    "## Relevant Files\n"
    "- workflows/phase_2_explore.py:182 — schema builder for the explore prompt\n\n"
    "## Patterns\n"
    "- Uses passthrough_if_skipped pattern at each step boundary.\n\n"
    "## Dependencies & Integration\n"
    "- Depends on skip_logic.passthrough_if_skipped.\n\n"
    "## Integration Points\n"
    "- _write_explore_doc at line 346 is the write step.\n\n"
    "## Existing Dispatch/Coordination Mechanisms\n"
    "none found\n\n"
    "## Summary For Architects\n"
    "- Three-step explore workflow with skip support.\n\n"
    "## Out of Scope\n"
    "- Security perspective (FEATURE complexity).\n\n"
    "## Open Questions\n"
    "- None.\n"
)

# Short status returned by the subagent under the new protocol (text reply only).
_SHORT_STATUS_DONE = "Wrote exploration doc to research/exploration.md.\nSTATUS: DONE"


# ─── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_explore_output_schema_with_doc_path_contains_required_substrings(tmp_path):
    """AC1: _explore_output_schema("FEATURE", doc_path) contains the doc_path string
    AND "Write" AND ("≤200" or "200-token") AND ("not echo"/"NOT echo"/"Do NOT echo").

    Fails today: _explore_output_schema() takes 1 positional arg (complexity only)
    → TypeError when called with 2 args.
    """
    sentinel = str(tmp_path / "research" / "exploration.md")

    # Call inside test body — TypeError is the assert-time RED failure for current code.
    out = e._explore_output_schema("FEATURE", sentinel)

    assert sentinel in out, (
        f"_explore_output_schema('FEATURE', doc_path) must embed the assigned doc_path "
        f"{sentinel!r} in the returned string. Got: {out[:200]!r}"
    )
    assert "Write" in out, (
        "_explore_output_schema('FEATURE', doc_path) must contain 'Write' tool instruction."
    )
    assert "≤200" in out or "200-token" in out, (
        "_explore_output_schema('FEATURE', doc_path) must contain '≤200' or '200-token' budget."
    )
    assert "Do NOT echo" in out or "NOT echo" in out or "not echo" in out, (
        "_explore_output_schema('FEATURE', doc_path) must contain a 'Do NOT echo' instruction."
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_explore_output_schema_begins_with_output_landmark_and_no_old_contradiction(tmp_path):
    """AC2: _explore_output_schema output begins with literal "OUTPUT — " AND does NOT
    contain "your response IS the file content".

    Fails today: _explore_output_schema() takes 1 arg → TypeError (primary RED failure).
    Even if the signature were patched, old phrase is present today.
    """
    sentinel = str(tmp_path / "research" / "exploration.md")

    out = e._explore_output_schema("FEATURE", sentinel)

    assert out.startswith("OUTPUT — "), (
        f"_explore_output_schema must start with 'OUTPUT — ' landmark. "
        f"Got: {out[:60]!r}"
    )
    assert "your response IS the file content" not in out, (
        "Old bare phrase 'your response IS the file content' must be removed "
        "in _explore_output_schema (§2.1 reword)."
    )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_explore_output_schema_preserves_status_control_directive(tmp_path):
    """AC3: _explore_output_schema output preserves the STATUS control directive on the TEXT
    channel: contains "End your text response with EXACTLY ONE status line" AND "STATUS: DONE"
    AND "STATUS: BLOCKED".

    Fails today: _explore_output_schema() takes 1 arg → TypeError (primary RED failure).
    Even if called with 1 arg, the old wording is "End your report with ..." not
    "End your text response with ...".
    """
    sentinel = str(tmp_path / "research" / "exploration.md")

    out = e._explore_output_schema("FEATURE", sentinel)

    assert "End your text response with EXACTLY ONE status line" in out, (
        "STATUS control directive must say 'End your text response with EXACTLY ONE status line' "
        "(not 'End your report with ...') so the agent reports STATUS on the text channel."
    )
    assert "STATUS: DONE" in out, (
        "_explore_output_schema must enumerate STATUS: DONE in the control directive."
    )
    assert "STATUS: BLOCKED" in out, (
        "_explore_output_schema must enumerate STATUS: BLOCKED in the control directive."
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_build_explore_prompt_doc_path_in_data_and_prompt(tmp_path):
    """AC4: _build_explore_prompt (non-skip ctx) returns data['doc_path'] ==
    str(scratchpad/EXPLORE_DOC_RELPATH) AND that path appears in data['prompt'].

    Fails today: _explore_output_schema() is called with 1 arg → prompt does NOT
    contain the assigned path (no-arg schema never embeds it).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, complexity="FEATURE")

    # _build_explore_prompt calls _check_decision_doc_skip internally via prev=None.
    # We pass a non-skip prev directly (data["skipped"]=False sentinel).
    prev_sentinel = StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )
    result = e._build_explore_prompt(ctx, prev_sentinel)

    assert result.status == "ok", (
        f"_build_explore_prompt failed: {result.error!r}"
    )
    expected_doc_path = str(scratchpad / EXPLORE_DOC_RELPATH)
    assert result.data["doc_path"] == expected_doc_path, (
        f"data['doc_path'] expected {expected_doc_path!r}, got {result.data.get('doc_path')!r}"
    )
    prompt = result.data.get("prompt", "")
    assert expected_doc_path in prompt, (
        f"The assembled prompt must contain the assigned doc_path {expected_doc_path!r}. "
        f"Today _explore_output_schema() takes no path arg → path never embedded. "
        f"Prompt tail: {prompt[-300:]!r}"
    )


# ─── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_invoke_explore_llm_passes_write_in_allowed_tools(tmp_path, monkeypatch):
    """AC5: _invoke_explore_llm passes allowed_tools containing "Write" to
    invoke_llm_subprocess.

    Fails today: allowed_tools=["Read","Grep","Glob","WebSearch","WebFetch"] — "Write" is absent.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH

    captured_kwargs: list[dict] = []

    def _fake_invoke(**kwargs):
        captured_kwargs.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": _SHORT_STATUS_DONE,
                "doc_path": str(doc_path),
                "complexity": "FEATURE",
                "response_bytes": len(_SHORT_STATUS_DONE),
                "command": ["claude", "-p"],
            },
            duration_ms=0,
            step_name="invoke_explore_llm",
        )

    monkeypatch.setattr(e, "invoke_llm_subprocess", _fake_invoke)

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = StepResult(
        status="ok",
        data={
            "prompt": "test prompt",
            "doc_path": str(doc_path),
            "complexity": "FEATURE",
            "prompt_bytes": 100,
        },
        duration_ms=0,
        step_name="build_explore_prompt",
    )

    e._invoke_explore_llm(ctx, prev)

    assert len(captured_kwargs) >= 1, (
        "_invoke_explore_llm did not call invoke_llm_subprocess at all."
    )
    actual_tools = captured_kwargs[0].get("allowed_tools", "__MISSING__")
    assert actual_tools != "__MISSING__", (
        "_invoke_explore_llm called invoke_llm_subprocess but passed no allowed_tools kwarg."
    )
    assert "Write" in actual_tools, (
        f"_invoke_explore_llm must pass 'Write' in allowed_tools. "
        f"Today it passes {actual_tools!r} — 'Write' is absent (§2.3)."
    )


# ─── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_invoke_explore_llm_unlinks_doc_path_before_dispatch(tmp_path, monkeypatch):
    """AC6: _invoke_explore_llm unlinks a pre-existing doc_path before dispatch.
    File is absent after invoke when the patched LLM does not recreate it.

    Fails today: no unlink in _invoke_explore_llm → pre-existing file survives.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH
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
            step_name="invoke_explore_llm",
        )

    monkeypatch.setattr(e, "invoke_llm_subprocess", _fake_invoke_no_write)

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = StepResult(
        status="ok",
        data={
            "prompt": "test prompt",
            "doc_path": str(doc_path),
            "complexity": "FEATURE",
            "prompt_bytes": 100,
        },
        duration_ms=0,
        step_name="build_explore_prompt",
    )

    e._invoke_explore_llm(ctx, prev)

    assert not doc_path.exists(), (
        f"AC6: _invoke_explore_llm must unlink doc_path before dispatch so a "
        f"non-writing LLM stub leaves the file absent. "
        f"Today no unlink exists → stale file survives → fail-open logic cannot "
        f"distinguish 'this dispatch wrote it' from 'leftover from prior run'. "
        f"File still exists at: {doc_path}"
    )


# ─── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_write_explore_doc_uses_file_body_over_short_raw_response(tmp_path):
    """AC7: when doc_path file exists with a full doc body (NO STATUS line) AND
    raw_response is a SHORT status ending "STATUS: DONE", then:
      - persisted doc_path == FILE body (not the short status text)
      - result status=="ok"
      - result data["marker"]=="DONE"

    Fails today: _write_explore_doc reads raw_response unconditionally (line 357).
    The pre-written file is never consulted → persisted == short status text, not the file body.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-write the full exploration doc (the subagent wrote it via Write tool).
    # Crucially: NO STATUS line inside the file body.
    doc_path.write_text(_EXPLORE_BODY, encoding="utf-8")

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = _make_prev(doc_path, _SHORT_STATUS_DONE, complexity="FEATURE")

    result = e._write_explore_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_explore_doc failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    # Persisted file must be the file body, NOT the short status text.
    assert _SHORT_STATUS_DONE not in persisted, (
        f"AC7: persisted doc must NOT be the short raw_response status. "
        f"Today _write_explore_doc writes raw_response unconditionally. "
        f"Persisted head: {persisted[:100]!r}"
    )
    assert "# Codebase Exploration" in persisted, (
        f"AC7: persisted doc must contain the file body content. Got: {persisted[:200]!r}"
    )

    # Marker must be DONE (parsed from raw_response text channel).
    assert result.data is not None and result.data.get("marker") == "DONE", (
        f"AC7: result data['marker'] must be 'DONE' (parsed from raw_response text channel). "
        f"Got: {result.data!r}"
    )


# ─── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_write_explore_doc_fallback_to_raw_response_when_file_absent(tmp_path):
    """AC8: when doc_path file is ABSENT AND raw_response = full body + "STATUS: DONE",
    persisted doc_path == raw_response (fail-open) AND marker=="DONE".

    Today: raw_response always used → persisted == raw_response already → this
    passes today (regression guard on the fallback chain; must keep passing post-GREEN).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    # doc_path deliberately NOT written.

    # Full body with STATUS appended (as raw_response under the fallback path).
    full_raw = _EXPLORE_BODY + "\nSTATUS: DONE\n"

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = _make_prev(doc_path, full_raw, complexity="FEATURE")

    result = e._write_explore_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_explore_doc (file absent) failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    assert "# Codebase Exploration" in persisted, (
        f"AC8: fallback path must persist raw_response content (contains '# Codebase Exploration'). "
        f"Got: {persisted[:200]!r}"
    )
    assert result.data is not None and result.data.get("marker") == "DONE", (
        f"AC8: result data['marker'] must be 'DONE'. Got: {result.data!r}"
    )


# ─── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_marker_parsed_from_raw_response_not_file_body(tmp_path):
    """AC9 — CRITICAL marker-channel split: doc_path file present whose body
    contains "STATUS: DONE", BUT raw_response text contains "STATUS: BLOCKED"
    → result error_code=="E_EXPLORE_BLOCKED" (marker from raw_response, NOT file body).

    Fails today: _write_explore_doc calls _parse_status_marker(raw) where raw==raw_response
    (line 362), BUT it also writes raw_response unconditionally to the file.  After GREEN,
    the file body is the resolved content (no STATUS line) and the STATUS comes only from
    raw_response.  In current prod, raw==raw_response=="STATUS: BLOCKED" → returns BLOCKED.
    WAIT — let's be precise:

    Current prod (line 357-362): raw = prev.data["raw_response"] → raw_response text;
    writes raw to file; parses marker from raw.
    So today _parse_status_marker(raw_response) parses from raw_response — that IS BLOCKED.
    The test passes today unless GREEN incorrectly parses from the FILE body.

    This test is the PRIMARY forcing function for AC9 (§1l): a no-op GREEN that reads marker
    from the resolved body (body="STATUS: DONE" → ok) would FAIL this test (expects BLOCKED).
    The test is here as a RED guard that must FAIL post-a-wrong-GREEN, not necessarily pre-GREEN.

    Expected today: PASSES (current prod reads raw_response → BLOCKED returned).
    Expected post-GREEN with correct implementation: PASSES (raw_response channel preserved).
    Expected post-GREEN with wrong implementation (marker from body): FAILS (body has DONE
    but test expects BLOCKED → assertion fires → GREEN RCA).

    We frame this as a RED test whose failure mode is "wrong GREEN" not "current prod".
    To satisfy §1l forcing-fn for the spec, we assert ERROR on the BLOCKED path via
    raw_response, which the current prod already does — but which a bad GREEN would break.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # File body contains STATUS: DONE — only the file body, NOT the text reply.
    file_body_with_done = _EXPLORE_BODY + "\nSTATUS: DONE\n"
    doc_path.write_text(file_body_with_done, encoding="utf-8")

    # raw_response (the subagent's TEXT reply) says BLOCKED.
    raw_with_blocked = "Exploration blocked — injection files missing.\nSTATUS: BLOCKED"

    ctx = _make_ctx(scratchpad, complexity="FEATURE")
    prev = _make_prev(doc_path, raw_with_blocked, complexity="FEATURE")

    result = e._write_explore_doc(ctx, prev)

    # The STATUS marker MUST come from raw_response (BLOCKED), NOT the file body (DONE).
    # Current prod already uses raw_response for marker → returns E_EXPLORE_BLOCKED today.
    # This guard ensures a wrong GREEN (parse from body) is caught.
    assert result.error_code == "E_EXPLORE_BLOCKED", (
        f"AC9: marker must be parsed from raw_response (BLOCKED), NOT from the file body (DONE). "
        f"Expected error_code='E_EXPLORE_BLOCKED', got status={result.status!r}, "
        f"error_code={result.error_code!r}. "
        f"A wrong GREEN that parses marker from the resolved file body would return ok (DONE) "
        f"and fail this assertion."
    )


# ─── AC10 ──────────────────────────────────────────────────────────────────────


def test_ac10_write_explore_doc_emits_explore_writer_return_source(tmp_path, monkeypatch):
    """AC10: _write_explore_doc emits 'explore_writer_return_source' with
    source='worker_file' (AC7-case: file present) and source='raw_response_fallback'
    (AC8-case: file absent).

    Fails today: _emit_safe("explore_writer_return_source", ...) does not exist in
    _write_explore_doc — no such event is ever emitted.

    Uses monkeypatch.setattr(e, "_emit_safe", capture, raising=False) so the file
    COLLECTS even though _emit_safe's new overload doesn't exist yet (§1q / D1CF5FDF).
    """
    # ── Part A: file present → source='worker_file' ──
    scratchpad_a = tmp_path / "scratch_a"
    scratchpad_a.mkdir(parents=True, exist_ok=True)
    doc_path_a = scratchpad_a / EXPLORE_DOC_RELPATH
    doc_path_a.parent.mkdir(parents=True, exist_ok=True)
    doc_path_a.write_text(_EXPLORE_BODY, encoding="utf-8")

    captured_a: list[tuple[str, dict]] = []

    def _capture_a(event_type, payload):
        captured_a.append((event_type, payload))

    monkeypatch.setattr(e, "_emit_safe", _capture_a, raising=False)

    ctx_a = _make_ctx(scratchpad_a, complexity="FEATURE")
    prev_a = _make_prev(doc_path_a, _SHORT_STATUS_DONE, complexity="FEATURE")
    result_a = e._write_explore_doc(ctx_a, prev_a)

    assert result_a.status == "ok", (
        f"_write_explore_doc (file present) failed: {result_a.error!r}"
    )
    source_events_a = [
        (et, p) for et, p in captured_a if et == "explore_writer_return_source"
    ]
    assert len(source_events_a) == 1, (
        f"AC10a: expected exactly 1 'explore_writer_return_source' event (file present), "
        f"got {len(source_events_a)}. All captured events: {[ev[0] for ev in captured_a]}"
    )
    assert source_events_a[0][1].get("source") == "worker_file", (
        f"AC10a: source must be 'worker_file' when doc_path file exists; "
        f"got {source_events_a[0][1]!r}"
    )

    # ── Part B: file absent → source='raw_response_fallback' ──
    scratchpad_b = tmp_path / "scratch_b"
    scratchpad_b.mkdir(parents=True, exist_ok=True)
    doc_path_b = scratchpad_b / EXPLORE_DOC_RELPATH
    doc_path_b.parent.mkdir(parents=True, exist_ok=True)
    # doc_path_b deliberately NOT written.

    captured_b: list[tuple[str, dict]] = []

    def _capture_b(event_type, payload):
        captured_b.append((event_type, payload))

    monkeypatch.setattr(e, "_emit_safe", _capture_b, raising=False)

    full_raw = _EXPLORE_BODY + "\nSTATUS: DONE\n"
    ctx_b = _make_ctx(scratchpad_b, complexity="FEATURE")
    prev_b = _make_prev(doc_path_b, full_raw, complexity="FEATURE")
    result_b = e._write_explore_doc(ctx_b, prev_b)

    assert result_b.status == "ok", (
        f"_write_explore_doc (file absent) failed: {result_b.error!r}"
    )
    source_events_b = [
        (et, p) for et, p in captured_b if et == "explore_writer_return_source"
    ]
    assert len(source_events_b) == 1, (
        f"AC10b: expected exactly 1 'explore_writer_return_source' event (file absent), "
        f"got {len(source_events_b)}. All captured events: {[ev[0] for ev in captured_b]}"
    )
    assert source_events_b[0][1].get("source") == "raw_response_fallback", (
        f"AC10b: source must be 'raw_response_fallback' when doc_path absent; "
        f"got {source_events_b[0][1]!r}"
    )


# ─── AC11 ──────────────────────────────────────────────────────────────────────


def test_ac11_resolve_explore_source_helper_exists_and_returns_fallback_for_absent_path(tmp_path):
    """AC11: _resolve_explore_source is a named callable helper (§1aa forcing-fn).
    Returns (raw, 'raw_response_fallback') for an absent path.

    Fails today: _resolve_explore_source does not exist in phase_2_explore.
    callable() assertion fails (getattr returns None).
    """
    # Access via getattr INSIDE test body (§1q / D1CF5FDF: never import at top level).
    helper = getattr(e, "_resolve_explore_source", None)

    assert callable(helper), (
        "phase_2_explore._resolve_explore_source does not exist or is not callable. "
        "42376F1B §1aa requires this named helper to be added."
    )

    # Only reached post-GREEN when helper exists.
    absent_path = tmp_path / "nonexistent" / "research" / "exploration.md"
    result = helper(str(absent_path), "fallback body")

    assert isinstance(result, tuple) and len(result) == 2, (
        f"_resolve_explore_source must return a 2-tuple (body, source); got {result!r}"
    )
    body, source = result
    assert source == "raw_response_fallback", (
        f"_resolve_explore_source(<absent-path>, ...) second element must be "
        f"'raw_response_fallback'; got {source!r}"
    )
    assert body == "fallback body", (
        f"_resolve_explore_source(<absent-path>, 'fallback body') first element must be "
        f"'fallback body'; got {body!r}"
    )


# ─── AC12 ──────────────────────────────────────────────────────────────────────


def test_ac12_passthrough_preserved_for_write_and_invoke_steps(tmp_path, monkeypatch):
    """AC12: given a skip/passthrough prev, _write_explore_doc AND _invoke_explore_llm
    return the passthrough unchanged — no unlink, no resolver call, no crash.

    Today: passthrough guard is BEFORE the unlink/resolver additions (which don't exist
    yet). The passthrough guard exists in both steps → this passes today (regression guard).
    Must keep passing post-GREEN to verify the new unlink/resolver additions sit AFTER
    the passthrough guard.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-create the file to verify it is NOT unlinked during passthrough.
    doc_path.write_text("existing content that must survive passthrough", encoding="utf-8")

    # Build the canonical skip StepResult (matches make_skip_result shape in skip_logic.py).
    skip_prev = StepResult(
        status="ok",
        data={
            "skipped": True,
            "reason": "decision_doc present + FEATURE complexity",
            "decision_doc": "/some/decision.md",
        },
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )

    ctx = _make_ctx(scratchpad, complexity="FEATURE")

    # ── _write_explore_doc passthrough ──
    # Patch _emit_safe (raising=False) to prevent AttributeError on unexpected call.
    captured_write: list[tuple[str, dict]] = []
    monkeypatch.setattr(e, "_emit_safe", lambda et, p: captured_write.append((et, p)), raising=False)

    write_result = e._write_explore_doc(ctx, skip_prev)

    assert write_result is not None, "_write_explore_doc must not return None for skip prev"
    assert write_result.data is not None and write_result.data.get("skipped") is True, (
        f"AC12: _write_explore_doc must return passthrough (skipped=True) for skip prev. "
        f"Got: {write_result!r}"
    )
    # No explorer_writer_return_source event should have been emitted.
    explore_source_events = [et for et, _ in captured_write if et == "explore_writer_return_source"]
    assert len(explore_source_events) == 0, (
        f"AC12: _write_explore_doc must NOT emit 'explore_writer_return_source' on passthrough. "
        f"Got: {explore_source_events}"
    )
    # File must NOT have been unlinked.
    assert doc_path.exists(), (
        f"AC12: _write_explore_doc must NOT unlink doc_path during passthrough. "
        f"File was unexpectedly removed."
    )

    # ── _invoke_explore_llm passthrough ──
    # Patch invoke_llm_subprocess to detect if it was called.
    invoke_called: list[bool] = []
    monkeypatch.setattr(e, "invoke_llm_subprocess", lambda **kw: invoke_called.append(True) or StepResult(
        status="ok", data={}, duration_ms=0, step_name="mock"
    ))

    invoke_result = e._invoke_explore_llm(ctx, skip_prev)

    assert invoke_result is not None, "_invoke_explore_llm must not return None for skip prev"
    assert invoke_result.data is not None and invoke_result.data.get("skipped") is True, (
        f"AC12: _invoke_explore_llm must return passthrough (skipped=True) for skip prev. "
        f"Got: {invoke_result!r}"
    )
    assert len(invoke_called) == 0, (
        f"AC12: _invoke_explore_llm must NOT call invoke_llm_subprocess during passthrough. "
        f"invoke_llm_subprocess was called {len(invoke_called)} time(s)."
    )
    # File must still NOT have been unlinked (invoking on passthrough must not side-effect).
    assert doc_path.exists(), (
        f"AC12: _invoke_explore_llm must NOT unlink doc_path during passthrough. "
        f"File was unexpectedly removed."
    )
