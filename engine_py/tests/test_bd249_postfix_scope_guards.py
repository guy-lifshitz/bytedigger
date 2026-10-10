"""Guard tests for bd#249 - post-fix scope behaviour that must not change.

Spec: docs/decisions/2026-10-10-bd249-postfix-empty-scope.md (AC4, AC6, AC7, AC8).

These already hold before the empty-scope enforcement lands and protect the
non-empty-scope path and the flag registry afterwards.  The failing tests live
in test_bd249_postfix_empty_scope.py.

Hermetic: tmp_path, a real tiny git repo with real commits, run_test_command
(the pytest process boundary) replaced by a recording sentinel, _emit_safe
captured.  No sys.path mutation.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine.workflows import phase_6_review

_FLAG = "HAL_POST_FIX_EMPTY_SCOPE_ENFORCE"
_FLAG_SUFFIX = "POST_FIX_EMPTY_SCOPE_ENFORCE"
_EVENT = "post_fix_empty_scope"
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _FakeRun:
    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str


class _Env:
    """Real tiny git repo + scratchpad, sentinel pytest runner, event capture."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.scratch = tmp_path / "scratch"
        (self.scratch / "reviews").mkdir(parents=True)
        self.events = []  # type: list
        self.run_calls = []  # type: list
        self.monkeypatch = monkeypatch
        for var in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"):
            monkeypatch.delenv(var, raising=False)
        for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
            monkeypatch.delenv(prefix + _FLAG_SUFFIX, raising=False)
        self._git("init", "-q")
        stdout = tmp_path / "stdout.txt"
        stdout.write_text("1 passed in 0.1s")

        def _sentinel(argv, cwd, timeout=180):
            self.run_calls.append(list(argv))
            return _FakeRun(exit_code=0, n_passed=1, n_failed=0, stdout_path=str(stdout))

        monkeypatch.setattr(phase_6_review, "run_test_command", _sentinel)
        monkeypatch.setattr(
            phase_6_review, "_emit_safe",
            lambda et, p, **kw: self.events.append({"type": et, "payload": p}),
        )
        self.base_sha = ""

    def _git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false"] + list(args),
            cwd=str(self.repo), check=True, stdout=subprocess.PIPE,
        ).stdout.decode().strip()

    def write(self, rel: str, body: str = "# fixture\n") -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    def commit_all(self, msg: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", msg)

    def base_commit(self, tracked_extra=()):
        """Base commit holding pkg/module.py (+ extras); records its sha."""
        self.write("pkg/module.py", "x = 1\n")
        for rel in tracked_extra:
            self.write(rel)
        self.commit_all("base")
        self.base_sha = self._git("rev-parse", "HEAD")

    def fix_commit(self):
        """The 'fix commit': a real commit touching only pkg/module.py."""
        self.write("pkg/module.py", "x = 2\n")
        self.commit_all("fix")

    def persist_red(self, lines) -> None:
        f = self.scratch / "integrity" / "red-test-paths.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines))

    def ctx(self):
        from bytedigger_engine.contracts import WorkflowContext
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={"scratchpad_dir": str(self.scratch), "git_cwd": str(self.repo)},
            question="bd249", session_id="test-session-bd249", persona="hal",
            framework=None, domain=None,
        )

    def run(self, manifest=("pkg/module.py",)):
        prev = MagicMock()
        prev.data = {
            "cycle": 1, "pre_fix_sha": self.base_sha,
            "worker_written_paths": list(manifest),
            "manifest_source": "harness_tool_record",
        }
        return phase_6_review._run_pytest_post_fix(self.ctx(), prev)

    def of(self, etype):
        return [e["payload"] for e in self.events if e["type"] == etype]

    def test_args(self):
        assert len(self.run_calls) == 1, "pytest must be invoked exactly once: %r" % (self.run_calls,)
        out = []
        for a in self.run_calls[0]:
            base = a.replace("\\", "/").rsplit("/", 1)[-1]
            if fnmatch.fnmatch(base, "test_*.py") or fnmatch.fnmatch(base, "*_test.py"):
                out.append(a)
        return out


def _has(args, rel: str) -> bool:
    return any(a == rel or a.replace("\\", "/").endswith("/" + rel) for a in args)


class TestPostFixScopeGuards:

    def test_ac4_reference_case_frozen_red_test_in_scope(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.base_commit(tracked_extra=["tests/test_frozen.py"])
        env.fix_commit()
        env.persist_red(["tests/test_frozen.py"])
        monkeypatch.setenv(_FLAG, "1")

        result = env.run()

        assert result.status == "ok", result
        assert _has(env.test_args(), "tests/test_frozen.py"), env.run_calls
        assert len(env.run_calls) == 1
        assert env.of(_EVENT) == [], env.events
        scopes = env.of("post_fix_pytest_scope")
        assert len(scopes) == 1 and scopes[0]["n_red"] == 1, env.events

    def test_ac6_sibling_keeps_scope_non_empty(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.base_commit(tracked_extra=["tests/test_module_extra.py"])
        env.fix_commit()
        monkeypatch.setenv(_FLAG, "1")

        result = env.run()

        assert result.status == "ok", result
        assert _has(env.test_args(), "tests/test_module_extra.py"), env.run_calls
        assert env.of(_EVENT) == [], env.events

    @pytest.mark.parametrize("auth", ["api_key", "oauth"])
    def test_ac7_outcome_independent_of_auth_mode(self, tmp_path, monkeypatch, auth):
        if auth == "api_key":
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
            monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        else:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-not-real")

        env = _Env(tmp_path, monkeypatch)
        env.base_commit(tracked_extra=["tests/test_frozen.py"])
        env.fix_commit()
        env.persist_red(["tests/test_frozen.py"])
        monkeypatch.setenv(_FLAG, "1")
        result = env.run()
        assert result.status == "ok", result
        assert _has(env.test_args(), "tests/test_frozen.py"), env.run_calls
        assert env.of(_EVENT) == [], env.events

    def test_ac8_flag_owner_lint_green(self):
        proc = subprocess.run(
            [sys.executable, "scripts/flag_owner_lint.py"], cwd=str(_REPO_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
            env={k: v for k, v in os.environ.items()
                 if k not in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")},
        )
        assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
