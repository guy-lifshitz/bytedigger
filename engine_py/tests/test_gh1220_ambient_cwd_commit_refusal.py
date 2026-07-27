"""RED tests for GH1220 v2 — ambient-cwd refusal on ALL mutating-git paths +
anti-rot lint + post-suite live-repo HEAD sentinel.

Frozen spec (v2): SHARED/memory/Decisions/2026-07-26_1C248308_gh1220_ambient_cwd_commit_refusal_spec.md

v2 supersedes v1 (Opus-gate REJECTED, 7 MAJOR): the chokepoint is every
MUTATING git op reachable at an ambient git_cwd, not just `git commit`
call-sites -- `git stash push -u` (destroys untracked files), a working-tree
`Path.unlink()`, `git checkout <sha> --`, and `git worktree add/remove` are
all in scope now (Change B, B1-B10).

Contract summary:
  Change A (lib/git_cwd.py): label EVERY ambient-contaminated resolution,
  including verbatim-unresolved relative `cfg["git_cwd"]` /
  `prev_data["git_cwd"]` (levels 1/2, MAJOR-3) as well as the relative
  current_worktree_path / scratchpad_climb labels; expose
  `EXPLICIT_GIT_CWD_SOURCES` / `AMBIENT_GIT_CWD_SOURCES` (5 ambient + 4
  explicit) / `is_ambient_git_cwd` / `resolve_git_cwd_for_write`.
  Change B: every mutating-git site (`_commit_red_tests`, `_commit_green_code`,
  `_commit_fix_code`, `_commit_fix_tests`, `_attempt_red_cycle_restore`,
  `_checkpoint_green_worktree`, `_autocommit_fix_tail`,
  `_compute_baseline_failed`, `_compute_baseline_typecheck_count`,
  `_verify_fix_typecheck`) refuses an ambient git_cwd immediately before its
  first mutating op (not merely before `git add`/`git commit`) — new error
  code `E_GIT_CWD_AMBIENT` for the commit-shaped sites; the two baseline
  helpers keep their existing declared `None` = "unavailable" contract.
  Change C: new `tests/_live_repo_sentinel.py` — post-suite live-repo HEAD
  drift detector, fail-open on infra, fail-closed on detection, with a
  single module-global `_STATE` + `reset_state()` (C7) so RED's mid-session
  `pytest_configure` calls against tmp repos never leak into the real
  conftest-wired instance.
  Change D: new `lib/mutating_git_lint.py` — deterministic AST-based lint so
  the §0 mutating-git enumeration (which has rotted twice) cannot rot again;
  every enclosing function of a mutating-git call must be declared in
  `GUARDED_WRITE_SITES` or `DECLARED_NON_GIT_CWD_SITES`, and the registry is
  re-verified against actual guard markers, not rubber-stamped; also flags a
  mutating verb mis-routed through `git_port.git_read` (AC37).
  Change E: `lib/git_write_port.py` (`op_capture`, `op_with_lock_retry`) and
  `lib/git_port.py` (`git_read`) refuse a RELATIVE `cwd` — provenance-free,
  no false positives — plus an unconditional `git_write_at_cwd` telemetry
  event on every port write. Explicitly a BACKSTOP, not a substitute for
  Change B: an absolute `cwd`, including `str(Path.cwd())`, sails through.

§1q: every symbol that does not exist yet on `main` today
(`is_ambient_git_cwd`, `resolve_git_cwd_for_write`, `AMBIENT_GIT_CWD_SOURCES`,
`EXPLICIT_GIT_CWD_SOURCES`, `E_GIT_CWD_AMBIENT`, `tests/_live_repo_sentinel.py`,
`lib/mutating_git_lint.py`, `reset_state`) is imported/referenced ONLY inside
the test body that needs it — never at module top level — so this file
always COLLECTS cleanly and fails at assert-time, never at collect-time.
`lib/git_write_port.py` and `lib/git_port.py` already exist today (Change E
only adds behavior to existing functions), so `git_write_port`/`git_port`
are imported inside the AC38-41 test bodies too, for symmetry with the rest
of this file and because their CURRENT behavior (no refusal) is what makes
those ACs fail pre-GREEN.

§1l: every ambient-refusal AC drives the REAL production step against a REAL
tmp git repo (git init + a base commit) and asserts a real side effect —
commit count / `git status --porcelain` / `git stash list` / `git worktree
list` / raw file bytes unchanged after the call — never only the returned
error code or event. No test ever points at the live repo except AC16/AC17/
AC32/AC37 (explicitly real-data ACs per spec, read-only assertions) and AC31
(which restores `_STATE` before returning, per the file-level autouse
fixture below).

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from helpers import live_repo  # noqa: E402 — bd#2 git-checkout requirement
from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_5_implement as p5  # noqa: E402
import phase_6_review as p6  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════
# shared helpers (real git repos, no mocking of the UUT — §1l)
# ═════════════════════════════════════════════════════════════════════════


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str = "# impl\n") -> Path:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _make_repo_with_base_commit(tmp_path: Path, name: str = "repo") -> "tuple[Path, str]":
    """Real tmp git repo (§1j realpath) + one placeholder commit.

    `name` gives each repo its own directory under `tmp_path` -- MANDATORY
    when a single test builds more than one repo (e.g. AC8b, AC29), since
    calling this twice with the same default name re-inits the same
    directory and the second `git commit` (nothing staged) raises under
    `check=True`."""
    repo = Path(os.path.realpath(str(tmp_path / name)))
    _init_repo(repo)
    base_sha = _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo, base_sha


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _commit_count(repo: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return int((r.stdout or "0").strip() or 0)


def _porcelain(repo: Path) -> str:
    # `-uall` (== `--untracked-files=all`): plain `--porcelain` collapses an
    # ENTIRELY untracked directory to a single `?? tests/` line rather than
    # listing its files -- AC8/AC11 write into a brand-new `tests/` dir with
    # no other tracked sibling, so the per-file assertions below
    # (`"test_ac8.py" in _porcelain(repo)`) would false-fail on a WORKING
    # guard, not just a broken one. `-uall` always lists individual files,
    # so it strictly adds discriminating power and never weakens any
    # existing per-file assertion elsewhere in this suite.
    return subprocess.run(
        ["git", "status", "--porcelain", "-uall"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout


def _record_events(monkeypatch, module) -> "list[dict]":
    captured: list[dict] = []

    def _recorder(event_type, payload=None, **kw):
        captured.append({"type": str(event_type), "payload": dict(payload or {})})
        return None

    monkeypatch.setattr(module, "_emit_safe", _recorder)
    return captured


def _make_ctx_ambient(scratchpad: Path, **extra) -> WorkflowContext:
    """org_config carrying NO git_cwd — resolution must fall to whatever the
    process CWD is at call time (the test controls that via monkeypatch.chdir)."""
    org: dict = {"scratchpad_dir": str(scratchpad), **extra}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="q", session_id="test-gh1220", persona="hal",
        framework=None, domain=None,
    )


def _build_repo_with_scratchpad(tmp_path: Path) -> "tuple[Path, Path]":
    """A tmp_repo with a bare .git marker dir and a nested scratchpad dir
    (mirrors test_gh381_git_cwd_resolver.py — the scratchpad-climb branch only
    checks (p / '.git').exists(), a bare marker dir is sufficient)."""
    tmp_repo = tmp_path / "repo"
    (tmp_repo / ".git").mkdir(parents=True)
    scratchpad = tmp_repo / ".hal-build" / "scratchpad" / "r1"
    scratchpad.mkdir(parents=True)
    return tmp_repo.resolve(), scratchpad.resolve()


# ═════════════════════════════════════════════════════════════════════════
# Change A — lib/git_cwd.py labelling (AC1-AC7)
# ═════════════════════════════════════════════════════════════════════════


def test_ac1_relative_current_worktree_path_labelled_ambient(tmp_path, monkeypatch):
    """AC1: relative current_worktree_path -> source
    'cfg_current_worktree_path_relative'; path byte-identical to today's
    str(Path(raw).expanduser().resolve()). Pre-GREEN FAIL: today's resolver
    always returns 'cfg_current_worktree_path' regardless of relative/absolute
    input (lib/git_cwd.py L45-49 has no relative/absolute branch)."""
    from lib.git_cwd import resolve_git_cwd_with_source  # noqa: PLC0415

    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.chdir(base)
    rel = "rel/dir"

    path, source = resolve_git_cwd_with_source({"current_worktree_path": rel})

    expected_path = str(Path(rel).expanduser().resolve())
    assert path == expected_path, (
        f"expected path unchanged vs today ({expected_path!r}); actual {path!r}"
    )
    assert source == "cfg_current_worktree_path_relative", (
        f"expected source=='cfg_current_worktree_path_relative' for a relative "
        f"current_worktree_path; actual {source!r}"
    )


def test_ac2_absolute_current_worktree_path_labelled_explicit(tmp_path):
    """AC2 (regression pin): absolute current_worktree_path -> source stays
    'cfg_current_worktree_path' (unaffected by the new relative-detection)."""
    from lib.git_cwd import resolve_git_cwd_with_source  # noqa: PLC0415

    abs_dir = tmp_path / "worktree"
    abs_dir.mkdir()

    path, source = resolve_git_cwd_with_source({"current_worktree_path": str(abs_dir)})

    assert path == str(abs_dir.resolve())
    assert source == "cfg_current_worktree_path", (
        f"expected source=='cfg_current_worktree_path' for an absolute path; "
        f"actual {source!r}"
    )


def test_ac3_relative_scratchpad_climb_labelled_ambient(tmp_path, monkeypatch):
    """AC3: relative scratchpad_dir whose climb finds a .git -> source
    'scratchpad_climb_relative', same resolved path as today. Pre-GREEN FAIL:
    today's climb branch always labels 'scratchpad_climb' regardless of
    relative/absolute input (lib/git_cwd.py L51-62)."""
    from lib.git_cwd import resolve_git_cwd_with_source  # noqa: PLC0415

    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    monkeypatch.chdir(tmp_path)
    rel_scratchpad = os.path.relpath(str(scratchpad), str(tmp_path))

    path, source = resolve_git_cwd_with_source({"scratchpad_dir": rel_scratchpad})

    assert path == str(tmp_repo), (
        f"expected the scratchpad-climb result unchanged at {str(tmp_repo)!r}; "
        f"actual {path!r}"
    )
    assert source == "scratchpad_climb_relative", (
        f"expected source=='scratchpad_climb_relative' for a relative "
        f"scratchpad_dir; actual {source!r}"
    )


def test_ac4_absolute_scratchpad_climb_labelled_explicit(tmp_path):
    """AC4 (regression pin): absolute scratchpad_dir climb -> source stays
    'scratchpad_climb'."""
    from lib.git_cwd import resolve_git_cwd_with_source  # noqa: PLC0415

    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)

    path, source = resolve_git_cwd_with_source({"scratchpad_dir": str(scratchpad)})

    assert path == str(tmp_repo)
    assert source == "scratchpad_climb", (
        f"expected source=='scratchpad_climb' for an absolute scratchpad_dir; "
        f"actual {source!r}"
    )


def test_ac5_is_ambient_git_cwd_both_directions():
    """AC5 (MAJOR-A fix): `is_ambient_git_cwd` is True for each of the FIVE
    ambient labels (§1 A4 -- 'cwd', 'cfg_git_cwd_relative', 'prev_data_relative',
    'cfg_current_worktree_path_relative', 'scratchpad_climb_relative') and
    False for each of the four explicit labels (both directions, one test).
    A prior version of this test asserted only 3 ambient labels -- a v1
    leftover that directly contradicted AC7b/AC35 (which correctly require
    5); no implementation could satisfy both, so this is the corrected form.
    Pre-GREEN FAIL: ImportError, `is_ambient_git_cwd` /
    `AMBIENT_GIT_CWD_SOURCES` / `EXPLICIT_GIT_CWD_SOURCES` do not exist yet."""
    from lib.git_cwd import (  # noqa: PLC0415
        AMBIENT_GIT_CWD_SOURCES,
        EXPLICIT_GIT_CWD_SOURCES,
        is_ambient_git_cwd,
    )

    assert set(AMBIENT_GIT_CWD_SOURCES) == {
        "cwd", "cfg_git_cwd_relative", "prev_data_relative",
        "cfg_current_worktree_path_relative", "scratchpad_climb_relative",
    }, f"actual AMBIENT_GIT_CWD_SOURCES={set(AMBIENT_GIT_CWD_SOURCES)!r}"
    assert set(EXPLICIT_GIT_CWD_SOURCES) == {
        "cfg_git_cwd", "prev_data", "cfg_current_worktree_path", "scratchpad_climb",
    }, f"actual EXPLICIT_GIT_CWD_SOURCES={set(EXPLICIT_GIT_CWD_SOURCES)!r}"

    for label in AMBIENT_GIT_CWD_SOURCES:
        assert is_ambient_git_cwd(label) is True, (
            f"expected is_ambient_git_cwd({label!r}) is True; actual False"
        )
    for label in EXPLICIT_GIT_CWD_SOURCES:
        assert is_ambient_git_cwd(label) is False, (
            f"expected is_ambient_git_cwd({label!r}) is False; actual True"
        )


def test_ac6_resolve_git_cwd_for_write_ambient_vs_explicit(tmp_path, monkeypatch):
    """AC6: `resolve_git_cwd_for_write({})` -> (None, "cwd");
    `resolve_git_cwd_for_write({"git_cwd": "/x"})` -> ("/x", "cfg_git_cwd").
    Pre-GREEN FAIL: ImportError, the function does not exist yet."""
    from lib.git_cwd import resolve_git_cwd_for_write  # noqa: PLC0415

    monkeypatch.chdir(tmp_path)

    path, source = resolve_git_cwd_for_write({})
    assert path is None, f"expected path is None on an ambient resolution; actual {path!r}"
    assert source == "cwd", f"expected source=='cwd'; actual {source!r}"

    path2, source2 = resolve_git_cwd_for_write({"git_cwd": "/x"})
    assert path2 == "/x", f"expected path=='/x' for an explicit cfg_git_cwd; actual {path2!r}"
    assert source2 == "cfg_git_cwd", f"expected source=='cfg_git_cwd'; actual {source2!r}"


def test_ac7_emit_resolver_resolved_called_with_relative_labels(tmp_path, monkeypatch):
    """AC7: `emit_resolver_resolved` is called with the new relative labels
    for BOTH the current_worktree_path and scratchpad_climb branches (§1g
    resolver contract pin). Pre-GREEN FAIL: today's code only ever emits
    'cfg_current_worktree_path' / 'scratchpad_climb', never the '_relative'
    labels."""
    from lib.git_cwd import resolve_git_cwd_with_source  # noqa: PLC0415
    import lib.git_cwd as git_cwd_mod  # noqa: PLC0415

    captured: list[tuple] = []
    monkeypatch.setattr(
        git_cwd_mod, "emit_resolver_resolved",
        lambda kind, source, value: captured.append((kind, source, value)),
    )

    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.chdir(base)
    resolve_git_cwd_with_source({"current_worktree_path": "rel/dir"})
    expected_worktree_path = str(Path("rel/dir").expanduser().resolve())
    assert ("git_cwd", "cfg_current_worktree_path_relative", expected_worktree_path) in captured, (
        f"expected an emit_resolver_resolved call with the relative worktree "
        f"label; actual calls={captured!r}"
    )

    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    monkeypatch.chdir(tmp_path)
    rel_scratchpad = os.path.relpath(str(scratchpad), str(tmp_path))
    resolve_git_cwd_with_source({"scratchpad_dir": rel_scratchpad})
    assert ("git_cwd", "scratchpad_climb_relative", str(tmp_repo)) in captured, (
        f"expected an emit_resolver_resolved call with the relative "
        f"scratchpad-climb label; actual calls={captured!r}"
    )


def test_ac7b_relative_cfg_and_prev_data_git_cwd_labelled_ambient(tmp_path, monkeypatch):
    """AC7b (MAJOR-3): resolver levels 1/2 (`cfg["git_cwd"]` /
    `prev_data["git_cwd"]`) are returned VERBATIM AND UNRESOLVED today -- a
    relative value there is anchored by the OS to the ambient process CWD
    the identical contamination levels 3/4 close. A relative `cfg["git_cwd"]`
    must label `cfg_git_cwd_relative` (path unchanged, still verbatim); same
    for a relative `prev_data["git_cwd"]` -> `prev_data_relative`. And
    `resolve_git_cwd_for_write` must return `(None, ...)` for both -- NOT the
    verbatim relative string, which the write-boundary caller would then
    silently anchor to cwd. Pre-GREEN FAIL: today both levels always label
    'cfg_git_cwd' / 'prev_data' regardless of relative/absolute input
    (lib/git_cwd.py L33-43), and `resolve_git_cwd_for_write` does not exist."""
    from lib.git_cwd import (  # noqa: PLC0415
        resolve_git_cwd_for_write,
        resolve_git_cwd_with_source,
    )

    monkeypatch.chdir(tmp_path)

    path, source = resolve_git_cwd_with_source({"git_cwd": "rel/dir"})
    assert path == "rel/dir", (
        f"expected the verbatim, unresolved string unchanged vs today; actual {path!r}"
    )
    assert source == "cfg_git_cwd_relative", (
        f"expected source=='cfg_git_cwd_relative' for a relative cfg git_cwd; "
        f"actual {source!r}"
    )

    path2, source2 = resolve_git_cwd_with_source({}, {"git_cwd": "rel/dir2"})
    assert path2 == "rel/dir2", f"expected verbatim; actual {path2!r}"
    assert source2 == "prev_data_relative", (
        f"expected source=='prev_data_relative' for a relative prev_data git_cwd; "
        f"actual {source2!r}"
    )

    write_path, write_source = resolve_git_cwd_for_write({"git_cwd": "rel/dir"})
    assert write_path is None, (
        f"expected resolve_git_cwd_for_write to refuse a relative cfg git_cwd "
        f"(path is None); actual {write_path!r}"
    )
    assert write_source == "cfg_git_cwd_relative"

    write_path2, write_source2 = resolve_git_cwd_for_write({}, {"git_cwd": "rel/dir2"})
    assert write_path2 is None, (
        f"expected resolve_git_cwd_for_write to refuse a relative prev_data "
        f"git_cwd (path is None); actual {write_path2!r}"
    )
    assert write_source2 == "prev_data_relative"


# ═════════════════════════════════════════════════════════════════════════
# Change B — commit-path refusal (AC8-AC15, AC26-AC28), each a real tmp git
# repo, asserting the production side effect: commit count / porcelain /
# stash list / worktree list unchanged (§1l).
# ═════════════════════════════════════════════════════════════════════════


def test_ac8_commit_red_tests_refuses_ambient_cwd_write(tmp_path, monkeypatch):
    """AC8: `_commit_red_tests` with no explicit git_cwd, process CWD chdir'd
    into a real tmp repo -> E_GIT_CWD_AMBIENT, recoverable=False, AND the tmp
    repo's commit count is unchanged. Pre-GREEN FAIL: no ambient guard exists
    at the write boundary today — the ambient repo's untracked test file is
    silently git-added and committed (the GH1220 incident shape)."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "tests/test_ac8.py", "def test_x():\n    assert True\n")
    monkeypatch.chdir(repo)

    no_git_scratch = tmp_path / "no_git_ancestor_ac8" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {"cycle": 1}

    before = _commit_count(repo)

    result = p5._commit_red_tests(ctx, prev)

    assert result.error_code == "E_GIT_CWD_AMBIENT", (
        f"expected error_code=='E_GIT_CWD_AMBIENT'; actual {result.error_code!r} "
        f"(status={result.status!r}, error={getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False; actual {result.recoverable!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected NO commit in the ambient repo (count stays {before}); actual {after}"
    )
    assert "test_ac8.py" in _porcelain(repo), (
        "expected the test file to remain UNCOMMITTED (git status --porcelain "
        f"non-empty) after the refusal; actual porcelain={_porcelain(repo)!r}"
    )


def test_ac8b_ambient_pre_red_ref_never_consumed_by_a_later_non_ambient_entry(tmp_path, monkeypatch):
    """AC8b (§1n accepted residual): `_persist_pre_red_ref` at L2029 writes a
    boundary ref derived from the AMBIENT repo's HEAD before the new guard
    (the guard cannot precede it without also reordering E_GIT_BAD_STATE for
    a non-ambient failure mode -- declared out of scope). This pins the
    accepted consequence: that ambient-tainted ref is never consumed by a
    later, genuinely non-ambient entry -- a corrected production re-entry
    uses its OWN scratchpad and resolves its OWN correct pre-red boundary
    from its OWN HEAD, completely unaffected by the ambient scratchpad's
    stale ref. Pre-GREEN FAIL: the first (ambient) call does not refuse at
    all today (no E_GIT_CWD_AMBIENT exists), so the fixture precondition
    itself fails."""
    _record_events(monkeypatch, p5)
    ambient_repo, ambient_base_sha = _make_repo_with_base_commit(tmp_path, name="ambient_repo")
    _write_file(ambient_repo, "tests/test_ac8b.py", "def test_x():\n    assert True\n")
    monkeypatch.chdir(ambient_repo)

    ambient_scratchpad = tmp_path / "scratch_ac8b_ambient"
    ambient_scratchpad.mkdir()
    ctx_ambient = _make_ctx_ambient(ambient_scratchpad)
    prev = MagicMock()
    prev.data = {"cycle": 1}

    result_ambient = p5._commit_red_tests(ctx_ambient, prev)
    assert result_ambient.error_code == "E_GIT_CWD_AMBIENT", (
        f"fixture precondition: expected the ambient call to refuse; actual "
        f"{result_ambient.error_code!r} (status={result_ambient.status!r})"
    )

    ambient_ref_path = ambient_scratchpad / p5.PRE_RED_REF_RELPATH
    assert ambient_ref_path.exists(), (
        "expected the pre-red ref to have been written on the ambient path "
        "(the accepted residual, §1n) even though the commit itself refused"
    )
    ambient_ref_value = ambient_ref_path.read_text().strip()
    assert ambient_ref_value == ambient_base_sha, (
        f"expected the ambient-written ref to hold the AMBIENT repo's "
        f"boundary {ambient_base_sha!r}; actual {ambient_ref_value!r}"
    )

    # A later, genuinely non-ambient entry -- its OWN scratchpad, an explicit
    # git_cwd -- resolves its own correct boundary; the ambient scratchpad
    # above is never read again by anything downstream.
    #
    # `_make_repo_with_base_commit` always writes byte-identical content
    # ("# placeholder\n") with the identical message ("init"), so calling it
    # for both `ambient_repo` and `real_repo` produces the SAME commit SHA
    # (content-addressed, and both repos are created within the same second)
    # -- an inequality assertion against `ambient_ref_value` would then have
    # NO discriminating power: it cannot tell "correctly used its own fresh
    # boundary" apart from "wrongly reused the stale ambient ref", since both
    # would coincidentally equal the same value. Built manually here with
    # DISTINCT content/message so `real_base_sha` is genuinely different from
    # `ambient_base_sha`, restoring the assertion's discriminating power.
    real_repo = Path(os.path.realpath(str(tmp_path / "real_repo")))
    _init_repo(real_repo)
    real_base_sha = _commit_file(
        real_repo, "src/placeholder.py",
        "# placeholder -- real_repo, distinct from ambient_repo\n",
        "init real_repo",
    )
    # Left UNCOMMITTED (mirrors the ambient leg's _write_file above) -- the
    # explicit-source leg's own frozen pre-red SHA resolves to real_base_sha
    # (its fresh scratchpad has no persisted ref yet, so
    # _resolve_frozen_pre_red_sha falls back to current HEAD == real_base_sha),
    # and _derive_red_paths_via_git_diff needs an actual untracked/uncommitted
    # diff against that boundary to discover this RED test file on disk.
    # Pre-committing it here (as an earlier revision did) leaves NO diff at
    # all against the real_repo's own HEAD, so the real step finds no RED
    # test files and errors instead of genuinely running.
    _write_file(real_repo, "tests/test_ac8b_real.py", "def test_y():\n    assert True\n")
    real_scratchpad = tmp_path / "scratch_ac8b_real"
    real_scratchpad.mkdir()
    ctx_explicit = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(real_scratchpad), "git_cwd": str(real_repo)},
        question="q", session_id="test-gh1220-ac8b", persona="hal",
        framework=None, domain=None,
    )
    prev2 = MagicMock()
    prev2.data = {"cycle": 1}

    result_explicit = p5._commit_red_tests(ctx_explicit, prev2)

    assert result_explicit.status == "ok", (
        f"expected the explicit-source entry to succeed; actual "
        f"{result_explicit.status!r}: {getattr(result_explicit, 'error', '')}"
    )
    real_ref_path = real_scratchpad / p5.PRE_RED_REF_RELPATH
    assert real_ref_path.exists(), "expected the real entry to persist its own ref"
    real_ref_value = real_ref_path.read_text().strip()
    # Positive companion (mandatory, not decorative): "differs from the
    # ambient ref" alone is satisfied by ANY wrong-but-different value, not
    # just the correct one -- pin the actual correct value too, so a GREEN
    # that wrongly resolves to some OTHER stale/wrong boundary (not the
    # ambient one, but still not its own HEAD) cannot slip past this test.
    assert real_ref_value == real_base_sha, (
        f"expected the non-ambient entry's persisted boundary to equal its "
        f"OWN repo's HEAD {real_base_sha!r}; actual {real_ref_value!r}"
    )
    assert real_ref_value != ambient_ref_value, (
        "expected the non-ambient entry's persisted boundary to differ from "
        "the ambient scratchpad's stale ref -- never consumed downstream "
        "(discriminating here because real_repo and ambient_repo were built "
        "with deliberately distinct content/message, so this is not a "
        "coincidental SHA collision)"
    )
    # The ambient scratchpad's ref is untouched by the later, unrelated entry.
    assert ambient_ref_path.read_text().strip() == ambient_ref_value


def test_ac9_commit_green_code_refuses_ambient_cwd_write(tmp_path, monkeypatch):
    """AC9: `_commit_green_code`, same shape as AC8 — E_GIT_CWD_AMBIENT,
    recoverable=False, commit count unchanged. Pre-GREEN FAIL: no ambient
    guard exists before the git add/commit at phase_5_implement.py L6934+."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED")
    _write_file(repo, "src/module.py", "def foo(): return 42\n")
    monkeypatch.chdir(repo)

    no_git_scratch = tmp_path / "no_git_ancestor_ac9" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {
        "cycle": 1, "red_commit_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "harness_tool_record",
    }

    before = _commit_count(repo)

    result = p5._commit_green_code(ctx, prev)

    assert result.error_code == "E_GIT_CWD_AMBIENT", (
        f"expected error_code=='E_GIT_CWD_AMBIENT'; actual {result.error_code!r} "
        f"(status={result.status!r}, error={getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False; actual {result.recoverable!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected NO commit in the ambient repo (count stays {before}); actual {after}"
    )
    assert "src/module.py" in _porcelain(repo), (
        "expected src/module.py to remain uncommitted after the refusal; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


def test_ac10_commit_fix_code_refuses_ambient_cwd_write(tmp_path, monkeypatch):
    """AC10: `_commit_fix_code` with a NON-EMPTY manifest (so it reaches the
    previously-unguarded branch identified in spec §0's "PARTIAL" hole) —
    E_GIT_CWD_AMBIENT, recoverable=False, commit count unchanged. Pre-GREEN
    FAIL: the source is only consulted inside the empty-manifest fallback
    (L4202); a non-empty manifest sails straight to git add/commit with no
    source check at all — this is the exact hole quoted in spec §0."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/fixed.py", "def fixed(): return 2\n")
    monkeypatch.chdir(repo)

    no_git_scratch = tmp_path / "no_git_ancestor_ac10" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["src/fixed.py"],
        "manifest_source": "harness_tool_record",
    }

    before = _commit_count(repo)

    result = p6._commit_fix_code(ctx, prev)

    assert result.error_code == "E_GIT_CWD_AMBIENT", (
        f"expected error_code=='E_GIT_CWD_AMBIENT'; actual {result.error_code!r} "
        f"(status={result.status!r}, error={getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False; actual {result.recoverable!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected NO commit in the ambient repo (count stays {before}); actual {after}"
    )
    assert "src/fixed.py" in _porcelain(repo), (
        "expected src/fixed.py to remain uncommitted after the refusal; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


def test_ac10b_commit_fix_code_ambient_preserves_off_surface_file_before_unlink(
    tmp_path, monkeypatch,
):
    """AC10b (MAJOR-2): `_commit_fix_code` with a `review_doc_path` that makes
    the GH947 surface guard fire (`_partition_fix_surface`), plus an
    UNTRACKED off-surface file not mentioned in the review doc and absent at
    `pre_fix_sha` -- the guard must precede `Path.unlink()` at L4257, NOT
    merely `git add` at L4325. The off-surface file must still EXIST (with
    its original bytes) after the ambient refusal. Pre-GREEN FAIL: no guard
    exists anywhere in this function; the GH947 surface scan runs, classifies
    the file as a violation, and deletes it (L4266) before the (currently
    absent) refusal could ever fire."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    off_surface_body = "def rogue(): return 1\n"
    off_surface = _write_file(repo, "src/off_surface.py", off_surface_body)
    monkeypatch.chdir(repo)

    review_doc = tmp_path / "review.md"
    review_doc.write_text("# Review\nNothing about off_surface here.\n")

    no_git_scratch = tmp_path / "no_git_ancestor_ac10b" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["src/off_surface.py"],
        "manifest_source": "harness_tool_record",
        "review_doc_path": str(review_doc),
    }

    result = p6._commit_fix_code(ctx, prev)

    assert result.error_code == "E_GIT_CWD_AMBIENT", (
        f"expected error_code=='E_GIT_CWD_AMBIENT'; actual {result.error_code!r} "
        f"(status={result.status!r}, error={getattr(result, 'error', '')!r})"
    )
    assert off_surface.exists(), (
        "expected the untracked off-surface file to SURVIVE the ambient "
        "refusal -- the guard must precede Path.unlink() at L4257, not merely "
        "git add at L4325"
    )
    assert off_surface.read_text() == off_surface_body, (
        "expected the off-surface file's bytes unchanged"
    )


def test_ac10c_commit_fix_code_explicit_source_reach_proof_for_unlink(tmp_path, monkeypatch):
    """AC10c (A9 companion positive control for AC10b): the SAME fixture as
    AC10b, but with an EXPLICIT source -- proves the fixture genuinely
    reaches `Path.unlink()` at L4257 for real. AC10b's own absence assertion
    (`off_surface.exists()`) has no companion proof that control ever
    arrived at the unlink loop; the guard's own event cannot serve as that
    proof here, because when the guard fires correctly `fix_surface_violation`
    is precisely what does NOT fire. This test is the anchor: it asserts
    `fix_surface_violation{deleted: True}` fired and that the off-surface
    file is GENUINELY GONE for an explicit source, so a future fixture or
    `_partition_fix_surface` change that silently stops reaching the unlink
    loop breaks THIS test, not just AC10b's absence check."""
    captured = _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    off_surface_body = "def rogue(): return 1\n"
    off_surface = _write_file(repo, "src/off_surface.py", off_surface_body)

    review_doc = tmp_path / "review.md"
    review_doc.write_text("# Review\nNothing about off_surface here.\n")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="q", session_id="test-gh1220-ac10c", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["src/off_surface.py"],
        "manifest_source": "harness_tool_record",
        "review_doc_path": str(review_doc),
    }

    p6._commit_fix_code(ctx, prev)

    violation_events = [e for e in captured if e["type"] == "fix_surface_violation"]
    assert len(violation_events) == 1, (
        f"REACH-PROOF FAILED: expected exactly 1 'fix_surface_violation' "
        f"event, proving the fixture genuinely reaches the GH947 surface "
        f"guard/unlink loop for an explicit source; actual events seen: "
        f"{[e['type'] for e in captured]!r}"
    )
    assert violation_events[0]["payload"].get("deleted") is True, (
        f"expected deleted=True in the fix_surface_violation payload; actual "
        f"{violation_events[0]['payload']!r}"
    )
    assert not off_surface.exists(), (
        "expected the off-surface file to be GENUINELY DELETED for an "
        "explicit source -- this is the reach-proof AC10b's absence "
        "assertion depends on"
    )


def test_ac11_commit_fix_tests_refuses_ambient_cwd_write(tmp_path, monkeypatch):
    """AC11: `_commit_fix_tests`, same shape as AC10 — E_GIT_CWD_AMBIENT,
    recoverable=False, commit count unchanged."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "tests/test_fixed.py", "def test_fixed(): assert True\n")
    monkeypatch.chdir(repo)

    no_git_scratch = tmp_path / "no_git_ancestor_ac11" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["tests/test_fixed.py"],
        "manifest_source": "harness_tool_record",
    }

    before = _commit_count(repo)

    result = p6._commit_fix_tests(ctx, prev)

    assert result.error_code == "E_GIT_CWD_AMBIENT", (
        f"expected error_code=='E_GIT_CWD_AMBIENT'; actual {result.error_code!r} "
        f"(status={result.status!r}, error={getattr(result, 'error', '')!r})"
    )
    assert result.recoverable is False, (
        f"expected recoverable is False; actual {result.recoverable!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected NO commit in the ambient repo (count stays {before}); actual {after}"
    )
    assert "tests/test_fixed.py" in _porcelain(repo), (
        "expected tests/test_fixed.py to remain uncommitted after the refusal; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


def test_ac12_attempt_red_cycle_restore_refuses_ambient_cwd(tmp_path, monkeypatch):
    """AC12: `_attempt_red_cycle_restore`'s cycle>=2 restore path must never
    touch a repo whose git_cwd resolved from the ambient process CWD. The
    guard must precede the `git checkout <c1_sha> --` at L2337, NOT merely
    the commit at L2358 (MAJOR-2/MAJOR-5): cycle-1 content ('assert False')
    is committed BEFORE the current HEAD's cycle-2 degenerate rewrite
    ('assert True'), so absent a correctly-placed guard the checkout WOULD
    succeed and stage a real diff. Asserts HEAD sha, `git status --porcelain`,
    AND the red test file's raw bytes are ALL unchanged -- a guard placed
    after the checkout but before the commit would pass a commit-count-only
    assertion while having already overwritten and staged the working tree;
    it fails this one. Returns None (fall through to legacy
    E_RED_NOT_FAILING) and emits exactly one
    red_restore_skipped{reason:'ambient_cwd'}. Pre-GREEN FAIL: no guard
    exists at any point in this function; the checkout runs, succeeds,
    stages a real diff, and the fixture's own commit-count assertion alone
    could be satisfied by a misplaced guard -- the bytes/porcelain
    assertions are what force correct placement."""
    captured = _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    c1_sha = _commit_file(
        repo, "tests/test_x.py", "def test_x():\n    assert False  # cycle 1 RED\n",
        "cycle 1 red test",
    )
    _commit_file(
        repo, "tests/test_x.py",
        "def test_x():\n    assert True  # cycle 2 degenerate rewrite\n",
        "cycle 2 degenerate rewrite",
    )
    monkeypatch.chdir(repo)

    scratchpad = tmp_path / "no_git_ancestor_ac12" / "scratch"
    scratchpad.mkdir(parents=True)
    p5._persist_red_cycle_sha(scratchpad, 1, c1_sha)

    ctx = _make_ctx_ambient(scratchpad)
    prev = MagicMock()
    prev.data = {"cycle": 2}

    before_head = _head_sha(repo)
    before_porcelain = _porcelain(repo)
    before_bytes = (repo / "tests" / "test_x.py").read_bytes()
    before_count = _commit_count(repo)

    out = p5._attempt_red_cycle_restore(
        ctx, prev, str(repo), ["tests/test_x.py"], {"groups": []},
    )

    assert out is None, (
        f"expected None (fall through to legacy E_RED_NOT_FAILING) on an "
        f"ambient git_cwd; actual {out!r}"
    )
    skip_events = [e for e in captured if e["type"] == "red_restore_skipped"]
    assert len(skip_events) == 1, (
        "expected exactly 1 'red_restore_skipped' event on the ambient path; "
        f"actual events seen: {[e['type'] for e in captured]!r}"
    )
    assert skip_events[0]["payload"].get("reason") == "ambient_cwd", (
        f"expected reason=='ambient_cwd'; actual "
        f"{skip_events[0]['payload'].get('reason')!r}"
    )
    assert _head_sha(repo) == before_head, (
        "expected HEAD sha unchanged in the ambient repo"
    )
    assert _commit_count(repo) == before_count, (
        f"expected commit count unchanged at {before_count}"
    )
    assert _porcelain(repo) == before_porcelain, (
        f"expected git status --porcelain unchanged (no checkout/stage "
        f"happened); actual {_porcelain(repo)!r} vs before {before_porcelain!r}"
    )
    assert (repo / "tests" / "test_x.py").read_bytes() == before_bytes, (
        "expected the red test file's bytes unchanged -- the guard must "
        "precede the `git checkout <c1_sha> --` at L2337, not merely the commit"
    )


def test_ac12b_attempt_red_cycle_restore_explicit_source_actually_restores(tmp_path, monkeypatch):
    """AC12b (MAJOR-E positive control for B5): with an EXPLICIT source,
    `_attempt_red_cycle_restore` must actually PERFORM the cycle-1 restore
    and return a real `StepResult` -- never `None`. B5's refusal shape
    (`None`) is ALSO its legitimate return at six existing bare-`None` sites
    (L2328/L2335/L2348/L2353/L2367/L2378), so a GREEN returning `None`
    unconditionally (regardless of source) would pass AC12 while leaving the
    GH1034 degeneracy guard permanently dead -- this is the AC that forces
    the guard to actually discriminate on source rather than disable the
    feature. Same fixture shape as AC12 (cycle-1 content committed BEFORE
    the cycle-2 degenerate rewrite, so the checkout genuinely succeeds and
    produces a diff), but with `ctx.org_config["git_cwd"]` explicit and a
    `plan` whose group genuinely fails on re-check (so the function reaches
    its real StepResult return, not an early bare-None). Pre-GREEN: expected
    to PASS already (this is today's real behavior, absent any source
    check)."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    c1_sha = _commit_file(
        repo, "tests/test_x.py", "def test_x():\n    assert False  # cycle 1 RED\n",
        "cycle 1 red test",
    )
    _commit_file(
        repo, "tests/test_x.py",
        "def test_x():\n    assert True  # cycle 2 degenerate rewrite\n",
        "cycle 2 degenerate rewrite",
    )

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    p5._persist_red_cycle_sha(scratchpad, 1, c1_sha)

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="q", session_id="test-gh1220-ac12b", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {"cycle": 2}
    plan = {"groups": [{"kind": "sh", "argv": ["sh", "-c", "exit 1"]}]}

    before_count = _commit_count(repo)

    out = p5._attempt_red_cycle_restore(ctx, prev, str(repo), ["tests/test_x.py"], plan)

    assert isinstance(out, StepResult), (
        f"expected a real StepResult (the restore actually ran) for an "
        f"explicit source; actual {out!r}"
    )
    assert out.status == "ok", f"expected status=='ok'; actual {out.status!r}"
    assert out.data.get("red_commit_sha") is not None, (
        f"expected a real restore commit sha in the result data; actual "
        f"{out.data!r}"
    )
    after_count = _commit_count(repo)
    assert after_count == before_count + 1, (
        f"expected exactly one new restore commit; before={before_count} "
        f"after={after_count}"
    )
    restored_bytes = (repo / "tests" / "test_x.py").read_bytes()
    assert b"assert False" in restored_bytes, (
        "expected the file content to have been restored to the cycle-1 "
        f"version; actual bytes={restored_bytes!r}"
    )


def test_ac13_checkpoint_green_worktree_refuses_relative_worktree_source(tmp_path, monkeypatch):
    """AC13: `_checkpoint_green_worktree` with source
    'cfg_current_worktree_path_relative' -> refuses (outcome=='ambient_cwd',
    detail=='ambient_cwd_refused'), commit count unchanged — #1123's existing
    guard (`git_cwd_source == "cwd"`) now covers the relative-contamination
    labels too. Pre-GREEN FAIL: today's guard only matches the literal
    'cwd' (phase_5_implement.py L3993); this label sails through and commits."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "newfile.txt", "dirty\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    before = _commit_count(repo)

    out = p5._checkpoint_green_worktree(
        str(repo), str(scratchpad), 2, "cycle_cap", "cfg_current_worktree_path_relative",
    )

    assert out.get("outcome") == "ambient_cwd", (
        f"expected outcome=='ambient_cwd' for source "
        f"'cfg_current_worktree_path_relative'; actual {out.get('outcome')!r} "
        f"(detail={out.get('detail')!r})"
    )
    assert out.get("detail") == "ambient_cwd_refused", (
        f"expected detail=='ambient_cwd_refused'; actual {out.get('detail')!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected commit count unchanged at {before}; actual {after}"
    )
    assert "newfile.txt" in _porcelain(repo), (
        "expected newfile.txt to remain uncommitted after the refusal; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


def test_ac14_autocommit_fix_tail_refuses_scratchpad_climb_relative_source(tmp_path, monkeypatch):
    """AC14: `_autocommit_fix_tail` with source 'scratchpad_climb_relative' ->
    refuses (tail_committed=False), commit count unchanged. Pre-GREEN FAIL:
    today's guard only matches the literal 'cwd' (phase_6_review.py L3887);
    this label sails through and commits the dirty tail."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/tail.py", "def tail(): return 1\n")

    before = _commit_count(repo)

    out = p6._autocommit_fix_tail(
        {}, str(repo), "scratchpad_climb_relative", 1, pre_sha, "commit_fix_code",
    )

    assert isinstance(out, dict), f"expected a dict; actual {type(out)!r}: {out!r}"
    assert out.get("tail_committed") is False, (
        f"expected tail_committed=False for source 'scratchpad_climb_relative'; "
        f"actual {out!r}"
    )
    after = _commit_count(repo)
    assert after == before, (
        f"expected commit count unchanged at {before}; actual {after}"
    )
    assert "src/tail.py" in _porcelain(repo), (
        "expected src/tail.py to remain uncommitted after the refusal; "
        f"actual porcelain={_porcelain(repo)!r}"
    )


def test_ac14b_autocommit_fix_tail_skip_reason_pinned_to_cwd_default(tmp_path, monkeypatch):
    """AC14b (MINOR-2, §1o): the SAME ambient refusal above must emit
    `fix_tail_skipped` with reason EXACTLY `"cwd_default"` -- the existing
    literal (phase_6_review.py L3888) is pinned so GREEN cannot silently
    rename a field a consumer dispatches on while widening the guard to
    `is_ambient_git_cwd(...)`. Pre-GREEN: this passes for the literal 'cwd'
    source already; it is pinned here so it cannot regress when the guard
    condition changes to cover the wider label set."""
    captured = _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/tail2.py", "def tail(): return 2\n")

    out = p6._autocommit_fix_tail(
        {}, str(repo), "scratchpad_climb_relative", 1, pre_sha, "commit_fix_code",
    )

    assert out.get("tail_committed") is False
    skip_events = [e for e in captured if e["type"] == "fix_tail_skipped"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'fix_tail_skipped' event; actual events seen: "
        f"{[e['type'] for e in captured]!r}"
    )
    assert skip_events[0]["payload"].get("reason") == "cwd_default", (
        f"expected reason=='cwd_default' (the existing literal, pinned); "
        f"actual {skip_events[0]['payload'].get('reason')!r}"
    )


def test_ac15_e_git_cwd_ambient_registered_in_error_codes():
    """AC15: E_GIT_CWD_AMBIENT is present in error_codes.py's registry AND in
    ERROR_CODES.md (two-direction, §1n). Pre-GREEN FAIL: neither exists yet."""
    import error_codes  # noqa: PLC0415

    assert "E_GIT_CWD_AMBIENT" in error_codes.ERROR_CODES, (
        f"expected 'E_GIT_CWD_AMBIENT' registered in error_codes.ERROR_CODES; "
        f"actual it is absent (registry has {len(error_codes.ERROR_CODES)} codes)"
    )

    engine_root = Path(__file__).resolve().parents[1]
    md_text = (engine_root / "ERROR_CODES.md").read_text(encoding="utf-8")
    assert "E_GIT_CWD_AMBIENT" in md_text, (
        "expected 'E_GIT_CWD_AMBIENT' documented in ERROR_CODES.md; actual: absent"
    )


def test_ac26_compute_baseline_failed_ambient_skips_stash(tmp_path, monkeypatch):
    """AC26 (MAJOR-1): `_compute_baseline_failed` with an ambient source ->
    returns `None`, emits `baseline_skipped_ambient_cwd`, `git stash list` is
    EMPTY, and the working tree (porcelain + untracked file BYTES) is
    unchanged. The untracked-file assertion is the point: `-u` destroying
    untracked files is worse than the stray commit this lot started from.
    Pre-GREEN FAIL: no source parameter or guard exists on this helper at
    all (`_compute_baseline_failed(plan, git_cwd)` takes only two params
    today) -- the call below raises TypeError."""
    captured = _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    untracked = _write_file(repo, "scratch_untracked_ac26.txt", "precious untracked data\n")
    monkeypatch.chdir(repo)

    plan = {"groups": []}

    result = p5._compute_baseline_failed(plan, str(repo), "cwd")

    assert result is None, f"expected None on an ambient source; actual {result!r}"
    skip_events = [e for e in captured if e["type"] == "baseline_skipped_ambient_cwd"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'baseline_skipped_ambient_cwd' event; actual "
        f"events seen: {[e['type'] for e in captured]!r}"
    )
    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` to be EMPTY (no stash ever pushed); "
        f"actual {stash_list!r}"
    )
    assert untracked.read_text() == "precious untracked data\n", (
        "expected the untracked file's bytes unchanged -- `git stash push -u` "
        "must never run against the ambient repo"
    )
    assert "scratch_untracked_ac26.txt" in _porcelain(repo), (
        "expected the untracked file to still show as untracked in porcelain"
    )


def test_ac26b_verify_green_passing_ambient_never_stashes_real_caller(tmp_path, monkeypatch):
    """AC26b (MAJOR-F): drives the REAL caller `_verify_green_passing` (not
    just the `_compute_baseline_failed` helper directly) with an ambient
    `git_cwd` -- forces a real test failure so control reaches the L4298
    baseline-delta call site -- asserts `git stash list` empty AND untracked
    file bytes intact. `_verify_green_passing` ALREADY binds the source
    label at L4114 ('source bound, unused' per spec §0); a GREEN that adds
    the parameter to the helper but forwards a hardcoded/wrong value at this
    real call site would pass AC26 while `git stash push -u` still runs
    against the ambient live repo -- the original incident, untouched.

    REACH-PROOF (mandatory, not decorative): `prev` MUST be a real
    `StepResult` -- `_verify_green_passing`'s very first line is `if not
    isinstance(prev, StepResult) ...: return StepResult(error_code=
    'E_MISSING_PREV_DATA')`, so a `MagicMock()` (not a `StepResult`
    instance) makes this test pass VACUOUSLY, exiting on line 1 without ever
    reaching L4298 -- exactly the bug an orchestrator audit caught in an
    earlier revision of this test (instrumenting the baseline helpers showed
    zero calls). The assertion on `verify_green_delta_verdict` proves L4298
    executed.

    DISCRIMINATING ASSERTIONS (Amendment 9 follow-up): `_compute_baseline_failed`
    pops its stash in a `finally`, so 'git stash list is empty afterwards' is
    satisfied EQUALLY by 'never stashed' (guard fired) and by 'stashed, ran,
    popped' (guard absent) -- the reach-proof above proves L4298 ran, but not
    that the AMBIENT GUARD inside the helper fired. Two independent
    discriminators close that: (1) `baseline_skipped_ambient_cwd` must be
    among the captured events -- the literal event only the guard emits; (2)
    `run_test_command` must be called EXACTLY ONCE -- the outer test loop's
    own call. Today's unguarded helper calls it a SECOND time internally (to
    re-run the same `plan['groups']` against the stashed-clean tree), so an
    unguarded/broken GREEN yields 2 calls; a correctly-guarded one returns
    before ever calling it, yielding 1.

    Only `run_test_command` (the disk_truth test-runner collaborator, same
    seam test_gh1123 uses) is stubbed -- never the UUT."""
    captured = _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    untracked = _write_file(repo, "scratch_untracked_ac26b.txt", "precious untracked data 4\n")
    monkeypatch.chdir(repo)

    stdout_file = tmp_path / "stdout.txt"
    stdout_file.write_text(
        "FAILED tests/test_example.py::test_something - AssertionError\n1 failed in 0.10s\n",
        encoding="utf-8",
    )
    stderr_file = tmp_path / "stderr.txt"
    stderr_file.write_text("", encoding="utf-8")

    from lib.plugins.disk_truth.test_runner import TestRunResult  # noqa: PLC0415

    fake_result = TestRunResult(
        exit_code=1, n_passed=0, n_failed=1,
        stdout_path=str(stdout_file), stderr_path=str(stderr_file),
    )
    run_test_command_calls: list[tuple] = []

    def _fake_run_test_command(*a, **kw):
        run_test_command_calls.append((a, kw))
        return fake_result

    monkeypatch.setattr(p5, "run_test_command", _fake_run_test_command)

    no_git_scratch = tmp_path / "no_git_ancestor_ac26b" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch, verify_green_passing=True)
    # A REAL StepResult -- MagicMock fails isinstance(prev, StepResult) and
    # exits on line 1 of _verify_green_passing (see docstring above).
    prev = StepResult(
        status="ok",
        data={
            "green_log_path": "tests/build-green-output.log",
            "spec_path": "scratchpad/spec.md",
            "red_log_path": "tests/build-red-output.log",
            "validation_doc_path": "scratchpad/validation.md",
            "green_status": "PASS",
            "red_test_paths": ["tests/test_example.py"],
            "cycle_count": 2,
        },
        duration_ms=0,
        step_name="write_green_artifact",
    )

    result = p5._verify_green_passing(ctx, prev)

    # ── reach-proof: control genuinely executed L4298, not an early return ──
    assert result.error_code != "E_MISSING_PREV_DATA", (
        f"fixture bug: exited on the isinstance(prev, StepResult) guard; "
        f"actual result={result!r}"
    )
    delta_events = [e for e in captured if e["type"] == "verify_green_delta_verdict"]
    assert len(delta_events) == 1, (
        f"REACH-PROOF FAILED: expected exactly 1 'verify_green_delta_verdict' "
        f"event, proving control reached the L4298 baseline-delta call site "
        f"(not merely that nothing stashed because nothing ran); actual "
        f"events seen: {[e['type'] for e in captured]!r} (result={result!r})"
    )

    # ── discriminator 1: the guard's own event, not just 'nothing stashed' ──
    skip_events = [e for e in captured if e["type"] == "baseline_skipped_ambient_cwd"]
    assert len(skip_events) == 1, (
        f"DISCRIMINATOR FAILED: expected exactly 1 'baseline_skipped_ambient_cwd' "
        f"event -- the empty `git stash list` alone is satisfied equally by "
        f"'never stashed' (guard fired) and by 'stashed, ran, popped' (guard "
        f"absent, since _compute_baseline_failed pops in its finally); actual "
        f"events seen: {[e['type'] for e in captured]!r}"
    )

    # ── discriminator 2 DROPPED (fixture repair, GH1220): the original intent
    #    was "unguarded code calls run_test_command a SECOND time inside the
    #    helper's re-run", assuming `_infer_test_command_for_paths` yields
    #    exactly one plan group. It evidently yields more than one group for
    #    this fixture's red_test_paths, so the OUTER test loop alone already
    #    contributes several calls (observed 6) regardless of guard state --
    #    the raw count is an artifact of `_infer_test_command_for_paths`'
    #    internals, not a contract this suite owns, and pinning it here would
    #    be a future false-failure on any planner change. The guard's own
    #    event (discriminator 1, above) and the delta-verdict payload
    #    (discriminator 3, below) already carry the semantically meaningful
    #    proof that the guard fired vs. did not; run_test_command_calls is
    #    still captured (unused past this point) in case a future fixture
    #    wants to re-derive a precise expected count empirically.

    # ── discriminator 3 (semantic, gate pass 3): baseline_failed=None (guarded)
    #    vs an int (unguarded, since run_test_command is stubbed to always
    #    return n_failed=1 regardless of stash state) flow through
    #    delta_verdict() into DIFFERENT classifications -- already captured
    #    in this same event's payload, no extra collaborator needed.
    delta_payload = delta_events[0]["payload"]
    assert delta_payload.get("baseline_failed") is None, (
        f"DISCRIMINATOR FAILED: expected baseline_failed=None (the guard's "
        f"'baseline unavailable' contract) in the verify_green_delta_verdict "
        f"payload; unguarded code would report an int (the stub always "
        f"returns n_failed=1); actual payload={delta_payload!r}"
    )
    assert delta_payload.get("classification") == "baseline_unavailable", (
        f"DISCRIMINATOR FAILED: expected classification=='baseline_unavailable'; "
        f"actual {delta_payload.get('classification')!r} (full payload "
        f"{delta_payload!r})"
    )

    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` EMPTY -- the real `_verify_green_passing` "
        f"caller must actually CONSULT the source label it already binds at "
        f"L4114, not just declare the new helper parameter; actual "
        f"{stash_list!r}"
    )
    assert untracked.read_text() == "precious untracked data 4\n", (
        "expected the untracked file's bytes unchanged"
    )


def test_ac27_compute_baseline_typecheck_count_ambient_skips_stash(tmp_path, monkeypatch):
    """AC27: same shape as AC26 for `_compute_baseline_typecheck_count`.
    Pre-GREEN FAIL: no source parameter or guard exists (two-arg signature
    today) -- TypeError."""
    captured = _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    untracked = _write_file(repo, "scratch_untracked_ac27.txt", "precious untracked data 2\n")
    monkeypatch.chdir(repo)

    result = p5._compute_baseline_typecheck_count([], str(repo), "cwd")

    assert result is None, f"expected None on an ambient source; actual {result!r}"
    skip_events = [e for e in captured if e["type"] == "baseline_skipped_ambient_cwd"]
    assert len(skip_events) == 1, (
        f"expected exactly 1 'baseline_skipped_ambient_cwd' event; actual "
        f"events seen: {[e['type'] for e in captured]!r}"
    )
    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` to be EMPTY; actual {stash_list!r}"
    )
    assert untracked.read_text() == "precious untracked data 2\n", (
        "expected the untracked file's bytes unchanged"
    )


def test_ac27b_compute_baseline_typecheck_count_explicit_source_still_stashes_and_pops(
    tmp_path, monkeypatch,
):
    """AC27b (MAJOR-E positive control for B9): `_compute_baseline_typecheck_count`
    with an EXPLICIT source still stashes, computes and pops -- returns an
    `int`, and `git stash list` is empty afterwards. Mirrors AC30's positive
    control for `_compute_baseline_failed`; without this, a GREEN could
    thread the source into B8 alone and leave B9 permanently disabled."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "dirty_ac27b.txt", "dirty\n")

    result = p5._compute_baseline_typecheck_count([], str(repo), "cfg_git_cwd")

    assert isinstance(result, int), (
        f"expected an int for an explicit source; actual {result!r}"
    )
    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` EMPTY afterwards; actual {stash_list!r}"
    )
    assert "dirty_ac27b.txt" in _porcelain(repo), (
        "expected the dirty file to be restored (popped) into the working "
        f"tree; actual porcelain={_porcelain(repo)!r}"
    )


def test_ac27c_verify_green_typecheck_ambient_never_stashes_real_caller(tmp_path, monkeypatch):
    """AC27c (MAJOR-F): drives the REAL caller `_verify_green_typecheck`
    (not just `_compute_baseline_typecheck_count` directly) with an ambient
    `git_cwd` -- asserts `git stash list` empty AND untracked file bytes
    intact. `_verify_green_typecheck` (L5106) resolves via the SOURCE-
    DISCARDING `_resolve_git_cwd(ctx, prev)` today; A3.1 requires it switch
    to `resolve_git_cwd_with_source` and thread the source into B9. Only
    `bounded_run` (the mypy collaborator) is stubbed to report one fake
    finding -- forcing progression past the 'no findings' early-return so
    control actually reaches the baseline-delta call site at L5196.

    REACH-PROOF (mandatory, not decorative): `prev` MUST be a real
    `StepResult` -- `_verify_green_typecheck`'s very first line is `if not
    isinstance(prev, StepResult) ...: return StepResult(error_code=
    'E_MISSING_PREV_DATA')`; a `MagicMock()` there makes the whole test pass
    vacuously (never calls the mypy stub, never reaches L5196 at all). The
    `verify_green_typecheck_delta_verdict` event assertion below is the
    positive proof control actually got there.

    DISCRIMINATING ASSERTION (Amendment 9 follow-up): `_compute_baseline_typecheck_count`
    pops its stash in a `finally` too, so an empty `git stash list` alone is
    satisfied equally by 'never stashed' and by 'stashed, ran, popped' --
    `baseline_skipped_ambient_cwd` (the literal event only the guard emits)
    is the discriminator. A `run_test_command`/`bounded_run` call-count
    discriminator (used for AC26b) does NOT work here: `src/typed.py` is
    UNTRACKED, so `git stash push -u` removes it from disk; the helper's own
    `existing_paths = [p for p in resolved_paths if Path(p).exists()]` filter
    then finds nothing and returns 0 WITHOUT calling `bounded_run` a second
    time regardless of whether the guard fired -- the call count is 1 either
    way, so it cannot distinguish the two cases for this helper."""
    captured = _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED")
    _write_file(repo, "src/typed.py", "def f(x): return x\n")
    untracked = _write_file(repo, "scratch_untracked_ac27c.txt", "precious untracked data 3\n")
    monkeypatch.chdir(repo)

    monkeypatch.setattr(
        p5, "bounded_run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="src/typed.py:1: error: fake finding  [fake-code]\n", stderr="",
        ),
    )

    no_git_scratch = tmp_path / "no_git_ancestor_ac27c" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    # A REAL StepResult -- MagicMock fails isinstance(prev, StepResult) and
    # exits on line 1 of _verify_green_typecheck (see docstring above).
    prev = StepResult(
        status="ok", data={"red_commit_sha": red_sha}, duration_ms=0,
        step_name="verify_green_passing",
    )

    result = p5._verify_green_typecheck(ctx, prev)

    # ── reach-proof: control genuinely executed L5196, not an early return ──
    assert result.error_code != "E_MISSING_PREV_DATA", (
        f"fixture bug: exited on the isinstance(prev, StepResult) guard; "
        f"actual result={result!r}"
    )
    delta_events = [e for e in captured if e["type"] == "verify_green_typecheck_delta_verdict"]
    assert len(delta_events) == 1, (
        f"REACH-PROOF FAILED: expected exactly 1 "
        f"'verify_green_typecheck_delta_verdict' event, proving control "
        f"reached the L5196 baseline-delta call site; actual events seen: "
        f"{[e['type'] for e in captured]!r} (result={result!r})"
    )

    # ── discriminator 1: the guard's own event, not just 'nothing stashed' ──
    skip_events = [e for e in captured if e["type"] == "baseline_skipped_ambient_cwd"]
    assert len(skip_events) == 1, (
        f"DISCRIMINATOR FAILED: expected exactly 1 'baseline_skipped_ambient_cwd' "
        f"event -- the empty `git stash list` alone is satisfied equally by "
        f"'never stashed' (guard fired) and by 'stashed, ran, popped' (guard "
        f"absent); actual events seen: {[e['type'] for e in captured]!r}"
    )

    # ── discriminator 2 (semantic, gate pass 3): baseline_failed=None
    #    (guarded) vs 0 (unguarded -- `src/typed.py` is untracked, so `git
    #    stash push -u` removes it and `existing_paths` filters to empty,
    #    returning 0 rather than running mypy again) flow through
    #    delta_verdict() into DIFFERENT classifications, already captured in
    #    this same event's payload -- no extra collaborator needed. This is
    #    the discriminator the call-count approach could not be (see the
    #    docstring above): the RETURN VALUE differs even though the CALL
    #    COUNT does not.
    delta_payload = delta_events[0]["payload"]
    assert delta_payload.get("baseline_failed") is None, (
        f"DISCRIMINATOR FAILED: expected baseline_failed=None (the guard's "
        f"'baseline unavailable' contract) in the "
        f"verify_green_typecheck_delta_verdict payload; unguarded code "
        f"would report 0 (existing_paths empty after the untracked file is "
        f"stashed away); actual payload={delta_payload!r}"
    )
    assert delta_payload.get("classification") == "baseline_unavailable", (
        f"DISCRIMINATOR FAILED: expected classification=='baseline_unavailable'; "
        f"actual {delta_payload.get('classification')!r} (full payload "
        f"{delta_payload!r})"
    )

    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` EMPTY -- the real `_verify_green_typecheck` "
        f"caller must thread the ambient source into "
        f"`_compute_baseline_typecheck_count`, not just the standalone helper "
        f"tested by AC27; actual {stash_list!r}"
    )
    assert untracked.read_text() == "precious untracked data 3\n", (
        "expected the untracked file's bytes unchanged"
    )


def _stub_bounded_run_success(*a, **kw):
    """Collaborator stub for the mypy subprocess invocation inside
    `_verify_fix_typecheck` -- NOT the UUT (§1l). Lets AC28/AC28b run
    unconditionally without depending on mypy/uvx actually being on PATH
    (MINOR fix: a `skipif` here would silently drop coverage and perturb the
    §1r skip count)."""
    return subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="Success: no issues found in 1 source file\n", stderr="",
    )


def test_ac28_verify_fix_typecheck_ambient_skips_worktree(tmp_path, monkeypatch):
    """AC28 (MINOR-4): `_verify_fix_typecheck` with an ambient source ->
    skips the worktree-based baseline typecheck, emits
    `fix_typecheck_skipped_ambient_cwd`, and `git worktree list` of the repo
    is UNCHANGED (no stale registration left behind by `git worktree add
    --detach` at L4834). Only the `bounded_run` mypy-subprocess collaborator
    is stubbed (never the UUT) so this AC's ambient-refusal half runs
    UNCONDITIONALLY, regardless of whether mypy/uvx is on PATH -- a `skipif`
    here would silently drop the MINOR-4 coverage and perturb the §1r skip
    count. Pre-GREEN FAIL: `resolve_git_cwd(cfg)` (no source) is used today
    (L4737); the ambient source is never distinguished and the worktree
    add/remove proceeds unconditionally."""
    captured = _record_events(monkeypatch, p6)
    monkeypatch.setattr(p6, "bounded_run", _stub_bounded_run_success)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/typed.py", "def f(x: int) -> int:\n    return x\n")
    monkeypatch.chdir(repo)

    no_git_scratch = tmp_path / "no_git_ancestor_ac28" / "scratch"
    no_git_scratch.mkdir(parents=True)
    ctx = _make_ctx_ambient(no_git_scratch)
    prev = MagicMock()
    prev.data = {"pre_fix_sha": pre_sha}

    before_wt = subprocess.run(
        ["git", "worktree", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout

    p6._verify_fix_typecheck(ctx, prev)

    events = [e for e in captured if e["type"] == "fix_typecheck_skipped_ambient_cwd"]
    assert len(events) == 1, (
        f"expected exactly 1 'fix_typecheck_skipped_ambient_cwd' event; "
        f"actual events seen: {[e['type'] for e in captured]!r}"
    )
    after_wt = subprocess.run(
        ["git", "worktree", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert after_wt == before_wt, (
        f"expected `git worktree list` unchanged (no stale registration); "
        f"actual before={before_wt!r} after={after_wt!r}"
    )


def test_ac28b_verify_fix_typecheck_explicit_source_actually_runs_worktree_scan(
    tmp_path, monkeypatch,
):
    """AC28b (MAJOR-E positive control for B10): with an EXPLICIT source,
    `_verify_fix_typecheck` must actually REACH and run the worktree-based
    baseline scan (`git worktree add --detach` / `remove --force`) -- not
    just skip cleanly. A GREEN returning the ambient-skip shape
    unconditionally would pass AC28 while silently disabling GH316's
    typecheck delta gate for every real fix cycle. Only the `bounded_run`
    mypy collaborator is stubbed (twice: current + baseline) -- the
    worktree add/remove calls are REAL git ops against a real tmp repo."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/typed.py", "def f(x: int) -> int:\n    return x\n")

    calls = {"n": 0}

    def _fake_bounded_run(*a, **kw):
        calls["n"] += 1
        return _stub_bounded_run_success(*a, **kw)

    monkeypatch.setattr(p6, "bounded_run", _fake_bounded_run)

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"git_cwd": str(repo)},
        question="q", session_id="test-gh1220-ac28b", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {"pre_fix_sha": pre_sha}

    before_wt = subprocess.run(
        ["git", "worktree", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout

    p6._verify_fix_typecheck(ctx, prev)

    assert calls["n"] == 2, (
        f"expected the mypy collaborator invoked exactly twice (current + "
        f"baseline via the worktree scan) for an explicit source; actual "
        f"{calls['n']} call(s) -- fewer means the worktree scan was never "
        f"reached"
    )
    after_wt = subprocess.run(
        ["git", "worktree", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert after_wt == before_wt, (
        "expected the temporary detached worktree to be cleanly removed "
        f"afterward; before={before_wt!r} after={after_wt!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Change C — post-suite live-repo HEAD sentinel (AC16-AC21, AC31)
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _sentinel_state_isolation():
    """C7/AC31 (MAJOR-6): any test below that drives `_live_repo_sentinel`'s
    `pytest_configure` against a tmp repo (or `None`) MUST NOT leak that
    baseline into the REAL end-of-session `pytest_sessionfinish` for the
    conftest-wired live instance -- `monkeypatch` restores the
    `find_repo_root` attribute it patches, but NOT the module-global `_STATE`
    dict that `pytest_configure` already overwrote. Save/restore `_STATE`
    around every test in this file (a cheap no-op for tests that never
    import the sentinel, since the ImportError pre-GREEN is swallowed)."""
    try:
        import _live_repo_sentinel as _sentinel  # noqa: PLC0415
    except ImportError:
        yield
        return
    saved = dict(getattr(_sentinel, "_STATE", {}) or {})
    try:
        yield
    finally:
        _sentinel.reset_state()
        _sentinel._STATE.update(saved)


def test_ac16_cheap_head_read_matches_git_rev_parse_on_live_repo():
    """AC16 (real-data, not a fixture): the sentinel's cheap file-based HEAD
    read on the REAL live repo of THIS worktree (a linked git worktree — its
    `.git` is a FILE, `gitdir: <path>`, and refs live in the commondir, NOT
    the per-worktree gitdir) equals `git rev-parse HEAD` run as a subprocess.
    Pre-GREEN FAIL: ImportError, tests/_live_repo_sentinel.py does not exist
    yet.

    bd#2: real data means a real checkout. Skipped honestly for anyone running
    from an installed wheel, a release tarball or a `git archive` export."""
    live_repo.skip_without_git_checkout()

    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    root = sentinel.find_repo_root()
    assert root is not None, (
        "expected the live worktree root to resolve (climbing from this file "
        "up to the entry named '.git')"
    )
    real_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(root), check=True,
    ).stdout.strip()

    cheap = sentinel.cheap_head_sha(root)

    assert cheap == real_head, (
        f"expected the cheap file-based HEAD read to equal `git rev-parse HEAD` "
        f"({real_head!r}); actual {cheap!r}. This worktree's .git is a FILE "
        f"('gitdir: ...'); the cheap read must follow gitdir -> HEAD -> ref -> "
        f"try gitdir/<ref>, then commondir/<ref>, then scan packed-refs."
    )


def test_ac17_baseline_capture_matches_git_rev_list_count_on_live_repo():
    """AC17 (real-data): baseline capture on the REAL live repo equals
    `git rev-list --count HEAD` from a subprocess. Pre-GREEN FAIL: ImportError,
    module absent.

    bd#2: requires a git checkout — skipped honestly without one."""
    live_repo.skip_without_git_checkout()

    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    root = sentinel.find_repo_root()
    assert root is not None

    real_count = int(subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], capture_output=True, text=True,
        cwd=str(root), check=True,
    ).stdout.strip())

    baseline = sentinel.capture_baseline(root)

    assert isinstance(baseline, dict), f"expected a dict; actual {type(baseline)!r}"
    assert baseline.get("count") == real_count, (
        f"expected baseline count == {real_count} (git rev-list --count HEAD); "
        f"actual {baseline.get('count')!r}"
    )


def test_ac18_simulated_drift_sets_exitstatus_1_and_report_names_culprit_and_commit(
    tmp_path, monkeypatch, capsys,
):
    """AC18: simulated drift (sentinel pointed at a tmp repo via the
    `find_repo_root` collaborator seam — never the live repo; a commit is
    made mid-'session') -> `pytest_sessionfinish` sets `exitstatus = 1` and
    the report text (stderr) contains BOTH the culprit nodeid recorded by
    `pytest_runtest_teardown` (C3) and the new commit's subject. Pre-GREEN
    FAIL: ImportError, module absent."""
    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    monkeypatch.setattr(sentinel, "find_repo_root", lambda *a, **kw: repo)
    monkeypatch.delenv("HAL_LIVE_REPO_SENTINEL", raising=False)

    sentinel.pytest_configure(object())

    class _FakeItem:
        nodeid = "tests/test_evil.py::test_writes_to_live_repo"

    # Simulated culprit: a commit lands in the sentinel's tracked repo mid-run
    # -- exactly the incident class under repair.
    _commit_file(repo, "src/leaked.py", "# leaked by a misbehaving test\n", "leaked commit")

    sentinel.pytest_runtest_teardown(_FakeItem())

    class _FakeSession:
        exitstatus = 0

    fake_session = _FakeSession()
    sentinel.pytest_sessionfinish(fake_session)

    assert fake_session.exitstatus == 1, (
        f"expected drift to set session.exitstatus=1; actual {fake_session.exitstatus!r}"
    )
    err = capsys.readouterr().err
    assert _FakeItem.nodeid in err, (
        f"expected the culprit nodeid {_FakeItem.nodeid!r} in the drift report; "
        f"actual stderr={err!r}"
    )
    assert "leaked commit" in err, (
        f"expected the new commit's subject 'leaked commit' in the drift report; "
        f"actual stderr={err!r}"
    )


def test_ac19_no_drift_leaves_exitstatus_untouched(tmp_path, monkeypatch):
    """AC19: no drift -> `exitstatus` is left UNTOUCHED (a passing session is
    not reddened). Pre-GREEN FAIL: ImportError, module absent."""
    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    monkeypatch.setattr(sentinel, "find_repo_root", lambda *a, **kw: repo)
    monkeypatch.delenv("HAL_LIVE_REPO_SENTINEL", raising=False)

    sentinel.pytest_configure(object())

    class _FakeSession:
        exitstatus = 0

    fake_session = _FakeSession()
    sentinel.pytest_sessionfinish(fake_session)

    assert fake_session.exitstatus == 0, (
        f"expected exitstatus to stay untouched (0) with no drift; actual "
        f"{fake_session.exitstatus!r}"
    )


def test_ac20_kill_switch_disables_sentinel_no_subprocess_even_under_drift(tmp_path, monkeypatch):
    """AC20 (MAJOR-7, rewritten): `HAL_LIVE_REPO_SENTINEL=0` -> no subprocess
    is spawned and `exitstatus` stays untouched even under REAL drift. The
    drift commit is made BEFORE the spawn tracker is installed -- with the
    kill switch on, `pytest_configure` is a no-op, so ordering is free, and
    the tracker then observes ONLY the sentinel's own calls. (v1's version
    installed the tracker first and then made three subprocess.run calls
    through that same tracked attribute via `_commit_file` -- `spawned ==
    []` was unsatisfiable regardless of GREEN.) Pre-GREEN FAIL: ImportError,
    module absent."""
    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    monkeypatch.setattr(sentinel, "find_repo_root", lambda *a, **kw: repo)
    monkeypatch.setenv("HAL_LIVE_REPO_SENTINEL", "0")

    # Real simulated drift BEFORE the tracker is installed.
    _commit_file(repo, "src/leaked2.py", "# leaked\n", "leaked commit 2")

    spawned: list[list[str]] = []
    real_run = subprocess.run

    def _tracking_run(cmd, *a, **kw):
        spawned.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", _tracking_run)

    sentinel.pytest_configure(object())  # no-op under the kill switch

    class _FakeSession:
        exitstatus = 0

    fake_session = _FakeSession()
    sentinel.pytest_sessionfinish(fake_session)

    assert spawned == [], (
        f"expected NO subprocess spawned when HAL_LIVE_REPO_SENTINEL=0; actual "
        f"calls: {spawned!r}"
    )
    assert fake_session.exitstatus == 0, (
        f"expected exitstatus untouched under the kill switch even with real "
        f"drift; actual {fake_session.exitstatus!r}"
    )


def test_ac21_unresolvable_repo_root_disables_fail_open(monkeypatch, capsys):
    """AC21: unresolvable repo root -> exactly one
    'LIVE-REPO-SENTINEL: disabled' stderr line, `exitstatus` untouched (C6
    fail-open on infrastructure, pinned so it cannot silently become
    fail-closed). Pre-GREEN FAIL: ImportError, module absent."""
    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    monkeypatch.setattr(sentinel, "find_repo_root", lambda *a, **kw: None)
    monkeypatch.delenv("HAL_LIVE_REPO_SENTINEL", raising=False)

    sentinel.pytest_configure(object())

    class _FakeSession:
        exitstatus = 0

    fake_session = _FakeSession()
    sentinel.pytest_sessionfinish(fake_session)

    err = capsys.readouterr().err
    disabled_lines = [
        ln for ln in err.splitlines() if ln.startswith("LIVE-REPO-SENTINEL: disabled")
    ]
    assert len(disabled_lines) == 1, (
        f"expected exactly one 'LIVE-REPO-SENTINEL: disabled' stderr line; "
        f"actual stderr={err!r}"
    )
    assert fake_session.exitstatus == 0, (
        f"expected exitstatus untouched (fail-open, C6) when the repo root "
        f"cannot be resolved; actual {fake_session.exitstatus!r}"
    )


def test_ac31_sentinel_state_restored_after_tmp_repo_configure(tmp_path, monkeypatch):
    """AC31 (MAJOR-6): after driving `pytest_configure` against a tmp repo
    (as AC18-AC21 above do), restoring `_STATE` -- exactly as the file-level
    `_sentinel_state_isolation` autouse fixture does -- means a subsequent
    `pytest_sessionfinish` against the REAL live root does not redden the
    session. Pins the state-leak contract of C7 directly (independent of the
    autouse fixture, which this test also exercises implicitly for every
    other test in this file). Pre-GREEN FAIL: ImportError, module absent.

    bd#2: the "REAL live root" half of the contract is unobservable without a
    checkout — skipped honestly without one."""
    live_repo.skip_without_git_checkout()

    import _live_repo_sentinel as sentinel  # noqa: PLC0415

    live_root = sentinel.find_repo_root()  # the REAL live repo, unpatched
    assert live_root is not None

    sentinel.pytest_configure(object())  # captures the REAL baseline
    real_state = dict(sentinel._STATE)

    # Simulate an AC in this file driving the sentinel against a tmp repo.
    tmp_repo, _base_sha = _make_repo_with_base_commit(tmp_path)
    monkeypatch.setattr(sentinel, "find_repo_root", lambda *a, **kw: tmp_repo)
    sentinel.pytest_configure(object())
    assert sentinel._STATE != real_state, (
        "fixture precondition: expected the tmp-repo configure to actually "
        "overwrite _STATE"
    )

    # Restore, exactly as the file-level autouse fixture does.
    sentinel.reset_state()
    sentinel._STATE.update(real_state)

    class _FakeSession:
        exitstatus = 0

    fake_session = _FakeSession()
    sentinel.pytest_sessionfinish(fake_session)

    assert fake_session.exitstatus == 0, (
        f"expected the restored real baseline to compare clean against the "
        f"live repo (no drift); actual exitstatus={fake_session.exitstatus!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Negative control — "fix by total refusal" must not pass
# (AC22-AC25, AC29, AC30)
# ═════════════════════════════════════════════════════════════════════════


def test_ac24_commit_red_tests_explicit_git_cwd_still_commits(tmp_path, monkeypatch):
    """AC24 (MAJOR-4, highest priority): `_commit_red_tests` with an explicit
    `git_cwd` STILL COMMITS -- status=='ok', count +1, the red test path in
    `git show --name-only`. v1 had NO positive control anywhere for the
    primary pipeline path, so a GREEN refusing unconditionally passed the
    whole table with a permanently dead pipeline. Pre-GREEN: expected to
    PASS already (this is today's real behavior); it is pinned here so a
    blanket-refusal GREEN cannot silently ship."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "tests/test_ac24.py", "def test_x():\n    assert True\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="q", session_id="test-gh1220-ac24", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {"cycle": 1}

    before = _commit_count(repo)

    result = p5._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' for an EXPLICIT git_cwd (must still commit); "
        f"actual {result.status!r}: {getattr(result, 'error', '')} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    after = _commit_count(repo)
    assert after == before + 1, (
        f"expected commit count {before + 1}; actual {after}"
    )
    red_sha = (result.data or {}).get("red_commit_sha")
    show = subprocess.run(
        ["git", "show", "--name-only", "--format=", red_sha],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_files = [ln.strip() for ln in show.stdout.splitlines() if ln.strip()]
    assert "tests/test_ac24.py" in committed_files, (
        f"expected 'tests/test_ac24.py' committed; actual {committed_files!r}"
    )


def test_ac25_commit_fix_tests_explicit_source_still_commits(tmp_path, monkeypatch):
    """AC25: `_commit_fix_tests` with an explicit source STILL COMMITS —
    same three assertions as AC24. Pre-GREEN: expected to PASS already."""
    _record_events(monkeypatch, p6)
    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "tests/test_ac25.py", "def test_y():\n    assert True\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="q", session_id="test-gh1220-ac25", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["tests/test_ac25.py"],
        "manifest_source": "harness_tool_record",
    }

    before = _commit_count(repo)

    result = p6._commit_fix_tests(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' for an EXPLICIT source; actual "
        f"{result.status!r}: {getattr(result, 'error', '')} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    after = _commit_count(repo)
    assert after == before + 1, (
        f"expected commit count {before + 1}; actual {after}"
    )
    fix_test_sha = (result.data or {}).get("fix_test_commit_sha")
    show = subprocess.run(
        ["git", "show", "--name-only", "--format=", fix_test_sha],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_files = [ln.strip() for ln in show.stdout.splitlines() if ln.strip()]
    assert "tests/test_ac25.py" in committed_files, (
        f"expected 'tests/test_ac25.py' committed; actual {committed_files!r}"
    )


@pytest.mark.parametrize(
    "source_kind",
    ["cfg_git_cwd", "prev_data", "cfg_current_worktree_path", "scratchpad_climb"],
)
def test_ac22_commit_green_code_still_commits_for_every_explicit_source(
    source_kind, tmp_path, monkeypatch,
):
    """AC22 (negative control): parametrized over all FOUR explicit sources —
    `_commit_green_code` on a real tmp git repo must STILL COMMIT: commit
    count +1, and the new commit contains the expected path. A blanket
    ambient refusal (rather than one that discriminates on source) fails
    ALL FOUR of these parametrizations — this is what proves the guard
    isn't 'refuse everything'."""
    _record_events(monkeypatch, p5)
    # Defense-in-depth (never touch the real live repo, regardless of
    # whichever source_kind resolves): chdir to an inert non-repo dir. Every
    # source below is EXPLICIT and takes precedence over the ambient cwd.
    inert = tmp_path / "inert_cwd"
    inert.mkdir()
    monkeypatch.chdir(inert)

    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED")
    _write_file(repo, "src/module.py", "def foo(): return 42\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    org: dict = {"scratchpad_dir": str(scratchpad)}
    prev_extra: dict = {}
    if source_kind == "cfg_git_cwd":
        org["git_cwd"] = str(repo)
    elif source_kind == "prev_data":
        prev_extra["git_cwd"] = str(repo)
    elif source_kind == "cfg_current_worktree_path":
        org["current_worktree_path"] = str(repo)
    elif source_kind == "scratchpad_climb":
        climb_scratch = repo / "sub" / "scratchpad"
        climb_scratch.mkdir(parents=True)
        org["scratchpad_dir"] = str(climb_scratch)

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="q", session_id="test-gh1220-ac22", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {
        "cycle": 1, "red_commit_sha": red_sha,
        "worker_written_paths": ["src/module.py"],
        "manifest_source": "harness_tool_record",
        **prev_extra,
    }

    before = _commit_count(repo)

    result = p5._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' for EXPLICIT source {source_kind!r} (must still "
        f"commit); actual {result.status!r}: {getattr(result, 'error', '')} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    after = _commit_count(repo)
    assert after == before + 1, (
        f"expected commit count {before + 1} for source {source_kind!r}; "
        f"actual {after}"
    )
    green_sha = (result.data or {}).get("green_commit_sha")
    show = subprocess.run(
        ["git", "show", "--name-only", "--format=", green_sha],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_files = [ln.strip() for ln in show.stdout.splitlines() if ln.strip()]
    assert "src/module.py" in committed_files, (
        f"expected 'src/module.py' committed for source {source_kind!r}; "
        f"actual {committed_files!r}"
    )


@pytest.mark.parametrize(
    "source_kind",
    ["cfg_git_cwd", "cfg_current_worktree_path", "scratchpad_climb"],
)
def test_ac23_commit_fix_code_still_commits_for_every_explicit_source(
    source_kind, tmp_path, monkeypatch,
):
    """AC23 (negative control): parametrized over the THREE explicit sources
    reachable in phase_6 — `_commit_fix_code` still commits, count +1, for
    each. Unlike AC22 (`_commit_green_code`, which resolves via
    `_resolve_git_cwd(ctx, prev)` and therefore threads `prev.data`),
    `_commit_fix_code` calls `resolve_git_cwd_with_source(cfg)` with NO
    prev_data argument (phase_6_review.py L4089) — resolver level 2
    ('prev_data') is structurally unreachable from this call site. Spec
    Amendment 1 confirms this is out of scope for GH1220; 'prev_data' is
    intentionally excluded from this parametrization."""
    _record_events(monkeypatch, p6)
    inert = tmp_path / "inert_cwd"
    inert.mkdir()
    monkeypatch.chdir(inert)

    repo, pre_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "src/fixed.py", "def fixed(): return 2\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    org: dict = {"scratchpad_dir": str(scratchpad)}
    prev_extra: dict = {}
    if source_kind == "cfg_git_cwd":
        org["git_cwd"] = str(repo)
    elif source_kind == "cfg_current_worktree_path":
        org["current_worktree_path"] = str(repo)
    elif source_kind == "scratchpad_climb":
        climb_scratch = repo / "sub2" / "scratchpad"
        climb_scratch.mkdir(parents=True)
        org["scratchpad_dir"] = str(climb_scratch)

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="q", session_id="test-gh1220-ac23", persona="hal",
        framework=None, domain=None,
    )
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_sha, "cycle": 1,
        "worker_written_paths": ["src/fixed.py"],
        "manifest_source": "harness_tool_record",
        **prev_extra,
    }

    before = _commit_count(repo)

    result = p6._commit_fix_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status=='ok' for EXPLICIT source {source_kind!r} (must still "
        f"commit); actual {result.status!r}: {getattr(result, 'error', '')} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    after = _commit_count(repo)
    assert after == before + 1, (
        f"expected commit count {before + 1} for source {source_kind!r}; "
        f"actual {after}"
    )
    fix_sha = (result.data or {}).get("fix_commit_sha")
    show = subprocess.run(
        ["git", "show", "--name-only", "--format=", fix_sha],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_files = [ln.strip() for ln in show.stdout.splitlines() if ln.strip()]
    assert "src/fixed.py" in committed_files, (
        f"expected 'src/fixed.py' committed for source {source_kind!r}; "
        f"actual {committed_files!r}"
    )


def test_ac29_checkpoint_and_autocommit_tail_explicit_source_still_commit(tmp_path, monkeypatch):
    """AC29: explicit-source positive controls -- `_checkpoint_green_worktree`
    with an explicit source still checkpoints (`outcome == 'committed'`), and
    `_autocommit_fix_tail` with an explicit source still commits
    (`tail_committed is True`). Pre-GREEN: expected to PASS already; pinned
    so a blanket-refusal GREEN cannot silently ship."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path, name="repo1")
    _write_file(repo, "newfile_ac29.txt", "dirty\n")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    out = p5._checkpoint_green_worktree(
        str(repo), str(scratchpad), 2, "cycle_cap", "cfg_git_cwd",
    )
    assert out.get("outcome") == "committed", (
        f"expected outcome=='committed' for an explicit source; actual "
        f"{out.get('outcome')!r} (detail={out.get('detail')!r})"
    )

    _record_events(monkeypatch, p6)
    repo2, pre_sha2 = _make_repo_with_base_commit(tmp_path, name="repo2")
    _write_file(repo2, "src/tail_ac29.py", "def tail(): return 2\n")

    out2 = p6._autocommit_fix_tail(
        {}, str(repo2), "cfg_git_cwd", 1, pre_sha2, "commit_fix_code",
    )
    assert out2.get("tail_committed") is True, (
        f"expected tail_committed=True for an explicit source; actual {out2!r}"
    )


def test_ac30_compute_baseline_failed_explicit_source_still_stashes_computes_and_pops(
    tmp_path, monkeypatch,
):
    """AC30: `_compute_baseline_failed` with an EXPLICIT source still
    stashes, computes and pops -- returns an `int`, and `git stash list` is
    empty AFTERWARDS (the D4 invariant survives the guard). Pre-GREEN FAIL:
    no source parameter exists on this helper today (two-arg signature) --
    the three-arg call below raises TypeError."""
    _record_events(monkeypatch, p5)
    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    _write_file(repo, "dirty_ac30.txt", "dirty\n")
    plan = {"groups": []}

    result = p5._compute_baseline_failed(plan, str(repo), "cfg_git_cwd")

    assert isinstance(result, int), (
        f"expected an int (baseline_failed count) for an explicit source; "
        f"actual {result!r}"
    )
    stash_list = subprocess.run(
        ["git", "stash", "list"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout
    assert stash_list.strip() == "", (
        f"expected `git stash list` EMPTY afterwards (D4 invariant: never "
        f"left stashed on exit); actual {stash_list!r}"
    )
    assert "dirty_ac30.txt" in _porcelain(repo), (
        "expected the dirty file to be restored (popped) into the working "
        f"tree; actual porcelain={_porcelain(repo)!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Change D — the anti-rot lint (AC32-AC36)
# ═════════════════════════════════════════════════════════════════════════


def test_ac32_lint_reports_zero_unclassified_sites_on_real_engine_tree():
    """AC32 (real data, not a fixture): the lint run over the REAL engine
    prod tree reports ZERO unclassified mutating-git sites -- every row of
    §0's inventory is in `GUARDED_WRITE_SITES` or
    `DECLARED_NON_GIT_CWD_SITES`. Pre-GREEN FAIL: ImportError,
    lib/mutating_git_lint.py does not exist yet."""
    import mutating_git_lint  # noqa: PLC0415

    engine_root = Path(__file__).resolve().parents[1]
    unclassified = mutating_git_lint.find_unclassified_sites(engine_root)

    assert unclassified == [], (
        f"expected zero unclassified mutating-git sites in the real engine "
        f"prod tree; actual {unclassified!r}"
    )


def test_ac45_guarded_write_sites_covers_all_ten_b1_b10_functions(tmp_path):
    """AC45 (A8.1, highest priority in this batch -- closes the rubber
    stamp): `GUARDED_WRITE_SITES` (the REAL production registry, not a
    fixture) must contain all TEN B1-B10 function names, and
    `DECLARED_NON_GIT_CWD_SITES` must NOT contain any of them. Without this,
    AC32 alone is satisfiable by a GREEN that drops all ten B1-B10 functions
    into `DECLARED_NON_GIT_CWD_SITES` with any reason string -- 'zero
    unclassified sites' would then be a true statement about a registry that
    declares away every real hazard, and the entire SYSTEMATIC justification
    for Change D over a per-site PATCH would be a declaration rather than an
    enforced property. Pre-GREEN FAIL: ImportError, module absent."""
    import mutating_git_lint  # noqa: PLC0415

    b1_through_b10 = {
        "_commit_red_tests",
        "_commit_green_code",
        "_commit_fix_code",
        "_commit_fix_tests",
        "_attempt_red_cycle_restore",
        "_checkpoint_green_worktree",
        "_autocommit_fix_tail",
        "_compute_baseline_failed",
        "_compute_baseline_typecheck_count",
        "_verify_fix_typecheck",
    }

    guarded = set(mutating_git_lint.GUARDED_WRITE_SITES)
    declared_non_git_cwd = set(mutating_git_lint.DECLARED_NON_GIT_CWD_SITES)

    missing = b1_through_b10 - guarded
    assert not missing, (
        f"expected GUARDED_WRITE_SITES to contain all ten B1-B10 functions; "
        f"missing: {missing!r} (actual GUARDED_WRITE_SITES keys: "
        f"{sorted(guarded)!r})"
    )
    smuggled = b1_through_b10 & declared_non_git_cwd
    assert not smuggled, (
        f"expected NONE of the ten B1-B10 functions in "
        f"DECLARED_NON_GIT_CWD_SITES (a GREEN cannot declare away a real "
        f"mutating-git hazard as 'not git_cwd-derived'); actual smuggled "
        f"entries: {smuggled!r}"
    )


def test_ac33_lint_reports_file_line_function_for_unclassified_synthetic_site(tmp_path):
    """AC33 (Amendment 6 + A8.3, three directions): a synthetic module with
    THREE unclassified functions --
      1. a `git commit` -- an actual mutating op -- which the lint MUST
         report with file, line and enclosing function,
      2. a `git worktree list` -- a READ-ONLY (verb, subcommand) pair, per
         Amendment 6's classify-on-the-pair rule -- which the lint MUST NOT
         report, and
      3. (A8.3, gate pass-3 fix) a `Path.unlink()` on a `git_cwd`-derived
         path, in the TWO-STEP shape production actually uses
         (`_full_path = Path(git_cwd) / rel; ...; _full_path.unlink()`,
         mirroring `phase_6_review.py:4257-4266` exactly, not the inline
         `(Path(git_cwd) / rel).unlink()` form) -- the non-git mutation
         shape D1 also covers. A lint handling only the inline form passes
         this AC while missing the actual production shape entirely, and
         AC32 cannot catch that gap on its own (`_commit_fix_code` is
         already classified via its `git add`/`git commit` sites). A lint
         implementing only the `["git", <verb>, ...]` list form passes AC32
         vacuously (finds nothing -> nothing unclassified) while missing
         this shape entirely too.
    A lint that flags everything (verb-only, ignoring the read-only-pair
    allowlist) would pass a two-directional version of this AC while forcing
    every real `worktree list` / `stash list` / `branch --list` call-site in
    the engine into `DECLARED_NON_GIT_CWD_SITES` as noise (AC32 would then
    force ~10 such sites in) -- exactly the failure this asserts against.
    Pre-GREEN FAIL: ImportError, module absent."""
    import mutating_git_lint  # noqa: PLC0415

    synthetic_root = tmp_path / "synthetic_engine_ac33"
    synthetic_root.mkdir()
    module_path = synthetic_root / "rogue_module.py"
    module_path.write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def rogue_write(git_cwd):\n"
        "    import subprocess\n"
        "    subprocess.run(['git', 'commit', '-m', 'rogue'], cwd=git_cwd)\n"
        "\n"
        "\n"
        "def benign_read(git_cwd):\n"
        "    import subprocess\n"
        "    subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=git_cwd)\n"
        "\n"
        "\n"
        "def rogue_unlink(git_cwd, rel):\n"
        "    _full_path = Path(git_cwd) / rel\n"
        "    _full_path.unlink()\n",
        encoding="utf-8",
    )

    unclassified = mutating_git_lint.find_unclassified_sites(synthetic_root)

    unclassified_functions = {s.get("function") for s in unclassified}
    assert unclassified_functions == {"rogue_write", "rogue_unlink"}, (
        f"expected exactly the two mutating sites ('rogue_write' the git "
        f"commit, 'rogue_unlink' the bare Path.unlink()) -- NOT the "
        f"read-only 'git worktree list' in 'benign_read' -- to be reported; "
        f"actual unclassified={unclassified!r}"
    )
    write_site = next(s for s in unclassified if s.get("function") == "rogue_write")
    assert write_site.get("file", "").endswith("rogue_module.py"), (
        f"expected the file name in the report; actual {write_site!r}"
    )
    assert isinstance(write_site.get("line"), int) and write_site["line"] > 0, (
        f"expected a positive int line number in the report; actual {write_site!r}"
    )
    unlink_site = next(s for s in unclassified if s.get("function") == "rogue_unlink")
    assert unlink_site.get("file", "").endswith("rogue_module.py"), (
        f"expected the file name in the report; actual {unlink_site!r}"
    )
    assert isinstance(unlink_site.get("line"), int) and unlink_site["line"] > 0, (
        f"expected a positive int line number in the report; actual {unlink_site!r}"
    )


def test_ac34_removing_guard_from_registered_site_is_detected(tmp_path):
    """AC34: `GUARDED_WRITE_SITES` is NOT a rubber stamp -- a synthetic
    module whose function is DECLARED guarded (passed as an explicit
    registry, the lint's test seam) but whose body contains no
    `is_ambient_git_cwd(`-style marker is still flagged. MAJOR-H note: this
    call relies on `guarded_sites`/`declared_non_git_cwd_sites` accepting any
    ITERABLE-OF-NAMES (a plain `set` here), not literally requiring the
    `dict` type Amendment 3.3 names -- the orchestrator has explicitly
    widened the frozen API to accept either shape for these two keyword
    params. Pre-GREEN FAIL: ImportError, module absent."""
    import mutating_git_lint  # noqa: PLC0415

    synthetic_root = tmp_path / "synthetic_engine_ac34"
    synthetic_root.mkdir()
    module_path = synthetic_root / "half_guarded_module.py"
    module_path.write_text(
        "def half_guarded_write(git_cwd):\n"
        "    import subprocess\n"
        "    # NOTE: no is_ambient_git_cwd check here -- guard was removed\n"
        "    subprocess.run(['git', 'commit', '-m', 'half'], cwd=git_cwd)\n",
        encoding="utf-8",
    )

    violations = mutating_git_lint.find_unclassified_sites(
        synthetic_root,
        guarded_sites={"half_guarded_write"},
        declared_non_git_cwd_sites=set(),
    )

    assert len(violations) == 1, (
        f"expected the registry entry WITHOUT an actual guard marker in its "
        f"body to be flagged, not rubber-stamped; actual {violations!r}"
    )
    assert violations[0].get("function") == "half_guarded_write", (
        f"expected the flagged function to be 'half_guarded_write'; actual "
        f"{violations[0]!r}"
    )


def test_ac35_lint_label_set_matches_resolver_output_both_directions(tmp_path, monkeypatch):
    """AC35: every label in `AMBIENT_GIT_CWD_SOURCES ∪ EXPLICIT_GIT_CWD_SOURCES`
    is exactly the set `resolve_git_cwd_with_source` can actually return --
    derived from the resolver, both directions, so a future sixth level
    cannot land unclassified. Pre-GREEN FAIL: ImportError, the lint module
    (and the wider label sets it derives from) do not exist yet."""
    import mutating_git_lint  # noqa: PLC0415
    from lib.git_cwd import (  # noqa: PLC0415
        AMBIENT_GIT_CWD_SOURCES,
        EXPLICIT_GIT_CWD_SOURCES,
        resolve_git_cwd_with_source,
    )

    declared = mutating_git_lint.resolver_source_labels()
    expected = set(AMBIENT_GIT_CWD_SOURCES) | set(EXPLICIT_GIT_CWD_SOURCES)
    assert declared == expected, (
        f"expected the lint's declared label set to equal AMBIENT ∪ EXPLICIT "
        f"({expected!r}); actual {declared!r}"
    )
    assert len(expected) == 9, (
        f"sanity: expected 9 distinct labels (5 ambient + 4 explicit); "
        f"actual {len(expected)}: {sorted(expected)!r}"
    )

    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.chdir(base)
    observed: set[str] = set()

    _, s = resolve_git_cwd_with_source({})
    observed.add(s)
    _, s = resolve_git_cwd_with_source({"git_cwd": "/abs/x"})
    observed.add(s)
    _, s = resolve_git_cwd_with_source({"git_cwd": "rel/x"})
    observed.add(s)
    _, s = resolve_git_cwd_with_source({}, {"git_cwd": "/abs/y"})
    observed.add(s)
    _, s = resolve_git_cwd_with_source({}, {"git_cwd": "rel/y"})
    observed.add(s)
    abs_wt = tmp_path / "wt"
    abs_wt.mkdir()
    _, s = resolve_git_cwd_with_source({"current_worktree_path": str(abs_wt)})
    observed.add(s)
    _, s = resolve_git_cwd_with_source({"current_worktree_path": "rel/wt"})
    observed.add(s)
    tmp_repo, scratchpad = _build_repo_with_scratchpad(tmp_path)
    _, s = resolve_git_cwd_with_source({"scratchpad_dir": str(scratchpad)})
    observed.add(s)
    rel_scratchpad = os.path.relpath(str(scratchpad), str(base))
    _, s = resolve_git_cwd_with_source({"scratchpad_dir": rel_scratchpad})
    observed.add(s)

    assert observed == expected, (
        f"expected every actually-producible resolver label to be exactly "
        f"the declared set; actual observed={observed!r} vs declared={expected!r}"
    )


def test_ac36_kill_switch_disables_lint(monkeypatch, tmp_path):
    """AC36 (+ A8.4 enforcing-direction): `run_lint` on a tree WITH findings
    returns non-zero (D4: this ships enforcing from day one -- AC36 alone
    only pinned the escape hatch, never that the lint actually enforces
    anything absent it); THEN `HAL_MUTATING_GIT_LINT=0` disables it (escape
    hatch present and effective) -- the SAME synthetic unclassified site no
    longer fails the run. Pre-GREEN FAIL: ImportError, module absent."""
    import mutating_git_lint  # noqa: PLC0415

    synthetic_root = tmp_path / "synthetic_engine_ac36"
    synthetic_root.mkdir()
    (synthetic_root / "rogue_module2.py").write_text(
        "def rogue_write2(git_cwd):\n"
        "    import subprocess\n"
        "    subprocess.run(['git', 'commit', '-m', 'rogue2'], cwd=git_cwd)\n",
        encoding="utf-8",
    )

    # A8.4: enforcing by default -- no env override yet.
    enforcing_result = mutating_git_lint.run_lint(synthetic_root)
    assert enforcing_result != 0, (
        f"expected run_lint to return non-zero on a tree WITH an "
        f"unclassified finding (D4: enforcing from day one, no falsy-default "
        f"flag); actual {enforcing_result!r}"
    )

    monkeypatch.setenv("HAL_MUTATING_GIT_LINT", "0")

    disabled_result = mutating_git_lint.run_lint(synthetic_root)
    assert disabled_result == 0 or disabled_result is None, (
        f"expected the kill switch to make run_lint a no-op (exit 0 / None) "
        f"on the SAME synthetic tree; actual {disabled_result!r}"
    )


def test_ac37_lint_flags_mutating_verb_through_read_port():
    """AC37 (gate §12, real data, not a fixture; A7.8 CLASSIFY-not-re-route):
    the lint flags a mutating git subcommand passed to `git_port.git_read` --
    a mutating op mis-routed through the READ port. Run over the REAL prod
    tree it must currently report EXACTLY ONE site, in `phase_5_implement.py`,
    enclosing function `_attempt_red_cycle_restore` -- the `git checkout
    <c1_sha> --` call, verified by direct read (§1d) to be the literal
    `git_port.git_read(` call site at that function. Asserts file + function
    ONLY, never a literal line number: Change B1's guard lands ABOVE this
    checkout inside `_commit_red_tests` (a DIFFERENT function earlier in the
    same file), shifting every subsequent line number down, and A7.8 freezes
    that this site stays CLASSIFIED (not re-routed to the write port) --
    re-routing is a follow-up OFI, so the site must still be found post-GREEN,
    just possibly at a different line. Pre-GREEN FAIL: ImportError, module
    absent."""
    import mutating_git_lint  # noqa: PLC0415

    engine_root = Path(__file__).resolve().parents[1]
    misuse = mutating_git_lint.find_read_port_misuse(engine_root)

    assert len(misuse) == 1, (
        f"expected exactly 1 mutating-verb-through-read-port site in the "
        f"real engine prod tree today; actual {misuse!r}"
    )
    site = misuse[0]
    assert site.get("file", "").endswith("phase_5_implement.py"), (
        f"expected the site in phase_5_implement.py; actual {site!r}"
    )
    assert site.get("function") == "_attempt_red_cycle_restore", (
        f"expected the enclosing function to be '_attempt_red_cycle_restore' "
        f"(the mis-routed `git checkout` call); actual {site!r}"
    )
    assert isinstance(site.get("line"), int) and site["line"] > 0, (
        f"expected a positive int line number in the report (not asserted "
        f"exactly -- Change B1's guard shifts it); actual {site!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Change E — port-level absolute-cwd backstop + write telemetry (AC38-AC41)
# ═════════════════════════════════════════════════════════════════════════


def test_ac38_git_write_port_refuses_relative_cwd(tmp_path):
    """AC38: `_GitWriteSubprocess.op_capture` and `op_with_lock_retry` refuse
    a RELATIVE `cwd` -- deterministically, no provenance needed -- and the
    refusal names the offending value. Pre-GREEN FAIL: `op_capture`/
    `op_with_lock_retry` (lib/git_write_port.py:36-64) run `bounded_run`
    unconditionally regardless of whether `cwd` is relative or absolute."""
    import git_write_port  # noqa: PLC0415

    relative_cwd = "rel/dir/ac38"

    # Full argv INCLUDING the leading "git" -- op_capture/op_with_lock_retry's
    # convention (bounded_run executes cmd verbatim).
    with pytest.raises(ValueError, match=relative_cwd.replace(".", r"\.")):
        git_write_port.git_op_capture(["git", "status", "--porcelain"], cwd=relative_cwd)

    with pytest.raises(ValueError, match=relative_cwd.replace(".", r"\.")):
        git_write_port.git_op_with_lock_retry(["git", "status", "--porcelain"], cwd=relative_cwd)


def test_ac39_git_read_port_refuses_relative_cwd():
    """AC39: the SAME predicate holds for `git_port.git_read` -- this is
    what closes the mis-routed `checkout` at phase_5_implement.py:2337 (AC37)
    once Change B5 threads an explicit/absolute cwd. Pre-GREEN FAIL:
    `git_read`/`_git_read_subprocess` (lib/git_port.py:63-96) run
    `bounded_run` unconditionally regardless of relative/absolute `cwd`."""
    import git_port  # noqa: PLC0415

    relative_cwd = "rel/dir/ac39"

    with pytest.raises(ValueError, match=relative_cwd.replace(".", r"\.")):
        git_port.git_read(["status", "--porcelain"], cwd=relative_cwd)


def test_ac40_absolute_cwd_including_process_cwd_is_unaffected(tmp_path, monkeypatch):
    """AC40 (the one that matters most): an ABSOLUTE `cwd` is unaffected by
    Change E -- including `str(Path.cwd())`. This pins that E1 is a BACKSTOP,
    not a substitute for Change B: `Path.cwd()` is absolute, so the
    'cwd'-label ambient case sails straight through the port predicate. A
    harmless read-only command is used (never a real mutating op) so this
    test is safe regardless of what the ambient process cwd happens to be.
    Pre-GREEN: expected to PASS already (today nothing refuses an absolute
    cwd); pinned here so a GREEN that over-widens Change E to reject
    'anything that looks ambient' cannot ship -- it must still fail every
    Change B refusal AC for the 'cwd' label specifically."""
    import git_port  # noqa: PLC0415
    import git_write_port  # noqa: PLC0415

    repo, base_sha = _make_repo_with_base_commit(tmp_path)

    # An explicit absolute cwd. `git_write_port.op_capture`/`op_with_lock_retry`
    # take the FULL argv INCLUDING the leading "git" (mirrors
    # phase_5_implement.py:3828's git_write_port.git_op_capture(["git",
    # "stash", "push", "-u", ...]) call shape) -- `bounded_run` executes cmd
    # verbatim, so the binary name must be present. `git_port.git_read`, by
    # contrast, PREPENDS "git" itself (`_git_read_subprocess`: `cmd = ["git"]
    # + args`; every real call site — e.g. phase_5_implement.py:4024's
    # `git_port.git_read(["status", "--porcelain"], ...)` — passes args
    # WITHOUT "git", confirmed by direct read, §1d) so it takes bare args.
    r1 = git_write_port.git_op_capture(["git", "status", "--porcelain"], cwd=str(repo))
    assert r1.returncode == 0, f"expected a normal successful GitResult; actual {r1!r}"

    cm, outcome = git_write_port.git_op_with_lock_retry(
        ["git", "status", "--porcelain"], cwd=str(repo),
    )
    assert outcome == "ok", f"expected outcome=='ok' for an absolute cwd; actual {outcome!r}"

    r2 = git_port.git_read(["status", "--porcelain"], cwd=str(repo))
    assert r2.returncode == 0, f"expected a normal successful GitResult; actual {r2!r}"

    # The exact case the backstop must NOT reject: str(Path.cwd()) -- itself
    # absolute, chdir'd here into the same harmless tmp repo (never the live
    # repo), with only a read-only command run.
    monkeypatch.chdir(repo)
    ambient_abs_cwd = str(Path.cwd())
    r3 = git_port.git_read(["status", "--porcelain"], cwd=ambient_abs_cwd)
    assert r3.returncode == 0, (
        f"expected str(Path.cwd()) (absolute) to be unaffected by the "
        f"relative-cwd backstop; actual {r3!r}"
    )


def test_ac41_git_write_at_cwd_emitted_unconditionally_on_every_port_write(tmp_path, monkeypatch):
    """AC41: `git_write_at_cwd{cmd0, cwd}` is emitted UNCONDITIONALLY on
    every port write (`op_capture` AND `op_with_lock_retry`) -- the
    observability that would have caught this bug class in a day. Only the
    `telemetry_ctx.emit_safe` collaborator seam is patched (§1l) -- never the
    UUT. Pre-GREEN FAIL: neither function emits any telemetry today."""
    import telemetry_ctx  # noqa: PLC0415
    import git_write_port  # noqa: PLC0415

    captured: list[dict] = []
    monkeypatch.setattr(
        telemetry_ctx, "emit_safe",
        lambda et, payload=None, **kw: captured.append({"type": et, "payload": dict(payload or {})}),
    )

    repo, base_sha = _make_repo_with_base_commit(tmp_path)
    cmd = ["git", "status", "--porcelain"]

    git_write_port.git_op_capture(cmd, cwd=str(repo))

    events = [e for e in captured if e["type"] == "git_write_at_cwd"]
    assert len(events) == 1, (
        f"expected exactly 1 'git_write_at_cwd' event from op_capture; "
        f"actual events seen: {[e['type'] for e in captured]!r}"
    )
    assert events[0]["payload"].get("cwd") == str(repo), (
        f"expected payload['cwd']=={str(repo)!r}; actual {events[0]['payload']!r}"
    )
    assert events[0]["payload"].get("cmd0") == "status", (
        f"expected payload['cmd0']=='status' (Amendment 4.2: the git "
        f"SUBCOMMAND VERB, not argv[0] which is always the literal 'git'); "
        f"actual {events[0]['payload'].get('cmd0')!r}"
    )

    captured.clear()
    git_write_port.git_op_with_lock_retry(cmd, cwd=str(repo))

    events2 = [e for e in captured if e["type"] == "git_write_at_cwd"]
    assert len(events2) == 1, (
        f"expected exactly 1 'git_write_at_cwd' event from op_with_lock_retry; "
        f"actual events seen: {[e['type'] for e in captured]!r}"
    )
    assert events2[0]["payload"].get("cwd") == str(repo)
    assert events2[0]["payload"].get("cmd0") == "status", (
        f"expected payload['cmd0']=='status' (the git subcommand verb); "
        f"actual {events2[0]['payload'].get('cmd0')!r}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Amendment 7 new ACs (AC42-AC44) — refusal shape never-raise contract,
# cwd=None domain, and the escaped-ValueError termination shape.
# ═════════════════════════════════════════════════════════════════════════


def test_ac42_git_write_telemetry_never_raises_even_if_emit_safe_fails(tmp_path, monkeypatch):
    """AC42 (Amendment 5.3, MAJOR-J): the write-port's AC41 telemetry has a
    LOAD-BEARING never-raise contract -- if the telemetry sink itself is
    unhealthy (here: `telemetry_ctx.emit_safe` raises), `op_capture` must
    still return a normal `GitResult`, never propagate. An emit that can
    raise inside `op_capture` would convert an observability nicety into an
    outage for every git write in the engine; `emit_git_write_at_cwd` MUST
    copy `emit_resolver_resolved`'s blanket `except Exception: pass` idiom
    exactly (A5.3). Only the `telemetry_ctx.emit_safe` collaborator seam is
    patched -- never the UUT. Pre-GREEN FAIL: no telemetry exists yet at all
    (AC41), so `op_capture` never calls the raising stub and this AC is
    vacuously satisfied for the wrong reason -- it becomes a REAL guard only
    once AC41 lands, and is written now so GREEN cannot ship AC41 without
    also shipping this."""
    import telemetry_ctx  # noqa: PLC0415
    import git_write_port  # noqa: PLC0415

    def _raise(*a, **kw):
        raise RuntimeError("telemetry sink is down")

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _raise)

    repo, base_sha = _make_repo_with_base_commit(tmp_path)

    result = git_write_port.git_op_capture(["git", "status", "--porcelain"], cwd=str(repo))

    assert result.returncode == 0, (
        f"expected a normal successful GitResult even though the telemetry "
        f"sink raises; actual {result!r}"
    )


def test_ac43_cwd_none_allowed_at_all_three_entry_points(tmp_path):
    """AC43 (A7.1, MAJOR-G): `cwd=None` is IN the refusal's domain and must
    be ALLOWED -- `git_port.git_read` declares `cwd: str | None = None` and
    `audit_gate.py:166`/`:178` call it with NO `cwd` at all (a deliberate,
    documented read-path affordance: 'allow commit-msg hook reads the index
    of whatever repo invoked it'). The frozen predicate is `cwd is not None
    and not os.path.isabs(cwd)` -> raise; only a genuinely relative STRING
    raises, `None` means 'inherit the process cwd'. Pre-GREEN: expected to
    PASS already (nothing refuses anything yet, and Python's subprocess
    already treats cwd=None as inherit); pinned so a naive `if not
    os.path.isabs(cwd): raise` (which throws TypeError on None) cannot ship
    and brick the commit-msg audit gate (gate 0a)."""
    import git_port  # noqa: PLC0415
    import git_write_port  # noqa: PLC0415

    r1 = git_port.git_read(["rev-parse", "--is-inside-work-tree"], cwd=None)
    assert isinstance(r1.returncode, int), (
        f"expected git_read(cwd=None) to run normally (inherits process "
        f"cwd), not raise; actual {r1!r}"
    )

    r2 = git_write_port.git_op_capture(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=None,
    )
    assert isinstance(r2.returncode, int), (
        f"expected op_capture(cwd=None) to run normally, not raise; actual {r2!r}"
    )

    cm, outcome = git_write_port.git_op_with_lock_retry(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=None,
    )
    assert outcome in ("ok", "non_lock_error"), (
        f"expected op_with_lock_retry(cwd=None) to run normally (never raise "
        f"for None); actual outcome={outcome!r}"
    )


def test_ac44_escaped_change_e_valueerror_propagates_uncaught_from_workflow_engine(tmp_path):
    """AC44 (A7.2, MAJOR-I): an escaped Change-E `ValueError` (e.g. from a
    step whose collaborator hits the relative-cwd backstop and does not
    catch it) propagates OUT of `WorkflowEngine.execute` uncaught --
    `engine.py`'s `step.execute(context, prev)` call sits in a
    `try:`/`finally:` with NO `except` (L420-424), so the raise is never
    converted into a StepResult inside the engine; only `run.py`'s own
    `except ValueError` (never invoked here -- no `run.py` per §6) converts
    it to `error_code='E_BAD_CTX'`. This is acceptable ONLY because Change E
    is a backstop for a condition Change B already refuses earlier -- pinned
    directly against the engine's real step-execution wrapper so a future
    refactor cannot silently start swallowing it. Pre-GREEN: expected to
    PASS already -- this is today's real `engine.py` behavior, independent
    of GH1220; pinned here so a GREEN touching `engine.py` incidentally
    cannot regress it."""
    from contracts import StepContract, WorkflowDefinition  # noqa: PLC0415
    from engine import WorkflowEngine  # noqa: PLC0415

    def _raising_step(ctx, prev):
        raise ValueError("git_write_port: refusing a relative cwd: 'rel/dir'")

    step = StepContract(name="fake_step", execute=_raising_step)
    workflow = WorkflowDefinition(name="gh1220-ac44-workflow", steps=[step])

    workflow_engine = WorkflowEngine()
    workflow_engine.register("gh1220-ac44-workflow", workflow)

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={},
        question="q", session_id="test-gh1220-ac44", persona="hal",
        framework=None, domain=None,
    )

    with pytest.raises(ValueError, match="refusing a relative cwd"):
        workflow_engine.execute("gh1220-ac44-workflow", ctx)
