"""RED tests for bd#315 -- token-consistency-lint leaves DECLARED_ABSENT.

Spec: bd315 frozen spec (sections 2 and 3), refs #315 (slice of #66, after
#304 and #305).

Pre-GREEN: the scanner reports five false positives on the existing specs
(file name vs package directory, case-only differences); the helpers
`_is_file_name` and `_is_drift` do not exist; `token-consistency-lint` is still
in `DECLARED_ABSENT` and has no `LINT_DRIVER_DIRS` entry, so the layer never
runs it and a staged spec with separator drift is accepted.

Anchors: the registry ACs read the module attributes the layer reads; the
layer ACs run the REAL `scripts/precommit_enforce_cli.py` (and the real
drivers it execs) in a throwaway git repo through a one-line `pre-commit`
hook with a hermetic git environment; a refusal is a real `git commit` side
effect, observed through the object database. The scanner ACs call the real
scanner and the real driver subprocess.

The guards (behavior that must not change, passing before and after GREEN)
live in the sibling file test_bd315_token_consistency_guards.py.

No path-list mutation, no conftest import; ASCII only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from bytedigger_engine import precommit_enforce, precommit_lints, token_consistency

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_CONFORMANCE = _PACKAGE_DIR / "conformance"
_DRIVER = _REPO_ROOT / "token-consistency-lint.py"

_TOKEN = "token-consistency-lint"
_CITE = "spec-cite-lint"
REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
LINT_OFF = "BD66-LINT-OFF"

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


def _lines_with(output: str, token: str, name: str) -> list[str]:
    return [ln for ln in output.splitlines() if token in ln and _lint_token(ln, name)]


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


# ------------------------------------------------------------------------ AC1


def test_ac1_the_five_existing_specs_have_zero_findings_and_the_driver_exits_0(tmp_path):
    env = _hermetic_env(tmp_path)
    for spec in _five_specs():
        text = spec.read_text(encoding="utf-8")
        assert token_consistency.scan_token_consistency(text) == [], (
            f"{spec.name}: the scanner must report no finding"
        )
        proc = _run_driver(["--spec", spec], tmp_path, env)
        assert proc.returncode == 0, f"{spec.name}: rc={proc.returncode} {proc.stdout!r}"
        assert proc.stdout == "", f"{spec.name}: stdout must be empty: {proc.stdout!r}"


# ------------------------------------------------------------------------ AC2


def test_ac2_r1_a_file_name_is_not_a_literal_token():
    is_file_name = token_consistency._is_file_name
    for token in ("engine.py", "foo.MD", "a/b.json", "notes.jsonl", "run.sh"):
        assert is_file_name(token) is True, token
    for token in ("engine_py", "emit.foo", "py", "emit.py_x"):
        assert is_file_name(token) is False, token

    # file name next to the package directory: two referents, no finding.
    spec = "The `engine.py` file lives in the `engine_py` package.\n"
    assert token_consistency.scan_token_consistency(spec) == []

    # the suffix must be the LAST segment: separator drift still reported.
    spec = "Use `emit.py_x` and `emit_py_x` consistently.\n"
    findings = token_consistency.scan_token_consistency(spec)
    assert [f["variants"] for f in findings] == [["emit.py_x", "emit_py_x"]], findings


# ------------------------------------------------------------------------ AC3


def test_ac3_r2_a_case_only_difference_is_not_drift():
    is_drift = token_consistency._is_drift
    assert is_drift({"TIMEOUT_S", "timeout_s"}) is False
    assert is_drift({"Audit_Case", "audit_case"}) is False
    assert is_drift({"emit_foo", "emit.foo"}) is True
    assert is_drift({"EMIT_FOO", "emit.foo"}) is True
    assert is_drift({"emit_foo"}) is False

    spec = "The constant `TIMEOUT_S` is read as `timeout_s`.\n"
    assert token_consistency.scan_token_consistency(spec) == []


# ------------------------------------------------------------------------ AC5


def test_ac5_registry_flip_declared_absent_is_spec_cite_only_and_token_is_mapped():
    assert precommit_lints.DECLARED_ABSENT == [_CITE], precommit_lints.DECLARED_ABSENT

    mapped = precommit_lints.LINT_DRIVER_DIRS.get(_TOKEN)
    assert mapped is not None and os.path.realpath(mapped) == os.path.realpath(str(_REPO_ROOT)), (
        f"LINT_DRIVER_DIRS[{_TOKEN!r}] must be the repo root, got {mapped!r}"
    )
    resolved = precommit_lints.driver_path(_TOKEN, "/nonexistent-lint-dir-bd315")
    assert os.path.realpath(resolved) == os.path.realpath(str(_DRIVER)), resolved
    assert os.path.isfile(resolved), f"resolved driver is not a file: {resolved!r}"

    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == [], f"the live registry must pre-pass clean: {violations!r}"
    assert present.get(_TOKEN) is True, present.get(_TOKEN)
    assert present.get(_CITE) is False, present.get(_CITE)


# ------------------------------------------------------------------------ AC6


def test_ac6_a_staged_spec_with_separator_drift_is_refused_and_its_twin_is_accepted(tmp_path):
    drift = _build(problem_extra=[_DRIFT_LINE])
    n = _line_of(drift, "emit_foo")
    rc, output, head = _scenario(tmp_path, "ac6drift", drift)
    assert rc != 0 and not head, f"must be refused: rc={rc} {output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, _TOKEN)) == 1, output
    assert re.search(
        rf"^TOKEN-INCONSISTENCY line {n}: emit\.foo vs emit_foo$", output, re.MULTILINE
    ), output

    rc, output, head = _scenario(tmp_path, "ac6twin", _build())
    assert rc == 0 and head, f"the same spec without the drift must commit: {output!r}"
    assert "BD66-REFUSE" not in output, output


# ------------------------------------------------------------------------ AC7


# ------------------------------------------------------------------------ AC8


def test_ac8_the_per_name_switch_turns_the_lint_off(tmp_path):
    drift = _build(problem_extra=[_DRIFT_LINE])
    rc, output, head = _scenario(tmp_path, "ac8", drift, {"BD_SPEC_LINTS_OFF": _TOKEN})
    assert rc == 0 and head, f"must be accepted with the lint off: {output!r}"
    assert re.findall(
        rf"^{LINT_OFF}: lint={re.escape(_TOKEN)} file=\S+\s*$", output, re.MULTILINE
    ), f"missing OFF line: {output!r}"
    assert not _lines_with(output, REFUSE_VIOLATION, _TOKEN), output
