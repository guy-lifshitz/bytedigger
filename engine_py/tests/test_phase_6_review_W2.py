"""Wave 2 RED tests for phase_6_review — 4-fix batch.

AC mapping:
    test_read_first_block_lists_five_files_including_producer_rules  → AC1 (MED-3)
    test_resolve_complexity_no_stale_todo_comment                    → AC2 (HIGH-2)
    test_resolve_complexity_none_emits_event                         → AC3 (HIGH-2)
    test_write_review_artifact_retry_uses_raise_not_assert           → AC4 (CRIT-4)
    test_write_fix_artifact_retry_uses_raise_not_assert              → AC5 (CRIT-4)
    test_write_fix_artifact_retry_preserves_extra_data               → AC6 (HIGH-3)
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from event_log import EventLog  # noqa: E402
from phase_6_review import (  # noqa: E402
    FIX_DOC_RELPATH,
    GREEN_LOG_RELPATH,
    RED_LOG_RELPATH,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    VERDICT_FAIL,
    phase_6_review_workflow,
    _read_first_block,
    _resolve_complexity,
)
from engine import WorkflowEngine  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-w2",
        persona="hal",
        framework=None,
        domain=None,
    )


def seed_spec(scratchpad: Path, body: str = "## US1\nAdd foo\n") -> Path:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body)
    return spec


def seed_red_log(scratchpad: Path, body: str = "RED COMPLETE — 1 test\n") -> Path:
    log = scratchpad / RED_LOG_RELPATH
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(body)
    return log


def seed_green_log(scratchpad: Path, body: str = "GREEN COMPLETE — passing\n") -> Path:
    log = scratchpad / GREEN_LOG_RELPATH
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(body)
    return log


# ─── AC1: MED-3 — _read_first_block lists five files including producer-rules ─


def test_read_first_block_lists_five_files_including_producer_rules(tmp_path):
    """AC1 (MED-3): _read_first_block must say 'five files' and list producer-rules.md.

    Current implementation says 'four files' and lists only:
    hal-memory.md, constitution.md, quality-gate.md, active-work.md.
    Fix: add producer-rules.md (matching phase_5 contract) and update count.
    """
    scratchpad = tmp_path / "scratch"
    block = _read_first_block(scratchpad)

    # Must explicitly say "five files" not "four files"
    assert "five files" in block, (
        f"_read_first_block says 'four files' not 'five files'; current block:\n{block}"
    )
    assert "four files" not in block, (
        "_read_first_block still says 'four files' — update to 'five files'"
    )

    # producer-rules.md must appear in the file list
    assert "producer-rules.md" in block, (
        f"producer-rules.md missing from _read_first_block; current block:\n{block}"
    )

    # The other four must still be present
    inj = scratchpad / "injection"
    for expected in ("hal-memory.md", "constitution.md", "quality-gate.md", "active-work.md"):
        assert expected in block, f"{expected} missing from _read_first_block"


# ─── AC2: HIGH-2 — stale TODO comment absent from _resolve_complexity ─────────


def test_resolve_complexity_no_stale_todo_comment():
    """AC2 (HIGH-2): Stale TODO comment at lines 206-207 must be removed.

    The TODO said 'orchestrator does not yet pass complexity to phase_6 ctx-json;
    SIMPLE 3-reviewer path is dormant...'. Fix: remove TODO, add docstring
    documenting the None→FEATURE default as an explicit contract.
    """
    import phase_6_review as m

    source = inspect.getsource(m._resolve_complexity)

    # The stale TODO must be gone
    assert "TODO: orchestrator does not yet pass" not in source, (
        "Stale TODO comment still present in _resolve_complexity — must be removed per AC2"
    )
    assert "SIMPLE 3-reviewer path is dormant" not in source, (
        "Stale 'SIMPLE 3-reviewer path is dormant' comment still in _resolve_complexity"
    )

    # The None→FEATURE behavior must be documented as a contract (not silent gap)
    # Accept either docstring form or inline comment form
    has_contract_doc = (
        '"""' in source or "'''" in source or "# Default:" in source
        or "documented contract" in source or "complexity is None" in source.lower()
    )
    assert has_contract_doc, (
        "_resolve_complexity has no docstring or contract comment explaining None→FEATURE default"
    )


# ─── AC3: HIGH-2 — _resolve_complexity None → emits complexity_default_used ──


def test_resolve_complexity_none_emits_event(tmp_path):
    """AC3 (HIGH-2): When complexity is None, _resolve_complexity must emit
    a 'complexity_default_used' telemetry event and return 'FEATURE'.

    Currently: returns 'FEATURE' silently, no event emitted.
    Fix: emit _emit_safe(..., 'complexity_default_used', {...}) before return.
    """
    import phase_6_review as m
    import telemetry_ctx  # same pattern as phase_05_inject

    # Set up a real EventLog backed by a temp file so we can inspect events
    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)

    # Simulate the telemetry run context that _emit_safe reads
    run_ctx_mock = MagicMock()
    run_ctx_mock.event_log = event_log
    run_ctx_mock.run_id = "test-w2-ac3"

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        result = m._resolve_complexity(
            WorkflowContext(
                tenant_id="hal",
                scope=None,
                db_path=None,
                org_config={},  # no complexity key → triggers default
                question="test",
                session_id="test-w2-ac3",
                persona="hal",
                framework=None,
                domain=None,
            )
        )

    assert result == "FEATURE", f"Expected 'FEATURE', got {result!r}"

    # Verify complexity_default_used event was emitted
    lines = log_path.read_text().strip().splitlines() if log_path.exists() else []
    import json
    events = [json.loads(line) for line in lines if line.strip()]
    default_events = [e for e in events if e.get("event_type") == "complexity_default_used"]
    assert len(default_events) == 1, (
        f"Expected 1 'complexity_default_used' event, got {len(default_events)}. "
        f"Events emitted: {[e.get('event_type') for e in events]}"
    )
    # Payload must contain the fallback value
    payload = default_events[0]["payload"]
    assert payload.get("fallback") == "FEATURE" or payload.get("complexity") == "FEATURE", (
        f"complexity_default_used payload missing 'FEATURE' value: {payload}"
    )


# ─── AC4: CRIT-4 — _write_review_artifact retry uses raise not assert ─────────


def test_write_review_artifact_retry_uses_raise_not_assert():
    """AC4 (CRIT-4): Line 537 'assert False' must be replaced with 'raise AssertionError'.

    'assert False' is stripped by python -O, making the branch silently fall
    through instead of failing loudly. Fix: 'raise AssertionError(...)'.

    Verified via source inspection — robust regardless of test runner flags.
    """
    import phase_6_review as m

    source = inspect.getsource(m._write_review_artifact)

    # Must NOT use bare 'assert False' (stripped under -O)
    assert "assert False" not in source, (
        "Line 537 still uses 'assert False' in _write_review_artifact — "
        "must be replaced with 'raise AssertionError(...)' (survives python -O)"
    )

    # Must use 'raise AssertionError' for the unexpected-retry-shape branch
    assert "raise AssertionError" in source, (
        "_write_review_artifact retry-shape branch does not use 'raise AssertionError' — "
        "the 'assert False' replacement is missing"
    )


# ─── AC5: CRIT-4 — _write_fix_artifact retry uses raise not assert ────────────


def test_write_fix_artifact_retry_uses_raise_not_assert():
    """AC5 (CRIT-4): Line 757 'assert isinstance(...)' must be replaced with
    explicit 'if not isinstance(...): raise AssertionError(...)'.

    'assert isinstance(retry_result.data, dict)' is stripped by python -O,
    silently falling through on malformed subprocess result.

    Verified via source inspection.
    """
    import phase_6_review as m

    source = inspect.getsource(m._write_fix_artifact)

    # The bare assert form must be gone
    # Match 'assert isinstance(retry_result' which is the problematic pattern
    assert "assert isinstance(retry_result.data, dict)" not in source, (
        "Line 757 still uses 'assert isinstance(retry_result.data, dict)' in "
        "_write_fix_artifact — must be replaced with explicit 'raise AssertionError' "
        "so it survives python -O"
    )

    # Must use raise AssertionError for the malformed-data branch
    assert "raise AssertionError" in source, (
        "_write_fix_artifact malformed-data branch does not use 'raise AssertionError' — "
        "the assert→raise replacement is missing"
    )


# ─── AC6: HIGH-3 — _write_fix_artifact retry preserves extra_data ─────────────


def test_write_fix_artifact_retry_preserves_extra_data(tmp_path):
    """AC6 (HIGH-3): _write_fix_artifact retry call at lines 730-735 must pass
    extra_data with same shape as the original invoke_fix_llm step.

    Current code calls invoke_llm_subprocess for retry WITHOUT extra_data,
    so the retry StepResult.data lacks log_path, spec_path, review_doc_path,
    verdict, and prompt.

    Strategy: intercept invoke_llm_subprocess calls; verify the SECOND call
    (the retry) receives extra_data containing the expected keys.
    """
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    seed_red_log(scratchpad)
    seed_green_log(scratchpad)

    call_log: list[dict] = []

    # Build a fake review doc so write_review_artifact succeeds
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text(
        "# Composite Review\n\n## Aggregated Findings\n\nSeverity: HIGH\nVERDICT: FAIL\n"
    )

    # Fake fix log destination
    fix_log = scratchpad / FIX_DOC_RELPATH
    fix_log.parent.mkdir(parents=True, exist_ok=True)

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, idle_timeout_sec=None, **kwargs):
        """Capture calls; first (invoke_fix_llm) returns no-marker; retry returns COMPLETE."""
        call_log.append({"step_name": step_name, "extra_data": extra_data, "prompt": prompt})
        fix_calls = [c for c in call_log if "fix" in c["step_name"]]
        if step_name == "invoke_fix_llm":
            # First call: no marker → triggers retry
            return StepResult(
                status="ok",
                data={
                    "raw_response": "Some text without any marker.\n",
                    "log_path": str(fix_log),
                    "spec_path": str(scratchpad / SPEC_DOC_RELPATH),
                    "review_doc_path": str(review_doc),
                    "verdict": VERDICT_FAIL,
                    "prompt": prompt,
                },
                duration_ms=10,
                step_name=step_name,
            )
        elif step_name == "invoke_fix_llm_retry":
            # Retry call: return FIX COMPLETE so the workflow proceeds
            return StepResult(
                status="ok",
                data={
                    "raw_response": "FIX COMPLETE — 1 of 1 findings fixed. Files: [x.py]\n",
                    "log_path": str(fix_log),
                    "spec_path": str(scratchpad / SPEC_DOC_RELPATH),
                    "review_doc_path": str(review_doc),
                    "verdict": VERDICT_FAIL,
                    "prompt": prompt,
                },
                duration_ms=10,
                step_name=step_name,
            )
        # Other LLM calls (review, satisfaction): return passthrough
        return StepResult(
            status="ok",
            data={"raw_response": "SCORE: 85\nVERDICT: PASS\n"},
            duration_ms=5,
            step_name=step_name,
        )

    import phase_6_review as m
    with patch.object(m, "invoke_llm_subprocess", side_effect=fake_invoke):
        # Directly call _write_fix_artifact with a prev that has no-marker output
        # (simulating the state after invoke_fix_llm returned FIX_NO_MARKER response)
        prev = StepResult(
            status="ok",
            data={
                "raw_response": "Some text without any marker.\n",
                "log_path": str(fix_log),
                "spec_path": str(scratchpad / SPEC_DOC_RELPATH),
                "review_doc_path": str(review_doc),
                "verdict": VERDICT_FAIL,
                "prompt": "Fix the following findings...",
            },
            duration_ms=10,
            step_name="invoke_fix_llm",
        )
        ctx = make_ctx(scratchpad)
        m._write_fix_artifact(ctx, prev)

    # Find the retry call
    retry_calls = [c for c in call_log if c["step_name"] == "invoke_fix_llm_retry"]
    assert len(retry_calls) == 1, (
        f"Expected 1 retry call (invoke_fix_llm_retry), got {len(retry_calls)}. "
        f"All calls: {[c['step_name'] for c in call_log]}"
    )

    retry_extra = retry_calls[0]["extra_data"]
    assert retry_extra is not None, (
        "Retry call passed extra_data=None — AC6 requires extra_data with "
        "log_path, spec_path, review_doc_path, verdict, prompt"
    )

    # Verify all required keys are present
    required_keys = {"log_path", "spec_path", "review_doc_path", "verdict", "prompt"}
    missing = required_keys - set(retry_extra.keys())
    assert not missing, (
        f"Retry extra_data missing keys: {missing}. "
        f"Got keys: {set(retry_extra.keys())}"
    )
