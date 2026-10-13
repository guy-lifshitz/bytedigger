"""Fences for bd#305: behaviour that must hold before AND after the change.

Spec: docs/decisions/2026-10-13-bd305-spec-lint-feed.md, section 3 (F1..F3).
These pass on the base commit and must keep passing once the layer feeds the
lints; the RED file `test_bd305_spec_lint_feed.py` holds the tests that fail
today.

No sys.path mutation, no conftest import. Real CLI in a temporary git clone,
hermetic git environment, no network, no LLM. ASCII only. The forbidden-import
fixture assembles the banned NAME token from pieces so this file carries none.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from bytedigger_engine import precommit_lints

_REPO_ROOT = Path(precommit_lints.__file__).resolve().parent.parent.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"

_SPEC_TOKEN = "spec_from" + "_file_location"


def _hermetic_env(tmp_path: Path) -> dict:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    empty_global = fake_home / ".gitconfig"
    empty_global.write_text("")
    env = dict(os.environ)
    for key in ("PYTHONPATH", "BD66_LINT_DIR", "BD_SPEC_LINTS_OFF", "BD_LINT_CRASH_IS_VIOLATION"):
        env.pop(key, None)
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
        ("user.email", "bd305@example.com"),
        ("user.name", "bd305 tester"),
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


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _scope_inverse_violating_spec() -> str:
    # A Files-in-scope header and no 'not in scope' block: scope-inverse only.
    return "\n".join([
        "# Widget spec",
        "",
        "## 1. Problem",
        "",
        "The widget parser returns a value.",
        "The `build_widget` function is defined in src/widget.py.",
        "",
        "## 2. Change",
        "",
        "1. **Parser entry** reads the input.",
        "",
        "- CREATE: src/widget.py",
        "",
        "### Files in scope",
        "",
        "- src/widget.py: the parser.",
        "",
        "## 3. Acceptance",
        "",
        "| AC | Check |",
        "| --- | --- |",
        "| AC1 | Parser entry returns the value. |",
        "",
    ]) + "\n"


def test_f1_clean_test_commits_and_the_forbidden_dynamic_import_is_refused(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _hooked_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    result = _git(["commit", "-m", "bd305 f1 clean"], repo, env)
    assert result.returncode == 0, (
        f"a clean staged test must commit: {result.stdout!r} {result.stderr!r}"
    )
    assert _has_head(repo, env)

    source = "\n".join([
        "import importlib.util",
        "",
        "",
        "def test_loads_a_module():",
        f"    spec = importlib.util.{_SPEC_TOKEN}('m', 'm.py')",
        "    assert spec is not None",
        "",
    ])
    _stage(repo, env, "test_x.py", source)
    result = _git(["commit", "-m", "bd305 f1 dirty"], repo, env)
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"forbidden dynamic import must be refused: {output!r}"
    lines = [
        ln for ln in output.splitlines()
        if "BD66-REFUSE-VIOLATION" in ln and "lint=forbidden-import-lint" in ln
    ]
    assert len(lines) == 1, f"expected one refusal line naming the lint: {output!r}"


def test_f2_registry_lists_names_order_and_the_other_ten_mappings_are_unchanged():
    lists = sorted(a for a in dir(precommit_lints) if a.endswith("_LINTS"))
    assert lists == ["SPEC_LINTS", "TEST_LINTS", "TEXT_LINTS", "TS_TEST_LINTS"], lists
    assert precommit_lints.SPEC_LINTS == [
        "scope-inverse-lint", "helper-extraction-lint", "spec-cite-lint",
        "spec-coverage-lint", "token-consistency-lint", "presence-triad-lint",
        "format-conversion-lint", "closure-evidence-lint",
        "mutation-two-sidedness-lint",
    ]
    assert precommit_lints.TEST_LINTS == ["forbidden-import-lint", "stub-passability-lint"]
    assert precommit_lints.TS_TEST_LINTS == ["one-sided-predicate-lint"]
    assert precommit_lints.TEXT_LINTS == ["cyrillic-prose-lint"]

    root = os.path.realpath(str(_REPO_ROOT))
    mapping = precommit_lints.LINT_DRIVER_DIRS
    for name in (
        "cyrillic-prose-lint", "one-sided-predicate-lint",
        "forbidden-import-lint", "stub-passability-lint",
        "scope-inverse-lint", "helper-extraction-lint", "presence-triad-lint",
        "format-conversion-lint", "closure-evidence-lint",
        "mutation-two-sidedness-lint",
    ):
        assert name in mapping, f"{name} must stay in LINT_DRIVER_DIRS"
        assert os.path.realpath(mapping[name]) == root, f"{name}: {mapping[name]!r}"


def test_f3_a_scope_inverse_violating_spec_is_still_refused_with_no_switch_set(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _hooked_clone(tmp_path, env)
    _stage(repo, env, "x_spec.md", _scope_inverse_violating_spec())

    result = _git(["commit", "-m", "bd305 f3"], repo, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, f"scope-inverse violation must be refused: {output!r}"
    assert not _has_head(repo, env), "no commit object may exist after the refusal"
    lines = [
        ln for ln in output.splitlines()
        if "BD66-REFUSE-VIOLATION" in ln and "lint=scope-inverse-lint" in ln
    ]
    assert len(lines) == 1, f"expected one refusal line naming the lint: {output!r}"


# F4 (was AC7 of the frozen spec: it passes before the change, so it is a pin, not a RED)

def test_f4_the_eight_drivers_exit_2_on_an_undecodable_or_directory_spec(tmp_path):
    repo_root = Path(precommit_lints.__file__).resolve().parents[2]
    names = [
        "scope-inverse-lint", "helper-extraction-lint", "presence-triad-lint",
        "format-conversion-lint", "closure-evidence-lint", "mutation-two-sidedness-lint",
        "spec-coverage-lint", "token-consistency-lint",
    ]
    env = _hermetic_env(tmp_path)
    undecodable = tmp_path / "undecodable_spec.md"
    undecodable.write_bytes(b"\xff\xfe\x00")
    directory = tmp_path / "a_directory_spec.md"
    directory.mkdir()
    for name in names:
        driver = repo_root / f"{name}.py"
        assert driver.is_file(), f"driver {driver} does not exist"
        for target in (undecodable, directory):
            proc = subprocess.run(
                [sys.executable, "-I", str(driver), "--spec", str(target)],
                cwd=tmp_path, env=env, capture_output=True, text=True,
            )
            assert proc.returncode == 2, (
                f"{name} on {target.name}: rc={proc.returncode} {proc.stdout!r} {proc.stderr!r}"
            )
            assert proc.stdout == "", f"{name} on {target.name}: stdout {proc.stdout!r}"
            assert str(target) in proc.stderr, f"{name}: stderr lacks the path: {proc.stderr!r}"
