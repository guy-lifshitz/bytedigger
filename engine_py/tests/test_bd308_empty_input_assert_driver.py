"""RED tests for bd#308 -- empty-input-assert-lint runs at commit time, ratcheted against HEAD.

Spec: docs/decisions/2026-10-13-bd308-empty-input-assert-driver.md

Pre-GREEN there is no driver `empty-input-assert-lint.py`, the name is not in the
registry, `RATCHETED_LINTS` / `LINT_OFF_ENVS` do not exist and the layer never runs
the scanner, so a staged test asserting `findings == []` with no live denominator
is committed silently. Every test here fails today for that reason.

The real-CLI ACs run the REAL installer (`scripts/install_git_hooks.py --install`),
the REAL `githooks/pre-commit` and the REAL `scripts/precommit_enforce_cli.py` in a
temporary git clone, through a real `git commit`. Every refusal is paired with the
acceptance of a twin, and the load-bearing observation is the object database:
`git rev-parse --verify HEAD` (or HEAD's sha) before and after.

Hermetic: fake HOME, empty global/system git config, no network, no model calls.
Legacy files are put into HEAD with `git commit --no-verify` (the ratchet is about
what is ALREADY in HEAD), and the scanner itself is asked how many findings each
fixture carries before the assertion relies on that count.

No sys.path mutation, no conftest import.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import precommit_lints
from bytedigger_engine.empty_input_assert import summarize_empty_input_asserts

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_INSTALLER = _REPO_ROOT / "scripts" / "install_git_hooks.py"
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_GITHOOKS = _REPO_ROOT / "githooks"

_NAME = "empty-input-assert-lint"
_DRIVER = _REPO_ROOT / f"{_NAME}.py"
_OFF_ENV = "BD_EMPTY_INPUT_ASSERT_LINT"

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_DRIVER_ERROR = "BD66-REFUSE-DRIVER-ERROR"
LINT_OFF = "BD66-LINT-OFF"
LINT_OFF_UNKNOWN = "BD66-LINT-OFF-UNKNOWN"

_NAME_CHAR = r"[0-9A-Za-z_\-]"


# --------------------------------------------------------------------- helpers


def _names_lint(output: str, name: str) -> bool:
    pattern = rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


def _hermetic_env(tmp_path: Path) -> dict:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    empty_global = fake_home / ".gitconfig"
    empty_global.write_text("")
    env = dict(os.environ)
    for key in ("PYTHONPATH", "BD66_LINT_DIR", _OFF_ENV, "BD_SPEC_LINTS_OFF",
                "BD_LINT_CRASH_IS_VIOLATION"):
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


def _init_repo(tmp_path: Path, env: dict, name: str) -> Path:
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd308@example.com"),
        ("user.name", "bd308 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    return repo


_OTHER_DRIVERS = (
    "cyrillic-prose-lint", "one-sided-predicate-lint", "forbidden-import-lint",
    "stub-passability-lint", "scope-inverse-lint", "helper-extraction-lint",
    "presence-triad-lint", "format-conversion-lint", "closure-evidence-lint",
    "mutation-two-sidedness-lint", "spec-coverage-lint", "token-consistency-lint",
    "spec-cite-lint",  # bd#323
)


def _installed_clone(tmp_path: Path, env: dict, name: str = "clone",
                     engine_copy: bool = False) -> Path:
    """Temp clone carrying the real installer, hook and CLI; hook installed by
    the real `install_git_hooks.py --install`. `engine_py` is a symlink to the
    real package so the CLI resolves the real repo root and the real drivers.

    `engine_copy=True`: the package is COPIED, so the copied registry maps every
    driver at the clone root; the twelve other drivers are exit-0 stand-ins and
    the driver under test is NOT put there (the caller decides)."""
    for needed in (_INSTALLER, _ENFORCE_CLI, _GITHOOKS / "pre-commit"):
        assert needed.is_file(), f"missing {needed}"
    repo = _init_repo(tmp_path, env, name)
    (repo / "scripts").mkdir()
    shutil.copy2(_INSTALLER, repo / "scripts" / _INSTALLER.name)
    shutil.copy2(_ENFORCE_CLI, repo / "scripts" / _ENFORCE_CLI.name)
    # githooks/commit-msg execs this (stdlib-only) script.
    subject_lint = _REPO_ROOT / "scripts" / "commit_subject_lint.py"
    assert subject_lint.is_file(), f"missing {subject_lint}"
    shutil.copy2(subject_lint, repo / "scripts" / subject_lint.name)
    shutil.copytree(_GITHOOKS, repo / "githooks")
    if engine_copy:
        shutil.copytree(
            _PACKAGE_DIR, repo / "engine_py" / "bytedigger_engine",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for other in _OTHER_DRIVERS:
            stand_in = repo / f"{other}.py"
            stand_in.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
            stand_in.chmod(0o755)
    else:
        (repo / "engine_py").symlink_to(_ENGINE_ROOT)
    proc = subprocess.run(
        [sys.executable, str(repo / "scripts" / _INSTALLER.name), "--install",
         "--root", str(repo)],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, f"installer failed: {proc.stdout!r} {proc.stderr!r}"
    path = _git(["config", "--get", "--local", "core.hooksPath"], repo, env)
    assert path.stdout.strip() == "githooks", f"hooks not installed: {path.stdout!r}"
    return repo


def _stage(repo: Path, env: dict, rel: str, text: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    assert _git(["add", rel], repo, env).returncode == 0


def _commit(repo: Path, env: dict, extra=()) -> tuple[int, str]:
    result = _git(["commit", "-m", "bd308 fixture", *extra], repo, env)
    return result.returncode, result.stdout + result.stderr


def _seed_head(repo: Path, env: dict, rel: str, text: str) -> str:
    """Put `text` at `rel` into HEAD bypassing the hook; returns HEAD's sha."""
    _stage(repo, env, rel, text)
    rc, out = _commit(repo, env, extra=("--no-verify",))
    assert rc == 0, f"seed commit failed: {out!r}"
    return _head(repo, env)


def _head(repo: Path, env: dict):
    result = _git(["rev-parse", "--verify", "HEAD"], repo, env)
    return result.stdout.strip() if result.returncode == 0 else None


def _findings(text: str) -> list:
    return summarize_empty_input_asserts(text)["findings"]


def _run_driver(args, cwd: Path, env: dict, py_args=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *py_args, str(_DRIVER), *[str(a) for a in args]],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


# --------------------------------------------------------------- fixture source

_ONE_FINDING = "\n".join([
    "def test_scan_is_clean():",
    "    findings = run_scan(['a.py'])",
    "    assert findings == []",
    "",
])

_WITH_DENOMINATOR = "\n".join([
    "def test_scan_is_clean():",
    "    rows = load_rows()",
    "    findings = run_scan(rows)",
    "    assert len(rows) > 0",
    "    assert findings == []",
    "",
])

_CONTROL_ONLY = "def test_ok():\n    assert 1 + 1 == 2\n"

_LEGACY_TWO = "\n".join([
    "def test_first():",
    "    findings = run_scan(['a.py'])",
    "    assert findings == []",
    "",
    "",
    "def test_second():",
    "    violations = run_other(['b.py'])",
    "    assert violations == []",
    "",
])

_LEGACY_ONE_EDITED = "\n".join([
    "# touched, no finding added",
    "def test_scan_is_clean():",
    "    findings = run_scan(['a.py'])",
    "    assert findings == []",
    "",
    "",
    "def test_unrelated():",
    "    assert 1 + 1 == 2",
    "",
])

_LEGACY_ONE_PLUS_SECOND = "\n".join([
    "def test_scan_is_clean():",
    "    findings = run_scan(['a.py'])",
    "    assert findings == []",
    "",
    "",
    "def test_second():",
    "    violations = run_other(['b.py'])",
    "    assert violations == []",
    "",
])


def _expected_line(path: str, source: str) -> str:
    f = _findings(source)[0]
    return (
        f"EMPTY-INPUT-ASSERT {path}:{f['line']}: {f['form']} - subject "
        f"{f['subject']!r} has no live denominator in scope"
    )


def test_fixtures_carry_the_findings_claimed_by_the_scanner():
    """Non-vacuity guard: the scanner itself, not the author, fixes the counts."""
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    assert len(_findings(_ONE_FINDING)) == 1
    assert len(_findings(_WITH_DENOMINATOR)) == 0
    assert len(_findings(_CONTROL_ONLY)) == 0
    assert len(_findings(_LEGACY_TWO)) == 2
    assert len(_findings(_LEGACY_ONE_EDITED)) == 1
    assert len(_findings(_LEGACY_ONE_PLUS_SECOND)) == 2


# ------------------------------------------------------------------------ AC1


def test_ac1_driver_text_json_and_exit_codes(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    (tmp_path / "test_dirty.py").write_text(_ONE_FINDING, encoding="utf-8")
    (tmp_path / "test_clean.py").write_text(_WITH_DENOMINATOR, encoding="utf-8")
    (tmp_path / "test_empty.py").write_text("", encoding="utf-8")
    (tmp_path / "helper.py").write_text(_ONE_FINDING, encoding="utf-8")
    (tmp_path / "test_bad.py").write_bytes(b"\xff\xfe\x00 not utf8\n")

    clean = _run_driver(["test_clean.py"], tmp_path, env)
    assert clean.returncode == 0, f"{clean.stdout!r} {clean.stderr!r}"
    assert clean.stdout == ""

    zero = _run_driver(["test_empty.py"], tmp_path, env)
    assert zero.returncode == 0 and zero.stdout == "", "zero candidates is clean"

    dirty = _run_driver(["test_dirty.py"], tmp_path, env)
    assert dirty.returncode == 1, f"{dirty.stdout!r} {dirty.stderr!r}"
    assert dirty.stdout.splitlines() == [_expected_line("test_dirty.py", _ONE_FINDING)], (
        f"stdout must be exactly the one finding line: {dirty.stdout!r}"
    )

    as_json = _run_driver(["test_dirty.py", "--json"], tmp_path, env)
    assert as_json.returncode == 1
    payload = json.loads(as_json.stdout)
    assert set(payload) == {"findings"}
    f = _findings(_ONE_FINDING)[0]
    assert payload["findings"] == [{
        "file": "test_dirty.py", "line": f["line"],
        "subject": f["subject"], "form": f["form"],
    }]
    clean_json = _run_driver(["test_clean.py", "--json"], tmp_path, env)
    assert clean_json.returncode == 0
    assert json.loads(clean_json.stdout) == {"findings": []}

    skipped = _run_driver(["helper.py"], tmp_path, env)
    assert skipped.returncode == 0, "a non-test basename is skipped"
    assert skipped.stdout + skipped.stderr == ""

    no_file = _run_driver([], tmp_path, env)
    assert no_file.returncode == 2, "no FILE is a usage error"

    missing = _run_driver(["test_missing.py"], tmp_path, env)
    assert missing.returncode == 2 and "test_missing.py" in missing.stderr
    undecodable = _run_driver(["test_bad.py"], tmp_path, env)
    assert undecodable.returncode == 2, f"undecodable must exit 2: {undecodable.stdout!r}"

    # Exit-code precedence: 2 wins over 1.
    mixed = _run_driver(["test_dirty.py", "test_missing.py"], tmp_path, env)
    assert mixed.returncode == 2, f"{mixed.stdout!r} {mixed.stderr!r}"


def test_ac1_identity_broken_scanner_is_a_driver_error(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    (tmp_path / "test_clean.py").write_text(_CONTROL_ONLY, encoding="utf-8")
    baseline = _run_driver(["test_clean.py"], tmp_path, env)
    assert baseline.returncode == 0, f"unpatched scanner must be clean: {baseline.stderr!r}"

    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_ENGINE_ROOT)!r})\n"
        "from bytedigger_engine import empty_input_assert as _m\n"
        "def _broken(text):\n"
        "    return {'candidates': 3, 'controlled': 0, 'escaped': 0,\n"
        "            'findings': [], 'identity_ok': False}\n"
        "_m.summarize_empty_input_asserts = _broken\n",
        encoding="utf-8",
    )
    broken_env = dict(env)
    broken_env["PYTHONPATH"] = str(shim)
    broken = _run_driver(["test_clean.py"], tmp_path, broken_env)
    assert broken.returncode == 2, (
        f"a scanner that did not walk its population is not clean: "
        f"rc={broken.returncode} {broken.stdout!r} {broken.stderr!r}"
    )
    assert broken.stderr.strip() != "", "the driver error needs a stderr line"


def test_ac1_syntax_warning_file_has_empty_stderr(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    source = _ONE_FINDING + "\n\ndef test_escape():\n    pattern = '\\d+'\n    assert pattern\n"
    target = tmp_path / "test_escape.py"
    target.write_text(source, encoding="utf-8")

    # Non-vacuity: parsing this file really does warn under the same flags.
    probe = subprocess.run(
        [sys.executable, "-W", "always", "-c",
         "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(target)],
        capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL,
    )
    assert "invalid escape sequence" in probe.stderr, (
        f"fixture must emit the warning: {probe.stderr!r}"
    )

    result = _run_driver(["test_escape.py"], tmp_path, env, py_args=("-W", "always"))
    assert result.returncode == 1, f"{result.stdout!r} {result.stderr!r}"
    assert result.stderr == "", f"SyntaxWarning must not be forwarded: {result.stderr!r}"


# ------------------------------------------------------------------------ AC2


def test_ac2_registry_lists_the_driver_and_the_denominator_is_13():
    assert _NAME in precommit_lints.TEST_LINTS
    assert list(precommit_lints.TEST_LINTS) == [
        "forbidden-import-lint", "stub-passability-lint", _NAME,
    ]
    mapped = precommit_lints.LINT_DRIVER_DIRS.get(_NAME)
    assert mapped is not None and os.path.realpath(mapped) == os.path.realpath(str(_REPO_ROOT))
    resolved = Path(precommit_lints.driver_path(_NAME, "/nonexistent-lint-dir-bd308"))
    assert os.path.realpath(str(resolved)) == os.path.join(
        os.path.realpath(str(_REPO_ROOT)), f"{_NAME}.py"
    )
    assert resolved.is_file(), f"driver file missing: {resolved}"
    assert os.access(resolved, os.X_OK), "driver must be executable"
    assert resolved.read_text(encoding="utf-8").splitlines()[0] == "#!/usr/bin/env python3"

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    names = precommit_enforce.registry_names()
    # registry_names() sweeps every `*_LINTS` list: SPEC 9 + TEST 3 + TS 1 + TEXT 1.
    assert len(names) == 14, f"registry denominator must be 14: {names!r}"
    assert len(set(names)) == 14
    assert len(
        list(precommit_lints.SPEC_LINTS)
        + list(precommit_lints.TEST_LINTS)
        + list(precommit_lints.TS_TEST_LINTS)
    ) == 13
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == [], f"pre-pass must be clean: {violations!r}"
    missing = [n for n in names
               if not present.get(n) and n not in precommit_lints.DECLARED_ABSENT]
    assert missing == [], f"names without a driver: {missing!r}"
    assert present.get(_NAME) is True
    assert precommit_lints.DECLARED_ABSENT == [], (
        "nothing stays held in DECLARED_ABSENT (bd#323)")


def test_ac2_ratchet_and_switch_tables_are_declared():
    assert precommit_lints.RATCHETED_NAMES == (_NAME,)
    assert isinstance(precommit_lints.RATCHETED_NAMES, tuple)
    assert not hasattr(precommit_lints, "RATCHETED_LINTS"), (
        "the tuple must not carry the `_LINTS` suffix (registry discovery)"
    )
    assert precommit_lints.LINT_OFF_ENVS == {_NAME: _OFF_ENV}


# ------------------------------------------------------------------------ AC3


def test_ac3a_b_reference_case_is_refused_and_its_denominator_twin_commits(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", _ONE_FINDING)
    assert len(_findings(_ONE_FINDING)) == 1

    rc, out = _commit(repo, env)

    assert rc != 0, f"the reference case must be refused: {out!r}"
    assert _head(repo, env) is None, f"no commit object may exist: {out!r}"
    assert REFUSE_VIOLATION in out and _names_lint(out, _NAME), out
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_x.py rc=1 findings=1 head=0" in out
    ), out
    assert _expected_line("test_x.py", _ONE_FINDING) in out.splitlines(), out

    _stage(repo, env, "test_x.py", _WITH_DENOMINATOR)
    rc2, out2 = _commit(repo, env)
    assert rc2 == 0, f"the twin with a live denominator must commit: {out2!r}"
    assert _head(repo, env) is not None


def test_ac3c_legacy_file_edited_without_a_new_finding_commits(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    seed = _seed_head(repo, env, "test_legacy.py", _ONE_FINDING)
    # Same clone, ratchet-active proof: a WORSENED variant is refused first.
    _stage(repo, env, "test_legacy.py", _LEGACY_ONE_PLUS_SECOND)
    rc_w, out_w = _commit(repo, env)
    assert rc_w != 0, f"the worsened variant must be refused: {out_w!r}"
    assert _head(repo, env) == seed
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_legacy.py rc=1 findings=2 head=1" in out_w
    ), out_w
    assert _git(["reset", "-q"], repo, env).returncode == 0
    _stage(repo, env, "test_legacy.py", _LEGACY_ONE_EDITED)

    rc, out = _commit(repo, env)

    assert rc == 0, f"a legacy finding that is not worsened must commit: {out!r}"
    assert _head(repo, env) != seed, "a new commit must exist"
    assert REFUSE_VIOLATION not in out
    assert "EMPTY-INPUT-ASSERT" not in out, f"accepted commit must be silent: {out!r}"


def test_ac3d_legacy_file_with_a_second_finding_is_refused(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    seed = _seed_head(repo, env, "test_legacy.py", _ONE_FINDING)
    _stage(repo, env, "test_legacy.py", _LEGACY_ONE_PLUS_SECOND)

    rc, out = _commit(repo, env)

    assert rc != 0, f"adding a finding to a legacy file must be refused: {out!r}"
    assert _head(repo, env) == seed, "no new commit may exist"
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_legacy.py rc=1 findings=2 head=1" in out
    ), out


def test_ac3e_legacy_file_reduced_from_two_findings_to_one_commits(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    seed = _seed_head(repo, env, "test_legacy.py", _LEGACY_TWO)
    worse = _LEGACY_TWO + "\n\ndef test_third():\n    hits = run_more()\n    assert hits == []\n"
    assert len(_findings(worse)) == 3
    _stage(repo, env, "test_legacy.py", worse)
    rc_w, out_w = _commit(repo, env)
    assert rc_w != 0, f"the worsened variant must be refused: {out_w!r}"
    assert _head(repo, env) == seed
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_legacy.py rc=1 findings=3 head=2" in out_w
    ), out_w
    assert _git(["reset", "-q"], repo, env).returncode == 0
    _stage(repo, env, "test_legacy.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc == 0, f"fewer findings than HEAD must commit: {out!r}"
    assert _head(repo, env) != seed
    assert REFUSE_VIOLATION not in out
    assert "EMPTY-INPUT-ASSERT" not in out, f"accepted commit must be silent: {out!r}"


def test_ac3f_new_file_with_a_finding_is_refused_even_when_head_exists(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    seed = _seed_head(repo, env, "test_other.py", _CONTROL_ONLY)
    _stage(repo, env, "test_new.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc != 0, f"a new file starts at base 0: {out!r}"
    assert _head(repo, env) == seed
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_new.py rc=1 findings=1 head=0" in out
    ), out


def test_ac7_per_file_comparison_in_one_real_commit(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    _seed_head(repo, env, "test_a.py", _LEGACY_TWO)
    seed = _seed_head(repo, env, "test_b.py", _ONE_FINDING)
    # a reduced (2 -> 1) and b worsened (1 -> 2): the totals are equal.
    _stage(repo, env, "test_a.py", _ONE_FINDING)
    _stage(repo, env, "test_b.py", _LEGACY_ONE_PLUS_SECOND)

    rc, out = _commit(repo, env)

    assert rc != 0, f"one worsened file refuses the commit: {out!r}"
    assert _head(repo, env) == seed
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_b.py rc=1 findings=2 head=1" in out
    ), out
    assert "file=test_a.py" not in out, f"the reduced file must not be named: {out!r}"

    assert _git(["reset", "-q"], repo, env).returncode == 0
    _stage(repo, env, "test_a.py", _ONE_FINDING)
    _stage(repo, env, "test_b.py", _LEGACY_ONE_EDITED)
    rc2, out2 = _commit(repo, env)
    assert rc2 == 0, f"reduced + unchanged must commit: {out2!r}"
    assert _head(repo, env) != seed
    assert "EMPTY-INPUT-ASSERT" not in out2


def test_ac8_rename_of_a_legacy_file_is_a_new_path_with_base_zero(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    _seed_head(repo, env, "test_clean_old.py", _CONTROL_ONLY)
    seed = _seed_head(repo, env, "test_old.py", _ONE_FINDING)
    assert _git(["mv", "test_old.py", "test_new.py"], repo, env).returncode == 0

    rc, out = _commit(repo, env)

    assert rc != 0, f"a renamed legacy finding is a new path: {out!r}"
    assert _head(repo, env) == seed
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_new.py rc=1 findings=1 head=0" in out
    ), out

    assert _git(["reset", "-q", "--hard"], repo, env).returncode == 0
    assert _git(["mv", "test_clean_old.py", "test_clean_new.py"], repo, env).returncode == 0
    rc2, out2 = _commit(repo, env)
    assert rc2 == 0, f"a clean rename must commit: {out2!r}"
    assert _head(repo, env) != seed


def test_ac11_nested_path_is_reported_in_full(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    rel = "tests/sub/test_x.py"
    _stage(repo, env, rel, _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc != 0, f"nested dirty file must be refused: {out!r}"
    assert _head(repo, env) is None
    assert _expected_line(rel, _ONE_FINDING) in out.splitlines(), out
    assert f"{REFUSE_VIOLATION}: lint={_NAME} file={rel} rc=1 findings=1 head=0" in out, out

    _stage(repo, env, rel, _WITH_DENOMINATOR)
    rc2, out2 = _commit(repo, env)
    assert rc2 == 0, f"nested twin must commit: {out2!r}"
    assert _head(repo, env) is not None


def test_ac9_undecodable_staged_file_is_a_driver_error_not_a_violation(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    (repo / "test_bad.py").write_bytes(b"\xff\xfe\x00 not utf8\n")
    assert _git(["add", "test_bad.py"], repo, env).returncode == 0

    rc, out = _commit(repo, env)

    assert rc != 0, f"an undecodable test file must be refused: {out!r}"
    assert _head(repo, env) is None
    assert f"{REFUSE_DRIVER_ERROR}: lint={_NAME} file=test_bad.py" in out, out
    assert f"{REFUSE_VIOLATION}: lint={_NAME}" not in out, out


def test_ac9_crashing_driver_is_a_driver_error_not_a_violation(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _installed_clone(tmp_path, env)
    # Pair: the clean file commits with the unpatched scanner.
    _stage(repo, env, "test_ok.py", _CONTROL_ONLY)
    rc0, out0 = _commit(repo, env)
    assert rc0 == 0, f"clean file must commit: {out0!r}"
    seed = _head(repo, env)
    assert seed is not None
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"

    shim = tmp_path / "crash-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_ENGINE_ROOT)!r})\n"
        "from bytedigger_engine import empty_input_assert as _m\n"
        "def _boom(text):\n"
        "    raise RuntimeError('scanner exploded')\n"
        "_m.summarize_empty_input_asserts = _boom\n",
        encoding="utf-8",
    )
    crash_env = dict(env)
    crash_env["PYTHONPATH"] = str(shim)
    _stage(repo, crash_env, "test_ok2.py", _CONTROL_ONLY)

    rc, out = _commit(repo, crash_env)

    assert rc != 0, f"a crashing driver must refuse: {out!r}"
    assert _head(repo, env) == seed
    assert f"{REFUSE_DRIVER_ERROR}: lint={_NAME} file=test_ok2.py" in out, out
    assert f"{REFUSE_VIOLATION}: lint={_NAME}" not in out, out


# ------------------------------------------------------------------------ AC10


def test_ac10_switch_off_does_not_skip_the_registry_prepass(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    env[_OFF_ENV] = "off"
    repo = _installed_clone(tmp_path, env, engine_copy=True)
    assert not (repo / f"{_NAME}.py").exists()
    _stage(repo, env, "test_x.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc != 0, f"driver absent + switch off must still refuse: {out!r}"
    assert _head(repo, env) is None
    assert "BD66-REFUSE-MISSING-DRIVER" in out, out
    assert _names_lint(out, _NAME), out

    # Other side: the driver present, switch off -> commits and reports OFF.
    shutil.copy2(_DRIVER, repo / _DRIVER.name)
    rc2, out2 = _commit(repo, env)
    assert rc2 == 0, f"driver present + off must commit: {out2!r}"
    assert f"{LINT_OFF}: lint={_NAME} file=test_x.py" in out2, out2
    assert _head(repo, env) is not None


# ------------------------------------------------------------------------ AC4


def test_ac4_switch_off_commits_and_reports_in_the_same_real_hook(tmp_path):
    env = _hermetic_env(tmp_path)
    env[_OFF_ENV] = "off"
    repo = _installed_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc == 0, f"off must let the reference case commit: {out!r}"
    assert _head(repo, env) is not None
    assert f"{LINT_OFF}: lint={_NAME} file=test_x.py" in out, out
    assert REFUSE_VIOLATION not in out
    assert "EMPTY-INPUT-ASSERT" not in out, "the driver must not run when switched off"


@pytest.mark.parametrize("value", ["on", "", "OFF", "off "])
def test_ac4_any_other_value_is_on_and_refuses(tmp_path, value):
    env = _hermetic_env(tmp_path)
    env[_OFF_ENV] = value
    repo = _installed_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc != 0, f"value {value!r} must leave the lint on: {out!r}"
    assert _head(repo, env) is None
    assert REFUSE_VIOLATION in out and _names_lint(out, _NAME), out
    assert f"{LINT_OFF}: lint={_NAME}" not in out


# ------------------------------------------------------------------------ AC5


def test_ac5_spec_lints_off_does_not_disable_the_test_lint(tmp_path):
    env = _hermetic_env(tmp_path)
    env["BD_SPEC_LINTS_OFF"] = _NAME
    repo = _installed_clone(tmp_path, env)
    _stage(repo, env, "test_x.py", _ONE_FINDING)

    rc, out = _commit(repo, env)

    assert rc != 0, f"BD_SPEC_LINTS_OFF honors only SPEC_LINTS: {out!r}"
    assert _head(repo, env) is None
    assert f"{LINT_OFF_UNKNOWN}: name={_NAME}" in out, out
    assert REFUSE_VIOLATION in out and _names_lint(out, _NAME), out


# ------------------------------------------------------------------------ AC6


def _apply_env(monkeypatch, env: dict) -> None:
    for key in ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT"):
        monkeypatch.setenv(key, env[key])
    for key in (_OFF_ENV, "BD_SPEC_LINTS_OFF", "BD_LINT_CRASH_IS_VIOLATION"):
        monkeypatch.delenv(key, raising=False)


def _plan(repo_files):
    commands = precommit_lints.build_lint_commands([], list(repo_files),
                                                   precommit_lints.DEFAULT_LINT_DIR)
    return [c for c in commands if c["lint"] == _NAME]


def test_ac6_ratchet_unborn_head_refuses_a_one_finding_file(tmp_path, monkeypatch, capsys):
    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    env = _hermetic_env(tmp_path)
    _apply_env(monkeypatch, env)
    repo = _init_repo(tmp_path, env, "unit")
    (repo / "test_x.py").write_text(_ONE_FINDING, encoding="utf-8")
    (repo / "test_ok.py").write_text(_CONTROL_ONLY, encoding="utf-8")
    assert _head(repo, env) is None, "HEAD must be unborn"
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == []
    assert present.get(_NAME) is True

    commands = _plan(["test_x.py"])
    assert len(commands) == 1, f"the lint must be planned for a test file: {commands!r}"
    refused = precommit_enforce.run_plan(commands, present, str(repo))
    assert refused == [
        f"{REFUSE_VIOLATION}: lint={_NAME} file=test_x.py rc=1 findings=1 head=0"
    ], refused
    assert _expected_line("test_x.py", _ONE_FINDING) in capsys.readouterr().out.splitlines()

    ok = precommit_enforce.run_plan(_plan(["test_ok.py"]), present, str(repo))
    assert ok == [], f"a clean file passes on an unborn HEAD: {ok!r}"


def test_ac6_ratchet_head_run_that_errors_is_a_driver_error_not_a_zero_baseline(
    tmp_path, monkeypatch
):
    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    env = _hermetic_env(tmp_path)
    _apply_env(monkeypatch, env)
    repo = _init_repo(tmp_path, env, "unit-err")
    # HEAD holds an undecodable blob: the driver exits 2 on it.
    (repo / "test_x.py").write_bytes(b"\xff\xfe\x00 not utf8\n")
    assert _git(["add", "test_x.py"], repo, env).returncode == 0
    rc, out = _commit(repo, env, extra=("--no-verify",))
    assert rc == 0, out
    # The working tree holds a decodable one-finding file.
    (repo / "test_x.py").write_text(_ONE_FINDING, encoding="utf-8")
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == []

    result = precommit_enforce.run_plan(_plan(["test_x.py"]), present, str(repo))

    assert len(result) == 1, result
    assert result[0].startswith(f"{REFUSE_DRIVER_ERROR}: lint={_NAME} file=test_x.py"), result
    assert REFUSE_VIOLATION not in result[0], "an erroring HEAD run is not baseline 0"


def test_ac6_ratcheted_crash_with_crash_is_violation_is_refused_not_accepted(
    tmp_path, monkeypatch
):
    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    env = _hermetic_env(tmp_path)
    _apply_env(monkeypatch, env)
    monkeypatch.setenv("BD_LINT_CRASH_IS_VIOLATION", "1")
    repo = _init_repo(tmp_path, env, "unit-crash-violation")
    (repo / "test_ok.py").write_text(_CONTROL_ONLY, encoding="utf-8")
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == []

    commands = _plan(["test_ok.py"])
    assert len(commands) == 1, f"the lint must be planned for a test file: {commands!r}"
    # A driver that crashes: exit 1, a traceback on stderr, nothing on stdout.
    crashing = dict(commands[0])
    crashing["argv"] = [sys.executable, "-c", "raise RuntimeError('scanner exploded')"]

    result = precommit_enforce.run_plan([crashing], present, str(repo))

    assert len(result) == 1, f"a ratcheted crash under CRASH_IS_VIOLATION must refuse: {result!r}"
    assert result[0].startswith(f"{REFUSE_VIOLATION}: lint={_NAME} file=test_ok.py"), result
    assert REFUSE_DRIVER_ERROR not in result[0], result
