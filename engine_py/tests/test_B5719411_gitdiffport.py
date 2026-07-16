"""RED tests for B5719411 — git_diff injectable GitDiffPort seam (OSS seam #6).

Spec: SHARED/memory/Decisions/2026-06-19_B5719411_gitdiffport_seam_spec.md

Current production code (lib/plugins/disk_truth/git_diff.py) is a FLAT module:
3 public functions (git_diff_files, git_status_porcelain, resolve_pre_phase_sha)
plus 2 private helpers (_run_git, _apply_segment_filter).
It has NO Protocol class, NO injectable factory, NO registry, NO resolver.

Pre-GREEN PASS/FAIL classification (§3):
  AC1  → FAIL  (GitDiffPort class absent; _GitDiffSubprocess absent)
  AC2  → PASS  (git_diff_files already works — regression guard)
  AC3  → PASS  (git_status_porcelain already works — regression guard)
  AC4  → PASS  (resolve_pre_phase_sha already works — regression guard; non-repo
                RuntimeError already raised)
  AC5  → FAIL  (set_default_git_diff_factory absent; get_git_diff absent)
  AC6  → FAIL  (reset_default_git_diff_factory absent; get_git_diff absent)
  AC7  → FAIL  (get_git_diff absent)
  AC8  → FAIL  (set_default_git_diff_factory absent — consumer-module patch still
                works today but the registry-untouched assertion fails because
                get_git_diff does not exist)
  AC9  → FAIL  (_run_git / _apply_segment_filter helpers may already be callable;
                this AC asserts both via callable() — PASSES if helpers present,
                so actually PASS on existing code; kept as regression guard)
  AC10 → FAIL  (GitDiffPort / get_git_diff / set_default_git_diff_factory /
                reset_default_git_diff_factory not re-exported from __init__.py)

D1CF5FDF: not-yet-existing symbols accessed via getattr(git_diff, "name") INSIDE
test bodies, NEVER at module top level.  Module-level
`import lib.plugins.disk_truth.git_diff as git_diff` is safe because the module
already exists.

§1i (workflows.md §1i — Singleton-resource tests pre-stage state, never race):
  AC5/AC6/AC7/AC8 pre-stage global factory state via set_default_git_diff_factory
  and restore via reset_default_git_diff_factory() in finally blocks.

§1q: no spec_from_file_location / exec_module — normal import used.
§1l: no patch/mock of UUT symbols; AC5/AC8 use plain fake-port objects.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import lib.plugins.disk_truth.git_diff as git_diff  # module already exists; safe at top level


# ─────────────────────────────────────────────────────────────────────────────
# Shared real-repo fixture  (§1j macOS-safe realpath, §1u real subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _init_repo(repo: Path) -> None:
    """Initialise a minimal real git repo with one HEAD commit."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True,
                   capture_output=True)
    (repo / "init.txt").write_text("hello\n")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Real macOS-safe git repo with one commit (§1j realpathSync equivalent)."""
    repo = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_path)))
    _init_repo(repo)
    return repo


# ─────────────────────────────────────────────────────────────────────────────
# Fake port for AC5 and AC8 injection tests (§1l — no mock of UUT)
# ─────────────────────────────────────────────────────────────────────────────

_SENTINEL_DIFF    = ["__FAKE_DIFF_SENTINEL__"]
_SENTINEL_STATUS  = ["__FAKE_STATUS_SENTINEL__"]
_SENTINEL_SHA     = "__FAKE_SHA_SENTINEL__"


class _FakeGitDiffPort:
    """Plain fake satisfying GitDiffPort's 3-method contract (no mock/patch involved)."""

    def diff_files(
        self,
        pre_sha: str,
        cwd: "str | Path",
        untracked: bool = True,
        segment_filter: "str | None" = None,
    ) -> list:
        return _SENTINEL_DIFF

    def status_porcelain(
        self,
        cwd: "str | Path",
        segment_filter: "str | None" = None,
    ) -> list:
        return _SENTINEL_STATUS

    def resolve_pre_phase_sha(self, cwd: "str | Path") -> str:
        return _SENTINEL_SHA


# ─────────────────────────────────────────────────────────────────────────────
# Helper: safely call reset_default_git_diff_factory if it exists
# ─────────────────────────────────────────────────────────────────────────────

def _safe_reset() -> None:
    """Call reset_default_git_diff_factory() only if it exists (GREEN guard)."""
    reset_fn = getattr(git_diff, "reset_default_git_diff_factory", None)
    if callable(reset_fn):
        reset_fn()


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — GitDiffPort is @runtime_checkable; isinstance(_GitDiffSubprocess(), GitDiffPort) is True
# ═════════════════════════════════════════════════════════════════════════════

def test_ac1_gitdiffport_protocol_runtime_checkable_and_subprocess_isinstance() -> None:
    """AC1: GitDiffPort is @runtime_checkable; isinstance(_GitDiffSubprocess(), GitDiffPort) is True."""
    # Access new symbols via getattr — they don't exist yet (D1CF5FDF guard)
    GitDiffPort = getattr(git_diff, "GitDiffPort", None)
    assert GitDiffPort is not None, (
        "GitDiffPort not found in lib.plugins.disk_truth.git_diff — "
        "symbol does not exist yet (expected RED)"
    )

    # @runtime_checkable sets _is_runtime_protocol on the Protocol class
    assert getattr(GitDiffPort, "_is_runtime_protocol", False) is True, (
        "GitDiffPort is not @runtime_checkable"
    )

    _GitDiffSubprocess = getattr(git_diff, "_GitDiffSubprocess", None)
    assert _GitDiffSubprocess is not None, (
        "_GitDiffSubprocess not found in lib.plugins.disk_truth.git_diff — "
        "symbol does not exist yet (expected RED)"
    )

    instance = _GitDiffSubprocess()
    assert isinstance(instance, GitDiffPort), (
        f"isinstance(_GitDiffSubprocess(), GitDiffPort) is False — "
        f"_GitDiffSubprocess does not satisfy the Protocol"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — git_diff_files() default-factory result byte-identical (real git repo)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac2_git_diff_files_default_factory_byte_identical(git_repo: Path) -> None:
    """AC2: git_diff_files() with default factory returns byte-identical result (tracked+untracked dedup-sorted).

    This is a regression guard: these functions already exist and must continue to
    work correctly after the seam is introduced.
    """
    # Make a tracked change
    (git_repo / "init.txt").write_text("hello\nworld\n")
    # Add an untracked file
    (git_repo / "untracked.txt").write_text("new\n")
    # Stage a new file
    (git_repo / "staged.txt").write_text("staged\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=git_repo, check=True, capture_output=True)

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = git_diff.git_diff_files(head_sha, git_repo)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert result == sorted(set(result)), "Result is not dedup-sorted"
    # init.txt tracked change + staged.txt + untracked.txt
    assert "init.txt" in result, f"Expected 'init.txt' in {result}"
    assert "staged.txt" in result, f"Expected 'staged.txt' in {result}"
    assert "untracked.txt" in result, f"Expected 'untracked.txt' in {result}"


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — git_status_porcelain() default-factory result unchanged (incl. rename case)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac3_git_status_porcelain_default_factory_rename_case(git_repo: Path) -> None:
    """AC3: git_status_porcelain() default-factory result unchanged including rename: old -> new → new path, 2-char prefix stripped.

    Regression guard.
    """
    # Rename a tracked file
    subprocess.run(
        ["git", "mv", "init.txt", "renamed.txt"],
        cwd=git_repo, check=True, capture_output=True,
    )

    result = git_diff.git_status_porcelain(git_repo)

    assert isinstance(result, list), f"Expected list, got {type(result)}"
    # Rename shows up as new path only; 2-char prefix stripped
    assert "renamed.txt" in result, (
        f"Expected 'renamed.txt' (post-rename path) in status output: {result}"
    )
    # The old name 'init.txt' should NOT appear (take new path only)
    assert "init.txt" not in result, (
        f"Old rename path 'init.txt' should not appear in result: {result}"
    )
    # No item should start with a 2-char status prefix
    for item in result:
        assert not item[:2].strip() == "" or len(item) > 3, (
            f"Item appears to still have status prefix: {item!r}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — resolve_pre_phase_sha() returns 40-hex HEAD; raises RuntimeError on non-repo
# ═════════════════════════════════════════════════════════════════════════════

def test_ac4_resolve_pre_phase_sha_default_returns_40hex_and_raises_on_non_repo(
    git_repo: Path, tmp_path: Path
) -> None:
    """AC4: resolve_pre_phase_sha() returns 40-hex HEAD sha; raises RuntimeError on non-repo.

    Regression guard.
    """
    sha = git_diff.resolve_pre_phase_sha(git_repo)
    assert isinstance(sha, str), f"Expected str, got {type(sha)}"
    assert len(sha) == 40, f"Expected 40-char SHA, got {len(sha)!r}: {sha!r}"
    assert all(c in "0123456789abcdef" for c in sha), f"SHA is not hex: {sha!r}"

    # Non-repo tmpdir — must raise RuntimeError
    non_repo = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_path))) / "not_a_repo"
    non_repo.mkdir()
    with pytest.raises(RuntimeError):
        git_diff.resolve_pre_phase_sha(non_repo)


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — set_default_git_diff_factory(fake) → all 3 delegators route to fake port
# ═════════════════════════════════════════════════════════════════════════════

def test_ac5_set_factory_routes_all_three_delegators_to_fake_port(tmp_path: Path) -> None:
    """AC5: set_default_git_diff_factory(lambda: fake) → git_diff_files/git_status_porcelain/resolve_pre_phase_sha ALL route to injected port.

    §1i: global factory state restored in finally block.
    """
    # Access new symbols via getattr — they don't exist yet (D1CF5FDF guard)
    set_factory = getattr(git_diff, "set_default_git_diff_factory", None)
    assert set_factory is not None, (
        "set_default_git_diff_factory not found in lib.plugins.disk_truth.git_diff — "
        "symbol does not exist yet (expected RED)"
    )

    get_git_diff_fn = getattr(git_diff, "get_git_diff", None)
    assert get_git_diff_fn is not None, (
        "get_git_diff not found in lib.plugins.disk_truth.git_diff — "
        "symbol does not exist yet (expected RED)"
    )

    fake = _FakeGitDiffPort()
    try:
        set_factory(lambda: fake)

        # git_diff_files routes to fake
        diff_result = git_diff.git_diff_files("HEAD~1", str(tmp_path))
        assert diff_result == _SENTINEL_DIFF, (
            f"git_diff_files: expected sentinel {_SENTINEL_DIFF!r}, got {diff_result!r}"
        )

        # git_status_porcelain routes to fake
        status_result = git_diff.git_status_porcelain(str(tmp_path))
        assert status_result == _SENTINEL_STATUS, (
            f"git_status_porcelain: expected sentinel {_SENTINEL_STATUS!r}, got {status_result!r}"
        )

        # resolve_pre_phase_sha routes to fake
        sha_result = git_diff.resolve_pre_phase_sha(str(tmp_path))
        assert sha_result == _SENTINEL_SHA, (
            f"resolve_pre_phase_sha: expected sentinel {_SENTINEL_SHA!r}, got {sha_result!r}"
        )
    finally:
        _safe_reset()


# ═════════════════════════════════════════════════════════════════════════════
# AC6 — reset_default_git_diff_factory() restores subprocess default after override
# ═════════════════════════════════════════════════════════════════════════════

def test_ac6_reset_restores_subprocess_default_after_override(git_repo: Path) -> None:
    """AC6: reset_default_git_diff_factory() restores the subprocess default; get_git_diff() is a fresh _GitDiffSubprocess.

    §1i: global factory state restored in finally block.
    """
    set_factory = getattr(git_diff, "set_default_git_diff_factory", None)
    assert set_factory is not None, (
        "set_default_git_diff_factory absent (expected RED)"
    )

    reset_factory = getattr(git_diff, "reset_default_git_diff_factory", None)
    assert reset_factory is not None, (
        "reset_default_git_diff_factory absent (expected RED)"
    )

    get_git_diff_fn = getattr(git_diff, "get_git_diff", None)
    assert get_git_diff_fn is not None, (
        "get_git_diff absent (expected RED)"
    )

    _GitDiffSubprocess = getattr(git_diff, "_GitDiffSubprocess", None)
    assert _GitDiffSubprocess is not None, (
        "_GitDiffSubprocess absent (expected RED)"
    )

    fake = _FakeGitDiffPort()
    try:
        set_factory(lambda: fake)
        # Confirm override is active
        assert get_git_diff_fn() is fake, "Injection did not take effect"
    finally:
        reset_factory()

    # After reset: get_git_diff() should return a _GitDiffSubprocess instance
    resolved = get_git_diff_fn()
    assert isinstance(resolved, _GitDiffSubprocess), (
        f"After reset, get_git_diff() returned {resolved!r}, expected _GitDiffSubprocess instance"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC7 — get_git_diff() returns an object satisfying isinstance(..., GitDiffPort)
# ═════════════════════════════════════════════════════════════════════════════

def test_ac7_get_git_diff_default_returns_gitdiffport_instance() -> None:
    """AC7: get_git_diff() with no override returns an object satisfying isinstance(..., GitDiffPort)."""
    GitDiffPort = getattr(git_diff, "GitDiffPort", None)
    assert GitDiffPort is not None, (
        "GitDiffPort absent (expected RED)"
    )

    get_git_diff_fn = getattr(git_diff, "get_git_diff", None)
    assert get_git_diff_fn is not None, (
        "get_git_diff absent (expected RED)"
    )

    resolved = get_git_diff_fn()
    assert isinstance(resolved, GitDiffPort), (
        f"get_git_diff() returned {resolved!r} which is not an instance of GitDiffPort"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC8 — Monkeypatch-independence: consumer-module rebind still intercepts;
#        producer registry (get_git_diff()) is untouched
# ═════════════════════════════════════════════════════════════════════════════

def test_ac8_consumer_module_rebind_intercepts_and_registry_untouched(tmp_path: Path) -> None:
    """AC8: patch.object(consumer_module, 'git_diff_files', fake) intercepts at the consumer;
    get_git_diff() from the producer registry is untouched.

    Builds a stand-in consumer module object that imports git_diff_files.
    Then patch.object rebinds its name. Confirms (a) the consumer sees fake,
    (b) the producer's get_git_diff() is still the real _GitDiffSubprocess.
    """
    from unittest.mock import patch

    get_git_diff_fn = getattr(git_diff, "get_git_diff", None)
    assert get_git_diff_fn is not None, (
        "get_git_diff absent in lib.plugins.disk_truth.git_diff (expected RED)"
    )

    _GitDiffSubprocess = getattr(git_diff, "_GitDiffSubprocess", None)
    assert _GitDiffSubprocess is not None, (
        "_GitDiffSubprocess absent (expected RED)"
    )

    # Build a minimal consumer module stand-in that holds a bound name
    consumer = types.ModuleType("_test_consumer_B5719411")
    consumer.git_diff_files = git_diff.git_diff_files  # type: ignore[attr-defined]

    fake_files_result = ["__CONSUMER_FAKE__"]

    with patch.object(consumer, "git_diff_files", return_value=fake_files_result):
        # Consumer sees the fake
        consumer_result = consumer.git_diff_files("HEAD", str(tmp_path))
        assert consumer_result == fake_files_result, (
            f"Consumer-module rebind: expected {fake_files_result!r}, got {consumer_result!r}"
        )

        # Producer registry is untouched — get_git_diff() still returns _GitDiffSubprocess
        registry_port = get_git_diff_fn()
        assert isinstance(registry_port, _GitDiffSubprocess), (
            f"Producer registry was affected by consumer-module patch: "
            f"get_git_diff() returned {registry_port!r}, expected _GitDiffSubprocess"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC9 — _run_git and _apply_segment_filter remain module-level callables
# ═════════════════════════════════════════════════════════════════════════════

def test_ac9_private_helpers_remain_module_level_callables() -> None:
    """AC9: _run_git and _apply_segment_filter remain module-level callables (callable() is True)."""
    assert callable(git_diff._run_git), (  # type: ignore[attr-defined]
        "_run_git is not callable or absent from lib.plugins.disk_truth.git_diff"
    )
    assert callable(git_diff._apply_segment_filter), (  # type: ignore[attr-defined]
        "_apply_segment_filter is not callable or absent from lib.plugins.disk_truth.git_diff"
    )


# ═════════════════════════════════════════════════════════════════════════════
# AC10 — __init__.py re-exports all new symbols
# ═════════════════════════════════════════════════════════════════════════════

def test_ac10_init_py_reexports_new_seam_symbols() -> None:
    """AC10: GitDiffPort, get_git_diff, set_default_git_diff_factory, reset_default_git_diff_factory are importable from the package."""
    # Import from the package (not the submodule directly)
    import lib.plugins.disk_truth as disk_truth_pkg

    GitDiffPort = getattr(disk_truth_pkg, "GitDiffPort", None)
    assert GitDiffPort is not None, (
        "GitDiffPort not re-exported from lib.plugins.disk_truth (expected RED — __init__.py not yet updated)"
    )

    get_git_diff_fn = getattr(disk_truth_pkg, "get_git_diff", None)
    assert get_git_diff_fn is not None, (
        "get_git_diff not re-exported from lib.plugins.disk_truth (expected RED)"
    )

    set_fn = getattr(disk_truth_pkg, "set_default_git_diff_factory", None)
    assert set_fn is not None, (
        "set_default_git_diff_factory not re-exported from lib.plugins.disk_truth (expected RED)"
    )

    reset_fn = getattr(disk_truth_pkg, "reset_default_git_diff_factory", None)
    assert reset_fn is not None, (
        "reset_default_git_diff_factory not re-exported from lib.plugins.disk_truth (expected RED)"
    )

    # Bonus: default_git_diff should also be re-exported per spec §2.8
    default_fn = getattr(disk_truth_pkg, "default_git_diff", None)
    assert default_fn is not None, (
        "default_git_diff not re-exported from lib.plugins.disk_truth (expected RED)"
    )
