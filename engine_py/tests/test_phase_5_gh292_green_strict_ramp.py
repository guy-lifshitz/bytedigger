"""GH #292 PR-3 RED tests — GREEN-prompt mypy strict-ramp grounding block.

Spec: SHARED/memory/Decisions/2026-07-13_GH292_pr3_green_strict_ramp_prompt_spec.md
(REVISED 2026-07-13: the strict-ramp block is now CONDITIONAL on
`_spec_wants_py_gates(spec_text)`, mirroring the existing python TYPECHECK
(GH596) grounding line, not unconditional. AC7 is a new negative/anti-bloat
guard.)

`_GREEN_STRICT_RAMP_BLOCK` does not exist yet in `_build_green_prompt`, so
AC1-AC6 below FAIL against current production code (no strict-ramp block
appended to the assembled GREEN prompt for a python-surface build). AC7
PASSES today (the marker is absent everywhere, including the non-python
path) — it is a regression guard that must stay green through GREEN too,
proving the block does not leak onto builds whose spec carries no `.py`
token. Mirrors `test_phase_5_gh340_security_fragment.py`'s `_build_green`
helper — the real builder is called, never mocked.
"""
from __future__ import annotations

from pathlib import Path

# ─── helpers (mirrored from test_phase_5_gh340_security_fragment.py, extended
# with a parameterized spec_text so tests control `_spec_wants_py_gates`) ────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_validation_pass(scratchpad: Path, *, spec_text: str):
    """Synthetic prev StepResult shaped like gate_on_validation output, with
    real touched fixture files (per §1y/§3 fixture pattern). The spec file
    content is `spec_text` — `.py`-bearing for AC1-AC6, `.py`-free for AC7 —
    so `_spec_wants_py_gates(spec_text)` (read by the real builder via
    `spec_path.read_text()`) is deterministically true/false."""
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    spec_path = scratchpad / "specs" / "build-spec.md"
    red_log_path = scratchpad / "tests" / "build-red-output.log"
    validation_doc_path = scratchpad / "reviews" / "build-opus-validation.md"
    for p in (red_log_path, validation_doc_path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture\n", encoding="utf-8")
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec_text, encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec_path),
            "red_log_path": str(red_log_path),
            "validation_doc_path": str(validation_doc_path),
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )


def _build_green(
    tmp_path: Path,
    *,
    question: str = "Add foo to bar",
    spec_text: str = "# fixture spec — modifies engine_py/foo.py\n",
    **org_extra,
):
    """Calls the real `_build_green_prompt` builder. Default `spec_text`
    contains a `.py` token so `_spec_wants_py_gates` is true (AC1-AC6 path).
    AC7 passes a `.py`-free `spec_text` to exercise the negative path."""
    from bytedigger_engine.workflows.phase_5_implement import _build_green_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, question=question, **org_extra)
    prev = _prev_validation_pass(scratchpad, spec_text=spec_text)
    return _build_green_prompt(ctx, prev)


# ─── AC1: strict-ramp block marker present, status=ok (python-surface build) ─


def test_gh292_ac1_green_prompt_contains_strict_ramp_marker(tmp_path):
    result = _build_green(tmp_path)
    assert result.status == "ok", f"_build_green_prompt returned {result.status!r}: {getattr(result, 'error', None)!r}"
    prompt = result.data["prompt"]
    assert "STRICT-TYPING BOY SCOUT" in prompt, (
        "GREEN prompt must contain the strict-ramp block marker "
        "'STRICT-TYPING BOY SCOUT' (GH292 PR-3 §2) when the spec references "
        "a python surface (`_spec_wants_py_gates` true)"
    )


# ─── AC2: names the strict list file ─────────────────────────────────────────


def test_gh292_ac2_green_prompt_names_strict_modules_list(tmp_path):
    result = _build_green(tmp_path)
    prompt = result.data["prompt"]
    assert "mypy-strict-modules.txt" in prompt, (
        "GREEN prompt must name the strict list file 'mypy-strict-modules.txt'"
    )


# ─── AC3: states the WHY — boy-scout gate, GH292 Step 2, fail-CI phrase ──────


def test_gh292_ac3_green_prompt_states_why_boyscout_gate_and_ci_fail(tmp_path):
    result = _build_green(tmp_path)
    prompt = result.data["prompt"]
    assert "boy-scout" in prompt, "GREEN prompt must reference the boy-scout diff-vs-list gate"
    assert "GH292 Step 2" in prompt, "GREEN prompt must reference GH292 Step 2"

    block_start = prompt.index("STRICT-TYPING BOY SCOUT")
    block_end = prompt.index("RESPONSE BUDGET")
    block = prompt[block_start:block_end]
    assert ("fail" in block) or ("FAIL" in block), (
        "strict-ramp block must state a fail-CI consequence phrase "
        "(substring 'fail' or 'FAIL' within the block region)"
    )


# ─── AC4: names the deferral escape (kill-by: allowlist entry form) ─────────


def test_gh292_ac4_green_prompt_names_killby_deferral_escape(tmp_path):
    result = _build_green(tmp_path)
    prompt = result.data["prompt"]
    assert "kill-by:" in prompt, (
        "GREEN prompt must name the deferral escape form using 'kill-by:'"
    )


# ─── AC5: block sits in the stable static-instruction region ────────────────


def test_gh292_ac5_green_strict_ramp_block_position_stable_region(tmp_path):
    result = _build_green(tmp_path)
    prompt = result.data["prompt"]
    assert "STRICT-TYPING BOY SCOUT" in prompt, "strict-ramp block marker not present in GREEN prompt yet"
    role_idx = prompt.index("You are the GREEN worker")
    block_idx = prompt.index("STRICT-TYPING BOY SCOUT")
    budget_idx = prompt.index("RESPONSE BUDGET")
    assert role_idx < block_idx < budget_idx, (
        "strict-ramp block must sit between the GREEN role line and the "
        "RESPONSE BUDGET section — stable-prefix requirement"
    )


# ─── AC6: call-invariant — exactly once, byte-identical, question-independent
#          (SAME .py-bearing spec for both questions — gate depends on the
#          spec, not the question) ────────────────────────────────────────────


def test_gh292_ac6_green_strict_ramp_block_call_invariant(tmp_path):
    result = _build_green(tmp_path)
    prompt = result.data["prompt"]

    # exactly once
    block_marker = "STRICT-TYPING BOY SCOUT"
    assert prompt.count(block_marker) == 1, (
        f"strict-ramp block marker must appear exactly once, found {prompt.count(block_marker)}"
    )

    # byte-identical across two calls
    result_a = _build_green(tmp_path)
    result_b = _build_green(tmp_path)
    prompt_a = result_a.data["prompt"]
    prompt_b = result_b.data["prompt"]
    block_a = prompt_a[prompt_a.index("STRICT-TYPING BOY SCOUT"):prompt_a.index("RESPONSE BUDGET")]
    block_b = prompt_b[prompt_b.index("STRICT-TYPING BOY SCOUT"):prompt_b.index("RESPONSE BUDGET")]
    assert block_a == block_b, (
        "strict-ramp block must be byte-identical across two consecutive "
        "_build_green_prompt calls (call-invariant, stable-prefix cacheable)"
    )

    # present regardless of ctx.question (empty vs non-empty) — SAME
    # .py-bearing spec_text (default) for both calls, so this proves the gate
    # tracks the spec, not the question.
    result_empty_q = _build_green(tmp_path, question="")
    result_nonempty_q = _build_green(tmp_path, question="Refactor the widget loader")
    prompt_empty_q = result_empty_q.data["prompt"]
    prompt_nonempty_q = result_nonempty_q.data["prompt"]
    assert block_marker in prompt_empty_q, (
        "strict-ramp block must be present when ctx.question is empty, "
        "given a .py-bearing spec (gate is spec-driven, not question-driven)"
    )
    assert block_marker in prompt_nonempty_q, (
        "strict-ramp block must be present when ctx.question is non-empty, "
        "given a .py-bearing spec (gate is spec-driven, not question-driven)"
    )


# ─── AC7 (NEW, revision 2026-07-13): negative / anti-bloat guard — block
#          absent when the spec does NOT reference a python surface ─────────


def test_gh292_ac7_block_absent_without_py_surface(tmp_path):
    result = _build_green(
        tmp_path,
        spec_text="# fixture spec — no python surface here\n",
    )
    assert result.status == "ok", f"_build_green_prompt returned {result.status!r}: {getattr(result, 'error', None)!r}"
    prompt = result.data["prompt"]
    assert "STRICT-TYPING BOY SCOUT" not in prompt, (
        "strict-ramp block must NOT appear when the spec does not reference "
        "a python surface (no `.py` token) — keeps the base non-python GREEN "
        "prompt unchanged and under the 228AB822 6 KB ceiling"
    )
