"""RED tests for bd#304 -- drivers for eight of the nine SPEC_LINTS.

Spec: docs/decisions/2026-10-13-bd304-spec-lint-drivers.md

Pre-GREEN, all nine spec lints are listed in `precommit_lints.DECLARED_ABSENT`
and none has a driver file, so a staged `*_spec.md` is committed with no
verdict. Six of them (scope-inverse, helper-extraction, presence-triad,
format-conversion, closure-evidence, mutation-two-sidedness) get a driver that
the layer runs; two (spec-coverage, token-consistency) get a driver on disk
that stays unmapped, so the layer reports them declared absent and does not run
them; spec-cite gets NO driver file (a live consumer in phase_45_spec would
turn it into a blocking /build gate, see spec 2.7) and stays declared absent;
its lint is shown through `spec_cite.lint_spec`. Every test here fails today
because the eight drivers at the repository root do not exist (and, for the
registry ACs, because the registry still declares all nine absent).

Anchors: the real-CLI ACs run the REAL `scripts/precommit_enforce_cli.py` (and
the real drivers it execs) in a temporary git clone, through a one-line
`pre-commit` hook, with a hermetic git environment. The load-bearing
observation is the git object database: after a refused commit
`git rev-parse --verify HEAD` must fail. Every refusal is paired with the
acceptance of a clean twin in the same repo, so an always-0 driver dies on the
refusal and an always-1 driver dies on the twin.

Fixtures: one clean base spec (`_build()`), and per lint a minimal mutation of
it that the checking module flags, each mutation clean for the other eight
lints. The module rule that fires is named next to each fixture.

No sys.path mutation, no conftest import; the units under test are reached by
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

import pytest

from bytedigger_engine import precommit_lints

_PACKAGE_DIR = Path(precommit_lints.__file__).resolve().parent
_ENGINE_ROOT = _PACKAGE_DIR.parent
_REPO_ROOT = _ENGINE_ROOT.parent
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"

_LEAVING = [
    "scope-inverse-lint",
    "helper-extraction-lint",
    "presence-triad-lint",
    "format-conversion-lint",
    "closure-evidence-lint",
    "mutation-two-sidedness-lint",
]
# bd#305: spec-coverage left the held group (the layer feeds it a spec with an
# AC table); bd#315: token-consistency left it too. One name stays held.
_HELD = ["spec-cite-lint"]
# The lints the layer runs: the six of #304, spec-coverage (#305) and
# token-consistency (#315).
_LAYER = _LEAVING + ["spec-coverage-lint", "token-consistency-lint"]
_ALL_NINE = _LAYER + _HELD
# Held names that DO have a dormant driver file at the repo root (unmapped):
# none since bd#315.
_DORMANT: list[str] = []
# The eight drivers this lot ships (spec-cite has none).
_SHIPPED = _LEAVING + ["spec-coverage-lint", "token-consistency-lint"]

_NAME_CHAR = r"[0-9A-Za-z_\-]"


# --------------------------------------------------------------------- helpers


def _names_lint(output: str, name: str) -> bool:
    """True iff `name` appears in `output` as a whole lint-name token."""
    pattern = rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


def _lint_token(output: str, name: str) -> bool:
    """True iff `lint=<name>` appears in `output` with `name` a whole token."""
    pattern = rf"(?<!{_NAME_CHAR})lint={re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


def _driver(name: str) -> Path:
    return _REPO_ROOT / f"{name}.py"


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
        ("user.email", "bd304@example.com"),
        ("user.name", "bd304 tester"),
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
    result = _git(["commit", "-m", "bd304 fixture"], repo, env)
    return result.returncode, result.stdout + result.stderr


def _has_head(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _line_of(source: str, needle: str) -> int:
    """1-based number of the first line containing `needle`."""
    for number, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"fixture has no line containing {needle!r}")


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

    The copied registry recomputes its repo root as the clone, so a name mapped
    in `LINT_DRIVER_DIRS` is looked for at `<clone>/<name>.py`. The eight drivers
    under test are copied from the real tree (never stand-ins), together with
    the checking modules they import; spec-cite has no driver file anywhere, so
    nothing is copied for it; the four older drivers are exit-0 stand-ins, they
    are not what this lot is about. The clone's hook execs the CLI copy, so a
    refusal is a real `git commit` side effect.
    """
    for lint in _SHIPPED:
        assert _driver(lint).is_file(), f"bd#304: the driver {_driver(lint)} does not exist yet"
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
        shutil.copy2(_driver(lint), clone / f"{lint}.py")
    for older in (
        "cyrillic-prose-lint", "one-sided-predicate-lint",
        "forbidden-import-lint", "stub-passability-lint",
        "empty-input-assert-lint",  # bd#308: mapped by the copied registry
    ):
        _exit_stand_in(clone, older, 0)
    _install_hook(clone, scripts / "precommit_enforce_cli.py")
    return clone


def _append_registry(clone: Path, text: str) -> None:
    with (clone / "engine_py" / "bytedigger_engine" / "precommit_lints.py").open("a") as fh:
        fh.write("\n" + text + "\n")


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


# --------------------------------------------------------------- fixture specs


def _build(
    *, not_in_scope=True, create_decl=True, table=True,
    problem_extra=(), sec2_extra=(), sec23=(), table_extra=(), tail=(),
) -> str:
    """The base spec (all nine lints clean) with optional single mutations.

    Why the base is clean for each lint:
      scope-inverse      has a Files-in-scope header AND a 'not in scope' block;
      helper-extraction  section 2 has no '$((' and no emit_event/emit_signal;
      spec-cite          the one citation (`build_widget` in src/widget.py) is a
                         declared CREATE target -> planned_file, advisory;
      spec-coverage      has an AC table and the one bold op is named in it;
      token-consistency  one backtick token, one written form;
      presence-triad     the only AC row has no side-effect token;
      format-conversion  no 'format conversion' trigger phrase;
      closure-evidence   no closedness term;
      mutation           no mutation declaration.
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
    ]
    # The CREATE declaration precedes the 2.3 block: the format-conversion rule
    # reads every line up to the next heading as the 2.3 body and the word
    # 'create' there would count as a covered writer case.
    if create_decl:
        lines += ["- CREATE: src/widget.py", ""]
    lines += [*sec23]
    lines += ["### Files in scope", "", "- src/widget.py: the parser.", ""]
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
    lines += ["", *tail]
    return "\n".join(lines) + "\n"


_HELPER_LINE = "- total=$((a + b))"
_CLAIM_LINE = "AC1 is a closed set of parser outputs, checked by the run."
_MUT_RED = "A mutation check removes the guard and the test must fail."
_MUT_TWO = _MUT_RED[:-1] + "; the unmutated baseline passes."
_JSONL_RED = "| AC2 | The parser writes a jsonl record. |"
_JSONL_TRIAD = (
    "| AC2 | The parser writes a jsonl record. Point: parse. "
    "Host: src/widget.py. Test-path: tests/test_w.py. |"
)
_FORMAT_RED = "The writer performs the format conversion of the stored value."
_UNCOVERED_OP = "2. **Totals report** prints the totals."
_TOKEN_LINE = "Use `emit_foo` and `emit.foo` consistently."


def _cases() -> dict:
    """Per-lint fixture records for the eight lints that run on `_build` specs.

    dirty/clean : the violating spec and its minimal compliant twin
    line        : 1-based line of the violation (read from the fixture)
    text_re     : regex a driver stdout line must fully match
    json_*      : the --json item contract (exact keys; values exact, prefix
                  or substring)
    """
    cases: dict = {}

    # scope-inverse: scope_inverse.scan_scope_inverse fires when a header
    # matching '^#+.*files in scope' exists and 'not in scope' appears nowhere.
    dirty = _build(not_in_scope=False)
    n = _line_of(dirty, "### Files in scope")
    cases["scope-inverse-lint"] = dict(
        dirty=dirty, clean=_build(), line=n,
        text_re=rf"SCOPE-INVERSE-MISSING line {n}: .+",
        json_exact={"line": n},
        json_prefix={"reason": "spec has a Files-in-scope section"},
        json_contains={},
    )

    # helper-extraction: helper_extraction.scan_helper_extraction flags an
    # inline '$((' inside section 2 unless 'declare -f' appears in the spec.
    dirty = _build(sec2_extra=[_HELPER_LINE])
    n = _line_of(dirty, "$((")
    cases["helper-extraction-lint"] = dict(
        dirty=dirty,
        clean=_build(sec2_extra=[_HELPER_LINE, "- see declare -f for the helper."]),
        line=n,
        text_re=re.escape(f"UNGUARDED-HELPER line {n}: inline-math - {_HELPER_LINE}"),
        json_exact={"line": n, "construct": "inline-math", "text": _HELPER_LINE},
        json_prefix={}, json_contains={},
    )

    # presence-triad: presence_triad.scan_presence_triad flags an AC row in
    # section 3 that carries a side-effect token (jsonl) and lacks
    # Point:/Host:/Test-path:.
    dirty = _build(table_extra=[_JSONL_RED])
    n = _line_of(dirty, "jsonl")
    cases["presence-triad-lint"] = dict(
        dirty=dirty, clean=_build(table_extra=[_JSONL_TRIAD]), line=n,
        text_re=re.escape(f"TRIAD-MISSING line {n}: missing Point:,Host:,Test-path:"),
        json_exact={
            "line": n, "rule_id": "presence-triad-missing",
            "missing": ["Point:", "Host:", "Test-path:"],
            "evidence": "missing Point:,Host:,Test-path:",
        },
        json_prefix={}, json_contains={},
    )

    # format-conversion: format_conversion_cases.scan_format_conversion flags a
    # spec with the trigger phrase whose '2.3' section lacks the four case
    # words; the finding line is the 2.3 header line.
    dirty = _build(sec23=["### 2.3 Writer behavior", "", _FORMAT_RED, ""])
    n = _line_of(dirty, "### 2.3 Writer behavior")
    clean = _build(sec23=[
        "### 2.3 Writer behavior", "",
        "Cases: append, migrate, refuse, create. This is a format conversion.", "",
    ])
    cases["format-conversion-lint"] = dict(
        dirty=dirty, clean=clean, line=n,
        text_re=re.escape(
            f"FORMAT-CASES line {n}: missing cases: append,migrate,refuse,create"
        ),
        json_exact={
            "line": n, "rule_id": "format-conversion-missing-cases",
            "missing": ["append", "migrate", "refuse", "create"],
            "evidence": "missing cases: append,migrate,refuse,create",
        },
        json_prefix={}, json_contains={},
    )

    # closure-evidence: closure_evidence_verifier.find_unbacked_closure_claims
    # flags a line with an AC id, a closedness term ('closed set') and a set
    # noun ('set') and no declaration naming that AC; rule closure-claim-unbacked.
    dirty = _build(tail=[_CLAIM_LINE, ""])
    n = _line_of(dirty, "closed set")
    cases["closure-evidence-lint"] = dict(
        dirty=dirty,
        clean=_build(tail=[_CLAIM_LINE, "closure-evidence: AC1 <- stdout", ""]),
        line=n,
        text_re=rf"closure-claim-unbacked line {n}: .*closed set.*",
        json_exact={"line": n, "rule_id": "closure-claim-unbacked"},
        json_prefix={"evidence": "closedness claim on AC1"},
        json_contains={"evidence": _CLAIM_LINE},
    )

    # mutation-two-sidedness: mutation_two_sidedness_verifier.
    # find_one_sided_mutations flags a unit anchored on a mutation declaration
    # that names a red outcome ('must fail') and no green outcome; rule
    # one-sided-mutation-declaration, evidence = the anchor line.
    dirty = _build(tail=[_MUT_RED, ""])
    n = _line_of(dirty, "mutation check")
    cases["mutation-two-sidedness-lint"] = dict(
        dirty=dirty, clean=_build(tail=[_MUT_TWO, ""]), line=n,
        text_re=re.escape(f"one-sided-mutation-declaration line {n}: {_MUT_RED}"),
        json_exact={
            "line": n, "rule_id": "one-sided-mutation-declaration",
            "evidence": _MUT_RED,
        },
        json_prefix={}, json_contains={},
    )

    # token-consistency (held): token_consistency.scan_token_consistency flags
    # two written forms of one backtick token (emit_foo vs emit.foo).
    dirty = _build(problem_extra=[_TOKEN_LINE])
    n = _line_of(dirty, "emit_foo")
    cases["token-consistency-lint"] = dict(
        dirty=dirty, clean=_build(), line=n,
        text_re=re.escape(f"TOKEN-INCONSISTENCY line {n}: emit.foo vs emit_foo"),
        json_exact={
            "line": n, "rule_id": "token-inconsistency",
            "variants": ["emit.foo", "emit_foo"], "evidence": "emit.foo vs emit_foo",
        },
        json_prefix={}, json_contains={},
    )

    # spec-coverage (held): spec_coverage.scan_spec_coverage flags a bold op in
    # a section-2 list that no AC table cell names.
    dirty = _build(sec2_extra=[_UNCOVERED_OP])
    n = _line_of(dirty, "**Totals report**")
    cases["spec-coverage-lint"] = dict(
        dirty=dirty, clean=_build(), line=n,
        text_re=rf"UNCOVERED-OP line {n}: op=['\"]?Totals report['\"]? - no covering AC",
        json_exact={"op": "Totals report", "line": n, "reason": "no covering AC"},
        json_prefix={}, json_contains={},
    )
    return cases


_CASES = _cases()


def _assert_item(item: dict, case: dict) -> None:
    expected_keys = set(case["json_exact"]) | set(case["json_prefix"]) | set(case["json_contains"])
    assert set(item) == expected_keys, f"item keys {sorted(item)!r} != {sorted(expected_keys)!r}"
    for key, value in case["json_exact"].items():
        assert item[key] == value, f"{key}: {item[key]!r} != {value!r}"
    for key, prefix in case["json_prefix"].items():
        assert str(item[key]).startswith(prefix), f"{key}: {item[key]!r} lacks prefix {prefix!r}"
    for key, part in case["json_contains"].items():
        assert part in str(item[key]), f"{key}: {item[key]!r} lacks {part!r}"


def _text_line_matches(stdout: str, pattern: str) -> bool:
    return any(re.fullmatch(pattern, ln) for ln in stdout.splitlines())


# ------------------------------------------------------------------------ AC1


@pytest.mark.parametrize("name", _LAYER)
def test_ac1_violating_staged_spec_is_refused_by_a_real_commit_and_its_twin_is_accepted(
    tmp_path, name
):
    case = _CASES[name]
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    _stage(repo, env, "x_spec.md", case["dirty"])

    rc, output = _commit(repo, env)

    assert rc != 0, f"the commit must be refused, got rc=0; output={output!r}"
    assert not _has_head(repo, env), (
        f"no commit object may exist after a refusal; output={output!r}"
    )
    assert REFUSE_VIOLATION in output, f"missing {REFUSE_VIOLATION}: {output!r}"
    assert _lint_token(output, name), f"lint={name} not a whole token: {output!r}"
    assert re.search(rf"^{case['text_re']}$", output, re.MULTILINE), (
        f"driver line for {name} at line {case['line']} missing: {output!r}"
    )
    refusals = [ln for ln in output.splitlines() if REFUSE_VIOLATION in ln]
    assert len(refusals) == 1 and _lint_token(refusals[0], name), (
        f"the fixture must violate exactly this lint, got {refusals!r}"
    )

    # Clean twin in the same repo: the minimal compliant variant is accepted.
    _stage(repo, env, "x_spec.md", case["clean"])
    rc_twin, out_twin = _commit(repo, env)
    assert rc_twin == 0, f"the clean twin must be accepted; output={out_twin!r}"
    assert _has_head(repo, env), "the clean twin commit must exist"


# ------------------------------------------------------------------------ AC2


_HELD_STAGED = {
    # spec_cite.lint_spec (no driver): the cited file is neither on disk nor
    # declared by a CREATE line -> missing_file, a blocking status -> non-zero.
    "spec-cite-lint": (_build(create_decl=False), 1, None),
}


@pytest.mark.parametrize("name", _HELD)
def test_ac2_held_lint_flags_the_spec_directly_but_the_layer_does_not_run_it(
    tmp_path, name
):
    spec_text, direct_rc, direct_err = _HELD_STAGED[name]
    driver = _driver(name)
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    _stage(repo, env, "x_spec.md", spec_text)

    # The eight others left DECLARED_ABSENT (bd#305, bd#315): one name is held.
    assert set(precommit_lints.DECLARED_ABSENT) == set(_HELD), (
        f"DECLARED_ABSENT must be exactly the one held name, got "
        f"{precommit_lints.DECLARED_ABSENT!r}"
    )
    assert len(precommit_lints.DECLARED_ABSENT) == 1, precommit_lints.DECLARED_ABSENT

    if name in _DORMANT:
        assert driver.is_file(), f"the held driver {driver} must exist on disk"
        assert os.access(driver, os.X_OK), f"{driver} must be executable"
        direct = _run_driver(driver, ["--spec", "x_spec.md"], repo, env)
        assert direct.returncode == direct_rc, (
            f"run directly the driver must exit {direct_rc}: {direct.stdout!r} {direct.stderr!r}"
        )
        if direct_err:
            assert direct_err in direct.stderr, f"stderr: {direct.stderr!r}"
    else:
        # spec-cite: no driver file; the lint is shown through the library call
        # the (withheld) driver would make.
        assert not driver.exists(), f"bd#304 withholds the cite driver, found {driver}"
        from bytedigger_engine import spec_cite  # noqa: PLC0415

        code, findings = spec_cite.lint_spec(repo / "x_spec.md", repo)
        assert code != 0 and any(
            getattr(f, "status", None) == "missing_file" for f in findings
        ), (
            f"spec_cite.lint_spec must flag a cite of a missing file: {code!r} {findings!r}"
        )

    rc, output = _commit(repo, env)
    assert rc == 0, f"a held lint must not be run by the layer; output={output!r}"
    assert _has_head(repo, env), "the commit must exist"
    assert not _lint_token(output, name), f"lint={name} must not appear: {output!r}"

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    assert name in precommit_lints.DECLARED_ABSENT, f"{name} must stay declared absent"
    resolved = precommit_lints.driver_path(name, precommit_lints.DEFAULT_LINT_DIR)
    assert not os.path.isfile(resolved), (
        f"driver_path must resolve a held name to the package dir where no file "
        f"exists, got {resolved!r}"
    )
    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == [], f"the live registry must pre-pass clean: {violations!r}"
    assert present.get(name) is False, f"{name} must be marked not present: {present.get(name)!r}"


def test_ac2_spec_coverage_is_run_by_the_layer_on_a_spec_with_an_ac_table_only(tmp_path):
    # bd#305: moved here from the held group. The layer feeds the lint only a
    # spec with an AC table: a table-less spec is accepted with a NOT-FED line,
    # a spec with an uncovered op is refused.
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env, "coverage-no-table")
    _stage(repo, env, "x_spec.md", _build(table=False))
    rc, output = _commit(repo, env)
    assert rc == 0, f"a table-less spec must be accepted; output={output!r}"
    assert _has_head(repo, env), "the commit must exist"
    assert "BD66-LINT-NOT-FED" in output, f"missing NOT-FED line: {output!r}"
    assert _lint_token(output, "spec-coverage-lint"), f"lint not named: {output!r}"

    case = _CASES["spec-coverage-lint"]
    repo = _real_clone(tmp_path, env, "coverage-uncovered")
    _stage(repo, env, "x_spec.md", case["dirty"])
    rc, output = _commit(repo, env)
    assert rc != 0, f"an uncovered op must be refused; output={output!r}"
    assert not _has_head(repo, env), f"no commit object may exist; output={output!r}"
    assert REFUSE_VIOLATION in output and _lint_token(output, "spec-coverage-lint"), output
    assert re.search(rf"^{case['text_re']}$", output, re.MULTILINE), output


# ------------------------------------------------------------------------ AC3


def test_ac3_clean_staged_spec_commits_and_exactly_the_seven_are_planned_with_a_driver(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _real_clone(tmp_path, env)
    _stage(repo, env, "x_spec.md", _build())

    rc, output = _commit(repo, env)
    assert rc == 0, f"a clean staged spec must commit; output={output!r}"
    assert _has_head(repo, env)

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    commands = precommit_lints.build_lint_commands(
        ["x_spec.md"], [], precommit_lints.DEFAULT_LINT_DIR
    )
    by_lint = {c["lint"]: c for c in commands}
    for name in _ALL_NINE:
        assert name in by_lint, f"no planned command for {name}: {commands!r}"
    for name in _LAYER:
        argv0 = Path(by_lint[name]["argv"][0])
        assert argv0.is_file(), f"argv[0] for {name} must exist, got {argv0}"
        assert os.access(argv0, os.X_OK), f"argv[0] for {name} must be executable"
    for name in _HELD:
        argv0 = Path(by_lint[name]["argv"][0])
        assert not argv0.is_file(), f"argv[0] for held {name} must not exist, got {argv0}"

    present, violations = precommit_enforce.registry_prepass(precommit_lints.DEFAULT_LINT_DIR)
    assert violations == [], f"the live registry must pre-pass clean: {violations!r}"
    marked = {n for n in precommit_lints.SPEC_LINTS if present.get(n) is True}
    assert marked == set(_LAYER), f"pre-pass must mark exactly the seven present, got {sorted(marked)!r}"


# ------------------------------------------------------------------------ AC4


def test_ac4_registry_declares_the_one_held_and_maps_the_eight(tmp_path):
    assert set(precommit_lints.DECLARED_ABSENT) == set(_HELD), (
        f"DECLARED_ABSENT must be exactly the one held name, got "
        f"{precommit_lints.DECLARED_ABSENT!r}"
    )
    assert len(precommit_lints.DECLARED_ABSENT) == 1, precommit_lints.DECLARED_ABSENT
    root = os.path.realpath(str(_REPO_ROOT))
    for name in _LAYER:
        mapped = precommit_lints.LINT_DRIVER_DIRS.get(name)
        assert mapped is not None and os.path.realpath(mapped) == root, (
            f"LINT_DRIVER_DIRS[{name!r}] must be the repo root, got {mapped!r}"
        )
    for name in _HELD:
        assert name not in precommit_lints.LINT_DRIVER_DIRS, f"{name} must stay unmapped"
    denominator = (
        list(precommit_lints.SPEC_LINTS)
        + list(precommit_lints.TEST_LINTS)
        + list(precommit_lints.TS_TEST_LINTS)
    )
    assert len(denominator) == 13, f"registry denominator must be 13 (bd#308): {denominator!r}"


@pytest.mark.parametrize("name", _LAYER)
def test_ac4_a_deleted_leaving_driver_that_is_not_declared_is_a_missing_driver_refusal(
    tmp_path, name
):
    env = _hermetic_env(tmp_path)
    clone = _package_copy(tmp_path, env, "closed-copy")
    assert name not in precommit_lints.DECLARED_ABSENT, f"{name} must have left DECLARED_ABSENT"
    (clone / f"{name}.py").unlink()

    proc = _run_cli(clone, env)
    output = proc.stdout + proc.stderr

    assert proc.returncode == 1, f"expected rc=1, got {proc.returncode}: {output!r}"
    assert REFUSE_MISSING_DRIVER in output, f"missing token: {output!r}"
    assert _lint_token(output, name), f"lint not named as a whole token: {output!r}"
    for other in _ALL_NINE:
        if other != name:
            assert not _names_lint(output, other), f"innocent {other} named: {output!r}"


@pytest.mark.parametrize("name", _HELD)
def test_ac4_a_held_name_removed_from_declared_absent_is_a_missing_driver_refusal(
    tmp_path, name
):
    env = _hermetic_env(tmp_path)
    clone = _package_copy(tmp_path, env, "closed-held-copy")
    if name in _DORMANT:
        assert (clone / f"{name}.py").is_file(), "the dormant driver file must sit at the clone root"
    else:
        assert not (clone / f"{name}.py").exists(), "spec-cite has no driver file"
        assert not _driver(name).exists(), f"bd#304 must not ship {_driver(name)}"
    _append_registry(
        clone,
        f'DECLARED_ABSENT = [n for n in DECLARED_ABSENT if n != "{name}"]',
    )

    proc = _run_cli(clone, env)
    output = proc.stdout + proc.stderr

    assert proc.returncode == 1, f"expected rc=1, got {proc.returncode}: {output!r}"
    assert REFUSE_MISSING_DRIVER in output, f"missing token: {output!r}"
    assert _lint_token(output, name), (
        f"the unmapped dormant file must not be resolved; {name} must be named: {output!r}"
    )
    for other in _ALL_NINE:
        if other != name:
            assert not _names_lint(output, other), f"innocent {other} named: {output!r}"


# ------------------------------------------------------------------------ AC5


@pytest.mark.parametrize("name", _SHIPPED)
def test_ac5_driver_placement_shebang_and_modes(name):
    path = _driver(name)
    assert path.is_file(), f"driver file missing at the repo root: {path}"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/env python3", f"first line must be the shebang: {first!r}"
    assert os.stat(path).st_mode & 0o111, "working tree exec bit must be set"
    if name in _LAYER:
        resolved = precommit_lints.driver_path(name, "/nonexistent-lint-dir-bd304")
        assert os.path.realpath(resolved) == os.path.realpath(str(path)), (
            f"driver_path must resolve a mapped name to the repo root, got {resolved!r}"
        )

    if not (_REPO_ROOT / ".git").exists():
        pytest.skip(
            "bd#304 AC5: this corpus is a `git archive` tree with no .git, so the "
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


_CONTRACT_NAMES = list(_SHIPPED)


@pytest.mark.parametrize("name", _CONTRACT_NAMES)
def test_ac6_driver_contract(tmp_path, name):
    driver = _driver(name)
    assert driver.is_file(), f"driver {driver} does not exist"
    case = _CASES[name]
    env = _hermetic_env(tmp_path)
    (tmp_path / "dirty_spec.md").write_text(case["dirty"], encoding="utf-8")
    (tmp_path / "clean_spec.md").write_text(_build(), encoding="utf-8")
    assert case["dirty"].splitlines()[case["line"] - 1], "fixture line number must be non-blank"

    clean = _run_driver(driver, ["--spec", "clean_spec.md"], tmp_path, env)
    assert clean.returncode == 0, f"clean must exit 0: {clean.stdout!r} {clean.stderr!r}"

    dirty = _run_driver(driver, ["--spec", "dirty_spec.md"], tmp_path, env)
    assert dirty.returncode == 1, f"dirty must exit 1: {dirty.stdout!r} {dirty.stderr!r}"
    assert _text_line_matches(dirty.stdout, case["text_re"]), (
        f"expected a line matching {case['text_re']!r}, got {dirty.stdout!r}"
    )

    as_json = _run_driver(driver, ["--spec", "dirty_spec.md", "--json"], tmp_path, env)
    assert as_json.returncode == 1, f"--json dirty must exit 1: {as_json.stderr!r}"
    payload = json.loads(as_json.stdout)
    assert set(payload) == {"findings"}, f"top-level keys: {sorted(payload)!r}"
    assert len(payload["findings"]) == 1, f"findings: {payload['findings']!r}"
    _assert_item(payload["findings"][0], case)
    assert payload["findings"][0]["line"] == case["line"]

    clean_json = _run_driver(driver, ["--spec", "clean_spec.md", "--json"], tmp_path, env)
    assert clean_json.returncode == 0
    assert json.loads(clean_json.stdout) == {"findings": []}

    unreadable = _run_driver(driver, ["--spec", "no_such_spec.md"], tmp_path, env)
    assert unreadable.returncode == 2, f"unreadable must exit 2: {unreadable.stdout!r}"
    assert "no_such_spec.md" in unreadable.stderr, f"stderr: {unreadable.stderr!r}"
    assert unreadable.stdout == "", f"nothing on stdout: {unreadable.stdout!r}"

    no_spec = _run_driver(driver, [], tmp_path, env)
    assert no_spec.returncode == 2, f"a missing --spec must exit 2: {no_spec.stderr!r}"
    assert no_spec.stdout == ""


def test_ac6_spec_coverage_without_an_ac_table_exits_2_with_a_message(tmp_path):
    driver = _driver("spec-coverage-lint")
    assert driver.is_file(), f"driver {driver} does not exist"
    env = _hermetic_env(tmp_path)
    (tmp_path / "no_table_spec.md").write_text(_build(table=False), encoding="utf-8")

    proc = _run_driver(driver, ["--spec", "no_table_spec.md"], tmp_path, env)

    assert proc.returncode == 2, f"no AC table must exit 2: {proc.stdout!r} {proc.stderr!r}"
    assert "no AC table found" in proc.stderr, f"stderr: {proc.stderr!r}"
    assert proc.stdout == "", f"nothing on stdout: {proc.stdout!r}"


# ------------------------------------------------------------------------ AC7


@pytest.mark.parametrize("name", _LAYER)
def test_ac7_rollback_needs_mapping_removal_together_with_declaration(tmp_path, name):
    env = _hermetic_env(tmp_path)
    case = _CASES[name]
    declare = f'DECLARED_ABSENT = list(DECLARED_ABSENT) + ["{name}"]'
    unmap = f'LINT_DRIVER_DIRS = {{k: v for k, v in LINT_DRIVER_DIRS.items() if k != "{name}"}}'

    # (a) mapping kept AND declared absent -> the violating spec is still refused.
    clone_a = _package_copy(tmp_path, env, "rollback-a")
    _append_registry(clone_a, declare)
    _stage(clone_a, env, "x_spec.md", case["dirty"])
    rc_a, out_a = _commit(clone_a, env)
    assert rc_a != 0, f"a declaration alone must not suppress a mapped driver; output={out_a!r}"
    assert not _has_head(clone_a, env), f"no commit object may exist; output={out_a!r}"
    assert REFUSE_VIOLATION in out_a and _lint_token(out_a, name), out_a
    _stage(clone_a, env, "x_spec.md", case["clean"])
    rc_twin, out_twin = _commit(clone_a, env)
    assert rc_twin == 0, f"the clean twin must be accepted; output={out_twin!r}"

    # (b) mapping removed AND declared absent (file stays at the clone root).
    clone_b = _package_copy(tmp_path, env, "rollback-b")
    _append_registry(clone_b, declare + "\n" + unmap)
    assert (clone_b / f"{name}.py").is_file()
    _stage(clone_b, env, "x_spec.md", case["dirty"])
    rc_b, out_b = _commit(clone_b, env)
    assert rc_b == 0, f"unmapped + declared must be accepted; output={out_b!r}"
    assert _has_head(clone_b, env), "the rolled-back lint must let the commit through"

    # (c) driver deleted AND declared absent, mapping removed.
    clone_c = _package_copy(tmp_path, env, "rollback-c")
    _append_registry(clone_c, declare + "\n" + unmap)
    (clone_c / f"{name}.py").unlink()
    _stage(clone_c, env, "x_spec.md", case["dirty"])
    rc_c, out_c = _commit(clone_c, env)
    assert rc_c == 0, f"deleted + declared + unmapped must be accepted; output={out_c!r}"
    assert _has_head(clone_c, env)


# ------------------------------------------------------------------------ AC8

_ALLOWED_STDLIB = {"argparse", "json", "sys", "pathlib", "__future__"}
_FORBIDDEN_TEXT = (
    ".claude", "HAL_", "SYSTEM/", "/Users/", "os.environ", "os.getenv",
    "subprocess", "socket", "urllib", "http",
)
_OWN_MODULE = {
    "scope-inverse-lint": ("bytedigger_engine.scope_inverse",),
    "helper-extraction-lint": ("bytedigger_engine.helper_extraction",),
    "spec-coverage-lint": ("bytedigger_engine.spec_coverage", "bytedigger_engine.error_codes"),
    "token-consistency-lint": ("bytedigger_engine.token_consistency",),
    "presence-triad-lint": ("bytedigger_engine.presence_triad",),
    "format-conversion-lint": ("bytedigger_engine.format_conversion_cases",),
    "closure-evidence-lint": ("bytedigger_engine.scripts.lib.closure_evidence_verifier",),
    "mutation-two-sidedness-lint": (
        "bytedigger_engine.scripts.lib.mutation_two_sidedness_verifier",
    ),
}
_PACKAGE_LEVEL = {"bytedigger_engine", "bytedigger_engine.scripts", "bytedigger_engine.scripts.lib"}
_BARE_STEMS = (
    "scope_inverse", "helper_extraction", "spec_cite", "spec_coverage",
    "token_consistency", "presence_triad", "format_conversion_cases",
    "closure_evidence_verifier", "mutation_two_sidedness_verifier", "error_codes",
)


def _qualified_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            if module in _PACKAGE_LEVEL:
                found.extend(f"{module}.{a.name}" for a in node.names)
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


@pytest.mark.parametrize("name", _SHIPPED)
def test_ac8_driver_is_portable_and_only_a_thin_cli_over_its_checking_module(name):
    driver = _driver(name)
    assert driver.is_file(), f"driver {driver} does not exist"
    text = driver.read_text(encoding="utf-8")
    tree = ast.parse(text)
    own = _OWN_MODULE[name]

    imports = _qualified_imports(tree)
    allowed = _ALLOWED_STDLIB | set(own)
    stray = sorted({m for m in imports if m not in allowed})
    assert stray == [], f"imports outside the allowed set: {stray!r}"
    assert own[0] in imports, f"the driver must import the rule from {own[0]}, got {imports!r}"

    for token in _FORBIDDEN_TEXT:
        assert token not in text, f"driver must not contain {token!r}"
    stems = "|".join(_BARE_STEMS)
    assert not re.search(rf"^\s*(import|from)\s+({stems})\b", text, re.MULTILINE), (
        "no bare-name import of a checking module"
    )

    mutations = _sys_path_mutations(tree)
    assert len(mutations) == 1 and mutations[0].func.attr == "insert", (
        f"exactly one sys.path.insert allowed, got {[ast.unparse(m) for m in mutations]!r}"
    )
    inside = [
        sub for arg in mutations[0].args for sub in ast.walk(arg)
        if isinstance(sub, ast.Name) and sub.id == "__file__"
    ]
    assert inside, (
        f"__file__ must appear inline in the sys.path.insert arguments, "
        f"got {ast.unparse(mutations[0])!r}"
    )
    # No intermediate variable: no assignment of any kind may carry __file__.
    def _mentions_file(node):
        return any(
            isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(node)
        )

    smuggled = [
        ast.unparse(n)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        and n.value is not None
        and _mentions_file(n.value)
    ]
    assert smuggled == [], (
        f"__file__ must not be bound to a variable (no intermediate): {smuggled!r}"
    )
    # __file__ may sit only in the sys.path.insert arguments or an If.test guard.
    allowed_ids = {id(n) for n in inside}
    for if_node in ast.walk(tree):
        if isinstance(if_node, ast.If):
            allowed_ids |= {
                id(n) for n in ast.walk(if_node.test)
                if isinstance(n, ast.Name) and n.id == "__file__"
            }
    stray_file = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == "__file__" and id(n) not in allowed_ids
    ]
    assert stray_file == [], (
        f"__file__ used outside the sys.path.insert arguments and the guarding if: "
        f"{len(stray_file)} stray use(s)"
    )
    absolute_literals = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/")
    )
    assert absolute_literals == [], f"absolute-path string literal: {absolute_literals!r}"
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


# ----------------------------------------------------------------------- AC10


_EXISTING_ROOT_DRIVERS = {
    "core-boundary-lint.py", "cyrillic-prose-lint.py", "empty-input-assert-lint.py",
    "fixture-schema-lint.py", "forbidden-import-lint.py", "one-sided-predicate-lint.py",
    "stub-passability-lint.py",
}


def test_ac10_consumer_guard_the_cite_driver_stays_withheld_and_the_root_driver_set_is_exact():
    from bytedigger_engine.workflows import phase_45_spec  # noqa: PLC0415

    computed = Path(phase_45_spec.__file__).parent.parent.parent.parent / "spec-cite-lint.py"
    assert not computed.is_file(), (
        f"phase_45_spec execs {computed} by path; shipping it would switch on a "
        f"blocking /build gate (spec 2.7) -- it belongs to #305"
    )
    assert not _driver("spec-cite-lint").exists(), "spec-cite-lint.py must be absent at the repo root"

    on_disk = {p.name for p in _REPO_ROOT.glob("*-lint.py")}
    expected = _EXISTING_ROOT_DRIVERS | {f"{n}.py" for n in _SHIPPED}
    assert on_disk == expected, (
        f"repo-root *-lint.py set must be the existing six plus the eight new ones; "
        f"extra={sorted(on_disk - expected)!r} missing={sorted(expected - on_disk)!r}"
    )


# ------------------------------------------------------------------------ AC9


def _spec_corpus() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(str(_REPO_ROOT)):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            if fname.lower().endswith("_spec.md"):
                found.append(Path(dirpath) / fname)
    return sorted(found)


def test_ac9_measurement_pin_six_are_clean_on_the_corpus_and_each_held_lint_still_has_a_hit(
    tmp_path,
):
    corpus = _spec_corpus()
    assert corpus, "the *_spec.md corpus must be non-empty (is_spec_file classifies it)"
    for name in _SHIPPED:
        assert _driver(name).is_file(), f"driver {_driver(name)} does not exist"
    env = _hermetic_env(tmp_path)

    def rcs(name: str) -> dict:
        return {
            str(p.relative_to(_REPO_ROOT)): _run_driver(
                _driver(name), ["--spec", p], _REPO_ROOT, env
            ).returncode
            for p in corpus
        }

    for name in _LEAVING:
        results = rcs(name)
        failing = {p: rc for p, rc in results.items() if rc != 0}
        assert failing == {}, (
            f"{name} must exit 0 on every spec in the corpus (measured 0 hits); "
            f"a hit means it must not leave DECLARED_ABSENT: {failing!r}"
        )
    # bd#305: spec-coverage is run directly here (the layer does not feed it a
    # spec without an AC table); its measurement pin keeps a hit.
    results = rcs("spec-coverage-lint")
    assert any(rc != 0 for rc in results.values()), (
        f"spec-coverage-lint is now clean on the whole corpus ({len(corpus)} specs): "
        f"the measurement that held it back is stale"
    )
    # bd#315: token-consistency left DECLARED_ABSENT; its scanner measures zero
    # findings on the corpus (a hit here is a regression of the flip).
    failing = {p: rc for p, rc in rcs("token-consistency-lint").items() if rc != 0}
    assert failing == {}, (
        f"token-consistency-lint must exit 0 on every spec in the corpus: {failing!r}"
    )

    # spec-cite has no driver; the call its driver would make is the library one.
    from bytedigger_engine import spec_cite  # noqa: PLC0415

    cite_codes = {
        str(p.relative_to(_REPO_ROOT)): spec_cite.lint_spec(p, _REPO_ROOT)[0] for p in corpus
    }
    assert any(code != 0 for code in cite_codes.values()), (
        f"spec-cite-lint is now clean on the whole corpus ({len(corpus)} specs): the "
        f"measurement that held it back is stale, so #305 can flip it"
    )
