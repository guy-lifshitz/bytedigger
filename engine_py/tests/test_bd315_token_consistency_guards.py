"""Guards for bd#315 -- behavior that must not change; pass before and after GREEN.

Sibling of test_bd315_token_consistency_flip.py (the RED flip tests). These
three tests already pass on the pre-GREEN code and protect post-GREEN
behavior: separator drift is still reported unchanged, each existing spec is
still accepted by the layer, and spec-cite-lint stays held.

No path-list mutation, no conftest import; ASCII only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bytedigger_engine import precommit_lints, token_consistency

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_CONFORMANCE = _PACKAGE_DIR / "conformance"
_DRIVER = _REPO_ROOT / "token-consistency-lint.py"

_CITE = "spec-cite-lint"

_EXISTING_SPECS = [
    "AUTHORSHIP_SPEC.md", "CONTRACTS_SPEC.md", "EMISSIONS_SPEC.md",
    "ORACLE_SPEC.md", "QUANT_LINT_SPEC.md",
]
_NAME_CHAR = r"[0-9A-Za-z_\-]"
_DRIFT_LINE = "Use `emit_foo` and `emit.foo` consistently."


# --------------------------------------------------------------------- helpers


def _lint_token(output: str, name: str) -> bool:
    pattern = rf"(?<!{_NAME_CHAR})lint={re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


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


def _real_clone(tmp_path: Path, env: dict, name: str) -> Path:
    assert _ENFORCE_CLI.is_file(), f"missing {_ENFORCE_CLI}"
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd315@example.com"),
        ("user.name", "bd315 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_ENFORCE_CLI}"\n')
    hook.chmod(0o755)
    return repo


def _stage(repo: Path, env: dict, rel: str, text: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    assert _git(["add", rel], repo, env).returncode == 0


def _commit(repo: Path, env: dict) -> tuple[int, str]:
    result = _git(["commit", "-m", "bd315 fixture"], repo, env)
    return result.returncode, result.stdout + result.stderr


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _line_of(source: str, needle: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture has no line containing {needle!r}")


def _run_driver(args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_DRIVER), *[str(a) for a in args]],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


def _build(*, problem_extra=()) -> str:
    """Base spec, clean for every spec lint the layer runs (same base as the
    bd#304/bd#305 tests), with optional extra lines in the problem section."""
    lines = [
        "# Widget spec",
        "",
        "## 1. Problem",
        "",
        "The widget parser returns a value.",
        "The `build_widget` function is defined in src/widget.py.",
        *problem_extra,
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
        "### Files NOT in scope",
        "",
        "- every other file.",
        "",
        "## 3. Acceptance",
        "",
        "| AC | Check |",
        "| --- | --- |",
        "| AC1 | Parser entry returns the value. |",
        "",
    ]
    return "\n".join(lines) + "\n"


def _scenario(tmp_path: Path, tag: str, spec_text: str,
              extra_env: dict | None = None) -> tuple[int, str, bool]:
    env = _hermetic_env(tmp_path)
    env.update(extra_env or {})
    repo = _real_clone(tmp_path, env, tag)
    _stage(repo, env, "x_spec.md", spec_text)
    rc, output = _commit(repo, env)
    return rc, output, _has_head(repo, env)


def _five_specs() -> list[Path]:
    specs = sorted(_CONFORMANCE.glob("*_SPEC.md"))
    assert [p.name for p in specs] == _EXISTING_SPECS, f"existing specs changed: {specs!r}"
    return specs


# ------------------------------------------------------------------------ AC4


def test_ac4_separator_drift_is_reported_unchanged(tmp_path):
    env = _hermetic_env(tmp_path)
    spec_text = _build(problem_extra=[_DRIFT_LINE])
    n = _line_of(spec_text, "emit_foo")
    (tmp_path / "drift_spec.md").write_text(spec_text, encoding="utf-8")

    findings = token_consistency.scan_token_consistency(spec_text)
    assert findings == [{
        "line": n, "rule_id": "token-inconsistency",
        "variants": ["emit.foo", "emit_foo"], "evidence": "emit.foo vs emit_foo",
    }]

    proc = _run_driver(["--spec", "drift_spec.md"], tmp_path, env)
    assert proc.returncode == 1, f"rc={proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert proc.stdout.splitlines() == [f"TOKEN-INCONSISTENCY line {n}: emit.foo vs emit_foo"]

    as_json = _run_driver(["--spec", "drift_spec.md", "--json"], tmp_path, env)
    assert as_json.returncode == 1, as_json.stderr
    assert json.loads(as_json.stdout) == {"findings": findings}

    three = "A `emit_foo` and `emit.foo` and `Emit-Foo` appear here.\n"
    cluster = token_consistency.scan_token_consistency(three)
    assert [f["variants"] for f in cluster] == [["Emit-Foo", "emit.foo", "emit_foo"]], cluster


def test_file_name_skip_does_not_remove_the_cluster():
    text = "Names `emit.py`, `emit_py` and `emit-py` appear here.\n"
    findings = token_consistency.scan_token_consistency(text)
    assert [f["variants"] for f in findings] == [["emit-py", "emit_py"]], findings


def test_file_name_suffix_set_is_exact():
    for suffix in ("py", "md", "json", "jsonl", "ts", "js", "sh", "yaml", "yml", "toml", "txt"):
        assert token_consistency._is_file_name(f"x.{suffix}") is True, suffix
        assert token_consistency._is_file_name(f"x.{suffix.upper()}") is True, suffix
    assert token_consistency._is_file_name("x.rs") is False
    assert token_consistency._is_file_name("x.pyc") is False


# ------------------------------------------------------------------------ AC7


def test_ac7_each_existing_spec_is_accepted_by_the_layer(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env, "ac7")
    for spec in _five_specs():
        _stage(repo, env, spec.name, spec.read_text(encoding="utf-8"))
        rc, output = _commit(repo, env)
        # bd#323: a flat clone has no `__init__.py` chain, so the cite lint (no union
        # there) may refuse; its cleanliness is pinned by bd#323 AC1. Every other
        # lint must still accept each spec.
        refusals = [
            ln for ln in output.splitlines() if "BD66-REFUSE-VIOLATION" in ln and spec.name in ln
        ]
        assert all("lint=spec-cite-lint " in ln for ln in refusals), f"{spec.name}: {output!r}"
        assert rc == 0 or refusals, f"{spec.name} must be accepted by the layer: {output!r}"
        if rc != 0:
            _git(["reset", "-q"], repo, env)


# ------------------------------------------------------------------------ AC9


def test_ac9_spec_cite_is_live(tmp_path):
    assert _CITE not in precommit_lints.DECLARED_ABSENT, precommit_lints.DECLARED_ABSENT
    assert (_REPO_ROOT / f"{_CITE}.py").is_file(), "spec-cite driver must exist at the repo root"

    rc, output, head = _scenario(tmp_path, "ac9", _build())
    assert rc == 0 and head, f"a clean staged spec must commit: {output!r}"
    assert "BD66-REFUSE" not in output, f"a clean spec must not be refused: {output!r}"
