"""RED tests for GH781 — force a standalone VERDICT line via a PRE-SUBMISSION
CHECKLIST block appended to the integrity reviewer's output schema.

Spec: SHARED/memory/Decisions/2026-07-14_GH781_integrity_verdict_forcing_output_spec.md

UUT: `_integrity_output_schema()` in
`SYSTEM/cli/build/engine_py/workflows/phase_5_integrity.py` (pure, zero-arg).
GREEN will APPEND a "PRE-SUBMISSION CHECKLIST" block to the returned string.
Also touches `_integrity_stable_prefix()`, `_build_integrity_prompt(ctx, prev)`,
and `_classify_diff_verdict(ctx, prev)` in the same module (all read-only here).

Per §1q: UUTs are imported INSIDE each test body, never at module top level.
No `sys.path` mutation here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per 81F97F3D.

§1i: no singleton/time-dependent resource under test. AC5's fixture mocks the
DEPENDENCY `phase_5_integrity.git_port.git_read` (never the builder UUT itself)
deterministically — pre-staged, not raced. Harness pattern copied/adapted from
`test_gh705_callsite_stable_prefix.py`'s `_make_ctx` / `_seed_injection` /
`_build_integrity` helpers (private helpers are not imported across test
files — duplicated here per instruction).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers (adapted from test_gh705_callsite_stable_prefix.py)
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar") -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
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
        session_id="test-gh781",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection(scratchpad: Path) -> None:
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


_FAKE_DIFF = (
    "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/tests/test_foo.py\n"
    "+++ b/tests/test_foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    "-assert x == 1\n"
    "+assert x == 2\n"
)


def _build_integrity(tmp_path: Path, *, question: str, diff_text: str):
    from bytedigger_engine.workflows.phase_5_integrity import _build_integrity_prompt  # noqa: PLC0415
    from bytedigger_engine.lib.git_port import GitResult  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question)
    fake_result = GitResult(returncode=0, stdout=diff_text, stderr="", timed_out=False)
    # Mock the DEPENDENCY (git_port.git_read), never the builder UUT.
    with patch("bytedigger_engine.workflows.phase_5_integrity.git_port.git_read", return_value=fake_result):
        return _build_integrity_prompt(ctx, None)


# ═══════════════════════════════════════════════════════════════════════════
# AC1-4 — direct assertions on _integrity_output_schema()
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_checklist_present():
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_output_schema  # noqa: PLC0415

    assert "PRE-SUBMISSION CHECKLIST" in _integrity_output_schema()


def test_ac2_standalone_verdict_item():
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_output_schema  # noqa: PLC0415

    schema = _integrity_output_schema()
    assert "ENDS with a standalone line that STARTS with `VERDICT: `" in schema
    assert "Prose alone is NOT a verdict" in schema


def test_ac3_failclosed_mnemonic():
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_output_schema  # noqa: PLC0415

    assert "no standalone VERDICT line is REJECTED by the gate" in _integrity_output_schema()


def test_ac4_no_nochanges_in_menu():
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_output_schema  # noqa: PLC0415

    assert "NO_CHANGES" not in _integrity_output_schema()


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — wired into the real prompt via _build_integrity_prompt, and present
# exactly once inside the call-invariant stable_prefix.
# ═══════════════════════════════════════════════════════════════════════════


def test_ac5_wired_and_stable_prefix(tmp_path):
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_stable_prefix  # noqa: PLC0415

    r = _build_integrity(tmp_path, question="Add foo to bar", diff_text=_FAKE_DIFF)
    assert r.status == "ok", f"builder failed: {r.error!r}"

    prompt = r.data["prompt"]
    assert "PRE-SUBMISSION CHECKLIST" in prompt
    assert prompt.count("PRE-SUBMISSION CHECKLIST") == 1

    stable_prefix = r.data["stable_prefix"]
    assert stable_prefix == _integrity_stable_prefix()
    assert prompt.count(stable_prefix) == 1
    assert "PRE-SUBMISSION CHECKLIST" in stable_prefix


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — regression-lock: prose-only mention of SPEC_CHANGE (no standalone
# VERDICT line) must still hard-block via E_INTEGRITY_NO_MARKER. Passes both
# pre- and post-GREEN (no tolerant prose->PASS path exists or is introduced).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac6_prose_only_still_blocks(tmp_path):
    from bytedigger_engine.workflows.phase_5_integrity import _classify_diff_verdict  # noqa: PLC0415

    doc_path = tmp_path / "reviews" / "build-integrity-review.md"
    diff_path = str(tmp_path / "integrity" / "test-diff.patch")

    prose = (
        "This diff modifies two assertions in tests/test_foo.py to reflect an "
        "updated spec requirement around boundary handling.\n\n"
        "In my judgment this is a legitimate spec-driven change (SPEC_CHANGE) "
        "and the assertions were correctly updated to match the new documented "
        "boundary behavior described in the feature request.\n\n"
        "No gaming of assertions was observed; the code paths exercised are "
        "unchanged and the refactor is otherwise clean and well-scoped."
    )

    prev = StepResult(
        status="ok",
        data={
            "doc_path": str(doc_path),
            "diff_path": diff_path,
            "raw_response": prose,
        },
        duration_ms=0,
        step_name="invoke_integrity_llm",
    )

    result = _classify_diff_verdict(None, prev)

    assert result.status == "error"
    assert result.error_code == "E_INTEGRITY_NO_MARKER"


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — terminal placement: the checklist must be the schema's FINAL block so
# it reads as the reviewer's last instruction before submitting (forcing
# efficacy depends on terminal placement; closes the mid-schema-insert
# trivial-GREEN gap flagged by the validation gate).
# ═══════════════════════════════════════════════════════════════════════════


def test_ac7_terminal_placement():
    from bytedigger_engine.workflows.phase_5_integrity import _integrity_output_schema  # noqa: PLC0415

    assert _integrity_output_schema().rstrip().endswith("the omission itself blocks.")
