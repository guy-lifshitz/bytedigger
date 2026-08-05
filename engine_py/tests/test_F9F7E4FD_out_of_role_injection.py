"""RED tests for F9F7E4FD: OUT-OF-ROLE block injection into all 12 prompt builders.

Each test:
  1. Calls the real prompt-builder function.
  2. Asserts result.status == "ok".
  3. Asserts "OUT OF ROLE" in result.data["prompt"].
  4. Asserts "bun run-phase.ts" in result.data["prompt"].

All 12 tests MUST FAIL before implementation (no block exists yet).
After GREEN they MUST PASS.
"""
from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec  # noqa: E402
from bytedigger_engine.workflows import phase_7_synthesize  # noqa: E402
from bytedigger_engine.workflows import phase_4_architect  # noqa: E402
from bytedigger_engine.workflows import phase_2_explore  # noqa: E402
from bytedigger_engine.workflows import phase_3_clarify  # noqa: E402
from bytedigger_engine.workflows import phase_5_integrity  # noqa: E402


# ─── shared fixtures ──────────────────────────────────────────────────────────


def _make_ctx(
    scratchpad: Path,
    *,
    question: str = "Add foo to bar",
    org_extra: dict | None = None,
) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    org_config = {"scratchpad_dir": str(scratchpad)}
    org_config.update(org_extra or {})
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question=question,
        session_id="test-F9F7E4FD",
        persona="hal",
        framework=None,
        domain=None,
    )



def _hermetic_diff_repo(root: Path) -> Path:
    """A throwaway git repo whose `git diff HEAD~1 -- *test*` is guaranteed
    non-empty.

    Why this exists. `_build_integrity_prompt` resolves its diff through
    `resolve_git_cwd(cfg)`, which without `cfg["git_cwd"]` falls back to the
    process CWD - i.e. whatever checkout pytest happens to run in. The diff is
    then `git diff HEAD~1 -- "*test*" "*spec*" "*.test.*"`, so the assertion
    below was live or dead depending on whether that checkout's top commit
    touched a test-named path.

    Measured (bd#76 gate): a commit touching only `docs/` - English or Russian,
    it makes no difference - empties the diff, and the test reports SUCCESS
    while the OUT OF ROLE check never runs. Reproduced on the untouched base
    with a pure-English docs commit, so the coupling is to the ambient repo's
    HEAD, not to any document's language.

    Owning the repo removes the dependency outright: no commit ordering in any
    checkout can silence this assertion again.
    """
    repo = root / "diffrepo"
    repo.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    git("init", "-q", "-b", "main")
    (repo / "sample_test_file.py").write_text("def test_one():\n    assert True\n")
    git("add", "-A")
    git("commit", "-q", "-m", "baseline")
    # the second commit MUST touch a path matching DEFAULT_DIFF_PATTERNS,
    # otherwise this helper would reproduce the very defect it removes
    (repo / "sample_test_file.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"
    )
    git("add", "-A")
    git("commit", "-q", "-m", "change under test")
    return repo

def _seed_injection(scratchpad: Path) -> None:
    """Seed injection/*.md stubs so _read_first_block doesn't raise."""
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _prev_with_paths(scratchpad: Path, **extra) -> StepResult:
    """Minimal prev StepResult with path keys used by several builders."""
    spec_path = scratchpad / "specs" / "build-spec.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# Spec\n")
    data = {
        "spec_path": str(spec_path),
        "red_log_path": str(scratchpad / "tests" / "build-red-output.log"),
        "red_test_paths": ["tests/test_foo.py"],
        "cycle": 1,
        **extra,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _assert_out_of_role(result: StepResult, builder_name: str) -> None:
    assert result.status == "ok", f"{builder_name}: prompt build failed: {result}"
    prompt: str = result.data["prompt"]
    assert "OUT OF ROLE" in prompt, f"{builder_name}: missing OUT OF ROLE block"
    assert "bun run-phase.ts" in prompt, f"{builder_name}: missing run-phase.ts deny"


# ─── T01: phase_5_implement._build_red_prompt ─────────────────────────────────


def test_red_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_5_implement._build_red_prompt(ctx, None)
    _assert_out_of_role(result, "_build_red_prompt")


# ─── T02: phase_5_implement._build_green_prompt ───────────────────────────────


def test_green_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    val_doc = scratch / "validation" / "validation.md"
    val_doc.parent.mkdir(parents=True, exist_ok=True)
    val_doc.write_text("VERDICT: PASS\n")
    prev = _prev_with_paths(
        scratch,
        validation_doc_path=str(val_doc),
    )
    result = phase_5_implement._build_green_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_green_prompt")


# ─── T03: phase_5_implement._build_validation_prompt ─────────────────────────


def test_validation_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    prev = _prev_with_paths(scratch)
    result = phase_5_implement._build_validation_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_validation_prompt")


# ─── T04: phase_6_review._build_review_prompt ────────────────────────────────


def test_review_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_6_review._build_review_prompt(ctx, None)
    _assert_out_of_role(result, "_build_review_prompt")


# ─── T05: phase_6_review._build_fix_prompt ───────────────────────────────────


def test_fix_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    review_doc = scratch / "reviews" / "review.md"
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\nVERDICT: PARTIAL\n")
    prev = _prev_with_paths(
        scratch,
        review_doc_path=str(review_doc),
        verdict="PARTIAL",
    )
    result = phase_6_review._build_fix_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_fix_prompt")


# ─── T06: phase_6_review._build_satisfaction_prompt ──────────────────────────


def test_satisfaction_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    review_doc = scratch / "reviews" / "review.md"
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\nVERDICT: PASS\n")
    fix_doc = scratch / "fixes" / "fix.md"
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("# Fix\n")
    prev = _prev_with_paths(
        scratch,
        review_doc_path=str(review_doc),
        fix_doc_path=str(fix_doc),
        verdict="PASS",
    )
    result = phase_6_review._build_satisfaction_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_satisfaction_prompt")


# ─── T07: phase_45_spec._build_spec_prompt ───────────────────────────────────


def test_spec_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_45_spec._build_spec_prompt(ctx, None)
    _assert_out_of_role(result, "_build_spec_prompt")


# ─── T08: phase_7_synthesize._build_synthesizer_prompt ───────────────────────


def test_synthesizer_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_7_synthesize._build_synthesizer_prompt(ctx, None)
    _assert_out_of_role(result, "_build_synthesizer_prompt")


# ─── T09: phase_4_architect._build_architect_prompt ──────────────────────────


def test_architect_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_4_architect._build_architect_prompt(ctx, None)
    _assert_out_of_role(result, "_build_architect_prompt")


# ─── T10: phase_2_explore._build_explore_prompt ──────────────────────────────


def test_explore_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_2_explore._build_explore_prompt(ctx, None)
    _assert_out_of_role(result, "_build_explore_prompt")


# ─── T11: phase_3_clarify._build_clarify_prompt ──────────────────────────────


def test_clarify_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_3_clarify._build_clarify_prompt(ctx, None)
    _assert_out_of_role(result, "_build_clarify_prompt")


# ─── T12: phase_5_integrity._build_integrity_prompt ──────────────────────────


def test_integrity_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    # The diff is resolved inside a repo this test owns, so the assertion below
    # cannot be silenced by what some ambient checkout's last commit touched.
    ctx = _make_ctx(scratch, org_extra={"git_cwd": str(_hermetic_diff_repo(tmp_path))})
    result = phase_5_integrity._build_integrity_prompt(ctx, None)
    # No skip branches. Both former ones were environment-dependent, and the
    # empty-diff one fired on a green run - an assertion that reports success
    # while never executing is worse than a red one.
    assert result.status != "error", f"_build_integrity_prompt errored: {result.error}"
    assert result.data.get("prompt") is not None, (
        "no prompt was assembled, so the OUT OF ROLE block was never checked; "
        f"verdict_override={result.data.get('verdict_override')!r}, "
        f"diff_command={result.data.get('diff_command')!r}"
    )
    _assert_out_of_role(result, "_build_integrity_prompt")
