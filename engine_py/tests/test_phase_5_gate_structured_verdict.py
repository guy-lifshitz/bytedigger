"""RED tests for F2C256A5 — phase_5 gate consumes structured ValidationVerdict.approve.

Agreement: F2C256A5 (95D3E5F6 G1 band-aid #7)

Contract (GREEN will implement):
1. _write_validation_doc returns `structured_verdict` key in StepResult.data.
2. _gate_on_validation uses structured_verdict.approve when present instead of
   the markdown VERDICT_PASS/VERDICT_FAIL comparison.

All tests MUST FAIL until GREEN implements the contract.
Do NOT implement any production change here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

import phase_5_implement  # noqa: E402  (needed for monkeypatch target)
from contracts import StepResult  # noqa: E402
from phase_5_implement import (  # noqa: E402
    _gate_on_validation,
    _write_validation_doc,
    VERDICT_PASS,
    VERDICT_FAIL,
    MAX_VALIDATION_CYCLES,
    ValidationVerdict,
)


# ─── structured-block markdown builders ──────────────────────────────────────
# These are the exact formats _parse_validation_structured expects.

_VALID_BLOCK_APPROVE = (
    "## validation-output (structured)\n"
    "```json\n"
    '{"approve": true, "reject_reason": null}\n'
    "```"
)

_VALID_BLOCK_REJECT = (
    "## validation-output (structured)\n"
    "```json\n"
    '{"approve": false, "reject_reason": "missing edge-case coverage"}\n'
    "```"
)

_VERDICT_PASS_MD = "## Verdict\nVerdict: PASS\n"
_VERDICT_FAIL_MD = "## Verdict\nVerdict: FAIL\n"


def _raw(verdict_md: str, block: str | None) -> str:
    return "\n".join([verdict_md] + ([block] if block is not None else []))


# ─── fixture builders ─────────────────────────────────────────────────────────


def _make_write_prev(tmp_path: Path, raw: str) -> StepResult:
    """Prev step result for _write_validation_doc (simulates invoke_validation_llm)."""
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(tmp_path / "validation.md"),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(tmp_path / "red.log"),
            "red_test_paths": [],
            "cycle": 1,
            "red_commit_sha": "deadbeef",
        },
        duration_ms=0,
        step_name="invoke_validation_llm",
    )


def _make_gate_prev(
    verdict: str,
    cycle: int,
    structured: "ValidationVerdict | None",
) -> StepResult:
    """Prev step result for _gate_on_validation (simulates write_validation_doc output).

    Only injects the 'structured_verdict' key when structured is not None, so
    the backward-compat "key absent" path is also exercisable.
    """
    base: dict = {
        "verdict": verdict,
        "cycle": cycle,
        "validation_doc_path": "/tmp/v.md",
        "spec_path": "/tmp/s.md",
        "red_log_path": "/tmp/r.log",
        "validation_raw": "...",
        "red_commit_sha": "deadbeef",
        "red_test_paths": [],
    }
    if structured is not None:
        base["structured_verdict"] = structured
    return StepResult(
        status="ok",
        data=base,
        duration_ms=0,
        step_name="write_validation_doc",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TestWriteValidationDocThreadsStructuredVerdict
# ═══════════════════════════════════════════════════════════════════════════════


class TestWriteValidationDocThreadsStructuredVerdict:
    """AC1–AC3: _write_validation_doc must add 'structured_verdict' to result data."""

    def test_write_doc_threads_structured_verdict_approve(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC1: structured block with approve=true → structured_verdict is a
        ValidationVerdict with approve=True in StepResult.data."""
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )
        raw = _raw(_VERDICT_PASS_MD, _VALID_BLOCK_APPROVE)
        r = _write_validation_doc(None, _make_write_prev(tmp_path, raw))

        assert r.status == "ok"
        assert "structured_verdict" in r.data, (
            "expected 'structured_verdict' key in _write_validation_doc result.data"
        )
        sv = r.data["structured_verdict"]
        assert isinstance(sv, ValidationVerdict), (
            f"expected ValidationVerdict instance, got {type(sv)}"
        )
        assert sv.approve is True

    def test_write_doc_threads_structured_verdict_reject(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC2: structured block with approve=false → structured_verdict.approve=False
        and reject_reason threaded through."""
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )
        raw = _raw(_VERDICT_PASS_MD, _VALID_BLOCK_REJECT)
        r = _write_validation_doc(None, _make_write_prev(tmp_path, raw))

        assert "structured_verdict" in r.data
        sv = r.data["structured_verdict"]
        assert isinstance(sv, ValidationVerdict)
        assert sv.approve is False
        assert sv.reject_reason == "missing edge-case coverage"

    def test_write_doc_structured_verdict_none_when_no_block(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC3: no structured block → structured_verdict is None, all existing keys survive."""
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )
        raw = _VERDICT_PASS_MD  # no structured block
        r = _write_validation_doc(None, _make_write_prev(tmp_path, raw))

        assert "structured_verdict" in r.data, (
            "expected 'structured_verdict' key even when block absent (value should be None)"
        )
        assert r.data["structured_verdict"] is None
        # Existing keys must survive unchanged
        assert r.data["verdict"] == VERDICT_PASS
        assert "validation_raw" in r.data
        assert r.data["cycle"] == 1
        assert r.data["red_commit_sha"] == "deadbeef"


# ═══════════════════════════════════════════════════════════════════════════════
# TestGateConsumesStructuredVerdict
# ═══════════════════════════════════════════════════════════════════════════════


class TestGateConsumesStructuredVerdict:
    """AC4, AC5, AC6, AC7: _gate_on_validation must use structured_verdict.approve
    when the key is present, falling back to markdown verdict only when absent."""

    def test_gate_rejects_when_markdown_pass_but_structured_reject(self) -> None:
        """AC4 (core proof): structured.approve=False overrides markdown PASS.
        Even at MAX_VALIDATION_CYCLES (no retry possible), gate must return error."""
        prev = _make_gate_prev(
            VERDICT_PASS,
            MAX_VALIDATION_CYCLES,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "error", (
            "expected gate to block when structured.approve=False, "
            f"even though markdown verdict={VERDICT_PASS!r}"
        )
        assert r.error_code == "E_VALIDATION_FAILED"

    def test_gate_proceeds_when_markdown_fail_but_structured_approve(self) -> None:
        """AC5 (inverse): structured.approve=True overrides markdown FAIL.
        Gate should return the 'proceed' ok dict (no findings, has red_commit_sha)."""
        prev = _make_gate_prev(
            VERDICT_FAIL,
            1,
            ValidationVerdict(approve=True, reject_reason=None),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "ok", (
            "expected gate to proceed when structured.approve=True, "
            f"even though markdown verdict={VERDICT_FAIL!r}"
        )
        # Must be the "proceed" branch, not the loop-continue branch
        assert "red_commit_sha" in r.data, (
            "proceed dict must contain red_commit_sha"
        )
        assert "findings" not in r.data, (
            "proceed dict must NOT contain findings (that belongs to loop-continue)"
        )
        # cycle should NOT be incremented in the proceed path
        assert r.data.get("cycle") == 1

    def test_gate_loop_continue_when_structured_reject_under_cap(self) -> None:
        """AC7: structured.approve=False at cycle < MAX_VALIDATION_CYCLES triggers
        loop-continue (status=ok with verdict + incremented cycle + findings)."""
        prev = _make_gate_prev(
            VERDICT_FAIL,
            1,  # cycle 1 < MAX_VALIDATION_CYCLES (2)
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "ok", (
            "expected loop-continue (status=ok) when structured rejects at cycle<cap"
        )
        # LoopRunner uses verdict to detect non-PASS and re-iterate
        assert r.data["verdict"] == VERDICT_FAIL, (
            "loop-continue must preserve the markdown verdict for LoopRunner marker check"
        )
        assert r.data["cycle"] == 2, (
            f"expected cycle incremented to 2, got {r.data.get('cycle')}"
        )
        assert "findings" in r.data, "loop-continue dict must include findings"

    def test_gate_falls_back_to_markdown_when_no_structured(self) -> None:
        """AC6 (backward-compat regression guard — may already pass pre-fix).

        When 'structured_verdict' key is absent, gate falls back to the markdown
        verdict for the pass/fail decision. This is a regression guard for the
        existing behavior that GREEN must preserve.
        """
        # (a) markdown PASS, no structured key → proceed
        prev_pass = _make_gate_prev(VERDICT_PASS, 1, None)
        r_pass = _gate_on_validation(None, prev_pass)

        assert r_pass.status == "ok", (
            "backward-compat: markdown PASS with no structured key should proceed"
        )
        assert "red_commit_sha" in r_pass.data, (
            "proceed dict must contain red_commit_sha"
        )

        # (b) markdown FAIL at MAX_VALIDATION_CYCLES, no structured key → terminal error
        prev_fail = _make_gate_prev(VERDICT_FAIL, MAX_VALIDATION_CYCLES, None)
        r_fail = _gate_on_validation(None, prev_fail)

        assert r_fail.status == "error", (
            "backward-compat: markdown FAIL at cap with no structured key should error"
        )
        assert r_fail.error_code == "E_VALIDATION_FAILED"
