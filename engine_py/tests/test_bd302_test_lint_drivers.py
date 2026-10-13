"""RED tests for bd#302 -- port the forbidden-import and stub-passability lint drivers.

Spec: docs/decisions/2026-10-13-bd302-test-lint-drivers.md

Pre-GREEN, `forbidden-import-lint` and `stub-passability-lint` are listed in
`precommit_lints.DECLARED_ABSENT` and have no driver on disk, so the #78 layer
licenses their absence and a staged test with a forbidden dynamic import or a
mock of its own unit under test is committed with no verdict. Every test here
fails today for that reason: the drivers `forbidden-import-lint.py` and
`stub-passability-lint.py` at the repository root do not exist and the names are
still declared absent.

Anchors (section 3 of the spec): the real-CLI ACs run the REAL
`scripts/precommit_enforce_cli.py` (and the real drivers it execs) in a
temporary git clone, through a one-line `pre-commit` hook, with a hermetic git
environment. The load-bearing observation is the git object database: after a
refused commit `git rev-parse --verify HEAD` must fail. Every refusal is paired
with the acceptance of a clean twin, so an always-refuse stub dies on the twin
and an always-accept stub dies on the refusal.

Fixture source text for the two forbidden NAME tokens is assembled from pieces,
so this file itself carries no such token.

No sys.path mutation, no conftest import; the unit under test is reached by
subprocess or by an in-test-body import.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import precommit_lints

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"

_FI_NAME = "forbidden-import-lint"
_SP_NAME = "stub-passability-lint"
_FI_DRIVER = _REPO_ROOT / f"{_FI_NAME}.py"
_SP_DRIVER = _REPO_ROOT / f"{_SP_NAME}.py"

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"

# The two forbidden NAME tokens, assembled so this file carries neither.
_SPEC_TOKEN = "spec_from" + "_file_location"
_EXEC_TOKEN = "exec" + "_module"

_NAME_CHAR = r"[0-9A-Za-z_\-]"


# --------------------------------------------------------------------- helpers


def _names_lint(output: str, name: str) -> bool:
    """True iff `name` appears in `output` as a whole lint-name token."""
    pattern = rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


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


def _init_repo(tmp_path: Path, env: dict, name: str) -> Path:
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd302@example.com"),
        ("user.name", "bd302 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    return repo


def _install_hook(repo: Path, cli: Path) -> None:
    """A one-line pre-commit hook that execs the CLI by absolute path."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{cli}"\n')
    hook.chmod(0o755)


def _real_clone(tmp_path: Path, env: dict, name: str = "clone") -> Path:
    """Temporary clone whose hook execs the REAL CLI of this repository."""
    assert _ENFORCE_CLI.is_file(), f"missing {_ENFORCE_CLI}"
    repo = _init_repo(tmp_path, env, name)
    _install_hook(repo, _ENFORCE_CLI)
    return repo


def _stage(repo: Path, env: dict, rel: str, text: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    assert _git(["add", rel], repo, env).returncode == 0


def _commit(repo: Path, env: dict) -> tuple[int, str]:
    result = _git(["commit", "-m", "bd302 fixture"], repo, env)
    return result.returncode, result.stdout + result.stderr


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _line_of(source: str, needle: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture has no line containing {needle!r}")


def _exit_stand_in(directory: Path, name: str, code: int = 0) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.exit({code})\n")
    path.chmod(0o755)


def _package_copy(tmp_path: Path, env: dict, name: str) -> Path:
    """A clone carrying a COPY of the registry, the layer and the real drivers.

    The copied registry recomputes its repo root as the clone, so a name mapped
    in `LINT_DRIVER_DIRS` is looked for at `<clone>/<name>.py`. The two drivers
    under test are copied from the real tree (never stand-ins); the two older
    drivers are exit-0 stand-ins, they are not what this lot is about. The
    clone's hook execs the CLI copy, so the refusal is a real `git commit`.
    """
    for driver in (_FI_DRIVER, _SP_DRIVER):
        assert driver.is_file(), f"bd#302: the driver {driver} does not exist yet"
    clone = _init_repo(tmp_path, env, name)
    pkg = clone / "engine_py" / "bytedigger_engine"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for module in (
        "precommit_lints", "precommit_enforce", "green_entry_guard",
        "verdict_verify", "forbidden_import", "stub_passability",
    ):
        shutil.copy2(_PACKAGE_DIR / f"{module}.py", pkg / f"{module}.py")
    scripts = clone / "scripts"
    scripts.mkdir()
    shutil.copy2(_ENFORCE_CLI, scripts / "precommit_enforce_cli.py")
    shutil.copy2(_FI_DRIVER, clone / _FI_DRIVER.name)
    shutil.copy2(_SP_DRIVER, clone / _SP_DRIVER.name)
    for older in ("cyrillic-prose-lint", "one-sided-predicate-lint"):
        _exit_stand_in(clone, older, 0)
    _install_hook(clone, scripts / "precommit_enforce_cli.py")
    return clone


def _run_driver(driver: Path, args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(driver), *[str(a) for a in args]],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


# --------------------------------------------------------------- fixture source


def _fi_dirty_source() -> str:
    return "\n".join([
        "import importlib.util",
        "",
        "",
        "def test_control():",
        "    assert 1 + 1 == 2",
        "",
        "",
        "def test_loads_a_module():",
        f"    spec = importlib.util.{_SPEC_TOKEN}('m', 'm.py')",
        "    assert spec is not None",
        "",
    ])


def _fi_clean_twin_source() -> str:
    """The same two tokens, only inside a string literal and a comment."""
    return "\n".join([
        "def test_control():",
        "    assert 1 + 1 == 2",
        "",
        "",
        "def test_mentions_the_names_as_text():",
        f"    text = 'the helpers {_SPEC_TOKEN} and {_EXEC_TOKEN} are banned'",
        "    assert text",
        "",
        f"# note: {_SPEC_TOKEN} appears only in this comment",
        "",
    ])


def _sp_dirty_source() -> str:
    return "\n".join([
        "from unittest import mock",
        "from mymod import my_func",
        "",
        "",
        "def test_control():",
        "    assert 1 + 1 == 2",
        "",
        "",
        "def test_it():",
        "    with mock.patch('mymod.my_func'):",
        "        assert my_func() is None",
        "",
    ])


def _sp_clean_twin_source() -> str:
    """Patches a symbol that is not imported from the unit under test."""
    return "\n".join([
        "import os",
        "from unittest import mock",
        "from mymod import my_func",
        "",
        "",
        "def test_control():",
        "    assert 1 + 1 == 2",
        "",
        "",
        "def test_it():",
        "    with mock.patch('os.getcwd'):",
        "        assert my_func() is None",
        "",
    ])


# ------------------------------------------------------------------------ AC1


def test_ac1_forbidden_dynamic_import_in_staged_test_is_refused_by_a_real_commit(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    source = _fi_dirty_source()
    bad_line = _line_of(source, _SPEC_TOKEN)
    _stage(repo, env, "test_x.py", source)

    rc, output = _commit(repo, env)

    assert rc != 0, f"the commit must be refused, got rc=0; output={output!r}"
    assert not _has_head(repo, env), (
        f"no commit object may exist after a refusal; output={output!r}"
    )
    assert REFUSE_VIOLATION in output, f"missing {REFUSE_VIOLATION}: {output!r}"
    assert _names_lint(output, _FI_NAME), f"lint name not a whole token: {output!r}"
    expected = f"FORBIDDEN-IMPORT test_x.py:{bad_line}:"
    assert expected in output, f"driver line {expected!r} missing: {output!r}"

    # Clean twin in the same repo: the same tokens as text only is accepted.
    _stage(repo, env, "test_x.py", _fi_clean_twin_source())
    rc_twin, out_twin = _commit(repo, env)
    assert rc_twin == 0, f"the clean twin must be accepted; output={out_twin!r}"
    assert _has_head(repo, env), "the clean twin commit must exist"


# ------------------------------------------------------------------------ AC2


def test_ac2_mocking_the_unit_under_test_is_refused_by_a_real_commit(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    source = _sp_dirty_source()
    patch_line = _line_of(source, "mock.patch(")
    _stage(repo, env, "test_x.py", source)

    rc, output = _commit(repo, env)

    assert rc != 0, f"the commit must be refused, got rc=0; output={output!r}"
    assert not _has_head(repo, env), (
        f"no commit object may exist after a refusal; output={output!r}"
    )
    assert REFUSE_VIOLATION in output, f"missing {REFUSE_VIOLATION}: {output!r}"
    assert _names_lint(output, _SP_NAME), f"lint name not a whole token: {output!r}"
    expected = f"STUB-PASSABLE test_x.py:{patch_line} mocks UUT 'my_func'"
    assert expected in output, f"driver line {expected!r} missing: {output!r}"

    # Clean twin: patching a symbol that is not imported from the UUT passes.
    _stage(repo, env, "test_x.py", _sp_clean_twin_source())
    rc_twin, out_twin = _commit(repo, env)
    assert rc_twin == 0, f"the clean twin must be accepted; output={out_twin!r}"
    assert _has_head(repo, env), "the clean twin commit must exist"


# ------------------------------------------------------------------------ AC3


def test_ac3_clean_staged_test_commits_and_both_lints_are_reported_as_run(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", "def test_ok():\n    assert 1 + 1 == 2\n")

    rc, output = _commit(repo, env)
    assert rc == 0, f"a clean staged test must commit; output={output!r}"
    assert _has_head(repo, env)

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    present, violations = precommit_enforce.registry_prepass(
        precommit_lints.DEFAULT_LINT_DIR
    )
    assert violations == [], f"the live registry must pre-pass clean: {violations!r}"
    for name in (_FI_NAME, _SP_NAME):
        assert present.get(name) is True, (
            f"{name} must be marked present by the pre-pass, got {present.get(name)!r}"
        )

    commands = precommit_lints.build_lint_commands(
        [], ["test_x.py"], precommit_lints.DEFAULT_LINT_DIR
    )
    by_lint = {c["lint"]: c for c in commands}
    for name in (_FI_NAME, _SP_NAME):
        assert name in by_lint, f"no planned command for {name}: {commands!r}"
        driver = Path(by_lint[name]["argv"][0])
        assert driver.is_file(), f"argv[0] for {name} must exist, got {driver}"
        assert os.access(driver, os.X_OK), f"argv[0] for {name} must be executable"


# ------------------------------------------------------------------------ AC4


def test_ac4_declared_absent_is_the_nine_spec_lints_and_the_registry_stays_closed(tmp_path):
    assert set(precommit_lints.DECLARED_ABSENT) == set(precommit_lints.SPEC_LINTS), (
        f"DECLARED_ABSENT must be exactly SPEC_LINTS, got "
        f"{precommit_lints.DECLARED_ABSENT!r}"
    )
    for name in (_FI_NAME, _SP_NAME):
        assert name not in precommit_lints.DECLARED_ABSENT, f"{name} still declared absent"
    denominator = (
        list(precommit_lints.SPEC_LINTS)
        + list(precommit_lints.TEST_LINTS)
        + list(precommit_lints.TS_TEST_LINTS)
    )
    assert len(denominator) == 12, f"registry denominator must stay 12: {denominator!r}"

    # Closed the other way: driver gone and not declared -> MISSING-DRIVER.
    env = _hermetic_env(tmp_path)
    clone = _package_copy(tmp_path, env, "closed-copy")
    (clone / _FI_DRIVER.name).unlink()
    cli = clone / "scripts" / "precommit_enforce_cli.py"
    proc = subprocess.run(
        [sys.executable, str(cli)], cwd=str(clone), env=env,
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"expected rc=1, got {proc.returncode}: {output!r}"
    assert REFUSE_MISSING_DRIVER in output, f"missing token: {output!r}"
    assert _names_lint(output, _FI_NAME), f"lint not named as a whole token: {output!r}"


# ------------------------------------------------------------------------ AC5


@pytest.mark.parametrize("name", [_FI_NAME, _SP_NAME])
def test_ac5_driver_placement_shebang_and_modes(name):
    root = os.path.realpath(str(_REPO_ROOT))
    mapped = precommit_lints.LINT_DRIVER_DIRS.get(name)
    assert mapped is not None and os.path.realpath(mapped) == root, (
        f"LINT_DRIVER_DIRS[{name!r}] must be the repo root, got {mapped!r}"
    )
    resolved = precommit_lints.driver_path(name, "/nonexistent-lint-dir-bd302")
    assert os.path.realpath(resolved) == os.path.join(root, f"{name}.py"), (
        f"driver_path must ignore the passed dir for a mapped name, got {resolved!r}"
    )
    path = Path(resolved)
    assert path.is_file(), f"driver file missing: {path}"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/env python3", f"first line must be the shebang: {first!r}"
    assert os.stat(path).st_mode & 0o111, "working tree exec bit must be set"

    if not (_REPO_ROOT / ".git").exists():
        pytest.skip(
            "bd#302 AC5: this corpus is a `git archive` tree with no .git, so the "
            "versioned index mode cannot be read; shebang and the working-tree "
            "exec bit were asserted above."
        )
    listed = subprocess.run(
        ["git", "ls-files", "-s", f"{name}.py"], cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr!r}"
    assert listed.stdout.startswith("100755 "), (
        f"driver must be versioned 100755, got {listed.stdout!r}"
    )


# ------------------------------------------------------------------------ AC6


def test_ac6_forbidden_import_driver_contract(tmp_path):
    assert _FI_DRIVER.is_file(), f"driver {_FI_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    source = _fi_dirty_source()
    bad_line = _line_of(source, _SPEC_TOKEN)
    (tmp_path / "test_dirty.py").write_text(source, encoding="utf-8")
    (tmp_path / "test_clean.py").write_text(_fi_clean_twin_source(), encoding="utf-8")
    (tmp_path / "helper.py").write_text(source, encoding="utf-8")

    clean = _run_driver(_FI_DRIVER, ["test_clean.py"], tmp_path, env)
    assert clean.returncode == 0, f"clean must exit 0: {clean.stdout!r} {clean.stderr!r}"

    dirty = _run_driver(_FI_DRIVER, ["test_dirty.py"], tmp_path, env)
    assert dirty.returncode == 1, f"dirty must exit 1: {dirty.stdout!r} {dirty.stderr!r}"
    text_line = (
        f"FORBIDDEN-IMPORT test_dirty.py:{bad_line}: {_SPEC_TOKEN} - "
        f"spec = importlib.util.{_SPEC_TOKEN}('m', 'm.py')"
    )
    assert text_line in dirty.stdout.splitlines(), (
        f"expected line {text_line!r}, got {dirty.stdout!r}"
    )

    as_json = _run_driver(_FI_DRIVER, ["test_dirty.py", "--json"], tmp_path, env)
    assert as_json.returncode == 1, f"--json dirty must exit 1: {as_json.stderr!r}"
    payload = json.loads(as_json.stdout)
    assert set(payload) == {"findings"}, f"top-level keys: {sorted(payload)!r}"
    assert payload["findings"] == [{
        "file": "test_dirty.py",
        "line": bad_line,
        "pattern": _SPEC_TOKEN,
        "text": f"spec = importlib.util.{_SPEC_TOKEN}('m', 'm.py')",
    }], f"findings: {payload['findings']!r}"

    clean_json = _run_driver(_FI_DRIVER, ["test_clean.py", "--json"], tmp_path, env)
    assert clean_json.returncode == 0
    assert json.loads(clean_json.stdout) == {"findings": []}

    missing = _run_driver(_FI_DRIVER, ["test_missing.py"], tmp_path, env)
    assert missing.returncode == 2, f"unreadable must exit 2: {missing.stdout!r}"
    assert "test_missing.py" in missing.stderr, f"stderr: {missing.stderr!r}"

    skipped = _run_driver(_FI_DRIVER, ["helper.py"], tmp_path, env)
    assert skipped.returncode == 0, "a non-test basename must be skipped"
    assert (skipped.stdout + skipped.stderr) == "", (
        f"a skipped file prints nothing: {skipped.stdout!r} {skipped.stderr!r}"
    )

    mixed = _run_driver(_FI_DRIVER, ["test_clean.py", "test_dirty.py"], tmp_path, env)
    assert mixed.returncode == 1, "one dirty file among several must exit 1"


def test_ac6_stub_passability_driver_contract(tmp_path):
    assert _SP_DRIVER.is_file(), f"driver {_SP_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    source = _sp_dirty_source()
    import_line = _line_of(source, "from mymod import my_func")
    patch_line = _line_of(source, "mock.patch(")
    (tmp_path / "test_dirty.py").write_text(source, encoding="utf-8")
    (tmp_path / "test_clean.py").write_text(_sp_clean_twin_source(), encoding="utf-8")
    (tmp_path / "test_syn.py").write_text("def test_x(:\n    pass\n", encoding="utf-8")
    uut_source = "\n".join([
        "import mymod",
        "from unittest import mock",
        "",
        "",
        "def test_x():",
        "    with mock.patch('mymod.target_fn'):",
        "        assert mymod.run() is None",
        "",
    ])
    uut_patch_line = _line_of(uut_source, "mock.patch(")
    (tmp_path / "test_uut.py").write_text(uut_source, encoding="utf-8")

    clean = _run_driver(_SP_DRIVER, ["test_clean.py"], tmp_path, env)
    assert clean.returncode == 0, f"clean must exit 0: {clean.stdout!r} {clean.stderr!r}"

    dirty = _run_driver(_SP_DRIVER, ["test_dirty.py"], tmp_path, env)
    assert dirty.returncode == 1, f"dirty must exit 1: {dirty.stdout!r} {dirty.stderr!r}"
    text_line = (
        f"STUB-PASSABLE test_dirty.py:{patch_line} mocks UUT 'my_func' "
        f"(subject at :{import_line})"
    )
    assert text_line in dirty.stdout.splitlines(), (
        f"expected line {text_line!r}, got {dirty.stdout!r}"
    )

    as_json = _run_driver(_SP_DRIVER, ["test_dirty.py", "--json"], tmp_path, env)
    assert as_json.returncode == 1, f"--json dirty must exit 1: {as_json.stderr!r}"
    payload = json.loads(as_json.stdout)
    assert set(payload) == {"violations", "errors"}, f"keys: {sorted(payload)!r}"
    assert payload["errors"] == []
    assert payload["violations"] == [{
        "test_file": "test_dirty.py",
        "symbol": "my_func",
        "import_line": import_line,
        "patch_line": patch_line,
        "kind": "mock_uut",
    }], f"violations: {payload['violations']!r}"

    missing = _run_driver(_SP_DRIVER, ["test_missing.py"], tmp_path, env)
    assert missing.returncode == 2, f"unreadable must exit 2: {missing.stdout!r}"
    assert "ERROR test_missing.py:" in missing.stderr, f"stderr: {missing.stderr!r}"

    syntax = _run_driver(_SP_DRIVER, ["test_syn.py"], tmp_path, env)
    assert syntax.returncode == 2, f"syntax error must exit 2: {syntax.stdout!r}"
    assert "ERROR test_syn.py:" in syntax.stderr, f"stderr: {syntax.stderr!r}"

    syntax_json = _run_driver(_SP_DRIVER, ["test_syn.py", "--json"], tmp_path, env)
    assert syntax_json.returncode == 2
    err_payload = json.loads(syntax_json.stdout)
    assert set(err_payload) == {"violations", "errors"}
    assert err_payload["violations"] == []
    assert len(err_payload["errors"]) == 1
    assert set(err_payload["errors"][0]) == {"test_file", "error"}
    assert err_payload["errors"][0]["test_file"] == "test_syn.py"

    bad_flag = _run_driver(_SP_DRIVER, ["--no-such-flag", "test_clean.py"], tmp_path, env)
    assert bad_flag.returncode == 2, "malformed args must exit 2"

    # --uut: the import heuristic alone does not flag target_fn; --uut does.
    without = _run_driver(_SP_DRIVER, ["test_uut.py"], tmp_path, env)
    assert without.returncode == 0, (
        f"no import of target_fn, so clean without --uut: {without.stdout!r}"
    )
    with_uut = _run_driver(_SP_DRIVER, ["test_uut.py", "--uut", "target_fn"], tmp_path, env)
    assert with_uut.returncode == 1, f"--uut must flag it: {with_uut.stdout!r}"
    assert f"STUB-PASSABLE test_uut.py:{uut_patch_line} mocks UUT 'target_fn'" in (
        with_uut.stdout
    )
    listed = _run_driver(
        _SP_DRIVER, ["test_uut.py", "--uut", "other_fn,target_fn"], tmp_path, env
    )
    assert listed.returncode == 1, "--uut takes a comma list"


# ------------------------------------------------------------------------ AC7


def test_ac7_declaration_alone_does_not_disable_but_driver_deletion_plus_declaration_does(tmp_path):
    env = _hermetic_env(tmp_path)

    # (a) driver present AND declared absent -> still refused.
    clone_a = _package_copy(tmp_path, env, "rollback-a")
    with (clone_a / "engine_py" / "bytedigger_engine" / "precommit_lints.py").open("a") as fh:
        fh.write(f'\nDECLARED_ABSENT = list(DECLARED_ABSENT) + ["{_FI_NAME}"]\n')
    _stage(clone_a, env, "test_x.py", _fi_dirty_source())
    rc_a, out_a = _commit(clone_a, env)
    assert rc_a != 0, (
        f"a declaration must not suppress a driver that is on disk; output={out_a!r}"
    )
    assert not _has_head(clone_a, env), f"no commit object may exist; output={out_a!r}"
    assert REFUSE_VIOLATION in out_a and _names_lint(out_a, _FI_NAME), out_a

    # (b) driver deleted AND declared absent -> the documented rollback works.
    clone_b = _package_copy(tmp_path, env, "rollback-b")
    with (clone_b / "engine_py" / "bytedigger_engine" / "precommit_lints.py").open("a") as fh:
        fh.write(f'\nDECLARED_ABSENT = list(DECLARED_ABSENT) + ["{_FI_NAME}"]\n')
    (clone_b / _FI_DRIVER.name).unlink()
    _stage(clone_b, env, "test_x.py", _fi_dirty_source())
    rc_b, out_b = _commit(clone_b, env)
    assert rc_b == 0, f"driver deleted + declared must be accepted; output={out_b!r}"
    assert _has_head(clone_b, env), "the rolled-back lint must let the commit through"


# ------------------------------------------------------------------------ AC8

_ALLOWED_STDLIB = {"argparse", "json", "sys", "pathlib", "__future__"}
_FORBIDDEN_TEXT = (
    ".claude", "HAL_", "SYSTEM/", "/Users/", "os.environ", "os.getenv",
    "subprocess", "socket", "urllib", "http",
)


def _qualified_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "bytedigger_engine":
                found.extend(f"bytedigger_engine.{a.name}" for a in node.names)
            else:
                found.append(module)
    return found


def _sys_path_mutations(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"insert", "append", "extend"}
            and ast.unparse(node.func.value) == "sys.path"
        ):
            calls.append(node)
    return calls


@pytest.mark.parametrize(
    "driver, engine_module",
    [(_FI_DRIVER, "bytedigger_engine.forbidden_import"),
     (_SP_DRIVER, "bytedigger_engine.stub_passability")],
)
def test_ac8_driver_is_portable_and_only_a_thin_cli_over_the_engine_module(driver, engine_module):
    assert driver.is_file(), f"driver {driver} does not exist"
    text = driver.read_text(encoding="utf-8")
    tree = ast.parse(text)

    imports = _qualified_imports(tree)
    allowed = _ALLOWED_STDLIB | {engine_module}
    stray = sorted({m for m in imports if m not in allowed})
    assert stray == [], f"imports outside the allowed set: {stray!r}"
    assert engine_module in imports, (
        f"the driver must import the rule from {engine_module}, got {imports!r}"
    )

    for token in _FORBIDDEN_TEXT:
        assert token not in text, f"driver must not contain {token!r}"
    assert not re.search(
        r"^\s*(import|from)\s+(forbidden_import|stub_passability)\b", text, re.MULTILINE
    ), "no bare-name import of the engine module"

    mutations = _sys_path_mutations(tree)
    assert len(mutations) == 1 and mutations[0].func.attr == "insert", (
        f"exactly one sys.path.insert allowed, got {[ast.unparse(m) for m in mutations]!r}"
    )
    rendered = ast.unparse(mutations[0])
    assert "__file__" in rendered or "__file__" in text, "engine_py must derive from __file__"
    guarded = any(
        isinstance(node, ast.If)
        and any(
            isinstance(op, ast.NotIn) for sub in ast.walk(node.test)
            if isinstance(sub, ast.Compare) for op in sub.ops
        )
        and "sys.path" in ast.unparse(node.test)
        for node in ast.walk(tree)
    )
    assert guarded, "the sys.path insert must sit behind an `if ... not in sys.path` guard"
    assert "engine_py" in text, "the inserted directory must be the sibling engine_py"
