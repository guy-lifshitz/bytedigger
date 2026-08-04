"""RED tests for GH530 — thread `structured_findings` losslessly from the
full pre-truncation `raw_review` through `_gate_on_review`, and prefer that
threaded list over text re-parse in `_build_spec_prompt`.

Spec: SHARED/memory/Decisions/2026-07-10_GH530_delta_revise_populate_spec.md

The new `structured_findings` key threading behavior does not exist yet in
`_gate_on_review` / `_build_spec_prompt` — no not-yet-existing symbol is
imported anywhere (all imported names already exist on main), so this file
COLLECTS cleanly and fails at ASSERT time (§1q ext, D1CF5FDF).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.lib.plugins.checklist_convergence import extract_structured_findings  # noqa: E402


# ─── shared fixtures / helpers (mirrors test_gh443_delta_retry.py style) ────

REVIEWER_SENTINEL_LINE = "Reviewer prose sentinel line for GH530 verbatim check."

STRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    '  {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",\n'
    '   "required_action": "add explicit forcing-function to AC2"},\n'
    '  {"id": "F2", "type": "fabrication", "evidence": "spec cites a line not read",\n'
    '   "required_action": "move claim to Open Questions"}\n'
    "]\n"
    "```\n\n"
    "## Findings\n"
    f"- {REVIEWER_SENTINEL_LINE}\n"
)

UNSTRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings\n"
    "- spec is missing an Out of Scope section\n"
)

COMPLETENESS_GATE_TEXT = (
    "REWRITE the FULL spec body — the previous draft was missing required "
    "sections per E_SPEC_INCOMPLETE. Address every AC gap before resubmitting."
)


def make_ctx(scratchpad: Path, *, question: str = "GH530 delta-revise populate") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "spec_revise_hard_cap": 99,
        },
        question=question,
        session_id="test-session-GH530",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_prev_spec(scratchpad: Path) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "## Context\nPrevious cycle-1 spec body used as the delta-retry input.\n",
        encoding="utf-8",
    )
    return spec_path


def make_gate_prev(*, verdict: str, cycle: int, review_raw: str) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "review_path": "review.md",
            "spec_path": "spec.md",
            "cycle": cycle,
            "review_raw": review_raw,
        },
        duration_ms=0,
        step_name="review",
    )


def call_gate(scratchpad: Path, *, review_raw: str, cycle: int = 1):
    from bytedigger_engine.workflows.phase_45_spec import _gate_on_review

    ctx = make_ctx(scratchpad)
    prev = make_gate_prev(verdict="REVISE", cycle=cycle, review_raw=review_raw)
    return _gate_on_review(ctx, prev)


# ─── AC1 ─────────────────────────────────────────────────────────────────


def test_ac1_gate_threads_structured_findings(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    result = call_gate(scratchpad, review_raw=STRUCTURED_FINDINGS_TEXT)

    assert isinstance(result.data, dict)
    expected = extract_structured_findings(STRUCTURED_FINDINGS_TEXT)
    assert result.data.get("structured_findings") == expected
    assert len(result.data.get("structured_findings") or []) == 2


# ─── AC2 ─────────────────────────────────────────────────────────────────


def test_ac2_threaded_findings_survive_truncation(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    # 5KB of padding placed BEFORE the structured block so `_truncate_findings`
    # (4096B cap) cuts the JSON fence entirely out of the forwarded `findings`
    # text — but the threading must happen from the FULL raw_review.
    padding = "PAD " * 1300  # ~6.5KB, well over the 4096B cap
    review_raw = padding + "\n\n" + STRUCTURED_FINDINGS_TEXT

    result = call_gate(scratchpad, review_raw=review_raw)

    assert isinstance(result.data, dict)
    # The truncated forward-threaded text no longer contains a parseable block.
    assert extract_structured_findings(result.data["findings"]) is None
    # But the threaded structured_findings list, built from the FULL raw_review,
    # is complete.
    expected = extract_structured_findings(STRUCTURED_FINDINGS_TEXT)
    assert result.data.get("structured_findings") == expected
    assert len(result.data.get("structured_findings") or []) == 2


# ─── AC3 ─────────────────────────────────────────────────────────────────


def test_ac3_freetext_fallback_threaded(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    result = call_gate(scratchpad, review_raw=UNSTRUCTURED_FINDINGS_TEXT)

    assert isinstance(result.data, dict)
    threaded = result.data.get("structured_findings")
    assert threaded, "expected non-empty free-text-fallback structured_findings"
    for item in threaded:
        assert set(item.keys()) >= {"id", "type", "evidence", "required_action"}


# ─── AC4 ─────────────────────────────────────────────────────────────────


def test_ac4_telemetry_emitted_on_every_revise(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    captured: list[tuple[str, dict]] = []
    real_emit = phase_45_spec._emit_safe

    def _capture(event_type, payload):
        captured.append((event_type, payload))
        return real_emit(event_type, payload)

    monkeypatch.setattr(phase_45_spec, "_emit_safe", _capture)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    call_gate(scratchpad, review_raw=STRUCTURED_FINDINGS_TEXT)

    matches = [p for (name, p) in captured if name == "spec_revise_findings_threaded"]
    assert matches, "spec_revise_findings_threaded event was never emitted"
    payload = matches[0]
    assert payload.get("phase") == "phase_45_spec"
    assert "cycle" in payload
    assert payload.get("n_findings") == 2
    assert payload.get("source") in {"structured", "freetext_fallback", "none"}
    assert payload.get("source") == "structured"


# ─── AC5 ─────────────────────────────────────────────────────────────────


def test_ac5_build_prompt_prefers_threaded_key(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)

    threaded_findings = [
        {"id": "T1", "type": "gap", "evidence": "e1", "required_action": "THREADED_ACTION_UNIQUE"},
        {"id": "T2", "type": "gap", "evidence": "e2", "required_action": "THREADED_ACTION_UNIQUE_2"},
    ]
    # `findings` text itself is unparseable garbage (simulating a truncated
    # or malformed re-parse) — only the threaded key should drive the delta path.
    garbage_findings_text = "```json\n[not valid json,,,\n"
    prev = {
        "cycle": 2,
        "findings": garbage_findings_text,
        "structured_findings": threaded_findings,
    }

    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    assert result.data.get("delta_retry") is True
    assert "FINDING_T1" in result.data["prompt"]
    assert "THREADED_ACTION_UNIQUE" in result.data["prompt"]


# ─── AC6 ─────────────────────────────────────────────────────────────────


def test_ac6_threaded_key_takes_precedence_over_text_reparse(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)

    threaded_findings = [
        {"id": "T1", "type": "gap", "evidence": "e1", "required_action": "THREADED_ACTION_UNIQUE_TOKEN"},
    ]
    text_block_findings_text = (
        "## Verdict\nREVISE\n\n"
        "## Findings (structured)\n"
        "```json\n"
        '[{"id": "X1", "type": "gap", "evidence": "e", '
        '"required_action": "TEXTBLOCK_ACTION_UNIQUE_TOKEN"}]\n'
        "```\n"
    )
    prev = {
        "cycle": 2,
        "findings": text_block_findings_text,
        "structured_findings": threaded_findings,
    }

    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    assert "THREADED_ACTION_UNIQUE_TOKEN" in result.data["prompt"]
    assert "FINDING_T1" in result.data["prompt"]
    # X1 (the text-block-only id) may still leak raw into the verbatim
    # reviewer-context channel — that's a separate, permitted pass-through.
    # The forcing assertion is that it must NOT be rendered as a FINDING_
    # line, i.e. it never drives the ADDRESS EACH FINDING checklist.
    assert "FINDING_X1" not in result.data["prompt"]


# ─── AC7 ─────────────────────────────────────────────────────────────────


def test_ac7_completeness_gate_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)

    prev = {"cycle": 2, "findings": COMPLETENESS_GATE_TEXT}  # no structured_findings key

    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    assert result.data.get("delta_retry") is not True


# ─── AC8 ─────────────────────────────────────────────────────────────────


def test_ac8_kill_switch_disables_delta_even_with_threaded_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)

    threaded_findings = [
        {"id": "T1", "type": "gap", "evidence": "e1", "required_action": "should not delta"},
    ]
    prev = {"cycle": 2, "findings": "garbage", "structured_findings": threaded_findings}

    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    assert result.data.get("delta_retry") is not True


# ─── AC9 ─────────────────────────────────────────────────────────────────


def test_ac9_catalog_default_and_kind_unchanged():
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS["HAL_SPEC_DELTA_RETRY"]
    assert entry["default"] == "1"
    assert entry["kind"] == "gate"


# ─── AC10 ────────────────────────────────────────────────────────────────


def test_ac10_structured_findings_not_a_retry_control_key():
    from bytedigger_engine import engine

    assert "structured_findings" not in engine._RETRY_CONTROL_KEYS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
