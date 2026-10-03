"""RED tests for bd#90 - phase_6 _run_pytest_post_fix scope is never empty.

Spec: docs/decisions/2026-10-03-bd90-postfix-test-scope.md (AC1-AC5).

The scope is the ordered, de-duplicated union of RED paths (prev.data list, else
the persisted integrity/red-test-paths.txt), manifest test files, and sibling
tracked tests of changed source files (max 50, sorted). Only paths that exist on
disk under the git cwd are kept. A `post_fix_pytest_scope` event is emitted with
{n_red, n_manifest, n_sibling, n_total} before pytest is invoked.

Hermetic: tmp_path, a real tiny git repo (git init + git add, no commits needed),
run_test_command and _emit_safe stubbed. No sys.path mutation.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

from bytedigger_engine.workflows import phase_6_review

_VALID_SHA = "a" * 40
_SCOPE_EVENT = "post_fix_pytest_scope"


@dataclass
class _FakeRun:
    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str


class _Env:
    """A tiny git repo + scratchpad with stubbed pytest runner and event capture."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.scratch = tmp_path / "scratch"
        (self.scratch / "reviews").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        self.events = []  # type: list
        self.run_calls = []  # type: list
        stdout = tmp_path / "stdout.txt"
        stdout.write_text("1 passed in 0.1s")

        def _fake_run(argv, cwd, timeout=180):
            self.run_calls.append(list(argv))
            return _FakeRun(exit_code=0, n_passed=1, n_failed=0, stdout_path=str(stdout))

        monkeypatch.setattr(phase_6_review, "run_test_command", _fake_run)
        monkeypatch.setattr(
            phase_6_review, "_emit_safe",
            lambda et, p, **kw: self.events.append({"type": et, "payload": p}),
        )

    def write(self, rel: str, tracked: bool = True) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# fixture\n")
        if tracked:
            subprocess.run(["git", "add", rel], cwd=str(self.repo), check=True)

    def persist_red(self, lines) -> None:
        f = self.scratch / "integrity" / "red-test-paths.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(lines))

    def ctx(self):
        from bytedigger_engine.contracts import WorkflowContext
        return WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={"scratchpad_dir": str(self.scratch), "git_cwd": str(self.repo)},
            question="bd90", session_id="test-session-bd90", persona="hal",
            framework=None, domain=None,
        )

    def prev(self, manifest, **extra):
        prev = MagicMock()
        prev.data = {
            "cycle": 1, "pre_fix_sha": _VALID_SHA,
            "worker_written_paths": list(manifest),
            "manifest_source": "harness_tool_record",
            **extra,
        }
        return prev

    def run(self, manifest, **extra):
        return phase_6_review._run_pytest_post_fix(self.ctx(), self.prev(manifest, **extra))

    def scope_events(self):
        return [e["payload"] for e in self.events if e["type"] == _SCOPE_EVENT]

    def test_args(self):
        """Test paths in the single pytest argv (everything ending in .py)."""
        assert len(self.run_calls) == 1, "pytest must be invoked exactly once: %r" % (self.run_calls,)
        return [a for a in self.run_calls[0] if a.endswith(".py")]


def _has(args, rel: str) -> bool:
    return any(a == rel or a.replace("\\", "/").endswith("/" + rel) for a in args)


class TestPostFixTestScope:

    def test_ac1_red_path_from_persisted_file_joins_scope(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")
        env.write("tests/test_red_one.py")
        env.persist_red(["tests/test_red_one.py"])

        result = env.run(["pkg/mod.py"])

        assert result.status == "ok"
        assert _has(env.test_args(), "tests/test_red_one.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, "expected one post_fix_pytest_scope event: %r" % (env.events,)
        assert scopes[0]["n_red"] >= 1
        for key in ("n_red", "n_manifest", "n_sibling", "n_total"):
            assert key in scopes[0], scopes[0]

    def test_ac2_sibling_tests_pulled_in_for_changed_source(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")
        env.write("tests/test_mod_extra.py")   # stem match, different directory
        env.write("pkg/test_neighbor.py")      # same directory
        env.write("tests/test_unrelated.py")   # must NOT be pulled in

        result = env.run(["pkg/mod.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_mod_extra.py"), env.run_calls
        assert _has(args, "pkg/test_neighbor.py"), env.run_calls
        assert not _has(args, "tests/test_unrelated.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 2
        assert scopes[0]["n_total"] == 2

    def test_ac2_siblings_capped_at_50_sorted(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")
        names = ["pkg/test_c%02d.py" % i for i in range(60)]
        for n in names:
            env.write(n)

        result = env.run(["pkg/mod.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert len(args) == 50, "expected 50 capped sibling paths, got %d" % len(args)
        for n in names[:50]:
            assert _has(args, n), "sorted first-50 sibling missing: %s" % n
        assert not _has(args, names[50]), "sibling beyond the cap must be dropped"
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_sibling"] == 50
        assert scopes[0]["n_total"] == 50

    def test_ac3_empty_scope_skips_without_running_pytest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")  # no RED file, no manifest tests, no siblings

        result = env.run(["pkg/mod.py"])

        assert result.status == "ok"
        assert env.run_calls == [], "pytest must not be invoked on empty scope"
        skips = [e["payload"] for e in env.events if e["type"] == "post_fix_pytest_skipped"]
        assert len(skips) == 1 and skips[0].get("reason") == "no_test_scope", env.events
        assert env.scope_events() == [] or env.scope_events()[0]["n_total"] == 0

    def test_ac4_missing_red_path_not_passed_to_pytest(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")
        env.write("tests/test_present.py")
        # tests/test_gone.py is never created on disk

        result = env.run(
            ["pkg/mod.py"],
            red_test_paths=["tests/test_gone.py", "tests/test_present.py"],
        )

        assert result.status == "ok"
        args = env.test_args()
        assert not _has(args, "tests/test_gone.py"), env.run_calls
        assert _has(args, "tests/test_present.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 1

    def test_ac5_prev_data_red_paths_take_precedence_over_persisted(self, tmp_path, monkeypatch):
        env = _Env(tmp_path, monkeypatch)
        env.write("pkg/mod.py")
        env.write("tests/test_from_prev.py")
        env.write("tests/test_from_file.py")
        env.persist_red(["tests/test_from_file.py"])

        result = env.run(["pkg/mod.py"], red_test_paths=["tests/test_from_prev.py"])

        assert result.status == "ok"
        args = env.test_args()
        assert _has(args, "tests/test_from_prev.py"), env.run_calls
        assert not _has(args, "tests/test_from_file.py"), env.run_calls
        scopes = env.scope_events()
        assert len(scopes) == 1, env.events
        assert scopes[0]["n_red"] == 1
