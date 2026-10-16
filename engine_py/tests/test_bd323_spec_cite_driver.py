"""RED tests for bd#323 -- spec-cite becomes a blocking lint (driver + layer).

Spec: docs/decisions/2026-10-16-bd323-spec-cite-blocking.md, sections 2 and 3.

Pre-GREEN: there is no `spec-cite-lint.py` at the repo root, `spec_cite` has no
`lint_spec_union`, `spec-cite-lint` is still the one name in `DECLARED_ABSENT`
and has no `LINT_DRIVER_DIRS` entry, so the layer never runs it and
`phase_45_spec._verify_spec_cite_lint` finds no driver.

Anchors: the driver ACs run the REAL `spec-cite-lint.py` by subprocess; the
layer ACs run the REAL `scripts/precommit_enforce_cli.py` (and the real driver
it execs) in a throwaway git repo through a one-line `pre-commit` hook with a
hermetic git environment; a refusal is a real `git commit` side effect observed
through the object database. The kill-switch ACs use a package copy whose
registry is the copied `precommit_lints.py` (same pattern as the bd#304/bd#305
tests). The library ACs call the real `spec_cite` functions on real conformance
specs. Every fixture lives in `tmp_path` (the one probe copy written next to a
conformance spec, AC2, is removed in a `finally`).

No sys.path mutation, no conftest import; units under test are reached by
subprocess or by an in-test-body import. ASCII only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from bytedigger_engine import precommit_lints

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_CONFORMANCE = _PACKAGE_DIR / "conformance"
_DRIVER = _REPO_ROOT / "spec-cite-lint.py"

_CITE = "spec-cite-lint"
REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"

_EXISTING_SPECS = [
    "AUTHORSHIP_SPEC.md", "CONTRACTS_SPEC.md", "EMISSIONS_SPEC.md",
    "ORACLE_SPEC.md", "QUANT_LINT_SPEC.md",
]
_NAME_CHAR = r"[0-9A-Za-z_\-]"

_REAL_CITE_FILE = "engine_py/bytedigger_engine/spec_cite.py"
_MISSING_CITE_FILE = "engine_py/bytedigger_engine/no_such_module.py"
_CLEAN_LINE = f"The `lint_spec` entry point is defined in `{_REAL_CITE_FILE}`."
_DIRTY_LINE = f"The `no_symbol` helper is defined in `{_MISSING_CITE_FILE}`."


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


def _init_repo(tmp_path: Path, env: dict, name: str) -> Path:
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd323@example.com"),
        ("user.name", "bd323 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    return repo


def _install_hook(repo: Path, cli: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{cli}"\n')
    hook.chmod(0o755)


def _real_clone(tmp_path: Path, env: dict, name: str) -> Path:
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
    result = _git(["commit", "-m", "bd323 fixture"], repo, env)
    return result.returncode, result.stdout + result.stderr


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _run_driver(args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_DRIVER), *[str(a) for a in args]],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=120, stdin=subprocess.DEVNULL,
    )


def _run_cli(clone: Path, env: dict) -> subprocess.CompletedProcess:
    cli = clone / "scripts" / "precommit_enforce_cli.py"
    return subprocess.run(
        [sys.executable, str(cli)], cwd=str(clone), env=env,
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )


def _exit_stand_in(directory: Path, name: str, code: int = 0) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.exit({code})\n")
    path.chmod(0o755)


def _five_specs() -> list[Path]:
    specs = sorted(_CONFORMANCE.glob("*_SPEC.md"))
    assert [p.name for p in specs] == _EXISTING_SPECS, f"existing specs changed: {specs!r}"
    return specs


def _build(*, create_decl: bool = True) -> str:
    """Base spec, clean for all nine lints (same base as the bd#304/bd#305
    tests). With `create_decl=False` the one citation (`build_widget` in
    src/widget.py) is neither on disk nor declared by a CREATE line, so the cite
    lint reports `missing_file`, a blocking status; every other lint stays clean."""
    lines = [
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
    ]
    if create_decl:
        lines += ["- CREATE: src/widget.py", ""]
    lines += [
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


_CHECK_MODULES = (
    "scope_inverse", "helper_extraction", "spec_cite", "spec_coverage",
    "token_consistency", "presence_triad", "format_conversion_cases",
    "error_codes",
)
_VERIFIERS = ("closure_evidence_verifier", "mutation_two_sidedness_verifier")
_OTHER_SPEC_AND_TEST_DRIVERS = (
    "scope-inverse-lint", "helper-extraction-lint", "spec-coverage-lint",
    "token-consistency-lint", "presence-triad-lint", "format-conversion-lint",
    "closure-evidence-lint", "mutation-two-sidedness-lint",
    "cyrillic-prose-lint", "one-sided-predicate-lint", "forbidden-import-lint",
    "stub-passability-lint", "empty-input-assert-lint",
)


def _package_copy(tmp_path: Path, env: dict, name: str) -> Path:
    """A clone carrying a COPY of the registry, the layer and the REAL cite driver.

    The copied registry recomputes its repo root as the clone, so a mapped name
    is looked for at `<clone>/<name>.py`. Only the cite driver is the real file
    (it imports the copied `spec_cite`); the thirteen other drivers are exit-0
    stand-ins, they are not what this lot is about. The clone's hook execs the
    CLI copy, so a refusal is a real `git commit`.
    """
    assert _DRIVER.is_file(), f"bd#323: the driver {_DRIVER} does not exist yet"
    clone = _init_repo(tmp_path, env, name)
    pkg = clone / "engine_py" / "bytedigger_engine"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for module in (
        "precommit_lints", "precommit_enforce", "green_entry_guard",
        "verdict_verify", *_CHECK_MODULES,
    ):
        shutil.copy2(_PACKAGE_DIR / f"{module}.py", pkg / f"{module}.py")
    scripts_pkg = pkg / "scripts"
    scripts_pkg.mkdir()
    (scripts_pkg / "__init__.py").write_text("")
    lib = scripts_pkg / "lib"
    lib.mkdir()
    for module in _VERIFIERS:
        shutil.copy2(_PACKAGE_DIR / "scripts" / "lib" / f"{module}.py", lib / f"{module}.py")
    allowlist = _PACKAGE_DIR / "sql-schema-allowlist.txt"
    if allowlist.is_file():
        shutil.copy2(allowlist, pkg / allowlist.name)
    scripts = clone / "scripts"
    scripts.mkdir()
    shutil.copy2(_ENFORCE_CLI, scripts / "precommit_enforce_cli.py")
    shutil.copy2(_DRIVER, clone / _DRIVER.name)
    for other in _OTHER_SPEC_AND_TEST_DRIVERS:
        _exit_stand_in(clone, other, 0)
    _install_hook(clone, scripts / "precommit_enforce_cli.py")
    return clone


def _append_registry(clone: Path, text: str) -> None:
    with (clone / "engine_py" / "bytedigger_engine" / "precommit_lints.py").open("a") as fh:
        fh.write("\n" + text + "\n")


def _blocking(findings) -> list:
    from bytedigger_engine import spec_cite  # noqa: PLC0415

    return [f for f in findings if f.status in spec_cite.BLOCKING_STATUSES]


def _as_tuples(findings) -> list[tuple[str, str, str]]:
    return [(f.file, f.symbol, f.status) for f in findings]


# ------------------------------------------------------------------------ AC1


def test_ac1_the_driver_exits_0_on_each_of_the_five_conformance_specs(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)
    for spec in _five_specs():
        proc = _run_driver(["--spec", spec], tmp_path, env)
        assert proc.returncode == 0, (
            f"{spec.name}: rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert proc.stdout == "", f"{spec.name}: a clean spec prints nothing: {proc.stdout!r}"


# ------------------------------------------------------------------------ AC2


def test_ac2_lint_spec_union_is_clean_on_a_conformance_copy_and_blocks_an_added_bad_citation():
    from bytedigger_engine import spec_cite  # noqa: PLC0415

    lint_spec_union = getattr(spec_cite, "lint_spec_union", None)
    assert lint_spec_union is not None, "spec_cite.lint_spec_union does not exist"

    # The probe copy must sit in the package directory (an __init__.py chain),
    # or the union rule has nothing to union. Name never matches *_spec.md.
    probe = _CONFORMANCE / f"_bd323_probe_{uuid.uuid4().hex}.md"
    original = (_CONFORMANCE / "ORACLE_SPEC.md").read_text(encoding="utf-8")
    try:
        probe.write_text(original, encoding="utf-8")
        code, findings = lint_spec_union(probe, _REPO_ROOT)
        assert code == 0, f"the unmodified copy must be clean: {_as_tuples(_blocking(findings))!r}"
        assert _blocking(findings) == []

        probe.write_text(original + "\n" + _DIRTY_LINE + "\n", encoding="utf-8")
        code, findings = lint_spec_union(probe, _REPO_ROOT)
        assert code != 0, "an added citation of a missing module must block"
        added = [
            f for f in _blocking(findings)
            if f.file.endswith("no_such_module.py") and f.symbol == "no_symbol"
        ]
        assert len(added) == 1, f"expected one blocking finding for the addition: {findings!r}"
        assert added[0].status == "missing_file", added[0]
        assert len(_blocking(findings)) == 1, (
            f"only the addition may block: {_as_tuples(_blocking(findings))!r}"
        )
    finally:
        probe.unlink()


def test_ac2_the_union_drops_a_repo_root_finding_only_when_the_package_root_resolves_it(tmp_path):
    from bytedigger_engine import spec_cite  # noqa: PLC0415

    lint_spec_union = getattr(spec_cite, "lint_spec_union", None)
    assert lint_spec_union is not None, "spec_cite.lint_spec_union does not exist"

    repo = tmp_path / "repo"
    pkg = repo / "pkgdir"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (sub / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("def helper_fn():\n    return 1\n")

    resolved_by_pkg = sub / "found_spec.md"
    resolved_by_pkg.write_text("See `mod.py` and `helper_fn` here.\n")
    blocked_in_both = sub / "gone_spec.md"
    blocked_in_both.write_text("See `gone.py` and `helper_fn` here.\n")

    # Under repo-root resolution alone both specs block (the file is not at the root).
    assert spec_cite.lint_spec(resolved_by_pkg, repo)[0] == 1
    assert spec_cite.lint_spec(blocked_in_both, repo)[0] == 1

    code, findings = lint_spec_union(resolved_by_pkg, repo)
    assert code == 0 and _blocking(findings) == [], (
        f"the package root resolves it: {_as_tuples(findings)!r}"
    )
    code, findings = lint_spec_union(blocked_in_both, repo)
    assert code == 1, "a finding blocking under both resolutions must stay blocking"
    assert [(f.file, f.symbol, f.status) for f in _blocking(findings)] == [
        ("gone.py", "helper_fn", "missing_file")
    ]


# ------------------------------------------------------------------------ AC3


def test_ac3_outside_a_package_the_union_equals_lint_spec_clean_and_blocking(tmp_path):
    from bytedigger_engine import spec_cite  # noqa: PLC0415

    lint_spec_union = getattr(spec_cite, "lint_spec_union", None)
    assert lint_spec_union is not None, "spec_cite.lint_spec_union does not exist"
    assert not (tmp_path / "__init__.py").exists()

    clean = tmp_path / "clean_spec.md"
    clean.write_text(_CLEAN_LINE + "\n", encoding="utf-8")
    dirty = tmp_path / "dirty_spec.md"
    dirty.write_text(_CLEAN_LINE + "\n" + _DIRTY_LINE + "\n", encoding="utf-8")

    for spec, expected_code in ((clean, 0), (dirty, 1)):
        want_code, want = spec_cite.lint_spec(spec, _REPO_ROOT)
        got_code, got = lint_spec_union(spec, _REPO_ROOT)
        assert want_code == expected_code, f"fixture {spec.name} drifted: {want_code}"
        assert got_code == want_code, f"{spec.name}: {got_code} != {want_code}"
        assert _as_tuples(got) == _as_tuples(want), f"{spec.name}: {got!r} != {want!r}"


# ------------------------------------------------------------------------ AC4


def test_ac4_phase_45_locates_the_real_driver_and_its_json_is_what_it_parses(tmp_path):
    from bytedigger_engine.workflows import phase_45_spec  # noqa: PLC0415

    computed = Path(phase_45_spec.__file__).parent.parent.parent.parent / "spec-cite-lint.py"
    assert computed.is_file(), f"phase_45_spec looks for the driver at {computed}: missing"
    assert os.path.realpath(computed) == os.path.realpath(str(_DRIVER))

    env = _hermetic_env(tmp_path)
    clean = tmp_path / "clean_spec.md"
    clean.write_text(_CLEAN_LINE + "\n", encoding="utf-8")
    dirty = tmp_path / "dirty_spec.md"
    dirty.write_text(_DIRTY_LINE + "\n", encoding="utf-8")

    def _call(spec: Path) -> subprocess.CompletedProcess:
        # The exact argv phase_45_spec._verify_spec_cite_lint builds.
        return subprocess.run(
            [sys.executable, str(computed), "--spec", str(spec),
             "--repo-root", str(_REPO_ROOT), "--json"],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
            timeout=120, stdin=subprocess.DEVNULL,
        )

    ok = _call(clean)
    assert ok.returncode == 0, f"clean fixture: {ok.stdout!r} {ok.stderr!r}"
    payload = json.loads(ok.stdout)
    assert set(payload) == {"findings"}, payload
    assert phase_45_spec._parse_cite_blocking(ok.stdout) == []
    assert any(f.get("symbol") == "lint_spec" for f in payload["findings"]), (
        f"the clean fixture's citation must be listed (resolved): {payload!r}"
    )

    bad = _call(dirty)
    assert bad.returncode == 1, f"missing path must exit 1: {bad.stdout!r} {bad.stderr!r}"
    blocking = phase_45_spec._parse_cite_blocking(bad.stdout)
    assert len(blocking) == 1, bad.stdout
    assert set(blocking[0]) >= {"file", "symbol", "status"}, blocking
    assert blocking[0]["file"].endswith("no_such_module.py"), blocking
    assert blocking[0]["symbol"] == "no_symbol", blocking
    assert blocking[0]["status"] == "missing_file", blocking
    for item in json.loads(bad.stdout)["findings"]:
        assert {"file", "symbol", "status"} <= set(item), item


def test_ac4_text_mode_prints_one_line_per_blocking_finding_and_repo_root_defaults_to_the_driver_dir(
    tmp_path,
):
    env = _hermetic_env(tmp_path)
    clean = tmp_path / "clean_spec.md"
    clean.write_text(_CLEAN_LINE + "\n", encoding="utf-8")
    dirty = tmp_path / "dirty_spec.md"
    dirty.write_text(_CLEAN_LINE + "\n" + _DIRTY_LINE + "\n", encoding="utf-8")

    # cwd is tmp_path, not the repo: the clean citation resolves only if the
    # default --repo-root is the directory of the driver.
    ok = _run_driver(["--spec", clean], tmp_path, env)
    assert ok.returncode == 0 and ok.stdout == "", f"{ok.stdout!r} {ok.stderr!r}"

    bad = _run_driver(["--spec", dirty], tmp_path, env)
    assert bad.returncode == 1, f"{bad.stdout!r} {bad.stderr!r}"
    lines = bad.stdout.splitlines()
    assert len(lines) == 1, f"one line per blocking finding (advisory ones not printed): {lines!r}"
    assert re.fullmatch(r"SPEC-CITE missing_file \S*no_such_module\.py no_symbol", lines[0]), lines


# ------------------------------------------------------------------------ AC5


def test_ac5_missing_spec_file_and_missing_spec_argument_exit_2(tmp_path):
    assert _DRIVER.is_file(), f"driver {_DRIVER} does not exist"
    env = _hermetic_env(tmp_path)

    gone = _run_driver(["--spec", tmp_path / "no_such_spec.md"], tmp_path, env)
    assert gone.returncode == 2, f"unreadable spec must exit 2: {gone.stdout!r} {gone.stderr!r}"
    assert gone.stdout == "", f"nothing on stdout: {gone.stdout!r}"

    none = _run_driver([], tmp_path, env)
    assert none.returncode == 2, f"a missing --spec must exit 2: {none.stdout!r} {none.stderr!r}"
    assert none.stdout == ""


# ------------------------------------------------------------------------ AC6


def test_ac6_registry_declares_nothing_absent_and_maps_the_cite_driver_to_the_repo_root():
    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    assert precommit_lints.DECLARED_ABSENT == [], precommit_lints.DECLARED_ABSENT

    mapped = precommit_lints.LINT_DRIVER_DIRS.get(_CITE)
    assert mapped is not None and os.path.realpath(mapped) == os.path.realpath(str(_REPO_ROOT)), (
        f"LINT_DRIVER_DIRS[{_CITE!r}] must be the repo root, got {mapped!r}"
    )
    resolved = precommit_lints.driver_path(_CITE, "/nonexistent-lint-dir-bd323")
    assert os.path.realpath(resolved) == os.path.realpath(str(_DRIVER)), resolved
    assert os.path.isfile(resolved), f"resolved driver is not a file: {resolved!r}"

    first = _DRIVER.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/env python3", f"first line must be the shebang: {first!r}"
    assert os.stat(_DRIVER).st_mode & 0o111, "the driver must have an exec bit"

    # bd#66 invariant: every name in SPEC_LINTS has a driver on disk.
    for name in precommit_lints.SPEC_LINTS:
        driver = precommit_lints.driver_path(name, precommit_lints.DEFAULT_LINT_DIR)
        assert os.path.isfile(driver), f"{name}: no driver at {driver}"
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert len(present) > 0, "registry pre-pass must report at least one lint"
    assert violations == [], f"the live registry must pre-pass clean: {violations!r}"
    assert present.get(_CITE) is True, present.get(_CITE)
    assert all(present.get(n) is True for n in precommit_lints.SPEC_LINTS), present


# ------------------------------------------------------------------------ AC7


def test_ac7_the_layer_refuses_a_staged_spec_citing_a_missing_path_and_accepts_its_twin(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env, "ac7dirty")
    _stage(repo, env, "x_spec.md", _build(create_decl=False))

    rc, output = _commit(repo, env)

    assert rc != 0, f"the commit must be refused: {output!r}"
    assert not _has_head(repo, env), f"no commit object may exist after a refusal: {output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, _CITE)) == 1, output
    assert re.search(
        r"^SPEC-CITE missing_file \S*src/widget\.py build_widget$", output, re.MULTILINE
    ), output
    refusals = [ln for ln in output.splitlines() if REFUSE_VIOLATION in ln]
    assert len(refusals) == 1, f"only the cite lint may refuse this fixture: {refusals!r}"

    # Clean twin in the same repo: the CREATE declaration makes the cite planned.
    _stage(repo, env, "x_spec.md", _build(create_decl=True))
    rc, output = _commit(repo, env)
    assert rc == 0 and _has_head(repo, env), f"the clean twin must commit: {output!r}"
    assert "BD66-REFUSE" not in output, output


# ------------------------------------------------------------------------ AC8


def test_ac8_a_declaration_does_not_hide_a_present_driver(tmp_path):
    env = _hermetic_env(tmp_path)
    clone = _package_copy(tmp_path, env, "ac8declared")
    assert (clone / f"{_CITE}.py").is_file()
    _append_registry(clone, f'DECLARED_ABSENT = list(DECLARED_ABSENT) + ["{_CITE}"]')

    _stage(clone, env, "x_spec.md", _build(create_decl=False))
    rc, output = _commit(clone, env)
    assert rc != 0, f"a declaration alone must not suppress a present driver: {output!r}"
    assert not _has_head(clone, env), f"no commit object may exist: {output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, _CITE)) == 1, output

    _stage(clone, env, "x_spec.md", _build(create_decl=True))
    rc, output = _commit(clone, env)
    assert rc == 0 and _has_head(clone, env), f"the clean twin must commit: {output!r}"


def test_ac8_a_deleted_driver_that_is_not_declared_is_a_missing_driver_refusal(tmp_path):
    env = _hermetic_env(tmp_path)
    clone = _package_copy(tmp_path, env, "ac8deleted")
    (clone / f"{_CITE}.py").unlink()

    proc = _run_cli(clone, env)
    output = proc.stdout + proc.stderr

    assert proc.returncode == 1, f"rc={proc.returncode} {output!r}"
    assert REFUSE_MISSING_DRIVER in output and _lint_token(output, _CITE), output
    for other in _OTHER_SPEC_AND_TEST_DRIVERS:
        assert not _lint_token(output, other), f"innocent {other} named: {output!r}"
