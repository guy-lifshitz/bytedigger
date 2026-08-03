"""RED tests for GH723 — phase_45 spec-writer Producer Guard Reachability
forcing-OUTPUT section.

Spec: SHARED/memory/Decisions/2026-07-13_GH723_phase45_guard_reachability_spec.md

UUT is `_spec_output_schema(doc_path)` in `phase_45_spec.py` — it is imported
and exercised only INDIRECTLY via the real `_build_spec_prompt` assembly path
and is NEVER mocked/patched (§1l stub-passability). On this worktree base the
new `## Producer Guard Reachability` REQUIRED section and its
PRE-SUBMISSION-CHECKLIST item do not exist yet, so every assertion below
FAILS against current production code — that is the RED.

`_build_spec_prompt` / `SPEC_DOC_RELPATH` are imported INSIDE each test body
(never at module top level) per §1q ext (D1CF5FDF) — the symbols already
exist today (only their emitted TEXT changes), so this is a defensive
convention match with the sibling file, not a collection-safety requirement.

sys.path is NOT touched here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per
§1q/81F97F3D.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── shared fixtures / helpers (mirrors test_gh729_cycle2_high_binding_parity.py) ──


def make_ctx(scratchpad: Path, *, question: str = "GH723 producer guard reachability") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH723",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_prev_spec(scratchpad: Path) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "## Context\nPrevious cycle-1 spec body used as the cycle>=2 revise input.\n",
        encoding="utf-8",
    )
    return spec_path


# Mirrors tests/test_gh729_cycle2_high_binding_parity.py's
# STRUCTURED_FINDINGS_FENCE_TEXT — with HAL_SPEC_DELTA_RETRY=0, the threaded
# `prev["structured_findings"]` kwarg is ignored by construction; the only
# route onto the restricted-writer (scaffold) branch that still calls
# `_spec_output_schema` is `extract_structured_findings(findings)`, which
# requires the reviewer body to carry this literal json fence.
STRUCTURED_FINDINGS_FENCE_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    '  {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",\n'
    '   "required_action": "add explicit forcing-function to AC2"}\n'
    "]\n"
    "```\n\n"
    "## Findings\n"
    "- reviewer prose sentinel line\n"
)


def build_prompt(scratchpad: Path, *, cycle: int, findings=None):
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle}
    if findings is not None:
        prev["findings"] = findings
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"], result.data


# ─── AC-1 ───────────────────────────────────────────────────────────────────


def test_ac1_section_present_cycle1(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "## Producer Guard Reachability" in prompt


# ─── AC-2 ───────────────────────────────────────────────────────────────────


def test_ac2_guard_enum_and_reachability(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "enumerate every early-return guard" in prompt
    assert "reachable past" in prompt


# ─── AC-3 ───────────────────────────────────────────────────────────────────


def test_ac3_f1_dead_branch(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "move it before the\n   guard or drop the AC" in prompt


# ─── AC-4 ───────────────────────────────────────────────────────────────────


def test_ac4_f2_fixture_fields(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "sets every field the producer\n   reads" in prompt


# ─── AC-5 ───────────────────────────────────────────────────────────────────


def test_ac5_none_escape(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "write `none`" in prompt


# ─── AC-6 ───────────────────────────────────────────────────────────────────


def test_ac6_checklist_item(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert "a branch after a short-circuit guard is dead (F1)" in prompt


# ─── AC-7 ───────────────────────────────────────────────────────────────────


def test_ac7_cycle2_single_occurrence(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, _ = build_prompt(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_FENCE_TEXT)

    # Branch-identity guard: must land on restricted-writer scaffold (calls
    # _spec_output_schema), not a delta/surgical early-return path.
    assert "ADDRESS EACH FINDING (by id):" in prompt
    assert prompt.count("## Producer Guard Reachability") == 1
