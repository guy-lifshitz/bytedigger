"""RED tests for GH934 — spec-writer prompt must ban citation-form for
NEW symbols the spec itself introduces.

Counterfactual (ppba#1274, forge-1784226556): spec-writer emitted
citations for symbols the spec INTRODUCES, not pre-existing ones.
content-grep cite-lint fails (E_SPEC_CITE_LINT_FAIL) because the symbol
is not yet in the file. Rule 7 (existing prompt) requires a `NEW:` label
but does not forbid citation FORM for such symbols — rule 9 (GH934)
closes that gap.

These tests are mechanical string/index asserts against
`_build_spec_prompt`'s returned prompt string — no LLM, no network, no
engine execution. Pattern mirrors test_phase_45_spec_D02C615D.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-GH934",
        persona="hal",
        framework=None,
        domain=None,
    )


def get_spec_prompt(scratchpad: Path, *, _prev=None) -> str:
    from phase_45_spec import _build_spec_prompt

    ctx = make_ctx(scratchpad)
    result = _build_spec_prompt(ctx, _prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"]


# ─── AC1: rule-9 tokens present in the spec-writer prompt ─────────────────


def test_spec_prompt_bans_new_symbol_citation_form(tmp_path):
    """AC1: the spec-writer prompt must contain the three rule-9 anchor
    tokens: the citation-form ban, the E_SPEC_CITE_LINT_FAIL rationale, and
    the 'reserved for EXISTING code' framing.

    Today: rule 9 does not exist in `_grounded_citation_contract()` — FAILS.
    """
    scratchpad = tmp_path / "scratch"
    prompt = get_spec_prompt(scratchpad)

    assert "must NEVER appear in citation form" in prompt, (
        "spec prompt missing rule-9 citation-form ban for NEW symbols"
    )
    assert "E_SPEC_CITE_LINT_FAIL" in prompt, (
        "spec prompt missing rule-9 E_SPEC_CITE_LINT_FAIL rationale"
    )
    assert "reserved for EXISTING code" in prompt, (
        "spec prompt missing rule-9 'reserved for EXISTING code' framing"
    )


# ─── AC2: rule 9 lives inside the GROUNDED CITATION CONTRACT block ────────


def test_new_symbol_ban_located_inside_grounded_citation_block(tmp_path):
    """AC2: rule-9's anchor token must sit strictly between the
    '## GROUNDED CITATION CONTRACT' heading and the next '## ' heading.

    Today: rule 9 doesn't exist at all -> the substring lookup fails, so
    this test FAILS (not merely index-mismatch, outright missing).
    """
    scratchpad = tmp_path / "scratch"
    prompt = get_spec_prompt(scratchpad)

    heading_idx = prompt.find("## GROUNDED CITATION CONTRACT")
    assert heading_idx >= 0, "test setup invariant: GROUNDED CITATION CONTRACT heading missing"
    # Confirm it's a real line-start heading (not an inline mention).
    assert heading_idx == 0 or prompt[heading_idx - 1] == "\n", (
        "test setup invariant: GROUNDED CITATION CONTRACT heading must start a line"
    )

    rule9_idx = prompt.find("must NEVER appear in citation form")
    assert rule9_idx >= 0, "rule-9 anchor token not found in prompt at all"

    # Find next REAL '## ' heading (line-start) after the GROUNDED CITATION
    # CONTRACT heading — must anchor on "\n## " so an inline backticked
    # mention like "`## Open Questions`" inside rule 2's body text does not
    # get mistaken for a section boundary.
    next_heading_idx = prompt.find("\n## ", heading_idx + len("## GROUNDED CITATION CONTRACT"))
    assert next_heading_idx >= 0, "test setup invariant: expected another '## ' heading after the block"
    next_heading_idx += 1  # point at the '#' itself, past the leading newline

    assert heading_idx < rule9_idx < next_heading_idx, (
        f"rule-9 token (idx {rule9_idx}) must lie between the GROUNDED CITATION "
        f"CONTRACT heading (idx {heading_idx}) and the next '## ' heading "
        f"(idx {next_heading_idx})"
    )


# ─── AC3: rule 9 persists on cycle-2 revision prompt ───────────────────────


def test_new_symbol_ban_persists_on_cycle2_revision(tmp_path):
    """AC3: rule 9 must still be present when _build_spec_prompt is invoked
    on a cycle-2 (revision) _prev payload — grounding discipline must not be
    dropped on retry.

    Today: rule 9 doesn't exist at all -> FAILS regardless of cycle.
    """
    scratchpad = tmp_path / "scratch"
    prev = {"cycle": 2, "findings": "## Findings\n- some prior issue\n"}
    prompt = get_spec_prompt(scratchpad, _prev=prev)

    assert "must NEVER appear in citation form" in prompt, (
        "rule-9 citation-form ban must persist on cycle-2 revision prompt"
    )
    assert "E_SPEC_CITE_LINT_FAIL" in prompt, (
        "rule-9 E_SPEC_CITE_LINT_FAIL rationale must persist on cycle-2 revision prompt"
    )


# ─── AC4: _SPEC_STABLE_PREFIX untouched by the rule-9 change (guard) ──────


def test_spec_stable_prefix_untouched_by_new_symbol_ban():
    """AC4 (guard): _SPEC_STABLE_PREFIX must NOT carry any of the rule-9
    citation-ban tokens (rule 9 belongs solely to
    `_grounded_citation_contract()`, appended separately per spec §2), and
    its known start/end anchors must be intact.

    This test PASSES today (rule 9 doesn't exist anywhere yet) and must
    still PASS after GREEN (rule 9 landed in the contract fn, not the
    stable prefix) — it is a leak guard, not a forcing function for GH934.
    """
    from phase_45_spec import _SPEC_STABLE_PREFIX

    assert "must NEVER appear in citation form" not in _SPEC_STABLE_PREFIX, (
        "rule-9 citation-form ban token leaked into _SPEC_STABLE_PREFIX"
    )
    assert "E_SPEC_CITE_LINT_FAIL" not in _SPEC_STABLE_PREFIX, (
        "rule-9 E_SPEC_CITE_LINT_FAIL token leaked into _SPEC_STABLE_PREFIX"
    )
    assert "reserved for EXISTING code" not in _SPEC_STABLE_PREFIX, (
        "rule-9 'reserved for EXISTING code' token leaked into _SPEC_STABLE_PREFIX"
    )

    assert _SPEC_STABLE_PREFIX.startswith("VALIDATION-LINE GUARD:"), (
        "_SPEC_STABLE_PREFIX must still start with its known VALIDATION-LINE GUARD anchor"
    )
    assert "SPEC-LINT PARITY RULES" in _SPEC_STABLE_PREFIX, (
        "_SPEC_STABLE_PREFIX must still contain its known SPEC-LINT PARITY RULES anchor"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
