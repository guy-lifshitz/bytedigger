"""RED tests for GH853 — ambiguous-basename deterministic auto-resolve.

Spec: SHARED/memory/Decisions/2026-07-15_GH853_ambiguous_basename_autoresolve_spec.md
Agreement: 3BBDD2D0-A200-4A66-A710-BD7BE2876412

Contract under test (citation_verifier.py verify()):
When a bare relative citation segment-walk-resolves to >=2 candidates, verify()
should content-disambiguate (call new helper `_verify_content` on each candidate)
instead of immediately failing with "ambiguous: N candidates".

Expected pre-GREEN outcome: 8 FAIL, 2 PASS (regression guards AC6, AC10).
AC1-AC5, AC7, AC8, AC9 FAIL today (auto-resolve/helper/cap not implemented yet).

Do NOT implement any contract here — RED-only file. §1q: `_verify_content` does
not exist yet at module top-level, so it is imported inside the AC9 test body.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.scripts.lib.citation_verifier import (  # noqa: E402
    Citation,
    VerifyResult,
    verify,
)


# ─── helpers (self-contained copy of test_citation_verifier_segment_walk.py) ──


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temp git repo with the given {relpath: content} files."""
    _init_repo(tmp_path)
    for relpath, content in files.items():
        _commit_file(tmp_path, relpath, body=content)
    return tmp_path


# ─── AC1 — auto-resolve: exactly one candidate contains the cited fn ──────────


def test_ac1_auto_resolves_when_exactly_one_candidate_verifies(tmp_path: pytest.TempPathFactory) -> None:
    """AC1: a/retrieval.py has target_fn, b/retrieval.py doesn't → auto-resolved verified=True."""
    repo = _make_repo(tmp_path / "repo", {
        "a/retrieval.py": "def target_fn():\n    pass\n",
        "b/retrieval.py": "def other_fn():\n    pass\n",
    })

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="retrieval.py",
        text_offset=0,
        text_snippet="source retrieval.py && target_fn",
    )
    result = verify(citation, repo)

    assert result.verified is True, (
        f"expected auto-resolve verified=True, got verified={result.verified}, "
        f"evidence={result.evidence!r}"
    )
    assert "auto-resolved" in result.evidence, f"evidence must contain 'auto-resolved': {result.evidence!r}"
    assert "a/retrieval.py" in result.evidence, f"evidence must cite a/retrieval.py: {result.evidence!r}"


# ─── AC2 — both candidates verify → still ambiguous, evidence has verified count ─


def test_ac2_ambiguous_when_both_candidates_verify(tmp_path: pytest.TempPathFactory) -> None:
    """AC2: both a/retrieval.py and b/retrieval.py contain target_fn → verified=False, '(2 verified)'."""
    repo = _make_repo(tmp_path / "repo", {
        "a/retrieval.py": "def target_fn():\n    pass\n",
        "b/retrieval.py": "def target_fn():\n    pass\n",
    })

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="retrieval.py",
        text_offset=0,
        text_snippet="source retrieval.py && target_fn",
    )
    result = verify(citation, repo)

    assert result.verified is False, f"expected verified=False, got {result.verified}"
    assert result.evidence.startswith("ambiguous:"), f"evidence must start with 'ambiguous:': {result.evidence!r}"
    assert "(2 verified)" in result.evidence, f"evidence must contain '(2 verified)': {result.evidence!r}"
    assert "a/retrieval.py" in result.evidence and "b/retrieval.py" in result.evidence, (
        f"evidence must list both relpaths: {result.evidence!r}"
    )


# ─── AC3 — neither candidate verifies → ambiguous, '(0 verified)' ─────────────


def test_ac3_ambiguous_when_zero_candidates_verify(tmp_path: pytest.TempPathFactory) -> None:
    """AC3: neither a/retrieval.py nor b/retrieval.py has target_fn → '(0 verified)'."""
    repo = _make_repo(tmp_path / "repo", {
        "a/retrieval.py": "def unrelated_fn():\n    pass\n",
        "b/retrieval.py": "def other_fn():\n    pass\n",
    })

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="retrieval.py",
        text_offset=0,
        text_snippet="source retrieval.py && target_fn",
    )
    result = verify(citation, repo)

    assert result.verified is False, f"expected verified=False, got {result.verified}"
    assert "(0 verified)" in result.evidence, f"evidence must contain '(0 verified)': {result.evidence!r}"


# ─── AC4 — snippet kind auto-resolves via content dispatch ────────────────────


def test_ac4_snippet_kind_auto_resolves(tmp_path: pytest.TempPathFactory) -> None:
    """AC4: only one of two candidates contains the cited snippet text → auto-resolves."""
    repo = _make_repo(tmp_path / "repo", {
        "a/config.py": "SOME_UNIQUE_SNIPPET_MARKER = 42\n",
        "b/config.py": "NOTHING_RELEVANT_HERE = 1\n",
    })

    citation = Citation(
        kind="snippet",
        identifier="SOME_UNIQUE_SNIPPET_MARKER = 42",
        file_path="config.py",
        text_offset=0,
        text_snippet='config.py:"SOME_UNIQUE_SNIPPET_MARKER = 42"',
    )
    result = verify(citation, repo)

    assert result.verified is True, (
        f"expected auto-resolve verified=True for snippet kind, got verified={result.verified}, "
        f"evidence={result.evidence!r}"
    )
    assert "auto-resolved" in result.evidence, f"evidence must contain 'auto-resolved': {result.evidence!r}"


# ─── AC5 — line kind auto-resolves to the candidate with enough lines ─────────


def test_ac5_line_kind_auto_resolves_to_candidate_with_enough_lines(tmp_path: pytest.TempPathFactory) -> None:
    """AC5: x.py:L50 — candidate A has 60 lines (valid), candidate B has 3 lines (out of range)."""
    lines_60 = "".join(f"line{i}\n" for i in range(1, 61))
    lines_3 = "line1\nline2\nline3\n"
    repo = _make_repo(tmp_path / "repo", {
        "a/x.py": lines_60,
        "b/x.py": lines_3,
    })

    citation = Citation(
        kind="line",
        identifier="L50",
        file_path="x.py",
        text_offset=0,
        text_snippet="Fix at x.py:L50",
    )
    result = verify(citation, repo)

    assert result.verified is True, (
        f"expected auto-resolve verified=True for line kind, got verified={result.verified}, "
        f"evidence={result.evidence!r}"
    )
    assert "auto-resolved" in result.evidence, f"evidence must contain 'auto-resolved': {result.evidence!r}"
    assert "a/x.py" in result.evidence, f"evidence must cite a/x.py (the resolvable candidate): {result.evidence!r}"


# ─── AC6 — regression: unique candidate still resolves as today ───────────────


def test_ac6_unique_candidate_regression_unchanged(tmp_path: pytest.TempPathFactory) -> None:
    """AC6: single (non-ambiguous) candidate resolves and verifies as today; no 'auto-resolved' text."""
    content = "def do_thing():\n    pass\n"
    repo = _make_repo(tmp_path / "repo", {"subdir/lib.py": content})

    citation = Citation(
        kind="function",
        identifier="do_thing",
        file_path="lib.py",
        text_offset=0,
        text_snippet="source lib.py && do_thing",
    )
    result = verify(citation, repo)

    assert result.verified is True, f"expected verified=True (unique candidate), got {result.verified}"
    assert "auto-resolved" not in result.evidence, (
        f"unique-candidate path must NOT say 'auto-resolved': {result.evidence!r}"
    )


# ─── AC7 — auto-resolved confidence is capped at 0.9 ──────────────────────────


def test_ac7_auto_resolved_confidence_capped_at_point_nine(tmp_path: pytest.TempPathFactory) -> None:
    """AC7: auto-resolved result confidence must be <= 0.9 (min(r.confidence, 0.9))."""
    repo = _make_repo(tmp_path / "repo", {
        "a/retrieval.py": "def target_fn():\n    pass\n",
        "b/retrieval.py": "def other_fn():\n    pass\n",
    })

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="retrieval.py",
        text_offset=0,
        text_snippet="source retrieval.py && target_fn",
    )
    result = verify(citation, repo)

    assert result.verified is True, f"precondition: expected auto-resolve verified=True, got {result.verified}"
    assert result.confidence <= 0.9, f"auto-resolved confidence must be <= 0.9, got {result.confidence}"


# ─── AC8 — too many candidates (>20) → keep ambiguous FAIL, no auto-resolve ───


def test_ac8_too_many_candidates_skips_auto_resolve(tmp_path: pytest.TempPathFactory) -> None:
    """AC8: 21 candidates (> _AMBIG_RESOLVE_MAX=20), each containing the fn → still fails, no resolve attempt."""
    files = {f"dir{i}/dup.py": "def target_fn():\n    pass\n" for i in range(21)}
    repo = _make_repo(tmp_path / "repo", files)

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="dup.py",
        text_offset=0,
        text_snippet="source dup.py && target_fn",
    )
    result = verify(citation, repo)

    assert result.verified is False, f"expected verified=False for >20 candidates, got {result.verified}"
    assert "too many to auto-resolve" in result.evidence, (
        f"evidence must contain 'too many to auto-resolve': {result.evidence!r}"
    )


# ─── AC9 — _verify_content helper exists and works directly ───────────────────


def test_ac9_verify_content_helper_exists_and_verifies_real_file(tmp_path: pytest.TempPathFactory) -> None:
    """AC9: named helper `_verify_content` importable, callable directly on a real file → verified=True."""
    from bytedigger_engine.scripts.lib.citation_verifier import _verify_content  # type: ignore  # noqa: PLC0415

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    target = repo / "mod.py"
    target.write_text("def target_fn():\n    pass\n")

    citation = Citation(
        kind="function",
        identifier="target_fn",
        file_path="mod.py",
        text_offset=0,
        text_snippet="source mod.py && target_fn",
    )
    result = _verify_content(citation, target)

    assert isinstance(result, VerifyResult), f"expected VerifyResult, got {type(result)}"
    assert result.verified is True, f"expected verified=True, got verified={result.verified}, evidence={result.evidence!r}"


# ─── AC10 — regression: absolute-path citation unchanged ──────────────────────


def test_ac10_absolute_path_citation_unchanged(tmp_path: pytest.TempPathFactory) -> None:
    """AC10: absolute path to an existing file verifies per content, bypassing segment-walk entirely."""
    repo = _make_repo(tmp_path / "repo", {"subdir/present.py": "def real_fn():\n    pass\n"})
    abs_path = str((repo / "subdir" / "present.py").resolve())

    citation = Citation(
        kind="function",
        identifier="real_fn",
        file_path=abs_path,
        text_offset=0,
        text_snippet=f"source {abs_path} && real_fn",
    )
    result = verify(citation, repo)

    assert result.verified is True, (
        f"expected verified=True for existing absolute-path citation, got verified={result.verified}, "
        f"evidence={result.evidence!r}"
    )
    assert "auto-resolved" not in result.evidence, (
        f"absolute-path path must not go through auto-resolve: {result.evidence!r}"
    )
