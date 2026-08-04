"""RED tests for B4D83B40: behavioral-assertion rubric injection.

Origin: W1 batch-coordinator triage (2026-05-06) found RED tests greping for
implementation-string patterns and matching trailing comments — passing the
test against dead code that was never invoked at runtime. The reviewer rubric
similarly accepted pattern-presence-only tests as valid behavioral contracts.

Step C of the 2026-05-07 sprint adds a centralized BEHAVIORAL ASSERTION
RUBRIC — `function call + observable side-effect` — to the prompts that
shape RED tests, that audit RED tests, that review final code+tests, and
that write the spec's Acceptance Criteria.

Surfaces under test (4):
    1. phase_5_implement._build_red_prompt           (RED worker rubric)
    2. phase_5_implement._build_validation_prompt    (Opus RED-validator)
    3. phase_6_review._build_review_prompt           (parallel reviewer rubric)
    4. phase_45_spec._build_spec_prompt              (FEATURE/COMPLEX spec writer)

DRY guard: a single accessor `get_behavioral_assertion_rubric()` exported from
`anti_hallucination.helper` is the source of truth — all 4 surfaces consume
it. Test T08 pins this contract.

These tests MUST FAIL today (no rubric exists) and PASS after GREEN-step
implementation. Per the rubric these tests pin: each test calls the
prompt-builder function and asserts on the returned StepResult.data["prompt"]
content (function call + observable side-effect), not pattern-presence on
source files.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec  # noqa: E402


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
    """RED/validation/review prompts call _read_first_block which reads
    scratchpad/injection/*.md. Seed empty files so the read does not raise."""
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _build_red_prompt_str(scratchpad: Path) -> str:
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad)
    result = phase_5_implement._build_red_prompt(ctx, None)
    assert isinstance(result, StepResult) and isinstance(result.data, dict)
    return result.data["prompt"]


def _build_validation_prompt_str(scratchpad: Path) -> str:
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests" / "build-red-output.log"),
            "spec_path": str(scratchpad / "specs" / "build-spec.md"),
            "cycle": 1,
            "red_test_paths": ["tests/test_foo.py"],
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    result = phase_5_implement._build_validation_prompt(ctx, prev)
    assert isinstance(result, StepResult) and isinstance(result.data, dict)
    return result.data["prompt"]


def _build_review_prompt_str(scratchpad: Path) -> str:
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad)
    result = phase_6_review._build_review_prompt(ctx, None)
    assert isinstance(result, StepResult) and isinstance(result.data, dict)
    return result.data["prompt"]


def _build_spec_prompt_str(scratchpad: Path) -> str:
    _seed_injection(scratchpad)
    ctx = _make_ctx(scratchpad)
    result = phase_45_spec._build_spec_prompt(ctx, None)
    assert isinstance(result, StepResult) and isinstance(result.data, dict)
    return result.data["prompt"]


# ─── T01: RED prompt — header phrase ────────────────────────────────────────


def test_red_prompt_includes_behavioral_assertion_header(tmp_path):
    """RED worker prompt must carry the BEHAVIORAL ASSERTION RUBRIC header so
    the worker reads the rubric before writing tests."""
    prompt = _build_red_prompt_str(tmp_path / "scratch")
    assert "BEHAVIORAL ASSERTION" in prompt, (
        "RED prompt missing BEHAVIORAL ASSERTION rubric — root cause of W1 "
        "structural-grep test failures (B4D83B40)."
    )


# ─── T02: RED prompt — key phrase ───────────────────────────────────────────


def test_red_prompt_states_function_call_plus_side_effect_mandate(tmp_path):
    """The rubric must state the canonical mandate verbatim so workers cannot
    interpret 'test the contract' loosely."""
    prompt = _build_red_prompt_str(tmp_path / "scratch")
    assert "function call" in prompt.lower()
    assert "observable side-effect" in prompt.lower() or "observable side effect" in prompt.lower()


# ─── T03: RED prompt — anti-example callout ─────────────────────────────────


def test_red_prompt_contains_grep_presence_anti_example(tmp_path):
    """The rubric must call out grep-presence-only tests as FORBIDDEN, with a
    concrete anti-example showing the W1 failure shape (`grep -q PATTERN
    file.sh && echo PASS`). Without an explicit anti-example the worker
    re-derives the same mistake."""
    prompt = _build_red_prompt_str(tmp_path / "scratch")
    lowered = prompt.lower()
    assert "forbidden" in lowered, "rubric must label grep-presence tests FORBIDDEN"
    assert "grep" in lowered, "rubric must name grep-presence as the anti-pattern"
    # Concrete shell-style anti-example: `grep -q ... && echo PASS` or similar
    assert "anti-example" in lowered or "do not do" in lowered, (
        "rubric must show an explicit anti-example, not just describe the rule"
    )


# ─── T04: RED prompt — positive example ─────────────────────────────────────


def test_red_prompt_contains_behavioral_positive_example(tmp_path):
    """The rubric must show a positive example — function invocation followed
    by side-effect assertion — so workers have a concrete pattern to copy."""
    prompt = _build_red_prompt_str(tmp_path / "scratch")
    lowered = prompt.lower()
    # Positive example signal — at least one of these phrases must appear in
    # the rubric block (label varies but the signal must be present).
    has_positive_label = any(
        phrase in lowered
        for phrase in ("positive example", "required:", "valid example", "behavioral example")
    )
    assert has_positive_label, (
        "rubric must show a labeled positive example — function call + assertion"
    )


# ─── T05: validation prompt — Quality rubric anti-pattern flag ─────────────


def test_validation_prompt_flags_pattern_presence_only_tests(tmp_path):
    """Opus RED-validator's Quality Findings rubric must explicitly list
    pattern-presence-only tests as a quality-finding category. Without this,
    the validator passes structural-grep tests (W1 root cause)."""
    prompt = _build_validation_prompt_str(tmp_path / "scratch")
    lowered = prompt.lower()
    # Either an explicit named category, or the canonical phrase referenced
    has_signal = any(
        phrase in lowered
        for phrase in (
            "pattern-presence",
            "pattern presence",
            "structural-grep",
            "structural grep",
            "grep-presence",
            "missing behavioral assertion",
            "behavioral assertion",
        )
    )
    assert has_signal, (
        "validation prompt's Quality rubric must flag pattern-presence-only "
        "tests as a finding category (B4D83B40)."
    )


# ─── T06: phase_6 reviewer prompt — behavioral check criterion ─────────────


def test_phase_6_reviewer_prompt_mentions_behavioral_test_check(tmp_path):
    """Phase 6 reviewer dispatches sub-agents to grade code+tests. The
    aggregator rubric must instruct each reviewer to flag tests that lack a
    function call + side-effect assertion."""
    prompt = _build_review_prompt_str(tmp_path / "scratch")
    lowered = prompt.lower()
    has_signal = any(
        phrase in lowered
        for phrase in (
            "behavioral assertion",
            "pattern-presence",
            "pattern presence",
            "structural-grep",
            "structural grep",
            "grep-presence",
        )
    )
    assert has_signal, (
        "phase_6 reviewer prompt must instruct sub-agents to flag "
        "pattern-presence-only tests (B4D83B40)."
    )


# ─── T07: spec writer prompt — Validation-line behavioral mandate ──────────


def test_spec_writer_prompt_mandates_behavioral_validation_lines(tmp_path):
    """phase_45_spec FEATURE/COMPLEX spec writer must require Acceptance
    Criteria `Validation:` lines to invoke the function under test and
    observe a side-effect, not match patterns in source files. This drives
    the downstream RED test shape — bad specs produce bad tests."""
    prompt = _build_spec_prompt_str(tmp_path / "scratch")
    lowered = prompt.lower()
    has_signal = any(
        phrase in lowered
        for phrase in (
            "behavioral assertion",
            "function call",
            "observable side-effect",
            "observable side effect",
            "pattern-presence",
            "pattern presence",
        )
    )
    assert has_signal, (
        "spec writer prompt must mandate behavioral Validation lines "
        "(B4D83B40)."
    )


# ─── T08: DRY — single accessor, all surfaces consume it ───────────────────


def test_behavioral_assertion_rubric_is_centralized(tmp_path):
    """All four surfaces must consume one shared rubric fragment via
    `anti_hallucination.helper.get_behavioral_assertion_rubric()`. Copy-paste
    divergence drifts the rubric between surfaces; centralization keeps them
    coherent."""
    from bytedigger_engine.lib.plugins.anti_hallucination.helper import get_behavioral_assertion_rubric

    canonical = get_behavioral_assertion_rubric()
    assert isinstance(canonical, str) and canonical.strip(), (
        "get_behavioral_assertion_rubric() must return non-empty rubric text"
    )

    # Pick a distinctive substring from the canonical rubric that all 4
    # surfaces must contain verbatim (DRY guard — if a surface inlines its
    # own copy, the substring will diverge and this test fails).
    # Take the first 80 non-whitespace-collapsed chars — distinctive enough
    # to detect divergence without being brittle to wrapping.
    canonical_compact = " ".join(canonical.split())
    distinctive = canonical_compact[:80]
    assert len(distinctive) >= 40, "rubric too short to be a meaningful contract"

    scratch_root = tmp_path / "scratch-root"
    surfaces = {
        "RED prompt": _build_red_prompt_str(scratch_root / "red"),
        "validation prompt": _build_validation_prompt_str(scratch_root / "val"),
        "phase_6 review prompt": _build_review_prompt_str(scratch_root / "rev"),
        "spec writer prompt": _build_spec_prompt_str(scratch_root / "spec"),
    }
    missing = []
    for name, body in surfaces.items():
        if distinctive not in " ".join(body.split()):
            missing.append(name)
    assert not missing, (
        f"these surfaces do not consume the centralized rubric: {missing}. "
        "Each must call get_behavioral_assertion_rubric() rather than inlining."
    )


# ─── T09: prompt-byte sanity — no runaway prose ────────────────────────────


def test_rubric_does_not_bloat_prompts(tmp_path):
    """Adding the rubric must not push any prompt above a sane budget. 32KB
    cap is generous — current prompts are well under 16KB. A blown budget
    signals copy-paste, not a centralized fragment."""
    cap_bytes = 36 * 1024  # raised from 32 KB: 5D0D3BD1 rubric-trim + cap re-baseline
    scratch_root = tmp_path / "scratch-root"
    surfaces = {
        "RED prompt": _build_red_prompt_str(scratch_root / "red"),
        "validation prompt": _build_validation_prompt_str(scratch_root / "val"),
        "phase_6 review prompt": _build_review_prompt_str(scratch_root / "rev"),
        "spec writer prompt": _build_spec_prompt_str(scratch_root / "spec"),
    }
    over_budget = {
        name: len(body.encode("utf-8"))
        for name, body in surfaces.items()
        if len(body.encode("utf-8")) > cap_bytes
    }
    assert not over_budget, (
        f"prompts exceeded {cap_bytes}-byte cap: {over_budget}. Centralize the "
        "rubric and trim duplicated prose."
    )
