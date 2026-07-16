"""RED tests for AA333D9B — phase_6 band-aid #12: _parse_fix_status markdown gate
→ structured FixVerdict (disk-truth option-a pattern, mirrors #13 satisfaction gate).

Agreement: AA333D9B (child of 95D3E5F6, last G2 item)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_6_review  # noqa: E402
from phase_6_review import (  # noqa: E402
    FIX_BLOCKED,
    FIX_COMPLETE,
    FIX_DOC_RELPATH,
    FIX_SKIPPED,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    _write_fix_artifact,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, worktree: Path | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if worktree is not None:
        org["current_worktree_path"] = str(worktree)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_fix_prev(scratchpad: Path, raw: str) -> StepResult:
    """Build the prev StepResult that _write_fix_artifact expects."""
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\n\nVERDICT: FAIL\n")
    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "log_path": str(scratchpad / FIX_DOC_RELPATH),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "verdict": "FAIL",
            "prompt": "fix worker prompt stub",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture every _emit_safe call in phase_6_review."""
    captured: list[dict] = []

    def _capture(event_type: str, payload: dict) -> None:
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_6_review, "_emit_safe", _capture)
    return captured


def _no_op_disk_truth(monkeypatch) -> None:
    """Stub out _emit_fix_disk_truth_telemetry to avoid needing a real git repo."""
    monkeypatch.setattr(
        phase_6_review,
        "_emit_fix_disk_truth_telemetry",
        lambda ctx, prev, raw: None,
        raising=False,
    )


# ─── AC1 — FixVerdict dataclass importable from plugins.disk_truth ───────────


def test_ac1_fix_verdict_importable_and_has_correct_fields():
    """AC1: FixVerdict exists as a dataclass with fix_complete: bool and
    remaining: List[dict]; importable as `from plugins.disk_truth import FixVerdict`.

    FAILS today: FixVerdict does not exist in schema.py / __init__.py.
    """
    from plugins.disk_truth import FixVerdict  # type: ignore[attr-defined]
    import dataclasses

    assert dataclasses.is_dataclass(FixVerdict), "FixVerdict must be a dataclass"
    fields = {f.name: f for f in dataclasses.fields(FixVerdict)}
    assert "fix_complete" in fields, "FixVerdict missing field 'fix_complete'"
    assert "remaining" in fields, "FixVerdict missing field 'remaining'"
    # Construct an instance to verify field types are accepted
    v = FixVerdict(fix_complete=True, remaining=[])
    assert v.fix_complete is True
    assert v.remaining == []


# ─── AC2 — enforce strict validation for FixVerdict ──────────────────────────


def test_ac2_enforce_fix_verdict_happy_path():
    """AC2 (happy path): enforce({"fix_complete": True, "remaining": []}, FixVerdict)
    returns FixVerdict(fix_complete=True, remaining=[]).

    FAILS today: FixVerdict not yet defined.
    """
    from plugins.disk_truth import FixVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    result = enforce({"fix_complete": True, "remaining": []}, FixVerdict)
    assert isinstance(result, FixVerdict)
    assert result.fix_complete is True
    assert result.remaining == []


def test_ac2_enforce_fix_verdict_strict_bool_rejects_int():
    """AC2 (strict bool): enforce({"fix_complete": 1, "remaining": []}, FixVerdict)
    raises SchemaViolation — int is not bool.

    FAILS today: FixVerdict not yet defined.
    """
    from plugins.disk_truth import FixVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce({"fix_complete": 1, "remaining": []}, FixVerdict)


def test_ac2_enforce_fix_verdict_missing_required_remaining_raises():
    """AC2 (missing required field): enforce({"fix_complete": True}, FixVerdict)
    raises SchemaViolation — 'remaining' is required.

    FAILS today: FixVerdict not yet defined.
    """
    from plugins.disk_truth import FixVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce({"fix_complete": True}, FixVerdict)


def test_ac2_enforce_fix_verdict_unknown_field_raises():
    """AC2 (unknown field): enforce({"fix_complete": True, "remaining": [], "extra": 1}, FixVerdict)
    raises SchemaViolation — extra fields not in schema.

    FAILS today: FixVerdict not yet defined.
    """
    from plugins.disk_truth import FixVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce({"fix_complete": True, "remaining": [], "extra": 1}, FixVerdict)


# ─── AC3 — _parse_fix_structured happy path ──────────────────────────────────


def test_ac3_parse_fix_structured_happy_path():
    """AC3: valid structured block → (FixVerdict(True, []), None).

    FAILS today: _parse_fix_structured does not exist in phase_6_review.
    """
    from phase_6_review import _parse_fix_structured  # type: ignore[attr-defined]
    from plugins.disk_truth import FixVerdict  # type: ignore[attr-defined]

    raw = (
        "Some fix text here.\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true, "remaining": []}\n'
        "```\n"
    )
    verdict, reason = _parse_fix_structured(raw)
    assert reason is None, f"expected reason=None, got {reason!r}"
    assert isinstance(verdict, FixVerdict), f"expected FixVerdict, got {type(verdict)}"
    assert verdict.fix_complete is True
    assert verdict.remaining == []


# ─── AC4 — _parse_fix_structured error branches ───────────────────────────────


def test_ac4_parse_fix_structured_no_block_returns_absent():
    """AC4: no structured block → (None, "absent").

    FAILS today: _parse_fix_structured does not exist.
    """
    from phase_6_review import _parse_fix_structured  # type: ignore[attr-defined]

    verdict, reason = _parse_fix_structured("FIX COMPLETE — done")
    assert verdict is None
    assert reason == "absent"


def test_ac4_parse_fix_structured_empty_string_returns_absent():
    """AC4: empty string → (None, "absent").

    FAILS today: _parse_fix_structured does not exist.
    """
    from phase_6_review import _parse_fix_structured  # type: ignore[attr-defined]

    verdict, reason = _parse_fix_structured("")
    assert verdict is None
    assert reason == "absent"


def test_ac4_parse_fix_structured_malformed_json_returns_malformed():
    """AC4: header present but JSON unparseable → (None, "malformed").

    FAILS today: _parse_fix_structured does not exist.
    """
    from phase_6_review import _parse_fix_structured  # type: ignore[attr-defined]

    raw = "## fix-output (structured)\n```json\n{not json}\n```\n"
    verdict, reason = _parse_fix_structured(raw)
    assert verdict is None
    assert reason == "malformed"


def test_ac4_parse_fix_structured_schema_violation_returns_schema_violation():
    """AC4: header + valid JSON but missing 'remaining' field → (None, "schema_violation: ...").

    FAILS today: _parse_fix_structured does not exist.
    """
    from phase_6_review import _parse_fix_structured  # type: ignore[attr-defined]

    raw = (
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true}\n'
        "```\n"
    )
    verdict, reason = _parse_fix_structured(raw)
    assert verdict is None
    assert reason is not None and reason.startswith("schema_violation:"), (
        f"expected reason starting with 'schema_violation:', got {reason!r}"
    )


# ─── AC5 — structured fix_complete=true → ok + fix_structured_ok event ───────


def test_ac5_structured_fix_complete_true_returns_ok_with_event(tmp_path, monkeypatch):
    """AC5: fix-worker raw includes FIX COMPLETE marker AND structured block with
    fix_complete=true → write_fix_artifact returns status='ok';
    result.data['structured_verdict'] is a FixVerdict(fix_complete=True);
    a 'fix_structured_ok' event is emitted with {fix_complete: True, phase: 6}.

    FAILS today: structured parsing not yet implemented; 'structured_verdict' key
    absent from common_data; no 'fix_structured_ok' event emitted.
    """
    from plugins.disk_truth import FixVerdict  # type: ignore[attr-defined]

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = (
        "I fixed all the things.\n"
        "FIX COMPLETE — 2 of 2 findings fixed.\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": true, "remaining": []}\n'
        "```\n"
    )
    prev = _make_fix_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok', got {result.status!r} ({result.error_code!r})"
    )
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from result.data"
    )
    sv = result.data["structured_verdict"]
    assert isinstance(sv, FixVerdict), (
        f"expected FixVerdict, got {type(sv)}"
    )
    assert sv.fix_complete is True

    ok_events = [e for e in captured if e["type"] == "fix_structured_ok"]
    assert len(ok_events) == 1, (
        f"expected exactly 1 'fix_structured_ok' event, got {len(ok_events)}: {ok_events}"
    )
    payload = ok_events[0]["payload"]
    assert payload.get("fix_complete") is True, (
        f"fix_structured_ok payload fix_complete should be True, got {payload}"
    )
    assert payload.get("phase") == 6, (
        f"fix_structured_ok payload phase should be 6, got {payload}"
    )


# ─── AC6 — structured fix_complete=false beats lying COMPLETE marker ──────────


def test_ac6_structured_fix_complete_false_overrides_complete_marker(tmp_path, monkeypatch):
    """AC6: fix-worker emits FIX COMPLETE marker BUT structured block has
    fix_complete=false → write_fix_artifact returns status='error',
    error_code='E_FIX_BLOCKED', recoverable=False; AND a 'fix_verdict_drift' event
    is emitted with marker_status='COMPLETE', structured_fix_complete=False.

    FAILS today: structured gate not implemented; lying marker would pass through.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = (
        "I tried but could not fix finding #2.\n"
        "FIX COMPLETE — 1 of 2 findings fixed.\n"
        "\n"
        "## fix-output (structured)\n"
        "```json\n"
        '{"fix_complete": false, "remaining": [{"file": "foo.py", "issue": "still broken"}]}\n'
        "```\n"
    )
    prev = _make_fix_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "error", (
        f"expected status='error' (structured wins over lying marker), got {result.status!r}"
    )
    assert result.error_code == "E_FIX_BLOCKED", (
        f"expected error_code='E_FIX_BLOCKED', got {result.error_code!r}"
    )
    assert result.recoverable is False

    drift_events = [e for e in captured if e["type"] == "fix_verdict_drift"]
    assert len(drift_events) == 1, (
        f"expected exactly 1 'fix_verdict_drift' event, got {len(drift_events)}: {drift_events}"
    )
    drift_payload = drift_events[0]["payload"]
    assert drift_payload.get("marker_status") == FIX_COMPLETE, (
        f"fix_verdict_drift marker_status should be FIX_COMPLETE ('{FIX_COMPLETE}'), "
        f"got {drift_payload.get('marker_status')!r}"
    )
    assert drift_payload.get("structured_fix_complete") is False, (
        f"fix_verdict_drift structured_fix_complete should be False, got {drift_payload}"
    )


# ─── AC7 — no structured block → backward compat: ok + fix_structured_missing ─


def test_ac7_no_structured_block_marker_only_returns_ok_with_missing_event(tmp_path, monkeypatch):
    """AC7: fix-worker emits ONLY FIX COMPLETE (no structured block) →
    write_fix_artifact returns status='ok'; result.data['structured_verdict'] is None;
    a 'fix_structured_missing' event is emitted with {reason: "absent", phase: 6}.

    FAILS today: 'structured_verdict' key absent; 'fix_structured_missing' not emitted.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "FIX COMPLETE — 1 of 1 findings fixed.\n"
    prev = _make_fix_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok' (legacy marker path), got {result.status!r} ({result.error_code!r})"
    )
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from result.data"
    )
    assert result.data["structured_verdict"] is None, (
        f"expected structured_verdict=None when no block present, "
        f"got {result.data['structured_verdict']!r}"
    )

    missing_events = [e for e in captured if e["type"] == "fix_structured_missing"]
    assert len(missing_events) == 1, (
        f"expected exactly 1 'fix_structured_missing' event, got {len(missing_events)}: {missing_events}"
    )
    payload = missing_events[0]["payload"]
    assert payload.get("reason") == "absent", (
        f"fix_structured_missing reason should be 'absent', got {payload.get('reason')!r}"
    )
    assert payload.get("phase") == 6, (
        f"fix_structured_missing phase should be 6, got {payload}"
    )


# ─── AC8 — backward compat: FIX BLOCKED marker only → legacy E_FIX_BLOCKED ───


def test_ac8_blocked_marker_only_legacy_path_returns_error(tmp_path, monkeypatch):
    """AC8: fix-worker emits ONLY FIX BLOCKED (no structured block) →
    write_fix_artifact returns status='error', error_code='E_FIX_BLOCKED',
    recoverable=False (legacy marker-fallback path unchanged).

    FAILS today: 'structured_verdict' key absent from common_data (minor),
    and 'fix_structured_missing' event not emitted — the error path itself
    should still work (status/error_code), but the new common_data shape will
    fail if the AC asserts structured_verdict is present in data.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "FIX BLOCKED — 2 of 3 findings fixed. Diagnosis: spec ambiguity.\n"
    prev = _make_fix_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_fix_artifact(ctx, prev)

    assert result.status == "error", (
        f"expected status='error', got {result.status!r}"
    )
    assert result.error_code == "E_FIX_BLOCKED", (
        f"expected error_code='E_FIX_BLOCKED', got {result.error_code!r}"
    )
    assert result.recoverable is False

    # structured_verdict should be in common_data (even for the error path)
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from error-path common_data (AC8 requires "
        "the same common_data shape for both ok and error returns)"
    )
    assert result.data["structured_verdict"] is None, (
        f"expected structured_verdict=None (no block in raw), got {result.data['structured_verdict']!r}"
    )

    # A fix_structured_missing event should also be emitted on this path
    missing_events = [e for e in captured if e["type"] == "fix_structured_missing"]
    assert len(missing_events) == 1, (
        f"expected 1 'fix_structured_missing' event on legacy BLOCKED path, "
        f"got {len(missing_events)}: {missing_events}"
    )


# ─── AC9 — smoke: _parse_fix_status still works (regression guard) ────────────


def test_ac9_parse_fix_status_still_works():
    """AC9: _parse_fix_status regression guard — existing marker parsing unchanged.

    This test exercises existing behavior and should pass today AND after GREEN.
    It guards that the #12 changes do not regress the legacy marker parser.
    """
    from phase_6_review import _parse_fix_status  # noqa: E402

    assert _parse_fix_status("FIX COMPLETE — all done") == FIX_COMPLETE
    assert _parse_fix_status("FIX SKIPPED — no findings") == FIX_SKIPPED
    assert _parse_fix_status("FIX BLOCKED — something") == FIX_BLOCKED
    # Last marker wins
    assert _parse_fix_status("FIX COMPLETE\nFIX BLOCKED") == FIX_BLOCKED
    assert _parse_fix_status("FIX BLOCKED\nFIX COMPLETE") == FIX_COMPLETE
