"""RED tests — GH450: phase_7 synthesizer resumed-run context rehydration.

Spec: SHARED/memory/Decisions/2026-07-09_GH450_synth_resume_context_spec.md

Covers AC1-AC7. AC8 (existing suite green) is orchestrator's suite run, not
covered here.

Per §1q ext (D1CF5FDF): the not-yet-existing symbol `_completed_phase_digest`
is accessed via getattr() INSIDE test bodies (never at module import time) so
this file collects cleanly and fails at assert time, not collection time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402
import derive_state as ds  # noqa: E402
import phase_7_synthesize as p7  # noqa: E402
from phase_7_synthesize import (  # noqa: E402
    FIX_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    _build_synthesizer_prompt,
)


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_all_docs(scratchpad: Path) -> None:
    for relpath, body in (
        (SPEC_DOC_RELPATH, "## US1\nAdd foo\n"),
        (REVIEW_DOC_RELPATH, "Composite review.\nVERDICT: PASS\n"),
        (FIX_DOC_RELPATH, "FIX SKIPPED — no findings\n"),
        (SATISFACTION_DOC_RELPATH, "SCORE: 95\nVERDICT: PASS\n"),
    ):
        doc = scratchpad / relpath
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(body)


def _write_events(log_path: Path, events: list[dict]) -> None:
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _finished_event(run_id: str, workflow_name: str, ts: str) -> dict:
    return {
        "ts": ts,
        "run_id": run_id,
        "event_type": "workflow_finished",
        "payload": {"workflow_name": workflow_name, "status": "ok", "wall_ms": 1000},
    }


_RESUMED_EVENTS = [
    _finished_event("r1", "phase_45_spec", "2026-07-09T10:00:00.000Z"),
    _finished_event("r2", "phase_5_implement", "2026-07-09T10:01:00.000Z"),
    _finished_event("r3", "phase_5_implement", "2026-07-09T10:02:00.000Z"),
    _finished_event("r4", "phase_6_review", "2026-07-09T10:03:00.000Z"),
    _finished_event("r5", "phase_7_synthesize", "2026-07-09T10:04:00.000Z"),
]


def _get_digest_fn():
    fn = getattr(p7, "_completed_phase_digest", None)
    assert fn is not None, (
        "phase_7_synthesize._completed_phase_digest does not exist yet "
        "(GH450 fix not implemented) — see spec §2.1"
    )
    return fn


def test_ac1_completed_phases_block_present_with_phases(tmp_path, monkeypatch):
    """AC1: resumed-run fixture -> prompt has COMPLETED PHASES block + phase_5_implement line."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    prompt = result.data["prompt"]
    assert "COMPLETED PHASES (engine-derived from event log — authoritative):" in prompt
    assert "- phase_5_implement" in prompt
    assert "- phase_45_spec" in prompt
    assert "- phase_6_review" in prompt


def test_ac2_digest_excludes_phase_7_self(tmp_path, monkeypatch):
    """AC2: literal '- phase_7_synthesize' absent from the COMPLETED PHASES block."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    prompt = result.data["prompt"]
    assert "COMPLETED PHASES (engine-derived from event log — authoritative):" in prompt
    assert "- phase_7_synthesize" not in prompt


def test_ac3_artifacts_on_disk_all_present(tmp_path, monkeypatch):
    """AC3a: all 4 docs present -> ARTIFACTS ON DISK block shows all PRESENT."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    prompt = result.data["prompt"]
    assert "ARTIFACTS ON DISK (engine-verified):" in prompt
    assert "spec: PRESENT" in prompt
    assert "review: PRESENT" in prompt
    assert "fix: PRESENT" in prompt
    assert "satisfaction: PRESENT" in prompt


def test_ac3_artifacts_on_disk_review_missing(tmp_path, monkeypatch):
    """AC3b: review doc deleted -> ARTIFACTS ON DISK shows review: MISSING."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    (scratchpad / REVIEW_DOC_RELPATH).unlink()
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    prompt = result.data["prompt"]
    assert "review: MISSING" in prompt
    assert "spec: PRESENT" in prompt


def test_ac4_resumed_run_diff_guidance_present(tmp_path, monkeypatch):
    """AC4: prompt has the empty-diff-not-evidence sentence + git log --stat -10."""
    log_path = tmp_path / "build-events.jsonl"
    log_path.write_text("")
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    prompt = result.data["prompt"]
    assert "an empty working diff is NOT evidence the build halted" in prompt
    assert "git log --stat -10" in prompt


def test_ac5_fresh_run_no_completed_phases_block_and_empty_list(tmp_path, monkeypatch):
    """AC5: empty/absent events.jsonl -> digest '' , no COMPLETED PHASES line, data key []."""
    digest_fn = _get_digest_fn()

    log_path = tmp_path / "build-events.jsonl"
    log_path.write_text("")
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    ctx = make_ctx(scratchpad)

    assert digest_fn(ctx) == ""

    result = _build_synthesizer_prompt(ctx, None)
    prompt = result.data["prompt"]
    assert "COMPLETED PHASES" not in prompt
    assert result.data["completed_phases"] == []


def test_ac6_default_log_path_raises_best_effort_guard(tmp_path, monkeypatch):
    """AC6: default_log_path raising -> digest '' and prompt build still status ok."""
    digest_fn = _get_digest_fn()

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(ds, "default_log_path", _raise, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    ctx = make_ctx(scratchpad)

    assert digest_fn(ctx) == ""

    result = _build_synthesizer_prompt(ctx, None)
    assert result.status == "ok"


def test_ac7_completed_phases_data_key_deduped_first_seen_order(tmp_path, monkeypatch):
    """AC7: data["completed_phases"] == first-seen deduped order, phase_5_implement dedup'd."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    result = _build_synthesizer_prompt(make_ctx(scratchpad), None)

    assert result.data["completed_phases"] == [
        "phase_45_spec",
        "phase_5_implement",
        "phase_6_review",
    ]
