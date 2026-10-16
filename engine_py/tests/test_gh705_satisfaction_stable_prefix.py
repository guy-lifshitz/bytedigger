"""RED tests for GH705 — wire the satisfaction fan-out call-site through the
already-shipped {prompt, stable_prefix} seam (v3, GH334 §2.0, frozen).

Spec: SHARED/memory/Decisions/2026-07-13_GH705_satisfaction_callsite_spec.md

The seam itself (invoke_llm_subprocess, per-adapter split) is SHIPPED and
UNCHANGED (#703). This file tests ONLY the satisfaction builder
(`_build_satisfaction_prompt`) + the fan-out helper
(`_run_satisfaction_evaluators_parallel`): the builder must emit
`data["stable_prefix"]` (a call-invariant, `len>500`, `count==1` substring of
`data["prompt"]`), and the fan-out helper must accept a `stable_prefix`
keyword and pass it through to every `invoke_llm_subprocess` submission.

The not-yet-existing symbol `_SATISFACTION_STABLE_PREFIX` is NEVER imported
at module top-level — it is accessed via `getattr(mod, ..., None)` INSIDE
each test body, per §1q ext / D1CF5FDF (avoids the non-collectable RED
collection hang: a top-level `from ... import _SATISFACTION_STABLE_PREFIX`
would raise ImportError at collect time, which hangs the engine's
red_runtime phase for ~30 min instead of failing fast at assert time).

sys.path is NOT touched here — conftest.py already installs the
conftest-import-time singleton (engine_py root + workflows dir) per
§1q/81F97F3D.

§1i: no singleton/time-dependent resource under test — no file locks, no
ports, no timing races. AC6/AC8 mock the DEPENDENCY (`invoke_llm_subprocess`)
deterministically; the ThreadPoolExecutor fan-out is joined synchronously
(`.result()` on every future) before assertions run, so there is no
concurrency race to pre-stage.

Stub-passability: only the DEPENDENCY `invoke_llm_subprocess` is patched.
The UUTs (`_build_satisfaction_prompt`, `_run_satisfaction_evaluators_parallel`,
`_SATISFACTION_STABLE_PREFIX`) are never mocked/patched.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.workflows import phase_6_review as _p6


# ═══════════════════════════════════════════════════════════════════════════
# Golden literal — verbatim copy of the current
# `PHASE-SPECIFIC ANTI-FAB (SATISFACTION)` parts.append(...) argument at
# phase_6_review.py ~:2541-2555 (frozen at RED time). AC2 proves the future
# `_SATISFACTION_STABLE_PREFIX` constant is byte-identical to this text —
# i.e. extraction did not alter the block.
# ═══════════════════════════════════════════════════════════════════════════

GOLDEN = (
    "PHASE-SPECIFIC ANTI-FAB (SATISFACTION):\n"
    "  - Each dimension score MUST be backed by an evidence bullet citing\n"
    "    a real file/test. Don't score blind. Don't default to 90+ because\n"
    "    nothing looks broken at a glance.\n"
    "  - Composite is MIN, not average. A 60 in any dimension = composite\n"
    "    60. Don't smooth it out.\n"
    "  - Genuinely n/a dimension (e.g. Boy Scout for SIMPLE) → write `n/a`\n"
    "    and exclude from MIN. Do NOT invent a score.\n"
    "  - Concerns = `none` only when you actually looked. Skimmed diff →\n"
    "    surface unverified parts as a concern.\n"
    "\n"
    "End your response with EXACTLY one of: `VERDICT: PASS` or `VERDICT: FAIL`.\n"
    "SCORE line MUST be present and numeric — missing/malformed score blocks pipeline."
)


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixture helpers (mirrors test_gh705_callsite_stable_prefix.py)
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", is_complex: bool = False):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    scratchpad.mkdir(parents=True, exist_ok=True)
    fake_worktree = scratchpad.parent / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    org_config = {
        "scratchpad_dir": str(scratchpad),
        "current_worktree_path": str(fake_worktree),
    }
    if is_complex:
        org_config["complexity"] = "COMPLEX"
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question=question,
        session_id="test-gh705-satisfaction",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection(scratchpad: Path) -> None:
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _build_satisfaction(tmp_path: Path, *, question: str, is_complex: bool = False):
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad, question=question, is_complex=is_complex)
    (scratchpad / "specs").mkdir(parents=True, exist_ok=True)
    (scratchpad / "specs" / "build-spec.md").write_text("## Context\nspec body\n")
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews" / "build-review.md").write_text("review body")
    (scratchpad / "reviews" / "build-fix-report.md").write_text("fix body")
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(scratchpad / "specs" / "build-spec.md"),
            "review_doc_path": str(scratchpad / "reviews" / "build-review.md"),
            "fix_doc_path": str(scratchpad / "reviews" / "build-fix-report.md"),
            "review_verdict": "PASS",
            "fix_commit_sha": "deadbeef",
        },
        duration_ms=0,
        step_name="write_fix_artifact",
    )
    return _p6._build_satisfaction_prompt(ctx, prev)


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — module constant exists, len > 500
# ═══════════════════════════════════════════════════════════════════════════


def test_AC1_satisfaction_stable_prefix_constant_exists_and_is_substantial():
    sp = getattr(_p6, "_SATISFACTION_STABLE_PREFIX", None)
    assert sp is not None, (
        "AC1: phase_6_review._SATISFACTION_STABLE_PREFIX must exist as a "
        "module-level constant"
    )
    assert len(sp) > 500, f"AC1: stable_prefix must be substantial (len>500); got {len(sp)}"


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — byte-identical to the golden (frozen) literal
# ═══════════════════════════════════════════════════════════════════════════


def test_AC2_satisfaction_stable_prefix_byte_identical_to_golden():
    sp = getattr(_p6, "_SATISFACTION_STABLE_PREFIX", None)
    assert sp == GOLDEN, (
        "AC2: _SATISFACTION_STABLE_PREFIX must be byte-identical to the original "
        "PHASE-SPECIFIC ANTI-FAB (SATISFACTION) literal — extraction must preserve bytes"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — builder returns data["stable_prefix"] == the constant
# ═══════════════════════════════════════════════════════════════════════════


def test_AC3_build_satisfaction_prompt_sets_stable_prefix_in_data(tmp_path):
    sp_const = getattr(_p6, "_SATISFACTION_STABLE_PREFIX", None)
    assert sp_const is not None, "precondition: AC1 constant must exist first"

    result = _build_satisfaction(tmp_path, question="Add foo to bar")
    assert result.status == "ok", f"builder failed: {result.error!r}"
    assert result.data.get("stable_prefix") == sp_const, (
        "AC3: _build_satisfaction_prompt(...).data['stable_prefix'] must equal "
        f"_SATISFACTION_STABLE_PREFIX; got {result.data.get('stable_prefix')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — stable_prefix occurs exactly once as a contiguous substring of prompt
# ═══════════════════════════════════════════════════════════════════════════


def test_AC4_stable_prefix_occurs_exactly_once_in_prompt(tmp_path):
    result = _build_satisfaction(tmp_path, question="Add foo to bar")
    sp = result.data.get("stable_prefix")
    assert sp, "precondition: stable_prefix must be present and non-empty (AC3)"
    prompt = result.data["prompt"]
    assert prompt.count(sp) == 1, (
        f"AC4: stable_prefix must occur exactly once in data['prompt']; "
        f"got count={prompt.count(sp)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC1/AC2 build-independence — two builds, different variable inputs,
# byte-identical stable_prefix
# ═══════════════════════════════════════════════════════════════════════════


def test_AC1_AC2_stable_prefix_build_independent_across_different_inputs(tmp_path):
    r1 = _build_satisfaction(tmp_path / "a", question="Add foo to bar", is_complex=False)
    r2 = _build_satisfaction(tmp_path / "b", question="A totally different feature request", is_complex=True)
    sp1 = r1.data.get("stable_prefix")
    sp2 = r2.data.get("stable_prefix")
    assert sp1, "precondition: build 1 must produce a stable_prefix"
    assert sp2, "precondition: build 2 must produce a stable_prefix"
    assert sp1 == sp2, (
        "AC1/AC2 build-independence: stable_prefix must be byte-identical across "
        "builds with different question text and is_complex True/False"
    )


# bd#317: AC6, AC7, AC8 (the fan-out helper `_run_satisfaction_evaluators_parallel`
# and the COMPLEX call-site wiring of stable_prefix) were removed with the helper.
# The builder ACs above stay: the builder still emits data["stable_prefix"].
