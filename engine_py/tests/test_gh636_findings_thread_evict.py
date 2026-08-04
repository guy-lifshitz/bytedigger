"""RED tests for GH#636 — findings-thread survives ERROR-row evict (Option-D).

Spec: SHARED/memory/Decisions/2026-07-12_0C39F486_gh636_findings_thread_evict_survival_spec.md

`lib.findings_sidecar` (persist_findings_thread, load_findings_thread,
SIDECAR_RELNAME) does NOT exist yet, and `phase_45_spec.py::_build_spec_prompt`
does not yet recover a dropped findings-thread from the sidecar. Per §1q
(D1CF5FDF), the not-yet-existing `findings_sidecar` module is imported ONLY
INSIDE each test function body, never at module top level, so this file
COLLECTS cleanly and every test fails at ASSERT/IMPORT-inside-body time, not
at collection time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared fixtures / helpers ─────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, question: str = "GH636 findings-thread evict survival") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH636",
        persona="hal",
        framework=None,
        domain=None,
    )


BASE_SPEC = (
    "## Context\n"
    "Section 1 body, prior cycle-1 spec text used as the surgical/delta base.\n"
    "\n"
    "## Findings\n"
    "OLD_FRAGMENT_TO_REPLACE_F1\n"
    "\n"
    "## Out of Scope\n"
    "Section 3 body.\n"
)

STRUCTURED_FINDINGS = [
    {
        "id": "F1",
        "type": "gap",
        "evidence": "spec §Findings has an unresolved placeholder fragment",
        "required_action": "replace OLD_FRAGMENT_TO_REPLACE_F1 with concrete text",
    },
]

STRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n"
    "```json\n"
    + json.dumps(STRUCTURED_FINDINGS)
    + "\n```\n\n"
    "## Findings\n"
    "- placeholder fragment must be replaced\n"
)


def _write_prev_spec(scratchpad: Path, text: str = BASE_SPEC) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


def _clear_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_persist_writes_sidecar(tmp_path):
    from bytedigger_engine.findings_sidecar import persist_findings_thread, SIDECAR_RELNAME

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    written = persist_findings_thread(scratchpad, STRUCTURED_FINDINGS, cycle=1)

    sidecar = scratchpad / SIDECAR_RELNAME
    assert sidecar.is_file(), "persist must write the sidecar file"
    assert written == sidecar
    on_disk = json.loads(sidecar.read_text(encoding="utf-8"))
    assert on_disk["structured_findings"] == STRUCTURED_FINDINGS


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_roundtrip(tmp_path):
    from bytedigger_engine.findings_sidecar import persist_findings_thread, load_findings_thread

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    persist_findings_thread(scratchpad, STRUCTURED_FINDINGS, cycle=1)

    loaded = load_findings_thread(scratchpad)
    assert loaded == STRUCTURED_FINDINGS


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_load_absent_returns_none(tmp_path):
    from bytedigger_engine.findings_sidecar import load_findings_thread

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    assert load_findings_thread(scratchpad) is None


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_persist_empty_and_unwritable_returns_none(tmp_path):
    from bytedigger_engine.findings_sidecar import persist_findings_thread, SIDECAR_RELNAME

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    assert persist_findings_thread(scratchpad, [], cycle=1) is None
    assert not (scratchpad / SIDECAR_RELNAME).exists()

    assert persist_findings_thread(scratchpad, "not-a-list", cycle=1) is None  # type: ignore[arg-type]
    assert not (scratchpad / SIDECAR_RELNAME).exists()

    unwritable = tmp_path / "scratch_is_a_file"
    unwritable.write_text("not a directory", encoding="utf-8")
    assert persist_findings_thread(unwritable, STRUCTURED_FINDINGS, cycle=1) is None


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_build_spec_prompt_recovers_from_sidecar_post_evict(tmp_path, monkeypatch):
    _clear_gates(monkeypatch)
    from bytedigger_engine.findings_sidecar import persist_findings_thread
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    persist_findings_thread(scratchpad, STRUCTURED_FINDINGS, cycle=1)

    ctx = make_ctx(scratchpad)
    # Post-evict: prev carries a cycle marker but NO threaded structured_findings
    # (the DBOS operation_outputs row for the prior step was DELETEd on retry).
    prev = StepResult(
        status="ok",
        data={"cycle": 2},
        duration_ms=0,
        step_name="gate_on_review",
    )

    result = _build_spec_prompt(ctx, prev)

    assert result.data.get("structured_findings") == STRUCTURED_FINDINGS


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_reentry_chain_surgical_stays_on(tmp_path, monkeypatch):
    _clear_gates(monkeypatch)
    from bytedigger_engine.workflows.phase_45_spec import _gate_on_review, _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)

    # Step 1: cycle-1 REVISE review with a structured findings fence — this is
    # the produce-time write site that must persist the sidecar (§2.2).
    gate_prev = StepResult(
        status="ok",
        data={
            "verdict": "REVISE",
            "review_path": "review.md",
            "spec_path": str(scratchpad / "specs" / "build-spec.md"),
            "cycle": 1,
            "review_raw": STRUCTURED_FINDINGS_TEXT,
        },
        duration_ms=0,
        step_name="review",
    )
    _gate_on_review(ctx, gate_prev)

    # Step 2: simulate ERROR-row evict + resume at cycle 2 — a FRESH prev with
    # no threaded structured_findings (DBOS operation_outputs was DELETEd).
    post_evict_prev = StepResult(
        status="ok",
        data={"cycle": 2},
        duration_ms=0,
        step_name="gate_on_review",
    )
    result = _build_spec_prompt(ctx, post_evict_prev)

    assert result.data.get("surgical_revise") is True


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_sidecar_wins_over_broken_fence_fallback(tmp_path, monkeypatch):
    _clear_gates(monkeypatch)
    from bytedigger_engine.findings_sidecar import persist_findings_thread
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    persist_findings_thread(scratchpad, STRUCTURED_FINDINGS, cycle=1)

    ctx = make_ctx(scratchpad)
    # GH615: `findings` is a fence-less STRINGIFIED list — the status-quo
    # extract_structured_findings(findings) fallback returns None on this shape.
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "findings": json.dumps(STRUCTURED_FINDINGS)},
        duration_ms=0,
        step_name="gate_on_review",
    )

    result = _build_spec_prompt(ctx, prev)

    assert result.data.get("surgical_revise") is True


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_cycle1_no_recovery_and_idempotent(tmp_path, monkeypatch):
    _clear_gates(monkeypatch)
    from bytedigger_engine.findings_sidecar import persist_findings_thread
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    persist_findings_thread(scratchpad, STRUCTURED_FINDINGS, cycle=1)
    ctx = make_ctx(scratchpad)

    cycle1_prev = StepResult(
        status="ok",
        data={"cycle": 1},
        duration_ms=0,
        step_name="gate_on_review",
    )
    cycle1_result = _build_spec_prompt(ctx, cycle1_prev)
    assert cycle1_result.data.get("surgical_revise") is not True

    cycle2_prev = StepResult(
        status="ok",
        data={"cycle": 2},
        duration_ms=0,
        step_name="gate_on_review",
    )
    first = _build_spec_prompt(ctx, cycle2_prev)
    second = _build_spec_prompt(ctx, cycle2_prev)
    assert first.data.get("structured_findings") == second.data.get("structured_findings")
    assert first.data.get("structured_findings") == STRUCTURED_FINDINGS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
