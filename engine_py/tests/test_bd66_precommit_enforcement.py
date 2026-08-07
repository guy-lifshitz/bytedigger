"""RED tests for bd#66 — pre-commit lint enforcement layer (round 3).

Spec: SHARED/memory/Decisions/2026-08-07_bd66_precommit_enforcement_layer_spec.md
(Class: SYSTEMATIC — Principle C, codified-but-not-enforced.)

Design under test (all three artefacts are NEW pre-GREEN):
  - scripts/install_git_hooks.py       — `--install` / `--check`, `--root R`.
  - githooks/pre-commit                — `exec python3 <top>/engine_py/
                                          bytedigger_engine/precommit_enforce.py`
  - engine_py/bytedigger_engine/precommit_enforce.py
  - engine_py/bytedigger_engine/precommit_lints.py::DECLARED_ABSENT,
    ::DEFAULT_LINT_DIR (EDIT)

Contract this file pins (spec §2.1/§2.1a/§2.2/§2.3; the parts the spec leaves
implicit are resolved here and are the RED-side contract GREEN must satisfy):
  1. `precommit_enforce.main(argv=None) -> int`, and `sys.exit(main())` under
     `__main__` so the hook's `exec` propagates the code verbatim.
  2. The repository is discovered from the process cwd (`git rev-parse
     --show-toplevel`); staged paths from the index — including the unborn-HEAD
     case, which is exactly the state AC5/AC10 commit from.
  3. §2.1a — the driver directory defaults to `precommit_lints.DEFAULT_LINT_DIR`
     (`os.path.realpath` of the registry module's own directory). `BD66_LINT_DIR`
     is an OVERRIDE on top of that canon (§1h), never a precondition: unset or
     empty means "canon", it never means "nothing to check". The KILLING PAIR is
     AC6c and AC12, not AC11/AC12: AC11 takes its refusal from the PLAN branch,
     so it does not pin the PRE-PASS on the canonical path. AC6c demands a
     PRE-PASS refusal with the variable deleted, AC12 a pass with the variable
     deleted. A gate on `BD66_LINT_DIR` anywhere in the layer — including the
     `if override: prepass(...)` shape of round 2, which passed every AC and
     never refused in the real repository — dies on AC6c. AC7 does NOT pin this;
     AC7 runs through the override.
  4. The registry is read from the `precommit_lints` MODULE at call time
     (`precommit_lints.DECLARED_ABSENT`, and the name lists through
     `build_lint_commands`) — not snapshotted into module-level constants of
     `precommit_enforce` at import time. §1g: one canonical source.
  5. §2.1 outcome 0 — `DECLARED_ABSENT` LICENSES a name to have no driver on
     disk (the planned command is then SKIPPED) and does NOT suppress a driver
     that IS on disk: a present driver runs even for a declared name. AC5/AC10
     ride exactly that clause. Driver presence is evaluated ONCE, in the registry
     pre-pass — a driver that vanishes after that is outcome 3
     (`BD66-REFUSE-DRIVER-ERROR`), not outcome 1. AC9 pins that cite.
  6. §2.1 — the pre-pass is REGISTRY-scoped and runs BEFORE `nothing_to_lint`:
     it sweeps SPEC_LINTS+TEST_LINTS+TS_TEST_LINTS regardless of what is staged.
     AC6b pins this (a plan-scoped implementation returns 0 there), AC6c pins it
     end to end through a real `git commit` on the canonical path.
  7. §2.2 closes both ways: a `DECLARED_ABSENT` entry matching no registry name
     refuses too, same token. AC13 pins it. §2.1: the pre-pass ACCUMULATES and
     prints every violator without an early exit, so AC13 also asserts that an
     innocent, properly declared name is NOT named.
  8. §2.3 — `githooks/pre-commit` carries the exec bit; the working-tree
     assertion is `mode & 0o111` (a clone made under umask 077 legitimately
     yields 0o700), while the exact `100755` is asserted against the VERSIONED
     mode via `git ls-files -s`. Git silently SKIPS a non-executable hook, which
     would read as a green pass with no check at all. `precommit_enforce.py`
     bootstraps its own `sys.path`; no fixture here exports PYTHONPATH, so a
     GREEN leaning on an inherited PYTHONPATH fails as in a bare clone.
  9. Exit codes: 0 clean, 1 refusal. Tokens `BD66-REFUSE-VIOLATION`,
     `BD66-REFUSE-MISSING-DRIVER`, `BD66-REFUSE-DRIVER-ERROR`. The
     driver-error line reports the exit code as `rc=<n>` on the SAME line as
     the token and the lint name.
 10. The installer reads the current value with `git config --get --local
     core.hooksPath`: a developer's global `core.hooksPath` must neither pass
     for an installation (false `--check` rc=0) nor trigger the foreign-value
     refusal. AC3b pins both halves. §2.1 argument edges: no flags behaves as
     `--check`; `--install --check` together is a usage error, rc=2; `--root` on
     a non-git directory is rc=1 with NO write anywhere. AC14 pins those three.
 11. Every assertion that a lint name appears in the output matches an EXACT
     token (`_output_names_lint`, boundaries on `[0-9A-Za-z_-]`), so `phantom-lint`
     cannot be satisfied by a printed `phantom-lint-2`.

Registry patching (MINOR-3 of the round-2 gate): whenever a test replaces one of
the name lists, it also sets `DECLARED_ABSENT` to exactly the names of the
PATCHED registry minus the intended violator. Otherwise the real names it just
removed are left orphaned in `DECLARED_ABSENT` and BOTH pre-pass branches fire
at once, so the AC no longer isolates the branch it claims to exercise.

§1q collectability: nothing that is absent pre-GREEN is imported at module
scope. `precommit_enforce` is imported INSIDE each test body; the installer and
the hook are reached only through subprocess. `precommit_lints` exists today and
is imported at module scope. No sys.path mutation, no `from conftest import`.

§1i (singleton/racy-fixture ban): AC9's "driver missing at run time" is
PRE-STAGED deterministically — the first planned driver deletes the second
driver's file and exits 0, so the vanish is sequential and reproducible. No
sleeps, no timing windows, no contended resource.

§1l anchor: AC5/AC10 are a two-sided pair anchored on a production side-effect
in a real store — the git object database. A stub that always refuses fails
AC10; a stub that always passes fails AC5. The printed token is an ADDITIONAL
assertion; the load-bearing one is whether a commit object exists.

Hermeticity: every test builds its own `git init` repo under tmp_path, with
GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM neutralised so a developer's global
`core.hooksPath` cannot leak in. No network. No writes outside tmp_path. The
real repository tree is read ONLY (AC7/AC12 registry facts, and AC11's two
file copies) and is never written to — AC11 materialises a package COPY under
tmp_path rather than symlinking the real package directory it plants a driver
into.

Pre-GREEN PASS/FAIL — every AC FAILs today:
  - AC1 FAIL: scripts/install_git_hooks.py and githooks/pre-commit do not
    exist; python exits 2 and core.hooksPath is never set.
  - AC2 FAIL: same — the first `--install` already returns non-zero.
  - AC3 FAIL: no installer, so rc is 2 (not the specified 1) and nothing names
    the foreign `other-hooks` value.
  - AC3b FAIL: no installer, so `--check` exits 2 (not 1) and no local value is
    ever written over the global one.
  - AC4 FAIL: no installer, so `--check` cannot return 1-then-0 around an
    install that never happens.
  - AC5 FAIL: no installer and no githooks/pre-commit, so `git commit` is
    unguarded — it SUCCEEDS and a commit object appears. This is the forcing
    assertion of the lot.
  - AC6 FAIL: assertion — engine_py/bytedigger_engine/precommit_enforce.py does
    not exist (asserted before the import, so the failure is an assertion and
    not an import accident).
  - AC6b FAIL: same missing-module assertion; post-GREEN a plan-scoped pre-pass
    returns 0 here.
  - AC6c FAIL: precommit_enforce.py does not exist, so there is nothing to copy
    into the materialised package; and with no installer/hook the `git commit`
    would be unguarded. This is the AC that kills every `BD66_LINT_DIR` gate.
  - AC7 FAIL: precommit_lints has no DECLARED_ABSENT attribute (0 names
    declared, denominator 12) and the enforcement layer does not exist.
  - AC8 FAIL: assertion — precommit_enforce.py does not exist.
  - AC9 FAIL: assertion — precommit_enforce.py does not exist.
  - AC10 FAIL: no installer to install a hook with, so the arrange step cannot
    even establish the guarded repo the AC is about.
  - AC11 FAIL: precommit_enforce.py cannot be copied — it does not exist.
  - AC12 FAIL: precommit_lints has no DEFAULT_LINT_DIR and no enforcement layer.
  - AC13 FAIL: assertion — precommit_enforce.py does not exist.
  - AC14 FAIL: no installer, so the no-flag call exits 2 instead of 1/0 and the
    `--install --check` usage error is never raised.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import precommit_lints

# ─── shared helpers ────────────────────────────────────────────────────────────

REFUSE_VIOLATION = "BD66-REFUSE-VIOLATION"
REFUSE_MISSING_DRIVER = "BD66-REFUSE-MISSING-DRIVER"
REFUSE_DRIVER_ERROR = "BD66-REFUSE-DRIVER-ERROR"

# precommit_lints.py lives at <repo>/engine_py/bytedigger_engine/.
_LINTS_FILE = Path(precommit_lints.__file__).resolve()
PACKAGE_DIR = _LINTS_FILE.parent
ENGINE_ROOT = PACKAGE_DIR.parent
REPO_ROOT = ENGINE_ROOT.parent

INSTALLER = REPO_ROOT / "scripts" / "install_git_hooks.py"
ENFORCE_CLI = REPO_ROOT / "scripts" / "precommit_enforce_cli.py"
GITHOOKS_DIR = REPO_ROOT / "githooks"
HOOK_FILE = GITHOOKS_DIR / "pre-commit"

ENFORCE_MODULE = PACKAGE_DIR / "precommit_enforce.py"

ALL_REGISTRY_NAMES = (
    list(precommit_lints.SPEC_LINTS)
    + list(precommit_lints.TEST_LINTS)
    + list(precommit_lints.TS_TEST_LINTS)
)

_GIT_ENV_KEYS = (
    "HOME",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM",
)


_NAME_CHAR = r"[0-9A-Za-z_\-]"


def _output_names_lint(output: str, name: str) -> bool:
    """True iff `name` appears in `output` as a WHOLE token.

    Off-by-substring guard: a plain `name in output` lets a refusal that names
    `phantom-lint-2` satisfy an assertion about `phantom-lint`. Boundaries are
    the lint-name alphabet itself (`[0-9A-Za-z_-]`), not `\\b`, because `\\b`
    matches on either side of a hyphen and would keep the bug alive.
    """
    pattern = rf"(?<!{_NAME_CHAR}){re.escape(name)}(?!{_NAME_CHAR})"
    return re.search(pattern, output) is not None


def _declared_for(spec_lints, test_lints, ts_test_lints, violators=()):
    """DECLARED_ABSENT consistent with a PATCHED registry (gate MINOR-3).

    Every name of the patched registry except the intended violators is declared
    absent, and nothing else is — so the forward branch (name without driver and
    without declaration) and the reverse branch (§2.2, an orphaned declaration)
    cannot both fire, and each AC exercises exactly the one branch it names.
    """
    excluded = set(violators)
    return [
        n
        for n in [*spec_lints, *test_lints, *ts_test_lints]
        if n not in excluded
    ]


def _hermetic_git_env(tmp_path: Path) -> dict:
    """Environment in which git sees NO global/system config.

    Without this, a developer's own `core.hooksPath` in ~/.gitconfig would be
    visible to `git config --get core.hooksPath` in the temp repo and AC1/AC4
    would read someone else's machine instead of the installer's effect.

    §2.3: PYTHONPATH is REMOVED, not seeded. `precommit_enforce.py` is required
    to bootstrap its own sys.path from `__file__`; a bare developer clone has no
    PYTHONPATH, so a GREEN that relies on inheriting one must fail here.
    BD66_LINT_DIR is removed too, so nothing leaks the override into a test that
    means to exercise the canonical default.
    """
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


def _git(args: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True
    )


def _init_repo(tmp_path: Path, env: dict, name: str = "repo") -> Path:
    """A fresh repo on an UNBORN branch (no commits) with local identity set."""
    repo = (tmp_path / name).resolve()
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "-b", "main"], repo, env).returncode == 0
    for key, value in (
        ("user.email", "bd66@example.com"),
        ("user.name", "bd66 tester"),
        ("commit.gpgsign", "false"),
    ):
        assert _git(["config", key, value], repo, env).returncode == 0
    return repo


def _stage_test_file(repo: Path, env: dict, name: str = "test_x.py") -> Path:
    """Stage a `test_*.py` path — the corpus TEST_LINTS/`is_test_file` matches."""
    target = repo / name
    target.write_text("def test_placeholder():\n    assert True\n")
    assert _git(["add", name], repo, env).returncode == 0
    return target


def _stage_readme(repo: Path, env: dict) -> Path:
    """Stage ONLY `README.md` — a path that classifies as nothing at all.

    `classify_staged` puts it in no bucket, so `nothing_to_lint` is True and the
    plan is empty. Any refusal observed after this staging came from the
    registry pre-pass, which is precisely what AC6b/AC13 separate.
    """
    target = repo / "README.md"
    target.write_text("# bd66 fixture\n")
    assert _git(["add", "README.md"], repo, env).returncode == 0
    return target


def _write_driver(driver_dir: Path, lint_name: str, body: str) -> Path:
    """Write an executable `<lint_name>.py` driver.

    build_lint_commands emits argv[0] == f"{build_dir}/{name}.py" and the layer
    execs it directly, so the file needs a shebang and the exec bit.
    """
    driver_dir.mkdir(parents=True, exist_ok=True)
    path = driver_dir / f"{lint_name}.py"
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return path


def _exit_driver(driver_dir: Path, lint_name: str, code: int) -> Path:
    return _write_driver(driver_dir, lint_name, f"import sys\nsys.exit({code})\n")


def _hooks_path(repo: Path, env: dict) -> tuple[int, str]:
    """Effective value — LOCAL or inherited from global/system."""
    r = _git(["config", "--get", "core.hooksPath"], repo, env)
    return r.returncode, r.stdout.strip()


def _local_hooks_path(repo: Path, env: dict) -> tuple[int, str]:
    """Value the REPOSITORY owns (§2.1: `git config --get --local`)."""
    r = _git(["config", "--get", "--local", "core.hooksPath"], repo, env)
    return r.returncode, r.stdout.strip()


def _run_installer(args: list[str], env: dict) -> subprocess.CompletedProcess:
    """Invoke the (pre-GREEN absent) installer by absolute path."""
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def _assert_hook_is_executable(ac: str) -> None:
    """§2.3: `githooks/pre-commit` must exist and be executable.

    Git SILENTLY skips a hook without the exec bit — the commit would then
    succeed with no check performed at all, and AC5 would be green for the one
    reason the whole lot exists to prevent.

    WORKING TREE mode only: `mode & 0o111`. A clone taken under `umask 077` gets
    0o700, which is a correct checkout of a 100755 blob; demanding exactly 0o755
    would red a correct GREEN on someone else's machine.

    The VERSIONED mode (`100755` in the index) is a different fact about a
    different corpus, and it lives in AC15 rather than here. The clean-room lane
    ships the tree through `git archive` — tracked files only, no `.git` — so
    `git ls-files` has nothing to answer from. Folding a "if there is an index"
    condition into this helper would have kept the run green while the check
    quietly did not happen; AC15 skips with a stated reason instead, so its
    absence is a line in the report rather than silence.
    """
    assert HOOK_FILE.is_file(), (
        f"bd#66 {ac}: {HOOK_FILE} must exist and be a versioned file "
        f"(spec §2.3). Pre-GREEN it does not exist — FAIL expected here."
    )
    mode = stat.S_IMODE(HOOK_FILE.stat().st_mode)
    assert mode & 0o111, (
        f"bd#66 {ac}: {HOOK_FILE} must carry the exec bit, got {mode:#o}. Git "
        f"silently SKIPS a non-executable hook, so a wrong mode reads as a "
        f"passing commit with no enforcement whatsoever."
    )


def _repo_has_index() -> bool:
    """True when the corpus under test carries a git index to interrogate."""
    return (
        _git(["rev-parse", "--git-dir"], REPO_ROOT, dict(os.environ)).returncode == 0
    )


def _materialize_layer(repo: Path) -> None:
    """Expose the REAL production layer at the path the hook `exec`s.

    §1t: symlinks, not copies — the temp repo runs the very file GREEN writes,
    so no stub in the fixture can stand in for the production module. Nothing is
    ever written through these links: AC5/AC10 keep their drivers in tmp_path
    and point BD66_LINT_DIR at that directory.
    """
    (repo / "engine_py").mkdir(parents=True, exist_ok=True)
    os.symlink(
        PACKAGE_DIR, repo / "engine_py" / "bytedigger_engine", target_is_directory=True
    )
    os.symlink(GITHOOKS_DIR, repo / "githooks", target_is_directory=True)
    os.symlink(
        REPO_ROOT / "scripts", repo / "scripts", target_is_directory=True
    )


def _materialize_package_copy(clone: Path) -> Path:
    """Copy the two production modules into `clone/engine_py/bytedigger_engine`.

    A COPY, not a symlink of the package directory: AC11 and AC6c both write
    next to the registry (a driver, an appended registry line), and the real
    tree must never be written to. The copy still IS the production source —
    a stub cannot stand in for it, because the file copied is whatever GREEN
    writes.
    """
    pkg = clone / "engine_py" / "bytedigger_engine"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    shutil.copy2(PACKAGE_DIR / "precommit_lints.py", pkg / "precommit_lints.py")
    shutil.copy2(ENFORCE_MODULE, pkg / "precommit_enforce.py")
    # The executable entry point lives OUTSIDE the package (bd#44 AC7/AC11
    # forbid the package from path-hacking), so the copy must carry it too.
    scripts = clone / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENFORCE_CLI, scripts / "precommit_enforce_cli.py")
    return pkg


def _commit(repo: Path, env: dict, message: str = "bd66 fixture commit"):
    return _git(["commit", "-m", message], repo, env)


def _has_commit(repo: Path, env: dict) -> bool:
    return _git(["rev-parse", "--verify", "HEAD"], repo, env).returncode == 0


def _commit_count(repo: Path, env: dict) -> str:
    return _git(["rev-list", "--count", "HEAD"], repo, env).stdout.strip()


def _guarded_repo(tmp_path: Path) -> tuple[Path, dict, Path]:
    """Repo + env + driver dir for the two real-`git commit` ACs (AC5/AC10).

    The hook is installed through the REAL installer (not by hand-writing
    .git/hooks), so these ACs also exercise the install operation end to end.
    No PYTHONPATH is exported (§2.3).
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _materialize_layer(repo)

    driver_dir = tmp_path / "drivers"
    driver_dir.mkdir()
    env["BD66_LINT_DIR"] = str(driver_dir)

    install = _run_installer(["--install", "--root", str(repo)], env)
    assert install.returncode == 0, (
        f"bd#66 arrange: installer must install the hook into the temp repo, "
        f"got rc={install.returncode} stdout={install.stdout!r} stderr={install.stderr!r}. "
        f"Pre-GREEN: {INSTALLER} does not exist — FAIL expected here."
    )
    return repo, env, driver_dir


def _apply_git_env(monkeypatch, env: dict) -> None:
    """Mirror the hermetic git env onto os.environ for in-process ACs."""
    for key in _GIT_ENV_KEYS:
        monkeypatch.setenv(key, env[key])


# ─── AC1: --install on a repo with no core.hooksPath ──────────────────────────


def test_ac1_install_sets_hooks_path_on_clean_repo(tmp_path):
    """AC1: `--install --root R` on a repo without core.hooksPath → rc=0 and
    `git config --get core.hooksPath` == "githooks"; the installed hook file is
    executable (0o755, §2.3).

    Pre-GREEN: FAIL — neither scripts/install_git_hooks.py nor
    githooks/pre-commit exists; python exits 2 and core.hooksPath is never set.
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)

    rc_before, _ = _hooks_path(repo, env)
    assert rc_before != 0, (
        "bd#66 AC1 arrange: the temp repo must start with core.hooksPath unset; "
        "the hermetic git env failed to neutralise global config."
    )

    result = _run_installer(["--install", "--root", str(repo)], env)

    assert result.returncode == 0, (
        f"bd#66 AC1: expected rc=0 from --install, got {result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    rc_after, value = _local_hooks_path(repo, env)
    assert rc_after == 0 and value == "githooks", (
        f"bd#66 AC1: expected LOCAL core.hooksPath == 'githooks' after install, "
        f"got rc={rc_after} value={value!r}."
    )
    _assert_hook_is_executable("AC1")


# ─── AC2: --install is idempotent ─────────────────────────────────────────────


def test_ac2_install_is_idempotent(tmp_path):
    """AC2: a second `--install` → rc=0, value still exactly "githooks", and the
    setting is not duplicated (`--get-all` yields exactly one line).

    Pre-GREEN: FAIL — the installer is absent, so even the first call is
    non-zero and no value is ever written.
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)

    first = _run_installer(["--install", "--root", str(repo)], env)
    second = _run_installer(["--install", "--root", str(repo)], env)

    assert first.returncode == 0, (
        f"bd#66 AC2: first --install must return 0, got {first.returncode}. "
        f"stderr={first.stderr!r}"
    )
    assert second.returncode == 0, (
        f"bd#66 AC2: repeated --install must return 0 (idempotent), got "
        f"{second.returncode}. stderr={second.stderr!r}"
    )

    all_values = _git(
        ["config", "--get-all", "--local", "core.hooksPath"], repo, env
    )
    lines = [ln for ln in all_values.stdout.splitlines() if ln.strip()]
    assert lines == ["githooks"], (
        f"bd#66 AC2: expected exactly one LOCAL core.hooksPath entry == "
        f"'githooks' after two installs, got {lines!r} (duplicated install)."
    )


# ─── AC3: foreign LOCAL core.hooksPath is refused, not overwritten ────────────


def test_ac3_install_refuses_foreign_local_hooks_path(tmp_path):
    """AC3: repo whose LOCAL core.hooksPath is `other-hooks` → `--install`
    rc=1, the value is NOT changed, and stderr names `other-hooks`.

    The value is planted with `git config --local` and the assertion reads it
    back with `--get --local`, so this AC is about a value the REPOSITORY owns;
    AC3b covers the global-only case, which must behave differently.

    Pre-GREEN: FAIL — no installer, so rc is 2 rather than the specified 1 and
    nothing names the foreign value.
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    assert (
        _git(["config", "--local", "core.hooksPath", "other-hooks"], repo, env).returncode
        == 0
    )

    result = _run_installer(["--install", "--root", str(repo)], env)

    assert result.returncode == 1, (
        f"bd#66 AC3: expected rc=1 when the LOCAL core.hooksPath is foreign, got "
        f"{result.returncode}. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    _, value = _local_hooks_path(repo, env)
    assert value == "other-hooks", (
        f"bd#66 AC3: the installer must NOT overwrite a foreign LOCAL "
        f"core.hooksPath; expected 'other-hooks', got {value!r}."
    )
    assert "other-hooks" in result.stderr, (
        f"bd#66 AC3: stderr must name the conflicting value 'other-hooks', got "
        f"{result.stderr!r}."
    )


# ─── AC3b: a GLOBAL core.hooksPath is neither an install nor a conflict ───────


def test_ac3b_global_hooks_path_is_not_a_local_installation(tmp_path):
    """AC3b (§2.1, `git config --get --local`): a repository whose GLOBAL config
    sets core.hooksPath while the LOCAL value is unset.

    Two halves, both of which an implementation reading the EFFECTIVE value gets
    wrong:
      - `--check` must return 1, not 0. A developer's ~/.gitconfig is not an
        installation of this repository's hooks; reading it as one ships an
        unguarded repo that reports itself guarded.
      - `--install` must return 0 and write the LOCAL value, not refuse with
        AC3's foreign-value branch over a value the repository does not own.

    Pre-GREEN: FAIL — no installer, so `--check` exits 2 (not 1) and no local
    value is ever written.
    """
    env = _hermetic_git_env(tmp_path)
    Path(env["GIT_CONFIG_GLOBAL"]).write_text("[core]\n\thooksPath = global-hooks\n")
    repo = _init_repo(tmp_path, env)

    rc_effective, effective = _hooks_path(repo, env)
    assert rc_effective == 0 and effective == "global-hooks", (
        f"bd#66 AC3b arrange: the fixture must make the GLOBAL core.hooksPath "
        f"visible to the repo, got rc={rc_effective} value={effective!r}."
    )
    rc_local, _ = _local_hooks_path(repo, env)
    assert rc_local != 0, (
        "bd#66 AC3b arrange: the LOCAL core.hooksPath must start unset; the "
        "fixture wrote it to the wrong scope."
    )

    check = _run_installer(["--check", "--root", str(repo)], env)
    assert check.returncode == 1, (
        f"bd#66 AC3b: `--check` must return 1 when only the GLOBAL config sets "
        f"core.hooksPath — a global value is not an installation of THIS repo. "
        f"Got {check.returncode}. stdout={check.stdout!r} stderr={check.stderr!r}"
    )

    install = _run_installer(["--install", "--root", str(repo)], env)
    assert install.returncode == 0, (
        f"bd#66 AC3b: `--install` must return 0 over a global-only value (the "
        f"AC3 foreign-value refusal applies to LOCAL values only), got "
        f"{install.returncode}. stderr={install.stderr!r}"
    )
    rc_after, local_value = _local_hooks_path(repo, env)
    assert rc_after == 0 and local_value == "githooks", (
        f"bd#66 AC3b: `--install` must write the LOCAL core.hooksPath == "
        f"'githooks', got rc={rc_after} value={local_value!r}."
    )


# ─── AC4: --check before/after install ────────────────────────────────────────


def test_ac4_check_returns_one_before_install_and_zero_after(tmp_path):
    """AC4: `--check` → rc=1 before installation, rc=0 after it, with the value
    read back from the LOCAL scope.

    Pre-GREEN: FAIL — no installer, so `--check` cannot return 1-then-0 around
    an install that never happens (both calls exit 2).
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)

    before = _run_installer(["--check", "--root", str(repo)], env)
    assert before.returncode == 1, (
        f"bd#66 AC4: expected --check rc=1 before install, got "
        f"{before.returncode}. stderr={before.stderr!r}"
    )

    installed = _run_installer(["--install", "--root", str(repo)], env)
    assert installed.returncode == 0, (
        f"bd#66 AC4: --install must succeed before the second --check, got "
        f"{installed.returncode}. stderr={installed.stderr!r}"
    )

    after = _run_installer(["--check", "--root", str(repo)], env)
    assert after.returncode == 0, (
        f"bd#66 AC4: expected --check rc=0 after install, got "
        f"{after.returncode}. stderr={after.stderr!r}"
    )
    rc_local, local_value = _local_hooks_path(repo, env)
    assert rc_local == 0 and local_value == "githooks", (
        f"bd#66 AC4: after --install the LOCAL value must be 'githooks', got "
        f"rc={rc_local} value={local_value!r}."
    )


# ─── AC5: a real `git commit` is REFUSED and no commit object appears ─────────


def test_ac5_real_git_commit_is_refused_and_no_commit_object_exists(tmp_path):
    """AC5 (§1l forcing pair, side 1): hook installed, a driver on disk exits 1
    for the staged `test_x.py` → `git commit` rc != 0 AND the git object
    database holds NO commit (HEAD unresolvable, rev-list unusable). The
    `BD66-REFUSE-VIOLATION` token is an ADDITIONAL assertion, never the
    load-bearing one.

    The hook's exec bit is asserted here too (§2.3): without it git would skip
    the hook and this AC would go green over zero enforcement.

    Pre-GREEN: FAIL — there is no installer and no githooks/pre-commit, so
    `git commit` is unguarded: it succeeds and a commit object appears. (The
    arrange step's installer assertion trips first, which is the same absence.)
    """
    repo, env, driver_dir = _guarded_repo(tmp_path)
    _assert_hook_is_executable("AC5")
    _exit_driver(driver_dir, "stub-passability-lint", 1)
    _stage_test_file(repo, env)

    result = _commit(repo, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"bd#66 AC5: `git commit` must be REFUSED when a driver reports a "
        f"violation, got rc=0. output={output!r}"
    )
    assert not _has_commit(repo, env), (
        f"bd#66 AC5: no commit object may exist after a refused commit; "
        f"`git rev-parse --verify HEAD` resolved. rev-list count="
        f"{_commit_count(repo, env)!r}. Refusing to print and committing anyway "
        f"is exactly the defect this lot removes."
    )
    assert REFUSE_VIOLATION in output, (
        f"bd#66 AC5: expected the {REFUSE_VIOLATION} token in the commit "
        f"output, got {output!r}."
    )


# ─── AC6: an undeclared name without a driver refuses ─────────────────────────


def test_ac6_undeclared_name_without_driver_refuses(tmp_path, monkeypatch, capsys):
    """AC6: registry carrying `phantom-lint`, no driver on disk, not listed in
    DECLARED_ABSENT → the layer returns 1 and its output contains
    `BD66-REFUSE-MISSING-DRIVER` and the name `phantom-lint`.

    This is the invariant of §2.2: a name in the registry must have either a
    driver on disk or an entry in DECLARED_ABSENT. Adding a 13th name without a
    driver must refuse the commit rather than read as an enabled gate.

    DECLARED_ABSENT is set consistently with the PATCHED registry (gate
    MINOR-3): every patched name except `phantom-lint`, and nothing the patch
    removed. Otherwise the displaced real TEST_LINTS names would sit orphaned in
    DECLARED_ABSENT, the §2.2 reverse branch would fire at the same time, and
    this AC would no longer isolate the forward branch it is about.

    Pre-GREEN: FAIL — the assertion below fires:
    bytedigger_engine/precommit_enforce.py does not exist.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC6: {ENFORCE_MODULE} must exist (spec §4.3). Pre-GREEN it does "
        f"not — FAIL expected here, as an assertion and not an import accident."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_test_file(repo, env)

    driver_dir = tmp_path / "drivers"
    driver_dir.mkdir()

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(driver_dir))
    monkeypatch.chdir(repo)

    patched_tests = ["phantom-lint"]
    monkeypatch.setattr(precommit_lints, "TEST_LINTS", patched_tests, raising=True)
    monkeypatch.setattr(
        precommit_lints,
        "DECLARED_ABSENT",
        _declared_for(
            precommit_lints.SPEC_LINTS,
            patched_tests,
            precommit_lints.TS_TEST_LINTS,
            violators=["phantom-lint"],
        ),
        raising=False,
    )

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc == 1, (
        f"bd#66 AC6: expected rc=1 for an undeclared name with no driver, got "
        f"{rc!r}. output={output!r}"
    )
    assert REFUSE_MISSING_DRIVER in output, (
        f"bd#66 AC6: expected {REFUSE_MISSING_DRIVER} in the output, got {output!r}."
    )
    assert _output_names_lint(output, "phantom-lint"), (
        f"bd#66 AC6: the refusal must name the offending lint 'phantom-lint' as "
        f"a whole token (a printed 'phantom-lint-2' does not count), got "
        f"{output!r}."
    )


# ─── AC6b: the pre-pass is REGISTRY-scoped, not plan-scoped ───────────────────


def test_ac6b_prepass_is_registry_scoped_with_nothing_classifiable_staged(
    tmp_path, monkeypatch, capsys
):
    """AC6b (§2.1, "the pre-pass is REGISTRY-scoped, not plan-scoped"):
    `phantom-spec-lint` is added to SPEC_LINTS, has no driver and is not
    declared absent, and the ONLY staged path is `README.md` — which
    `classify_staged` puts in no bucket at all, so `nothing_to_lint` is True and
    the plan is empty.

    The layer must still return 1 with `BD66-REFUSE-MISSING-DRIVER` naming
    `phantom-spec-lint`. A plan-scoped implementation returns 0 here and dies:
    with 0 `*_spec.md` files in bytedigger (§1b), a plan-scoped pre-pass would
    be inert for 10 of the 12 names, and a new name would keep reading as an
    enabled gate — the exact danger the issue body names.

    As in AC6, DECLARED_ABSENT matches the PATCHED registry minus the intended
    violator (gate MINOR-3), so only the forward pre-pass branch can fire.

    Pre-GREEN: FAIL — the assertion below fires:
    bytedigger_engine/precommit_enforce.py does not exist.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC6b: {ENFORCE_MODULE} must exist (spec §4.3). Pre-GREEN it "
        f"does not — FAIL expected here, as an assertion not an import accident."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_readme(repo, env)

    driver_dir = tmp_path / "drivers"
    driver_dir.mkdir()

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(driver_dir))
    monkeypatch.chdir(repo)

    patched_specs = ["phantom-spec-lint"]
    monkeypatch.setattr(precommit_lints, "SPEC_LINTS", patched_specs, raising=True)
    monkeypatch.setattr(
        precommit_lints,
        "DECLARED_ABSENT",
        _declared_for(
            patched_specs,
            precommit_lints.TEST_LINTS,
            precommit_lints.TS_TEST_LINTS,
            violators=["phantom-spec-lint"],
        ),
        raising=False,
    )

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    staged = _git(["diff", "--cached", "--name-only"], repo, env).stdout.split()
    assert staged == ["README.md"], (
        f"bd#66 AC6b arrange: exactly one staged path, README.md, is required "
        f"so that nothing classifies as a spec; got {staged!r}."
    )
    assert precommit_lints.nothing_to_lint(precommit_lints.classify_staged(staged)), (
        "bd#66 AC6b arrange: README.md must classify as nothing to lint, so a "
        "plan-scoped implementation genuinely has an empty plan here."
    )

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc == 1, (
        f"bd#66 AC6b: the registry pre-pass runs BEFORE nothing_to_lint and "
        f"sweeps every registry name regardless of what is staged — expected "
        f"rc=1, got {rc!r}. rc=0 here means the pre-pass is plan-scoped. "
        f"output={output!r}"
    )
    assert REFUSE_MISSING_DRIVER in output, (
        f"bd#66 AC6b: expected {REFUSE_MISSING_DRIVER} in the output, got "
        f"{output!r}."
    )
    assert _output_names_lint(output, "phantom-spec-lint"), (
        f"bd#66 AC6b: the refusal must name 'phantom-spec-lint' as a whole "
        f"token, got {output!r}."
    )


# ─── AC7: the REAL registry keeps the repository committable ──────────────────


def test_ac7_real_registry_twelve_declared_absent_returns_zero(
    tmp_path, monkeypatch, capsys
):
    """AC7: the repository's OWN registry — 12 names, 0 drivers on disk, all 12
    listed in DECLARED_ABSENT — must leave the repository committable: with
    `test_x.py` staged the layer returns 0.

    The denominator is asserted explicitly (12), so the AC cannot silently
    weaken if names are added or removed without a matching declaration. This AC
    runs through the BD66_LINT_DIR override; the canonical-default path is AC11
    and AC12, not this one.

    Pre-GREEN: FAIL — precommit_lints has no DECLARED_ABSENT attribute at all
    (0 declared against a denominator of 12), and the enforcement layer does not
    exist.
    """
    names = ALL_REGISTRY_NAMES
    assert len(names) == 12, (
        f"bd#66 AC7: the live registry denominator must be 12 names "
        f"(SPEC 9 + TEST 2 + TS 1), got {len(names)}: {names!r}."
    )

    on_disk = sorted(
        str(p)
        for name in names
        for p in REPO_ROOT.rglob(f"{name}.py")
        if ".git" not in p.parts
    )
    assert on_disk == [], (
        f"bd#66 AC7: the live baseline is 0/{len(names)} drivers on disk; "
        f"found {on_disk!r}. If a driver has landed, its name must leave "
        f"DECLARED_ABSENT and this AC must be re-baselined."
    )

    declared = list(getattr(precommit_lints, "DECLARED_ABSENT", []))
    assert len(declared) == 12 and set(declared) == set(names), (
        f"bd#66 AC7: expected all 12/{len(names)} registry names declared "
        f"absent, got {len(declared)}: {declared!r}."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_test_file(repo, env)

    empty_driver_dir = tmp_path / "drivers"
    empty_driver_dir.mkdir()

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(empty_driver_dir))
    monkeypatch.chdir(repo)

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc == 0, (
        f"bd#66 AC7: with 12/12 names declared absent the repository must stay "
        f"committable — expected rc=0, got {rc!r}. output={output!r}"
    )


# ─── AC8: a driver exiting rc=2 is a refusal, not a pass ──────────────────────


def test_ac8_driver_exiting_two_refuses(tmp_path, monkeypatch, capsys):
    """AC8: a driver that exits with rc=2 (neither clean nor violation) → the
    layer returns non-zero, and ONE line of output carries
    `BD66-REFUSE-DRIVER-ERROR`, the lint name and `rc=2` together.

    The three facts must appear on the SAME line: a bare `"2" in output` is
    satisfied by any tmp path containing the digit, which is not evidence that
    the driver's exit code was reported at all.

    Fail-closed: the load-bearing assertion is the non-zero return, not a
    logged message — a driver whose contract broke must not be read as a pass.

    Pre-GREEN: FAIL — the assertion below fires:
    bytedigger_engine/precommit_enforce.py does not exist.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC8: {ENFORCE_MODULE} must exist (spec §4.3). Pre-GREEN it does "
        f"not — FAIL expected here, as an assertion and not an import accident."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_test_file(repo, env)

    driver_dir = tmp_path / "drivers"
    _exit_driver(driver_dir, "stub-passability-lint", 2)

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(driver_dir))
    monkeypatch.chdir(repo)

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc != 0, (
        f"bd#66 AC8: a driver exiting 2 must fail closed — expected non-zero, "
        f"got {rc!r}. output={output!r}"
    )
    anchored = [
        line
        for line in output.splitlines()
        if REFUSE_DRIVER_ERROR in line
        and "stub-passability-lint" in line
        and "rc=2" in line
    ]
    assert anchored, (
        f"bd#66 AC8: exactly one line must carry {REFUSE_DRIVER_ERROR}, the "
        f"lint name 'stub-passability-lint' and 'rc=2' TOGETHER; none of the "
        f"output lines does. output={output!r}"
    )


# ─── AC9: a planned driver that vanishes before exec refuses ──────────────────


def test_ac9_driver_missing_at_run_time_refuses(tmp_path, monkeypatch, capsys):
    """AC9: a driver present during the registry pre-pass but gone when the
    command is executed → the layer returns non-zero with
    `BD66-REFUSE-DRIVER-ERROR`; no silent `continue`.

    Cite (§2.1, "driver presence is evaluated ONCE, in the pre-pass"): a driver
    that disappears AFTER the pre-pass is an execution error (outcome 3), not an
    undeclared name (outcome 1). Hence DRIVER-ERROR here and MISSING-DRIVER in
    AC6/AC6b — the two are distinguished by WHEN the absence is observed.

    §1i pre-staging (no race): the vanish is produced deterministically and
    sequentially — the FIRST planned driver (`driver-a-lint`) deletes the
    SECOND driver's file and exits 0. Both names carry a driver at pre-pass
    time and neither is in DECLARED_ABSENT, so the pre-pass is clean and the
    only possible refusal comes from execution. SPEC_LINTS/TS_TEST_LINTS are
    emptied so the pre-pass has nothing else to say. No sleeps, no contended
    resource, no timing window.

    Cite (§2.1, "the plan's commands are executed SEQUENTIALLY, in the order
    returned by build_lint_commands; there is no parallelism"): the deletion is
    ordered only because `driver-a-lint` precedes `vanishing-lint` in the patched
    TEST_LINTS, and build_lint_commands emits per staged file in list order.
    DECLARED_ABSENT is emptied to match the patched registry exactly — both
    patched names have a driver at pre-pass time, so neither pre-pass branch can
    fire and the only possible refusal comes from execution.

    Pre-GREEN: FAIL — the assertion below fires:
    bytedigger_engine/precommit_enforce.py does not exist.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC9: {ENFORCE_MODULE} must exist (spec §4.3). Pre-GREEN it does "
        f"not — FAIL expected here, as an assertion and not an import accident."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_test_file(repo, env)

    driver_dir = tmp_path / "drivers"
    vanishing = _exit_driver(driver_dir, "vanishing-lint", 0)
    _write_driver(
        driver_dir,
        "driver-a-lint",
        "import os, sys\n" f"os.remove({str(vanishing)!r})\n" "sys.exit(0)\n",
    )

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(driver_dir))
    monkeypatch.chdir(repo)

    monkeypatch.setattr(precommit_lints, "SPEC_LINTS", [], raising=True)
    monkeypatch.setattr(precommit_lints, "TS_TEST_LINTS", [], raising=True)
    monkeypatch.setattr(
        precommit_lints, "TEST_LINTS", ["driver-a-lint", "vanishing-lint"], raising=True
    )
    monkeypatch.setattr(precommit_lints, "DECLARED_ABSENT", [], raising=False)

    ordered = [
        c["lint"]
        for c in precommit_lints.build_lint_commands(
            [], ["test_x.py"], str(driver_dir)
        )
    ]
    assert ordered == ["driver-a-lint", "vanishing-lint"], (
        f"bd#66 AC9 arrange (§2.1 sequential order): build_lint_commands must "
        f"emit the deleting driver BEFORE the vanishing one, got {ordered!r}. "
        f"Without that order the pre-staged vanish is not deterministic."
    )

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert not vanishing.exists(), (
        "bd#66 AC9 arrange: the first driver must have deleted the second "
        "driver's file; the pre-staged vanish did not happen."
    )
    assert rc != 0, (
        f"bd#66 AC9: a driver that disappears between the pre-pass and exec must "
        f"fail closed — expected non-zero, got {rc!r}. A silent `continue` here "
        f"is the muted-signal defect. output={output!r}"
    )
    assert REFUSE_DRIVER_ERROR in output, (
        f"bd#66 AC9: expected {REFUSE_DRIVER_ERROR} (execution error, not "
        f"{REFUSE_MISSING_DRIVER}: presence was already established in the "
        f"pre-pass) in the output, got {output!r}."
    )


# ─── AC10: a real `git commit` SUCCEEDS when the drivers are clean ────────────


def test_ac10_real_git_commit_succeeds_when_drivers_are_clean(tmp_path):
    """AC10 (§1l forcing pair, side 2): same guarded repo, the driver exits 0 →
    `git commit` rc=0 AND `git rev-list --count HEAD` == "1", i.e. exactly one
    commit object was written to the git object database.

    Together with AC5 this is two-sided: an always-refuse stub fails here, an
    always-pass stub fails AC5.

    Pre-GREEN: FAIL — there is no installer to install a hook with, so the
    guarded repo the AC is about cannot even be arranged.
    """
    repo, env, driver_dir = _guarded_repo(tmp_path)
    _exit_driver(driver_dir, "stub-passability-lint", 0)
    _stage_test_file(repo, env)

    result = _commit(repo, env)
    output = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"bd#66 AC10: `git commit` must SUCCEED when every driver is clean, got "
        f"rc={result.returncode}. output={output!r}"
    )
    assert _commit_count(repo, env) == "1", (
        f"bd#66 AC10: expected exactly one commit object in the repository, "
        f"`git rev-list --count HEAD` returned "
        f"{_commit_count(repo, env)!r}. output={output!r}"
    )


# ─── AC11: the CANONICAL driver directory, with BD66_LINT_DIR deleted ─────────


def test_ac11_refuses_with_env_override_deleted_using_default_lint_dir(
    tmp_path, monkeypatch
):
    """AC11 (§2.1a, production resolution path): `BD66_LINT_DIR` is DELETED from
    the environment; the package is materialised as a COPY under tmp_path
    (precommit_lints.py + precommit_enforce.py — the real package directory is
    never symlinked here, because this AC plants a driver next to the registry
    and must not write into the real tree); the copied registry drops
    `stub-passability-lint` from DECLARED_ABSENT and gets a driver of that name,
    exiting 1, sitting beside it — i.e. inside DEFAULT_LINT_DIR.

    Expected: rc=1 and `BD66-REFUSE-VIOLATION`. The implementation
    `if not os.environ.get("BD66_LINT_DIR"): return 0` passes AC12 and dies
    here, so this AC pins the PLAN branch on the canonical path.

    It does NOT pin the PRE-PASS on the canonical path — the round-2 gate found
    exactly that hole: `if override: prepass(...)` satisfies this AC through the
    plan branch while the pre-pass never runs in the real repository. AC6c is
    the AC that closes it.

    The module is run BY PATH in a subprocess with no PYTHONPATH (§2.3), so it
    must bootstrap its own sys.path — and the copy's engine root, inserted at
    position 0 by the module itself, is what makes `precommit_lints` resolve to
    the COPY rather than to the real package.

    Pre-GREEN: FAIL — precommit_enforce.py does not exist, so there is nothing
    to copy; DECLARED_ABSENT does not exist either.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC11: {ENFORCE_MODULE} must exist to be copied into the "
        f"materialised package. Pre-GREEN it does not — FAIL expected here."
    )
    assert hasattr(precommit_lints, "DECLARED_ABSENT"), (
        "bd#66 AC11: precommit_lints must export DECLARED_ABSENT (§2.2). "
        "Pre-GREEN it does not."
    )

    env = _hermetic_git_env(tmp_path)
    monkeypatch.delenv("BD66_LINT_DIR", raising=False)
    env.pop("BD66_LINT_DIR", None)

    clone = _init_repo(tmp_path, env, name="clone")
    pkg = _materialize_package_copy(clone)

    # Format-independent edit of the COPY: rebind DECLARED_ABSENT after the
    # original definition so the name we are about to give a driver to is no
    # longer licensed to be missing.
    with (pkg / "precommit_lints.py").open("a") as fh:
        fh.write(
            "\nDECLARED_ABSENT = [n for n in DECLARED_ABSENT "
            'if n != "stub-passability-lint"]\n'
        )

    _exit_driver(pkg, "stub-passability-lint", 1)
    _stage_test_file(clone, env)

    result = subprocess.run(
        [sys.executable, str(clone / "scripts" / "precommit_enforce_cli.py")],
        cwd=str(clone),
        env=env,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assert "BD66_LINT_DIR" not in env, (
        "bd#66 AC11 arrange: BD66_LINT_DIR must be absent from the child's "
        "environment; the fixture leaked the override."
    )
    assert result.returncode == 1, (
        f"bd#66 AC11: with BD66_LINT_DIR DELETED the layer must resolve drivers "
        f"from DEFAULT_LINT_DIR (the registry's own directory) and REFUSE the "
        f"violation — expected rc=1, got {result.returncode}. rc=0 here is the "
        f"early-exit-on-unset-variable construction the gate rejected. "
        f"output={output!r}"
    )
    assert REFUSE_VIOLATION in output, (
        f"bd#66 AC11: expected {REFUSE_VIOLATION} in the output, got {output!r}."
    )


# ─── AC12: BD66_LINT_DIR deleted, REAL registry, docs-only commit passes ──────


def test_ac12_real_registry_with_env_override_deleted_returns_zero(
    tmp_path, monkeypatch, capsys
):
    """AC12 (§2.1a, other side of the pair): `BD66_LINT_DIR` is DELETED, the
    REAL repository registry is in force (12 names, 0 drivers, 12 declared
    absent) and the only staged path is `README.md`.

    Expected rc=0: the registry pre-pass DID run — it runs on every commit,
    docs-only included (§2.1) — and had nothing to refuse, and there is no plan
    work to do. An implementation that always refuses when the variable is unset
    dies here; one that always returns 0 when it is unset dies in AC11.

    Pre-GREEN: FAIL — precommit_lints exports neither DEFAULT_LINT_DIR nor
    DECLARED_ABSENT, and the enforcement layer does not exist.
    """
    default_lint_dir = getattr(precommit_lints, "DEFAULT_LINT_DIR", None)
    expected_lint_dir = os.path.realpath(str(PACKAGE_DIR))
    assert default_lint_dir is not None and (
        os.path.realpath(str(default_lint_dir)) == expected_lint_dir
    ), (
        f"bd#66 AC12 (§2.1a): precommit_lints.DEFAULT_LINT_DIR must be the "
        f"registry module's own directory {expected_lint_dir!r}, got "
        f"{default_lint_dir!r}. This is the single canonical source the layer "
        f"defaults to. §1j: BOTH sides go through os.path.realpath, because on "
        f"macOS /tmp is a symlink to /private/tmp and comparing an unexpanded "
        f"path with an expanded one would red a correct GREEN."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_readme(repo, env)

    _apply_git_env(monkeypatch, env)
    monkeypatch.delenv("BD66_LINT_DIR", raising=False)
    monkeypatch.chdir(repo)

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc == 0, (
        f"bd#66 AC12: with BD66_LINT_DIR deleted and 12/12 names declared "
        f"absent, a docs-only commit must stay possible — expected rc=0, got "
        f"{rc!r}. output={output!r}"
    )


# ─── AC13: DECLARED_ABSENT ⊆ registry closes the other way ────────────────────


def test_ac13_declared_absent_entry_matching_no_registry_name_refuses(
    tmp_path, monkeypatch, capsys
):
    """AC13 (§2.2, closure in both directions): DECLARED_ABSENT carries
    `stale-renamed-lint`, which appears in none of SPEC_LINTS, TEST_LINTS or
    TS_TEST_LINTS — the residue of a rename → the layer returns 1 with
    `BD66-REFUSE-MISSING-DRIVER` naming `stale-renamed-lint`.

    Without this half, a rename silently strips a name of its driver
    requirement: the old entry keeps licensing an absence nobody looks for.

    The registry itself is NOT patched here, and all 12 real names stay declared,
    so `stale-renamed-lint` is the ONLY violator. §2.1 says the pre-pass
    accumulates and prints every violator, which makes the negative assertion
    meaningful: an innocent, properly declared name must NOT be named in the
    refusal. A pre-pass that dumps the whole registry on any failure passes the
    positive assertion and dies here.

    Pre-GREEN: FAIL — the assertion below fires:
    bytedigger_engine/precommit_enforce.py does not exist.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC13: {ENFORCE_MODULE} must exist (spec §4.3). Pre-GREEN it "
        f"does not — FAIL expected here, as an assertion not an import accident."
    )

    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)
    _stage_readme(repo, env)

    driver_dir = tmp_path / "drivers"
    driver_dir.mkdir()

    _apply_git_env(monkeypatch, env)
    monkeypatch.setenv("BD66_LINT_DIR", str(driver_dir))
    monkeypatch.chdir(repo)

    assert "stale-renamed-lint" not in ALL_REGISTRY_NAMES, (
        "bd#66 AC13 arrange: 'stale-renamed-lint' must not be a real registry "
        "name; the fixture no longer describes an orphaned declaration."
    )
    monkeypatch.setattr(
        precommit_lints,
        "DECLARED_ABSENT",
        [*ALL_REGISTRY_NAMES, "stale-renamed-lint"],
        raising=False,
    )

    from bytedigger_engine import precommit_enforce  # noqa: PLC0415

    rc = precommit_enforce.main([])
    output = "".join(capsys.readouterr())

    assert rc == 1, (
        f"bd#66 AC13: a DECLARED_ABSENT entry matching no registry name must "
        f"refuse — expected rc=1, got {rc!r}. output={output!r}"
    )
    assert REFUSE_MISSING_DRIVER in output, (
        f"bd#66 AC13: expected {REFUSE_MISSING_DRIVER} in the output, got "
        f"{output!r}."
    )
    assert _output_names_lint(output, "stale-renamed-lint"), (
        f"bd#66 AC13: the refusal must name the orphaned declaration "
        f"'stale-renamed-lint' as a whole token, got {output!r}."
    )
    innocent = "forbidden-import-lint"
    assert innocent in ALL_REGISTRY_NAMES, (
        f"bd#66 AC13 arrange: {innocent!r} must be a real, properly declared "
        f"registry name for the negative assertion to mean anything."
    )
    assert not _output_names_lint(output, innocent), (
        f"bd#66 AC13: the pre-pass accumulates and prints exactly the violators "
        f"(§2.1); {innocent!r} is declared absent and is not one, yet it appears "
        f"in the refusal. output={output!r}"
    )


# ─── AC6c: the PRE-PASS refuses a real commit on the CANONICAL path ───────────


def test_ac6c_prepass_refuses_real_commit_on_canonical_path(tmp_path, monkeypatch):
    """AC6c (§2.1a, the killing half of the AC6c/AC12 pair): the registry
    pre-pass must refuse an actual `git commit` when the driver directory is
    resolved from the CANON, with `BD66_LINT_DIR` deleted from the environment.

    Fixture (AC11's, end to end): the package is materialised as a COPY under
    tmp_path, `BD66_LINT_DIR` and `PYTHONPATH` are both deleted, the COPIED
    registry gets `phantom-canon-lint` appended to TEST_LINTS — no driver, and
    not in DECLARED_ABSENT — the hook is installed with the REAL installer, and
    the ONLY staged path is `README.md`, which classifies as nothing at all.

    Expected: `git commit` rc != 0, NO commit object in the object database, and
    the output carries `BD66-REFUSE-MISSING-DRIVER` and `phantom-canon-lint`.

    This is what round 2 could not see. That construction —

        override = os.environ.get("BD66_LINT_DIR")
        lint_dir = override or DEFAULT_LINT_DIR
        if override:            # pre-pass gated on the override
            rc = prepass(present)
            if rc: return rc
        ... plan ...

    passed all fifteen ACs and never refused in the real repository, because the
    variable is not set in production and AC11's refusal came from the PLAN
    branch. Here there is no plan at all (README.md only) and no override, so
    the ONLY thing that can produce a refusal is the pre-pass running on the
    canonical path. §2.1a: a gate on `BD66_LINT_DIR` is forbidden on EVERY
    stretch of the layer, not just at the entry to `main`.

    §1l: the load-bearing assertion is the production side-effect — whether a
    commit object exists in the git object database — not the printed token.

    Pre-GREEN: FAIL — precommit_enforce.py does not exist (asserted first), and
    with no installer and no hook the commit would be entirely unguarded.
    """
    assert ENFORCE_MODULE.is_file(), (
        f"bd#66 AC6c: {ENFORCE_MODULE} must exist to be copied into the "
        f"materialised package. Pre-GREEN it does not — FAIL expected here."
    )

    env = _hermetic_git_env(tmp_path)
    monkeypatch.delenv("BD66_LINT_DIR", raising=False)
    env.pop("BD66_LINT_DIR", None)
    env.pop("PYTHONPATH", None)

    clone = _init_repo(tmp_path, env, name="canon-clone")
    pkg = _materialize_package_copy(clone)

    # Append to the COPY only: a 13th name with no driver, deliberately absent
    # from DECLARED_ABSENT. Format-independent, so a reformatting of the real
    # registry cannot silently defuse this fixture.
    with (pkg / "precommit_lints.py").open("a") as fh:
        fh.write('\nTEST_LINTS = list(TEST_LINTS) + ["phantom-canon-lint"]\n')

    assert "phantom-canon-lint" not in list(
        getattr(precommit_lints, "DECLARED_ABSENT", [])
    ), (
        "bd#66 AC6c arrange: 'phantom-canon-lint' must NOT be declared absent — "
        "the whole point is an undeclared name with no driver."
    )
    assert not (pkg / "phantom-canon-lint.py").exists(), (
        "bd#66 AC6c arrange: no driver may exist for 'phantom-canon-lint'."
    )

    os.symlink(GITHOOKS_DIR, clone / "githooks", target_is_directory=True)
    install = _run_installer(["--install", "--root", str(clone)], env)
    assert install.returncode == 0, (
        f"bd#66 AC6c arrange: the REAL installer must install the hook, got "
        f"rc={install.returncode} stderr={install.stderr!r}. Pre-GREEN "
        f"{INSTALLER} does not exist — FAIL expected here."
    )
    _assert_hook_is_executable("AC6c")

    _stage_readme(clone, env)
    staged = _git(["diff", "--cached", "--name-only"], clone, env).stdout.split()
    assert staged == ["README.md"], (
        f"bd#66 AC6c arrange: exactly one staged path, README.md, so the plan is "
        f"empty and only the pre-pass can refuse; got {staged!r}."
    )
    assert "BD66_LINT_DIR" not in env and "PYTHONPATH" not in env, (
        "bd#66 AC6c arrange: neither BD66_LINT_DIR nor PYTHONPATH may reach the "
        "commit's environment; the fixture leaked one of them."
    )

    result = _commit(clone, env)
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"bd#66 AC6c: `git commit` must be REFUSED — an undeclared registry name "
        f"has no driver in DEFAULT_LINT_DIR and BD66_LINT_DIR is unset. rc=0 "
        f"here means the pre-pass is gated on the override and never runs in the "
        f"real repository. output={output!r}"
    )
    assert not _has_commit(clone, env), (
        f"bd#66 AC6c: no commit object may exist after the pre-pass refuses; "
        f"`git rev-parse --verify HEAD` resolved (rev-list count="
        f"{_commit_count(clone, env)!r}). Printing a verdict and committing "
        f"anyway is the exact defect this lot removes. output={output!r}"
    )
    assert REFUSE_MISSING_DRIVER in output, (
        f"bd#66 AC6c: expected {REFUSE_MISSING_DRIVER} in the commit output, got "
        f"{output!r}."
    )
    assert _output_names_lint(output, "phantom-canon-lint"), (
        f"bd#66 AC6c: the refusal must name 'phantom-canon-lint' as a whole "
        f"token, got {output!r}."
    )


# ─── AC14: installer argument edges ───────────────────────────────────────────


def test_ac14_installer_argument_edges(tmp_path):
    """AC14 (§2.1, "argument edges stated explicitly"), three of them:

      - NO flags behaves exactly as `--check`: rc=1 before an installation and
        rc=0 after one. A bare invocation must never install by accident.
      - `--install --check` together is a USAGE error, rc=2 — distinct from both
        the success (0) and the refusal (1) codes, so a caller cannot read a
        malformed invocation as a verdict.
      - `--root` pointing at a non-git directory is rc=1 and writes NOTHING:
        neither a local value (there is no repository to hold one) nor a global
        one. The global side is asserted twice — through `git config --global`
        and by reading the config FILE — because an installer that falls back to
        the global scope on a missing repo would silently reconfigure a
        developer's machine.

    Pre-GREEN: FAIL — no installer exists, so the bare call exits 2 instead of
    1-then-0 and the usage error is never raised.
    """
    env = _hermetic_git_env(tmp_path)
    repo = _init_repo(tmp_path, env)

    bare_before = _run_installer(["--root", str(repo)], env)
    assert bare_before.returncode == 1, (
        f"bd#66 AC14: with no flags the installer must behave as `--check` and "
        f"return 1 before installation, got {bare_before.returncode}. "
        f"stderr={bare_before.stderr!r}"
    )
    rc_local, _ = _local_hooks_path(repo, env)
    assert rc_local != 0, (
        "bd#66 AC14: a bare (check-like) invocation must not install anything, "
        "yet core.hooksPath was written."
    )

    installed = _run_installer(["--install", "--root", str(repo)], env)
    assert installed.returncode == 0, (
        f"bd#66 AC14: --install must succeed, got {installed.returncode}. "
        f"stderr={installed.stderr!r}"
    )

    bare_after = _run_installer(["--root", str(repo)], env)
    assert bare_after.returncode == 0, (
        f"bd#66 AC14: with no flags the installer must behave as `--check` and "
        f"return 0 after installation, got {bare_after.returncode}. "
        f"stderr={bare_after.stderr!r}"
    )

    both = _run_installer(["--install", "--check", "--root", str(repo)], env)
    assert both.returncode == 2, (
        f"bd#66 AC14: `--install --check` together is a usage error and must "
        f"return 2, got {both.returncode}. stdout={both.stdout!r} "
        f"stderr={both.stderr!r}"
    )

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    outside = _run_installer(["--install", "--root", str(plain)], env)
    assert outside.returncode == 1, (
        f"bd#66 AC14: `--root` on a non-git directory must return 1, got "
        f"{outside.returncode}. stdout={outside.stdout!r} "
        f"stderr={outside.stderr!r}"
    )
    assert not (plain / ".git").exists(), (
        "bd#66 AC14: the installer must not create a repository under a --root "
        "that is not one."
    )
    local_after = _git(["config", "--get", "--local", "core.hooksPath"], plain, env)
    assert local_after.returncode != 0, (
        f"bd#66 AC14: no LOCAL core.hooksPath may exist under a non-git --root, "
        f"got rc={local_after.returncode} value={local_after.stdout.strip()!r}."
    )
    global_after = _git(["config", "--get", "--global", "core.hooksPath"], tmp_path, env)
    assert global_after.returncode != 0, (
        f"bd#66 AC14: the installer must NOT fall back to the GLOBAL scope when "
        f"--root is not a repository, got {global_after.stdout.strip()!r}."
    )
    global_text = Path(env["GIT_CONFIG_GLOBAL"]).read_text()
    assert "hooksPath" not in global_text, (
        f"bd#66 AC14: the global config file must be untouched, got "
        f"{global_text!r}."
    )


# ─── AC15: the hook is VERSIONED executable, or the check is visibly absent ────


def test_ac15_hook_is_versioned_with_mode_100755():
    """§2.3: `githooks/pre-commit` must be recorded in the index as `100755`.

    This is the fact GREEN actually controls and the only one that survives a
    fresh clone: a 100644 blob is checked out non-executable everywhere, and git
    then skips the hook without a word — enforcement that reads as a passing
    commit, which is the exact defect this lot exists to remove.

    It is a separate AC because it interrogates a different corpus. The
    clean-room lane ships the tree with `git archive` (tracked files only, no
    `.git`), so there is no index to ask. In that corpus this AC SKIPS with the
    reason printed, rather than being hidden inside a condition in the shared
    helper — an unperformed check has to be a visible fact, the same way a zero
    finding has to carry its denominator.
    """
    if not _repo_has_index():
        pytest.skip(
            "bd#66 AC15: no git index in this corpus — the lane ships the tree "
            "via `git archive` (tracked files only, no .git), so the VERSIONED "
            "mode of githooks/pre-commit cannot be read here. The working-tree "
            "exec bit is still asserted by AC1/AC5/AC6c; only the index-mode "
            "half is unavailable, and this line is the record that it did not run."
        )

    listed = _git(
        ["ls-files", "-s", "--", "githooks/pre-commit"], REPO_ROOT, dict(os.environ)
    )
    fields = listed.stdout.split()
    assert fields[:1] == ["100755"], (
        f"bd#66 AC15: githooks/pre-commit must be VERSIONED with mode 100755 "
        f"(`git ls-files -s` said {listed.stdout.strip()!r}). A 100644 blob is "
        f"checked out non-executable in every fresh clone, and git then skips "
        f"the hook without a word."
    )
