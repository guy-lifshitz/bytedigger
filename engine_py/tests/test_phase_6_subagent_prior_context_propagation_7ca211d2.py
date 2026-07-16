"""RED tests for 7CA211D2 — phase_6_review PARALLEL DISPATCH block must include
a SUB-AGENT PRIOR-CONTEXT PROPAGATION directive forwarding last_findings.json
to each dispatched sub-agent when a prior attempt exists.

AC1: Propagation directive present in prompt when last_findings.json exists,
     containing all required literals.
AC2: Directive is placed AFTER PARALLEL DISPATCH section, BEFORE ## Prior-Attempt Context.
AC3: No directive emitted when last_findings.json is absent (first attempt).
AC4: Telemetry event phase_6_subagent_prior_context_propagated emitted with
     prior_attempt and finding_count fields.

ALL tests MUST FAIL on current main — production has no propagation directive yet.
Do NOT implement the fix here — RED-only file (7CA211D2).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import call, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import WorkflowContext  # noqa: E402
from phase_6_review import _build_review_prompt  # noqa: E402

# ─── shared fixtures ──────────────────────────────────────────────────────────

_SAMPLE_STRUCTURED_FINDINGS = [
    {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "test"},
]


def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 80) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratch),
            "satisfaction_threshold": satisfaction_threshold,
        },
        question="add feature X",
        session_id="test-7CA211D2",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_last_findings(scratch: Path, attempt: int = 1, findings: list | None = None) -> Path:
    """Write last_findings.json to <scratch>/reviews/last_findings.json."""
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    if findings is None:
        findings = _SAMPLE_STRUCTURED_FINDINGS
    data = {
        "attempt": attempt,
        "score": 60,
        "threshold": 80,
        "review_doc_path": str(reviews_dir / "build-review.md"),
        "structured_findings": findings,
    }
    path = reviews_dir / "last_findings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _suppress_emit(monkeypatch) -> list[tuple]:
    """Suppress _emit_safe and capture calls for assertion."""
    import phase_6_review
    captured: list[tuple] = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# T01 — AC1+AC2: propagation directive present with all required literals
# ═══════════════════════════════════════════════════════════════════════════════


def test_propagation_directive_present_when_last_findings_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1 (7CA211D2): _build_review_prompt must include a ## SUB-AGENT PRIOR-CONTEXT
    PROPAGATION block in the PARALLEL DISPATCH section when last_findings.json exists.

    The block must contain all required literals and the absolute path to last_findings.json.
    FAILS on current main — no propagation directive exists yet.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    scratch = tmp_path / "scratch"
    last_findings_path = _write_last_findings(scratch, attempt=1)

    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T01 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## SUB-AGENT PRIOR-CONTEXT PROPAGATION" in prompt, (
        "T01 FAIL (AC1): prompt must contain literal heading "
        "'## SUB-AGENT PRIOR-CONTEXT PROPAGATION' when last_findings.json present; "
        "not found — spec 7CA211D2 AC1"
    )

    assert "Each dispatched Agent MUST read" in prompt, (
        "T01 FAIL (AC1): prompt must contain 'Each dispatched Agent MUST read'; "
        "not found — spec 7CA211D2 AC1"
    )

    assert str(last_findings_path) in prompt, (
        f"T01 FAIL (AC1): prompt must contain absolute path to last_findings.json "
        f"({last_findings_path}); not found — spec 7CA211D2 AC1"
    )

    assert "PRIOR — still present" in prompt, (
        "T01 FAIL (AC1): prompt must contain literal 'PRIOR — still present'; "
        "not found — spec 7CA211D2 AC1"
    )

    assert "verify file:line" in prompt, (
        "T01 FAIL (AC1): prompt must contain literal 'verify file:line'; "
        "not found — spec 7CA211D2 AC1"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T02 — AC2: directive placed AFTER dispatch table, BEFORE ## Prior-Attempt Context
# ═══════════════════════════════════════════════════════════════════════════════


def test_propagation_directive_placement(tmp_path: Path, monkeypatch) -> None:
    """AC2 (7CA211D2): the ## SUB-AGENT PRIOR-CONTEXT PROPAGATION heading must appear
    AFTER the 'Dispatch table' line (end of PARALLEL DISPATCH section) and BEFORE
    '## Prior-Attempt Context' (the C834481A block added in _build_review_prompt).

    FAILS on current main — no propagation directive exists yet.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    scratch = tmp_path / "scratch"
    _write_last_findings(scratch, attempt=1)

    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T02 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## SUB-AGENT PRIOR-CONTEXT PROPAGATION" in prompt, (
        "T02 FAIL (AC2): '## SUB-AGENT PRIOR-CONTEXT PROPAGATION' not found in prompt; "
        "cannot check placement — spec 7CA211D2 AC2"
    )
    assert "Dispatch table" in prompt, (
        "T02 FAIL (AC2): 'Dispatch table' not found in prompt; "
        "needed as placement anchor — spec 7CA211D2 AC2"
    )
    assert "## Prior-Attempt Context" in prompt, (
        "T02 FAIL (AC2): '## Prior-Attempt Context' not found in prompt; "
        "needed as placement anchor — spec 7CA211D2 AC2"
    )

    dispatch_end = prompt.index("Dispatch table")
    propagation_pos = prompt.index("## SUB-AGENT PRIOR-CONTEXT PROPAGATION")
    prior_context_pos = prompt.index("## Prior-Attempt Context")

    assert propagation_pos > dispatch_end, (
        f"T02 FAIL (AC2): propagation directive (pos={propagation_pos}) must appear AFTER "
        f"'Dispatch table' (pos={dispatch_end}); ordering violated — spec 7CA211D2 AC2"
    )
    assert propagation_pos < prior_context_pos, (
        f"T02 FAIL (AC2): propagation directive (pos={propagation_pos}) must appear BEFORE "
        f"'## Prior-Attempt Context' (pos={prior_context_pos}); ordering violated — spec 7CA211D2 AC2"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T03 — AC3: no propagation when last_findings.json is absent
# ═══════════════════════════════════════════════════════════════════════════════


def test_no_propagation_when_no_prior_findings(tmp_path: Path, monkeypatch) -> None:
    """AC3 (7CA211D2): when last_findings.json does NOT exist (first attempt),
    _build_review_prompt must NOT include the propagation directive or any mention
    of last_findings.json in the prompt.

    Expected to PASS on current main (no injection yet); regression guard post-GREEN.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    # No last_findings.json written

    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T03 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## SUB-AGENT PRIOR-CONTEXT PROPAGATION" not in prompt, (
        "T03 FAIL (AC3): prompt must NOT contain '## SUB-AGENT PRIOR-CONTEXT PROPAGATION' "
        "when last_findings.json is absent; found — spec 7CA211D2 AC3"
    )
    assert "last_findings.json" not in prompt, (
        "T03 FAIL (AC3): prompt must NOT mention 'last_findings.json' when file absent; "
        "found — spec 7CA211D2 AC3"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T04 — AC4: telemetry event emitted with prior_attempt and finding_count
# ═══════════════════════════════════════════════════════════════════════════════


def test_propagation_emits_telemetry(tmp_path: Path, monkeypatch) -> None:
    """AC4 (7CA211D2): when the propagation directive is added, _emit_safe must be called
    with event_type 'phase_6_subagent_prior_context_propagated' and payload
    {'prior_attempt': 1, 'finding_count': 2}.

    The existing C834481A 'phase_6_inject_prior_context' event is separate — we assert
    the 7CA211D2 event is present in the call list, not that it's the only call.
    FAILS on current main — no propagation directive or telemetry event exists yet.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    scratch = tmp_path / "scratch"
    two_findings = [
        {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "test"},
        {"id": "2", "severity": "MEDIUM", "path": "src/bar.py", "description": "test2"},
    ]
    _write_last_findings(scratch, attempt=1, findings=two_findings)

    import phase_6_review
    emitted_events: list[tuple] = []

    def _capture_emit(event_type, payload, **kw):
        emitted_events.append((event_type, payload, kw))

    monkeypatch.setattr(phase_6_review, "_emit_safe", _capture_emit)

    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T04 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )

    propagation_events = [
        (et, payload)
        for et, payload, _ in emitted_events
        if et == "phase_6_subagent_prior_context_propagated"
    ]
    assert len(propagation_events) >= 1, (
        f"T04 FAIL (AC4): expected _emit_safe call with event_type "
        f"'phase_6_subagent_prior_context_propagated'; not found. "
        f"All emitted events: {[et for et, _, __ in emitted_events]} — spec 7CA211D2 AC4"
    )

    _, payload = propagation_events[0]
    assert payload.get("prior_attempt") == 1, (
        f"T04 FAIL (AC4): telemetry payload must have prior_attempt=1; "
        f"got {payload.get('prior_attempt')!r} — spec 7CA211D2 AC4"
    )
    assert payload.get("finding_count") == 2, (
        f"T04 FAIL (AC4): telemetry payload must have finding_count=2; "
        f"got {payload.get('finding_count')!r} — spec 7CA211D2 AC4"
    )
