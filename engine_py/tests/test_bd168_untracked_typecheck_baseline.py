"""RED tests for bd#168 -- the Phase 5 typecheck baseline must see untracked files.

Spec: docs/decisions/2026-10-02-bd168-untracked-typecheck-baseline.md

`_tree_identical_to_head_for_paths` only runs `git diff --quiet HEAD -- <paths>`,
which cannot see untracked files, so a GREEN that only ADDS a module is judged
"identical to HEAD" and the baseline degrades to None (never blocks).

Real temp git repos and real `git`; only mypy is canned where a count is needed
(own local copies of the gh1612b helpers, nothing imported across test files).
AC7 is a regression run of the two sibling files, no test here.

Pre-GREEN expected FAIL: AC1, AC4 (a/b/c), AC5(a), AC6 (AC1-shape value).
Pre-GREEN expected PASS (pins): AC2, AC3, AC5(b), AC5(c) control.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine.workflows import phase_5_implement
from bytedigger_engine.lib import git_port
from bytedigger_engine.lib.git_port import GitResult

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not available")


# --- local helpers ----------------------------------------------------------


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo_with_commit(dest: Path) -> Path:
    """Repo with one commit containing committed a.py."""
    repo = Path(os.path.realpath(str(dest)))
    _init_git_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    return repo


def _make_local_repo_with_red_commit(dest: Path) -> Path:
    repo = _make_repo_with_commit(dest)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_stub.py").write_text("def test_placeholder(): pass\n")
    subprocess.run(["git", "add", "tests/test_stub.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "RED commit"], cwd=repo, check=True)
    return repo


def _mypy_passthrough_except(monkeypatch, canned_stdout: str, canned_rc: int = 0):
    """Can ONLY mypy invocations; every other subprocess.run (git) is real."""
    mypy_calls: list[list] = []
    _real_run = subprocess.run

    def _fake_run(argv, **kwargs):
        if not (bool(argv) and any("mypy" in str(a) for a in argv[:2])):
            return _real_run(argv, **kwargs)
        mypy_calls.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv, returncode=canned_rc, stdout=canned_stdout, stderr="",
        )

    monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)
    return mypy_calls


def _capture_events(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def _capture(event_type, payload, severity="info"):
        captured.append({"type": event_type, "payload": payload, "severity": severity})

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture)
    return captured


def _identity_events(captured: list[dict]) -> list[dict]:
    return [e for e in captured if e["type"] == "baseline_tree_identity_check_failed"]


def _patch_git_read(monkeypatch, diff_result=None, ls_files_result=None):
    """Patch git_port.git_read; `diff` / `ls-files` calls answered by the given
    GitResult-or-Exception, everything else passes to the real implementation.
    """
    real = git_port.git_read

    def _fake(args, *a, **kw):
        toks = [str(x) for x in args]
        for verb, answer in (("diff", diff_result), ("ls-files", ls_files_result)):
            if verb in toks and answer is not None:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return real(args, *a, **kw)

    monkeypatch.setattr(git_port, "git_read", _fake)


def _res(rc: int) -> GitResult:
    return GitResult(returncode=rc, stdout="", stderr="fatal: boom\n", timed_out=False)


# --- AC1 ---------------------------------------------------------------------


def test_ac1_untracked_file_is_not_identical_to_head(tmp_path):
    """AC1: an untracked new.py makes the tree NOT identical to HEAD.
    FAILS today: `git diff HEAD` ignores untracked files so the helper says True.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    new = repo / "new.py"
    new.write_text("x: int = 'a'\n")

    result = phase_5_implement._tree_identical_to_head_for_paths([str(new)], str(repo))

    assert result is False, f"AC1: untracked file must be 'not identical'; got {result!r}"


def test_ac1_mixed_tracked_unchanged_and_untracked_is_not_identical(tmp_path):
    """AC1 (mixed list): [tracked unchanged a.py, untracked new.py] -> False.
    FAILS today: diff sees nothing for either path, so True.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    new = repo / "new.py"
    new.write_text("x: int = 'a'\n")

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py"), str(new)], str(repo),
    )

    assert result is False, f"AC1 mixed: expected False; got {result!r}"


# --- AC2 ---------------------------------------------------------------------


def test_ac2_tracked_unchanged_is_identical_and_silent(tmp_path, monkeypatch):
    """AC2: committed, untouched a.py -> True, no event. Pins today's behaviour
    (passes pre-GREEN); guards the GREEN against over-reporting.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    captured = _capture_events(monkeypatch)

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is True
    assert captured == [], f"AC2: no event expected; got {captured!r}"


# --- AC3 ---------------------------------------------------------------------


def test_ac3_tracked_modified_is_not_identical(tmp_path, monkeypatch):
    """AC3: committed a.py edited -> False (diff rc 1), and no event is emitted.
    Pins today's behaviour (passes pre-GREEN).
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    (repo / "a.py").write_text("A = 2\n")
    captured = _capture_events(monkeypatch)

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is False
    assert captured == [], f"AC3: rc 1 is a plain 'differs', no event; got {captured!r}"


# --- AC4 ---------------------------------------------------------------------


def test_ac4a_git_read_raises_is_not_identical_and_logged(tmp_path, monkeypatch):
    """AC4(a): git_read raising -> False plus exactly one
    baseline_tree_identity_check_failed event whose reason is the exception text.
    FAILS today: returns False but emits nothing.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    captured = _capture_events(monkeypatch)
    _patch_git_read(monkeypatch, diff_result=RuntimeError("bd168-boom"))

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is False
    events = _identity_events(captured)
    assert len(events) == 1, f"AC4(a): expected exactly one event; got {captured!r}"
    payload = events[0]["payload"]
    assert payload["reason"] == "bd168-boom"
    assert payload["phase"] == 5
    assert payload["step"] == "tree_identical_to_head_for_paths"
    assert events[0]["severity"] == "warning"


def test_ac4b_diff_rc128_is_not_identical_and_logged(tmp_path, monkeypatch):
    """AC4(b): diff rc=128 -> False and event reason starts with 'diff rc=128'.
    FAILS today: no event.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    captured = _capture_events(monkeypatch)
    _patch_git_read(monkeypatch, diff_result=_res(128))

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is False
    events = _identity_events(captured)
    assert len(events) == 1, f"AC4(b): expected one event; got {captured!r}"
    assert events[0]["payload"]["reason"].startswith("diff rc=128")


def test_ac4c_ls_files_rc128_is_not_identical_and_logged(tmp_path, monkeypatch):
    """AC4(c): diff rc=0 but ls-files rc=128 -> False and event reason starts
    with 'ls-files rc=128'. FAILS today: returns True (diff rc 0), no event.
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    captured = _capture_events(monkeypatch)
    _patch_git_read(
        monkeypatch,
        diff_result=GitResult(returncode=0, stdout="", stderr="", timed_out=False),
        ls_files_result=_res(128),
    )

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is False
    events = _identity_events(captured)
    assert len(events) == 1, f"AC4(c): expected one event; got {captured!r}"
    assert events[0]["payload"]["reason"].startswith("ls-files rc=128")


def test_ac4d_ls_files_raises_is_not_identical_and_logged(tmp_path, monkeypatch):
    """AC4(d): diff rc 0, ls-files read raises -> False plus exactly one event
    whose reason equals the exception text. FAILS today: returns True (diff rc 0).
    """
    repo = _make_repo_with_commit(tmp_path / "repo")
    captured = _capture_events(monkeypatch)
    _patch_git_read(
        monkeypatch,
        diff_result=GitResult(returncode=0, stdout="", stderr="", timed_out=False),
        ls_files_result=RuntimeError("bd168-ls-boom"),
    )

    result = phase_5_implement._tree_identical_to_head_for_paths(
        [str(repo / "a.py")], str(repo),
    )

    assert result is False
    events = _identity_events(captured)
    assert len(events) == 1, f"AC4(d): expected one event; got {captured!r}"
    assert events[0]["payload"]["reason"] == "bd168-ls-boom"


# --- AC5 ---------------------------------------------------------------------


def test_ac5a_untracked_new_module_baseline_is_zero_not_none(tmp_path, monkeypatch):
    """AC5(a): RED commit repo, GREEN adds untracked new.py with a type error;
    mypy canned to one error on new.py for the live tree. The baseline helper
    must return 0 (path absent at HEAD), not None. The helper returns 0 before
    any mypy call (new.py does not exist in the baseline worktree), so the
    canned mypy is unused here; it only guards against a real mypy run.
    FAILS today: the untracked file is invisible to the clean-tree check, so None.
    Driven at the baseline-helper level (not _verify_green_typecheck), which
    needs far less fixture and is the chokepoint the spec names.
    """
    repo = _make_local_repo_with_red_commit(tmp_path / "repo")
    new = repo / "new.py"
    new.write_text('x: int = "s"\n')
    _mypy_passthrough_except(
        monkeypatch, f'{new}:1: error: Incompatible types  [assignment]\n', canned_rc=1,
    )

    result = phase_5_implement._compute_baseline_typecheck_count(
        [str(new.resolve())], str(repo), "cfg_git_cwd",
    )

    assert result == 0, f"AC5(a): expected baseline 0, got {result!r}"


def test_ac5b_zero_baseline_one_current_blocks_under_enforce():
    """AC5(b): delta_verdict(0, 1, enforce=True).would_block is True. Pins the
    downstream half of the chain (passes pre-GREEN).
    """
    from bytedigger_engine.net_new_delta import delta_verdict

    assert delta_verdict(0, 1, enforce=True).would_block is True


def test_ac5c_control_clean_untracked_module_does_not_block(tmp_path, monkeypatch):
    """AC5(c) control: untracked ok.py, mypy canned clean (0 live findings) ->
    the baseline helper result never produces a block. Pins both before and
    after GREEN. Driven at the baseline-helper level, not _verify_green_typecheck.
    """
    from bytedigger_engine.net_new_delta import delta_verdict

    repo = _make_local_repo_with_red_commit(tmp_path / "repo")
    ok = repo / "ok.py"
    ok.write_text("y: int = 1\n")
    _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

    baseline = phase_5_implement._compute_baseline_typecheck_count(
        [str(ok.resolve())], str(repo), "cfg_git_cwd",
    )

    assert baseline in (None, 0)
    assert delta_verdict(baseline, 0, enforce=True).would_block is False


# --- AC6 ---------------------------------------------------------------------


def test_ac6_identity_check_never_reaches_llm_dispatch(tmp_path, monkeypatch):
    """AC6: with llm_subprocess.invoke_llm_subprocess patched to raise, the AC1
    and AC2 calls still return their expected values. Pins the plain-git nature
    of the check. FAILS today on the AC1-shape value (untracked -> True), not on
    the LLM seam; the seam half is a guard that must stay green after GREEN.
    """
    from bytedigger_engine import llm_subprocess

    def _boom(*a, **kw):
        raise AssertionError("bd168 AC6: identity check reached the LLM dispatch seam")

    monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", _boom)

    repo = _make_repo_with_commit(tmp_path / "repo")
    new = repo / "new.py"
    new.write_text("z = 1\n")

    fn = phase_5_implement._tree_identical_to_head_for_paths
    assert fn([str(repo / "a.py")], str(repo)) is True      # AC2 shape
    assert fn([str(new)], str(repo)) is False               # AC1 shape
