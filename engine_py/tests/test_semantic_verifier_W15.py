"""W15 RED tests — semantic verifier for phase_6_review findings.

Closes agreement FE06CB65 HIGH per W14 spec at:
    SHARED/memory/Decisions/2026-04-30_W14_semantic_verifier_spec.md

ALL tests below MUST fail with ``NotImplementedError("W15 GREEN")`` against the
W15 stub. GREEN must (a) implement ``parse_verdict`` last-marker-wins parsing
matching the W11 line-anchor regex, (b) implement
``verify_findings_semantic`` step that reads ``review_doc_path`` from
``prev.data``, spawns one independent subagent per finding via
``_invoke_verifier_agent`` (mocked here), demotes REFUTED findings to a
``## Refuted (Semantic)`` section, caps total findings at
``MAX_SEMANTIC_VERIFY_FINDINGS``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier import (  # noqa: E402
    MAX_SEMANTIC_VERIFY_FINDINGS,
    _invoke_verifier_agent,
    parse_verdict,
    verify_findings_semantic,
)


# -------------------------- Parser tests --------------------------


def test_parse_verdict_reproduced_well_formed():
    """REPRODUCED marker with file/repro(x2)/evidence keys parses cleanly."""
    raw = (
        "REPRODUCED:\n"
        "file: foo/bar.py:42\n"
        "repro: pytest tests/test_foo.py -v\n"
        "repro: grep -n 'parents\\[4\\]' foo/bar.py\n"
        "evidence: line 42 calls parents[4] on a depth-3 path\n"
    )
    result = parse_verdict(raw)
    assert result["verdict"] == "REPRODUCED"
    assert result["file"] == "foo/bar.py:42"
    assert isinstance(result["repros"], list)
    assert len(result["repros"]) == 2
    assert result["evidence"] == "line 42 calls parents[4] on a depth-3 path"


def test_parse_verdict_refuted_well_formed():
    """REFUTED marker with reason/rationale keys parses cleanly."""
    raw = (
        "REFUTED:\n"
        "reason: guard exists upstream at line 38\n"
        "rationale: caller validates depth >= 5 before reaching this branch\n"
    )
    result = parse_verdict(raw)
    assert result["verdict"] == "REFUTED"
    assert result["reason"] == "guard exists upstream at line 38"
    assert (
        result["rationale"]
        == "caller validates depth >= 5 before reaching this branch"
    )


def test_parse_verdict_unverified_well_formed():
    """UNVERIFIED marker with reason key parses cleanly."""
    raw = "UNVERIFIED:\nreason: cannot_decide\n"
    result = parse_verdict(raw)
    assert result["verdict"] == "UNVERIFIED"
    assert result["reason"] == "cannot_decide"


def test_parse_verdict_no_marker_returns_unverified():
    """No verdict marker → UNVERIFIED with reason no_verdict_marker."""
    raw = "this is just prose with no marker line\n"
    result = parse_verdict(raw)
    assert result["verdict"] == "UNVERIFIED"
    assert result["reason"] == "no_verdict_marker"


def test_parse_verdict_last_marker_wins():
    """Multiple markers → take the last one (W11 line-anchor pattern)."""
    raw = (
        "REFUTED:\n"
        "reason: initial false reading\n"
        "rationale: thought guard existed\n"
        "\n"
        "On second look, the guard does not cover this path:\n"
        "REPRODUCED:\n"
        "file: foo/bar.py:42\n"
        "repro: pytest tests/test_foo.py\n"
        "repro: python -c 'import foo.bar'\n"
        "evidence: guard is upstream of a different branch\n"
    )
    result = parse_verdict(raw)
    assert result["verdict"] == "REPRODUCED"


def test_parse_verdict_malformed_required_key_demotes_to_unverified():
    """REPRODUCED missing required ``evidence:`` → UNVERIFIED malformed_output."""
    raw = (
        "REPRODUCED:\n"
        "file: foo/bar.py:42\n"
        "repro: pytest tests/test_foo.py\n"
    )
    result = parse_verdict(raw)
    assert result["verdict"] == "UNVERIFIED"
    assert result["reason"] == "malformed_output"


# -------------------------- Step tests --------------------------


def test_verify_findings_semantic_returns_error_on_missing_prev_data():
    """prev not StepResult → E_MISSING_PREV_DATA."""

    class _BadPrev:
        pass

    result = verify_findings_semantic(ctx=None, prev=_BadPrev())
    assert isinstance(result, StepResult)
    assert result.status == "error"
    assert result.error_code == "E_MISSING_PREV_DATA"


def test_verify_findings_semantic_returns_error_on_missing_review_doc_path():
    """prev.data lacking review_doc_path → E_MISSING_REVIEW_DOC_PATH."""
    prev = StepResult(
        status="ok",
        data={"verify_verified": 1},
        duration_ms=0,
        step_name="verify_findings",
    )
    result = verify_findings_semantic(ctx=None, prev=prev)
    assert isinstance(result, StepResult)
    assert result.status == "error"
    assert result.error_code == "E_MISSING_REVIEW_DOC_PATH"


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_demotes_refuted_finding_to_section(
    mock_agent, tmp_path
):
    """A REFUTED finding moves to '## Refuted (Semantic)' section."""
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — guard missing on parents[4]\n"
        "> foo/bar.py:42: parents[4]\n"
        "Claim: indexing without guard.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.return_value = (
        "REFUTED:\n"
        "reason: guard exists\n"
        "rationale: caller validates depth >= 5 upstream\n"
    )
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    assert result.status == "ok"
    assert result.data["semantic_refuted"] == 1
    assert result.data["semantic_reproduced"] == 0
    rewritten = review_doc.read_text(encoding="utf-8")
    assert "## Refuted (Semantic)" in rewritten
    # Original finding should appear in the Refuted section, not Aggregated.
    refuted_idx = rewritten.index("## Refuted (Semantic)")
    assert "guard missing on parents[4]" in rewritten[refuted_idx:]


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_caps_findings_at_max(mock_agent, tmp_path):
    """>20 findings: first 20 verified, last 5 tagged [UNVERIFIED OVERFLOW]."""
    n_findings = 25
    parts = ["# Build Review\n", "\n", "## Aggregated Findings\n", "\n"]
    for i in range(n_findings):
        parts.append(f"### SEVERITY: HIGH — finding number {i}\n")
        parts.append(f"> src/mod_{i}.py:10: dummy_call({i})\n")
        parts.append(f"Claim: bug {i}.\n")
        parts.append("\n")
    review_doc = tmp_path / "review.md"
    review_doc.write_text("".join(parts), encoding="utf-8")

    # Mock returns UNVERIFIED for all calls so we can isolate overflow accounting.
    mock_agent.return_value = "UNVERIFIED:\nreason: cannot_decide\n"

    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": n_findings,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    assert mock_agent.call_count == MAX_SEMANTIC_VERIFY_FINDINGS
    overflow = n_findings - MAX_SEMANTIC_VERIFY_FINDINGS
    rewritten = review_doc.read_text(encoding="utf-8")
    assert rewritten.count("[UNVERIFIED OVERFLOW]") == overflow
    # Overflow findings count toward semantic_unverified.
    assert result.data["semantic_unverified"] >= overflow


# -------------------------- Opus REVISE coverage tests (11-17) --------------------------


def test_verify_findings_semantic_returns_error_on_unreadable_review_doc():
    """review_doc_path points at a nonexistent file → E_SEMANTIC_VERIFY_READ_FAILED.

    Mirrors helper.py:134-150 — verify_findings raises E_VERIFY_READ_FAILED on
    FileNotFoundError/OSError; semantic verifier should expose its own code so
    callers can distinguish which step failed.
    """
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": "/tmp/does_not_exist_W15_semantic_verifier.md",
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )
    result = verify_findings_semantic(ctx=None, prev=prev)
    assert isinstance(result, StepResult)
    assert result.status == "error"
    assert result.error_code == "E_SEMANTIC_VERIFY_READ_FAILED"


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_skips_uncited_findings(mock_agent, tmp_path):
    """An [UNCITED] finding is skipped (not sent to verifier agent)."""
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — uncited claim about parents[4] [UNCITED]\n"
        "Claim: indexing without guard.\n"
        "\n",
        encoding="utf-8",
    )
    # If GREEN incorrectly invokes for an UNCITED finding, this would be the
    # response; the assertions below ensure it never gets called.
    mock_agent.return_value = "REFUTED:\nreason: should_not_be_called\nrationale: x\n"
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 0,
            "verify_unverified": 0,
            "verify_uncited": 1,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    assert mock_agent.call_count == 0
    assert result.data["semantic_skipped"] >= 1


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_tags_unverified_finding_severity(
    mock_agent, tmp_path
):
    """UNVERIFIED verdict → finding stays in place but severity gets [UNVERIFIED] prefix."""
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — ambiguous claim near parents[4]\n"
        "> foo/bar.py:42: parents[4]\n"
        "Claim: maybe a bug, hard to say.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.return_value = "UNVERIFIED:\nreason: cannot_verify\n"
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    assert result.status == "ok"
    rewritten = review_doc.read_text(encoding="utf-8")
    assert "[UNVERIFIED] HIGH" in rewritten
    assert result.data["semantic_unverified"] >= 1


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_refuted_finding_removed_from_aggregated_section(
    mock_agent, tmp_path
):
    """REFUTED finding leaves Aggregated; section ordering preserved.

    Aggregated section retains only REPRODUCED finding; REFUTED moves to
    ## Refuted (Semantic). C1 fix: ordering is
    Aggregated < Refuted < Unverified (Refuted appears between them, by
    signal-priority — verified findings first, demoted next, low-trust last).
    """
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — refuted_finding_header_text\n"
        "> foo/bar.py:42: parents[4]\n"
        "Claim: bug A.\n"
        "\n"
        "### SEVERITY: MEDIUM — reproduced_finding_header_text\n"
        "> foo/baz.py:17: dangerous_call()\n"
        "Claim: bug B.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.side_effect = [
        # First finding (HIGH) — refuted.
        "REFUTED:\nreason: guard exists\nrationale: caller validates depth.\n",
        # Second finding (MEDIUM) — reproduced.
        "REPRODUCED:\n"
        "file: foo/baz.py:17\n"
        "repro: pytest tests/test_baz.py -v\n"
        "repro: grep -n 'dangerous_call' foo/baz.py\n"
        "evidence: line 17 invokes dangerous_call() with unchecked input.\n",
    ]
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 2,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)
    assert isinstance(result, StepResult)
    assert result.status == "ok"

    rewritten = review_doc.read_text(encoding="utf-8")

    # Section presence + ordering: Aggregated section before Refuted (C1 fix).
    refuted_idx = rewritten.index("## Refuted (Semantic)")
    agg_idx = rewritten.index("## Aggregated Findings")
    assert agg_idx < refuted_idx

    # Refuted finding header gone from the Aggregated section content slice.
    # Aggregated section runs from agg_idx until next "## " (or EOF).
    after_agg = rewritten[agg_idx + len("## Aggregated Findings"):]
    next_h2 = after_agg.find("\n## ")
    agg_section_body = after_agg if next_h2 == -1 else after_agg[:next_h2]
    assert "refuted_finding_header_text" not in agg_section_body
    assert "reproduced_finding_header_text" in agg_section_body


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_haiku_to_opus_fallback_on_cannot_decide(
    mock_agent, tmp_path
):
    """Haiku UNVERIFIED:cannot_decide → Opus retry; Opus REFUTED counts."""
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — guard ambiguity at line 12\n"
        "> foo/bar.py:12: branch_call()\n"
        "Claim: branch executes without guard.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.side_effect = [
        "UNVERIFIED:\nreason: cannot_decide\n",
        "REFUTED:\nreason: guard exists\n"
        "rationale: the early-return at line 12 prevents this branch\n",
    ]
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)
    assert isinstance(result, StepResult)
    assert mock_agent.call_count == 2

    # Inspect the second call: model_tier should be "opus" (kwarg or 2nd positional).
    second_call = mock_agent.call_args_list[1]
    tier = second_call.kwargs.get("model_tier")
    if tier is None and len(second_call.args) >= 2:
        tier = second_call.args[1]
    assert tier == "opus", (
        f"second call must escalate to opus; got tier={tier!r}, "
        f"args={second_call.args!r}, kwargs={second_call.kwargs!r}"
    )

    assert result.data["semantic_refuted"] == 1


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_refute_rate_no_division_by_zero_when_all_unverified(
    mock_agent, tmp_path
):
    """All findings UNVERIFIED → semantic_refute_rate == 0.0, no ZeroDivisionError."""
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — finding the verifier cannot resolve\n"
        "> foo/bar.py:99: opaque_call()\n"
        "Claim: maybe a bug.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.return_value = "UNVERIFIED:\nreason: timeout\n"
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    # The whole point: GREEN must guard the divisor — no ZeroDivisionError.
    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    assert result.data["semantic_refute_rate"] == 0.0
    assert result.data["semantic_reproduced"] == 0
    assert result.data["semantic_refuted"] == 0
    assert result.data["semantic_unverified"] >= 1


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier.EventLog", create=True)
@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_verify_findings_semantic_emits_telemetry_event(
    mock_agent, mock_event_log_cls, tmp_path
):
    """End of step emits one telemetry event whose type contains 'semantic_verify'.

    Patching strategy: ``semantic_verifier.EventLog`` is patched with
    ``create=True`` so the test does not require GREEN to have imported it yet
    (the test will still fail on NotImplementedError before emit logic runs in
    RED). GREEN is expected to import ``from event_log import EventLog`` and
    instantiate it (or use a module-level singleton); we inspect the mock class
    instance's ``.append`` calls for the event_type. Per W14 spec §7 the exact
    event_type is ``phase_6_semantic_verify_complete`` but we accept any string
    containing ``semantic_verify`` to give GREEN flexibility. Assumption: GREEN
    invokes ``EventLog().append(event_type, payload, run_id=...)`` rather than
    a free function.
    """
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — verified bug at line 4\n"
        "> foo/bar.py:4: real_call()\n"
        "Claim: real bug.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.return_value = (
        "REFUTED:\nreason: guard exists\nrationale: real_call is wrapped.\n"
    )
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 1,
            "verify_unverified": 0,
            "verify_uncited": 0,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)

    assert isinstance(result, StepResult)
    # EventLog() is called somewhere; collect every .append() event_type
    # across instance + class-level mock_calls so we are agnostic to whether
    # GREEN uses a fresh instance, a singleton, or class-level append.
    instance_append = mock_event_log_cls.return_value.append
    seen_event_types: list[str] = []
    for call in instance_append.call_args_list:
        if call.args:
            seen_event_types.append(str(call.args[0]))
        elif "event_type" in call.kwargs:
            seen_event_types.append(str(call.kwargs["event_type"]))
    assert any("semantic_verify" in et for et in seen_event_types), (
        f"expected a 'semantic_verify' event; saw event_types={seen_event_types!r}"
    )


# -------------------------- Code-review fix tests (18-19) --------------------------


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier._invoke_verifier_agent")
def test_section_placement_aggregated_first_refuted_middle_unverified_last(
    mock_agent, tmp_path
):
    """C1 fix: section ordering is Aggregated < Refuted < Unverified.

    Step 4 (helper.py) appends ``## Unverified Findings (Auto-Filtered)`` at
    end of doc. W15 must insert ``## Refuted (Semantic)`` between Aggregated
    and Unverified — by signal-priority (verified → demoted → low-trust).
    """
    review_doc = tmp_path / "review.md"
    review_doc.write_text(
        "# Build Review\n"
        "\n"
        "## Aggregated Findings\n"
        "\n"
        "### SEVERITY: HIGH — first finding to be refuted\n"
        "> foo/bar.py:42: parents[4]\n"
        "Claim: bug A.\n"
        "\n"
        "### SEVERITY: MEDIUM — second finding reproduced\n"
        "> foo/baz.py:17: dangerous_call()\n"
        "Claim: bug B.\n"
        "\n"
        "## Unverified Findings (Auto-Filtered)\n"
        "\n"
        "### SEVERITY: LOW — pre-existing low-trust [UNCITED]\n"
        "Claim: low-trust claim.\n"
        "\n",
        encoding="utf-8",
    )
    mock_agent.side_effect = [
        # First finding — REFUTED.
        "REFUTED:\nreason: guard exists\nrationale: caller validates depth.\n",
        # Second finding — REPRODUCED.
        "REPRODUCED:\n"
        "file: foo/baz.py:17\n"
        "repro: pytest tests/test_baz.py -v\n"
        "repro: grep -n 'dangerous_call' foo/baz.py\n"
        "evidence: line 17 invokes dangerous_call() with unchecked input.\n",
    ]
    prev = StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_doc),
            "verify_verified": 2,
            "verify_unverified": 0,
            "verify_uncited": 1,
            "verify_unparseable": 0,
        },
        duration_ms=0,
        step_name="verify_findings",
    )

    result = verify_findings_semantic(ctx=None, prev=prev)
    assert isinstance(result, StepResult)
    assert result.status == "ok"

    rewritten = review_doc.read_text(encoding="utf-8")
    agg_idx = rewritten.index("## Aggregated Findings")
    ref_idx = rewritten.index("## Refuted (Semantic)")
    unv_idx = rewritten.index("## Unverified Findings")
    assert agg_idx < ref_idx < unv_idx, (
        f"order: agg={agg_idx} ref={ref_idx} unv={unv_idx}"
    )


@patch("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier.subprocess.run")
def test_invoke_verifier_agent_sanitises_stderr_to_prevent_reason_collision(
    mock_run,
):
    """H1 fix: multi-line stderr cannot inject a second ``reason:`` line.

    Without sanitisation, attacker-controlled stderr containing
    ``\\nreason: malicious\\n`` would interpolate into the synthetic
    UNVERIFIED block and parse_verdict's last-key-wins kv loop would
    overwrite the legitimate ``reason``. After sanitisation (newlines→space,
    colons stripped) only one ``reason:`` line is possible.
    """
    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "first line\nreason: injected_overwrite\nthird line"
    fake.stdout = ""
    mock_run.return_value = fake

    raw = _invoke_verifier_agent(
        {
            "file": "x.py",
            "line": 1,
            "quote": "q",
            "claim": "c",
            "severity": "HIGH",
        }
    )

    # Only ONE line should start with "reason:" — no injected second one.
    reason_lines = [ln for ln in raw.split("\n") if ln.strip().startswith("reason:")]
    assert len(reason_lines) == 1, (
        f"expected exactly 1 reason: line, got {len(reason_lines)}: {reason_lines}"
    )

    parsed = parse_verdict(raw)
    assert parsed["verdict"] == "UNVERIFIED"
    # Legitimate reason is preserved (starts with agent_error); injection text
    # has its colon stripped so it cannot reappear as a separate key.
    assert "agent_error" in parsed["reason"]
