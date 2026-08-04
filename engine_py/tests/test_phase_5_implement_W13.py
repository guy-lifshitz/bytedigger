"""W13 RED tests — cap findings payload re-injected into cycle-2 RED prompt.

Closes /ultrareview HIGH #11. ``_gate_on_validation`` (phase_5_implement.py
lines 770-786) currently passes ``prev.data["validation_raw"]`` verbatim
into ``data["findings"]``. ``validation_raw`` holds the full Opus
validation document, which can run multi-MB on verbose runs. This payload
flows into ``_build_red_prompt`` for cycle-2 retry and blows the token
budget.

Fix encoded in tests: tail-truncate ``findings`` to ``FINDINGS_MAX_CHARS``
preserving the RIGHT side (trailing VERDICT marker + closing rationale,
which is what cycle-2 RED actually needs).

Expected on current code (constant added, body unchanged):
  * ``test_gate_truncates_oversized_validation_raw_to_findings_max_bytes``
    — FAILS (no truncation yet); PASSES once GREEN lands.
  * ``test_gate_passes_through_short_validation_raw_unchanged`` — PASSES
    (short input, no truncation needed even after fix).
  * ``test_gate_handles_empty_validation_raw_safely`` — PASSES (empty
    input, no crash, error_code preserved).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    FINDINGS_MAX_CHARS,
    _gate_on_validation,
)


def _make_prev(validation_raw: str) -> StepResult:
    """Build a prev StepResult mirroring opus_validate output on FAIL.

    D352C2D1: prev's status is now 'ok' (verify_validation_citations
    returns ok with verdict in data); FAIL is signalled by data['verdict']
    not by an error_code on prev. The gate then drives the LoopRunner
    iteration via cycle+1 in its own returned data dict."""
    return StepResult(
        status="ok",
        duration_ms=0,
        step_name="verify_validation_citations",
        data={
            "verdict": "FAIL",
            "validation_doc_path": "/tmp/x.md",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/r.log",
            "cycle": 1,
            "validation_raw": validation_raw,
        },
    )


def test_gate_truncates_oversized_validation_raw_to_findings_max_bytes():
    """Multi-MB validation_raw must be truncated to FINDINGS_MAX_CHARS.

    Truncation must keep the TAIL (VERDICT marker + closing rationale),
    since cycle-2 RED prompt needs the verdict + final findings, not
    earlier scaffolding.

    D352C2D1: gate now returns status='ok' (LoopRunner consumes cycle+1
    via marker_field='verdict'); the truncation invariant on
    data['findings'] is unchanged.
    """
    marker = "VERDICT: FAIL\n"
    raw = ("A" * 100_000) + marker
    prev = _make_prev(raw)

    result = _gate_on_validation(_ctx=None, prev=prev)

    assert result.status == "ok"
    assert result.error_code is None
    findings = result.data["findings"]
    assert len(findings) <= FINDINGS_MAX_CHARS, (
        f"findings len={len(findings)} exceeds FINDINGS_MAX_CHARS="
        f"{FINDINGS_MAX_CHARS}. Fix: left-truncate validation_raw in "
        f"_gate_on_validation before placing into data['findings']."
    )
    assert findings.endswith(marker), (
        "Truncation must preserve trailing VERDICT marker (left-truncate, "
        "keep tail). Got tail: " + repr(findings[-40:])
    )


def test_gate_passes_through_short_validation_raw_unchanged():
    """Short validation_raw must pass through verbatim, no truncation."""
    raw = "FAIL: small content"
    prev = _make_prev(raw)

    result = _gate_on_validation(_ctx=None, prev=prev)

    assert result.status == "ok"
    assert result.data["findings"] == raw


def test_gate_handles_empty_validation_raw_safely():
    """Empty validation_raw must not crash; findings stays empty string."""
    prev = _make_prev("")

    result = _gate_on_validation(_ctx=None, prev=prev)

    assert result.status == "ok"
    assert result.data["findings"] == ""
