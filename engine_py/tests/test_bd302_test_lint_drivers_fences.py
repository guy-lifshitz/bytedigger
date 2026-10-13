"""Fences for bd#302: behaviour that must hold before AND after the change.

Spec: docs/decisions/2026-10-13-bd302-test-lint-drivers.md, section 3 (F1..F3).
These pass on the base commit and must keep passing once the two test-lint
drivers exist; the RED file `test_bd302_test_lint_drivers.py` holds the tests
that fail today.

No sys.path mutation, no conftest import. Real CLI in a temporary git clone,
hermetic git environment, no network, no LLM.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from bytedigger_engine import precommit_lints

_REPO_ROOT = Path(precommit_lints.__file__).resolve().parent.parent.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"


def _hermetic_env(tmp_path: Path) -> dict:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    empty_global = fake_home / ".gitconfig"
    empty_global.write_text("")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("BD66_LINT_DIR", None)
    env["HOME"] = str(fake_home)
    env["GIT_CONFIG_GLOBAL"] = str(empty_global)
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


def _hooked_clone(tmp_path: Path, env: dict) -> Path:
    repo = (tmp_path / "clone").resolve()
    repo.mkdir(parents=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd302@example.com"),
        ("user.name", "bd302 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    assert _ENFORCE_CLI.is_file(), f"missing {_ENFORCE_CLI}"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_ENFORCE_CLI}"\n')
    hook.chmod(0o755)
    return repo


def _stage(repo: Path, env: dict, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")
    assert _git(["add", rel], repo, env).returncode == 0


def test_f1_clean_staged_test_commits_through_the_real_cli(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _hooked_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", "def test_ok():\n    assert 1 + 1 == 2\n")

    result = _git(["commit", "-m", "bd302 f1"], repo, env)

    assert result.returncode == 0, (
        f"a clean staged test must commit: {result.stdout!r} {result.stderr!r}"
    )
    assert _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def test_f2_one_sided_ts_test_is_still_refused_by_its_own_driver(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _hooked_clone(tmp_path, env)
    _stage(
        repo, env, "x.test.ts",
        'test("checks failure", () => {\n  expect(r.code).not.toBe(0);\n});\n',
    )

    result = _git(["commit", "-m", "bd302 f2"], repo, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"one-sided TS test must be refused: {output!r}"
    assert _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode != 0, (
        "no commit object may exist after the refusal"
    )
    lines = [
        ln for ln in output.splitlines()
        if "BD66-REFUSE-VIOLATION" in ln and "lint=one-sided-predicate-lint" in ln
    ]
    assert len(lines) == 1, f"expected one refusal line naming the lint: {output!r}"


def test_f3_older_mappings_and_registry_lists_are_unchanged():
    mapping = precommit_lints.LINT_DRIVER_DIRS
    root = os.path.realpath(str(_REPO_ROOT))
    for name in ("cyrillic-prose-lint", "one-sided-predicate-lint"):
        assert name in mapping, f"{name} must stay in LINT_DRIVER_DIRS"
        assert os.path.realpath(mapping[name]) == root

    lists = sorted(a for a in dir(precommit_lints) if a.endswith("_LINTS"))
    assert lists == ["SPEC_LINTS", "TEST_LINTS", "TEXT_LINTS", "TS_TEST_LINTS"], lists
    assert precommit_lints.SPEC_LINTS == [
        "scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint",
        "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint",
        "format-conversion-lint", "closure-evidence-lint",
        "mutation-two-sidedness-lint",
    ]
    assert precommit_lints.TEST_LINTS == [
        "forbidden-import-lint", "stub-passability-lint", "empty-input-assert-lint",
    ]
    assert precommit_lints.TS_TEST_LINTS == ["one-sided-predicate-lint"]
    assert precommit_lints.TEXT_LINTS == ["cyrillic-prose-lint"]
