"""Agreement E52F241F (+ duplicate 62DC65FA) — phase_6_review prompt must
reference reviews/* paths via ABSOLUTE scratchpad paths, not relative
``reviews/build-review.md`` / ``reviews/role-<slug>.md``.

Symptom: sub-agent dispatched into a worktree where CWD = worktree root
(NOT scratchpad). Relative ``reviews/role-X.md`` write lands at
``<worktree_root>/reviews/role-X.md`` — stray files outside scratchpad,
also missed by the Python aggregator (which globs ``<scratchpad>/reviews/``).

Fix: inject absolute scratchpad paths into the prompt.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_PY = Path(__file__).resolve().parent.parent
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import WorkflowContext  # type: ignore
from bytedigger_engine.workflows.phase_6_review import _build_review_prompt  # type: ignore


def _ctx(tmp_path: Path, complexity: str = "SIMPLE") -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratch),
            "complexity": complexity,
        },
        question="some feature",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def test_review_prompt_uses_absolute_scratchpad_path_for_build_review_md(tmp_path):
    """The prompt must mention the absolute path
    ``<scratchpad>/reviews/build-review.md`` — sub-agent writes to that
    explicit path, not a CWD-relative ``reviews/...``.
    """
    ctx = _ctx(tmp_path)
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok"
    prompt: str = result.data["prompt"]
    abs_review = str((tmp_path / "scratch" / "reviews" / "build-review.md").resolve())
    assert abs_review in prompt, (
        f"prompt must reference absolute review path; got prompt without {abs_review!r}"
    )


def test_review_prompt_uses_absolute_scratchpad_path_for_role_files(tmp_path):
    """The per-role write target must be the absolute scratchpad path,
    not relative ``reviews/role-<slug>.md``.
    """
    ctx = _ctx(tmp_path)
    result = _build_review_prompt(ctx, None)
    prompt: str = result.data["prompt"]
    abs_reviews_dir = str((tmp_path / "scratch" / "reviews").resolve())
    assert abs_reviews_dir in prompt, (
        f"prompt must reference absolute reviews dir; got prompt without {abs_reviews_dir!r}"
    )
    # And the per-role schema line must say role-<slug>.md under THAT dir.
    expected_role_glob = f"{abs_reviews_dir}/role-"
    assert expected_role_glob in prompt, (
        "prompt must show role-<slug>.md under absolute scratchpad reviews/"
    )


def test_review_prompt_does_not_emit_bare_reviews_relpath(tmp_path):
    """Regression guard: the prompt should NOT instruct sub-agents to write
    to bare ``reviews/build-review.md`` — that's the relative-path bug.

    We assert the bare relative phrase ``reviews/build-review.md`` does not
    appear in any line that does NOT also contain the absolute scratchpad
    path. (Constants like REVIEW_DOC_RELPATH used for python-side path
    construction are fine; the prompt body must not have the bare form.)
    """
    ctx = _ctx(tmp_path)
    prompt: str = _build_review_prompt(ctx, None).data["prompt"]
    abs_scratch = str((tmp_path / "scratch").resolve())
    bare_lines = [
        ln for ln in prompt.splitlines()
        if "reviews/build-review.md" in ln and abs_scratch not in ln
    ]
    assert bare_lines == [], (
        "prompt should not have bare relative reviews/build-review.md "
        f"refs; offending lines: {bare_lines}"
    )
