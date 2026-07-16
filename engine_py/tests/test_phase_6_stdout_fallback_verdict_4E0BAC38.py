"""RED-phase tests for 4E0BAC38 — phase_6 stdout-fallback verdict derives from
structured findings, never auto-PASS.

AC mapping:
    test_ac1_derive_fallback_verdict_importable       → AC1
    test_ac2_critical_high_finding_yields_fail        → AC2
    test_ac3_medium_low_finding_yields_partial        → AC3
    test_ac4_empty_structured_block_yields_pass       → AC4
    test_ac5_no_block_prose_pass_yields_suspect       → AC5
    test_ac6_no_block_prose_fail_partial_unknown      → AC6
    test_ac7_malformed_json_block_treated_as_no_block → AC7
    test_ac8_write_artifact_fallback_high_yields_fail → AC8
    test_ac9_write_artifact_fallback_empty_yields_pass → AC9
    test_ac10_normal_aggregated_path_regression_guard → AC10 (regression guard)
    test_ac11_telemetry_event_fires_on_fallback_path  → AC11
    test_ac12_parse_review_verdict_still_works        → AC12 (regression guard)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_conformant_review(structured_block: str, prose_verdict: str = "") -> str:
    """Build a minimal conformant composite review (passes _is_review_conformant).

    _is_review_conformant requires a line exactly '## Aggregated Findings'.
    The structured block and optional prose VERDICT line are appended after it.
    """
    parts = [
        "# Composite Review\n",
        "\n",
        "## Aggregated Findings\n",
        "\n",
        structured_block,
    ]
    if prose_verdict:
        parts.append(f"\n{prose_verdict}\n")
    return "".join(parts)


# ─── AC1: _derive_fallback_verdict is importable ──────────────────────────────


def test_ac1_derive_fallback_verdict_importable():
    """AC1: _derive_fallback_verdict exists in phase_6_review and is importable."""
    # This import will raise ImportError / AttributeError against current code
    # because _derive_fallback_verdict does not yet exist.
    from phase_6_review import _derive_fallback_verdict  # noqa: F401

    assert callable(_derive_fallback_verdict), "_derive_fallback_verdict must be callable"


# ─── AC2: CRITICAL/HIGH → VERDICT_FAIL (ignores prose VERDICT: PASS) ──────────


def test_ac2_critical_high_finding_yields_fail():
    """AC2: structured block with CRITICAL (or HIGH) finding → VERDICT_FAIL,
    even if prose contains VERDICT: PASS."""
    from phase_6_review import _derive_fallback_verdict, VERDICT_FAIL

    # CRITICAL + prose PASS
    block_crit = (
        '## Findings (structured)\n'
        '```json\n'
        '[{"id": "F1", "severity": "CRITICAL", "path": "foo.py:1", "description": "bad"}]\n'
        '```\n'
    )
    content_crit = _make_conformant_review(block_crit, prose_verdict="VERDICT: PASS")
    assert _derive_fallback_verdict(content_crit) == VERDICT_FAIL, (
        "CRITICAL finding must yield VERDICT_FAIL even when prose says VERDICT: PASS"
    )

    # HIGH + prose PASS
    block_high = (
        '## Findings (structured)\n'
        '```json\n'
        '[{"id": "F2", "severity": "HIGH", "path": "bar.py:5", "description": "also bad"}]\n'
        '```\n'
    )
    content_high = _make_conformant_review(block_high, prose_verdict="VERDICT: PASS")
    assert _derive_fallback_verdict(content_high) == VERDICT_FAIL, (
        "HIGH finding must yield VERDICT_FAIL even when prose says VERDICT: PASS"
    )


# ─── AC3: MEDIUM/LOW → VERDICT_PARTIAL ────────────────────────────────────────


def test_ac3_medium_low_finding_yields_partial():
    """AC3: structured block with only MEDIUM/LOW findings → VERDICT_PARTIAL,
    even if prose says VERDICT: PASS."""
    from phase_6_review import _derive_fallback_verdict, VERDICT_PARTIAL

    # MEDIUM + prose PASS
    block_med = (
        '## Findings (structured)\n'
        '```json\n'
        '[{"id": "F3", "severity": "MEDIUM", "path": "baz.py:10", "description": "medium issue"}]\n'
        '```\n'
    )
    content_med = _make_conformant_review(block_med, prose_verdict="VERDICT: PASS")
    assert _derive_fallback_verdict(content_med) == VERDICT_PARTIAL, (
        "MEDIUM finding must yield VERDICT_PARTIAL even when prose says VERDICT: PASS"
    )

    # LOW + prose PASS
    block_low = (
        '## Findings (structured)\n'
        '```json\n'
        '[{"id": "F4", "severity": "LOW", "path": "qux.py:2", "description": "low issue"}]\n'
        '```\n'
    )
    content_low = _make_conformant_review(block_low, prose_verdict="VERDICT: PASS")
    assert _derive_fallback_verdict(content_low) == VERDICT_PARTIAL, (
        "LOW finding must yield VERDICT_PARTIAL even when prose says VERDICT: PASS"
    )


# ─── AC4: empty structured block → VERDICT_PASS ───────────────────────────────


def test_ac4_empty_structured_block_yields_pass():
    """AC4: structured block = [] (reviewer reported zero findings) → VERDICT_PASS."""
    from phase_6_review import _derive_fallback_verdict, VERDICT_PASS

    block_empty = (
        '## Findings (structured)\n'
        '```json\n'
        '[]\n'
        '```\n'
    )
    content = _make_conformant_review(block_empty)
    assert _derive_fallback_verdict(content) == VERDICT_PASS, (
        "Empty structured findings list must yield VERDICT_PASS"
    )


# ─── AC5: no structured block + prose PASS → VERDICT_SUSPECT ─────────────────


def test_ac5_no_block_prose_pass_yields_suspect():
    """AC5: no ## Findings (structured) block, prose VERDICT: PASS → VERDICT_SUSPECT
    (never auto-PASS on the anomalous fallback path)."""
    from phase_6_review import _derive_fallback_verdict, VERDICT_SUSPECT

    content = _make_conformant_review("", prose_verdict="VERDICT: PASS")
    assert _derive_fallback_verdict(content) == VERDICT_SUSPECT, (
        "No structured block + prose VERDICT: PASS must yield VERDICT_SUSPECT, not PASS"
    )


# ─── AC6: no structured block + various prose verdicts ────────────────────────


def test_ac6_no_block_prose_fail_partial_unknown():
    """AC6: no structured block:
    - prose VERDICT: FAIL → VERDICT_FAIL
    - prose VERDICT: PARTIAL → VERDICT_PARTIAL
    - no VERDICT: line → VERDICT_SUSPECT
    """
    from phase_6_review import _derive_fallback_verdict, VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_SUSPECT

    # prose FAIL
    content_fail = _make_conformant_review("", prose_verdict="VERDICT: FAIL")
    assert _derive_fallback_verdict(content_fail) == VERDICT_FAIL, (
        "No structured block + prose VERDICT: FAIL must yield VERDICT_FAIL"
    )

    # prose PARTIAL
    content_partial = _make_conformant_review("", prose_verdict="VERDICT: PARTIAL")
    assert _derive_fallback_verdict(content_partial) == VERDICT_PARTIAL, (
        "No structured block + prose VERDICT: PARTIAL must yield VERDICT_PARTIAL"
    )

    # no VERDICT line at all → VERDICT_SUSPECT
    content_none = _make_conformant_review("", prose_verdict="")
    assert _derive_fallback_verdict(content_none) == VERDICT_SUSPECT, (
        "No structured block + no VERDICT line must yield VERDICT_SUSPECT"
    )


# ─── AC7: malformed JSON block → treated as "no structured block" ─────────────


def test_ac7_malformed_json_block_treated_as_no_block():
    """AC7: a ## Findings (structured) block with invalid JSON is treated as absent;
    falls back to prose-verdict path (PASS → SUSPECT)."""
    from phase_6_review import _derive_fallback_verdict, VERDICT_SUSPECT

    block_bad_json = (
        '## Findings (structured)\n'
        '```json\n'
        '{not valid json at all!\n'
        '```\n'
    )
    content = _make_conformant_review(block_bad_json, prose_verdict="VERDICT: PASS")
    # Malformed block → no structured data → prose PASS → VERDICT_SUSPECT
    assert _derive_fallback_verdict(content) == VERDICT_SUSPECT, (
        "Malformed structured block must be treated as absent; VERDICT: PASS must downgrade to SUSPECT"
    )


# ─── AC8: _write_review_artifact fallback path, HIGH finding → VERDICT_FAIL ──


def test_ac8_write_artifact_fallback_high_yields_fail(tmp_path):
    """AC8: _write_review_artifact via stdout-fallback path with raw_response
    containing a HIGH structured finding and prose VERDICT: PASS →
    StepResult.data["verdict"] == VERDICT_FAIL."""
    from contracts import StepResult
    from phase_6_review import _write_review_artifact, VERDICT_FAIL

    doc_path = tmp_path / "scratch" / "reviews" / "build-review.md"

    block_high = (
        '## Findings (structured)\n'
        '```json\n'
        '[{"id": "F1", "severity": "HIGH", "path": "src/main.py:42", "description": "serious problem"}]\n'
        '```\n'
    )
    raw_response = _make_conformant_review(block_high, prose_verdict="VERDICT: PASS")

    prev = StepResult(
        status="ok",
        data={
            # no aggregated_content → stdout-fallback path
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "spec_path": "/dev/null",
            "red_log_path": "/dev/null",
            "green_log_path": "/dev/null",
            "prompt": "test prompt",
        },
        duration_ms=0,
        step_name="prev",
    )

    result = _write_review_artifact(None, prev)
    assert result.status == "ok", f"Expected ok, got {result.status}: {result.error}"
    assert result.data["verdict"] == VERDICT_FAIL, (
        f"Expected VERDICT_FAIL for HIGH finding with prose VERDICT: PASS, "
        f"got {result.data['verdict']!r}"
    )


# ─── AC9: fallback path, empty structured block, no prose verdict → VERDICT_PASS


def test_ac9_write_artifact_fallback_empty_yields_pass(tmp_path):
    """AC9: _write_review_artifact via stdout-fallback path with raw_response
    containing an empty structured block [] and no prose VERDICT: line →
    StepResult.data["verdict"] == VERDICT_PASS."""
    from contracts import StepResult
    from phase_6_review import _write_review_artifact, VERDICT_PASS

    doc_path = tmp_path / "scratch" / "reviews" / "build-review.md"

    block_empty = (
        '## Findings (structured)\n'
        '```json\n'
        '[]\n'
        '```\n'
    )
    # No prose VERDICT line
    raw_response = _make_conformant_review(block_empty, prose_verdict="")

    prev = StepResult(
        status="ok",
        data={
            # no aggregated_content → stdout-fallback path
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "spec_path": "/dev/null",
            "red_log_path": "/dev/null",
            "green_log_path": "/dev/null",
            "prompt": "test prompt",
        },
        duration_ms=0,
        step_name="prev",
    )

    result = _write_review_artifact(None, prev)
    assert result.status == "ok", f"Expected ok, got {result.status}: {result.error}"
    assert result.data["verdict"] == VERDICT_PASS, (
        f"Expected VERDICT_PASS for empty structured block, got {result.data['verdict']!r}"
    )


# ─── AC10: normal aggregated_content path is untouched (regression guard) ─────


def test_ac10_normal_aggregated_path_regression_guard(tmp_path):
    """AC10 (regression guard): _write_review_artifact with aggregated_content set
    and verdict == VERDICT_PASS → returned data["verdict"] == VERDICT_PASS.
    Verifies the fix did not bleed into the non-fallback path.
    Expected to PASS against current code too (correctness guard)."""
    from contracts import StepResult
    from phase_6_review import _write_review_artifact, VERDICT_PASS

    doc_path = tmp_path / "r" / "build-review.md"

    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": (
                "# Composite Review\n\n"
                "## Aggregated Findings\n\n"
                "(no findings)\n\n"
                "VERDICT: PASS\n"
            ),
            "doc_path": str(doc_path),
            "spec_path": "/dev/null",
            "red_log_path": "/dev/null",
            "green_log_path": "/dev/null",
            "verdict": VERDICT_PASS,
        },
        duration_ms=0,
        step_name="prev",
    )

    result = _write_review_artifact(None, prev)
    assert result.status == "ok", f"Expected ok, got {result.status}: {result.error}"
    assert result.data["verdict"] == VERDICT_PASS, (
        f"Normal aggregated_content path must still yield VERDICT_PASS, "
        f"got {result.data['verdict']!r}"
    )


# ─── AC11: review_stdout_fallback_verdict telemetry event fires on fallback ───


def test_ac11_telemetry_event_fires_on_fallback_path(tmp_path):
    """AC11: on the stdout-fallback path, a review_stdout_fallback_verdict event
    fires with payload {phase: 6, verdict: <derived>, had_structured_block: bool}."""
    import telemetry_ctx
    from event_log import EventLog
    from contracts import StepResult
    from phase_6_review import _write_review_artifact, VERDICT_FAIL

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(
        event_log=log, run_id="rid-4e0b", step_name="write_review_artifact"
    )
    try:
        doc_path = tmp_path / "scratch" / "reviews" / "build-review.md"

        block_high = (
            '## Findings (structured)\n'
            '```json\n'
            '[{"id": "F1", "severity": "HIGH", "path": "src/x.py:1", "description": "high issue"}]\n'
            '```\n'
        )
        raw_response = _make_conformant_review(block_high, prose_verdict="VERDICT: PASS")

        prev = StepResult(
            status="ok",
            data={
                # no aggregated_content → stdout-fallback path
                "raw_response": raw_response,
                "doc_path": str(doc_path),
                "spec_path": "/dev/null",
                "red_log_path": "/dev/null",
                "green_log_path": "/dev/null",
                "prompt": "test prompt",
            },
            duration_ms=0,
            step_name="prev",
        )

        result = _write_review_artifact(None, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", f"Expected ok result, got {result.status}"

    events = EventLog(log_path).read_all()
    fallback_verdict_events = [
        e for e in events if e["event_type"] == "review_stdout_fallback_verdict"
    ]
    assert len(fallback_verdict_events) == 1, (
        f"Expected exactly 1 review_stdout_fallback_verdict event, "
        f"got {len(fallback_verdict_events)}. All events: {[e['event_type'] for e in events]}"
    )
    payload = fallback_verdict_events[0]["payload"]
    assert payload["phase"] == 6, f"Expected phase==6, got {payload.get('phase')!r}"
    assert payload["verdict"] == VERDICT_FAIL, (
        f"Expected verdict==VERDICT_FAIL in telemetry, got {payload.get('verdict')!r}"
    )
    assert payload["had_structured_block"] is True, (
        f"Expected had_structured_block==True (block present), got {payload.get('had_structured_block')!r}"
    )


# ─── AC12: _parse_review_verdict still works (regression guard) ───────────────


def test_ac12_parse_review_verdict_still_works():
    """AC12 (regression guard): _parse_review_verdict is kept and still returns
    correct values. Expected to PASS against current code too."""
    from phase_6_review import _parse_review_verdict, VERDICT_PASS, VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_UNKNOWN

    assert _parse_review_verdict("VERDICT: PASS\n") == VERDICT_PASS
    assert _parse_review_verdict("VERDICT: FAIL\n") == VERDICT_FAIL
    assert _parse_review_verdict("VERDICT: PARTIAL\n") == VERDICT_PARTIAL
    assert _parse_review_verdict("no verdict line here") == VERDICT_UNKNOWN
