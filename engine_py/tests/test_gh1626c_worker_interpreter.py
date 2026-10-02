"""GH1626 C — RED tests (round 4): the interpreter is infrastructure, RESOLVED at
use time and provisioned NOWHERE.

Frozen spec: SHARED/memory/Decisions/2026-08-12_gh1626C_worker_interpreter_spec.md
Class: INFRA-REPORTED-AS-PRODUCT.  Chokepoint: ONE canonical resolver
(`lib/interpreter.py::resolve_project_python`) that CLIMBS from the worktree to
the main checkout, replacing the `_venv_pytest` / `_main_checkout_root` pair that
exists TWICE today.

PROVISIONING IS WITHDRAWN ENTIRELY (round 4) — and why
------------------------------------------------------
Rounds 1-3 tried to create `<worktree>/.venv` at worktree-CREATION time, at three
successive sites, each measured worse than the last: `phase_6_review.py:4965`
(runs only `uvx mypy` from PATH), `phase_8_post_deploy.py:1735` (unreachable —
`full_suite_delta_enforce` defaults False at `:1764`), and finally
`runner-v2/deps.ts:59 gitWorktreeAdd()` — which names worktrees `rv2-<issue>`
while batch names them `<repo>/.batch-worktrees/batch-<issue>`, and the five REAL
worktrees (`.ppba-worktrees/{feat,fix}-*`) match NEITHER.  runner-v2 has no state
files and its README names `batch-build.sh` the production driver (#985 open).
Real creators include `coord/lot-launch.sh:233` and a HUMAN typing
`git worktree add`.

That is the finding: **build worktrees have many producers, one of them a
person.**  Any fix living at creation time is bypassable by the next producer
nobody named.  Provisioning is the wrong layer.  The layer that cannot be
bypassed is the one that asks "which Python?", because every test run goes
through it.

Consequences in this file: AC3/AC4 (engine sites) and AC21/AC22/AC23 (the TS
file `SYSTEM/cli/build/__tests__/gh1626c_worktree_venv_provision.test.ts`, which
is deleted) are withdrawn.  NO test here creates or asserts a symlink.  AC10a,
AC9 and AC12 are stated purely as RESOLUTION facts; AC11 is the lot's spine, and
AC26/AC27 add the production shape on the prompt side and the §1ab every-entry-
path check.

AC → test map
  AC1  → test_ac1_venv_resolver_defined_in_exactly_one_module
  AC2  → test_ac2_phase5_and_phase6_resolve_the_same_interpreter
  AC5  → test_ac5_no_venv_anywhere_is_a_documented_degradation
  AC6  → test_ac6_fix_prompt_carries_absolute_interpreter_path
  AC7  → test_ac7_fix_prompt_calls_missing_interpreter_infrastructure
  AC8  → test_ac8_unresolved_interpreter_emits_warning_naming_roots
  AC9  → test_ac9_worktrees_own_real_venv_wins_over_main_checkout
  AC10a→ test_ac10a_venvless_worktree_resolves_to_main_checkout_venv
  AC10b→ test_ac10b_resolver_exercises_parent_venv_and_repo_venv
  AC11 → test_ac11_venvless_worktree_does_not_run_bare_system_python3
  AC12 → test_ac12_broken_venv_symlink_does_not_break_resolution
  AC24 → test_ac24_fix_prompt_names_unresolvable_interpreter_as_infrastructure
  AC26 → test_ac26_fix_prompt_in_venvless_worktree_names_main_checkout_python
  AC27 → test_ac27_both_consumers_resolve_identically_for_venvless_worktree
  AC31 → test_ac31_every_worktree_worker_prompt_carries_the_interpreter_block
  AC32 → test_ac32_interpreter_block_names_the_make_python_override_form
  (AC3/AC4 and AC21/AC22/AC23 withdrawn — provisioning ships nowhere.
   AC30 lives in the bun suite:
   SYSTEM/cli/build/__tests__/run-workflow-python-climb-gh1626c.test.ts.)

§1q (assert-time RED, 81F97F3D): `lib.interpreter` does NOT exist yet.  It is
reached ONLY inside test bodies via importlib, so collection never trips.  No
module-level `sys.path` mutation and no `from conftest import` — the engine_py
root (package parent) and tests/ are already exposed via tests/conftest.py's
import-time singleton.

§1l (real side-effect / no mocked UUT): every fixture is a real on-disk layout
under `tmp_path` (`_make_linked_worktree` writes genuine git worktree metadata,
guarded by a real `git rev-parse --git-common-dir` probe through the production
`lib.git_port` seam).  The UUTs — `resolve_project_python`,
`phase_6_review._resolve_pytest_argv`, `phase_5_implement._runner_for_path`,
`phase_6_review._build_fix_prompt` — run for real and are never patched.  The
only monkeypatch in this file is `git_diff_files` in the prompt fixture, a
collaborator that feeds the changed-file list, not the prompt builder itself.

Fixture honesty: the venv binaries are `#!/bin/sh` / `exit 0` stubs.  They make
the resolvers RESOLVE; no AC here claims they prove real dependency
availability (`pydantic_ai`).  AC11 pins only the link this lot owns: the
resolved runner is the project's interpreter, not bare `python3`.

§1i (singleton resources): nothing contended is shared.  The only process-global
touched is the telemetry run-context, set and cleared inside the two test bodies
that need it (conftest's `_run_identity_isolation` restores the ambient slot
either way).  No timing, no locks, no ports.

Pre-GREEN expectation
  AC1  FAIL — `_venv_pytest` / `_main_checkout_root` are DEFINED in 2 modules
              today (phase_5_implement.py:2527,2542 and
              phase_6_review.py:3847,3857).
  AC2  FAIL — `lib.interpreter` does not exist (asserted, not imported at
              collect time).  The consumer-agreement half holds today and is the
              post-GREEN divergence guard.
  AC5  FAIL — on the `lib.interpreter` half; the "build still proceeds" half is
              a post-GREEN degradation guard that passes today.
  AC6  FAIL — `_build_fix_prompt` names no interpreter at all.
  AC7  FAIL — the prompt contains neither "interpreter" nor "infrastructure".
  AC8  FAIL — `lib.interpreter` does not exist; no `interpreter_unresolved`
              event exists anywhere in the engine today.
  AC9  FAIL — on the canonical-resolver half.  The consumer half (a worktree's
              own real `.venv` wins) passes today: that is deliberate, it is the
              §1x guard that GREEN's dedup must not regress.
  AC10a FAIL — on the canonical-resolver half; the climb itself is live today.
  AC10b FAIL — `lib.interpreter` does not exist, and no `source` vocabulary
              exists to exercise.
  AC11 PASS today (correctness guard) — `_resolve_pytest_argv`'s climb already
              works; this pins that GREEN's move to `lib/interpreter.py` does
              not reintroduce the bare-`python3` fallback that surfaced as
              `ModuleNotFoundError: pydantic_ai`.
  AC12 FAIL — on the canonical-resolver half; the no-raise half passes today.
  AC24 FAIL — the `none` branch is silent today: `_build_fix_prompt` emits no
              interpreter block at all, resolved or not.
  AC26 FAIL — the production shape on the prompt side.  `_build_fix_prompt`
              names no interpreter at all today; and after GREEN, a
              `<git_cwd>/.venv`-only implementation still fails it, because 4 of
              the 5 real worktrees have no `.venv` of their own.
  AC27 PASS today (correctness guard, §1ab) on its consumer-agreement half —
              the two copies of the climb are currently identical, so they
              already agree; the canonical-resolver third opinion is RED
              (`lib.interpreter` absent).  Post-GREEN this is the guard that the
              dedup keeps every entry path on one answer.
  AC31 FAIL — no worker prompt carries an interpreter block today, on ANY
              branch.  Keyed (file, function, ordinal) after gate round 6
              (MAJOR-9): the population is the 4 `_worktree_edit_boundary_block`
              CALL SITES across 3 builders, and BOTH of `_build_red_prompt`'s
              mutually exclusive returns (GH496 delta-retry and cycle-1
              scaffold) are driven for real with a witness.  The enumeration
              half holds today and is the guard that neither a FIFTH
              worktree-worker prompt nor a new branch of an existing one can
              ship without an interpreter block.
  AC32 FAIL — the string "PYTHON=" does not occur in any prompt today.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.workflows import phase_6_review
from bytedigger_engine.workflows import phase_5_implement
from bytedigger_engine.config_provider import get_config
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib.git_port import git_read


# ─── helpers ──────────────────────────────────────────────────────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) triples."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _write_venv(root: Path) -> Path:
    """Create a `<root>/.venv/bin/{python,pytest}` that is a FILE and executable.

    NOTE (fixture honesty): these are `#!/bin/sh\\nexit 0` stubs.  They satisfy
    every probe production performs (`is_file()` + `os.X_OK`) and let the
    resolvers RESOLVE, which is all any AC here asserts.  No AC in this file
    claims — or could prove — that real project dependencies (e.g.
    `pydantic_ai`) are importable from them; AC11 pins only that the resolved
    interpreter is the PROJECT's rather than the bare system `python3`, which is
    the link in the chain this lot owns.
    """
    bindir = root / ".venv" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name in ("python", "pytest"):
        exe = bindir / name
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
    return root / ".venv"


def _make_repo(tmp_path: Path, *, with_venv: bool = True) -> Path:
    """A checkout-shaped dir; when with_venv, a real `.venv/bin/{python,pytest}`."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True, exist_ok=True)
    (repo / "mod.py").write_text("VALUE = 1\n")
    if with_venv:
        _write_venv(repo)
    return repo


def _make_linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """THE PRODUCTION SHAPE: a venv-less linked worktree whose MAIN CHECKOUT owns `.venv`.

    Built as real on-disk git metadata (no `git` command is run by the test),
    so production's own `rev-parse --git-common-dir` climb executes for real:

        main/.git/{HEAD,config,objects/,refs/heads/}   + main/.venv/bin/*
        main/.git/worktrees/wt/{HEAD,commondir,gitdir}
        wt/.git  ->  "gitdir: main/.git/worktrees/wt"   (NO .venv)

    Returns (main_checkout, worktree).
    """
    main = tmp_path / "main"
    gitdir = main / ".git"
    (gitdir / "objects").mkdir(parents=True, exist_ok=True)
    (gitdir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (gitdir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
    )
    (main / "mod.py").write_text("VALUE = 1\n")
    _write_venv(main)

    wt = tmp_path / "wt"
    wt.mkdir(parents=True, exist_ok=True)
    (wt / "mod.py").write_text("VALUE = 1\n")
    admin = gitdir / "worktrees" / "wt"
    admin.mkdir(parents=True, exist_ok=True)
    (admin / "HEAD").write_text("ref: refs/heads/main\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "gitdir").write_text(str(wt / ".git") + "\n")
    (wt / ".git").write_text(f"gitdir: {admin}\n")
    return main, wt


def _assert_layout_is_a_real_worktree(main: Path, wt: Path) -> None:
    """Fixture guard: real `git` must see `wt` as a linked worktree of `main`.

    Probed through the SAME seam production uses (`lib.git_port.git_read`) and
    through no production symbol, so a GREEN refactor cannot invalidate it.  A
    failure here means the environment's git rejected the hand-built metadata —
    an infrastructure fault, distinguishable from an AC failure by this message.
    """
    probe = git_read(["rev-parse", "--git-common-dir"], dir_=str(wt), timeout=5)
    common = os.path.realpath(os.path.join(str(wt), probe.stdout.strip()))
    assert probe.returncode == 0 and common == os.path.realpath(str(main / ".git")), (
        f"FIXTURE FAULT (not an AC failure): git did not accept the hand-built "
        f"linked-worktree layout. expected rc=0 and common-dir="
        f"{os.path.realpath(str(main / '.git'))!r}; seen rc={probe.returncode} "
        f"common={common!r} stderr={probe.stderr!r}."
    )


def _make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="make the worker's interpreter resolvable",
        session_id="test-gh1626c",
        persona="hal",
        framework=None,
        domain=None,
    )


def _import_interpreter():
    """Deferred import of the GREEN deliverable (§1q — assert-time, not collect-time)."""
    try:
        return importlib.import_module("bytedigger_engine.lib.interpreter")
    except ModuleNotFoundError:
        return None


def _require_resolver(ac: str):
    """Fetch the canonical resolver or fail with the AC-specific diagnosis."""
    mod = _import_interpreter()
    assert mod is not None, (
        f"{ac}: `lib.interpreter` does not exist. expected the canonical module "
        f"providing resolve_project_python(git_cwd) -> (path, source); seen "
        f"ModuleNotFoundError. Both phases must become thin callers of it "
        f"(spec Design §1)."
    )
    resolve = getattr(mod, "resolve_project_python", None)
    assert callable(resolve), (
        f"{ac}: expected lib.interpreter.resolve_project_python to be callable; "
        f"seen {resolve!r}."
    )
    return resolve


def _bindir_of(path: str | None) -> str | None:
    return None if not path else os.path.dirname(os.path.realpath(path))


# ─── AC1 (§1g, structural, AST — never a literal grep) ────────────────────────


_ENGINE_ROOT = Path(phase_6_review.__file__).resolve().parent.parent
_SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", "tests",
              "site-packages", ".mypy_cache", ".pytest_cache"}


def _modules_defining(symbol: str) -> list[str]:
    """Modules under engine_py (excluding tests/ and vendored dirs) whose AST
    contains a top-level-or-nested FunctionDef named `symbol`.

    AST-based on purpose: a docstring, comment, import, or test mention can
    never register — only a real DEFINITION does.
    """
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(_ENGINE_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                    hits.append(os.path.relpath(path, _ENGINE_ROOT))
                    break
    return sorted(hits)


def test_ac1_venv_resolver_defined_in_exactly_one_module() -> None:
    """AC1: `_venv_pytest` and `_main_checkout_root` are DEFINED in exactly one module.

    Isolation-forcing: pure AST walk of the tree on disk; no shared state, no
    ordering dependency — fails identically run alone.
    Victim-coupled: counts DEFINITIONS of the two duplicated production symbols;
    no stub or monkeypatch can remove a `def` from a file on disk.
    Not-already-shipped: measured today — each name is defined TWICE
    (workflows/phase_5_implement.py and workflows/phase_6_review.py).
    """
    canonical = os.path.join("lib", "interpreter.py")
    for symbol in ("_venv_pytest", "_main_checkout_root"):
        modules = _modules_defining(symbol)
        assert modules == [canonical], (
            f"AC1 (§1g, tightened): `{symbol}` must be DEFINED in exactly ONE "
            f"module, and that module must be {canonical!r}. expected="
            f"[{canonical!r}], seen={modules}. The duplicated copy is how phase 5 "
            f"and phase 6 came to disagree about which Python is real; GREEN must "
            f"leave a single definition in the canonical resolver and no second copy."
        )


# ─── AC2: one function, one answer ────────────────────────────────────────────


def test_ac2_phase5_and_phase6_resolve_the_same_interpreter(tmp_path: Path) -> None:
    """AC2: `_runner_for_path` (p5) and `_resolve_pytest_argv` (p6) agree with
    each other AND with the canonical `resolve_project_python` answer.

    Isolation-forcing: a fresh tmp checkout with a real `.venv/bin/{python,pytest}`;
    the venv hit short-circuits any git probe, so nothing ambient participates.
    Victim-coupled: asserts the two REAL consumers land in the same
    `<repo>/.venv/bin` the canonical resolver names.  A per-test stub of either
    consumer cannot make `lib.interpreter` exist.
    Not-already-shipped: `lib/interpreter.py` does not exist today ⇒ RED.  The
    consumer-agreement half already holds (the two copies are identical) and is
    the post-GREEN divergence guard.
    """
    repo = _make_repo(tmp_path, with_venv=True)

    p5 = phase_5_implement._runner_for_path("tests/test_x.py", str(repo))
    assert p5 is not None, "AC2: expected a py runner dict for a .py path, seen None."
    p5_interp = p5["argv_prefix"][0]
    p6_interp = phase_6_review._resolve_pytest_argv(str(repo))[0]

    assert p5_interp == p6_interp, (
        f"AC2: phase 5 and phase 6 must resolve the SAME interpreter for the same "
        f"git_cwd={str(repo)!r}. expected equal, seen p5={p5_interp!r} p6={p6_interp!r}."
    )

    resolve = _require_resolver("AC2")
    path, source = resolve(str(repo))
    assert source == "worktree_venv", (
        f"AC2: for a git_cwd that itself owns `.venv`, expected source='worktree_venv'; "
        f"seen source={source!r} (path={path!r}). Allowed values per spec Design §1: "
        f"worktree_venv, parent_venv, repo_venv, none."
    )
    expected_bin = os.path.realpath(str(repo / ".venv" / "bin"))
    assert _bindir_of(path) == expected_bin, (
        f"AC2: canonical resolver must name the interpreter inside {expected_bin!r}; "
        f"seen path={path!r}."
    )
    assert _bindir_of(p5_interp) == expected_bin, (
        f"AC2: the phase consumers must agree with the canonical resolver's answer. "
        f"expected both inside {expected_bin!r}; seen consumer={p5_interp!r}, canonical={path!r}."
    )


# ─── AC5: nothing resolvable — documented degradation, the build proceeds ─────


def test_ac5_no_venv_anywhere_is_a_documented_degradation(tmp_path: Path) -> None:
    """AC5 (retargeted, round 3): with no `.venv` reachable, resolution is a
    DOCUMENTED degradation — the canonical resolver says `none` without raising,
    and both consumers still hand back a usable command so the build proceeds.

    Round-2 shape (a provisioning no-op at phase_8_post_deploy.py:1735) is
    withdrawn with AC4, and round-3's TS replacement (AC22) is withdrawn with
    provisioning itself — nothing anywhere creates a `.venv`, so "no `.venv`
    reachable" is a resolution outcome only.

    Isolation-forcing: a bare tmp dir under pytest's tmp tree — no `.venv`, no
    git repo above it, no shared state.  Fails alone for the same reason.
    Victim-coupled: it calls the canonical resolver and BOTH real consumers; a
    stub of either phase cannot make `lib.interpreter` exist.
    Not-already-shipped: `lib/interpreter.py` does not exist ⇒ RED.  The
    build-proceeds half passes today and is the post-GREEN degradation guard —
    GREEN must not turn an unresolvable interpreter into a hard failure.
    """
    bare = tmp_path / "no_venv_anywhere"
    bare.mkdir(parents=True, exist_ok=True)

    argv = phase_6_review._resolve_pytest_argv(str(bare))
    assert argv and argv[0], (
        f"AC5: the build must proceed — phase 6 must still return a runnable "
        f"pytest argv when nothing resolves; seen argv={argv!r}."
    )
    runner = phase_5_implement._runner_for_path("tests/test_x.py", str(bare))
    assert runner is not None and runner.get("argv_prefix"), (
        f"AC5: the build must proceed — phase 5 must still return a runner for a "
        f".py path when nothing resolves; seen {runner!r}."
    )

    resolve = _require_resolver("AC5")
    path, source = resolve(str(bare))
    assert (path, source) == ("", "none"), (
        f"AC5: with no `.venv` reachable from {str(bare)!r}, the canonical "
        f'resolver must degrade to ("", "none") rather than raise or guess; '
        f"seen {(path, source)!r}."
    )


# ─── AC6 / AC7 / AC24: the fix-worker prompt ──────────────────────────────────


def _build_fix_prompt_for(tmp_path: Path, monkeypatch, git_cwd: Path) -> StepResult:
    """Drive the REAL `phase_6_review._build_fix_prompt`.

    Only `git_diff_files` (the changed-file feed) is stood in for; the prompt
    builder itself — the UUT — is untouched real code.
    """
    scratchpad = tmp_path / "scratch"
    (scratchpad / "integrity").mkdir(parents=True, exist_ok=True)
    (scratchpad / "integrity" / "pre-red-ref.txt").write_text("d" * 40)
    spec_md = scratchpad / "spec.md"
    review_md = scratchpad / "review.md"
    spec_md.write_text("# Spec\n\nAC1: do the thing\n")
    review_md.write_text("# Review\n\n## Aggregated Findings\n\n")
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_md),
            "review_doc_path": str(review_md),
            "verdict": "CHANGES_REQUESTED",
        },
        duration_ms=0,
        step_name="verify_findings",
    )
    ctx = _make_ctx(scratchpad, git_cwd=str(git_cwd), current_worktree_path=str(git_cwd))
    monkeypatch.setattr(phase_6_review, "git_diff_files", lambda *a, **k: [])
    return phase_6_review._build_fix_prompt(ctx, prev)


def _windows_around(text: str, needle: str, radius: int = 500) -> list[str]:
    out: list[str] = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return out
        out.append(text[max(0, i - radius): i + radius])
        start = i + 1


def test_ac6_fix_prompt_carries_absolute_interpreter_path(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: the fix-worker prompt from `_build_fix_prompt()` contains the
    resolved interpreter's ABSOLUTE path.

    Isolation-forcing: fresh tmp checkout owning `.venv/bin/python`; the prompt
    is built from that ctx alone. Fails alone.
    Victim-coupled: asserts the real `_build_fix_prompt` output string contains a
    path that only a real resolution of the real git_cwd can produce.
    Not-already-shipped: measured — `_build_fix_prompt` (phase_6_review.py:
    2124-2262) appends spec/review paths and the boundary block at :2247 and
    names no interpreter at all.
    """
    repo = _make_repo(tmp_path, with_venv=True)
    result = _build_fix_prompt_for(tmp_path, monkeypatch, repo)

    assert result.status == "ok", (
        f"AC6 fixture guard: expected status='ok' from _build_fix_prompt; "
        f"seen {result.status!r} error={getattr(result, 'error', None)!r}."
    )
    prompt = result.data["prompt"]
    expected_abs = str(repo / ".venv" / "bin" / "python")
    assert expected_abs in prompt, (
        f"AC6: expected the fix prompt to name the resolved interpreter's ABSOLUTE "
        f"path {expected_abs!r} (so a worker running `make test-posture` uses "
        f"PYTHON=<abs> instead of a `.venv/bin/python` that is not there); "
        f"seen no such path in a prompt of {len(prompt)} chars."
    )


def test_ac7_fix_prompt_calls_missing_interpreter_infrastructure(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7: the same prompt states that a missing interpreter is an
    INFRASTRUCTURE condition, to be reported separately and never as a coverage
    or product concern.

    Isolation-forcing: same self-contained fixture as AC6; no shared state.
    Victim-coupled: asserts on the real prompt text produced by the real
    `_build_fix_prompt`, in a window that must tie 'interpreter' to
    'infrastructure' and to 'coverage'/'product' — not a bare word count.
    Not-already-shipped: measured — neither 'interpreter' nor 'infrastructure'
    occurs anywhere in phase_6_review.py's prompt parts or in the shared
    out-of-role / boundary blocks today.
    """
    repo = _make_repo(tmp_path, with_venv=True)
    result = _build_fix_prompt_for(tmp_path, monkeypatch, repo)

    assert result.status == "ok", (
        f"AC7 fixture guard: expected status='ok' from _build_fix_prompt; "
        f"seen {result.status!r} error={getattr(result, 'error', None)!r}."
    )
    low = result.data["prompt"].lower()

    assert "infrastructure" in low, (
        "AC7: expected the fix prompt to name a missing interpreter as an "
        "INFRASTRUCTURE condition; seen no occurrence of 'infrastructure'."
    )

    windows = _windows_around(low, "infrastructure")
    ok = [
        w for w in windows
        if "interpreter" in w and ("coverage" in w or "product" in w)
    ]
    assert ok, (
        "AC7: expected ONE block tying the three together — a missing "
        "'interpreter' is 'infrastructure' and must NOT be reported as a "
        "'coverage' or 'product' concern (spec Design §4). expected >=1 such "
        f"block; seen {len(windows)} 'infrastructure' mention(s), none of which "
        "also mentions 'interpreter' together with 'coverage'/'product'."
    )


def test_ac24_fix_prompt_names_unresolvable_interpreter_as_infrastructure(
    tmp_path: Path, monkeypatch
) -> None:
    """AC24 (gate round 2, condition C-3): the `none` branch is NOT silent.

    When no project interpreter resolves for the fix-worker's git_cwd, the
    prompt must SAY SO — state that no project interpreter was found, and that
    this is an infrastructure condition — instead of quietly omitting the block
    AC6/AC7 add on the resolved branch.  Silence on this branch is exactly how a
    missing interpreter reached the synthesizer as a product/coverage claim.

    Isolation-forcing: `git_cwd` is a bare tmp dir with no `.venv` and no git
    repository above it inside pytest's tmp tree, so neither the local probe nor
    the `--git-common-dir` climb can find one.  No shared state, no ordering
    dependency — fails identically run alone.
    Victim-coupled: the assertion is on the real prompt string returned by the
    real `_build_fix_prompt`, on a real unresolvable input.  A stub of the
    resolver cannot add a sentence to that prompt, and passing AC6 (the resolved
    branch) does not pass this one — the two branches are distinct text.
    Not-already-shipped: measured — `_build_fix_prompt` emits no interpreter
    block at all today, on either branch, so the word 'interpreter' never
    appears in its output ⇒ RED.
    """
    bare = tmp_path / "no_venv_anywhere"
    bare.mkdir(parents=True, exist_ok=True)
    assert not (bare / ".venv").exists(), (
        f"AC24 fixture guard: git_cwd must start without `.venv`; seen one at "
        f"{str(bare / '.venv')!r}."
    )

    result = _build_fix_prompt_for(tmp_path, monkeypatch, bare)
    assert result.status == "ok", (
        f"AC24 fixture guard: expected status='ok' from _build_fix_prompt; "
        f"seen {result.status!r} error={getattr(result, 'error', None)!r}."
    )
    low = result.data["prompt"].lower()

    assert "interpreter" in low, (
        f"AC24: with nothing resolvable from git_cwd={str(bare)!r}, the prompt "
        f"must still SPEAK about the interpreter — the `none` branch may not be "
        f"silent (spec round 3, C-3). expected the word 'interpreter'; seen none "
        f"in a prompt of {len(low)} chars."
    )

    negated = re.compile(
        r"\b(no|not|none|never|unresolved|unresolvable|missing|absent|"
        r"could ?not|couldn't|unable)\b"
    )
    windows = _windows_around(low, "interpreter")
    stated_absent = [w for w in windows if negated.search(w)]
    assert stated_absent, (
        f"AC24: the prompt must state that NO project interpreter was found — a "
        f"bare mention is not a statement. expected an 'interpreter' mention "
        f"whose surrounding block negates its availability; seen "
        f"{len(windows)} mention(s), none of them negated."
    )
    assert any("infrastructure" in w for w in stated_absent), (
        f"AC24: the same block must call the missing interpreter an "
        f"INFRASTRUCTURE condition, so the worker reports it as infra and not as "
        f"a coverage or product concern. expected 'infrastructure' inside the "
        f"block that states the absence; seen "
        f"{len(stated_absent)} absence statement(s), none mentioning it."
    )


# ─── AC8: unresolvable is loud ────────────────────────────────────────────────


def test_ac8_unresolved_interpreter_emits_warning_naming_roots(tmp_path: Path) -> None:
    """AC8: `resolve_project_python` returning ("", "none") emits
    `interpreter_unresolved` at severity="warning" naming the roots it searched.

    Isolation-forcing: a bare tmp dir with no `.venv` and no git repo above it in
    the pytest tmp tree; the telemetry context is set inside the test body.
    Victim-coupled: asserts the canonical resolver's own return value AND its own
    telemetry — no phase-level stub participates.
    Not-already-shipped: `lib/interpreter.py` does not exist ⇒ RED at assert time,
    and no `interpreter_unresolved` event type exists anywhere in the engine.

    Observability note: the event-log seam carries (type, payload, run_id) only —
    the emitter's `severity` kwarg is not visible there, so the severity is
    asserted as a payload key.  GREEN must put it in the payload.
    """
    bare = tmp_path / "no_venv_anywhere"
    bare.mkdir(parents=True, exist_ok=True)

    resolve = _require_resolver("AC8")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="gh1626c-ac8",
        step_name="resolve_project_python",
        phase="lib.interpreter",
    )
    try:
        path, source = resolve(str(bare))
    finally:
        telemetry_ctx.clear_current_run()

    assert (path, source) == ("", "none"), (
        f"AC8: with no `.venv` reachable from {str(bare)!r}, expected "
        f'("", "none"); seen {(path, source)!r}.'
    )

    matches = [(etype, payload) for etype, payload, _ in log.events
               if etype == "interpreter_unresolved"]
    assert len(matches) == 1, (
        f"AC8: expected exactly 1 'interpreter_unresolved' event; seen "
        f"{len(matches)}. all event types={[e[0] for e in log.events]!r}. "
        f"Silence is how this reached the synthesizer as a product claim "
        f"(spec Design §5)."
    )
    _, payload = matches[0]
    assert payload.get("severity") == "warning", (
        f"AC8: expected payload['severity'] == 'warning' (the event-log seam does "
        f"not carry the emitter's severity kwarg, so it must be in the payload); "
        f"seen {payload.get('severity')!r}. payload={payload!r}."
    )
    flat = repr(payload)
    assert str(bare) in flat, (
        f"AC8: expected the payload to NAME the roots searched — {str(bare)!r} "
        f"must appear; seen payload={payload!r}."
    )


# ─── AC9 (§1x legacy input): an existing REAL .venv is never overridden ───────


def test_ac9_worktrees_own_real_venv_wins_over_main_checkout(tmp_path: Path) -> None:
    """AC9 (§1x, retargeted round 3): a worktree that owns a REAL `.venv` keeps
    it — resolution never climbs past an existing environment to the main
    checkout's.  The round-2/3 shape (a provisioning helper declining to replace
    a real directory) is withdrawn with provisioning; the resolver half is what
    is live on both phases and is all this lot ships.

    Isolation-forcing: a fresh linked-worktree layout under tmp_path where BOTH
    the worktree and the main checkout own a `.venv`.  Nothing shared, no order
    dependency.
    Victim-coupled: asserts the canonical resolver's `source` vocabulary AND the
    real phase-6 consumer's argv, on a layout whose two candidate venvs are
    distinguishable by path.  A stub cannot produce a real `--git-common-dir`
    climb decision.
    Not-already-shipped: the canonical-resolver half is RED (`lib.interpreter`
    absent).  The consumer half passes today deliberately — it is the guard that
    GREEN's dedup must not invert the precedence.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    _write_venv(wt)

    wt_bin = os.path.realpath(str(wt / ".venv" / "bin"))
    main_bin = os.path.realpath(str(main / ".venv" / "bin"))
    assert wt_bin != main_bin, "AC9 fixture guard: the two venvs must be distinguishable."

    argv = phase_6_review._resolve_pytest_argv(str(wt))
    assert _bindir_of(argv[0]) == wt_bin, (
        f"AC9: the worktree's OWN `.venv` must win — expected the phase-6 runner "
        f"inside {wt_bin!r}; seen argv={argv!r}. Climbing past an existing "
        f"environment would silently run the wrong one."
    )

    resolve = _require_resolver("AC9")
    path, source = resolve(str(wt))
    assert source == "worktree_venv", (
        f"AC9: expected source='worktree_venv' for a worktree owning `.venv`; "
        f"seen source={source!r} path={path!r}."
    )
    assert _bindir_of(path) == wt_bin, (
        f"AC9: expected the canonical resolver to name an interpreter inside "
        f"{wt_bin!r} (never {main_bin!r}); seen path={path!r}."
    )


# ─── AC10a: THE PRODUCTION SHAPE — resolution must CLIMB ──────────────────────


def test_ac10a_venvless_worktree_resolves_to_main_checkout_venv(tmp_path: Path) -> None:
    """AC10a: `git_cwd` is a venv-LESS linked worktree whose MAIN CHECKOUT owns
    `.venv` ⇒ the canonical resolver names the MAIN CHECKOUT's interpreter and
    does not answer `none`.  4 of 5 live build worktrees on this machine are
    exactly this shape (spec, gate round 2 measurement).

    Isolation-forcing: the whole layout is built fresh under tmp_path with real
    git metadata; no ambient repo, no shared state — fails identically alone.
    Victim-coupled: the answer can only come from production's own
    `rev-parse --git-common-dir` climb over a real on-disk worktree; a stub that
    looks only inside git_cwd finds nothing here.
    Not-already-shipped: `lib/interpreter.py` does not exist ⇒ RED.  The climb
    itself is live (phase_6_review.py:3877-3879); this AC pins that moving it to
    the canonical module preserves it, and names the `source`.

    Round 4: this is a pure RESOLUTION assertion.  No symlink is created and none
    is asserted — the worktree is still venv-less AFTER the call, and the test
    checks that too, because provisioning was withdrawn and a GREEN that quietly
    provisions is out of scope.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    assert not os.path.lexists(wt / ".venv"), (
        f"AC10a fixture guard: the worktree must start venv-LESS (that is the "
        f"production shape); seen something at {str(wt / '.venv')!r}."
    )

    resolve = _require_resolver("AC10a")
    path, source = resolve(str(wt))

    expected_bin = os.path.realpath(str(main / ".venv" / "bin"))
    assert source != "none", (
        f"AC10a: expected the resolver to CLIMB from the venv-less worktree "
        f"{str(wt)!r} to its main checkout {str(main)!r}; seen source='none' "
        f"(path={path!r}). A resolver that only looks inside git_cwd answers "
        f"'none' here — that is the defect's home."
    )
    assert _bindir_of(path) == expected_bin, (
        f"AC10a: the resolved interpreter must live in the MAIN CHECKOUT's venv "
        f"{expected_bin!r}; seen path={path!r} source={source!r}."
    )
    assert not os.path.lexists(wt / ".venv"), (
        f"AC10a (round 4): resolution must PROVISION NOTHING — the worktree must "
        f"still be venv-less after the call.  expected nothing at "
        f"{str(wt / '.venv')!r}; seen "
        f"islink={os.path.islink(wt / '.venv')}. Provisioning was withdrawn "
        f"because build worktrees have many producers, one of them human."
    )


# ─── AC10b: both remaining enum members are EXERCISED, not merely declared ────


def test_ac10b_resolver_exercises_parent_venv_and_repo_venv(tmp_path: Path) -> None:
    """AC10b: `resolve_project_python` returns `parent_venv` for one layout and
    `repo_venv` for the other — both members are produced by real inputs.

    Layout A — venv-less linked WORKTREE, main checkout owns `.venv`
               (reached via the `rev-parse --git-common-dir` climb).
    Layout B — a SUBDIRECTORY of a main checkout that owns `.venv` at its root
               (reached by ascending to the repository root).

    Spec ambiguity + resolution: the spec names four sources
    (`worktree_venv`, `parent_venv`, `repo_venv`, `none`) but does not say which
    of `parent_venv` / `repo_venv` belongs to which of these two layouts, and
    both readings are defensible ("parent" = parent checkout vs parent
    directory).  Rather than fabricate a mapping, this AC asserts the pair is a
    BIJECTION onto {parent_venv, repo_venv} — both members exercised, distinct
    per layout — plus the substantive fact that each layout resolves to the
    correct `.venv/bin`.  GREEN may assign either way.

    Isolation-forcing: two fresh tmp layouts, no shared state, no telemetry.
    Victim-coupled: the canonical resolver's own return value; no phase stub
    participates and no monkeypatch can synthesise a real climb.
    Not-already-shipped: `lib/interpreter.py` does not exist ⇒ RED at assert time.
    """
    main_a, wt_a = _make_linked_worktree(tmp_path / "A")
    _assert_layout_is_a_real_worktree(main_a, wt_a)

    main_b, _ = _make_linked_worktree(tmp_path / "B")
    subdir = main_b / "SYSTEM" / "cli" / "build" / "engine_py"
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "mod.py").write_text("VALUE = 1\n")
    probe_b = git_read(["rev-parse", "--git-common-dir"], dir_=str(subdir), timeout=5)
    assert probe_b.returncode == 0, (
        f"FIXTURE FAULT (not an AC failure): git did not accept the subdirectory "
        f"layout at {str(subdir)!r}; rc={probe_b.returncode} stderr={probe_b.stderr!r}."
    )

    resolve = _require_resolver("AC10b")

    path_a, source_a = resolve(str(wt_a))
    path_b, source_b = resolve(str(subdir))

    assert {source_a, source_b} == {"parent_venv", "repo_venv"}, (
        f"AC10b: the two layouts must EXERCISE both remaining enum members. "
        f"expected {{'parent_venv', 'repo_venv'}}; seen "
        f"{{worktree-climb: {source_a!r}, subdir-ascent: {source_b!r}}}. "
        f"Declaring a member in a docstring or a Literal is not exercising it."
    )
    bin_a = os.path.realpath(str(main_a / ".venv" / "bin"))
    assert _bindir_of(path_a) == bin_a, (
        f"AC10b (layout A, worktree climb): expected an interpreter inside "
        f"{bin_a!r}; seen path={path_a!r} source={source_a!r}."
    )
    bin_b = os.path.realpath(str(main_b / ".venv" / "bin"))
    assert _bindir_of(path_b) == bin_b, (
        f"AC10b (layout B, subdirectory ascent): expected an interpreter inside "
        f"{bin_b!r}; seen path={path_b!r} source={source_b!r}."
    )


# ─── AC11: the chain to `ModuleNotFoundError: pydantic_ai` ────────────────────


def test_ac11_venvless_worktree_does_not_run_bare_system_python3(tmp_path: Path) -> None:
    """AC11 (correctness guard — expected GREEN today): for the production shape
    (venv-less worktree, main checkout with `.venv`),
    `_resolve_pytest_argv(<worktree>)[0]` is NOT the bare system `python3`.

    Why this is the reported bug's chain: with nothing reachable, BOTH resolvers
    fall back to `["python3", "-m", "pytest", ...]`
    (phase_6_review.py:3882, phase_5_implement.py:2584) — a Python without the
    project's dependencies, which is exactly what surfaced as
    `ModuleNotFoundError: pydantic_ai` and reached the synthesizer as a product
    claim.  This AC is stated explicitly because GREEN MOVES this logic into
    `lib/interpreter.py`; the move must not reintroduce the fallback.

    Scope note (fixture honesty): the fixture venv holds `#!/bin/sh` stubs, so
    this AC proves the RESOLUTION lands on the project's interpreter; it does
    not, and cannot, prove that any real package imports.

    Isolation-forcing: fresh real-git layout under tmp_path, no ambient repo.
    Victim-coupled: calls the REAL phase-6 resolver on a real venv-less worktree.
    Not-already-shipped: this one IS already shipped (BF7890C8 / 9F3A7C21's
    climb) and is declared here as a post-GREEN regression guard, not as a
    forcing RED — AC1/AC10a carry the forcing weight for the resolver.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)

    argv = phase_6_review._resolve_pytest_argv(str(wt))
    assert argv[0] != "python3", (
        f"AC11: the pytest runner for a venv-less worktree {str(wt)!r} must NOT "
        f"be the bare system interpreter. expected argv[0] != 'python3'; seen "
        f"argv={argv!r}. Bare python3 lacks the project's dependencies — the "
        f"reported `ModuleNotFoundError: pydantic_ai`."
    )
    expected_bin = os.path.realpath(str(main / ".venv" / "bin"))
    assert _bindir_of(argv[0]) == expected_bin, (
        f"AC11: the runner must be the project's interpreter, i.e. inside "
        f"{expected_bin!r} (reached by climbing to the main checkout); seen "
        f"argv[0]={argv[0]!r}."
    )


# ─── AC12 (§1x legacy input): a BROKEN .venv symlink must not poison resolution ─


def test_ac12_broken_venv_symlink_does_not_break_resolution(tmp_path: Path) -> None:
    """AC12 (§1x, retargeted round 3): a DANGLING `<worktree>/.venv` symlink is
    not an environment.  Resolution must step over it — no exception, no
    nonexistent interpreter returned, and the main checkout's venv is used
    instead.

    KEPT in round 4 (retargeted, not deleted).  Its provisioning half — "the
    dangling link is REPLACED" — dies with provisioning, but the input survives
    the withdrawal: a dangling `<wt>/.venv` still exists on disk in the wild
    (batch's `ln -s` outlives a deleted repo venv, and humans leave stale links),
    and it can still MISLEAD the resolver into either raising or returning a
    path that does not exist.  That is a live resolution hazard, so the AC is
    retargeted rather than dropped.

    §1i pre-stage: the contested path is created before the UUT is called, in
    this test's own tmp tree.  Nothing races.

    Isolation-forcing: fresh layout per run under tmp_path; no shared state.
    Victim-coupled: the canonical resolver AND the real phase-6 consumer are
    driven over a real dangling link on disk; no stub can reproduce the
    `is_file()`-on-a-broken-link decision.
    Not-already-shipped: the canonical-resolver half is RED (`lib.interpreter`
    absent).  The no-raise/step-over half passes today and is the post-GREEN
    guard that the dedup keeps probing with `is_file()` (which follows the link)
    rather than `lexists()`.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    dangling = tmp_path / "this-target-does-not-exist"
    os.symlink(str(dangling), str(wt / ".venv"))
    assert os.path.islink(wt / ".venv") and not os.path.exists(wt / ".venv"), (
        f"AC12 fixture guard: `<worktree>/.venv` must be a BROKEN symlink; seen "
        f"islink={os.path.islink(wt / '.venv')} exists={os.path.exists(wt / '.venv')}."
    )

    expected_bin = os.path.realpath(str(main / ".venv" / "bin"))
    argv = phase_6_review._resolve_pytest_argv(str(wt))
    assert _bindir_of(argv[0]) == expected_bin, (
        f"AC12: a dangling `.venv` must not stop the climb — expected the "
        f"phase-6 runner inside {expected_bin!r}; seen argv={argv!r}."
    )

    resolve = _require_resolver("AC12")
    path, source = resolve(str(wt))
    assert source != "none", (
        f"AC12: a dangling `<worktree>/.venv` is not an environment; expected the "
        f"resolver to step over it and climb, seen source='none' (path={path!r})."
    )
    assert _bindir_of(path) == expected_bin, (
        f"AC12: expected the resolved interpreter inside {expected_bin!r}; seen "
        f"path={path!r} source={source!r}."
    )
    assert path and os.path.exists(path), (
        f"AC12: the resolver must never return a path that does not exist "
        f"(that is what following a dangling link produces); seen path={path!r}."
    )


# ─── AC26: the production shape on the PROMPT side ────────────────────────────


def test_ac26_fix_prompt_in_venvless_worktree_names_main_checkout_python(
    tmp_path: Path, monkeypatch
) -> None:
    """AC26 (gate round 3, MAJOR-3): `_build_fix_prompt` for a fix worker whose
    `git_cwd` is a venv-LESS worktree, but whose MAIN CHECKOUT owns `.venv`, must
    name THAT interpreter's absolute path.

    This is the production shape and the reason AC6 alone is not enough: AC6
    feeds a git_cwd that itself owns `.venv`, so an implementation that only
    probes `<git_cwd>/.venv` passes AC6 and then prints "no interpreter found" on
    4 of the 5 real build worktrees measured on this machine.  Only a prompt
    built on a resolver that CLIMBS passes both.

    Isolation-forcing: the whole layout (main checkout + linked worktree + venv)
    is built fresh under this test's `tmp_path` with real git metadata, and the
    prompt is built from a ctx pointing at it.  No ambient repo, no shared
    state, no ordering dependency — it fails identically when run alone, for the
    forcing reason (the prompt names no climbed interpreter).
    Victim-coupled: the assertion is on the real string returned by the real
    `phase_6_review._build_fix_prompt`, and the expected path can only be
    produced by production's own `rev-parse --git-common-dir` climb over a real
    on-disk worktree.  A per-test stub of a resolver cannot put a sentence into
    that prompt, and a `<git_cwd>/.venv`-only GREEN cannot produce this path.
    Not-already-shipped: measured — `_build_fix_prompt` (phase_6_review.py:
    2124-2262) appends spec/review paths and the boundary block at :2247 and
    names no interpreter on any branch ⇒ RED today.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    assert not os.path.lexists(wt / ".venv"), (
        f"AC26 fixture guard: git_cwd must be a venv-LESS worktree (the "
        f"production shape); seen something at {str(wt / '.venv')!r}."
    )

    result = _build_fix_prompt_for(tmp_path, monkeypatch, wt)
    assert result.status == "ok", (
        f"AC26 fixture guard: expected status='ok' from _build_fix_prompt; "
        f"seen {result.status!r} error={getattr(result, 'error', None)!r}."
    )
    prompt = result.data["prompt"]

    expected_abs = os.path.realpath(str(main / ".venv" / "bin" / "python"))
    candidates = {expected_abs, str(main / ".venv" / "bin" / "python")}
    assert any(c in prompt for c in candidates), (
        f"AC26: expected the fix prompt to name the MAIN CHECKOUT's interpreter "
        f"{expected_abs!r}, reached by climbing from the venv-less worktree "
        f"{str(wt)!r}. seen no such path in a prompt of {len(prompt)} chars. An "
        f"implementation that probes only `<git_cwd>/.venv` prints 'no "
        f"interpreter found' here — and on 4 of the 5 real build worktrees."
    )

    low = prompt.lower()
    assert "interpreter" in low, (
        f"AC26: the prompt must SPEAK about the interpreter it names, not merely "
        f"embed a path; seen no occurrence of 'interpreter' in {len(low)} chars."
    )
    assert not os.path.lexists(wt / ".venv"), (
        f"AC26 (round 4): building the prompt must PROVISION NOTHING — the "
        f"worktree must still be venv-less afterwards; seen something at "
        f"{str(wt / '.venv')!r}."
    )


# ─── AC27 (§1ab, every entry path): one answer, whichever phase asks ──────────


def test_ac27_both_consumers_resolve_identically_for_venvless_worktree(
    tmp_path: Path,
) -> None:
    """AC27 (§1ab): BOTH consumers — `phase_5_implement._runner_for_path` and
    `phase_6_review._resolve_pytest_argv` — resolve the SAME interpreter for the
    same venv-less worktree, and it is the one the canonical resolver names.

    AC2 already pins agreement for a git_cwd that owns `.venv` — the easy shape,
    where no climb happens.  This AC pins the shape where the climb IS the
    answer, which is the entry path the lot exists for: every phase that asks
    "which Python?" for a worktree-shaped cwd must get one answer.

    Isolation-forcing: a fresh real-git layout under tmp_path; both consumers are
    called on it in this test body.  Nothing ambient, no ordering dependency —
    the canonical-resolver half fails identically alone.
    Victim-coupled: calls both REAL consumers plus the canonical resolver on a
    real venv-less worktree; a stub of one phase cannot make `lib.interpreter`
    exist, and cannot make the other phase agree with it.
    Not-already-shipped: the consumer-agreement half PASSES today — the two
    copies of the climb are byte-identical, which is precisely why the §1g
    violation has stayed invisible.  It is declared here as the post-GREEN
    guard that the dedup keeps every entry path on one answer.  The
    canonical-resolver half is RED (`lib.interpreter` absent).
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    assert not os.path.lexists(wt / ".venv"), (
        "AC27 fixture guard: the worktree must start venv-LESS."
    )

    p5 = phase_5_implement._runner_for_path("tests/test_x.py", str(wt))
    assert p5 is not None and p5.get("argv_prefix"), (
        f"AC27: expected a py runner dict for a .py path in {str(wt)!r}; seen {p5!r}."
    )
    p5_interp = p5["argv_prefix"][0]
    p6_interp = phase_6_review._resolve_pytest_argv(str(wt))[0]

    assert _bindir_of(p5_interp) == _bindir_of(p6_interp), (
        f"AC27 (§1ab): every entry path must get ONE answer for the same "
        f"venv-less worktree {str(wt)!r}. expected phase 5 and phase 6 to resolve "
        f"the same interpreter directory; seen p5={p5_interp!r} p6={p6_interp!r}."
    )

    expected_bin = os.path.realpath(str(main / ".venv" / "bin"))
    assert _bindir_of(p5_interp) == expected_bin, (
        f"AC27: that one answer must be the MAIN CHECKOUT's interpreter "
        f"{expected_bin!r}, reached by the climb; seen p5={p5_interp!r}."
    )

    resolve = _require_resolver("AC27")
    path, source = resolve(str(wt))
    assert source != "none", (
        f"AC27: the canonical resolver must be the source of that one answer; "
        f"seen source='none' path={path!r} while both consumers resolved "
        f"{p5_interp!r}."
    )
    assert _bindir_of(path) == expected_bin, (
        f"AC27: expected the canonical resolver to agree with both consumers on "
        f"{expected_bin!r}; seen path={path!r} source={source!r}."
    )


# ─── AC31 (gate round 4, MAJOR-8): EVERY worktree-worker prompt, not one ──────
#
# Rounds 1-4 pinned `_build_fix_prompt` alone (AC6/AC7/AC24/AC26).  The gate's
# MAJOR-8 is that §1ab was claimed on one prompt of several.  This block
# enumerates the population instead of naming it.
#
# ENUMERATION CRITERION (and why it is honest rather than a hardcoded list):
# a prompt builder hands its worker a BUILD WORKTREE to operate in exactly when
# it appends `_worktree_edit_boundary_block(...)`
# (`workflows/phase_workflows_common.py:362`) — that block is what tells the
# worker which checkout it owns.  A worker that owns a checkout is a worker that
# can invoke that project's test command; a reviewer/auditor prompt that never
# hands over a worktree (`_build_validation_prompt`, `_build_satisfaction_prompt`,
# `_build_decorr_prompt`, `_build_fix_integrity_prompt`) cannot.  So the
# population is DISCOVERED from the AST of `workflows/*.py`, not typed out, and a
# FIFTH builder that gains the boundary block — or an existing one that loses its
# interpreter block — fails this test without anyone remembering to edit a list.
#
# Measured on this base: 3 builder functions across 4 boundary-block call sites
# (`_build_red_prompt` carries TWO, and they are MUTUALLY EXCLUSIVE — the GH496
# delta-retry early return at phase_5_implement.py:1293-1318, gated by
# `HAL_IMPL_DELTA_RETRY` (default 1, docs/FLAGS.md:47) and taken only for
# cycle>=2 WITH findings, and the cycle-1 scaffold at :1425).
#
# BRANCH KEYING (gate round 6, MAJOR-9) — and why (file, function) was too coarse:
# keying the population on (file, function) let ONE driver stand for BOTH of
# `_build_red_prompt`'s returns.  The old `_drive_red_prompt` passed `_prev=None`
# ⇒ cycle 1 ⇒ only the scaffold branch ever ran, so a GREEN could ship the
# interpreter block on the scaffold and leave the delta-retry prompt without one
# and this test still passed.  The population is therefore keyed on
# (file, function, ORDINAL) where ordinal is the 0-based index of the
# boundary-block call inside that function in SOURCE order — one key per call
# site, four keys today.  Each driver additionally carries a BRANCH WITNESS read
# off the real StepResult (`data["delta_retry"]`), so the key is not merely a
# counter: the test proves which return actually executed.
#
# The hardcoded set below is the ASSERTED expectation, not the source of truth:
# the test fails if discovery disagrees with it in either direction, and fails if
# any DISCOVERED call site has no driver — a branch that exists but is never
# driven is a failure, not a silent pass.
#
# Honest limitation, stated rather than hidden: a future prompt builder that
# hands over a worktree WITHOUT the boundary block would escape discovery.  The
# criterion is production's own marker for "this worker owns a checkout"; there
# is no stronger one on this base.


_WORKTREE_BOUNDARY_HELPER = "_worktree_edit_boundary_block"

# (file, function, ordinal-of-boundary-block-call-in-source-order)
_EXPECTED_WORKER_PROMPT_BUILDERS = {
    ("phase_5_implement.py", "_build_red_prompt", 0),   # GH496 delta-retry return
    ("phase_5_implement.py", "_build_red_prompt", 1),   # cycle-1 scaffold return
    ("phase_5_implement.py", "_build_green_prompt", 0),
    ("phase_6_review.py", "_build_fix_prompt", 0),
}


def _discover_worktree_worker_prompt_builders() -> set[tuple[str, str, int]]:
    """(filename, function, ordinal) for every CALL SITE of
    `_worktree_edit_boundary_block` inside a `workflows/*.py` function — i.e.
    every distinct prompt-assembly path handed to a worker that owns a build
    worktree.  AST-based: a docstring, comment, import or test mention can never
    register, only a real call.

    `ordinal` is the 0-based rank of the call site among that function's own
    boundary-block calls, ordered by source position.  Two mutually exclusive
    returns in one function are therefore two DISTINCT keys, which is what makes
    an undriven branch visible.
    """
    hits: set[tuple[str, str, int]] = set()
    wf_dir = _ENGINE_ROOT / "workflows"
    for path in sorted(wf_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name == _WORKTREE_BOUNDARY_HELPER:
                continue  # the helper's own definition is not a prompt builder
            sites: list[tuple[int, int]] = []
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                fn = inner.func
                name = (
                    fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute)
                    else None
                )
                if name == _WORKTREE_BOUNDARY_HELPER:
                    sites.append((inner.lineno, inner.col_offset))
            for ordinal, _pos in enumerate(sorted(sites)):
                hits.add((path.name, node.name, ordinal))
    return hits


def _drive_red_prompt_delta_retry(
    tmp_path: Path, monkeypatch, git_cwd: Path
) -> StepResult:
    """Drive the REAL `_build_red_prompt` down its GH496 DELTA-RETRY early return.

    The gate `HAL_IMPL_DELTA_RETRY` is put into its DEFAULT state deterministically
    (§1i pre-stage: the var is REMOVED rather than raced against whatever the
    ambient shell holds; `config_provider._DefaultConfigProvider.gate_enabled`
    reads it live and treats unset as enabled), and `_prev` is the legacy engine
    retry-hook shape `{"cycle": 2, "findings": ...}`, so the early return at
    phase_5_implement.py:1293-1318 is the path taken.  The caller checks the
    returned `data["delta_retry"]` witness to prove it.
    """
    monkeypatch.delenv("HAL_IMPL_DELTA_RETRY", raising=False)
    assert get_config().gate_enabled("HAL_IMPL_DELTA_RETRY"), (
        "AC31 fixture guard (not an AC failure): with HAL_IMPL_DELTA_RETRY unset, "
        "production's own gate predicate must read ENABLED (docs/FLAGS.md:47 — "
        "default 1). It did not, so the delta-retry branch is unreachable here."
    )
    scratchpad = tmp_path / "scratch-red-delta"
    (scratchpad / "specs").mkdir(parents=True, exist_ok=True)
    (scratchpad / "specs" / "build-spec.md").write_text(
        "# Spec\n\nAC1: implement the widget helper in mod.py\n", encoding="utf-8"
    )
    ctx = _make_ctx(scratchpad, git_cwd=str(git_cwd), current_worktree_path=str(git_cwd))
    return phase_5_implement._build_red_prompt(
        ctx, {"cycle": 2, "findings": "AC1 has no failing assertion; add one."}
    )


def _drive_red_prompt_scaffold(tmp_path: Path, monkeypatch, git_cwd: Path) -> StepResult:
    """Drive the REAL `phase_5_implement._build_red_prompt` (cycle-1 scaffold)."""
    scratchpad = tmp_path / "scratch-red"
    (scratchpad / "specs").mkdir(parents=True, exist_ok=True)
    (scratchpad / "specs" / "build-spec.md").write_text(
        "# Spec\n\nAC1: implement the widget helper in mod.py\n", encoding="utf-8"
    )
    ctx = _make_ctx(scratchpad, git_cwd=str(git_cwd), current_worktree_path=str(git_cwd))
    return phase_5_implement._build_red_prompt(ctx, None)


def _drive_green_prompt(tmp_path: Path, monkeypatch, git_cwd: Path) -> StepResult:
    """Drive the REAL `phase_5_implement._build_green_prompt`."""
    scratchpad = tmp_path / "scratch-green"
    (scratchpad / "specs").mkdir(parents=True, exist_ok=True)
    (scratchpad / "tests").mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    spec = scratchpad / "specs" / "build-spec.md"
    spec.write_text("# Spec\n\nAC1: implement the widget helper in mod.py\n", encoding="utf-8")
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec),
            "red_log_path": str(scratchpad / "tests" / "build-red-output.log"),
            "validation_doc_path": str(scratchpad / "reviews" / "build-opus-validation.md"),
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )
    ctx = _make_ctx(scratchpad, git_cwd=str(git_cwd), current_worktree_path=str(git_cwd))
    return phase_5_implement._build_green_prompt(ctx, prev)


def _drive_fix_prompt(tmp_path: Path, monkeypatch, git_cwd: Path) -> StepResult:
    return _build_fix_prompt_for(tmp_path, monkeypatch, git_cwd)


# key -> (driver, branch-witness, human name of the branch).
# The witness is read off the REAL StepResult and proves WHICH mutually exclusive
# return executed, so an ordinal is never merely a counter.
_WORKER_PROMPT_DRIVERS = {
    ("phase_5_implement.py", "_build_red_prompt", 0): (
        _drive_red_prompt_delta_retry,
        lambda d: d.get("delta_retry") is True and int(d.get("cycle", 0)) >= 2,
        "GH496 delta-retry early return (:1293-1318)",
    ),
    ("phase_5_implement.py", "_build_red_prompt", 1): (
        _drive_red_prompt_scaffold,
        lambda d: not d.get("delta_retry"),
        "cycle-1 scaffold return (:1425)",
    ),
    ("phase_5_implement.py", "_build_green_prompt", 0): (
        _drive_green_prompt,
        lambda d: True,
        "single return",
    ),
    ("phase_6_review.py", "_build_fix_prompt", 0): (
        _drive_fix_prompt,
        lambda d: True,
        "single return",
    ),
}


def test_ac31_every_worktree_worker_prompt_carries_the_interpreter_block(
    tmp_path: Path, monkeypatch
) -> None:
    """AC31 (MAJOR-8): every worker prompt builder that hands over a build
    worktree — hence can run that project's tests — names the resolved
    interpreter, not just `_build_fix_prompt`.

    The population is DISCOVERED from the AST as CALL SITES, keyed
    (file, function, ordinal) — see the block comment above.  A fifth builder
    that appends the worktree-boundary block, or a NEW mutually exclusive branch
    inside an existing builder, fails this test at the enumeration assert because
    it has no driver; and a builder that ships the interpreter block on only ONE
    of its two branches fails at the prompt assert, because BOTH of
    `_build_red_prompt`'s returns are driven for real — the GH496 delta-retry
    early return (with `HAL_IMPL_DELTA_RETRY` in its default-enabled state and
    `_prev={"cycle": 2, "findings": ...}`) and the cycle-1 scaffold — and each
    driver's branch is confirmed by a witness read off the real StepResult
    (`data["delta_retry"]`), not assumed.

    Every prompt is built for a venv-LESS linked worktree whose MAIN CHECKOUT
    owns `.venv` — the production shape.  A `<git_cwd>/.venv`-only GREEN cannot
    satisfy this for any of the four call sites.

    Isolation-forcing: the git layout and all three scratchpads are built fresh
    under this test's `tmp_path`; no ambient repo, no shared state, no ordering
    dependency.  It fails identically run alone, for the forcing reason (no
    prompt names an interpreter today).
    Victim-coupled: all four call sites are driven through the real production
    prompt builders; the expected path can only be produced by a resolver that
    climbs over genuine worktree metadata.  A stub cannot insert a sentence into
    those prompts, and the enumeration half reads the production AST, which no
    monkeypatch can alter.
    Not-already-shipped: measured — 'interpreter' occurs in none of the four
    prompt bodies today, and the word "PYTHON=" occurs in none of them ⇒ RED.
    """
    discovered = _discover_worktree_worker_prompt_builders()
    assert discovered == _EXPECTED_WORKER_PROMPT_BUILDERS, (
        f"AC31 (enumeration): the set of prompt-builder CALL SITES that hand a "
        f"worker a build worktree changed. expected "
        f"{sorted(_EXPECTED_WORKER_PROMPT_BUILDERS)}; seen {sorted(discovered)}. "
        f"A NEW builder — or a new mutually exclusive branch inside an existing "
        f"one — must either carry the interpreter block (add it here and to "
        f"_WORKER_PROMPT_DRIVERS) or the spec must say plainly why it does not "
        f"(gate round 4 MAJOR-8, round 6 MAJOR-9)."
    )
    assert set(_WORKER_PROMPT_DRIVERS) == discovered, (
        f"AC31 (round 6, MAJOR-9): the set of DRIVEN branches must equal the set "
        f"of DISCOVERED call sites — a branch that exists but is never driven "
        f"must FAIL here, not pass silently. discovered-but-undriven="
        f"{sorted(discovered - set(_WORKER_PROMPT_DRIVERS))}; "
        f"driven-but-not-discovered="
        f"{sorted(set(_WORKER_PROMPT_DRIVERS) - discovered)}."
    )

    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)
    assert not os.path.lexists(wt / ".venv"), (
        "AC31 fixture guard: the worktree must start venv-LESS (the production shape)."
    )
    expected_abs = os.path.realpath(str(main / ".venv" / "bin" / "python"))
    candidates = {expected_abs, str(main / ".venv" / "bin" / "python")}

    missing: list[str] = []
    seen_prompts: dict[tuple[str, str, int], str] = {}
    for key in sorted(_WORKER_PROMPT_DRIVERS):
        driver, witness, branch_name = _WORKER_PROMPT_DRIVERS[key]
        result = driver(tmp_path, monkeypatch, wt)
        assert result.status == "ok", (
            f"AC31 fixture guard: expected status='ok' from {key[1]} in {key[0]} "
            f"[{branch_name}]; seen {result.status!r} "
            f"error={getattr(result, 'error', None)!r}."
        )
        data = result.data or {}
        assert witness(data), (
            f"AC31 branch witness (round 6, MAJOR-9): the driver for "
            f"{key[0]}::{key[1]}#{key[2]} was supposed to execute the "
            f"{branch_name}, but the returned StepResult does not evidence it: "
            f"delta_retry={data.get('delta_retry')!r} cycle={data.get('cycle')!r}. "
            f"Without this, an undriven mutually exclusive branch would be "
            f"reported as covered."
        )
        prompt = data.get("prompt") or ""
        seen_prompts[key] = prompt
        if not any(c in prompt for c in candidates) or "interpreter" not in prompt.lower():
            missing.append(
                f"{key[0]}::{key[1]}#{key[2]} [{branch_name}] "
                f"(prompt {len(prompt)} chars; "
                f"names_path={any(c in prompt for c in candidates)}, "
                f"says_interpreter={'interpreter' in prompt.lower()})"
            )

    red_delta = seen_prompts[("phase_5_implement.py", "_build_red_prompt", 0)]
    red_scaffold = seen_prompts[("phase_5_implement.py", "_build_red_prompt", 1)]
    assert red_delta != red_scaffold, (
        "AC31 branch witness: `_build_red_prompt`'s delta-retry and scaffold "
        "returns produced IDENTICAL prompts, so only one branch was really "
        "exercised; the two returns must be distinguishable."
    )

    assert missing == [], (
        f"AC31: EVERY worker prompt path that hands over a build worktree must "
        f"name the resolved interpreter {expected_abs!r} and speak about it — one "
        f"prompt of {len(_WORKER_PROMPT_DRIVERS)} is what the gate rejected, and "
        f"one BRANCH of {len(_WORKER_PROMPT_DRIVERS)} is MAJOR-9. call sites "
        f"still without an interpreter block: {missing}."
    )


# ─── AC32 (gate round 4): the override FORM, measured not assumed ─────────────


def test_ac32_interpreter_block_names_the_make_python_override_form(
    tmp_path: Path, monkeypatch
) -> None:
    """AC32: the interpreter block names the `make PYTHON=<abs>` form.

    Measured by the gate on the real target repo: `ppba/Makefile:3` is
    `PYTHON := $(CURDIR)/.venv/bin/python`.  `:=` binds at parse time from the
    Makefile's own text, so an ENVIRONMENT variable `PYTHON=<abs> make test` does
    NOT override it — only a command-line override, `make PYTHON=<abs> test`,
    does.  Design item 3 stated the env form and was wrong; a prompt that teaches
    the env form teaches a no-op, and the worker lands right back on the
    `.venv/bin/python` that is not there.

    Isolation-forcing: the venv-less-worktree layout is built fresh under this
    test's `tmp_path`; the prompt is built from a ctx pointing at it.  No shared
    state — fails identically run alone.
    Victim-coupled: asserts the ORDER of tokens on a real line of the real
    `_build_fix_prompt` output, tied to the absolute path only a climbing
    resolver can produce.  A stub cannot emit that line, and passing AC26 (the
    path is named) does not pass this (the invocation form must be correct).
    Not-already-shipped: measured — "PYTHON=" occurs nowhere in any prompt the
    engine builds today ⇒ RED.
    """
    main, wt = _make_linked_worktree(tmp_path)
    _assert_layout_is_a_real_worktree(main, wt)

    result = _build_fix_prompt_for(tmp_path, monkeypatch, wt)
    assert result.status == "ok", (
        f"AC32 fixture guard: expected status='ok' from _build_fix_prompt; "
        f"seen {result.status!r} error={getattr(result, 'error', None)!r}."
    )
    prompt = result.data["prompt"]
    expected_abs = os.path.realpath(str(main / ".venv" / "bin" / "python"))
    candidates = {expected_abs, str(main / ".venv" / "bin" / "python")}

    lines = [ln for ln in prompt.splitlines() if "PYTHON=" in ln]
    assert lines, (
        f"AC32: expected the interpreter block to show the worker HOW to point "
        f"the project's build at {expected_abs!r} — no line containing 'PYTHON=' "
        f"appears in a prompt of {len(prompt)} chars."
    )

    command_line_form = [ln for ln in lines if re.search(r"\bmake\b[^\n]*?PYTHON=", ln)]
    assert command_line_form, (
        f"AC32: expected the `make PYTHON=<abs>` COMMAND-LINE override form "
        f"(`make` before `PYTHON=`), because `ppba/Makefile:3` binds "
        f"`PYTHON := $(CURDIR)/.venv/bin/python` with `:=` and an environment "
        f"variable does not override it. seen {len(lines)} 'PYTHON=' line(s), "
        f"none of that shape: {lines!r}."
    )
    assert any(c in ln for ln in command_line_form for c in candidates), (
        f"AC32: the `make PYTHON=` override must carry the RESOLVED ABSOLUTE "
        f"interpreter {expected_abs!r}, not a placeholder; seen "
        f"{command_line_form!r}."
    )

    env_prefix_form = [ln for ln in lines if re.search(r"PYTHON=\S+\s+make\b", ln)]
    assert not env_prefix_form, (
        f"AC32: the ENVIRONMENT-prefix form `PYTHON=<abs> make ...` must not be "
        f"taught — `:=` in ppba/Makefile:3 ignores it, so the worker silently "
        f"falls back to the missing `.venv/bin/python`. seen {env_prefix_form!r}."
    )
