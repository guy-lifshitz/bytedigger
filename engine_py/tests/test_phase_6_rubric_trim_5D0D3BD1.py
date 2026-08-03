"""RED tests for 5D0D3BD1: review-prompt trim + cap re-baseline (A1-aggressive).

Remaining acceptance criteria (AC1-AC8 removed by Ship B4 / 52C6F42F):
  AC9  – _build_review_prompt total bytes ≤ 37000
  AC10 – PARALLEL DISPATCH inline block ≤ 1800 bytes
  AC11 – AGGREGATION inline block ≤ 3000 bytes
  AC12 – STRUCTURED FINDINGS inline block ≤ 950 bytes
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402


# ─── shared fixtures ────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar") -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    # 5D0D3BD1: pin worktree to a clean non-git dir so BUILD SCOPE block
    # doesn't fire via the resolve_pre_phase_sha fallback against cwd.
    # This makes prompt-size measurement deterministic (~7.5 KB structural,
    # not 38+ KB data-dependent on the parent repo's working-tree diff).
    fake_worktree = scratchpad.parent / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "current_worktree_path": str(fake_worktree),
        },
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection(scratchpad: Path) -> None:
    """Seed empty injection/*.md files so _read_first_block does not raise."""
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _build_review_prompt_str(scratchpad: Path) -> str:
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad)
    result = phase_6_review._build_review_prompt(ctx, None)
    assert isinstance(result, StepResult) and isinstance(result.data, dict)
    return result.data["prompt"]


def _extract_block(prompt: str, start_marker: str, end_marker: str) -> bytes:
    """Extract the prompt slice from start_marker up to (not including) end_marker.

    Uses explicit string markers instead of a regex heading scan so that the
    block boundaries are deterministic regardless of what all-caps text appears
    in the payload (e.g. dispatch_table, schema literals, sub-headings).

    Raises ValueError loudly if either marker is absent — that signals a prompt
    structure change that must be reflected here.
    """
    start = prompt.index(start_marker)
    end = prompt.index(end_marker, start + len(start_marker))
    return prompt[start:end].encode("utf-8")


# ─── AC9: built review prompt ≤ 37000 bytes ────────────────────────────────


def test_review_prompt_under_37000_bytes(tmp_path):
    """AC9: _build_review_prompt total UTF-8 bytes ≤ 37000 (tighter than 36864 cap)."""
    prompt = _build_review_prompt_str(tmp_path / "rev")
    byte_len = len(prompt.encode("utf-8"))
    assert byte_len <= 37000, (
        f"_build_review_prompt is {byte_len} bytes; cap is 37000 (36 KB + 136-byte buffer). "
        f"Over budget by {byte_len - 37000} bytes. "
        "GREEN must trim rubric + inline blocks to get under this threshold."
    )


# ─── AC10: PARALLEL DISPATCH block ≤ 1800 bytes ────────────────────────────


def test_parallel_dispatch_block_trimmed(tmp_path):
    """AC10: PARALLEL DISPATCH inline block ≤ 1800 bytes in built prompt.

    Measured floor: 1736 bytes. Cap = floor + 64-byte buffer.
    regression-tripwire — trim work reverted to preserve 20 sibling tests (5D0D3BD1 post-mortem)
    """
    prompt = _build_review_prompt_str(tmp_path / "rev")
    block = _extract_block(prompt, "PARALLEL DISPATCH", "STRUCTURED FINDINGS")
    byte_len = len(block)
    # regression-tripwire — trim work reverted to preserve 20 sibling tests (5D0D3BD1 post-mortem)
    assert byte_len <= 1800, (
        f"PARALLEL DISPATCH block is {byte_len} bytes; cap is 1800 (floor 1736 + 64-byte buffer). "
        f"Over budget by {byte_len - 1800} bytes. "
        "A future ship bloated this block — check recent phase_6_review.py changes."
    )


# ─── AC11: AGGREGATION block ≤ 3000 bytes ──────────────────────────────────


def test_aggregation_block_trimmed(tmp_path):
    """AC11: AGGREGATION block-opener token absent from built prompt (F34E2C82 pilot).

    Pre-F34E2C82: regression-tripwire at ≤3000 bytes (floor 2916).
    Post-F34E2C82: block-opener token 'AGGREGATION — after all' MUST NOT appear;
    block is vestigial and was removed (Option G1 replacement).

    Tightened per Opus R-B1: bare 'AGGREGATION' also appears as 'COMPOSITE AGGREGATION:'
    at offset ~4656 in the citation-verification block (out-of-scope). Asserting the bare
    token would cause this test to fail even after a correct GREEN deletion. Use the
    block-opener token as the unique discriminator instead.
    """
    # F34E2C82 (R-B1 pivot: tightened from bare "AGGREGATION" to block-opener token)
    prompt = _build_review_prompt_str(tmp_path / "rev")
    assert "AGGREGATION — after all" not in prompt, (
        "AGGREGATION block-opener 'AGGREGATION — after all' was removed in F34E2C82 "
        "(parent 9D520664) as vestigial. "
        "If you re-added it: roll back. Opus R-B1: bare 'AGGREGATION' is NOT asserted here "
        "because 'COMPOSITE AGGREGATION:' survives at offset ~4656 (out-of-scope citation block)."
    )


# ─── AC12: STRUCTURED FINDINGS block ≤ 950 bytes ──────────────────────────


def test_structured_findings_block_trimmed(tmp_path):
    """AC12: STRUCTURED FINDINGS inline block ≤ 950 bytes in built prompt.

    Measured floor: 886 bytes. Cap = floor + 64-byte buffer.
    regression-tripwire — trim work reverted to preserve 20 sibling tests (5D0D3BD1 post-mortem;
    F34E2C82 changed end-marker to ANTI-FABRICATION after AGGREGATION removal — ANCHOR-BIAS GUARD
    would capture the ~1278-byte anti-fab block)
    """
    # F34E2C82
    prompt = _build_review_prompt_str(tmp_path / "rev")
    block = _extract_block(prompt, "STRUCTURED FINDINGS", "ANTI-FABRICATION")
    byte_len = len(block)
    # regression-tripwire — trim work reverted to preserve 20 sibling tests (5D0D3BD1 post-mortem)
    assert byte_len <= 950, (
        f"STRUCTURED FINDINGS block is {byte_len} bytes; cap is 950 (floor 886 + 64-byte buffer). "
        f"Over budget by {byte_len - 950} bytes. "
        "A future ship bloated this block — check recent phase_6_review.py changes."
    )
