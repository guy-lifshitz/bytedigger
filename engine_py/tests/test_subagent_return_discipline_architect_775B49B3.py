"""RED tests for agreement 775B49B3 — subagent-return-discipline (SLICE-5: architect phase).

Spec: SHARED/memory/Decisions/2026-06-12_775B49B3_architect_return_discipline_spec.md
Parent SYSTEMATIC: FD2592D9 (chokepoint — subagent raw_response bloats in-session servicer context).

Problem: the architect subagent returns the full architecture doc body as raw_response;
_write_architecture_doc writes that text to doc_path anyway.  Fix: instruct it to Write
to the pre-assigned doc_path and return only a ≤200-token status; _write_architecture_doc
reads doc_path as source of truth, failing open to raw_response.

Architect is MARKERLESS — NO STATUS-marker control channel; no _parse_status_marker.
The output schema explicitly forbids markers; result is ok on a successful write.
No marker-channel-split AC (unlike clarify slice-4).

All 12 tests MUST fail against current (unmodified) production code:
  AC1  — _output_schema_block(doc_path): TypeError (takes 0 positional args today).
  AC2  — _output_schema_block(doc_path): TypeError + old "OUTPUT — your response IS the
          file content of architecture" phrasing today.
  AC3  — _output_schema_block(doc_path): TypeError; also missing markerless-contract tokens
          (though old code already has them — TypeError is primary RED failure; AC3 also
          verifies the new "No status markers" wording survives the reword).
  AC4  — _build_architect_prompt: doc_path absent from prompt today (no-arg schema →
          path never embedded in prompt).
  AC5  — _invoke_architect_llm: allowed_tools=["Read","Grep","Glob"] today → "Write" absent.
  AC6  — _invoke_architect_llm: no unlink before dispatch today → pre-existing file survives.
  AC7  — _write_architecture_doc: raw_response used unconditionally → short confirmation
          written, not the full file body.
  AC8  — _write_architecture_doc fail-open: raw_response always used → passes today
          (regression guard).
  AC9  — architect_writer_return_source never emitted today (no _emit_safe, no telemetry).
  AC10 — _resolve_architect_source: does not exist today → callable() assertion fails.
  AC11 — Passthrough preserved: passes today (regression guard; verifies unlink/resolver
          additions sit AFTER passthrough guard after GREEN).
  AC12 — ROLE internal-consistency: prompt still contains "Your response IS the architecture
          file itself" today (old wording) → assertion "does NOT contain" fails.

§1q extension (D1CF5FDF): _resolve_architect_source does NOT exist at import time.
  Access via getattr INSIDE test bodies; never import at top level.
  _emit_safe monkeypatched via monkeypatch.setattr(a, "_emit_safe", ..., raising=False)
  so the file COLLECTS and tests FAIL at assert/runtime, never at collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

# Stable, always-exist imports only — no _resolve_architect_source at top level.
import phase_4_architect as a  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_4_architect import (  # noqa: E402
    ARCHITECTURE_DOC_RELPATH,
)


# ─── shared helpers ────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext for direct step-function calls."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session-775B49B3",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_invoke_prev(doc_path: Path, security_classification: str = "LOW") -> StepResult:
    """Build a StepResult shaped like the build_architect_prompt step output."""
    return StepResult(
        status="ok",
        data={
            "prompt": "test architect prompt",
            "doc_path": str(doc_path),
            "security_classification": security_classification,
            "research_file_count": 0,
            "prompt_bytes": 100,
        },
        duration_ms=0,
        step_name="build_architect_prompt",
    )


def _make_write_prev(doc_path: Path, raw_response: str) -> StepResult:
    """Build a StepResult shaped like the invoke_architect_llm step output."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "security_classification": "LOW",
        },
        duration_ms=0,
        step_name="invoke_architect_llm",
    )


# A realistic full architecture doc body.
_ARCH_BODY = (
    "## Context\n"
    "Adding a new caching layer to the data pipeline. Research shows Redis is the\n"
    "standard choice. This approach minimises latency at the read path.\n\n"
    "## Approach\n"
    "Wrap existing DataService with a CachingDataService decorator.\n\n"
    "## Trade-offs\n"
    "Cache invalidation complexity vs latency improvement.\n\n"
    "## Files\n"
    "src/cache/caching_data_service.py\n"
    "src/cache/__init__.py\n\n"
    "## Data Model\n"
    "n/a\n\n"
    "## Component Boundaries\n"
    "CachingDataService wraps DataService (research/data-model.md).\n\n"
    "## Implementation Order\n"
    "1. Create caching_data_service.py\n"
    "2. Wire into application factory\n\n"
    "## Out of Scope\n"
    "- Cache clustering\n"
    "- Cache warming strategies\n\n"
    "## Open Questions\n"
    "none\n"
)

# Short confirmation returned by the subagent under the new protocol (text reply only).
_SHORT_CONFIRMATION = "Wrote architecture to architecture/architecture.md. Covers CachingDataService approach."


# ─── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_output_schema_block_with_doc_path_contains_required_substrings(tmp_path):
    """AC1: _output_schema_block(doc_path) contains the doc_path string
    AND "Write" AND ("≤200" or "200-token") AND "Do NOT echo the architecture body".

    Fails today: _output_schema_block() takes 0 positional args
    → TypeError when called with 1 arg.
    """
    sentinel = str(tmp_path / "architecture" / "architecture.md")

    # Call inside test body — TypeError is the assert-time RED failure for current code.
    out = a._output_schema_block(sentinel)

    assert sentinel in out, (
        f"_output_schema_block(doc_path) must embed the assigned doc_path "
        f"{sentinel!r} in the returned string. Got: {out[:200]!r}"
    )
    assert "Write" in out, (
        "_output_schema_block(doc_path) must contain 'Write' tool instruction."
    )
    assert "≤200" in out or "200-token" in out, (
        "_output_schema_block(doc_path) must contain '≤200' or '200-token' budget."
    )
    norm = " ".join(out.split())
    assert "Do NOT echo the architecture body" in norm, (
        "_output_schema_block(doc_path) must contain 'Do NOT echo the architecture body' instruction."
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_output_schema_block_begins_with_output_landmark_no_old_contradiction(tmp_path):
    """AC2: _output_schema_block output begins with literal "OUTPUT — " AND does NOT
    contain "your response IS the file content of architecture".

    Fails today: _output_schema_block() takes 0 args → TypeError (primary RED failure).
    Even if signature were patched, old phrase "your response IS the file content of
    architecture/architecture.md" is present today (line 122).
    """
    sentinel = str(tmp_path / "architecture" / "architecture.md")

    out = a._output_schema_block(sentinel)

    assert out.startswith("OUTPUT — "), (
        f"_output_schema_block must start with 'OUTPUT — ' landmark. "
        f"Got: {out[:60]!r}"
    )
    assert "your response IS the file content of architecture" not in out, (
        "Old phrase 'your response IS the file content of architecture' must be removed "
        "from _output_schema_block (§2.1 reword). It contradicts the Write-to-file protocol."
    )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_output_schema_block_preserves_markerless_contract(tmp_path):
    """AC3: _output_schema_block preserves architect's markerless contract:
    still contains "No status markers" AND "## Context" AND
    "Surface-specific for ARCHITECT".

    Fails today: _output_schema_block() takes 0 args → TypeError (primary RED failure).
    The markerless contract tokens exist in current code (the reword must not destroy them).
    This test is the sentinel that the §2.1 reword keeps the required content intact.
    """
    sentinel = str(tmp_path / "architecture" / "architecture.md")

    out = a._output_schema_block(sentinel)

    norm = " ".join(out.split())
    assert "No status markers" in norm, (
        "_output_schema_block must retain 'No status markers' directive "
        "(architect is markerless — this must survive the §2.1 reword)."
    )
    assert "## Context" in out, (
        "_output_schema_block must retain '## Context' section header "
        "(architecture doc starts directly with ## Context)."
    )
    assert "Surface-specific for ARCHITECT" in out, (
        "_output_schema_block must retain 'Surface-specific for ARCHITECT' section "
        "(anti-fabrication + architect-specific bullets must survive reword)."
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_build_architect_prompt_doc_path_in_data_and_prompt(tmp_path):
    """AC4: _build_architect_prompt (non-skip ctx) returns data['doc_path'] ==
    str(scratchpad/ARCHITECTURE_DOC_RELPATH) AND that path appears in data['prompt'].

    Fails today: _output_schema_block() is called with 0 args → prompt does NOT
    contain the assigned path (no-arg schema never embeds it).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad)

    # Pass a non-skip prev (skipped=False → passthrough_if_skipped returns None → step runs).
    prev_sentinel = StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )
    result = a._build_architect_prompt(ctx, prev_sentinel)

    assert result.status == "ok", (
        f"_build_architect_prompt failed: {result.error!r}"
    )
    expected_doc_path = str(scratchpad / ARCHITECTURE_DOC_RELPATH)
    assert result.data["doc_path"] == expected_doc_path, (
        f"data['doc_path'] expected {expected_doc_path!r}, got {result.data.get('doc_path')!r}"
    )
    prompt = result.data.get("prompt", "")
    assert expected_doc_path in prompt, (
        f"The assembled prompt must contain the assigned doc_path {expected_doc_path!r}. "
        f"Today _output_schema_block() takes no path arg → path never embedded. "
        f"Prompt tail: {prompt[-300:]!r}"
    )


# ─── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_invoke_architect_llm_passes_write_in_allowed_tools(tmp_path, monkeypatch):
    """AC5: _invoke_architect_llm passes allowed_tools containing "Write" to
    invoke_llm_subprocess.

    Fails today: allowed_tools=["Read", "Grep", "Glob"] (line 290) — "Write" is absent.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH

    captured_kwargs: list[dict] = []

    def _fake_invoke(**kwargs):
        captured_kwargs.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": _SHORT_CONFIRMATION,
                "doc_path": str(doc_path),
                "security_classification": "LOW",
                "response_bytes": len(_SHORT_CONFIRMATION),
                "command": ["claude", "-p"],
            },
            duration_ms=0,
            step_name="invoke_architect_llm",
        )

    monkeypatch.setattr(a, "invoke_llm_subprocess", _fake_invoke)

    ctx = make_ctx(scratchpad)
    prev = _make_invoke_prev(doc_path)

    a._invoke_architect_llm(ctx, prev)

    assert len(captured_kwargs) >= 1, (
        "_invoke_architect_llm did not call invoke_llm_subprocess at all."
    )
    actual_tools = captured_kwargs[0].get("allowed_tools", "__MISSING__")
    assert actual_tools != "__MISSING__", (
        "_invoke_architect_llm called invoke_llm_subprocess but passed no allowed_tools kwarg."
    )
    assert "Write" in actual_tools, (
        f"_invoke_architect_llm must pass 'Write' in allowed_tools. "
        f"Today it passes {actual_tools!r} — 'Write' is absent (§2.3)."
    )


# ─── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_invoke_architect_llm_unlinks_doc_path_before_dispatch(tmp_path, monkeypatch):
    """AC6: _invoke_architect_llm unlinks a pre-existing doc_path before dispatch.
    File is absent after invoke when the patched LLM does not recreate it.

    Fails today: no unlink in _invoke_architect_llm → pre-existing file survives.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-create the doc_path file (simulates a stale doc from a prior run).
    doc_path.write_text("stale architecture from previous run", encoding="utf-8")
    assert doc_path.exists(), "pre-condition: file must exist before invoke"

    def _fake_invoke_no_write(**kwargs):
        # Deliberately does NOT write to doc_path.
        return StepResult(
            status="ok",
            data={
                "raw_response": "short confirmation only",
                "doc_path": str(doc_path),
                "security_classification": "LOW",
                "response_bytes": 22,
                "command": ["claude", "-p"],
            },
            duration_ms=0,
            step_name="invoke_architect_llm",
        )

    monkeypatch.setattr(a, "invoke_llm_subprocess", _fake_invoke_no_write)

    ctx = make_ctx(scratchpad)
    prev = _make_invoke_prev(doc_path)

    a._invoke_architect_llm(ctx, prev)

    assert not doc_path.exists(), (
        f"AC6: _invoke_architect_llm must unlink doc_path before dispatch so a "
        f"non-writing LLM stub leaves the file absent. "
        f"Today no unlink exists → stale file survives → fail-open logic cannot "
        f"distinguish 'this dispatch wrote it' from 'leftover from prior run'. "
        f"File still exists at: {doc_path}"
    )


# ─── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_write_architecture_doc_uses_file_body_over_short_raw_response(tmp_path):
    """AC7: when doc_path file exists with a DISTINCT full doc body AND raw_response is a
    SHORT confirmation string, then:
      - persisted doc_path == FILE body (not the short confirmation)
      - result status=="ok"

    Fails today: _write_architecture_doc reads raw_response unconditionally (line 308-311).
    The pre-written file is never consulted → persisted == short confirmation, not the file body.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-write the full architecture doc (the subagent wrote it via Write tool).
    doc_path.write_text(_ARCH_BODY, encoding="utf-8")

    prev = _make_write_prev(doc_path, _SHORT_CONFIRMATION)

    result = a._write_architecture_doc(None, prev)

    assert result.status == "ok", (
        f"_write_architecture_doc failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    # Persisted file must be the file body, NOT the short confirmation.
    assert _SHORT_CONFIRMATION not in persisted or "## Context" in persisted, (
        f"AC7: this is a weak guard — see next assertion for the forcing check."
    )
    # The short confirmation is DISTINCT from the full body; persisted must equal the full body.
    assert persisted == _ARCH_BODY, (
        f"AC7: persisted doc must be the FILE body (not the short raw_response confirmation). "
        f"Today _write_architecture_doc writes raw_response unconditionally. "
        f"Persisted head: {persisted[:120]!r}"
    )


# ─── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_write_architecture_doc_fallback_to_raw_response_when_file_absent(tmp_path):
    """AC8: when doc_path file is ABSENT AND raw_response = full body,
    persisted doc_path == raw_response (fail-open) AND status=="ok".

    Today: raw_response always used → persisted == raw_response already → this
    passes today (regression guard on the fallback chain; must keep passing post-GREEN).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    # doc_path deliberately NOT written.

    prev = _make_write_prev(doc_path, _ARCH_BODY)

    result = a._write_architecture_doc(None, prev)

    assert result.status == "ok", (
        f"_write_architecture_doc (file absent) failed: status={result.status!r}, error={result.error!r}"
    )
    persisted = doc_path.read_text(encoding="utf-8")

    assert "## Context" in persisted, (
        f"AC8: fallback path must persist raw_response content (contains '## Context'). "
        f"Got: {persisted[:200]!r}"
    )
    assert persisted == _ARCH_BODY, (
        f"AC8: persisted must equal full raw_response body under fail-open path. "
        f"Got: {persisted[:200]!r}"
    )


# ─── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_write_architecture_doc_emits_architect_writer_return_source(tmp_path, monkeypatch):
    """AC9: _write_architecture_doc emits 'architect_writer_return_source' with
    source='worker_file' (AC7-case: file present) and source='raw_response_fallback'
    (AC8-case: file absent).

    Fails today: _emit_safe("architect_writer_return_source", ...) is never called —
    no telemetry scaffolding (_emit_safe) exists in phase_4_architect today.

    Uses monkeypatch.setattr(a, "_emit_safe", capture, raising=False) so the file
    COLLECTS even though _emit_safe doesn't exist yet (§1q / D1CF5FDF).
    """
    # ── Part A: file present → source='worker_file' ──
    scratchpad_a = tmp_path / "scratch_a"
    scratchpad_a.mkdir(parents=True, exist_ok=True)
    doc_path_a = scratchpad_a / ARCHITECTURE_DOC_RELPATH
    doc_path_a.parent.mkdir(parents=True, exist_ok=True)
    doc_path_a.write_text(_ARCH_BODY, encoding="utf-8")

    captured_a: list[tuple[str, dict]] = []

    def _capture_a(event_type, payload):
        captured_a.append((event_type, payload))

    monkeypatch.setattr(a, "_emit_safe", _capture_a, raising=False)

    prev_a = _make_write_prev(doc_path_a, _SHORT_CONFIRMATION)
    result_a = a._write_architecture_doc(None, prev_a)

    assert result_a.status == "ok", (
        f"_write_architecture_doc (file present) failed: {result_a.error!r}"
    )
    source_events_a = [
        (et, p) for et, p in captured_a if et == "architect_writer_return_source"
    ]
    assert len(source_events_a) == 1, (
        f"AC9a: expected exactly 1 'architect_writer_return_source' event (file present), "
        f"got {len(source_events_a)}. All captured events: {[ev[0] for ev in captured_a]}"
    )
    assert source_events_a[0][1].get("source") == "worker_file", (
        f"AC9a: source must be 'worker_file' when doc_path file exists; "
        f"got {source_events_a[0][1]!r}"
    )

    # ── Part B: file absent → source='raw_response_fallback' ──
    scratchpad_b = tmp_path / "scratch_b"
    scratchpad_b.mkdir(parents=True, exist_ok=True)
    doc_path_b = scratchpad_b / ARCHITECTURE_DOC_RELPATH
    doc_path_b.parent.mkdir(parents=True, exist_ok=True)
    # doc_path_b deliberately NOT written.

    captured_b: list[tuple[str, dict]] = []

    def _capture_b(event_type, payload):
        captured_b.append((event_type, payload))

    monkeypatch.setattr(a, "_emit_safe", _capture_b, raising=False)

    prev_b = _make_write_prev(doc_path_b, _ARCH_BODY)
    result_b = a._write_architecture_doc(None, prev_b)

    assert result_b.status == "ok", (
        f"_write_architecture_doc (file absent) failed: {result_b.error!r}"
    )
    source_events_b = [
        (et, p) for et, p in captured_b if et == "architect_writer_return_source"
    ]
    assert len(source_events_b) == 1, (
        f"AC9b: expected exactly 1 'architect_writer_return_source' event (file absent), "
        f"got {len(source_events_b)}. All captured events: {[ev[0] for ev in captured_b]}"
    )
    assert source_events_b[0][1].get("source") == "raw_response_fallback", (
        f"AC9b: source must be 'raw_response_fallback' when doc_path absent; "
        f"got {source_events_b[0][1]!r}"
    )


# ─── AC10 ──────────────────────────────────────────────────────────────────────


def test_ac10_resolve_architect_source_helper_exists_and_returns_fallback_for_absent_path(tmp_path):
    """AC10: _resolve_architect_source is a named callable helper (§1aa forcing-fn).
    Returns (raw, 'raw_response_fallback') for an absent path.

    Fails today: _resolve_architect_source does not exist in phase_4_architect.
    callable() assertion fails (getattr returns None).
    """
    # Access via getattr INSIDE test body (§1q / D1CF5FDF: never import at top level).
    helper = getattr(a, "_resolve_architect_source", None)

    assert callable(helper), (
        "phase_4_architect._resolve_architect_source does not exist or is not callable. "
        "775B49B3 §1aa requires this named helper to be added."
    )

    # Only reached post-GREEN when helper exists.
    absent_path = tmp_path / "nonexistent" / "architecture" / "architecture.md"
    result = helper(str(absent_path), "fallback architecture body")

    assert isinstance(result, tuple) and len(result) == 2, (
        f"_resolve_architect_source must return a 2-tuple (body, source); got {result!r}"
    )
    body, source = result
    assert source == "raw_response_fallback", (
        f"_resolve_architect_source(<absent-path>, ...) second element must be "
        f"'raw_response_fallback'; got {source!r}"
    )
    assert body == "fallback architecture body", (
        f"_resolve_architect_source(<absent-path>, 'fallback architecture body') first element must be "
        f"'fallback architecture body'; got {body!r}"
    )


# ─── AC11 ──────────────────────────────────────────────────────────────────────


def test_ac11_passthrough_preserved_for_write_and_invoke_steps(tmp_path, monkeypatch):
    """AC11: given a skip/passthrough prev, _write_architecture_doc AND _invoke_architect_llm
    return the passthrough unchanged — no unlink, no resolver call, no crash.

    Today: passthrough guard is BEFORE the unlink/resolver additions (which don't exist
    yet). The passthrough guard exists in both steps → this passes today (regression guard).
    Must keep passing post-GREEN to verify the new unlink/resolver additions sit AFTER
    the passthrough guard.

    §1i (singleton/time ACs pre-stage, never race): passthrough state pre-staged
    deterministically via the canonical make_skip_result shape from skip_logic.py.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-create the file to verify it is NOT unlinked during passthrough.
    doc_path.write_text("existing architecture that must survive passthrough", encoding="utf-8")

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

    ctx = make_ctx(scratchpad)

    # ── _write_architecture_doc passthrough ──
    # Patch _emit_safe (raising=False) to prevent AttributeError on unexpected call.
    captured_write: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        a, "_emit_safe", lambda et, p: captured_write.append((et, p)), raising=False
    )

    write_result = a._write_architecture_doc(ctx, skip_prev)

    assert write_result is not None, "_write_architecture_doc must not return None for skip prev"
    assert write_result.data is not None and write_result.data.get("skipped") is True, (
        f"AC11: _write_architecture_doc must return passthrough (skipped=True) for skip prev. "
        f"Got: {write_result!r}"
    )
    # No architect_writer_return_source event should have been emitted on passthrough.
    arch_source_events = [et for et, _ in captured_write if et == "architect_writer_return_source"]
    assert len(arch_source_events) == 0, (
        f"AC11: _write_architecture_doc must NOT emit 'architect_writer_return_source' on passthrough. "
        f"Got: {arch_source_events}"
    )
    # File must NOT have been unlinked.
    assert doc_path.exists(), (
        f"AC11: _write_architecture_doc must NOT unlink doc_path during passthrough. "
        f"File was unexpectedly removed."
    )

    # ── _invoke_architect_llm passthrough ──
    # Patch invoke_llm_subprocess to detect if it was called.
    invoke_called: list[bool] = []

    def _record_invoke(**kw):
        invoke_called.append(True)
        return StepResult(status="ok", data={}, duration_ms=0, step_name="mock")

    monkeypatch.setattr(a, "invoke_llm_subprocess", _record_invoke)

    invoke_result = a._invoke_architect_llm(ctx, skip_prev)

    assert invoke_result is not None, "_invoke_architect_llm must not return None for skip prev"
    assert invoke_result.data is not None and invoke_result.data.get("skipped") is True, (
        f"AC11: _invoke_architect_llm must return passthrough (skipped=True) for skip prev. "
        f"Got: {invoke_result!r}"
    )
    assert len(invoke_called) == 0, (
        f"AC11: _invoke_architect_llm must NOT call invoke_llm_subprocess during passthrough. "
        f"invoke_llm_subprocess was called {len(invoke_called)} time(s)."
    )
    # File must still exist (passthrough must not side-effect).
    assert doc_path.exists(), (
        f"AC11: _invoke_architect_llm must NOT unlink doc_path during passthrough. "
        f"File was unexpectedly removed."
    )


# ─── AC12 ──────────────────────────────────────────────────────────────────────


def test_ac12_role_reword_no_old_contradiction_in_prompt(tmp_path):
    """AC12 — ROLE internal-consistency: _build_architect_prompt (non-skip) data["prompt"]
    contains "You WRITE the architecture file" AND does NOT contain
    "Your response IS the architecture file itself".

    Fails today: old ROLE wording "Your response IS the architecture file itself — not a
    report about it." is present (line 206-210) → negative assertion fires.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad)

    prev_sentinel = StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )
    result = a._build_architect_prompt(ctx, prev_sentinel)

    assert result.status == "ok", (
        f"_build_architect_prompt failed: {result.error!r}"
    )
    prompt = result.data.get("prompt", "")

    assert "You WRITE the architecture file" in prompt, (
        f"AC12: prompt must contain 'You WRITE the architecture file' (§2.2 ROLE reword). "
        f"Old wording still present → this assertion fails until GREEN applies the reword."
    )
    assert "Your response IS the architecture file itself" not in prompt, (
        f"AC12: prompt must NOT contain old ROLE contradiction "
        f"'Your response IS the architecture file itself'. "
        f"Found it in the prompt → §2.2 ROLE reword not applied. "
        f"Prompt ROLE region: {prompt[prompt.find('ROLE:'):prompt.find('ROLE:')+300]!r}"
    )
