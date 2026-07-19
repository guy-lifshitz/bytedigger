"""RED tests for GH1050-item2 — "SIBLING-MOCK FIRST" rule in RED cycle-1 prompt.

Spec: SHARED/memory/Decisions/2026-07-19_692D41F0_red_sibling_mock_rule_spec.md

`RED_SIBLING_MOCK_RULE` does NOT exist yet in `phase_5_implement` — AC1 probes
it via getattr INSIDE the test body only (D1CF5FDF/§1q) so this file COLLECTS
cleanly and fails at assert time, never at collection time.
`_build_red_prompt` exists today and is imported at module top level,
mirroring test_prompt_parity_gh596_gh601.py.

§1i: no singleton/time-dependent resource under test — spec fixtures are
pre-staged deterministically before invoking the UUT.

Expected to FAIL today (rule not yet wired):
  AC1 — RED_SIBLING_MOCK_RULE does not exist as a module attribute yet.
  AC2 — prompt does not contain "SIBLING-MOCK FIRST" / the reuse phrase yet.
  AC3 — rule text absent entirely, so no ordering relative to
        RED_COLLECTABILITY_RULE can be established (index() raises ValueError).
  AC4 — bash-only spec prompt also lacks the rule (always-on requirement unmet).
"""
from __future__ import annotations

from pathlib import Path

import phase_5_implement
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_5_implement import _build_red_prompt  # noqa: E402


# ─── shared helpers (mirrors test_prompt_parity_gh596_gh601.py) ────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-gh1050-sibling-mock",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_spec(scratchpad: Path, spec_text: str) -> Path:
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / "build-spec.md"
    spec_path.write_text(spec_text, encoding="utf-8")
    return spec_path


_PY_SPEC_TEXT = (
    "## Context\nModify `engine_py/workflows/foo.py` to add a new helper "
    "function that the RED test in `tests/test_foo.py` exercises.\n"
)

_SH_ONLY_SPEC_TEXT = (
    "## Context\nModify `scripts/foo.sh` to add a new bash helper that the "
    "shellcheck lint verifies. No other files touched.\n"
)


def _build_red(tmp_path: Path, spec_text: str, **org_extra) -> StepResult:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _write_spec(scratchpad, spec_text)
    ctx = _make_ctx(scratchpad, **org_extra)
    return _build_red_prompt(ctx, None)


# ─── AC1: RED_SIBLING_MOCK_RULE constant exists with the three frozen tokens ──


def test_ac1_red_sibling_mock_rule_constant_exists_with_frozen_tokens():
    rule = getattr(phase_5_implement, "RED_SIBLING_MOCK_RULE", None)
    assert rule is not None, "RED_SIBLING_MOCK_RULE constant must exist in phase_5_implement"
    assert isinstance(rule, str)
    assert "SIBLING-MOCK FIRST" in rule
    assert "existing sibling-test mock/env infrastructure" in rule
    assert "before inventing any new mock" in rule


# ─── AC2: cycle-1 RED prompt (py spec) contains the sibling-mock rule text ──


def test_ac2_red_prompt_py_spec_contains_sibling_mock_first_rule(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "SIBLING-MOCK FIRST" in prompt, "cycle-1 RED prompt must inject the sibling-mock-first rule"
    assert "existing sibling-test mock/env infrastructure" in prompt


# ─── AC3: sibling-mock rule appears BEFORE RED_COLLECTABILITY_RULE text ──


def test_ac3_sibling_mock_rule_precedes_collectability_rule_in_prompt(tmp_path):
    result = _build_red(tmp_path, _PY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    collectability_token = "COLLECTABILITY (D1CF5FDF)"
    assert collectability_token in prompt, "sanity: RED_COLLECTABILITY_RULE must be present in the prompt"
    sibling_idx = prompt.index("SIBLING-MOCK FIRST")
    collectability_idx = prompt.index(collectability_token)
    assert sibling_idx < collectability_idx, (
        "SIBLING-MOCK FIRST rule must appear before RED_COLLECTABILITY_RULE in the RULES block"
    )


# ─── AC4: bash-only spec RED prompt ALSO contains the rule (always-on) ──


def test_ac4_red_prompt_bash_only_spec_still_contains_sibling_mock_first_rule(tmp_path):
    result = _build_red(tmp_path, _SH_ONLY_SPEC_TEXT)
    assert result.status == "ok"
    prompt = result.data["prompt"]
    assert "SIBLING-MOCK FIRST" in prompt, "sibling-mock rule must be always-on, not py-gated"
    assert "existing sibling-test mock/env infrastructure" in prompt
