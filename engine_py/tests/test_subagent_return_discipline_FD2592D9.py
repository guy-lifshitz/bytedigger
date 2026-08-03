"""RED tests for agreement FD2592D9 — subagent-return-discipline (spec-writer pilot).

Problem: the spec-writer subagent returns the full spec body (~2.5K tok) as
raw_response, which bloats the in-session servicer context quadratically.

Fix (this slice):
 - _spec_output_schema(doc_path) instructs the agent to Write to the pre-assigned
   path and return only a ≤200-token status.
 - _write_spec_doc reads doc_path as the source of truth (fail-open to
   raw_response when the file is absent/empty).
 - A new named helper _resolve_spec_source encapsulates the source-selection
   logic (§1aa forcing-fn).
 - _emit_safe("spec_writer_return_source", ...) on both branches.

All 9 tests MUST fail against current production code:
  AC1 — _spec_output_schema() takes no args today (TypeError at call time).
  AC2 — old bare phrasing "response IS the file content of specs/build-spec.md"
         still present; reframed Write-to-path form absent.
  AC3 — _spec_output_schema() accepts no doc_path arg today → call fails.
  AC4 — _write_spec_doc reads raw_response unconditionally (line 874);
         a pre-written doc_path file is silently ignored → canonical == short status.
  AC5 — same raw_response path (happens to pass today — see comment inline).
  AC6 — spec_writer_return_source event never emitted today.
  AC7 — file-sourced content path (AC4 setup) doesn't exist yet → raw_response
         used, but raw_response is the short status → citation never prefixed.
  AC8 — file-sourced content path (AC4 setup) doesn't exist yet → canary block
         in file is never parsed when short raw_response is used.
  AC9 — _resolve_spec_source does not exist yet.

§1q extension (D1CF5FDF): _resolve_spec_source does NOT exist at import time.
  Access via getattr INSIDE test body; never import at top level.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

# Stable, always-exist imports only — no _resolve_spec_source at top level.
from bytedigger_engine.workflows import phase_45_spec as mod  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: E402


# ─── shared helpers ────────────────────────────────────────────────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for telemetry assertions."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _make_ctx(scratchpad: Path, *, git_cwd: str | None = None) -> WorkflowContext:
    """Build a WorkflowContext for _write_spec_doc / _build_spec_prompt calls."""
    org: dict = {"scratchpad_dir": str(scratchpad)}
    org["git_cwd"] = git_cwd if git_cwd is not None else str(scratchpad)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session-FD2592D9",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(doc_path: Path, raw_response: str, *, cycle: int = 1) -> StepResult:
    """Build a StepResult shaped like the spec-LLM step output."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="invoke_spec_llm",
    )


# Minimal valid spec body that contains a bare citation and a Canary Integration block.
_SPEC_BODY_WITH_CITATION_AND_CANARY = (
    "## Context\n"
    "Full spec body written to file by the subagent.\n\n"
    "## User Stories\n"
    "### US1 - Demo (P1 - MVP)\n"
    "  **Why P1**: needed.\n"
    "  **Acceptance**: Given x, When y, Then z.\n\n"
    "## Files\n"
    "MODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n\n"
    "## Interfaces\n"
    "stub() -> None\n\n"
    "## Data Model\n"
    "None.\n\n"
    "## Behavior\n"
    "- Resolves spec source from file.\n\n"
    "## Constraints\n"
    "- See phase_45_spec.py:874 for the insertion point.\n\n"
    "## Out of Scope\n"
    "- Everything else.\n\n"
    "## Acceptance Criteria\n"
    "1. File-sourced path works.\n"
    "   Validation: call _write_spec_doc and read canonical.\n\n"
    "## Open Questions\n"
    "- None.\n\n"
    "## Canary Integration\n\n"
    "event_type: spec_writer_return_source_test\n"
)

# Short status string — what the subagent returns as raw_response under the new protocol.
_SHORT_STATUS = "Wrote spec to /tmp/specs/build-spec.md. Done."


# ─── AC1 ───────────────────────────────────────────────────────────────────────


def test_ac1_spec_output_schema_with_doc_path_contains_required_substrings(tmp_path):
    """AC1: _spec_output_schema(doc_path) contains doc_path AND 'Write' AND
    ('≤200' or '200-token') AND ('do NOT echo' or 'not echo').

    Fails today: _spec_output_schema() takes 0 args → TypeError.
    """
    sentinel_path = str(tmp_path / "specs" / "build-spec.md")

    # Call inside test body; TypeError is the assert-time failure for current code.
    schema = mod._spec_output_schema(sentinel_path)

    assert sentinel_path in schema, (
        f"_spec_output_schema output must contain the assigned doc_path {sentinel_path!r}; "
        f"got: {schema[:200]!r}"
    )
    assert "Write" in schema, (
        "_spec_output_schema output must contain 'Write' tool instruction."
    )
    assert "≤200" in schema or "200-token" in schema, (
        "_spec_output_schema output must contain '≤200' or '200-token' token budget."
    )
    assert "do NOT echo" in schema or "not echo" in schema or "NOT echo" in schema, (
        "_spec_output_schema output must contain 'do NOT echo' or 'not echo' phrase."
    )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_spec_output_schema_reframes_response_instruction(tmp_path):
    """AC2: _spec_output_schema(doc_path) does NOT contain bare old phrasing
    'response IS the file content of specs/build-spec.md'; contains Write-to-path
    reframing instead.

    Fails today: _spec_output_schema() takes 0 args → TypeError (primary fail).
    Even if the signature were patched, the old bare phrase is still present today.
    """
    sentinel_path = str(tmp_path / "specs" / "build-spec.md")

    schema = mod._spec_output_schema(sentinel_path)

    # Old bare contradiction must be absent.
    assert "response IS the file content of specs/build-spec.md" not in schema, (
        "Old bare phrasing 'response IS the file content of specs/build-spec.md' "
        "must be removed or reframed in _spec_output_schema."
    )
    # Reframed form: Write-to-path instruction must be present.
    assert sentinel_path in schema, (
        "Reframed Write-to-path form must reference the assigned doc_path."
    )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


def test_ac3_build_spec_prompt_doc_path_and_path_in_prompt(tmp_path):
    """AC3: _build_spec_prompt returns StepResult.data['doc_path'] ==
    str(scratchpad / SPEC_DOC_RELPATH) AND data['prompt'] contains that path.

    Fails today: _spec_output_schema() (called inside _build_spec_prompt at line 657)
    takes 0 args — calling with no args today succeeds, but the prompt will NOT
    contain the assigned path because the no-arg version never embeds it.
    So assert 'prompt contains doc_path' fails.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad)

    result = mod._build_spec_prompt(ctx, None)

    assert result.status == "ok", f"_build_spec_prompt failed: {result.error!r}"
    expected_doc_path = str(scratchpad / SPEC_DOC_RELPATH)

    assert result.data["doc_path"] == expected_doc_path, (
        f"data['doc_path'] expected {expected_doc_path!r}, "
        f"got {result.data.get('doc_path')!r}"
    )
    prompt = result.data.get("prompt", "")
    assert expected_doc_path in prompt, (
        f"Assembled prompt must contain the assigned doc_path {expected_doc_path!r}. "
        f"Today's prompt does not embed it (no-arg schema never references path). "
        f"Prompt tail: {prompt[-300:]!r}"
    )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


def test_ac4_write_spec_doc_uses_file_body_over_short_raw_response(tmp_path):
    """AC4: when doc_path file exists with full spec body AND raw_response is a
    short status string, persisted canonical == file body (post-autoprefix), NOT status.

    Fails today: line 874 reads raw = prev.data['raw_response'] unconditionally;
    the pre-written file is never consulted → canonical == short status.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-write the full spec to doc_path (the subagent wrote it via Write tool).
    doc_path.write_text(_SPEC_BODY_WITH_CITATION_AND_CANARY, encoding="utf-8")

    ctx = _make_ctx(scratchpad)
    prev = _make_prev(doc_path, _SHORT_STATUS, cycle=1)

    result = mod._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed: status={result.status!r}, error={result.error!r}"
    )
    canonical_path = Path(result.data["spec_path"])
    canonical = canonical_path.read_text(encoding="utf-8")

    # The canonical MUST NOT be the short status.
    assert _SHORT_STATUS not in canonical, (
        f"AC4: canonical spec must NOT be the short raw_response status. "
        f"Today _write_spec_doc writes raw_response unconditionally. "
        f"Canonical head: {canonical[:100]!r}"
    )
    # The canonical MUST contain the file body's content.
    assert "Full spec body written to file by the subagent" in canonical, (
        f"AC4: canonical spec must contain the file body content. "
        f"Got: {canonical[:200]!r}"
    )


# ─── AC5 ───────────────────────────────────────────────────────────────────────


def test_ac5_write_spec_doc_fallback_to_raw_response_when_file_absent(tmp_path):
    """AC5: when doc_path file is ABSENT, persisted canonical == raw_response
    content (post-autoprefix) — fallback preserved.

    Today: raw_response is always used → this test MAY pass today.
    It must still pass post-GREEN (regression guard on the fallback path).
    We assert on content the current code also produces — but AC9 ensures
    the new helper exists; without it, the file-present path (AC4) breaks.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    # doc_path file deliberately NOT written.

    full_body = (
        "## Context\nFallback spec body from raw_response.\n\n"
        "## User Stories\n### US1 - Fallback (P1 - MVP)\n"
        "  **Why P1**: needed.\n"
        "  **Acceptance**: Given x, When y, Then z.\n\n"
        "## Files\nMODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n\n"
        "## Interfaces\nfallback() -> None\n\n"
        "## Data Model\nNone.\n\n"
        "## Behavior\n- Raw response used when file absent.\n\n"
        "## Constraints\n- None.\n\n"
        "## Out of Scope\n- Everything else.\n\n"
        "## Acceptance Criteria\n1. Fallback works.\n   Validation: read canonical.\n\n"
        "## Open Questions\n- None.\n"
    )
    ctx = _make_ctx(scratchpad)
    prev = _make_prev(doc_path, full_body, cycle=1)

    result = mod._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed: status={result.status!r}, error={result.error!r}"
    )
    canonical_path = Path(result.data["spec_path"])
    canonical = canonical_path.read_text(encoding="utf-8")

    assert "Fallback spec body from raw_response" in canonical, (
        f"AC5: fallback path must persist raw_response content. Got: {canonical[:200]!r}"
    )


# ─── AC6 ───────────────────────────────────────────────────────────────────────


def test_ac6_write_spec_doc_emits_spec_writer_return_source(tmp_path):
    """AC6: _write_spec_doc emits spec_writer_return_source with source='worker_file'
    (file-present path) and source='raw_response_fallback' (file-absent path).

    Fails today: _emit_safe("spec_writer_return_source", ...) does not exist in
    _write_spec_doc — no such event is ever emitted.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    # --- AC6 part A: file present → source='worker_file' ---
    doc_path_a = scratchpad / "specs" / "build-spec.md"
    doc_path_a.parent.mkdir(parents=True, exist_ok=True)
    doc_path_a.write_text(
        "## Context\nFile body for AC6a.\n\n"
        "## User Stories\n### US1 - AC6a (P1 - MVP)\n"
        "  **Why P1**: AC6a.\n  **Acceptance**: Given x, When y, Then z.\n\n"
        "## Files\nMODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n\n"
        "## Interfaces\nac6a() -> None\n\n"
        "## Data Model\nNone.\n\n"
        "## Behavior\n- AC6a test.\n\n"
        "## Constraints\n- None.\n\n"
        "## Out of Scope\n- Everything.\n\n"
        "## Acceptance Criteria\n1. AC6a.\n   Validation: read event.\n\n"
        "## Open Questions\n- None.\n",
        encoding="utf-8",
    )

    log_a = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log_a,
        run_id="test-FD2592D9-ac6a",
        step_name="write_spec_doc",
        phase="phase_45_spec",
    )
    try:
        ctx_a = _make_ctx(scratchpad)
        prev_a = _make_prev(doc_path_a, _SHORT_STATUS, cycle=1)
        result_a = mod._write_spec_doc(ctx_a, prev_a)
    finally:
        telemetry_ctx.clear_current_run()

    assert result_a.status == "ok", (
        f"_write_spec_doc (file present) failed: {result_a.error!r}"
    )
    source_events_a = [
        (etype, payload) for etype, payload, _ in log_a.events
        if etype == "spec_writer_return_source"
    ]
    assert len(source_events_a) == 1, (
        f"AC6a: expected exactly 1 'spec_writer_return_source' event (file present), "
        f"got {len(source_events_a)}. All events: {[e[0] for e in log_a.events]}"
    )
    assert source_events_a[0][1].get("source") == "worker_file", (
        f"AC6a: source must be 'worker_file' when doc_path file exists; "
        f"got {source_events_a[0][1]!r}"
    )

    # --- AC6 part B: file absent → source='raw_response_fallback' ---
    scratchpad_b = tmp_path / "scratch_b"
    scratchpad_b.mkdir(parents=True, exist_ok=True)
    doc_path_b = scratchpad_b / "specs" / "build-spec.md"
    doc_path_b.parent.mkdir(parents=True, exist_ok=True)
    # doc_path_b deliberately NOT written

    fallback_body = (
        "## Context\nFallback for AC6b.\n\n"
        "## User Stories\n### US1 - AC6b (P1 - MVP)\n"
        "  **Why P1**: AC6b.\n  **Acceptance**: Given x, When y, Then z.\n\n"
        "## Files\nMODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n\n"
        "## Interfaces\nac6b() -> None\n\n"
        "## Data Model\nNone.\n\n"
        "## Behavior\n- AC6b test.\n\n"
        "## Constraints\n- None.\n\n"
        "## Out of Scope\n- Everything.\n\n"
        "## Acceptance Criteria\n1. AC6b.\n   Validation: read event.\n\n"
        "## Open Questions\n- None.\n"
    )
    log_b = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log_b,
        run_id="test-FD2592D9-ac6b",
        step_name="write_spec_doc",
        phase="phase_45_spec",
    )
    try:
        ctx_b = _make_ctx(scratchpad_b)
        prev_b = _make_prev(doc_path_b, fallback_body, cycle=1)
        result_b = mod._write_spec_doc(ctx_b, prev_b)
    finally:
        telemetry_ctx.clear_current_run()

    assert result_b.status == "ok", (
        f"_write_spec_doc (file absent) failed: {result_b.error!r}"
    )
    source_events_b = [
        (etype, payload) for etype, payload, _ in log_b.events
        if etype == "spec_writer_return_source"
    ]
    assert len(source_events_b) == 1, (
        f"AC6b: expected exactly 1 'spec_writer_return_source' event (file absent), "
        f"got {len(source_events_b)}. All events: {[e[0] for e in log_b.events]}"
    )
    assert source_events_b[0][1].get("source") == "raw_response_fallback", (
        f"AC6b: source must be 'raw_response_fallback' when doc_path absent; "
        f"got {source_events_b[0][1]!r}"
    )


# ─── AC7 ───────────────────────────────────────────────────────────────────────


def test_ac7_autoprefix_applies_to_file_sourced_content(tmp_path):
    """AC7: when doc_path file contains a bare citation like 'phase_45_spec.py:12',
    the persisted canonical has it repo-rooted (autoprefix applied to file body).

    Fails today: _write_spec_doc uses raw_response unconditionally (line 874).
    raw_response is _SHORT_STATUS (no bare citation) → autoprefix has nothing to
    rewrite → the bare citation from the file body never appears in canonical.
    The assertion 'bare citation prefixed in canonical' fails because the file
    body is never read.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # File body contains a bare citation that autoprefix should rewrite.
    # Use a git repo so autoprefix has a chance to work post-GREEN.
    # For the RED assertion we only need to show file body is the source.
    file_body = (
        "## Context\nSpec with bare citation. See phase_45_spec.py:874 for the insertion point.\n\n"
        "## User Stories\n### US1 - Bare citation (P1 - MVP)\n"
        "  **Why P1**: AC7.\n  **Acceptance**: Given x, When y, Then z.\n\n"
        "## Files\nMODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n\n"
        "## Interfaces\nac7() -> None\n\n"
        "## Data Model\nNone.\n\n"
        "## Behavior\n- Autoprefix on file source.\n\n"
        "## Constraints\n- None.\n\n"
        "## Out of Scope\n- Everything.\n\n"
        "## Acceptance Criteria\n1. Citation prefixed.\n   Validation: read canonical.\n\n"
        "## Open Questions\n- None.\n"
    )
    doc_path.write_text(file_body, encoding="utf-8")

    ctx = _make_ctx(scratchpad)
    prev = _make_prev(doc_path, _SHORT_STATUS, cycle=1)

    result = mod._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed: {result.error!r}"
    )
    canonical = Path(result.data["spec_path"]).read_text(encoding="utf-8")

    # The canonical must contain the file body's content (not the short status).
    # This is the forcing function: if file-sourced path works, this phrase is present.
    assert "Spec with bare citation" in canonical, (
        f"AC7: canonical must contain file body content (not short raw_response). "
        f"Today raw_response (_SHORT_STATUS) is used → this phrase never appears. "
        f"Canonical head: {canonical[:200]!r}"
    )


# ─── AC8 ───────────────────────────────────────────────────────────────────────


def test_ac8_canary_sidecar_written_from_file_sourced_spec(tmp_path):
    """AC8: when doc_path file contains a Canary Integration block, the canary
    sidecar JSON is written (even though raw_response is a short status with no block).

    Fails today: _write_spec_doc uses raw_response unconditionally (line 874).
    _SHORT_STATUS has no Canary Integration block → _parse_canary_integration
    returns {} → no sidecar is written.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # File body contains the Canary Integration block.
    doc_path.write_text(_SPEC_BODY_WITH_CITATION_AND_CANARY, encoding="utf-8")

    ctx = _make_ctx(scratchpad)
    # raw_response is the short status (NO canary block).
    prev = _make_prev(doc_path, _SHORT_STATUS, cycle=1)

    result = mod._write_spec_doc(ctx, prev)

    assert result.status == "ok", (
        f"_write_spec_doc failed: {result.error!r}"
    )

    # Canary sidecar must exist (sourced from file, not from short raw_response).
    sidecar_path = scratchpad / "integration" / "canary-meta.json"
    assert sidecar_path.exists(), (
        f"AC8: canary sidecar must be written when file body contains Canary Integration block. "
        f"Today raw_response (_SHORT_STATUS) is used → no canary block → sidecar never created. "
        f"Expected sidecar at: {sidecar_path}"
    )
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_data.get("event_type") == "spec_writer_return_source_test", (
        f"AC8: sidecar event_type expected 'spec_writer_return_source_test', "
        f"got {sidecar_data!r}"
    )


# ─── AC9 ───────────────────────────────────────────────────────────────────────


def test_ac9_resolve_spec_source_helper_exists_and_returns_fallback_for_absent_path(tmp_path):
    """AC9: _resolve_spec_source is a callable named helper (§1aa forcing-fn).
    _resolve_spec_source(<absent-path>, 'fallback body') returns a tuple whose
    second element == 'raw_response_fallback'.

    Fails today: _resolve_spec_source does not exist in phase_45_spec.
    callable() assertion fails (None is not callable).
    """
    # Access via getattr INSIDE test body (§1q / D1CF5FDF: never import at top level).
    helper = getattr(mod, "_resolve_spec_source", None)

    assert callable(helper), (
        "phase_45_spec._resolve_spec_source does not exist or is not callable. "
        "FD2592D9 §1aa requires this named helper to be added."
    )

    # Only reach here post-GREEN when helper exists.
    absent_path = tmp_path / "nonexistent" / "build-spec.md"
    # absent_path does not exist — should trigger raw_response_fallback branch.
    result = helper(absent_path, "fallback body")

    assert isinstance(result, tuple) and len(result) == 2, (
        f"_resolve_spec_source must return a 2-tuple (raw, source); got {result!r}"
    )
    _, source = result
    assert source == "raw_response_fallback", (
        f"_resolve_spec_source(<absent-path>, ...) second element must be "
        f"'raw_response_fallback'; got {source!r}"
    )
