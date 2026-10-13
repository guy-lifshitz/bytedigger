"""RED tests for bd#305 -- the layer decides what each spec lint is fed; a
driver crash is not a violation.

Spec: docs/decisions/2026-10-13-bd305-spec-lint-feed.md, sections 2 and 3.

Pre-GREEN: `spec-coverage-lint` is declared absent and unmapped, so the layer
never runs it; text mode of `spec-coverage-lint.py` indexes `f['op']` on the
opt-in findings (KeyError); a driver that crashes with an uncaught exception
exits 1 and is read as a violation; neither switch exists.

Anchors: the real-CLI ACs run the REAL `scripts/precommit_enforce_cli.py` (and
the real drivers it execs) in a temporary git clone, through a one-line
`pre-commit` hook, with a hermetic git environment; a refusal is a real
`git commit` side effect, observed through the object database. Switches are
set only in the child environment of the case.

No sys.path mutation, no conftest import; units under test are reached by
subprocess or by an in-test-body import. ASCII only.
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

from bytedigger_engine import precommit_lints

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_CONFORMANCE = _PACKAGE_DIR / "conformance"
_COVERAGE_DRIVER = _REPO_ROOT / "spec-coverage-lint.py"

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"
REFUSE_DRIVER_ERROR = "BD66-REFUSE-DRIVER-ERROR"
LINT_OFF = "BD66-LINT-OFF"
LINT_OFF_UNKNOWN = "BD66-LINT-OFF-UNKNOWN"
LINT_NOT_FED = "BD66-LINT-NOT-FED"

_COVERAGE = "spec-coverage-lint"
_HELD = ["spec-cite-lint", "token-consistency-lint"]
_LEAVING = [
    "scope-inverse-lint", "helper-extraction-lint", "presence-triad-lint",
    "format-conversion-lint", "closure-evidence-lint", "mutation-two-sidedness-lint",
]
_SHIPPED = _LEAVING + [_COVERAGE, "token-consistency-lint"]
_EXISTING_ROOT_DRIVERS = {
    "core-boundary-lint.py", "cyrillic-prose-lint.py", "fixture-schema-lint.py",
    "forbidden-import-lint.py", "one-sided-predicate-lint.py", "stub-passability-lint.py",
}
_EXISTING_SPECS = [
    "AUTHORSHIP_SPEC.md", "CONTRACTS_SPEC.md", "EMISSIONS_SPEC.md",
    "ORACLE_SPEC.md", "QUANT_LINT_SPEC.md",
]

_NAME_CHAR = r"[0-9A-Za-z_\-]"
_SPEC_TOKEN = "spec_from" + "_file_location"


# --------------------------------------------------------------------- helpers


def _lint_token(output: str, name: str) -> bool:
    pattern = rf"(?<!{_NAME_CHAR})lint={re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


def _names_lint(output: str, name: str) -> bool:
    pattern = rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})"
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
        ("user.email", "bd305@example.com"),
        ("user.name", "bd305 tester"),
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
    result = _git(["commit", "-m", "bd305 fixture"], repo, env)
    return result.returncode, result.stdout + result.stderr


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _line_of(source: str, needle: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture has no line containing {needle!r}")


def _run_driver(driver: Path, args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(driver), *[str(a) for a in args]],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=60, stdin=subprocess.DEVNULL,
    )


def _run_cli(clone: Path, env: dict) -> subprocess.CompletedProcess:
    cli = clone / "scripts" / "precommit_enforce_cli.py"
    return subprocess.run(
        [sys.executable, str(cli)], cwd=str(clone), env=env,
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )


def _exit_stand_in(directory: Path, name: str, code: int = 0) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.exit({code})\n")
    path.chmod(0o755)


_CHECK_MODULES = (
    "scope_inverse", "helper_extraction", "spec_cite", "spec_coverage",
    "token_consistency", "presence_triad", "format_conversion_cases",
    "error_codes",
)
_VERIFIERS = ("closure_evidence_verifier", "mutation_two_sidedness_verifier")


def _package_copy(tmp_path: Path, env: dict, name: str) -> Path:
    """A clone carrying a COPY of the registry, the layer and the real drivers.

    Same pattern as test_bd304_spec_lint_drivers.py: the copied registry
    recomputes its repo root as the clone, so a mapped name is looked for at
    `<clone>/<name>.py`. The eight spec drivers are the real files; the four
    older drivers are exit-0 stand-ins. The clone's hook execs the CLI copy.
    """
    for lint in _SHIPPED:
        assert (_REPO_ROOT / f"{lint}.py").is_file(), f"missing driver for {lint}"
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
    scripts = clone / "scripts"
    scripts.mkdir()
    shutil.copy2(_ENFORCE_CLI, scripts / "precommit_enforce_cli.py")
    for lint in _SHIPPED:
        shutil.copy2(_REPO_ROOT / f"{lint}.py", clone / f"{lint}.py")
    for older in (
        "cyrillic-prose-lint", "one-sided-predicate-lint",
        "forbidden-import-lint", "stub-passability-lint",
    ):
        _exit_stand_in(clone, older, 0)
    _install_hook(clone, scripts / "precommit_enforce_cli.py")
    return clone


def _append_registry(clone: Path, text: str) -> None:
    with (clone / "engine_py" / "bytedigger_engine" / "precommit_lints.py").open("a") as fh:
        fh.write("\n" + text + "\n")


# --------------------------------------------------------------- fixture specs


def _build(
    *, not_in_scope=True, table=True, problem_extra=(), sec2_extra=(),
    table_extra=(),
) -> str:
    """Base spec, clean for all nine lints, with optional single mutations.

    Same base as test_bd304_spec_lint_drivers.py: a Files-in-scope header and
    a not-in-scope block (scope-inverse), one bold op named in the AC table
    (spec-coverage), one CREATE-declared citation (spec-cite), no `$((`, no
    side-effect token in the AC row, no closedness term.
    """
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
        *sec2_extra,
        "",
        "- CREATE: src/widget.py",
        "",
        "### Files in scope",
        "",
        "- src/widget.py: the parser.",
        "",
    ]
    if not_in_scope:
        lines += ["### Files NOT in scope", "", "- every other file.", ""]
    lines += ["## 3. Acceptance", ""]
    if table:
        lines += [
            "| AC | Check |",
            "| --- | --- |",
            "| AC1 | Parser entry returns the value. |",
            *table_extra,
        ]
    lines += [""]
    return "\n".join(lines) + "\n"


_UNCOVERED_OP = "2. **Totals report** prints the totals."
_TOTALS_ROW = "| AC2 | Totals report prints the totals. |"
_TOKEN_LINE = "Use `emit_foo` and `emit.foo` consistently."

_NEW_CODE = "E_BD305_NOVEL_CODE"
_AC_TABLE = """\
## 3. Acceptance criteria

| AC | Criterion |
|----|-----------|
| AC1 | Parse input rejects an empty payload |
"""
_TAXONOMY_SPEC = (
    "# Spec\n\n## 2. Design\n\n"
    "- **Parse input** reads the payload.\n"
    f"- A malformed payload raises {_NEW_CODE}.\n\n"
) + _AC_TABLE
_FINALIZE_BRANCH_SPEC = (
    "# Spec\n\n## 2. Design\n\n"
    "- **Parse input** reads the payload.\n"
    "- A cleanup branch removes the temp directory afterwards.\n\n"
) + _AC_TABLE
_FINALIZE_UNANCHORED_SPEC = (
    "# Spec\n\n## 2. Design\n\n"
    "- **Parse input** reads the payload.\n\n"
) + _AC_TABLE + "| AC2 | The cleanup removes the temp directory |\n"
_ISSUE_TAXONOMY_SPEC = (
    "# Spec\n\n## 2. Design\n\n"
    "- **Parse input** reads the payload.\n"
    "- Bad input raises E_FOO.\n\n"
) + _AC_TABLE


def _scenario(tmp_path: Path, tag: str, spec_text: str, extra_env: dict | None = None,
              rel: str = "x_spec.md") -> tuple[int, str, bool]:
    env = _hermetic_env(tmp_path)
    env.update(extra_env or {})
    repo = _real_clone(tmp_path, env, tag)
    _stage(repo, env, rel, spec_text)
    rc, output = _commit(repo, env)
    return rc, output, _has_head(repo, env)


# ------------------------------------------------------------------------ AC1


def test_ac1_opt_in_findings_have_a_text_line_with_the_rule_label_and_no_traceback(tmp_path):
    env = _hermetic_env(tmp_path)

    # --taxonomy
    spec = tmp_path / "taxonomy_spec.md"
    spec.write_text(_TAXONOMY_SPEC, encoding="utf-8")
    n = _line_of(_TAXONOMY_SPEC, _NEW_CODE)
    proc = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--taxonomy"], tmp_path, env)
    assert proc.returncode == 1, f"rc: {proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "Traceback" not in proc.stderr, f"stderr: {proc.stderr!r}"
    assert re.search(
        rf"^TAXONOMY-MISSING-DISPOSITION line {n}: token={_NEW_CODE} - .+$",
        proc.stdout, re.MULTILINE,
    ), f"stdout: {proc.stdout!r}"
    as_json = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--taxonomy", "--json"], tmp_path, env)
    hits = [
        f for f in json.loads(as_json.stdout)["findings"]
        if f.get("rule") == "spec_taxonomy_missing_disposition"
    ]
    assert [h["line"] for h in hits] == [n], f"json findings: {as_json.stdout!r}"

    # --finalize, uncovered branch
    spec = tmp_path / "finalize_spec.md"
    spec.write_text(_FINALIZE_BRANCH_SPEC, encoding="utf-8")
    n = _line_of(_FINALIZE_BRANCH_SPEC, "cleanup branch")
    proc = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--finalize"], tmp_path, env)
    assert proc.returncode == 1, f"rc: {proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "Traceback" not in proc.stderr, f"stderr: {proc.stderr!r}"
    assert re.search(
        rf"^FINALIZE-BRANCH-UNCOVERED line {n}: group=cleanup - .+$",
        proc.stdout, re.MULTILINE,
    ), f"stdout: {proc.stdout!r}"

    # --finalize, unanchored: an AC row names `cleanup`, section 2 does not.
    spec = tmp_path / "unanchored_spec.md"
    spec.write_text(_FINALIZE_UNANCHORED_SPEC, encoding="utf-8")
    n = _line_of(_FINALIZE_UNANCHORED_SPEC, "The cleanup removes")
    proc = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--finalize"], tmp_path, env)
    assert proc.returncode == 1, f"rc: {proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "Traceback" not in proc.stderr, f"stderr: {proc.stderr!r}"
    assert re.search(
        rf"^FINALIZE-AC-UNANCHORED line {n}: group=cleanup - .+$",
        proc.stdout, re.MULTILINE,
    ), f"stdout: {proc.stdout!r}"


# ------------------------------------------------------------------------ AC2


def test_ac2_the_issue_input_gives_one_finding_line_and_no_keyerror(tmp_path):
    env = _hermetic_env(tmp_path)
    spec = tmp_path / "issue_spec.md"
    spec.write_text(_ISSUE_TAXONOMY_SPEC, encoding="utf-8")

    proc = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--taxonomy"], tmp_path, env)
    assert proc.returncode == 1, f"rc: {proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "KeyError" not in proc.stderr and "Traceback" not in proc.stderr, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"exactly one finding line expected: {proc.stdout!r}"
    assert lines[0].startswith("TAXONOMY-MISSING-DISPOSITION line "), lines[0]
    assert "token=E_FOO" in lines[0], lines[0]

    spec = tmp_path / "issue_finalize_spec.md"
    spec.write_text(_FINALIZE_BRANCH_SPEC, encoding="utf-8")
    proc = _run_driver(_COVERAGE_DRIVER, ["--spec", spec, "--finalize"], tmp_path, env)
    assert proc.returncode == 1, f"rc: {proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "KeyError" not in proc.stderr and "Traceback" not in proc.stderr, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"exactly one finding line expected: {proc.stdout!r}"
    assert lines[0].startswith("FINALIZE-BRANCH-UNCOVERED line "), lines[0]


# ------------------------------------------------------------------------ AC3


def test_ac3_the_layer_feeds_spec_coverage_only_a_spec_with_an_ac_table(tmp_path):
    # (a) no AC table: accepted, with a NOT-FED line, never a refusal.
    rc, output, head = _scenario(tmp_path, "ac3a", _build(table=False))
    assert rc == 0, f"a table-less spec must be accepted: {output!r}"
    assert head, "the commit must exist"
    assert LINT_NOT_FED in output, f"missing {LINT_NOT_FED}: {output!r}"
    assert _lines_with(output, LINT_NOT_FED, _COVERAGE), f"not naming the lint: {output!r}"
    assert "BD66-REFUSE" not in output, f"no refusal expected: {output!r}"

    # (b) AC table and an uncovered bold op: refused for the lint.
    dirty = _build(sec2_extra=[_UNCOVERED_OP])
    n = _line_of(dirty, "**Totals report**")
    rc, output, head = _scenario(tmp_path, "ac3b", dirty)
    assert rc != 0 and not head, f"must be refused: rc={rc} output={output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, _COVERAGE)) == 1, output
    assert re.search(rf"^UNCOVERED-OP line {n}: ", output, re.MULTILINE), output
    assert LINT_NOT_FED not in output, f"a fed spec has no NOT-FED line: {output!r}"

    # (c) AC table, op covered: accepted, no NOT-FED line.
    rc, output, head = _scenario(
        tmp_path, "ac3c", _build(sec2_extra=[_UNCOVERED_OP], table_extra=[_TOTALS_ROW])
    )
    assert rc == 0 and head, f"covered op must be accepted: {output!r}"
    assert LINT_NOT_FED not in output, output

    # (d) each of the five existing specs, one commit each: accepted.
    existing = sorted(p.name for p in _CONFORMANCE.glob("*_SPEC.md"))
    assert existing == _EXISTING_SPECS, f"existing specs changed: {existing!r}"
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env, "ac3d")
    for name in existing:
        text = (_CONFORMANCE / name).read_text(encoding="utf-8")
        _stage(repo, env, name, text)
        rc, output = _commit(repo, env)
        assert rc == 0, f"{name} must be accepted by the layer: {output!r}"
        assert "BD66-REFUSE" not in output, f"{name}: {output!r}"


# ------------------------------------------------------------------------ AC4


def test_ac4_the_two_held_lints_stay_held_and_the_registry_stays_closed(tmp_path):
    assert set(precommit_lints.DECLARED_ABSENT) == set(_HELD), (
        f"DECLARED_ABSENT must be exactly {_HELD!r}, got {precommit_lints.DECLARED_ABSENT!r}"
    )
    assert len(precommit_lints.DECLARED_ABSENT) == 2, precommit_lints.DECLARED_ABSENT

    # Token drift is not flagged by the layer: the lint is held.
    rc, output, head = _scenario(tmp_path, "ac4drift", _build(problem_extra=[_TOKEN_LINE]))
    assert rc == 0 and head, f"token drift must be accepted: {output!r}"
    assert not _lint_token(output, "token-consistency-lint"), output

    # No cite driver at the root; the path phase_45_spec computes is not a file.
    from bytedigger_engine.workflows import phase_45_spec  # noqa: PLC0415

    computed = Path(phase_45_spec.__file__).parent.parent.parent.parent / "spec-cite-lint.py"
    assert not computed.is_file(), f"{computed} would switch on a blocking /build gate"
    assert not (_REPO_ROOT / "spec-cite-lint.py").exists()
    on_disk = {p.name for p in _REPO_ROOT.glob("*-lint.py")}
    expected = _EXISTING_ROOT_DRIVERS | {f"{n}.py" for n in _SHIPPED}
    assert on_disk == expected, (
        f"root *-lint.py set changed: extra={sorted(on_disk - expected)!r} "
        f"missing={sorted(expected - on_disk)!r}"
    )

    # Closed both ways, in a package copy.
    env = _hermetic_env(tmp_path)
    for name in _HELD:
        clone = _package_copy(tmp_path, env, f"ac4-undeclare-{name}")
        _append_registry(
            clone, f'DECLARED_ABSENT = [n for n in DECLARED_ABSENT if n != "{name}"]'
        )
        proc = _run_cli(clone, env)
        output = proc.stdout + proc.stderr
        assert proc.returncode == 1, f"{name}: rc={proc.returncode} {output!r}"
        assert REFUSE_MISSING_DRIVER in output and _lint_token(output, name), output

    clone = _package_copy(tmp_path, env, "ac4-deleted-coverage")
    (clone / f"{_COVERAGE}.py").unlink()
    proc = _run_cli(clone, env)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"rc={proc.returncode} {output!r}"
    assert REFUSE_MISSING_DRIVER in output and _lint_token(output, _COVERAGE), output


# ------------------------------------------------------------------------ AC5


_BOTH_VIOLATED = _build(not_in_scope=False, sec2_extra=[_UNCOVERED_OP])


def _off_lines(output: str, name: str) -> list[str]:
    return re.findall(
        rf"^{LINT_OFF}: lint={re.escape(name)} file=\S+\s*$", output, re.MULTILINE
    )


def test_ac5_switch_one_turns_a_named_spec_lint_off_and_nothing_else(tmp_path):
    dirty = _build(sec2_extra=[_UNCOVERED_OP])

    # on: the AC3(b) spec is accepted, with an OFF line.
    rc, output, head = _scenario(tmp_path, "ac5on", dirty, {"BD_SPEC_LINTS_OFF": _COVERAGE})
    assert rc == 0 and head, f"must be accepted with the lint off: {output!r}"
    assert len(_off_lines(output, _COVERAGE)) == 1, f"missing OFF line: {output!r}"
    assert not _lines_with(output, REFUSE_VIOLATION, _COVERAGE), output

    # off by default: unset, empty and whitespace-only keep the lint running.
    for tag, extra in (("unset", {}), ("empty", {"BD_SPEC_LINTS_OFF": ""}),
                       ("blank", {"BD_SPEC_LINTS_OFF": "   "})):
        rc, output, head = _scenario(tmp_path, f"ac5-{tag}", dirty, extra)
        assert rc != 0 and not head, f"{tag}: must be refused: {output!r}"
        assert len(_lines_with(output, REFUSE_VIOLATION, _COVERAGE)) == 1, output
        assert LINT_OFF not in output, f"{tag}: {output!r}"

    # two names, with and without spaces: both off, one OFF line per lint.
    for tag, value in (("tight", f"scope-inverse-lint,{_COVERAGE}"),
                       ("spaced", f" scope-inverse-lint , {_COVERAGE} ")):
        rc, output, head = _scenario(
            tmp_path, f"ac5-two-{tag}", _BOTH_VIOLATED, {"BD_SPEC_LINTS_OFF": value}
        )
        assert rc == 0 and head, f"{tag}: both off must accept: {output!r}"
        assert len(_off_lines(output, "scope-inverse-lint")) == 1, output
        assert len(_off_lines(output, _COVERAGE)) == 1, output

    # only spec-coverage off: a scope-inverse violation is still refused.
    rc, output, head = _scenario(
        tmp_path, "ac5-only", _BOTH_VIOLATED, {"BD_SPEC_LINTS_OFF": _COVERAGE}
    )
    assert rc != 0 and not head, f"scope-inverse must still refuse: {output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, "scope-inverse-lint")) == 1, output
    assert not _lines_with(output, REFUSE_VIOLATION, _COVERAGE), output
    assert len(_off_lines(output, _COVERAGE)) == 1, output


def test_ac5_an_unknown_name_is_reported_once_and_disables_nothing(tmp_path):
    # a test lint name is not a spec off switch; the switch is spec-only.
    source = "\n".join([
        "import importlib.util",
        "",
        "",
        "def test_loads_a_module():",
        f"    spec = importlib.util.{_SPEC_TOKEN}('m', 'm.py')",
        "    assert spec is not None",
        "",
    ])
    rc, output, head = _scenario(
        tmp_path, "ac5-unknown-test", source, {"BD_SPEC_LINTS_OFF": "forbidden-import-lint"},
        rel="test_x.py",
    )
    assert rc != 0 and not head, f"the test lint must still refuse: {output!r}"
    assert re.findall(
        rf"^{LINT_OFF_UNKNOWN}: name=forbidden-import-lint\s*$", output, re.MULTILINE
    ) == [f"{LINT_OFF_UNKNOWN}: name=forbidden-import-lint"], output
    assert len(_lines_with(output, REFUSE_VIOLATION, "forbidden-import-lint")) == 1, output

    # a typo disables nothing.
    rc, output, head = _scenario(
        tmp_path, "ac5-typo", _build(sec2_extra=[_UNCOVERED_OP]),
        {"BD_SPEC_LINTS_OFF": "spec-coverage-lnt"},
    )
    assert rc != 0 and not head, f"a typo must disable nothing: {output!r}"
    assert f"{LINT_OFF_UNKNOWN}: name=spec-coverage-lnt" in output, output
    assert len(_lines_with(output, REFUSE_VIOLATION, _COVERAGE)) == 1, output


# ------------------------------------------------------------------------ AC6

_CRASH_DRIVER = (
    "#!/usr/bin/env python3\n"
    "raise RuntimeError('bd305 driver crash')\n"
)
_VIOLATION_DRIVER = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "print('bd305-finding line 1: a real finding')\n"
    "sys.exit(1)\n"
)
_ERROR_DRIVER = "#!/usr/bin/env python3\nimport sys\nsys.exit(2)\n"
_HELPER = "helper-extraction-lint"


def _driver_case(tmp_path: Path, tag: str, body: str, extra_env: dict) -> tuple[int, str, bool]:
    env = _hermetic_env(tmp_path)
    env.update(extra_env)
    clone = _package_copy(tmp_path, env, tag)
    replaced = clone / f"{_HELPER}.py"
    replaced.write_text(body)
    replaced.chmod(0o755)
    _stage(clone, env, "x_spec.md", _build())
    rc, output = _commit(clone, env)
    return rc, output, _has_head(clone, env)


def _assert_crash_is_driver_error(rc: int, output: str, head: bool, label: str) -> None:
    assert rc != 0 and not head, f"{label}: must be refused: rc={rc} {output!r}"
    errors = _lines_with(output, REFUSE_DRIVER_ERROR, _HELPER)
    assert len(errors) == 1, f"{label}: one driver-error line expected: {output!r}"
    assert "rc=1" in errors[0] and "crashed" in errors[0], f"{label}: {errors[0]!r}"
    assert not _lines_with(output, REFUSE_VIOLATION, _HELPER), f"{label}: {output!r}"
    assert "Traceback (most recent call last)" in output and "RuntimeError" in output, (
        f"{label}: the traceback must still reach stderr: {output!r}"
    )


def test_ac6_a_driver_crash_is_a_driver_error_and_switch_two_restores_the_old_reading(tmp_path):
    # default (no variable): a crash is a driver error.
    rc, output, head = _driver_case(tmp_path, "ac6-default", _CRASH_DRIVER, {})
    _assert_crash_is_driver_error(rc, output, head, "default")

    # exactly "1": the pre-#305 reading, a violation.
    rc, output, head = _driver_case(
        tmp_path, "ac6-one", _CRASH_DRIVER, {"BD_LINT_CRASH_IS_VIOLATION": "1"}
    )
    assert rc != 0 and not head, f"=1: must be refused: {output!r}"
    assert len(_lines_with(output, REFUSE_VIOLATION, _HELPER)) == 1, output
    assert not _lines_with(output, REFUSE_DRIVER_ERROR, _HELPER), output

    # "0" and "" behave as the default.
    for tag, value in (("zero", "0"), ("empty", "")):
        rc, output, head = _driver_case(
            tmp_path, f"ac6-{tag}", _CRASH_DRIVER, {"BD_LINT_CRASH_IS_VIOLATION": value}
        )
        _assert_crash_is_driver_error(rc, output, head, f"={value!r}")

    # a real violation (exit 1, findings, no traceback): violation under both.
    for tag, extra in (("viol-default", {}), ("viol-one", {"BD_LINT_CRASH_IS_VIOLATION": "1"})):
        rc, output, head = _driver_case(tmp_path, f"ac6-{tag}", _VIOLATION_DRIVER, extra)
        assert rc != 0 and not head, f"{tag}: {output!r}"
        assert len(_lines_with(output, REFUSE_VIOLATION, _HELPER)) == 1, f"{tag}: {output!r}"
        assert not _lines_with(output, REFUSE_DRIVER_ERROR, _HELPER), f"{tag}: {output!r}"

    # a driver exiting 2 is a driver error under both.
    for tag, extra in (("two-default", {}), ("two-one", {"BD_LINT_CRASH_IS_VIOLATION": "1"})):
        rc, output, head = _driver_case(tmp_path, f"ac6-{tag}", _ERROR_DRIVER, extra)
        assert rc != 0 and not head, f"{tag}: {output!r}"
        errors = _lines_with(output, REFUSE_DRIVER_ERROR, _HELPER)
        assert len(errors) == 1 and "rc=2" in errors[0], f"{tag}: {output!r}"
        assert not _lines_with(output, REFUSE_VIOLATION, _HELPER), f"{tag}: {output!r}"


# ------------------------------------------------------------------------ AC8


def test_ac8_text_mode_has_no_unconditional_op_subscript():
    tree = ast.parse(_COVERAGE_DRIVER.read_text(encoding="utf-8"))
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def _is_op_key(sub: ast.Subscript) -> bool:
        key = sub.slice
        return isinstance(key, ast.Constant) and key.value == "op"

    def _conditional(node: ast.AST) -> bool:
        # A guard counts only when it sits between the subscript and the loop
        # (or function) that iterates the findings; the `if args.json_output`
        # branch around the whole loop is not a guard on a finding.
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.For, ast.AsyncFor, ast.comprehension,
                                ast.FunctionDef, ast.AsyncFunctionDef)):
                return False
            if isinstance(cur, (ast.If, ast.IfExp, ast.BoolOp, ast.Try)):
                return True
            cur = parents.get(id(cur))
        return False

    blind = [
        ast.unparse(sub)
        for sub in ast.walk(tree)
        if isinstance(sub, ast.Subscript) and _is_op_key(sub) and not _conditional(sub)
    ]
    assert blind == [], f"unconditional subscript of the 'op' key on a finding: {blind!r}"


# ------------------------------------------------------------------------ AC9


def _package_root(path: Path) -> Path:
    """Top directory of the unbroken __init__.py chain above the spec."""
    top = path.parent
    current = path.parent
    while (current / "__init__.py").is_file():
        top = current
        current = current.parent
    return top


def test_ac9_measurement_pin_the_held_lints_still_have_findings_and_coverage_is_not_fed():
    from bytedigger_engine import spec_cite, token_consistency  # noqa: PLC0415

    specs = sorted(_CONFORMANCE.glob("*_SPEC.md"))
    assert [p.name for p in specs] == _EXISTING_SPECS, f"existing specs: {specs!r}"
    texts = {p: p.read_text(encoding="utf-8") for p in specs}

    drift = {p.name: token_consistency.scan_token_consistency(t) for p, t in texts.items()}
    assert any(drift.values()), (
        "token-consistency is now clean on the five existing specs: the "
        "measurement that held it back is stale, so #305's remainder can flip it"
    )

    blocking_both = []
    for p in specs:
        code_repo, _ = spec_cite.lint_spec(p, _REPO_ROOT)
        code_pkg, _ = spec_cite.lint_spec(p, _package_root(p))
        if code_repo != 0 and code_pkg != 0:
            blocking_both.append(p.name)
    assert blocking_both, (
        "spec-cite is now clean on the five existing specs under repo-root and "
        "package-root resolution: the measurement that held it back is stale, "
        "so #305's remainder can flip it"
    )

    feed_skip_reason = getattr(precommit_lints, "feed_skip_reason", None)
    assert feed_skip_reason is not None, "precommit_lints.feed_skip_reason does not exist"
    fed = [p.name for p in specs if feed_skip_reason(_COVERAGE, str(p)) is None]
    assert fed == [], f"the layer must feed spec-coverage-lint to none of them, fed: {fed!r}"
