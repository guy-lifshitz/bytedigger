"""RED tests for GH729 — cycle>=2 high-binding spec-rule parity in phase_45_spec.

Spec: SHARED/memory/Decisions/2026-07-13_GH729_cycle2_high_binding_parity_spec.md

`phase_45_spec.SPEC_HIGH_BINDING_AXES`, `_spec_high_binding_block`, and
`missing_high_binding_axes` do not exist yet on `main`/this worktree base —
each is imported INSIDE the relevant test body (never at module top level) so
this file COLLECTS cleanly and fails at assert/call time, not at collection
time (§1q ext, D1CF5FDF).

sys.path is NOT touched here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per §1q/81F97F3D.

§1l: only `_emit_safe` (a dependency, not the UUT) is patched, for AC-12's
event-capture assertion. `_build_spec_prompt`, `_spec_high_binding_block`,
and `missing_high_binding_axes` themselves are never mocked/patched — every
assertion exercises the real prompt-assembly path.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from contracts import WorkflowContext  # noqa: E402


# ─── shared fixtures / helpers (mirrors test_gh443_delta_retry.py) ─────────


def make_ctx(scratchpad: Path, *, question: str = "Add cycle2 high-binding parity") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH729",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_prev_spec(scratchpad: Path) -> Path:
    from phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "## Context\nPrevious cycle-1 spec body used as the cycle>=2 revise input.\n",
        encoding="utf-8",
    )
    return spec_path


STRUCTURED_FINDINGS = [
    {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",
     "required_action": "add explicit forcing-function to AC2"},
]

EDGE_CASE_GAP_FINDING = [
    {
        "id": "F1",
        "type": "edge-case-gap",
        "evidence": "early-return guard of the cited producer fn not enumerated",
        "required_action": "enumerate every early-return guard of the cited producer fn "
                            "and prove the AC value is reachable",
    },
]

UNSTRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings\n"
    "- spec is missing an Out of Scope section\n"
)

# Mirrors tests/test_gh443_delta_retry.py's STRUCTURED_FINDINGS_TEXT shape —
# a reviewer body carrying the literal `## Findings (structured)` json fence.
# Required whenever HAL_SPEC_DELTA_RETRY=0: with delta OFF, `_prev["structured_
# findings"]` is IGNORED by construction (phase_45_spec.py:786 gates the
# threaded value on delta_enabled) — the only route to structured findings is
# `extract_structured_findings(findings)`, which needs this exact fence.
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


def build_prompt(scratchpad: Path, *, cycle: int, findings=None, structured_findings=None,
                  surgical_fallback=None):
    from phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle}
    if findings is not None:
        prev["findings"] = findings
    if structured_findings is not None:
        prev["structured_findings"] = structured_findings
    if surgical_fallback is not None:
        prev["surgical_fallback"] = surgical_fallback
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"], result.data


# ─── AC-1 ───────────────────────────────────────────────────────────────────


def test_ac1_axes_constant_and_block_helper_exist(tmp_path):
    try:
        from phase_45_spec import (
            SPEC_HIGH_BINDING_AXES,
            _spec_high_binding_block,
            missing_high_binding_axes,
        )
    except ImportError:
        pytest.fail(
            "phase_45_spec.SPEC_HIGH_BINDING_AXES / _spec_high_binding_block / "
            "missing_high_binding_axes do not exist yet (GH729 not implemented)"
        )

    assert len(SPEC_HIGH_BINDING_AXES) >= 9
    block = _spec_high_binding_block()
    assert missing_high_binding_axes(block) == []


# ─── AC-2 ───────────────────────────────────────────────────────────────────


def test_ac2_cycle2_surgical_path_carries_all_axes(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    try:
        from phase_45_spec import missing_high_binding_axes
    except ImportError:
        pytest.fail("phase_45_spec.missing_high_binding_axes does not exist yet")

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = build_prompt(
        scratchpad, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    assert data.get("surgical_revise") is True
    assert missing_high_binding_axes(prompt) == []
    assert data.get("high_binding_missing") == []


# ─── AC-3 ───────────────────────────────────────────────────────────────────


def test_ac3_cycle2_delta_path_carries_all_axes(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    try:
        from phase_45_spec import missing_high_binding_axes
    except ImportError:
        pytest.fail("phase_45_spec.missing_high_binding_axes does not exist yet")

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = build_prompt(
        scratchpad, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    assert data.get("delta_retry") is True
    assert not data.get("surgical_revise")
    assert missing_high_binding_axes(prompt) == []


# ─── AC-4 ───────────────────────────────────────────────────────────────────


def test_ac4_cycle2_restricted_writer_scaffold_path_carries_all_axes(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    try:
        from phase_45_spec import missing_high_binding_axes
    except ImportError:
        pytest.fail("phase_45_spec.missing_high_binding_axes does not exist yet")

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    # HAL_SPEC_DELTA_RETRY=0 => the threaded `structured_findings` kwarg is
    # IGNORED by construction (phase_45_spec.py:786 gates it on delta_enabled).
    # The only route onto the restricted-writer branch is
    # extract_structured_findings(findings), which requires the reviewer body
    # to carry the literal `## Findings (structured)` json fence.
    prompt, data = build_prompt(
        scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_FENCE_TEXT,
    )

    # Branch-identity guard: must land on restricted-writer, not legacy —
    # prevents the fixture from silently drifting back onto the wrong branch.
    assert "ADDRESS EACH FINDING (by id):" in prompt
    assert "## REVISION (cycle 2" not in prompt
    assert missing_high_binding_axes(prompt) == []


# ─── AC-5 ───────────────────────────────────────────────────────────────────


def test_ac5_cycle2_legacy_revision_path_carries_all_axes(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    try:
        from phase_45_spec import missing_high_binding_axes
    except ImportError:
        pytest.fail("phase_45_spec.missing_high_binding_axes does not exist yet")

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = build_prompt(scratchpad, cycle=2, findings=UNSTRUCTURED_FINDINGS_TEXT)

    assert "## REVISION (cycle 2" in prompt
    assert missing_high_binding_axes(prompt) == []


# ─── AC-6 ───────────────────────────────────────────────────────────────────


def test_ac6_grep_parity_axes_present_in_cycle1_equal_axes_present_in_cycle2(tmp_path, monkeypatch):
    try:
        from phase_45_spec import SPEC_HIGH_BINDING_AXES
    except ImportError:
        pytest.fail("phase_45_spec.SPEC_HIGH_BINDING_AXES does not exist yet")

    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    scratchpad1 = tmp_path / "scratch_c1"
    p1, _ = build_prompt(scratchpad1, cycle=1)
    axes1 = {a for a in SPEC_HIGH_BINDING_AXES if a in p1}
    assert axes1 == set(SPEC_HIGH_BINDING_AXES), "cycle-1 prompt must already carry every axis"

    scenarios = {}

    scratchpad_surg = tmp_path / "scratch_surgical"
    _write_prev_spec(scratchpad_surg)
    scenarios["surgical"], _ = build_prompt(
        scratchpad_surg, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    scratchpad_delta = tmp_path / "scratch_delta"
    _write_prev_spec(scratchpad_delta)
    scenarios["delta"], _ = build_prompt(
        scratchpad_delta, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad_restricted = tmp_path / "scratch_restricted"
    _write_prev_spec(scratchpad_restricted)
    # See test_ac4: with delta OFF, threaded structured_findings is ignored —
    # must pass the json-fenced findings body to actually reach the
    # restricted-writer branch (not the legacy ## REVISION branch).
    restricted_prompt, _ = build_prompt(
        scratchpad_restricted, cycle=2, findings=STRUCTURED_FINDINGS_FENCE_TEXT,
    )
    assert "ADDRESS EACH FINDING (by id):" in restricted_prompt, (
        "fixture must land on the restricted-writer branch"
    )
    assert "## REVISION (cycle 2" not in restricted_prompt
    scenarios["scaffold_restricted"] = restricted_prompt

    scratchpad_legacy = tmp_path / "scratch_legacy"
    _write_prev_spec(scratchpad_legacy)
    scenarios["legacy"], _ = build_prompt(scratchpad_legacy, cycle=2, findings=UNSTRUCTURED_FINDINGS_TEXT)

    for name, p2 in scenarios.items():
        axes2 = {a for a in SPEC_HIGH_BINDING_AXES if a in p2}
        assert axes2 == axes1, f"axis-set mismatch on {name} path: {axes1 - axes2!r} missing"


# ─── AC-7 ───────────────────────────────────────────────────────────────────


def test_ac7_reentry_cycle3_surgical_and_post_fallback_delta_both_carry_all_axes(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    try:
        from phase_45_spec import missing_high_binding_axes
    except ImportError:
        pytest.fail("phase_45_spec.missing_high_binding_axes does not exist yet")

    scratchpad_a = tmp_path / "scratch_c3_surgical"
    _write_prev_spec(scratchpad_a)
    prompt_a, data_a = build_prompt(
        scratchpad_a, cycle=3, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )
    assert missing_high_binding_axes(prompt_a) == []

    scratchpad_b = tmp_path / "scratch_c3_delta"
    _write_prev_spec(scratchpad_b)
    prompt_b, data_b = build_prompt(
        scratchpad_b, cycle=3, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
        surgical_fallback=True,
    )
    assert data_b.get("surgical_revise") is not True
    assert missing_high_binding_axes(prompt_b) == []


# ─── AC-8 ───────────────────────────────────────────────────────────────────


def test_ac8_guard_miss_revise_regression_shield_carries_reachability_and_citeverify_axes(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = build_prompt(
        scratchpad, cycle=2, findings="reviewer prose", structured_findings=EDGE_CASE_GAP_FINDING,
    )

    assert "§1o consumer cite-verify" in data["prompt"]
    assert "§1l/§1y forcing-function + reachability" in data["prompt"]
    assert "FINDING_F1" in prompt


# ─── AC-9 ───────────────────────────────────────────────────────────────────


def test_ac9_no_double_injection_and_cycle1_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    from phase_45_spec import _SPEC_STABLE_PREFIX  # exists today

    scratchpad_c1 = tmp_path / "scratch_c1"
    p1, _ = build_prompt(scratchpad_c1, cycle=1)
    assert p1.count("HIGH-BINDING SPEC RULES") <= 1
    assert p1.count(_SPEC_STABLE_PREFIX) == 1
    assert p1.count("SPEC PREFLIGHT (top cycle-1 reject axes") == 1

    scratchpad_c2 = tmp_path / "scratch_c2"
    _write_prev_spec(scratchpad_c2)
    p2, _ = build_prompt(
        scratchpad_c2, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )
    assert p2.count("HIGH-BINDING SPEC RULES") <= 1


# ─── AC-10 ──────────────────────────────────────────────────────────────────


def test_ac10_byte_cap_and_bounded_growth_vs_kill_switch_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    try:
        from phase_45_spec import _spec_high_binding_block
    except ImportError:
        pytest.fail("phase_45_spec._spec_high_binding_block does not exist yet")

    # GH724 item-5 finalize-coverage grew the block to 2280B; headroom per GH729 bounded-growth intent
    assert len(_spec_high_binding_block().encode("utf-8")) <= 2560

    scratchpad_on = tmp_path / "scratch_on"
    _write_prev_spec(scratchpad_on)
    p_on, _ = build_prompt(
        scratchpad_on, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    monkeypatch.setenv("HAL_SPEC_HIGH_BINDING_PARITY", "0")
    scratchpad_off = tmp_path / "scratch_off"
    _write_prev_spec(scratchpad_off)
    p_off, _ = build_prompt(
        scratchpad_off, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    # GH724 item-5 finalize-coverage grew the block to 2280B; headroom per GH729 bounded-growth intent
    assert len(p_on) - len(p_off) <= 2560


# ─── AC-11 ──────────────────────────────────────────────────────────────────


def test_ac11_kill_switch_restores_status_quo_byte_identically(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    monkeypatch.setenv("HAL_SPEC_HIGH_BINDING_PARITY", "0")
    try:
        from phase_45_spec import SPEC_HIGH_BINDING_AXES, missing_high_binding_axes
    except ImportError:
        pytest.fail(
            "phase_45_spec.SPEC_HIGH_BINDING_AXES / missing_high_binding_axes "
            "do not exist yet"
        )

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    p_off, _ = build_prompt(
        scratchpad, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    assert missing_high_binding_axes(p_off) == list(SPEC_HIGH_BINDING_AXES)
    assert "HIGH-BINDING SPEC RULES" not in p_off


# ─── AC-12 ──────────────────────────────────────────────────────────────────


def test_ac12_event_emitted_once_with_path_and_cycle(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)

    with patch("phase_45_spec._emit_safe") as mock_emit:
        build_prompt(
            scratchpad, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
        )

    matching = [
        call for call in mock_emit.call_args_list
        if call.args and call.args[0] == "spec_prompt_high_binding"
    ]
    assert len(matching) == 1, (
        f"expected exactly one spec_prompt_high_binding emit; got {len(matching)} "
        f"of {[c.args[0] for c in mock_emit.call_args_list if c.args]!r}"
    )
    payload = matching[0].args[1]
    assert payload["path"] == "surgical"
    assert payload["cycle"] == 2
    assert payload["missing"] == []


# ─── AC-13 ──────────────────────────────────────────────────────────────────


def test_ac13_gh443_economics_preserved_scaffold_still_dropped(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from phase_45_spec import _spec_output_schema

    schema_first_line = next(
        line for line in _spec_output_schema("x").splitlines() if line.strip()
    )

    scratchpad_delta = tmp_path / "scratch_delta"
    _write_prev_spec(scratchpad_delta)
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    delta_prompt, _ = build_prompt(
        scratchpad_delta, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)

    scratchpad_surg = tmp_path / "scratch_surgical"
    _write_prev_spec(scratchpad_surg)
    surgical_prompt, _ = build_prompt(
        scratchpad_surg, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    for prompt in (delta_prompt, surgical_prompt):
        assert "FEATURE REQUEST:" not in prompt
        assert "ARCHITECTURE DECISION" not in prompt
        assert schema_first_line not in prompt

    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad_legacy = tmp_path / "scratch_legacy"
    _write_prev_spec(scratchpad_legacy)
    legacy_scaffold_prompt, _ = build_prompt(
        scratchpad_legacy, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    assert len(delta_prompt) < len(legacy_scaffold_prompt) - 1024
