"""Regression tests for ppba#1603 — empty GREEN manifest must not silently skip the commit.

Context: `manifest_from_result` parses the GREEN agent's transcript. When the
phrasing drifts (run `forge-1785898177-1599` closed with
``Files modified: [bark/posture/anchor_judge.py, ...]``), the manifest comes
back empty, `_commit_green_code` takes the `empty_manifest` skip branch and
still returns ``status="ok"``. The production edits are real on disk but never
committed — phase 8 can then ship a PR containing only the RED tests and report
a clean DONE.

Contract under test:
  - empty manifest + git shows production changes since red_commit_sha
    → emit ``green_manifest_git_divergence``, commit the git-derived paths,
      return a real ``green_commit_sha`` (git is ground truth, not the parser)
  - empty manifest + git shows nothing but test changes
    → unchanged behaviour: ``green_commit_skipped`` / ``reason=empty_manifest``
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.workflows.phase_5_implement import _commit_green_code  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── helpers (mirror test_phase_5_step5_commit_green_code.py) ─────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(
        repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED: add failing test"
    )
    return repo, red_sha


def _write_file(repo: Path, relpath: str, body: str = "# impl\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_commit_sha=None, cycle: int = 1, **extra) -> MagicMock:
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    prev.data = data
    return prev


def _capture_events(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    from bytedigger_engine.workflows import phase_5_implement

    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


# ─── tests ────────────────────────────────────────────────────────────────────


class TestEmptyManifestGitDivergence:
    def test_empty_manifest_with_git_prod_changes_commits_git_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The ppba#1603 scenario: GREEN wrote production files, transcript parse
        yielded nothing. The engine must commit what git sees, not skip."""
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # GREEN edits: one pre-existing tracked file, one brand-new untracked file.
        _write_file(repo, "src/placeholder.py", "# placeholder\ndef fixed(): return 1\n")
        _write_file(repo, "src/anchor_judge.py", "def judge(): return True\n")

        captured = _capture_events(monkeypatch)
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            red_commit_sha=red_sha,
            cycle=1,
            worker_written_paths=[],  # transcript phrasing not recognised
            manifest_source="harness_tool_record",
        )

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok", f"expected ok, got {result.status}: {getattr(result, 'error', '')}"

        # Must NOT take the silent skip branch.
        skips = [e for e in captured if e["type"] == "green_commit_skipped"]
        assert not skips, f"commit was skipped despite real production changes: {skips}"

        # Must telemeter the manifest/git disagreement.
        div = [e for e in captured if e["type"] == "green_manifest_git_divergence"]
        assert len(div) == 1, (
            f"expected 1 green_manifest_git_divergence event, got {len(div)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        payload = div[0]["payload"]
        assert payload.get("chosen") == "git"
        assert payload.get("phase") == 5
        names = {Path(p).name for p in payload.get("git_prod_paths", [])}
        assert names == {"placeholder.py", "anchor_judge.py"}, names

        # A real commit must exist.
        sha = result.data.get("green_commit_sha")
        assert isinstance(sha, str) and len(sha) == 40, f"expected a real green_commit_sha, got {sha!r}"

        committed = subprocess.run(
            ["git", "diff", "--name-only", red_sha, "HEAD"],
            capture_output=True, text=True, cwd=repo, check=True,
        ).stdout.split()
        assert sorted(committed) == ["src/anchor_judge.py", "src/placeholder.py"], committed

    def test_empty_manifest_with_only_test_changes_still_skips(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Guard against over-correction: the git fallback is production-only, so a
        dirty test file must not resurrect the commit (test edits stay excluded)."""
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        _write_file(repo, "tests/test_red.py", "def test_fail(): assert True\n")

        captured = _capture_events(monkeypatch)
        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            red_commit_sha=red_sha,
            cycle=1,
            worker_written_paths=[],
            manifest_source="harness_tool_record",
        )

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok"
        assert result.data.get("green_commit_sha") is None
        skips = [e for e in captured if e["type"] == "green_commit_skipped"]
        assert len(skips) == 1, f"expected the empty_manifest skip, got {[e['type'] for e in captured]}"
        assert skips[0]["payload"].get("reason") == "empty_manifest"
        assert not [e for e in captured if e["type"] == "green_manifest_git_divergence"]
