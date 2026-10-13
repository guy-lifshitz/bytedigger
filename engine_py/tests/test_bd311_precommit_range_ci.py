"""RED tests for bd#311 -- the pre-commit lint layer as a server-side range check.

Spec: docs/decisions/2026-10-13-bd311-precommit-range-ci.md

Pre-GREEN `scripts/precommit_enforce_cli.py` takes no arguments: `--range` is
ignored and the file set is read from `git diff --cached`, which is empty in a
CI checkout, so every range reads as clean and exits 0. There is no
`BD_PRECOMMIT_CI` switch, no `BD66-CI-RANGE-ERROR` and no `precommit-lints` job
in `.github/workflows/ci.yml`. Every test below fails today for that reason.

The behavioural ACs run the REAL `scripts/precommit_enforce_cli.py` as a
subprocess in a temporary git repository, with the real engine package reached
through a symlink so the real drivers are the ones executed. Offending and
legacy commits are made with `--no-verify` (the ratchet is about what is
ALREADY in the base), and the scanner itself is asked how many findings each
fixture carries before an assertion relies on that count. Every refusal is
paired with the acceptance of a twin in the same test, and each acceptance that
would also hold pre-GREEN is paired with a refusal that cannot.

Observations are the exit code and stdout. Hermetic: fake HOME, empty
global/system git config, no network, no model calls.

No sys.path mutation, no conftest import.
"""
from __future__ import annotations

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
_ENFORCE_CLI = _REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

_NAME = "empty-input-assert-lint"
_OFF_ENV = "BD_EMPTY_INPUT_ASSERT_LINT"
_CI_OFF_ENV = "BD_PRECOMMIT_CI"

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"
LINT_OFF = "BD66-LINT-OFF"
CI_OFF = "BD66-CI-OFF"
CI_RANGE_ERROR = "BD66-CI-RANGE-ERROR"

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
    for key in ("PYTHONPATH", "BD66_LINT_DIR", _OFF_ENV, _CI_OFF_ENV,
                "BD_SPEC_LINTS_OFF", "BD_LINT_CRASH_IS_VIOLATION"):
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


def _git_ok(args, cwd: Path, env: dict) -> str:
    result = _git(args, cwd, env)
    assert result.returncode == 0, f"git {args!r} failed: {result.stdout!r} {result.stderr!r}"
    return result.stdout.strip()


def _init_repo(tmp_path: Path, env: dict, name: str) -> Path:
    """Temp repo carrying the real CLI shim; `engine_py` is a symlink to the
    real package so the CLI resolves the real engine and the real drivers."""
    assert _ENFORCE_CLI.is_file(), f"missing {_ENFORCE_CLI}"
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    _git_ok(["init", "-q", "-b", "main"], repo, env)
    for key, value in (
        ("user.email", "bd311@example.com"),
        ("user.name", "bd311 tester"),
        ("commit.gpgsign", "false"),
    ):
        _git_ok(["config", key, value], repo, env)
    (repo / "scripts").mkdir()
    shutil.copy2(_ENFORCE_CLI, repo / "scripts" / _ENFORCE_CLI.name)
    (repo / "engine_py").symlink_to(_ENGINE_ROOT)
    return repo


def _commit_files(repo: Path, env: dict, files: dict, message: str = "bd311 fixture") -> str:
    """Write `files` (rel -> text), commit them with --no-verify, return the sha."""
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git_ok(["add", rel], repo, env)
    _git_ok(["commit", "-q", "--no-verify", "-m", message], repo, env)
    return _git_ok(["rev-parse", "--verify", "HEAD"], repo, env)


def _checkout(repo: Path, env: dict, sha: str) -> None:
    _git_ok(["checkout", "-q", "--detach", sha], repo, env)


def _cli(repo: Path, env: dict, args=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / _ENFORCE_CLI.name), *args],
        cwd=str(repo), env=env, capture_output=True, text=True,
        timeout=120, stdin=subprocess.DEVNULL,
    )


def _range(repo: Path, env: dict, base: str, tip: str = "HEAD", dots: str = "..",
           env_extra=None) -> subprocess.CompletedProcess:
    run_env = dict(env)
    run_env.update(env_extra or {})
    return _cli(repo, run_env, ["--range", f"{base}{dots}{tip}"])


def _findings(text: str) -> list:
    return summarize_empty_input_asserts(text)["findings"]


def _expected_line(path: str, source: str) -> str:
    f = _findings(source)[0]
    return (
        f"EMPTY-INPUT-ASSERT {path}:{f['line']}: {f['form']} - subject "
        f"{f['subject']!r} has no live denominator in scope"
    )


def _refused_with(proc: subprocess.CompletedProcess, rel: str, now: int, head: int) -> None:
    assert proc.returncode == 1, f"expected a refusal: rc={proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert (
        f"{REFUSE_VIOLATION}: lint={_NAME} file={rel} rc=1 findings={now} head={head}"
        in proc.stdout
    ), proc.stdout


def _accepted(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, f"expected acceptance: rc={proc.returncode} {proc.stdout!r} {proc.stderr!r}"
    assert "BD66-REFUSE" not in proc.stdout, proc.stdout
    assert "EMPTY-INPUT-ASSERT" not in proc.stdout, proc.stdout


def _phantom_registry_env(tmp_path: Path, env: dict) -> dict:
    """Env whose interpreter registers a `phantom-lint` TEST_LINTS name that has
    no driver and no DECLARED_ABSENT entry; BD66_LINT_DIR is an empty directory."""
    shim = tmp_path / "phantom-shim"
    shim.mkdir(exist_ok=True)
    (shim / "sitecustomize.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_ENGINE_ROOT)!r})\n"
        "from bytedigger_engine import precommit_lints as _m\n"
        "_m.TEST_LINTS.append('phantom-lint')\n",
        encoding="utf-8",
    )
    empty_dir = tmp_path / "empty-lint-dir"
    empty_dir.mkdir(exist_ok=True)
    phantom = dict(env)
    phantom["PYTHONPATH"] = str(shim)
    phantom["BD66_LINT_DIR"] = str(empty_dir)
    return phantom


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

_LEGACY_THREE = _LEGACY_TWO + "\n\ndef test_third():\n    hits = run_more()\n    assert hits == []\n"

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

_LEGACY_ONE_PLUS_SECOND = _LEGACY_TWO

_DOCS = "# Notes\n\nPlain ASCII documentation, nothing lintable.\n"


def _offending_repo(tmp_path: Path, env: dict, name: str = "off"):
    """main: base with a clean test; HEAD: adds an offending test (1 finding)."""
    repo = _init_repo(tmp_path, env, name)
    base = _commit_files(repo, env, {"test_base.py": _CONTROL_ONLY})
    tip = _commit_files(repo, env, {"test_new.py": _ONE_FINDING})
    return repo, base, tip


def _assert_fixture_counts():
    """Non-vacuity guard: the scanner itself, not the author, fixes the counts.
    Called inside the AC1 and AC3 tests, never as a standalone test."""
    assert len(_findings(_ONE_FINDING)) == 1
    assert len(_findings(_WITH_DENOMINATOR)) == 0
    assert len(_findings(_CONTROL_ONLY)) == 0
    assert len(_findings(_LEGACY_TWO)) == 2
    assert len(_findings(_LEGACY_THREE)) == 3
    assert len(_findings(_LEGACY_ONE_EDITED)) == 1
    assert len(_findings(_LEGACY_ONE_PLUS_SECOND)) == 2
    assert precommit_lints.is_test_file("test_new.py")
    assert not precommit_lints.is_spec_file("docs/notes.md")
    assert not precommit_lints.is_test_file("docs/notes.md")


# ------------------------------------------------------------------------ AC1


def test_ac1_range_with_an_added_offending_test_is_refused_and_the_clean_twin_passes(tmp_path):
    _assert_fixture_counts()
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)

    refused = _range(repo, env, base)

    _refused_with(refused, "test_new.py", now=1, head=0)
    assert _names_lint(refused.stdout, _NAME), refused.stdout
    assert _expected_line("test_new.py", _ONE_FINDING) in refused.stdout.splitlines(), refused.stdout

    twin = _init_repo(tmp_path, env, "twin")
    twin_base = _commit_files(twin, env, {"test_base.py": _CONTROL_ONLY})
    _commit_files(twin, env, {"test_new.py": _WITH_DENOMINATOR})
    _accepted(_range(twin, env, twin_base))


def test_ac1_three_dot_range_means_the_same_merge_base(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)

    refused = _range(repo, env, base, dots="...")

    _refused_with(refused, "test_new.py", now=1, head=0)


# ------------------------------------------------------------------------ AC2


def test_ac2_legacy_finding_edited_without_adding_one_is_accepted(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _init_repo(tmp_path, env, "legacy")
    base = _commit_files(repo, env, {"test_legacy.py": _ONE_FINDING})
    edited = _commit_files(repo, env, {"test_legacy.py": _LEGACY_ONE_EDITED})
    worse = _commit_files(repo, env, {"test_legacy.py": _LEGACY_ONE_PLUS_SECOND})

    # Ratchet-active proof: the worsened tip is refused by the same range check.
    _checkout(repo, env, worse)
    _refused_with(_range(repo, env, base), "test_legacy.py", now=2, head=1)

    # The same file edited with no new finding is accepted, silently.
    _checkout(repo, env, edited)
    _accepted(_range(repo, env, base))


# ------------------------------------------------------------------------ AC3


def test_ac3_ratchet_baseline_is_the_merge_base_not_the_tip(tmp_path):
    _assert_fixture_counts()
    env = _hermetic_env(tmp_path)
    repo = _init_repo(tmp_path, env, "baseline")
    base = _commit_files(repo, env, {"test_legacy.py": _LEGACY_TWO})
    # The branch worsens the file (2 -> 3), then touches it again without
    # adding one: against a baseline of the tip's own parent the last commit
    # would look innocent, against BASE it must still be refused.
    _commit_files(repo, env, {"test_legacy.py": _LEGACY_THREE})
    tip = _commit_files(repo, env, {"test_legacy.py": "# touched again\n" + _LEGACY_THREE})
    assert len(_findings("# touched again\n" + _LEGACY_THREE)) == 3

    refused = _range(repo, env, base)

    _refused_with(refused, "test_legacy.py", now=3, head=2)

    # Twin: the same branch, range anchored at the worsened commit (baseline 3),
    # accepts the no-new-finding edit.
    parent = _git_ok(["rev-parse", "--verify", f"{tip}~1"], repo, env)
    _accepted(_range(repo, env, parent))


# ------------------------------------------------------------------------ AC4


def test_ac4_ci_off_switch_prints_token_and_passes_only_on_exact_off(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)

    on = _range(repo, env, base)
    _refused_with(on, "test_new.py", now=1, head=0)

    off = _range(repo, env, base, env_extra={_CI_OFF_ENV: "off"})
    assert off.returncode == 0, f"{off.stdout!r} {off.stderr!r}"
    assert CI_OFF in off.stdout, off.stdout
    assert "BD66-REFUSE" not in off.stdout
    assert "EMPTY-INPUT-ASSERT" not in off.stdout


@pytest.mark.parametrize("value", ["OFF", "0", "", "off ", "on"])
def test_ac4_any_other_value_leaves_the_job_on(tmp_path, value):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)

    proc = _range(repo, env, base, env_extra={_CI_OFF_ENV: value})

    _refused_with(proc, "test_new.py", now=1, head=0)
    assert CI_OFF not in proc.stdout, proc.stdout


def test_ac4_switch_is_range_mode_only_staged_mode_still_refuses(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    off_env = dict(env)
    off_env[_CI_OFF_ENV] = "off"

    # Range mode honours the switch (cannot hold pre-GREEN).
    ranged = _cli(repo, off_env, ["--range", f"{base}..HEAD"])
    assert ranged.returncode == 0 and CI_OFF in ranged.stdout, ranged.stdout

    # Staged mode ignores it: a staged offending file is still refused.
    (repo / "test_staged.py").write_text(_ONE_FINDING, encoding="utf-8")
    _git_ok(["add", "test_staged.py"], repo, env)
    staged = _cli(repo, off_env)
    _refused_with(staged, "test_staged.py", now=1, head=0)
    assert CI_OFF not in staged.stdout


def test_ac4_off_runs_before_anything_else_no_git_no_prepass(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    phantom = _phantom_registry_env(tmp_path, env)

    # Pre-pass would refuse (phantom name without a driver) and the range
    # is malformed; with the switch exactly `off` neither is consulted.
    pre = _cli(repo, phantom, ["--range", f"{base}..HEAD"])
    assert pre.returncode != 0, "the phantom registry must refuse without the switch"

    phantom[_CI_OFF_ENV] = "off"
    ok = _cli(repo, phantom, ["--range", "not-a-range"])
    assert ok.returncode == 0, f"{ok.stdout!r} {ok.stderr!r}"
    assert CI_OFF in ok.stdout, ok.stdout
    assert REFUSE_MISSING_DRIVER not in ok.stdout
    assert CI_RANGE_ERROR not in ok.stdout

    # And outside any git repository.
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    outside = subprocess.run(
        [sys.executable, str(_ENFORCE_CLI), "--range", "a..b"],
        cwd=str(bare), env={**env, _CI_OFF_ENV: "off"}, capture_output=True,
        text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert outside.returncode == 0 and CI_OFF in outside.stdout, (
        f"{outside.stdout!r} {outside.stderr!r}"
    )


# ------------------------------------------------------------------------ AC5


def test_ac5_malformed_or_unresolvable_range_fails_closed_with_exit_2(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _init_repo(tmp_path, env, "closed")
    base = _commit_files(repo, env, {"test_base.py": _CONTROL_ONLY})
    _commit_files(repo, env, {"test_new.py": _WITH_DENOMINATOR})
    # A branch whose tip is a real commit that is not the checked-out HEAD.
    _git_ok(["checkout", "-q", "-b", "other", base], repo, env)
    _commit_files(repo, env, {"test_other.py": _CONTROL_ONLY})
    _git_ok(["checkout", "-q", "main"], repo, env)

    # Accepted twin: the well-formed range over the same repo passes.
    _accepted(_range(repo, env, base))

    cases = {
        "unresolvable BASE (sha)": ["--range", f"{'0' * 40}..HEAD"],
        "unresolvable BASE (name)": ["--range", "no-such-ref-bd311..HEAD"],
        "missing value": ["--range"],
        "no dots": ["--range", "HEAD"],
        "empty left side": ["--range", "..HEAD"],
        "empty right side": ["--range", f"{base}.."],
        "tip is another branch": ["--range", f"{base}..other"],
        "tip is not HEAD": ["--range", f"HEAD..{base}"],
        "unknown flag": ["--bogus"],
        "unknown flag after range": ["--range", f"{base}..HEAD", "--bogus"],
        "stray positional": ["stray", "--range", f"{base}..HEAD"],
    }
    for label, args in cases.items():
        proc = _cli(repo, env, args)
        assert proc.returncode == 2, (
            f"{label}: must exit 2, got {proc.returncode}: {proc.stdout!r} {proc.stderr!r}"
        )
        assert any(
            line.startswith(f"{CI_RANGE_ERROR}:") for line in proc.stdout.splitlines()
        ), f"{label}: stdout must carry `{CI_RANGE_ERROR}: <reason>`: {proc.stdout!r}"


def test_ac5_a_range_that_fails_closed_is_never_a_silent_pass_on_an_offending_tree(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    bad = _cli(repo, env, ["--range", f"{'f' * 40}..HEAD"])

    assert bad.returncode == 2, f"{bad.stdout!r} {bad.stderr!r}"
    assert CI_RANGE_ERROR in bad.stdout


# ------------------------------------------------------------------------ AC6


def test_ac6_empty_range_runs_nothing_and_passes(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    empty = _cli(repo, env, ["--range", "HEAD..HEAD"])

    assert empty.returncode == 0, f"{empty.stdout!r} {empty.stderr!r}"
    assert "BD66-REFUSE" not in empty.stdout
    assert "EMPTY-INPUT-ASSERT" not in empty.stdout
    assert CI_RANGE_ERROR not in empty.stdout


# ------------------------------------------------------------------------ AC7


def test_ac7_registry_prepass_runs_in_range_mode(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    # Precondition: range mode really lints this tree (cannot hold pre-GREEN).
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    clean = _init_repo(tmp_path, env, "prepass-clean")
    clean_base = _commit_files(clean, env, {"test_base.py": _CONTROL_ONLY})
    _commit_files(clean, env, {"test_new.py": _WITH_DENOMINATOR})
    phantom = _phantom_registry_env(tmp_path, env)

    # Accepted twin: a clean range under the unmodified registry passes.
    _accepted(_range(clean, env, clean_base))

    refused = _range(clean, phantom, clean_base)
    assert refused.returncode == 1, f"{refused.stdout!r} {refused.stderr!r}"
    assert REFUSE_MISSING_DRIVER in refused.stdout, refused.stdout
    assert _names_lint(refused.stdout, "phantom-lint"), refused.stdout


# ------------------------------------------------------------------------ AC8


def test_ac8_per_lint_switch_is_honoured_in_range_mode(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    off = _range(repo, env, base, env_extra={_OFF_ENV: "off"})

    assert off.returncode == 0, f"{off.stdout!r} {off.stderr!r}"
    assert f"{LINT_OFF}: lint={_NAME} file=test_new.py" in off.stdout, off.stdout
    assert "EMPTY-INPUT-ASSERT" not in off.stdout
    assert CI_OFF not in off.stdout


# ------------------------------------------------------------------------ AC9


def test_ac9_docs_only_range_is_accepted(tmp_path):
    env = _hermetic_env(tmp_path)
    repo = _init_repo(tmp_path, env, "docs")
    base = _commit_files(repo, env, {"test_base.py": _CONTROL_ONLY})
    docs = _commit_files(repo, env, {"docs/notes.md": _DOCS})
    dirty = _commit_files(repo, env, {"test_new.py": _ONE_FINDING})

    # Refused twin: the same base with the offending test in range.
    _checkout(repo, env, dirty)
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    _checkout(repo, env, docs)
    _accepted(_range(repo, env, base))


# ----------------------------------------------------------------------- AC10


def _as_text(value) -> str:
    return "" if value is None else str(value)


def test_ac10_ci_yml_declares_the_precommit_lints_job():
    import yaml  # noqa: PLC0415 -- available in CI; absence is a failure, not a skip

    assert _CI_YML.is_file(), f"missing {_CI_YML}"
    doc = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    jobs = doc["jobs"]
    assert "precommit-lints" in jobs, f"job missing; jobs: {sorted(jobs)!r}"
    job = jobs["precommit-lints"]

    guard = " ".join(_as_text(job.get("if")).split())
    assert guard == "github.event_name == 'pull_request'", f"job guard: {guard!r}"
    assert job.get("runs-on") == "ubuntu-latest"
    assert job.get("timeout-minutes") == 10
    assert "continue-on-error" not in job

    steps = job.get("steps") or []
    assert steps, "job has no steps"
    checkouts = [s for s in steps if _as_text(s.get("uses")).startswith("actions/checkout@v4")]
    assert len(checkouts) == 1, f"exactly one actions/checkout@v4 step: {steps!r}"
    assert (checkouts[0].get("with") or {}).get("fetch-depth") == 0, checkouts[0]

    for step in steps:
        assert "continue-on-error" not in step, f"step may not continue on error: {step!r}"
        assert "|| true" not in _as_text(step.get("run")), f"step swallows failure: {step!r}"
        assert "pip " not in _as_text(step.get("run")), f"no install step: {step!r}"

    runners = [
        s for s in steps
        if "scripts/precommit_enforce_cli.py" in _as_text(s.get("run"))
    ]
    assert len(runners) == 1, f"exactly one step runs the CLI: {steps!r}"
    runner = runners[0]
    run_text = _as_text(runner.get("run"))
    assert "--range" in run_text, run_text
    assert "BASE_SHA" in run_text, f"range base must come from BASE_SHA: {run_text!r}"
    assert "python3" in run_text, run_text

    env_block = dict(job.get("env") or {})
    env_block.update(runner.get("env") or {})
    assert "github.event.pull_request.base.sha" in _as_text(env_block.get("BASE_SHA")), env_block
    assert "BD_PRECOMMIT_CI" in env_block, f"BD_PRECOMMIT_CI must be passed: {env_block!r}"
    assert "BD_PRECOMMIT_CI" in _as_text(env_block["BD_PRECOMMIT_CI"]), env_block


# ----------------------------------------------------------------------- AC11


def test_ac11_no_argument_invocation_is_still_the_staged_mode(tmp_path):
    env = _hermetic_env(tmp_path)
    repo, base, _tip = _offending_repo(tmp_path, env)

    # The offending file is committed and nothing is staged: staged mode has
    # nothing to lint, range mode over the same repo does (cannot hold pre-GREEN).
    nothing = _cli(repo, env)
    _accepted(nothing)
    _refused_with(_range(repo, env, base), "test_new.py", now=1, head=0)

    # An unstaged violation is not read by staged mode.
    (repo / "test_unstaged.py").write_text(_ONE_FINDING, encoding="utf-8")
    _accepted(_cli(repo, env))

    # The same file staged is refused, against the HEAD baseline.
    _git_ok(["add", "test_unstaged.py"], repo, env)
    staged = _cli(repo, env)
    _refused_with(staged, "test_unstaged.py", now=1, head=0)
    assert _expected_line("test_unstaged.py", _ONE_FINDING) in staged.stdout.splitlines()
    assert CI_RANGE_ERROR not in staged.stdout
