"""RED tests for EECA708D — reject-reason capture mechanism.

Spec: SHARED/memory/Decisions/2026-06-14_EECA708D_reject_reason_capture_spec.md
Agreement: EECA708D  Date: 2026-06-14

All 8 tests MUST FAIL until GREEN ships reject_log.py + two one-line wirings.

DEFERRED-IMPORT RATIONALE (D1CF5FDF / §1q-ext):
reject_log.py does NOT exist yet. A top-level `import bytedigger_engine.reject_log` would make
this file non-collectable, causing a ~30-min engine hang. All imports of the
UUT (reject_log) and the phase workflow modules are therefore placed INSIDE
the test function body so collection succeeds and tests fail at runtime.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

# Engine root on sys.path so phase modules and telemetry_ctx are importable.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))


# ---------------------------------------------------------------------------
# AC1 — record_plan_review_reject writes correct schema
# ---------------------------------------------------------------------------

def test_ac1_record_plan_review_reject_writes_correct_line(tmp_path: Path) -> None:
    """AC1: record_plan_review_reject appends exactly one line; round-trips
    with phase=='phase_45_spec', reason_code=='PLAN_REVIEW_REVISE',
    axes==['§1w'], detail=={'cycle':2,'n_unresolved':3}.
    """
    from bytedigger_engine.reject_log import record_plan_review_reject  # deferred: module absent pre-GREEN
    from bytedigger_engine import telemetry_ctx

    p = tmp_path / "reject-reasons.jsonl"
    telemetry_ctx.set_current_run(event_log=None, run_id="ac1-run", step_name="test")
    try:
        record_plan_review_reject(
            "spec must satisfy §1w requirement and nothing else",
            2,
            3,
            path=p,
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert p.exists(), "reject-reasons.jsonl was not created"
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}"

    record = json.loads(lines[0])
    assert record["phase"] == "phase_45_spec"
    assert record["reason_code"] == "PLAN_REVIEW_REVISE"
    assert record["axes"] == ["§1w"]
    assert record["detail"] == {"cycle": 2, "n_unresolved": 3, "findings_source": "unknown"}


# ---------------------------------------------------------------------------
# AC2 — record_satisfaction_reject writes correct schema
# ---------------------------------------------------------------------------

def test_ac2_record_satisfaction_reject_writes_correct_line(tmp_path: Path) -> None:
    """AC2: record_satisfaction_reject appends one line with phase=='phase_6_review',
    reason_code passed through, axes==['§1o'], detail score/threshold correct.
    """
    from bytedigger_engine.reject_log import record_satisfaction_reject  # deferred
    from bytedigger_engine import telemetry_ctx

    p = tmp_path / "reject-reasons.jsonl"
    telemetry_ctx.set_current_run(event_log=None, run_id="ac2-run", step_name="test")
    try:
        record_satisfaction_reject(
            "BELOW_THRESHOLD",
            "score 62 < 70 (§1o gap)",
            62,
            70,
            path=p,
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert p.exists(), "reject-reasons.jsonl was not created"
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}"

    record = json.loads(lines[0])
    assert record["phase"] == "phase_6_review"
    assert record["reason_code"] == "BELOW_THRESHOLD"
    assert record["axes"] == ["§1o"]
    assert record["detail"]["score"] == 62
    assert record["detail"]["threshold"] == 70


# ---------------------------------------------------------------------------
# AC3 — _extract_axes dedup + empty/None handling
# ---------------------------------------------------------------------------

def test_ac3_extract_axes_dedup_and_edge_cases() -> None:
    """AC3: _extract_axes deduplicates preserving first-seen order; returns []
    for empty string and None.
    """
    from bytedigger_engine.reject_log import _extract_axes  # deferred

    result = _extract_axes("must satisfy §1w and §1o and §1w again; §2.3 too")
    assert result == ["§1w", "§1o", "§2.3"], (
        f"expected ['§1w','§1o','§2.3'], got {result!r}"
    )
    assert _extract_axes("") == [], "empty string should return []"
    assert _extract_axes(None) == [], "None should return []"


# ---------------------------------------------------------------------------
# AC4 — emit_reject_reason is fail-safe on unwritable path
# ---------------------------------------------------------------------------

def test_ac4_emit_reject_reason_failsafe_on_unwritable_path(tmp_path: Path) -> None:
    """AC4: emit_reject_reason returns None and raises NOTHING when the target
    path is unwritable (parent is a regular file, not a directory).
    """
    from bytedigger_engine.reject_log import emit_reject_reason  # deferred

    # Make a regular FILE at the "parent directory" location so any attempt
    # to create the log file at that path (or create parent dirs) will fail.
    bad_parent = tmp_path / "not-a-dir"
    bad_parent.write_text("I am a file, not a directory")
    unwritable = bad_parent / "reject-reasons.jsonl"

    # Must not raise — fail-safe contract
    result = emit_reject_reason(
        "phase_45_spec",
        "PLAN_REVIEW_REVISE",
        ["§1w"],
        {"cycle": 1, "n_unresolved": 1},
        run_id="test-r1",
        path=unwritable,
    )
    assert result is None, f"expected None return, got {result!r}"
    # No line was written (bad_parent is a file, so unwritable can't exist)
    assert not unwritable.exists(), "no file should have been created at unwritable path"


# ---------------------------------------------------------------------------
# AC5 — schema keys + ts format + line byte cap
# ---------------------------------------------------------------------------

def test_ac5_written_line_schema_keys_ts_format_byte_cap(tmp_path: Path) -> None:
    """AC5: every written line has keys exactly {ts,build_id,phase,reason_code,axes,detail};
    ts matches ^\\d{4}-\\d{2}-\\d{2}T.*Z$; line <= 4096 bytes.
    """
    from bytedigger_engine.reject_log import emit_reject_reason  # deferred

    p = tmp_path / "reject-reasons.jsonl"
    emit_reject_reason(
        "phase_45_spec",
        "PLAN_REVIEW_REVISE",
        ["§1w"],
        {"cycle": 1, "n_unresolved": 2},
        run_id="schema-check-run",
        path=p,
    )

    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])

    expected_keys = {"ts", "build_id", "phase", "reason_code", "axes", "detail"}
    assert set(record.keys()) == expected_keys, (
        f"unexpected keys: {set(record.keys()) - expected_keys!r}, "
        f"missing: {expected_keys - set(record.keys())!r}"
    )

    ts = record["ts"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T.*Z$", ts), (
        f"ts {ts!r} does not match ISO-8601 UTC Z pattern"
    )

    raw_line = lines[0]
    assert len(raw_line.encode()) <= 4096, (
        f"line is {len(raw_line.encode())} bytes, exceeds 4096"
    )


# ---------------------------------------------------------------------------
# AC6 — run_id resolution: explicit overrides; omitted+no ctx → build_id is None
# ---------------------------------------------------------------------------

def test_ac6_run_id_explicit_overrides_ctx(tmp_path: Path) -> None:
    """AC6a: emit_reject_reason(..., run_id='r123') → build_id=='r123'."""
    from bytedigger_engine.reject_log import emit_reject_reason  # deferred

    p = tmp_path / "reject-reasons.jsonl"
    emit_reject_reason(
        "phase_45_spec",
        "PLAN_REVIEW_REVISE",
        ["§1w"],
        {},
        run_id="r123",
        path=p,
    )
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["build_id"] == "r123", (
        f"expected build_id=='r123', got {record['build_id']!r}"
    )


def test_ac6_no_run_id_no_ctx_build_id_is_none(tmp_path: Path, monkeypatch) -> None:
    """AC6b (GH497 D1 flip): run_id omitted AND telemetry_ctx.get_current_run()
    returns None → build_id resolves to None and the write is SUPPRESSED
    entirely (no file/row created) — no more build_id=null prod-log rows.
    """
    from bytedigger_engine import telemetry_ctx  # exists pre-GREEN — safe to import at top of fn
    # Monkeypatch infra (not UUT) to return None
    monkeypatch.setattr(telemetry_ctx, "get_current_run", lambda: None)

    from bytedigger_engine.reject_log import emit_reject_reason  # deferred

    p = tmp_path / "reject-reasons.jsonl"
    emit_reject_reason(
        "phase_6_review",
        "BELOW_THRESHOLD",
        ["§1o"],
        {"score": 62, "threshold": 70},
        # run_id intentionally omitted
        path=p,
    )
    assert not p.exists(), (
        f"expected no file to be created (build_id-null suppression), but {p} exists"
    )


# ---------------------------------------------------------------------------
# AC7 — phase_45_spec.py wiring: imports + call inside REVISE block
# ---------------------------------------------------------------------------

def test_ac7_phase_45_spec_wired_with_record_plan_review_reject() -> None:
    """AC7: phase_45_spec.py must import record_plan_review_reject AND contain
    a call to record_plan_review_reject( within ~12 lines after the existing
    _emit_safe('phase_45_spec_revise' call (i.e. inside the REVISE block).

    Deterministic source-grep forcing function (§1y §1aa): this test reads the
    production source and asserts structural properties. Fails pre-GREEN (not
    wired yet). Passes post-GREEN.
    """
    phase_45_path = _ENGINE_ROOT / "bytedigger_engine" / "workflows" / "phase_45_spec.py"
    assert phase_45_path.exists(), f"phase_45_spec.py not found at {phase_45_path}"

    source = phase_45_path.read_text()

    # 1. Module must import the helper
    assert "record_plan_review_reject" in source, (
        "phase_45_spec.py does not import or reference record_plan_review_reject"
    )

    # 2. The call must appear within 12 lines after the _emit_safe("phase_45_spec_revise" anchor
    lines = source.splitlines()
    anchor_idx = None
    for i, line in enumerate(lines):
        if 'phase_45_spec_revise' in line:
            anchor_idx = i
            break
    assert anchor_idx is not None, (
        "Could not find anchor line _emit_safe('phase_45_spec_revise' in phase_45_spec.py"
    )

    window = lines[anchor_idx: anchor_idx + 13]  # anchor + 12 following lines
    window_text = "\n".join(window)
    assert "record_plan_review_reject(" in window_text, (
        f"record_plan_review_reject( not found within 12 lines after "
        f"_emit_safe('phase_45_spec_revise') (lines {anchor_idx}–{anchor_idx+12}):\n"
        f"{window_text}"
    )


# ---------------------------------------------------------------------------
# AC8 — phase_6_review.py wiring: imports + call before E_SATISFACTION_BELOW_THRESHOLD
# ---------------------------------------------------------------------------

def test_ac8_phase_6_review_wired_with_record_satisfaction_reject() -> None:
    """AC8: phase_6_review.py must import record_satisfaction_reject AND contain
    a call to record_satisfaction_reject( before the error_code='E_SATISFACTION_BELOW_THRESHOLD'
    return AND after the _decide_satisfaction_passed line (inside the FAIL branch).

    Deterministic source-grep forcing function (§1y §1aa). Fails pre-GREEN.
    """
    phase6_path = _ENGINE_ROOT / "bytedigger_engine" / "workflows" / "phase_6_review.py"
    assert phase6_path.exists(), f"phase_6_review.py not found at {phase6_path}"

    source = phase6_path.read_text()

    # 1. Module must import the helper
    assert "record_satisfaction_reject" in source, (
        "phase_6_review.py does not import or reference record_satisfaction_reject"
    )

    # 2. Find the _decide_satisfaction_passed anchor
    lines = source.splitlines()
    decide_idx = None
    for i, line in enumerate(lines):
        if "_decide_satisfaction_passed(" in line:
            decide_idx = i
            break
    assert decide_idx is not None, (
        "Could not find _decide_satisfaction_passed( line in phase_6_review.py"
    )

    # 3. Find the E_SATISFACTION_BELOW_THRESHOLD return (first occurrence after decide)
    below_idx = None
    for i in range(decide_idx, len(lines)):
        if 'error_code="E_SATISFACTION_BELOW_THRESHOLD"' in lines[i]:
            below_idx = i
            break
    assert below_idx is not None, (
        "Could not find error_code='E_SATISFACTION_BELOW_THRESHOLD' after "
        "_decide_satisfaction_passed in phase_6_review.py"
    )

    # 4. record_satisfaction_reject( must appear between decide_idx and below_idx
    window_text = "\n".join(lines[decide_idx:below_idx])
    assert "record_satisfaction_reject(" in window_text, (
        f"record_satisfaction_reject( not found between _decide_satisfaction_passed "
        f"(line {decide_idx}) and E_SATISFACTION_BELOW_THRESHOLD return (line {below_idx}):\n"
        f"{window_text[:500]}"
    )
