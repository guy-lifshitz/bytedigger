"""RED tests for bd#249 - an empty post-fix test scope is an error, not a pass.

Spec: docs/decisions/2026-10-10-bd249-postfix-empty-scope.md (r2).  This file
holds AC1, AC2, AC3, AC5, AC5b, AC5c, AC7 (empty-scope legs) and AC8; every
test here fails before the change.  AC4, AC6 and AC7 (non-empty-scope legs)
and the flag_owner_lint half of AC8 are passing pins in
test_bd249_postfix_scope_guards.py.

When the post-fix scope builder returns nothing, _run_pytest_post_fix used to
skip and return ok.  Behind HAL_POST_FIX_EMPTY_SCOPE_ENFORCE it now:
  unset / "0" / any value but "1": shadow - same skip + ok, plus one
               post_fix_empty_scope event (mode="shadow");
  "1":         enforce - post_fix_empty_scope (mode="enforce"), no skip event,
               status="error", error_code="E_POST_FIX_EMPTY_SCOPE",
               recoverable=False, pytest never invoked;
  docs-only manifest (non-empty, no .py): skip + ok, event mode="exempt",
               in either flag state.

Hermetic: tmp_path, a real tiny git repo with real commits, run_test_command
(the pytest process boundary) replaced by a recording sentinel, _emit_safe
captured.  The scope builder and the step itself are the real code.
No sys.path mutation.
"""
from __future__ import annotations

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
_CODE = "E_POST_FIX_EMPTY_SCOPE"
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


def _empty_scope_env(tmp_path, monkeypatch):
    """Fix commit touches only pkg/module.py; no RED file, manifest tests or siblings."""
    env = _Env(tmp_path, monkeypatch)
    env.base_commit()
    env.fix_commit()
    return env


def _assert_payload(env, mode, n_manifest_files=1, n_manifest_py=1):
    evs = env.of(_EVENT)
    assert len(evs) == 1, env.events
    p = evs[0]
    assert p["mode"] == mode, p
    assert p["phase"] == 6 and p["step"] == "run_pytest_post_fix", p
    for key in ("n_red", "n_manifest", "n_sibling", "n_total"):
        assert p[key] == 0, p
    assert p["n_manifest_files"] == n_manifest_files, p
    assert p["n_manifest_py"] == n_manifest_py, p


def _assert_enforced(env, result, **counts):
    assert result.status == "error", result
    assert result.error_code == _CODE, result
    assert result.recoverable is False
    assert result.data is None
    assert result.error, result
    _assert_payload(env, "enforce", **counts)
    assert env.of("post_fix_pytest_skipped") == [], env.events
    assert env.run_calls == [], "pytest must not be invoked on empty scope"


def _assert_skipped_ok(env, result, mode, **counts):
    assert result.status == "ok", result
    skips = env.of("post_fix_pytest_skipped")
    assert len(skips) == 1 and skips[0].get("reason") == "no_test_scope", env.events
    _assert_payload(env, mode, **counts)
    assert env.run_calls == [], "pytest must not be invoked on empty scope"


def _assert_shadow(env, result, **counts):
    _assert_skipped_ok(env, result, "shadow", **counts)


class TestPostFixEmptyScope:

    def test_ac1_enforce_empty_scope_is_error(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv(_FLAG, "1")

        result = env.run()

        _assert_enforced(env, result)

    def test_ac2_shadow_unset_keeps_skip_and_emits_event(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)

        result = env.run()

        _assert_shadow(env, result)

    def test_ac3_kill_switch_zero_is_shadow(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv(_FLAG, "0")

        result = env.run()

        _assert_shadow(env, result)

    def test_ac3_bd_alias_enforces_when_hal_unset(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv("BD_" + _FLAG_SUFFIX, "1")

        result = env.run()

        _assert_enforced(env, result)

    def test_ac3_hal_value_wins_over_alias(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv(_FLAG, "0")
        monkeypatch.setenv("BD_" + _FLAG_SUFFIX, "1")

        result = env.run()

        _assert_shadow(env, result)

    def test_ac5_missing_frozen_test_is_empty_scope(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        env.persist_red(["tests/test_gone.py"])  # never created on disk
        monkeypatch.setenv(_FLAG, "1")

        result = env.run()

        _assert_enforced(env, result)

    # ---- AC5b: docs-only exemption -------------------------------------------

    @pytest.mark.parametrize("flag", ["1", None])
    def test_ac5b_docs_only_manifest_is_exempt(self, tmp_path, monkeypatch, flag):
        env = _empty_scope_env(tmp_path, monkeypatch)
        if flag is not None:
            monkeypatch.setenv(_FLAG, flag)

        result = env.run(manifest=("docs/notes.md",))

        _assert_skipped_ok(env, result, "exempt", n_manifest_files=1, n_manifest_py=0)

    def test_ac5b_empty_manifest_not_exempt_enforce(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv(_FLAG, "1")

        result = env.run(manifest=())

        _assert_enforced(env, result, n_manifest_files=0, n_manifest_py=0)

    def test_ac5b_empty_manifest_not_exempt_shadow(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)

        result = env.run(manifest=())

        _assert_shadow(env, result, n_manifest_files=0, n_manifest_py=0)

    # ---- AC5c: flag values ---------------------------------------------------

    def test_ac5c_true_is_shadow_not_enforcing(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv(_FLAG, "true")

        result = env.run()

        _assert_shadow(env, result)

    def test_ac5c_bytedigger_alias_enforces(self, tmp_path, monkeypatch):
        env = _empty_scope_env(tmp_path, monkeypatch)
        monkeypatch.setenv("BYTEDIGGER_" + _FLAG_SUFFIX, "1")

        result = env.run()

        _assert_enforced(env, result)

    # ---- AC7: auth mode does not matter --------------------------------------

    @pytest.mark.parametrize("scenario", ["enforce_empty", "shadow_empty"])
    @pytest.mark.parametrize("auth", ["api_key", "oauth"])
    def test_ac7_outcome_independent_of_auth_mode(self, tmp_path, monkeypatch, scenario, auth):
        if auth == "api_key":
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
            monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        else:
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-not-real")

        env = _empty_scope_env(tmp_path, monkeypatch)
        if scenario == "enforce_empty":
            monkeypatch.setenv(_FLAG, "1")
            _assert_enforced(env, env.run())
        else:
            _assert_shadow(env, env.run())

    # ---- AC8: registry and docs ----------------------------------------------

    def test_ac8_flag_registered_with_owner_and_provenance(self):
        from bytedigger_engine.flags_catalog import FLAGS
        assert _FLAG in FLAGS, "%s missing from flags_catalog.FLAGS" % _FLAG
        entry = FLAGS[_FLAG]
        assert entry.get("kind") == "flag", entry
        assert entry.get("module") == "workflows/phase_6_review.py", entry
        assert entry.get("default") == "0", entry
        assert str(entry.get("owner", "")).strip(), entry
        assert str(entry.get("provenance", "")).startswith("introduced:"), entry

    def test_ac8_events_doc_has_empty_scope_row(self):
        text = (_REPO_ROOT / "docs" / "events.md").read_text(encoding="utf-8")
        assert _EVENT in text, "docs/events.md must document %s" % _EVENT

    def test_ac8_error_code_registered_and_documented(self):
        from bytedigger_engine.error_codes import ERROR_CODES
        assert _CODE in ERROR_CODES, "%s missing from error_codes.ERROR_CODES" % _CODE
        for rel in ("engine_py/ERROR_CODES.md", "engine_py/bytedigger_engine/ERROR_CODES.md"):
            text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            assert _CODE in text, "%s must list %s" % (rel, _CODE)
        proc = subprocess.run(
            [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
            cwd=str(_REPO_ROOT / "engine_py"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
            env={k: v for k, v in os.environ.items()
                 if k not in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")},
        )
        assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
