"""RED tests for hal#1612-B — the typecheck baseline stops mutating the
tree it measures.

bytedigger port note: AC7, AC8, AC15, AC16 and AC20 pin the upstream D5
leftover check over the run-scoped baseline-stash families; bytedigger never
creates those entries (the pytest-baseline stash went with bd#88, the typecheck
one with this change), so D5 is not carried and those classes are omitted.
AC11 (exactly one worktree-add site) is omitted because bd#88 keeps its own
detached-worktree RED-commit baseline; AC12's real-tree positive control is
omitted because its subject, `_compute_baseline_failed`, no longer exists here.

Spec: SHARED/memory/Decisions/2026-08-13_1612B_typecheck_baseline_no_tree_mutation_spec.md
(re-frozen 2026-08-14 — coordinator ruling widened the lot to the
canonical-provider + lint-rule chokepoint; see the file's front matter
"supersedes" line)

19 ACs (AC1-AC19; AC13 is a PROCESS AC discharged in the PR body by a
recorded mutation run, not by a test in this file). RED amendment after
gate round 1 REJECTION (SHARED/memory/Decisions/2026-08-14_1612B_gate_round1.md):
AC5 re-fixtured onto a DIRTY tree and its event assertion pinned by value
(MAJOR-3/4); AC11 re-expressed over the lint's own (verb, subcommand) AST
parse instead of a source-text regex (adversarial edge 4); AC12 gained a
REAL-TREE POSITIVE CONTROL (MAJOR-1, the blocking finding); AC14-AC18 are
new (MAJOR-2, MAJOR-7a, MAJOR-7b, MAJOR-5, adversarial edge 1). AC1-AC4,
AC6-AC10 are UNCHANGED from the first freeze.
RED amendment after gate round 2 REJECTION
(SHARED/memory/Decisions/2026-08-14_1612B_gate_round2.md), attempt 3 of 3:
AC12 gains (d) the fail-closed edge (real tree, empty under the default
registry) and (e) a synthetic generality control that survives #1652
(MAJOR-1); AC16a's spy now returns rc=128, identical to AC16b's real
non-repo git, so repo-ness alone discriminates (MAJOR-2); AC9 pins D2.1's
worktree/mkdtemp-avoidance saving (MINOR-3); AC17's mkdtemp leftover check
is scoped to the `hal_baseline_tree_` prefix (MINOR-5); AC19 is NEW, pinning
that B9's registry reason string is rewritten off `git stash push -u`
(MINOR-7).
Pre-GREEN expected FAIL: AC2, AC3, AC5, AC7, AC8 (warning-event half),
AC10, AC11, AC12 (all of a/b/c/d/e), AC15, AC16, AC17, AC18, AC19, AC20
(round 4 addition, gate round 3 MAJOR-2 -- D5's ambient-git_cwd skip does
not exist yet).
Pre-GREEN expected PASS (guard-strength / regression pin, not a forcing
function — see per-class docstrings): AC1, AC4, AC6, AC8 (no-raise/no-drop
half), AC9, AC14.
AC2 fails because the leftover is pre-staged under the bare stash name
the TYPECHECK helper itself mints (`_STASH_PREFIX_TYPECHECK`,
phase_5_implement.py:4611); `_preflight_baseline_stash` finds it on the
first call and raises `BaselineStashLeftover`.
AC5 (coordinator ruling 2026-08-14, re-fixtured to a DIRTY tree per gate
MAJOR-3): the house degradation (`None` on `worktree add` rc!=0, per
phase_6_review.py:4936 / phase_8_post_deploy.py:1736) stands; the forcing
element is a NAMED PROVIDER EVENT (`baseline_tree_unavailable`, `returncode`
field, asserted BY VALUE per gate MAJOR-4) recording the rc — on a DIRTY
tree `result is None` is ALSO forcing on its own today, since the spied
`git stash push` returns rc=0 and the helper proceeds to a real mypy run.
AC11 fails because TWO copy-pasted `worktree add --detach` implementations
exist today (phase_6_review.py, phase_8_post_deploy.py) instead of one.
AC12 fails because `lib/mutating_git_lint.py` has no stash-at-baseline
rule at all yet (grepped; only the generic unclassified-site check exists);
its real-tree positive control additionally proves a hollow `return []`
body cannot satisfy it (gate MAJOR-1).
AC14 is expected to PASS today — `_verify_fix_typecheck` already calls its
OWN module-level `_git_write` directly (phase_6_review.py:4940), so a spy
installed as `phase_6_review._git_write` already sees the argv. Its value
is as a REGRESSION GUARD once the canonical provider (D3) lands: if a
future GREEN has the provider import the port or capture it in a default
argument instead of resolving it from the caller's globals at call time,
THIS test — not the migrated helper's own tests — is what reddens
(gate MAJOR-2).
AC15 fails because `_verify_green_typecheck` returns status="ok" at the
`not green_paths` early return (:6781) unconditionally — no leftover check
exists before it at all (gate MAJOR-7a).
AC16 fails on BOTH directions because D5's own `git stash list` read does
not exist yet: a real repo with a broken listing has nothing to make it
terminal, and a non-repository `git_cwd` never gets its (not-yet-existing)
warning event (gate MAJOR-7b).
AC17 fails because today's helper never invokes mypy inside a detached
worktree at all (cwd is always the live `git_cwd`) — the forcing
assertion is that the recorded mypy cwd differs from the live repo, which
is never true today (gate MAJOR-5, the unported exception-path cleanup).
AC18 fails because no path-mapping step exists today — an unmappable path
that exists on disk is passed straight through to mypy and the helper
returns an int, never `None` with a named event (gate adversarial edge 1).

Detail on the pass-today guard-strength pins named above: AC1 (today's
stash mechanism already round-trips the tree, index state, stash stack and
worktree registry byte-for-byte on the happy path — S1 "success path is
clean" per the spec's own measurement; the AC's value is that the NEW
worktree mechanism must not regress this), AC4 (the "newly-added path -> 0"
rule is not touched by the rewrite — the current stash-based helper
already excludes vanished-at-baseline paths the same way), AC6 (the stash
mechanism ALSO hides the GREEN edit from mypy on the happy path — a real
numeric pin, not a forcing function, since S1 already holds today), AC8's
no-raise/no-drop half (the current implementation only reacts to ITS OWN
stash name via `_find_stash_ref`'s exact-message match, so a foreign entry
never raises today either, and nothing in this codebase ever drops a
`p2-*` entry), AC9 (the clean-tree-returns-None rule and the
zero-mypy-invocations side of it both already hold via the "No local
changes to save" stash sentinel), and AC14 (`_verify_fix_typecheck`
already calls its own module-level `_git_write` directly, so the spy
already sees the argv — the AC's value is as a future regression guard).

Module-identity note (OFI 74b135e9): the engine executes the
package-qualified `workflows.phase_5_implement`; this file drives the bare
`phase_5_implement` module object, matching every sibling test in this
directory.  Every monkeypatch/spy below is installed on that SAME bare
object (`phase_5_implement.subprocess`, `phase_5_implement._emit_safe`) or on
the process-global `git_write_port` / `git_port` factories, which resolve
at CALL time from a single `sys.modules` entry regardless of which module
object imported them -- so a patch here reaches the helper's real call site.

Not-yet-existing symbols (`TypecheckBaselineWorktreeUnavailable`) are
reached via `getattr(phase_5_implement, "NAME", None)` INSIDE test bodies
(assert-time RED, not collection-time -- see §1q).  No module-level
`sys.path` manipulation (81F97F3D): conftest.py already wires engine_py
root + workflows/ at import time.

AC1 and AC2 (§1l) anchor a REAL production side effect: real `git init`
repos, real commits, real `git stash push` where a leftover is being
pre-staged, sha256 tree manifests taken before/after.  They do NOT mock
`git_write_port` or any git seam -- only the `mypy` subprocess call is
canned (the sibling pattern in test_green_typecheck_baseline_5C14EF32.py
does the same: real git, canned mypy).

AC3 and AC8 use a local spy on `git_write_port.git_op_capture` (own copy --
per RED instructions, not imported from test_F84D3006_baseline_stash_pop_rc.py).
`git_write_port.reset_default_git_write_factory()` runs in a `finally` at
every install site (§1i).
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

# Both modules import cleanly today (conftest inserts sys.path onto the bare
# module objects).  Not-yet-existing attrs are looked up inside test bodies.
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.lib import git_write_port as git_write_port  # noqa: E402
from bytedigger_engine.lib import git_port  # noqa: E402
from bytedigger_engine.lib.git_port import GitResult  # noqa: E402

_BASELINE_STASH_NAME = "p2-baseline-585e30e3"  # must match phase_5_implement.py:4610
_TYPECHECK_STASH_NAME = "p2-typecheck-baseline-5c14ef32"  # must match phase_5_implement.py:4611


# ─── shared helpers (local copies -- not imported across test files) ─────────


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_local_repo_with_red_commit(dest: Path) -> tuple[Path, str]:
    """Real git repo: baseline commit, then a RED commit; returns (repo, red_sha).
    Local copy of the pattern in test_green_typecheck_baseline_5C14EF32.py /
    test_34AEB235_green_typecheck_gate.py (§1j: realpath for macOS /var/folders).
    """
    repo = Path(os.path.realpath(str(dest)))
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("# baseline\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_stub.py").write_text("def test_placeholder(): pass\n")
    subprocess.run(["git", "add", "tests/test_stub.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "RED commit"], cwd=repo, check=True)
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, red_sha


def _make_local_ctx(git_cwd: str) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd, "build_class": "SIMPLE"},
        question="q",
        session_id="test-1612b",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_local_prev(git_cwd: str, red_commit_sha: str | None = None) -> StepResult:
    data: dict[str, Any] = {
        "git_cwd": git_cwd, "build_class": "SIMPLE", "cycle": 1, "cycle_count": 1,
    }
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    return StepResult(
        status="ok", data=data, duration_ms=0, step_name="verify_green_passing",
    )


def _sha256_manifest(root: Path) -> dict[str, str]:
    """Hash of every tracked-or-not file under *root*, excluding .git/."""
    manifest: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts:
            continue
        if p.is_file():
            manifest[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


def _stash_line(name: str) -> str:
    return "stash@{0}: On main: " + name + "\n"


class _GitOpSpy:
    """Local spy for `git_write_port.git_op_capture` (own copy -- per RED
    instructions #4, not the `_CaptureSpy` in test_F84D3006_baseline_stash_pop_rc.py).
    Records every argv; answers `git stash list` / `git worktree add` with
    configured canned GitResults, everything else with a bare rc=0 success.
    """

    def __init__(
        self,
        *,
        stash_list_stdout: str = "",
        stash_list_rc: int = 0,
        worktree_add_rc: int = 0,
        worktree_add_stderr: str = "",
    ) -> None:
        self.calls: list[list] = []
        self._stash_list_stdout = stash_list_stdout
        self._stash_list_rc = stash_list_rc
        self._worktree_add_rc = worktree_add_rc
        self._worktree_add_stderr = worktree_add_stderr

    def op_capture(self, cmd: list, *, cwd: str, timeout: int = 30) -> GitResult:
        toks = [str(c) for c in cmd]
        self.calls.append(toks)
        if "stash" in toks and "list" in toks:
            if self._stash_list_rc:
                return GitResult(
                    returncode=self._stash_list_rc, stdout="",
                    stderr="fatal: could not read stash list\n", timed_out=False,
                )
            return GitResult(
                returncode=0, stdout=self._stash_list_stdout, stderr="", timed_out=False,
            )
        if "worktree" in toks and "add" in toks:
            return GitResult(
                returncode=self._worktree_add_rc,
                stdout="" if self._worktree_add_rc else "Preparing worktree\n",
                stderr=self._worktree_add_stderr, timed_out=False,
            )
        if "worktree" in toks and "remove" in toks:
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    def op_with_lock_retry(self, cmd: list, *, cwd: str, timeout: int = 30):
        self.calls.append([str(c) for c in cmd])
        return None, "ok"

    def drop_calls(self) -> list[list]:
        return [c for c in self.calls if "drop" in c]


def _install_stash_list_read_seam(monkeypatch, spy: "_GitOpSpy") -> None:
    """Route BOTH read seams a `git stash list` might travel through --
    `git_write_port` (the seam both EXISTING baseline helpers use for their
    preflight reads) and `git_port.git_read` -- so AC7/AC8 do not silently
    pass by guessing which one GREEN picks for the new leg's read. Mirrors
    `_install_git_seams` in test_F84D3006_baseline_stash_pop_rc.py; own local
    copy, not imported.
    """
    git_write_port.set_default_git_write_factory(lambda: spy)
    real_read = git_port.git_read

    def _fake_read(args, *a, **kw):
        toks = [str(x) for x in args]
        if "stash" in toks and "list" in toks:
            spy.calls.append(["git", *toks])
            return spy.op_capture(
                ["git", *toks], cwd=str(kw.get("cwd") or ""), timeout=kw.get("timeout") or 30,
            )
        return real_read(args, *a, **kw)

    monkeypatch.setattr(git_port, "git_read", _fake_read)


def _mypy_passthrough_except(monkeypatch, canned_stdout: str, canned_rc: int = 0):
    """Patch phase_5_implement.subprocess.run (the real, shared `subprocess`
    module -- reaches bounded_run's internal call too) so ONLY `mypy`
    invocations are canned; every other subprocess.run call (git, etc.) is
    real. Returns the list mypy argvs are appended to.
    """
    mypy_calls: list[list] = []
    _real_run = subprocess.run

    def _fake_run(argv, **kwargs):
        is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
        if not is_mypy:
            return _real_run(argv, **kwargs)
        mypy_calls.append(list(argv))
        return subprocess.CompletedProcess(
            args=argv, returncode=canned_rc, stdout=canned_stdout, stderr="",
        )

    monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)
    return mypy_calls


# ─── AC1: real side effect -- one call leaves the tree byte-identical ────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC1RealSideEffectNoTreeMutation:
    """AC1 (§1l) -- real temp git repo: a.py committed, then a GREEN edit
    (a.py gains a line, new untracked+staged b.py). One call to
    `_compute_baseline_typecheck_count` must leave (a) the tree manifest,
    (b) `git status --porcelain`, (c) `git stash list` and (d)
    `git worktree list` all exactly as they were.

    Expected to legitimately PASS pre-GREEN too: this is a pass-today
    non-regression pin (same class as AC4/AC6/AC9), not a forcing function.
    It defends that the tree manifest, index state (`git status
    --porcelain`), stash stack and worktree registry are exactly as before
    after one call. MEASURED: today's mechanism already achieves this on
    the happy path -- push, then a targeted pop, restores everything
    exactly, staged status included. The AC's value is that the NEW
    worktree-based mechanism must not regress this: it stays armed to catch
    a botched rewrite that leaves the tree, index, stash stack or worktree
    registry mutated.
    """

    def test_ac1_no_tree_mutation_after_one_call(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC1 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "a.py").write_text("CONST = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)

        # GREEN edit: tracked a.py gains a line; new untracked+staged b.py.
        (repo / "a.py").write_text("CONST = 1\nOTHER = CONST + 1\n")
        (repo / "b.py").write_text("from a import CONST\nprint(CONST)\n")
        subprocess.run(["git", "add", "b.py"], cwd=repo, check=True)

        manifest_before = _sha256_manifest(repo)
        status_before = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
        ).stdout

        _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

        result = fn([str((repo / "a.py").resolve())], str(repo), "cfg_git_cwd")

        assert isinstance(result, int), (
            f"AC1 FAIL: expected an int baseline count; got {result!r}"
        )

        manifest_after = _sha256_manifest(repo)
        assert manifest_after == manifest_before, (
            f"AC1(a) FAIL: tree manifest changed after one call. "
            f"before={manifest_before!r} after={manifest_after!r}"
        )
        status_after = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
        ).stdout
        assert status_after == status_before, (
            f"AC1(b) FAIL: git status --porcelain changed. "
            f"before={status_before!r} after={status_after!r}"
        )
        stash_list = subprocess.run(
            ["git", "stash", "list"], cwd=repo, capture_output=True, text=True,
        ).stdout.strip()
        assert stash_list == "", (
            f"AC1(c) FAIL: git stash list is non-empty: {stash_list!r} -- "
            "the stash-based mechanism is still in use."
        )
        worktree_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True,
        ).stdout
        worktree_entries = [l for l in worktree_list.splitlines() if l.startswith("worktree ")]
        assert len(worktree_entries) == 1, (
            f"AC1(d) FAIL: expected only the primary worktree registered after "
            f"one call; got {worktree_entries!r}"
        )


# ─── AC2: forced cycle 2 survives a REAL pre-existing leftover ───────────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC2ForcedCycleTwoSurvivesRealLeftover:
    """AC2 (the issue's AC1 -- forced cycle 2). A REAL leftover stash entry
    (§1i: pre-staged deterministically -- simulating a cycle-1 crash between
    push and pop, spec finding S2/S3 -- never a race) sits on the stack
    under the bare stash name the TYPECHECK helper itself would mint (no
    run identity is set; conftest's `_run_identity_isolation` autouse
    fixture clears it, which is why the bare `_STASH_PREFIX_TYPECHECK`
    prefix is what gets minted -- see phase_5_implement.py:4611). Two
    consecutive calls (cycle 2) must each return an int, neither may raise
    BaselineStashLeftover, and the tree manifest after the second call must
    match the manifest taken before the first.

    FAILS today: `_preflight_baseline_stash` finds our own leftover on the
    FIRST call (the pre-staged entry's message equals the stash name the
    helper mints) and raises `BaselineStashLeftover` before either call can
    complete.
    """

    def test_ac2_two_calls_survive_a_real_leftover_entry(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC2 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "a.py").write_text("CONST = 1\n")
        subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)

        # Cycle 1's GREEN edit, then a REAL "crashed between push and pop"
        # leftover: genuinely push, never pop.
        (repo / "a.py").write_text("CONST = 1\nCYCLE_ONE = CONST + 1\n")
        subprocess.run(
            ["git", "stash", "push", "-u", "-m", _TYPECHECK_STASH_NAME],
            cwd=repo, check=True,
        )
        # Cycle 2's own GREEN edit -- independent dirt on top of the clean tree.
        (repo / "a.py").write_text("CONST = 1\nCYCLE_TWO = CONST + 2\n")

        manifest_before = _sha256_manifest(repo)
        stash_before = subprocess.run(
            ["git", "stash", "list"], cwd=repo, capture_output=True, text=True,
        ).stdout

        _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

        leftover_exc = getattr(phase_5_implement, "BaselineStashLeftover", Exception)
        paths = [str((repo / "a.py").resolve())]
        results: list[Any] = []
        for n in (1, 2):
            try:
                results.append(fn(paths, str(repo), "cfg_git_cwd"))
            except leftover_exc as exc:
                pytest.fail(
                    f"AC2 FAIL: call #{n} (cycle 2, same run identity) raised "
                    f"BaselineStashLeftover on a real pre-existing leftover: "
                    f"{exc!r}. Not yet implemented."
                )

        for n, r in enumerate(results, start=1):
            assert isinstance(r, int), (
                f"AC2 FAIL: call #{n} returned {r!r}, expected an int"
            )

        manifest_after = _sha256_manifest(repo)
        assert manifest_after == manifest_before, (
            f"AC2 FAIL: tree manifest after the second call differs from "
            f"before the first. before={manifest_before!r} after={manifest_after!r}"
        )
        stash_after = subprocess.run(
            ["git", "stash", "list"], cwd=repo, capture_output=True, text=True,
        ).stdout
        assert stash_after == stash_before, (
            f"AC2 FAIL: git stash list changed across the two calls -- the "
            f"pre-existing leftover must be left exactly as it was. "
            f"before={stash_before!r} after={stash_after!r}"
        )


# ─── AC3: structural proof -- zero `git stash` argv during the helper ────────


class TestAC3NoStashArgvDuringHelper:
    """AC3 (§1l structural proof) -- spy on `git_write_port.git_op_capture`:
    zero argv whose first two elements are `["git", "stash"]` during the
    call. Proof that no kill can leak an entry: none is ever created.

    FAILS today: the preflight `git stash list` read at
    phase_5_implement.py:5354 alone is recorded.
    """

    def test_ac3_zero_stash_argv(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC3 FAIL: _compute_baseline_typecheck_count missing"

        spy = _GitOpSpy()
        git_write_port.set_default_git_write_factory(lambda: spy)
        try:
            result = fn([str(tmp_path / "nonexistent.py")], str(tmp_path), "cfg_git_cwd")
        finally:
            git_write_port.reset_default_git_write_factory()  # §1i teardown

        stash_argvs = [c for c in spy.calls if c[:2] == ["git", "stash"]]
        assert stash_argvs == [], (
            f"AC3 FAIL: {len(stash_argvs)} `git stash ...` argv recorded during "
            f"_compute_baseline_typecheck_count: {stash_argvs!r}. Not yet "
            "implemented -- the helper must never issue a stash write."
        )
        assert result is None or isinstance(result, int), (
            f"AC3 sanity: helper must return None or int, not raise; got {result!r}"
        )


# ─── AC4: newly-added-only path returns 0 (unchanged rule) ───────────────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC4AllNewPathsReturnZero:
    """AC4 (semantics preserved) -- a resolved path absent at HEAD (staged,
    never committed) is excluded; when it is the ONLY in-scope path the
    helper returns 0, not None. This rule (`:5386-5389`) is UNCHANGED by the
    rewrite.

    Expected to legitimately PASS pre-GREEN too: the current stash-based
    helper already excludes vanished-at-baseline paths via the same
    `Path(p).exists()` filter -- the rule, not the mechanism, is what this
    AC pins, and the mechanism swap does not touch it.
    """

    def test_ac4_newly_added_only_path_returns_zero(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC4 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "placeholder.py").write_text("# baseline\n")
        subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)

        new_file = repo / "brand_new_module.py"
        new_file.write_text("x: int = 'this would be a type error'\n")
        subprocess.run(["git", "add", "brand_new_module.py"], cwd=repo, check=True)

        mypy_calls = _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

        result = fn([str(new_file.resolve())], str(repo), "cfg_git_cwd")

        assert result == 0, (
            f"AC4 FAIL: expected 0 (newly-added path excluded); got {result!r}"
        )
        for call_argv in mypy_calls:
            assert str(new_file.resolve()) not in call_argv, (
                f"AC4 FAIL: newly-added file path was passed to mypy at "
                f"baseline: {call_argv!r}"
            )


# ─── AC5: worktree add failure is terminal, never None ──────────────────────


class TestAC5WorktreeAddFailureIsNoneWithEvent:
    """AC5 (house degradation, conformed -- coordinator ruling 2026-08-14;
    RE-FIXTURED after gate round 1 MAJOR-3/MAJOR-4). `git worktree add`
    rc!=0 must NEVER raise and must NEVER get a new error code: the helper
    returns `None`, exactly as `phase_6_review.py:4936` /
    `phase_8_post_deploy.py:1736` already do.

    DIRTY tree, not clean (gate MAJOR-3): a CLEAN fixture smuggles in an
    implementation ordering the spec never requires -- a GREEN doing AC9's
    clean-tree check FIRST (cheap, no worktree) legitimately returns `None`
    before `worktree add` is ever called, so AC5 would pass for the wrong
    reason and silently pin a "worktree-before-clean-check" ordering D2.1
    explicitly rejects on cost grounds. On a DIRTY tree, `result is None`
    is forcing ON ITS OWN: with the spy installed, today's `git stash push`
    returns `GitResult(returncode=0)`, so the "No local changes to save"
    sentinel never fires and the helper proceeds to a real mypy run and
    returns an int -- the old docstring's claim that "None alone would
    already pass" was FALSE and is deleted (gate MAJOR-3).

    The event assertion is pinned by VALUE (gate MAJOR-4, "Names pinned"
    table): type == 'baseline_tree_unavailable', `payload["returncode"]`
    equal to the injected rc -- never a `repr()` substring match, which the
    pre-existing `p2_typecheck_baseline_error` event could satisfy by
    coincidence (any errno/path fragment containing "128"), and which
    silently required patching `_emit_safe` on `phase_5_implement`
    specifically without ever stating that channel.

    FAILS today on BOTH assertions: nothing in
    `_compute_baseline_typecheck_count` calls `git worktree add` at all, so
    (a) the dirty-tree push succeeds and the helper returns an int, not
    None, and (b) no `baseline_tree_unavailable` event is ever emitted.
    """

    def test_ac5_worktree_add_rc_nonzero_returns_none_with_named_event(
        self, tmp_path, monkeypatch,
    ):
        from helpers.host_tools import skip_without  # noqa: PLC0415
        skip_without("git")
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC5 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "dirty.py").write_text("x: int = 1\n")
        subprocess.run(["git", "add", "dirty.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
        (repo / "dirty.py").write_text("x: int = 1\ny: int = 2\n")  # GREEN edit -- DIRTY tree

        captured: list[dict] = []

        def _capture_emit(event_type, payload, severity="info"):
            captured.append({"type": event_type, "payload": payload, "severity": severity})

        monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture_emit)

        injected_rc = 128
        spy = _GitOpSpy(worktree_add_rc=injected_rc, worktree_add_stderr="fatal: no space left\n")
        git_write_port.set_default_git_write_factory(lambda: spy)
        try:
            result = fn([str((repo / "dirty.py").resolve())], str(repo), "cfg_git_cwd")
        finally:
            git_write_port.reset_default_git_write_factory()  # §1i teardown

        assert result is None, (
            f"AC5 FAIL: expected None on `git worktree add` rc!=0 (house rule, "
            f"never a raise, never a new error code); got {result!r}. On this "
            "DIRTY fixture this is forcing on its own -- with the spy "
            "installed, today's `git stash push` succeeds (rc=0), so the "
            "helper proceeds to a real mypy run and returns an int."
        )
        matching = [
            e for e in captured
            if e["type"] == "baseline_tree_unavailable"
            and e["payload"].get("returncode") == injected_rc
        ]
        assert matching, (
            "AC5 FAIL: expected an event type='baseline_tree_unavailable' "
            f"with payload['returncode']=={injected_rc} (Names pinned table, "
            "asserted by value -- never a repr() substring); captured events="
            f"{captured!r}. Not yet implemented -- no worktree-based provider "
            "exists today, so nothing emits this event."
        )


# ─── AC6: guard strength unchanged -- baseline count is the HEAD count ──────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC6BaselineCountMatchesHeadNotDirtyTree:
    """AC6 (guard strength unchanged, numeric) -- the tracked file carries
    ONE marker-finding at HEAD; the GREEN edit (dirty working tree) adds a
    SECOND. The helper must count the marker content at HEAD (1) -- the same
    number the stash mechanism produced by hiding the GREEN edit -- never
    the live dirty tree's count (2).

    Expected to legitimately PASS pre-GREEN too: the stash mechanism ALSO
    hides the GREEN edit from mypy on this happy path (S1 "success path is
    clean" per the spec's own measurement) -- this is a numeric guard-
    strength PIN, not a forcing function, and stays armed post-GREEN as the
    proof that the worktree design measures the identical count.
    """

    def test_ac6_baseline_count_matches_head_not_dirty_tree(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC6 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "mod.py").write_text("TYPE_ERROR_MARKER = 1\n")
        subprocess.run(["git", "add", "mod.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
        # GREEN edit adds a SECOND marker to the dirty working tree.
        (repo / "mod.py").write_text("TYPE_ERROR_MARKER = 1\nTYPE_ERROR_MARKER = 2\n")

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            file_args = [str(a) for a in argv if str(a).endswith(".py")]
            lines: list[str] = []
            for fp in file_args:
                try:
                    content = Path(fp).read_text()
                except OSError:
                    content = ""
                n = content.count("TYPE_ERROR_MARKER")
                lines += [f"{fp}:{i + 1}: error: marker  [assignment]" for i in range(n)]
            stdout = "\n".join(lines) + ("\n" if lines else "")
            return subprocess.CompletedProcess(
                args=argv, returncode=1 if lines else 0, stdout=stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        result = fn([str((repo / "mod.py").resolve())], str(repo), "cfg_git_cwd")

        assert result == 1, (
            f"AC6 FAIL: expected baseline count=1 (the HEAD content's marker "
            f"count); got {result!r}. A helper measuring the live dirty tree "
            "(2 markers) or the wrong content is not equivalent in guard strength."
        )


# ─── AC7: zero-findings early return is blocked by a leftover ───────────────


# ─── AC8: a foreign run's leftover is a warning, never a drop ───────────────


# ─── AC9: clean tree -> None, without any stash stdout sentinel ─────────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC9CleanTreeReturnsNoneWithoutStashSentinel:
    """AC9 (the re-derived clean-tree rule) -- every in-scope path identical
    to HEAD => helper returns None, obtained WITHOUT the "No local changes to
    save" stash stdout sentinel (there is no stash to ask), and without ever
    invoking mypy. Re-derives the property
    test_green_typecheck_baseline_5C14EF32.py:191 (AC2) defends today.

    Expected to PASS today for the return value (None) but this test also
    pins mypy never being invoked on a clean tree, which the CURRENT
    stash-sentinel-based short circuit already satisfies too -- both facets
    together are the re-derived rule and remain armed post-GREEN.

    Round 2 MINOR-3 (Principle C: codified != enforced) -- D2.1 (spec
    :105-112) rules the clean-tree check runs BEFORE the worktree so a
    clean invocation pays no `1.24 s / 10 653 files` checkout, but nothing
    enforced it: a GREEN that builds and tears down a worktree on every
    clean invocation still passed with only the return-value assertion
    above. This version RECORDS (never fakes -- delegates to the real
    `git_write_port.git_op_capture` and the real `tempfile.mkdtemp`, so
    today's real stash behaviour on a clean tree is undisturbed) every git
    argv and every mkdtemp allocation, and asserts NO `("worktree", "add")`
    argv and NO `hal_baseline_tree_*` allocation occurred.
    """

    def test_ac9_clean_tree_returns_none_no_mypy_call(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC9 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "clean.py").write_text("x: int = 1\n")
        subprocess.run(["git", "add", "clean.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
        # No edit at all -- the working tree is identical to HEAD.

        mypy_calls = _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

        # D2.1 ordering pin (round 2 MINOR-3) -- RECORD, never fake, so
        # today's real `git stash push` sentinel behaviour on this clean
        # tree is unaffected by installing the wrapper.
        recorded_git_argv: list[list] = []
        _real_op_capture = git_write_port.git_op_capture

        def _record_op_capture(cmd, *, cwd, timeout=30):
            recorded_git_argv.append([str(c) for c in cmd])
            return _real_op_capture(cmd, cwd=cwd, timeout=timeout)

        monkeypatch.setattr(git_write_port, "git_op_capture", _record_op_capture)

        import tempfile  # noqa: PLC0415 -- local, only this test wraps it

        mkdtemp_dirs: list[str] = []
        _real_mkdtemp = tempfile.mkdtemp

        def _spy_mkdtemp(*args, **kwargs):
            d = _real_mkdtemp(*args, **kwargs)
            mkdtemp_dirs.append(d)
            return d

        monkeypatch.setattr(tempfile, "mkdtemp", _spy_mkdtemp)

        result = fn([str((repo / "clean.py").resolve())], str(repo), "cfg_git_cwd")

        assert result is None, (
            f"AC9 FAIL: expected None for a tree identical to HEAD; got {result!r}"
        )
        assert mypy_calls == [], (
            f"AC9 FAIL: mypy must not be invoked when every in-scope path is "
            f"identical to HEAD; recorded {len(mypy_calls)} call(s): {mypy_calls!r}"
        )
        worktree_add_argv = [
            c for c in recorded_git_argv
            if len(c) >= 3 and c[0] == "git" and c[1] == "worktree" and c[2] == "add"
        ]
        assert worktree_add_argv == [], (
            "AC9 FAIL (D2.1 ordering pin, round 2 MINOR-3): a clean tree "
            f"must never pay for a worktree checkout; recorded "
            f"('worktree', 'add') argv={worktree_add_argv!r}. A GREEN that "
            "builds and tears down a worktree on every clean invocation "
            "must be caught here, not merely by the return value."
        )
        baseline_tree_allocs = [
            d for d in mkdtemp_dirs
            if os.path.basename(d).startswith("hal_baseline_tree_")
        ]
        assert baseline_tree_allocs == [], (
            "AC9 FAIL (D2.1 ordering pin, round 2 MINOR-3): a clean tree "
            f"must never allocate a `hal_baseline_tree_*` temp dir; "
            f"recorded={baseline_tree_allocs!r} (all mkdtemp allocations "
            f"during the call: {mkdtemp_dirs!r})"
        )


# ─── AC10: no guard silently retired -- machine-checkable half ──────────────


def _string_constants_excluding_docstring(func_node: "ast.FunctionDef | ast.AsyncFunctionDef") -> list[str]:
    """Every `ast.Constant` string value inside *func_node*'s body, EXCLUDING
    its own docstring (the first statement, if it is a bare string
    expression). Comments never reach the AST at all -- only the docstring
    needs an explicit carve-out. AC10 (re-frozen 2026-08-14) targets
    EXECUTABLE code, not memory: a comment or docstring naming the removed
    `stash` mechanism and its issue is explicitly permitted.
    """
    body = list(func_node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring statement
    consts: list[str] = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            consts.append(node.value)
    return consts


class TestAC10NoExecutableStashStringInHelperSource:
    """AC10 (no guard silently retired), re-scoped 2026-08-14 to EXECUTABLE
    code only -- the original "no 'stash' substring anywhere in source" bar
    would forbid an explanatory comment naming the removed mechanism and its
    issue, pushing GREEN into deleting institutional knowledge. Parses the
    helper's OWN source with `ast` and asserts no string constant reaching
    executable code (docstring excluded) contains 'stash'. The twin-inventory
    half (§1a audit table) is discharged by the spec table itself, not by
    this file.

    FAILS today: the current source builds real `git stash push/pop/list`
    argv out of string-literal elements ("stash", "push", "-u", "pop", ...)
    and a `stash_name` local built from a `"p2-typecheck-baseline-..."`
    literal -- all executable string constants, not comments.
    """

    def test_ac10_helper_source_has_no_executable_stash_string(self):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC10 FAIL: _compute_baseline_typecheck_count missing"
        source = textwrap.dedent(inspect.getsource(fn))
        func_node = ast.parse(source).body[0]
        assert isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)), (
            f"AC10 setup FAIL: expected a function def, got {func_node!r}"
        )
        consts = _string_constants_excluding_docstring(func_node)
        stash_consts = [c for c in consts if "stash" in c.lower()]
        assert stash_consts == [], (
            "AC10 FAIL: executable string constant(s) containing 'stash' found "
            f"in _compute_baseline_typecheck_count's body (docstring excluded): "
            f"{stash_consts!r}. The design requires the mechanism to be gone, "
            "not merely bypassed at runtime."
        )

    def test_ac10_docstring_naming_stash_is_permitted(self):
        """Positive control (AC10 re-frozen wording: "comments and the
        docstring are explicitly permitted"). A helper whose docstring names
        the removed mechanism and its issue must NOT trip the scan -- only
        executable string constants are forbidden. Guards against a later
        reader "tightening" the scan back to substring-over-full-source.
        Expected to PASS always: this pins the scanner's own behaviour, not
        production code.
        """
        def _sample_with_stash_docstring():
            """This helper no longer uses git stash (see #1652)."""
            return 1

        source = textwrap.dedent(inspect.getsource(_sample_with_stash_docstring))
        func_node = ast.parse(source).body[0]
        consts = _string_constants_excluding_docstring(func_node)
        stash_consts = [c for c in consts if "stash" in c.lower()]
        assert stash_consts == [], (
            "AC10 sanity FAIL: a docstring naming the removed mechanism must "
            f"be excluded from the executable-string scan; actual flagged="
            f"{stash_consts!r}"
        )


# ─── AC11: exactly ONE worktree-baseline provider in engine_py (§1g) ────────


def _files_with_worktree_add_call(root: Path) -> list[Path]:
    """Every prod `.py` file under *root* containing a git call whose
    (verb, subcommand) pair -- parsed by the SAME AST helpers the lint uses,
    `mutating_git_lint._callee_name` / `_argv_elements` / `_verb_subcommand`
    -- is exactly `("worktree", "add")`.

    NEVER a regex over source text (gate adversarial edge 4): a regex
    matching three adjacent string literals is a blacklist --
    `["worktree", "add"] + (["--detach"] if detach else [])`, `--detach`
    held in a variable, or an f-string all evade it, while still being a
    second worktree-baseline implementation in a different spelling.
    Classifying on the (verb, subcommand) PAIR alone -- exactly what
    `mutating_git_lint` already does for every other mutating-git site --
    closes that: `_argv_elements` follows a `[...] + more` BinOp down its
    LEFT operand (production's own `["git", "add", "--"] + paths` shape),
    so the pair is recovered regardless of how any trailing `--detach` is
    spelled or omitted.
    """
    from bytedigger_engine.lib import mutating_git_lint as mutating_git_lint  # noqa: PLC0415

    hits: list[Path] = []
    for path in mutating_git_lint._iter_prod_py_files(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, ValueError):
            continue
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = mutating_git_lint._callee_name(node)
            if callee not in mutating_git_lint._GIT_CALL_NAMES or not node.args:
                continue
            elements = mutating_git_lint._argv_elements(node.args[0])
            if elements is None:
                continue
            verb, subcommand = mutating_git_lint._verb_subcommand(elements)
            if verb == "worktree" and subcommand == "add":
                found = True
                break
        if found:
            hits.append(path)
    return hits


# ─── AC12: the chokepoint rule is armed in mutating_git_lint ────────────────


class TestAC12BaselineStashRuleIsArmed:
    """AC12 (the chokepoint rule FIRES -- REAL-TREE POSITIVE CONTROL, gate
    MAJOR-1, the blocking finding). Round 1 asserted only that the symbol
    exists, that consumer (1) is absent from its output, and that the
    registry names #1652 -- all THREE satisfied by
    `def find_baseline_stash_violations(root): return []`, an empty corpus
    that proves nothing. This version adds the positive control the gate
    demanded:

    (a) with `exceptions=frozenset()` (registry disabled), the rule MUST
        report `_compute_baseline_failed` -- REAL production code that
        genuinely reaches `git stash push -u` at phase_5_implement.py:5263
        today. A synthetic fixture is FORBIDDEN here (spec AC12): it is
        exactly what would make this guard inert -- a hollow `return []`
        satisfies every other assertion in this test while failing THIS one.
    (b) with the DEFAULT registry, the SAME site must NOT be reported --
        proves `BASELINE_STASH_EXCEPTIONS` is load-bearing, not decorative.
    (c) this lot's own subject, `_compute_baseline_typecheck_count`, is
        absent under EITHER call (it no longer stashes, and it is never the
        declared exception).

    Named stably as `test_ac12_baseline_stash_rule_is_armed_on_real_tree` --
    this is the test AC13's mutation proof (a BODY mutation,
    `find_baseline_stash_violations`'s body replaced with `return []`, per
    the re-frozen spec -- a symbol deletion is too weak a mutant, since it
    reddens only `assert rule_fn is not None`) must be shown to redden;
    AC13 itself is a PROCESS AC discharged in the PR body, not by a test in
    this file.

    FAILS today: no such rule exists on `mutating_git_lint` at all -- grepped
    the module in full; only the generic unclassified-site check is present.
    Even a GREEN that stops at the hollow `return []` shape now fails
    assertion (a): `violations_no_exceptions` is empty, so
    `_compute_baseline_failed` is absent from it.
    """

    def test_ac12d_fail_closed_edge_empty_on_real_tree(self):
        """AC12(d) (round 2 MAJOR-1b, the AC32 analogue -- the fail-closed
        edge). `find_baseline_stash_violations(engine_root)` under the
        DEFAULT registry must be `[]` on the real tree. Without this, a
        fifth baseline site landing next month appears in the rule's
        output and NO test fails -- the front-matter's `enforcement_layer:`
        claim rests on exactly this shape at `test_gh1220_...:2122-2127`.

        Genuinely forcing today for the OFFICIAL GREEN too, not just for a
        hollow one: D4.1's own predicate table (spec :164-172) names exactly
        TWO detectable violators reaching a mutating `stash` pair on the
        real tree today -- `_compute_baseline_failed` and
        `_drop_stash_entry_if_unchanged` -- and requires each to gain a
        registry entry. (Round 3 gate MAJOR-1(a): a third row,
        `_restore_baseline_stash`, is NOT a detectable site -- it passes its
        pop argv as a variable, `pop_argv` at phase_5_implement.py:5185, an
        `ast.Name`, and `_argv_elements` (mutating_git_lint.py:174-188)
        returns `None` for anything but a literal list/tuple, so
        `visit_Call` records nothing for it. Corroboration needing no
        code-reading: were it detectable it would already be
        unregistered-and-reported, and `test_ac32`
        (test_gh1220_ambient_cwd_commit_refusal.py:2113-2127, real engine
        root, fail-closed) would be RED today -- it is green. It therefore
        needs no registry entry and is excluded from this AC's forcing
        claim.) A GREEN that follows AC12(b)/(c) and registers only
        `_compute_baseline_failed` (this test's sibling assertions never
        exercise the other one) still reddens THIS one until BOTH detectable
        violators are declared, which is the point: emptiness on the default
        registry, not merely "the one name we were handed", is the enforced
        property.

        FAILS today: `find_baseline_stash_violations` does not exist yet.
        """
        from bytedigger_engine.lib import mutating_git_lint as mutating_git_lint  # noqa: PLC0415

        rule_fn = getattr(mutating_git_lint, "find_baseline_stash_violations", None)
        assert rule_fn is not None, (
            "AC12(d) FAIL: mutating_git_lint.find_baseline_stash_violations "
            "not yet defined -- the fail-closed edge cannot be evaluated"
        )

        engine_root = Path(__file__).resolve().parents[1]
        violations = rule_fn(engine_root)
        assert violations == [], (
            "AC12(d) FAIL: find_baseline_stash_violations must report ZERO "
            "violations on the real engine tree under the DEFAULT registry "
            f"-- a fifth site must be caught HERE; actual offending "
            f"records={violations!r}"
        )

    def test_ac12e_generality_control_synthetic_unregistered_site(self, tmp_path):
        """AC12(e) (round 2 MAJOR-1c, tightened round 3 MAJOR-1(b) -- the
        AC33 analogue, the ONLY control that survives #1652). A synthetic
        module under `tmp_path` holds a function whose NAME carries NO
        `baseline`/`measure`/`compute` token -- the shape must come from
        the ARGV, which is the entire point of D4.1's name-free predicate,
        never from the identifier -- reaching `["git", "stash", "push",
        "-u"]`; it MUST be reported even though nothing in its name keys a
        rule. A second rogue reaches `["git", "stash", "save", ...]` (round
        3 MINOR-1) so a `push`-only subcommand whitelist also reddens. A
        `["git", "stash", "list"]` read-only decoy in the SAME module MUST
        NOT be reported. When #1652 migrates `_compute_baseline_failed`,
        AC12(a)'s real-tree corpus empties and the cheapest repair is
        deleting the positive control -- this synthetic control is the one
        that keeps the rule from returning to the round-1 inert state.

        Round 3 measured that the previous fixture name,
        `unregistered_measure_something`, contained the token "measure" and
        so let a rule body keyed on name fragments
        (`"baseline"`/`"measure"`/`"compute"`) pass (a)(b)(c)(d)(e) intact
        -- that mutant is dead only once the fixture's name itself gives the
        rule nothing to key on.

        FAILS today: `find_baseline_stash_violations` does not exist yet.
        """
        from bytedigger_engine.lib import mutating_git_lint as mutating_git_lint  # noqa: PLC0415

        rule_fn = getattr(mutating_git_lint, "find_baseline_stash_violations", None)
        assert rule_fn is not None, (
            "AC12(e) FAIL: mutating_git_lint.find_baseline_stash_violations "
            "not yet defined -- the generality control cannot be evaluated"
        )

        synthetic_root = tmp_path / "synthetic_baseline_ac12e"
        synthetic_root.mkdir()
        module_path = synthetic_root / "rogue_baseline_module.py"
        module_path.write_text(
            "def frobnicate_widgets(git_cwd):\n"
            "    import subprocess\n"
            "    subprocess.run(\n"
            "        ['git', 'stash', 'push', '-u', '-m', 'x'], cwd=git_cwd,\n"
            "    )\n"
            "\n"
            "\n"
            "def frobnicate_gadgets(git_cwd):\n"
            "    import subprocess\n"
            "    subprocess.run(\n"
            "        ['git', 'stash', 'save', '-u', '-m', 'y'], cwd=git_cwd,\n"
            "    )\n"
            "\n"
            "\n"
            "def read_only_stash_listing(git_cwd):\n"
            "    import subprocess\n"
            "    subprocess.run(['git', 'stash', 'list'], cwd=git_cwd)\n",
            encoding="utf-8",
        )

        violations = rule_fn(synthetic_root)
        violating_functions = {v.get("function") for v in violations}
        assert "frobnicate_widgets" in violating_functions, (
            "AC12(e) FAIL: an unregistered function whose NAME gives the "
            "rule nothing to key on, reaching "
            "['git','stash','push','-u',...], must be reported; actual "
            f"violations={violations!r}"
        )
        assert "frobnicate_gadgets" in violating_functions, (
            "AC12(e) FAIL (round 3 MINOR-1): a second unregistered function "
            "reaching ['git','stash','save',...] must ALSO be reported -- a "
            f"push-only subcommand whitelist misses it; actual "
            f"violations={violations!r}"
        )
        assert "read_only_stash_listing" not in violating_functions, (
            "AC12(e) FAIL: a ['git','stash','list'] read-only decoy in the "
            f"SAME module must NOT be reported (Amendment 6: classify on "
            f"the (verb, subcommand) pair); actual violations={violations!r}"
        )


# ─── AC14: the caller-supplied port is not hard-wired (gate MAJOR-2) ────────


class TestAC14ProviderPortNotHardWired:
    """AC14 (the port is not hard-wired -- gate MAJOR-2). A spy installed as
    `phase_6_review._git_write` must record a
    `["worktree", "add", "--detach", ...]` argv during `_verify_fix_typecheck`.
    D1 requires the future canonical provider to resolve its git port from
    the CALLER's module globals at call time (`provider(..., git=_git_write)`
    written inside `phase_6_review`) -- never an import inside the provider,
    never a default-argument capture. `test_phase_6_post_fix_typecheck_gate_GH316.py`
    (:463/:530/:780) blinds by `monkeypatch.setattr(phase_6_review, "_git_write", ...)`;
    a provider that reaches `_git_write` any other way bypasses that patch,
    runs a REAL `worktree add` in a non-repo `tmp_path`, degrades to `None`
    (house rule), and that sibling's `status="ok"` expectation still holds --
    green while the seam is severed. This test is the one that must redden
    if that happens.

    Expected to legitimately PASS today: `_verify_fix_typecheck` already
    calls its OWN module-level `_git_write` directly (phase_6_review.py:4940),
    so a spy installed as `phase_6_review._git_write` already sees the argv.
    This is a REGRESSION GUARD, the same class as AC1/AC4/AC6/AC9 in this
    file: it stays armed post-GREEN as the proof that the migrated provider
    (D3) still resolves its port the caller-supplied way.
    """

    def test_ac14_git_write_spy_sees_worktree_add_detach_argv(self, tmp_path, monkeypatch):
        from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # noqa: PLC0415

        captured_argv: list[list] = []

        def _spy_git_write(args, cwd, **kw):
            captured_argv.append(list(args))
            return (0, "", "")

        monkeypatch.setattr(phase_6_review, "_git_write", _spy_git_write, raising=False)
        monkeypatch.setattr(
            phase_6_review, "git_diff_files", lambda *a, **kw: ["src/foo.py"],
        )

        def _fake_bounded_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="Success: no issues found\n", stderr="",
            )

        monkeypatch.setattr(phase_6_review, "bounded_run", _fake_bounded_run)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "foo.py").write_text("x: int = 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()

        ctx = WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={"git_cwd": str(repo), "scratchpad_dir": str(scratchpad)},
            question="AC14 -- 1612B", session_id="test-1612b-ac14", persona="hal",
            framework=None, domain=None,
        )
        prev = _make_local_prev(str(repo))
        prev.data["pre_fix_sha"] = "a" * 40
        prev.data["cycle"] = 2

        _verify_fix_typecheck(ctx, prev)

        worktree_add_calls = [
            c for c in captured_argv
            if len(c) >= 3 and c[0] == "worktree" and c[1] == "add" and "--detach" in c
        ]
        assert worktree_add_calls, (
            "AC14 FAIL: expected a spy installed as phase_6_review._git_write "
            "to record a ['worktree','add','--detach',...] argv during "
            f"_verify_fix_typecheck; recorded argv={captured_argv!r}. If this "
            "reddens post-GREEN, the provider is resolving its git port some "
            "way other than the caller's module globals at call time -- the "
            "exact severing gate MAJOR-2 describes."
        )


# ─── AC15: D5 fires before the empty-green_paths early return (MAJOR-7a) ────


# ─── AC16: unreadable `git stash list`, both directions (gate MAJOR-7b) ─────


# ─── AC17: cleanup survives an exception during the baseline run (MAJOR-5) ──


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC17CleanupSurvivesBaselineException:
    """AC17 (cleanup survives an exception -- gate MAJOR-5). This ports the
    property retired from `test_green_typecheck_baseline_5C14EF32.py:221`
    (AC3), which the spec's first §1a audit wrongly claimed was "discharged
    by AC1(c)(d)" -- AC1 cans mypy as a SUCCESS, so its cleanup assertions
    are vacuous once nothing stashes. This is the dedicated exception leg:
    the baseline mypy run RAISES; afterwards the mechanism must have
    genuinely run inside a DETACHED WORKTREE (not the live tree), and
    `git worktree list --porcelain` must still name only the primary
    worktree.

    FAILS today: today's helper always runs mypy with `cwd=git_cwd` -- the
    live tree itself, via the stash mechanism -- so the forcing assertion
    (the recorded mypy `cwd` differs from the live repo) can never hold.
    The temp-parent-directory assertion is taken directly, not via a proxy:
    `tempfile.mkdtemp` (stdlib, patchable today, before any provider module
    exists) is wrapped to record every directory it hands out, and each
    recorded directory must be gone from disk after the exception -- a
    provider that removes the worktree but leaks its temp PARENT dir would
    pass the `git worktree list` check alone but fail this one.

    Round 2 MINOR-5: the leftover check is scoped to allocations whose
    basename starts with `hal_baseline_tree_` (Names pinned table), so an
    unrelated third-party temp dir legitimately allocated and kept during
    the call cannot redden this AC.
    """

    def test_ac17_cleanup_survives_baseline_mypy_exception(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC17 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "mod.py").write_text("x: int = 1\n")
        subprocess.run(["git", "add", "mod.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
        (repo / "mod.py").write_text("x: int = 1\ny: int = 2\n")  # GREEN edit -- dirty tree

        worktree_before = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True,
        ).stdout

        import tempfile  # noqa: PLC0415 -- local, only this test wraps it

        mkdtemp_dirs: list[str] = []
        _real_mkdtemp = tempfile.mkdtemp

        def _spy_mkdtemp(*args, **kwargs):
            d = _real_mkdtemp(*args, **kwargs)
            mkdtemp_dirs.append(d)
            return d

        monkeypatch.setattr(tempfile, "mkdtemp", _spy_mkdtemp)

        mypy_cwds: list[str] = []
        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            mypy_cwds.append(str(kwargs.get("cwd")))
            raise RuntimeError("AC17 injected baseline mypy failure")

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        result = fn([str((repo / "mod.py").resolve())], str(repo), "cfg_git_cwd")

        assert result is None, (
            f"AC17 sanity: an exception during baseline measurement must "
            f"degrade to None (house rule, never a raise); got {result!r}"
        )
        assert mypy_cwds and mypy_cwds[0] != str(repo), (
            "AC17 FAIL: expected the baseline mypy run to execute inside a "
            f"DETACHED WORKTREE (recorded cwd != the live repo {str(repo)!r}); "
            f"recorded cwd(s)={mypy_cwds!r}. Not yet implemented -- today's "
            "helper runs mypy with cwd=git_cwd itself; no worktree mechanism "
            "exists."
        )
        # Round 2 MINOR-5: `mkdtemp_dirs` is process-global -- any
        # collaborator legitimately allocating and keeping an UNRELATED temp
        # directory during the call would otherwise redden this AC. Scope to
        # the `hal_baseline_tree_` prefix (Names pinned table) so only the
        # PROVIDER's own allocations are checked.
        baseline_tree_dirs = [
            d for d in mkdtemp_dirs
            if os.path.basename(d).startswith("hal_baseline_tree_")
        ]
        assert baseline_tree_dirs, (
            "AC17 FAIL: expected the provider to allocate its temp parent "
            "directory via tempfile.mkdtemp with the `hal_baseline_tree_` "
            f"prefix (Names pinned table); recorded none matching (all "
            f"mkdtemp allocations: {mkdtemp_dirs!r}). Not yet implemented "
            "-- today's helper never creates a worktree at all."
        )
        leftover_dirs = [d for d in baseline_tree_dirs if os.path.isdir(d)]
        assert leftover_dirs == [], (
            "AC17 FAIL: the spec's own clause -- 'the temp parent directory "
            f"is gone' -- after an exception during the baseline mypy run; "
            f"still on disk: {leftover_dirs!r} (all "
            f"`hal_baseline_tree_*` allocations: {baseline_tree_dirs!r}; all "
            f"mkdtemp allocations: {mkdtemp_dirs!r}). A provider that removes "
            "the worktree but leaks its temp PARENT dir passes the "
            "worktree-registry check below but must fail this one."
        )
        worktree_after = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True,
        ).stdout
        entries_after = [l for l in worktree_after.splitlines() if l.startswith("worktree ")]
        assert len(entries_after) == 1, (
            f"AC17 FAIL: after an exception during the baseline mypy run, "
            f"`git worktree list --porcelain` must still name only the "
            f"primary worktree; got {entries_after!r} "
            f"(before={worktree_before!r})"
        )


# ─── AC18: an unmappable path is not a baseline of 0 (adversarial edge 1) ───


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC18UnmappablePathIsNotAZero:
    """AC18 (an unmappable path is not a baseline of 0 -- gate adversarial
    edge 1). D2.2 distinguishes two cases the stash mechanism never had to:
    an in-scope path ABSENT at HEAD because it is newly added is a
    legitimate exclusion (AC4's `0`); an in-scope path that EXISTS on disk
    but cannot be MAPPED into the worktree (outside `git_cwd`, or the macOS
    `/var` -> `/private/var` realpath mismatch) is a BROKEN measurement --
    it must emit `baseline_tree_path_unmappable` (Names pinned table) and
    return `None`, never `0`. Fixture below
    uses a path that EXISTS on disk (unlike AC4's vanished-at-baseline
    path) but lives OUTSIDE `git_cwd`, so the only way to return `0` would
    be to conflate the two classes.

    FAILS today: no path-mapping step exists at all -- the unmappable path
    survives the `Path(p).exists()` filter (it exists) and is passed
    straight through to mypy with its live absolute path, so the helper
    returns an int (today's canned "Success" => 0), not `None`, and no
    event about an unmappable path is ever emitted.
    """

    def test_ac18_path_outside_git_cwd_returns_none_with_event(self, tmp_path, monkeypatch):
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, "AC18 FAIL: _compute_baseline_typecheck_count missing"

        repo = Path(os.path.realpath(str(tmp_path / "repo")))
        _init_git_repo(repo)
        (repo / "in_scope.py").write_text("x: int = 1\n")
        subprocess.run(["git", "add", "in_scope.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)

        outside_dir = Path(os.path.realpath(str(tmp_path / "outside")))
        outside_dir.mkdir(parents=True, exist_ok=True)
        outside_file = outside_dir / "unmappable.py"
        outside_file.write_text("y: int = 2\n")  # EXISTS on disk -- not AC4's vanished case

        captured: list[dict] = []

        def _capture_emit(event_type, payload, severity="info"):
            captured.append({"type": event_type, "payload": payload, "severity": severity})

        monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture_emit)

        _mypy_passthrough_except(monkeypatch, "Success: no issues found\n")

        result = fn([str(outside_file.resolve())], str(repo), "cfg_git_cwd")

        assert result is None, (
            "AC18 FAIL: an in-scope path that cannot be mapped into the "
            f"worktree (outside git_cwd) must return None -- never a "
            f"baseline count, and never AC4's legitimate 0 for a genuinely "
            f"newly-added path; got {result!r}"
        )
        # Names pinned by the RED (spec "Names pinned" table): event type
        # `baseline_tree_path_unmappable` -- asserted BY VALUE, never a
        # loose truthy/`captured`-non-empty match (gate MAJOR-4's class).
        matching = [e for e in captured if e["type"] == "baseline_tree_path_unmappable"]
        assert matching, (
            "AC18 FAIL: expected an event type='baseline_tree_path_unmappable' "
            f"when a path could not be mapped into the worktree; captured "
            f"events={captured!r}. Not yet implemented -- today's helper has "
            "no path-mapping step at all, so an unmappable path silently "
            "reaches mypy unmapped."
        )


# ─── AC19: B9's registry reason string stops lying (round 2 MINOR-7) ────────


class TestAC19GuardedWriteSiteReasonNoLongerMentionsStashPush:
    """AC19 (round 2 MINOR-7). `find_unclassified_sites` only re-verifies
    the `is_ambient_git_cwd(` marker for a guarded function that has AT
    LEAST ONE mutating-git site of its own (`mutating_git_lint.py:341-351`).
    Post-GREEN `_compute_baseline_typecheck_count` has none -- the provider
    does -- so the lint silently stops re-checking B9's ambient guard while
    `test_ac45` (`test_gh1220_...:2130`) still requires the registry entry
    to STAY. Nothing is left unguarded (the behavioural guard survives at
    `test_gh1220_...:1243/1271/1300`), but the enforcement layer for B9
    changes from lint to behaviour, and the reason string
    "phase_5_implement.py B9 -- refuses ambient before `git stash push -u`"
    (`mutating_git_lint.py:79`) becomes FALSE the moment the helper stops
    stashing. It must be rewritten in the same PR to name what the helper
    actually refuses ambient before -- the worktree checkout the provider
    performs, not the retired stash push.

    FAILS today (expected pre-GREEN): the reason string still reads
    "refuses ambient before `git stash push -u`" -- TRUE today, and would
    go silently FALSE the moment GREEN lands without this test catching it.
    """

    def test_ac19_b9_reason_string_no_longer_mentions_stash_push(self):
        from bytedigger_engine.lib import mutating_git_lint as mutating_git_lint  # noqa: PLC0415

        reason = mutating_git_lint.GUARDED_WRITE_SITES.get(
            "_compute_baseline_typecheck_count", "",
        )
        assert reason, (
            "AC19 FAIL: expected a non-empty GUARDED_WRITE_SITES reason "
            "string for '_compute_baseline_typecheck_count' (AC45 requires "
            "the entry to stay)"
        )
        assert "stash push" not in reason, (
            "AC19 FAIL: GUARDED_WRITE_SITES['_compute_baseline_typecheck_count'] "
            "must no longer name `git stash push -u` -- that claim is FALSE "
            f"once the helper is rebuilt on the worktree provider; actual "
            f"reason={reason!r}"
        )
        assert "worktree" in reason.lower(), (
            "AC19 FAIL: expected the rewritten reason string to name what "
            "the helper actually refuses ambient before -- the worktree "
            f"checkout, not the retired stash push; actual reason={reason!r}"
        )


# ─── AC20: D5's ambient-git_cwd skip, by mechanism (round 3 MAJOR-2) ────────


