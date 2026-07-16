"""RED tests for HAL agreement 8A9C0F24 Subtask B — REVISION block prompt
ordering + anti-changelog rule in ``_build_spec_prompt``.

Two production failures motivate Subtask B:

  - 886765EC (Sonnet writer): cycle-2 wrote a 10-line changelog instead of a
    full spec body. Both cycles byte-identical 1453B stub.
  - CE66FA4D (Opus writer): cycle-2 fabricated bare-filename citations 4×
    despite explicit grounding contract.

RCA: in ``_build_spec_prompt`` the REVISION block bullet list reads
top-down. Today's first bullet is «Address every concern listed under
## Findings» — writers anchor on that and produce a diff/changelog of
fixes. The «response IS the spec file» rule is the LAST bullet, so
attention has already been spent.

GREEN step (under 35F1A4EB manual subagent flow) must:

  1. Promote «response IS the spec» rule to the FIRST bullet.
  2. Add an explicit anti-changelog/anti-diff bullet in the same block.

These are mechanical string asserts — no LLM, no network, no engine
execution. Each test calls ``_build_spec_prompt`` directly with a
constructed ctx and a cycle-2 ``_prev`` dict, then asserts substring
shape on the returned ``StepResult.data["prompt"]``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402


# ─── helpers (mirrors test_phase_45_spec_A813CA08.py boilerplate) ─────────────


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-8A9C0F24",
        persona="hal",
        framework=None,
        domain=None,
    )


def get_revision_prompt(scratchpad: Path, *, cycle: int = 2, findings: str = "stub finding text") -> str:
    """Drive _build_spec_prompt with cycle≥2 ``_prev`` dict (matches
    engine retry hook injection — see phase_45_spec.py L357 isinstance dict)."""
    from phase_45_spec import _build_spec_prompt

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle, "findings": findings}
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"]


def get_cycle1_prompt(scratchpad: Path) -> str:
    from phase_45_spec import _build_spec_prompt

    ctx = make_ctx(scratchpad)
    result = _build_spec_prompt(ctx, None)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"]


def _first_bullet_after_rules_header(prompt: str) -> str:
    """Find the line ``RULES FOR THIS REVISION:`` and return the next
    non-blank line that starts with ``  - ``. Raises if not found."""
    lines = prompt.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "RULES FOR THIS REVISION:" in line:
            header_idx = i
            break
    assert header_idx is not None, "RULES FOR THIS REVISION: header not found in prompt"

    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            return line
        # Non-blank, non-bullet line means we left the bullet list.
        break
    raise AssertionError("no bullet line found after RULES FOR THIS REVISION: header")


# ─── tests ────────────────────────────────────────────────────────────────────


def test_revision_block_first_bullet_is_response_is_spec_rule(tmp_path):
    """RED: the FIRST bullet under ``RULES FOR THIS REVISION:`` must contain
    ``response IS the spec`` (case-insensitive). Today the first bullet is
    «Address every concern» — the writer reads top-down and produces a
    changelog of fixes instead of a full spec."""
    prompt = get_revision_prompt(tmp_path)
    first_bullet = _first_bullet_after_rules_header(prompt)
    assert re.search(r"IS the spec file", first_bullet, flags=re.IGNORECASE), (
        f"expected first bullet to contain 'IS the spec file' "
        f"(case-insensitive); got: {first_bullet!r}"
    )


def test_revision_block_contains_no_changelog_rule(tmp_path):
    """RED: the REVISION block must contain wording that explicitly forbids
    changelog/diff output. Without this, Sonnet writers default to
    summarizing fixes rather than re-emitting the spec."""
    prompt = get_revision_prompt(tmp_path)
    acceptable_patterns = [
        r"do not describe what you changed",
        r"do not list changes",
        r"not a diff",
        r"not a changelog",
    ]
    matched = [p for p in acceptable_patterns if re.search(p, prompt, flags=re.IGNORECASE)]
    assert matched, (
        "expected at least one anti-changelog phrase in the REVISION block "
        f"(any of {acceptable_patterns!r}); none found. "
        f"Prompt tail (last 800 chars): {prompt[-800:]!r}"
    )


def test_revision_block_still_has_existing_rules(tmp_path):
    """Regression guard: existing rules must SURVIVE the reorder — protects
    against accidental rule loss when the GREEN step rewrites the block."""
    prompt = get_revision_prompt(tmp_path)
    required = [
        r"address every concern",
        r"do not widen scope",
        r"## open questions",
        r"do not add features",
        r"speckit",
        r"## context",
    ]
    missing = [p for p in required if not re.search(p, prompt, flags=re.IGNORECASE)]
    assert not missing, (
        f"existing REVISION rules dropped after reorder: {missing!r}. "
        f"Prompt tail (last 800 chars): {prompt[-800:]!r}"
    )


def test_cycle_1_has_no_revision_block(tmp_path):
    """Invariant: cycle-1 (no _prev) must NOT contain a REVISION block.
    Protects against accidentally always-on revision rules."""
    prompt = get_cycle1_prompt(tmp_path)
    assert "## REVISION" not in prompt, (
        "cycle-1 prompt must not contain a REVISION block; "
        f"prompt head (first 600 chars): {prompt[:600]!r}"
    )
