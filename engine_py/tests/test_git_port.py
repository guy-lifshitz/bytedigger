"""164E4EFA RED tests — lib/git_port.py read-surface adapter (rc-aware, REVISED 2026-06-17).

Spec: SHARED/memory/Decisions/2026-06-16_164E4EFA_git_port_read_surface_spec.md §1/§3
Surface under test: GitResult dataclass + git_read primitive + 4 convenience fns
(rev_parse, status_porcelain, ls_files_others, worktree_list_porcelain).
diff_name_only is DROPPED — AC1b asserts it is absent.

All imports of lib.git_port are DEFERRED to inside each test-function body so the file
COLLECTS cleanly while git_port.py does not yet exist (D1CF5FDF / §1q extension).
Every Group-A test exercises the UUT against a REAL temporary git repository — no
mocking of the UUT functions themselves (§1l stub-passability).

Group-B tests monkeypatch lib.git_port.git_read into the in-scope phase_5 dispatcher
sites to assert rc-branch routing; these fail today because (a) lib.git_port does not
exist (monkeypatch target missing) and (b) the dispatcher sites still call raw
bounded_run — the injection has no effect until GREEN migrates each site.

Failing today because lib/git_port.py does not exist.
Pass once GREEN creates git_port.py and migrates the in-scope phase_5 read sites.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Shared real-repo fixture  (§1j macOS-safe realpath, §1u real subprocess)
# ──────────────────────────────────────────────────────────────────────────────

def _init_repo(repo: Path) -> None:
    """Initialise a minimal real git repo with one HEAD commit."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "init.txt").write_text("hello\n")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Real macOS-safe git repo with one commit (§1j realpathSync equivalent)."""
    repo = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_path)))
    _init_repo(repo)
    return repo


# ══════════════════════════════════════════════════════════════════════════════
# GROUP A — git_port surface (fails now = module missing)
# ══════════════════════════════════════════════════════════════════════════════


# ── AC1 / AC1b ────────────────────────────────────────────────────────────────

def test_ac1_importable_surface() -> None:
    """AC1: GitResult, git_read and the 4 convenience fns are importable."""
    from bytedigger_engine.lib.git_port import (  # noqa: PLC0415
        GitResult,
        git_read,
        ls_files_others,
        rev_parse,
        status_porcelain,
        worktree_list_porcelain,
    )
    assert callable(git_read)
    assert callable(rev_parse)
    assert callable(status_porcelain)
    assert callable(ls_files_others)
    assert callable(worktree_list_porcelain)
    # GitResult is a frozen dataclass — verify fields exist
    gr = GitResult(returncode=0, stdout="", stderr="", timed_out=False)
    assert gr.returncode == 0
    assert gr.timed_out is False


def test_ac1b_diff_name_only_absent() -> None:
    """AC1b: diff_name_only is DROPPED — must not be exported."""
    from bytedigger_engine.lib import git_port as m  # noqa: PLC0415
    assert not hasattr(m, "diff_name_only"), (
        "diff_name_only should have been dropped (no in-scope consumer); "
        "it is still present — GREEN did not remove it"
    )


# ── AC2 — git_read returncode + timed_out preservation ───────────────────────

def test_ac2_git_read_rc0_head(git_repo: Path) -> None:
    """AC2: git_read(['rev-parse','HEAD'], cwd=repo) → rc==0, sha matches, timed_out==False."""
    from bytedigger_engine.lib.git_port import git_read  # noqa: PLC0415

    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    result = git_read(["rev-parse", "HEAD"], cwd=str(git_repo))
    assert result.returncode == 0, f"Expected rc==0, got {result.returncode}"
    assert result.stdout.strip() == expected_sha, (
        f"sha mismatch: {result.stdout.strip()!r} != {expected_sha!r}"
    )
    assert result.timed_out is False


def test_ac2_git_read_rc1_does_not_raise(git_repo: Path) -> None:
    """AC2: git_read returns rc==1 on staged diff --cached --quiet; never raises."""
    from bytedigger_engine.lib.git_port import git_read  # noqa: PLC0415

    (git_repo / "staged.txt").write_text("new content\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=git_repo, check=True)

    # git diff --cached --quiet exits 1 when staged changes exist
    result = git_read(
        ["diff", "--cached", "--quiet", "--", "staged.txt"],
        cwd=str(git_repo),
    )
    assert result.returncode == 1, f"Expected rc==1 (staged changes), got {result.returncode}"
    assert result.timed_out is False
    # Must NOT have raised — if we get here, the test passes this assertion


def test_ac2_timed_out_flag_matches_rc124(git_repo: Path) -> None:
    """AC2: calling with timeout=10 succeeds; rc==0 and timed_out==False."""
    from bytedigger_engine.lib.git_port import git_read  # noqa: PLC0415

    result = git_read(["rev-parse", "HEAD"], cwd=str(git_repo), timeout=10)
    assert result.returncode == 0
    assert result.timed_out is False


# ── AC2b — dir_ emits git -C <dir_> arg-vector ───────────────────────────────

def test_ac2b_dir_arg_vector_form(git_repo: Path) -> None:
    """AC2b: git_read with dir_=repo_str uses 'git -C <dir_> ...' form; succeeds."""
    from bytedigger_engine.lib.git_port import git_read  # noqa: PLC0415

    # Behavioral: -C form resolves the repo even without cwd
    result = git_read(
        ["rev-parse", "--git-common-dir"],
        dir_=str(git_repo),
    )
    assert result.returncode == 0, (
        f"Expected rc==0 for 'git -C <dir_> rev-parse --git-common-dir', got {result.returncode}; "
        f"stderr={result.stderr!r}"
    )
    assert result.stdout.strip() != "", "Expected non-empty --git-common-dir output"


# ── AC3 / AC4 — convenience fns ──────────────────────────────────────────────

def test_ac3_rev_parse_cwd_matches_head(git_repo: Path) -> None:
    """AC3: rev_parse(cwd=repo) returns the known HEAD sha."""
    from bytedigger_engine.lib.git_port import rev_parse  # noqa: PLC0415

    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert rev_parse(cwd=str(git_repo)) == expected


def test_ac3_rev_parse_parity_with_git_read(git_repo: Path) -> None:
    """AC3: rev_parse(cwd=r) == git_read(['rev-parse','HEAD'], cwd=r).stdout.strip()."""
    from bytedigger_engine.lib.git_port import git_read, rev_parse  # noqa: PLC0415

    via_convenience = rev_parse(cwd=str(git_repo))
    via_primitive = git_read(["rev-parse", "HEAD"], cwd=str(git_repo)).stdout.strip()
    assert via_convenience == via_primitive, (
        f"rev_parse and git_read parity broken: {via_convenience!r} != {via_primitive!r}"
    )


def test_ac3_rev_parse_flag_show_toplevel(git_repo: Path) -> None:
    """AC3: rev_parse(flag='--show-toplevel', cwd=repo) returns repo root."""
    from bytedigger_engine.lib.git_port import rev_parse  # noqa: PLC0415

    expected = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert rev_parse(flag="--show-toplevel", cwd=str(git_repo)) == expected


def test_ac4_status_porcelain_dirty(git_repo: Path) -> None:
    """AC4: status_porcelain returns str containing untracked file."""
    from bytedigger_engine.lib.git_port import status_porcelain  # noqa: PLC0415

    (git_repo / "new.txt").write_text("untracked\n")
    result = status_porcelain(cwd=str(git_repo))
    assert isinstance(result, str)
    assert "new.txt" in result


def test_ac4_status_porcelain_clean(git_repo: Path) -> None:
    """AC4: status_porcelain returns empty string on clean repo."""
    from bytedigger_engine.lib.git_port import status_porcelain  # noqa: PLC0415

    result = status_porcelain(cwd=str(git_repo))
    assert result == "", f"Expected empty string on clean repo, got {result!r}"


def test_ac4_ls_files_others_untracked(git_repo: Path) -> None:
    """AC4: ls_files_others returns list containing untracked file."""
    from bytedigger_engine.lib.git_port import ls_files_others  # noqa: PLC0415

    (git_repo / "untracked.txt").write_text("data\n")
    result = ls_files_others(cwd=str(git_repo))
    assert isinstance(result, list)
    assert "untracked.txt" in result


def test_ac4_ls_files_others_empty_clean(git_repo: Path) -> None:
    """AC4: ls_files_others returns [] when no untracked files."""
    from bytedigger_engine.lib.git_port import ls_files_others  # noqa: PLC0415

    result = ls_files_others(cwd=str(git_repo))
    assert isinstance(result, list)
    assert result == []


def test_ac4_worktree_list_porcelain_nonempty(git_repo: Path) -> None:
    """AC4: worktree_list_porcelain returns non-empty str for any git repo."""
    from bytedigger_engine.lib.git_port import worktree_list_porcelain  # noqa: PLC0415

    result = worktree_list_porcelain(cwd=str(git_repo))
    assert isinstance(result, str)
    assert result.strip() != "", "worktree list --porcelain must not be empty for a git repo"


# ── AC8 — no subprocess import in git_port.py ────────────────────────────────

def test_ac8_git_port_no_raw_subprocess() -> None:
    """AC8: git_port.py routes through bounded_run only — no import subprocess."""
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.find_spec("bytedigger_engine.lib.git_port")
    if spec is None or spec.origin is None:
        pytest.fail("lib.git_port not found — module does not exist yet (expected RED failure)")

    source = Path(spec.origin).read_text()
    assert "import subprocess" not in source, (
        "git_port.py must not 'import subprocess' — all git calls must go through bounded_run"
    )
    assert "subprocess." not in source, (
        "git_port.py must not reference subprocess.* directly"
    )


# ══════════════════════════════════════════════════════════════════════════════
# GROUP B — per-site rc-branch routing (forcing-function for AC5 / G1–G5)
#
# Strategy: monkeypatch lib.git_port.git_read in the module where phase_5
# imports it.  Today this fails because (a) lib.git_port does not exist
# (monkeypatch target is absent) and (b) phase_5 calls raw bounded_run —
# the injection has no effect.  After GREEN migrates each site to route
# through git_port.git_read, the injection takes effect and the rc-branch
# decision can be asserted.
#
# §1i (singleton/time ACs pre-stage): all shared state pre-staged in fixture.
#
# stub-passability: allow
# Rationale: git_read is patched here ONLY as an injection point to verify
# phase_5 dispatcher routing AFTER GREEN migration.  The UUT under test in
# Group B is the phase_5 site (_paths_have_staged_changes etc.), NOT git_read
# itself.  Group A tests exercise git_read for real against a live repo with
# NO mocking.  This use of setattr(gp, "git_read", ...) is the spec-endorsed
# "spy/capture bounded_run args OR behavioral" pattern (AC2b).
# ══════════════════════════════════════════════════════════════════════════════


def _make_git_result(returncode: int, stdout: str = "", stderr: str = "") -> object:
    """Build a GitResult inside the test body (deferred import, D1CF5FDF)."""
    from bytedigger_engine.lib.git_port import GitResult  # noqa: PLC0415
    return GitResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=(returncode == 124),
    )


# ── G1: _paths_have_staged_changes — 3-way rc branch ────────────────────────

def test_g1_paths_have_staged_changes_rc1_true(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G1: rc==1 (staged changes present) → _paths_have_staged_changes returns True."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(1, "", ""),
    )
    # The forcing-function: after GREEN migration the monkeypatch redirects the
    # site through git_read which returns the injected GitResult.
    # We also inject at the git_port level to ensure both paths are covered.
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(1, "", ""))

    result = p5._paths_have_staged_changes(
        git_cwd=str(git_repo),
        paths=["staged.txt"],
    )
    assert result is True, f"rc==1 must return True (staged changes), got {result!r}"


def test_g1_paths_have_staged_changes_rc0_false(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G1: rc==0 (no staged changes) → _paths_have_staged_changes returns False."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(0, "", ""),
    )
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(0, "", ""))

    result = p5._paths_have_staged_changes(
        git_cwd=str(git_repo),
        paths=["clean.txt"],
    )
    assert result is False, f"rc==0 must return False (no staged), got {result!r}"


def test_g1_paths_have_staged_changes_ambiguous_rc_true(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G1: ambiguous rc (e.g. 2) → _paths_have_staged_changes returns True (fail-toward-commit)."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(2, "", "error"),
    )
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(2, "", "error"))

    result = p5._paths_have_staged_changes(
        git_cwd=str(git_repo),
        paths=["file.txt"],
    )
    assert result is True, f"ambiguous rc==2 must return True (fail-toward-commit), got {result!r}"


# ── G2: _filter_gitignored_paths — rc∉{0,1} → keep-all ──────────────────────

def test_g2_filter_gitignored_rc128_keeps_all(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2: rc==128 (git error) → _filter_gitignored_paths returns ALL input paths (degraded keep-all)."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(128, "", "fatal: not a git repository"),
    )
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(128, "", "fatal"))

    input_paths = ["app.py", "app.log", "main.py"]
    result = p5._filter_gitignored_paths(
        paths=input_paths,
        git_cwd=str(git_repo),
    )
    assert sorted(result) == sorted(input_paths), (
        f"rc==128 must keep all paths (degraded); got {result!r}"
    )


# ── G4: _main_checkout_root — rc==124 (timeout) → None ──────────────────────

def test_g4_main_checkout_root_rc124_returns_none(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G4: rc==124 (timeout) → _main_checkout_root returns None."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(124, "", ""),
    )
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(124, "", ""))

    result = p5._main_checkout_root(git_cwd=str(git_repo))
    assert result is None, f"rc==124 (timeout) must return None, got {result!r}"


def test_g4_main_checkout_root_main_repo_returns_none(git_repo: Path) -> None:
    """G4: calling _main_checkout_root on a plain (non-worktree) repo returns None.

    This is the real-repo branch: common-dir == realpath(git_cwd) → None.
    Fails today because lib.git_port does not exist.  After GREEN migrates the
    bounded_run call in _main_checkout_root to git_read (dir_= form), the
    real-repo path remains correct (rc==0, common-dir == repo/.git → parent == repo
    == realpath(git_cwd) → returns None).
    """
    from bytedigger_engine.lib import git_port as _gp  # noqa: PLC0415 — triggers ImportError today
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    result = p5._main_checkout_root(git_cwd=str(git_repo))
    assert result is None, (
        f"A plain (non-worktree) repo must return None from _main_checkout_root; got {result!r}"
    )


# ── G5: _finding_in_diff_hunks — rc!=0 → conservative True ──────────────────

def test_g5_finding_in_diff_hunks_rc1_conservative_true(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G5: rc==1 → _finding_in_diff_hunks returns True (conservative hard-fail)."""
    from bytedigger_engine.lib import git_port as gp  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **k: _make_git_result(1, "", ""),
    )
    monkeypatch.setattr(gp, "git_read", lambda *a, **k: _make_git_result(1, "", ""))

    result = p5._finding_in_diff_hunks(
        abs_path=str(git_repo / "init.txt"),
        red_sha=head_sha,
        finding_line=1,
        git_cwd=str(git_repo),
    )
    assert result is True, f"rc==1 must return True (conservative), got {result!r}"


def test_g5_finding_in_diff_hunks_none_finding_line(git_repo: Path) -> None:
    """G5: finding_line=None → True (conservative, no git call needed).

    Fails today due to lib.git_port import failing inside _make_git_result.
    After GREEN: this path never calls git at all (early return), but the
    import of lib.git_port must succeed for the deferred import in the group
    to work.
    """
    from bytedigger_engine import lib
    import bytedigger_engine.lib.git_port  # noqa: PLC0415 — triggers ImportError today (RED forcing)
    from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    result = p5._finding_in_diff_hunks(
        abs_path=str(git_repo / "init.txt"),
        red_sha=head_sha,
        finding_line=None,
        git_cwd=str(git_repo),
    )
    assert result is True, f"finding_line=None must always return True, got {result!r}"
