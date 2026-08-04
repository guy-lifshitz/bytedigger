"""RED tests for 8F4131E3 — citation_verifier _segment_walk_resolve symlink dedup.

Problem: when two git-tracked paths point to the same on-disk inode via symlinks,
_segment_walk_resolve returns (None, [path_a, path_b]) and verify() emits an
"ambiguous" finding.  After GREEN the helper deduplicates candidates by
`(hal_root / c).resolve()` and returns (resolved_path, []) for the symlink case.

Expected RED state (BEFORE production fix — intentional 5F/5P shape):
  FAIL: AC1, AC2, AC4, AC6, AC9  — new symlink-dedup behaviour not yet implemented
  PASS: AC3, AC5, AC7, AC8, AC10 — regression guards; pre-fix code returns
        (None, candidates) for any >=2-candidate case, which trivially satisfies
        AC3/AC5/AC7/AC8; AC10 also trivially passes because resolve() is never
        called pre-fix so no OSError can propagate.

Rationale for 5P trivially passing:
  AC3: truly distinct files → (None, ['a/foo.sh', 'b/foo.sh']) — already current behaviour.
  AC5: mixed 3-candidate case → (None, [c1, c2, c3]) — current code returns raw list.
  AC7: verify() truly-ambiguous → verified=False with 'ambiguous' evidence — current.
  AC8: broken symlink → no crash, (None, candidates) — resolve() not called today.
  AC10: OSError during resolve() → fall-through — resolve() not called today; trivial.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Skip entire module on Windows where symlinks require admin privileges.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need admin on Windows"
)

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.scripts.lib.citation_verifier import (  # noqa: E402
    Citation,
    verify,
)


# ─── helpers (copied from test_citation_verifier_segment_walk.py) ─────────────


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


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_symlink_dedup_file_plus_symlink_to_same_target(tmp_path: Path) -> None:
    """AC1: Two paths resolving to same realpath via symlink (file + symlink → file) → (resolved_path, []).

    WILL FAIL pre-fix: current code returns (None, ['a/foo.sh', 'link/foo.sh']).
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    # Create real file and commit it.
    _make_repo(repo, {"a/foo.sh": "#!/bin/bash\necho real\n"})

    # Create symlink directory tracked by git, pointing to real file.
    link_dir = repo / "link"
    link_dir.mkdir(parents=True, exist_ok=True)
    link_file = link_dir / "foo.sh"
    link_file.symlink_to(repo / "a" / "foo.sh")

    # Stage and commit the symlink so git ls-files sees it.
    subprocess.run(["git", "add", "link/foo.sh"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add symlink"], cwd=repo, check=True)

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    assert candidates == [], (
        f"AC1: expected empty candidates (symlink dedup), got {candidates}"
    )
    assert resolved is not None, "AC1: expected a resolved Path, got None"


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_symlink_dedup_two_symlinks_to_one_target(tmp_path: Path) -> None:
    """AC2: Two paths resolving to same realpath via two-symlinks-to-one-target → (resolved_path, []).

    WILL FAIL pre-fix: current code returns (None, [candidate1, candidate2]).
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Create a real file (not named foo.sh — so only the two symlinks match).
    real_dir = repo / "real"
    real_dir.mkdir(parents=True)
    real_file = real_dir / "target.sh"
    real_file.write_text("#!/bin/bash\necho target\n")

    # Two symlink dirs, each linking foo.sh → same target.
    for d in ("link1", "link2"):
        link_dir = repo / d
        link_dir.mkdir(parents=True)
        (link_dir / "foo.sh").symlink_to(real_file)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add two symlinks"], cwd=repo, check=True)

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    assert candidates == [], (
        f"AC2: expected empty candidates (two symlinks to one target), got {candidates}"
    )
    assert resolved is not None, "AC2: expected a resolved Path, got None"


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_truly_distinct_files_still_returns_none_candidates(tmp_path: Path) -> None:
    """AC3: Two truly distinct files at a/foo.sh + b/foo.sh → still (None, ['a/foo.sh', 'b/foo.sh']).

    WILL PASS pre-fix (regression guard): current code returns raw candidates.
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = _make_repo(tmp_path / "repo", {
        "a/foo.sh": "#!/bin/bash\necho a\n",
        "b/foo.sh": "#!/bin/bash\necho b\n",
    })

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    assert resolved is None, f"AC3: expected None for truly distinct files, got {resolved}"
    assert "a/foo.sh" in candidates, f"AC3: missing 'a/foo.sh' in {candidates}"
    assert "b/foo.sh" in candidates, f"AC3: missing 'b/foo.sh' in {candidates}"
    assert len(candidates) == 2, f"AC3: expected exactly 2 candidates, got {candidates}"


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_three_candidates_all_same_realpath(tmp_path: Path) -> None:
    """AC4: Three candidates, all resolving to same realpath → (resolved_path, []).

    WILL FAIL pre-fix: current code returns (None, [c1, c2, c3]).
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    real_dir = repo / "real"
    real_dir.mkdir(parents=True)
    real_file = real_dir / "target.sh"
    real_file.write_text("#!/bin/bash\necho target\n")

    # Three symlink dirs all pointing to the same target.
    for d in ("link1", "link2", "link3"):
        link_dir = repo / d
        link_dir.mkdir(parents=True)
        (link_dir / "foo.sh").symlink_to(real_file)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "three symlinks"], cwd=repo, check=True)

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    assert candidates == [], (
        f"AC4: expected empty candidates (3 symlinks same target), got {candidates}"
    )
    assert resolved is not None, "AC4: expected a resolved Path, got None"


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_three_candidates_two_same_one_distinct_returns_none_raw(tmp_path: Path) -> None:
    """AC5: Three candidates, two resolving to same + one distinct → (None, [c1, c2, c3]).

    Raw paths preserved, NOT collapsed to 2.
    WILL PASS pre-fix (regression guard): current code returns (None, all_candidates).
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Distinct real files in a/ and b/.
    for d in ("a", "b"):
        dir_path = repo / d
        dir_path.mkdir(parents=True)
        (dir_path / "foo.sh").write_text(f"#!/bin/bash\necho {d}\n")

    # A symlink in link/ → a/foo.sh (same realpath as a/foo.sh).
    link_dir = repo / "link"
    link_dir.mkdir(parents=True)
    (link_dir / "foo.sh").symlink_to(repo / "a" / "foo.sh")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "mixed candidates"], cwd=repo, check=True)

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    # There are 3 candidates (a/foo.sh, b/foo.sh, link/foo.sh).
    # Two of them (a/foo.sh, link/foo.sh) share a realpath, but b/foo.sh is distinct.
    # So: still ambiguous → (None, [all three raw paths]).
    assert resolved is None, (
        f"AC5: expected None when mixed distinct+symlink candidates, got {resolved}"
    )
    assert len(candidates) == 3, (
        f"AC5: expected 3 raw candidates (not collapsed to 2), got {candidates}"
    )
    assert "b/foo.sh" in candidates, f"AC5: missing 'b/foo.sh' in {candidates}"


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_verify_symlink_ambiguous_citation_returns_verified_true(tmp_path: Path) -> None:
    """AC6: verify() with symlink-ambiguous citation returns verified=True.

    Was verified=False pre-fix (emitted 'ambiguous: 2 candidates' evidence).
    WILL FAIL pre-fix.
    """
    repo = tmp_path / "repo"
    _make_repo(repo, {"a/batch.sh": "#!/bin/bash\nline1\nline2\n"})

    # Add symlink also tracked by git.
    link_dir = repo / "link"
    link_dir.mkdir(parents=True)
    (link_dir / "batch.sh").symlink_to(repo / "a" / "batch.sh")
    subprocess.run(["git", "add", "link/batch.sh"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "symlink"], cwd=repo, check=True)

    citation = Citation(
        kind="line",
        identifier="L1",
        file_path="batch.sh",
        text_offset=0,
        text_snippet="batch.sh:L1",
    )
    result = verify(citation, repo)

    assert result.verified is True, (
        f"AC6: expected verified=True for symlink-ambiguous citation, "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC7 ──────────────────────────────────────────────────────────────────────


def test_verify_truly_ambiguous_citation_returns_verified_false(tmp_path: Path) -> None:
    """AC7: verify() with truly-ambiguous citation still returns verified=False with 'ambiguous' evidence.

    WILL PASS pre-fix (regression guard).
    """
    repo = _make_repo(tmp_path / "repo", {
        "a/foo.sh": "#!/bin/bash\necho a\n",
        "b/foo.sh": "#!/bin/bash\necho b\n",
    })

    citation = Citation(
        kind="line",
        identifier="L1",
        file_path="foo.sh",
        text_offset=0,
        text_snippet="foo.sh:L1",
    )
    result = verify(citation, repo)

    assert result.verified is False, (
        f"AC7: expected verified=False for truly ambiguous, got {result.verified}"
    )
    assert "ambiguous" in result.evidence.lower(), (
        f"AC7: expected 'ambiguous' in evidence, got {result.evidence!r}"
    )


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_broken_symlink_does_not_crash_returns_none_candidates(tmp_path: Path) -> None:
    """AC8: Broken symlink in candidates (target deleted) → still (None, candidates), no crash.

    WILL PASS pre-fix (regression guard): resolve() not called today so broken
    symlinks are just candidates returned as-is.
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Create a real file in a/.
    (repo / "a").mkdir(parents=True)
    (repo / "a" / "foo.sh").write_text("#!/bin/bash\necho a\n")

    # Create a symlink in link/ pointing to a file that will be deleted.
    (repo / "link").mkdir(parents=True)
    ephemeral_target = repo / "ephemeral_target.sh"
    ephemeral_target.write_text("#!/bin/bash\necho ephemeral\n")
    (repo / "link" / "foo.sh").symlink_to(ephemeral_target)

    # Commit all (including the symlink while target still exists).
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add symlink and files"], cwd=repo, check=True)

    # Now delete the symlink target to make it broken.
    ephemeral_target.unlink()

    # Must not raise, must return (None, candidates).
    try:
        result = _segment_walk_resolve("foo.sh", repo)
    except Exception as exc:
        pytest.fail(f"AC8: _segment_walk_resolve raised on broken symlink: {exc!r}")

    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    assert resolved is None, (
        f"AC8: expected None when broken symlink present, got {resolved}"
    )
    assert len(candidates) >= 1, f"AC8: expected at least 1 candidate, got {candidates}"


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_resolved_path_is_realpath_not_symlink(tmp_path: Path) -> None:
    """AC9: Returned resolved_path for symlink dedup is the realpath (.is_symlink() is False).

    WILL FAIL pre-fix: code doesn't call resolve(), so candidate is the symlink path.
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _make_repo(repo, {"a/foo.sh": "#!/bin/bash\necho real\n"})

    # Add symlink.
    (repo / "link").mkdir(parents=True)
    (repo / "link" / "foo.sh").symlink_to(repo / "a" / "foo.sh")
    subprocess.run(["git", "add", "link/foo.sh"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "symlink"], cwd=repo, check=True)

    result = _segment_walk_resolve("foo.sh", repo)
    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    # If candidates is non-empty pre-fix, resolved will be None → skip symlink check.
    # But the AC requires resolved is not None AND not a symlink.
    assert resolved is not None, (
        "AC9: expected a resolved path (not None); symlink dedup must fire"
    )
    assert not resolved.is_symlink(), (
        f"AC9: resolved_path must be realpath (not a symlink), got {resolved}"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────────


def test_resolve_oserror_falls_through_to_none_candidates(tmp_path: Path) -> None:
    """AC10: resolve() raising OSError during dedup → fall through to (None, candidates), no propagation.

    WILL PASS pre-fix (trivial): resolve() is never called pre-fix, so no OSError
    can propagate. After GREEN the except clause must catch OSError/RuntimeError.

    We test this behaviourally by verifying that even with a broken-symlink setup
    (which causes resolve() to raise on some platforms), the helper does not raise
    and returns (None, candidates) when dedup cannot reduce candidates to 1 unique path.

    Note: on platforms where resolve() does NOT raise on broken symlinks (resolves to
    the dangling path instead), this test guards the no-crash contract — (None, candidates)
    is still the correct return when all realpaths are not a single unique value.
    """
    from bytedigger_engine.scripts.lib.citation_verifier import _segment_walk_resolve  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Two broken symlinks pointing to distinct non-existent targets.
    for d, target_name in [("link1", "ghost1.sh"), ("link2", "ghost2.sh")]:
        link_dir = repo / d
        link_dir.mkdir(parents=True)
        # Symlink points to a non-existent file (broken).
        (link_dir / "foo.sh").symlink_to(repo / target_name)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "two broken symlinks"], cwd=repo, check=True)

    try:
        result = _segment_walk_resolve("foo.sh", repo)
    except (OSError, RuntimeError) as exc:
        pytest.fail(
            f"AC10: _segment_walk_resolve must not propagate OSError/RuntimeError, got {exc!r}"
        )

    assert isinstance(result, tuple) and len(result) == 2
    resolved, candidates = result

    # With two broken symlinks that don't resolve to the same target, or if resolve()
    # raises (OSError), the helper must fall through to (None, candidates).
    assert resolved is None, (
        f"AC10: expected None when resolve() errors or distinct broken symlinks, got {resolved}"
    )
    assert len(candidates) == 2, (
        f"AC10: expected 2 candidates (both broken-symlink paths), got {candidates}"
    )
