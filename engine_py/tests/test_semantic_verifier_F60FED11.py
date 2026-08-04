"""F60FED11 RED tests — skip semantic verifier when phase_6 verdict is FAIL.

Agreement F60FED11: when prev.data["verdict"] == "FAIL", the build is heading
to fix-iteration; running semantic verification wastes ~$0.025 × N findings + ~6 min.
Spec: skip verifier loop, return ok with empty counters when verdict == "FAIL".
PASS and PARTIAL must still run (refute-rate signal matters there).

ALL three tests must be RED against the current production code:
  - Test 1 FAILS: verifier called 3 times instead of 0 (bug we're fixing).
  - Tests 2 + 3 PASS: regression guards confirming PASS/PARTIAL already runs verifier.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier import verify_findings_semantic  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REVIEW_DOC_WITH_3_FINDINGS = (
    "# Build Review\n"
    "\n"
    "## Aggregated Findings\n"
    "\n"
    "### SEVERITY: HIGH — null pointer dereference at line 10\n"
    "> src/core.py:10: obj.value\n"
    "Claim: obj is not validated before access.\n"
    "\n"
    "### SEVERITY: HIGH — unguarded index access at line 42\n"
    "> src/util.py:42: items[idx]\n"
    "Claim: idx may exceed len(items).\n"
    "\n"
    "### SEVERITY: MEDIUM — missing error handler at line 77\n"
    "> src/handler.py:77: risky_call()\n"
    "Claim: exception propagates to caller unchecked.\n"
    "\n"
)

_REPRODUCED_RESPONSE = (
    "REPRODUCED:\n"
    "file: src/core.py:10\n"
    "repro: pytest tests/test_core.py -v\n"
    "repro: grep -n 'obj.value' src/core.py\n"
    "evidence: line 10 accesses obj.value without None check.\n"
)


def _make_prev(verdict: str, review_doc_path: str) -> StepResult:
    """Build a StepResult-like prev with the given verdict and review_doc_path."""
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "review_doc_path": review_doc_path,
            "verify_verified": 3,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )


# ---------------------------------------------------------------------------
# Test 1: FAIL verdict skips the verifier entirely
# ---------------------------------------------------------------------------


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_fail_verdict_skips_semantic_verifier(mock_agent, tmp_path):
    """verdict==FAIL + only LOW/MED findings → verifier not called.

    Counters all zero; doc path forwarded. After C2565639 refinement the skip
    is severity-aware: this test exercises the skip path with a fixture that
    has no CRITICAL/HIGH findings. (See test_semantic_verify_runs_on_fail_when_critical_present
    for the FAIL+HIGH case where verifier MUST run.)
    """
    review_doc = tmp_path / "review_fail.md"
    review_doc.write_text(_REVIEW_DOC_LOW_MED_ONLY, encoding="utf-8")

    # Provide a real string return value so the function doesn't crash on
    # parse_verdict before reaching the assertion; the test still fails because
    # call_count will be 3 (current code ignores verdict) not 0.
    mock_agent.return_value = _REPRODUCED_RESPONSE

    prev = _make_prev("FAIL", str(review_doc))
    result = verify_findings_semantic(ctx=None, prev=prev)

    # Verifier subagent must NOT be invoked when build is already failing.
    assert mock_agent.call_count == 0, (
        f"expected 0 verifier calls on FAIL verdict, got {mock_agent.call_count}"
    )

    assert isinstance(result, StepResult)
    assert result.status == "ok"

    # All semantic counters must be zero (nothing was checked).
    assert result.data["semantic_reproduced"] == 0
    assert result.data["semantic_refuted"] == 0
    assert result.data["semantic_unverified"] == 0
    assert result.data["semantic_skipped"] == 0

    # Fields needed by downstream steps must be forwarded.
    assert "review_doc_path" in result.data
    assert "verdict" in result.data
    assert result.data["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# Test 2: PASS verdict still runs the verifier
# ---------------------------------------------------------------------------


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_pass_verdict_still_runs_semantic_verifier(mock_agent, tmp_path):
    """verdict==PASS → verifier runs for each finding; 3 findings = 3 calls.

    Regression guard: PASS must continue to run semantic verification because
    refute-rate is the key signal for PASS builds.
    """
    review_doc = tmp_path / "review_pass.md"
    review_doc.write_text(_REVIEW_DOC_WITH_3_FINDINGS, encoding="utf-8")

    # Return REPRODUCED for each of the 3 findings.
    mock_agent.return_value = _REPRODUCED_RESPONSE

    prev = _make_prev("PASS", str(review_doc))
    result = verify_findings_semantic(ctx=None, prev=prev)

    assert mock_agent.call_count == 3, (
        f"expected 3 verifier calls on PASS verdict, got {mock_agent.call_count}"
    )

    assert isinstance(result, StepResult)
    assert result.status == "ok"
    assert result.data["semantic_reproduced"] == 3


# ---------------------------------------------------------------------------
# Test 3: PARTIAL verdict still runs the verifier
# ---------------------------------------------------------------------------


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_partial_verdict_still_runs_semantic_verifier(mock_agent, tmp_path):
    """verdict==PARTIAL → verifier runs for each finding; 3 findings = 3 calls.

    Regression guard: PARTIAL must continue to run semantic verification;
    refute-rate differentiates a strong PARTIAL from a near-FAIL.
    """
    review_doc = tmp_path / "review_partial.md"
    review_doc.write_text(_REVIEW_DOC_WITH_3_FINDINGS, encoding="utf-8")

    mock_agent.return_value = _REPRODUCED_RESPONSE

    prev = _make_prev("PARTIAL", str(review_doc))
    result = verify_findings_semantic(ctx=None, prev=prev)

    assert mock_agent.call_count == 3, (
        f"expected 3 verifier calls on PARTIAL verdict, got {mock_agent.call_count}"
    )

    assert isinstance(result, StepResult)
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# C2565639 RED: FAIL verdict + CRITICAL/HIGH findings → MUST run verifier
# ---------------------------------------------------------------------------

# Fixture with only LOW/MEDIUM severities (no CRIT/HIGH).
_REVIEW_DOC_LOW_MED_ONLY = (
    "# Build Review\n"
    "\n"
    "## Aggregated Findings\n"
    "\n"
    "### SEVERITY: MEDIUM — missing error handler at line 77\n"
    "> src/handler.py:77: risky_call()\n"
    "Claim: exception propagates to caller unchecked.\n"
    "\n"
    "### SEVERITY: LOW — verbose logging at line 11\n"
    "> src/log.py:11: logger.debug(...)\n"
    "Claim: noisy debug log left enabled.\n"
    "\n"
)


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_semantic_verify_runs_on_fail_when_critical_present(mock_agent, tmp_path):
    """C2565639 refinement: skip ONLY when verdict==FAIL AND no CRIT/HIGH findings.

    The existing _REVIEW_DOC_WITH_3_FINDINGS has 2 HIGH findings, so even with
    verdict==FAIL the verifier MUST run — fabricated CRIT/HIGH findings must be
    refuted at this gate, not propagated into expensive fix-iteration.

    RED: currently fails because production code unconditionally skips on FAIL
    (line 320). Expected 3 verifier calls, got 0.
    """
    review_doc = tmp_path / "review_fail_with_high.md"
    review_doc.write_text(_REVIEW_DOC_WITH_3_FINDINGS, encoding="utf-8")

    mock_agent.return_value = _REPRODUCED_RESPONSE

    prev = _make_prev("FAIL", str(review_doc))
    result = verify_findings_semantic(ctx=None, prev=prev)

    assert mock_agent.call_count == 3, (
        f"expected 3 verifier calls on FAIL+HIGH, got {mock_agent.call_count}"
    )

    assert isinstance(result, StepResult)
    assert result.status == "ok"
    assert result.data["semantic_reproduced"] == 3


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_fail_verdict_with_only_low_medium_findings_still_skips(mock_agent, tmp_path):
    """C2565639 regression guard: skip is preserved when no CRIT/HIGH findings.

    verdict==FAIL with only MEDIUM/LOW findings → still skip (cost not justified
    for low-severity findings). Currently passes because the unconditional skip
    swallows this case too; will continue to pass after refinement adds the
    severity check.
    """
    review_doc = tmp_path / "review_fail_low_med.md"
    review_doc.write_text(_REVIEW_DOC_LOW_MED_ONLY, encoding="utf-8")

    mock_agent.return_value = _REPRODUCED_RESPONSE

    prev = _make_prev("FAIL", str(review_doc))
    result = verify_findings_semantic(ctx=None, prev=prev)

    assert mock_agent.call_count == 0, (
        f"expected 0 verifier calls on FAIL with only LOW/MED, got {mock_agent.call_count}"
    )

    assert isinstance(result, StepResult)
    assert result.status == "ok"
