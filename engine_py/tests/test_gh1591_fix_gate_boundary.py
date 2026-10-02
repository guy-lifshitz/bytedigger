"""RED tests for #1591 — the phase-6 fix-side typecheck gate is inert in production.

Spec: scratch-1591/spec.md

C1 (chokepoint _commit_fix_code / _verify_fix_typecheck): construction B
(round-3 gate). `_commit_fix_code`'s read of `prev.data["pre_fix_sha"]` is
UNCHANGED (same precedence, same fallback, no new rev-parse). It publishes
the boundary it actually used under a NEW key the producer never reads:
`"fix_boundary_sha"`. Consumers prefer fix_boundary_sha > pre_fix_sha >
resolve_pre_phase_sha. Staleness is structurally impossible: the producer
never reads its own output and re-publishes every cycle before any consumer
runs. (The round-2 "honour inherited pre_fix_sha only if it equals HEAD,
else re-resolve" rule was tried and REJECTED — it produced 5 net-new
full-suite failures, including silencing a live `fix_surface_violation`
guard. Superseded; no `fix_boundary_reresolved` event exists.)

`_verify_fix_typecheck` — a boundary that equals HEAD while a fix commit
landed is REFUSED (recover to <fix_commit_sha>~1, else fail loud) instead of
silently degrading to a no_python_scope skip.

C2 (chokepoint _persist_fix_feed): when the reviewer suspect rate exceeds its
own threshold, suspect findings must be WITHHELD from the fix worker's feed.

Single module object (bd#44): phase_6_review exists only as
`bytedigger_engine.workflows.phase_6_review`. This file imports and patches
through it (bound to the local name `p6`) and asserts that identity
explicitly below.

AC4, AC10, and AC12's pre-existing-behaviour clauses (verbatim honouring +
single rev-parse) describe behaviour that already holds today — they must
PASS today. Every other test (including AC12's new fix_boundary_sha
assertion) targets behaviour that does not exist in production yet and must
FAIL today.
"""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── conftest-singleton path seam (§1q / 81F97F3D) ─────────────────────────────
# conftest.py inserts engine_py root + workflows at import time; we rely on that.
# Do NOT re-insert sys.path here.

from bytedigger_engine.workflows import phase_6_review as p6  # noqa: E402  — the module the engine executes
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.lib.git_port import (  # noqa: E402
    GitResult,
    set_default_git_read_factory,
    reset_default_git_read_factory,
)

_VALID_SHA = "a" * 40


# ═══════════════════════════════════════════════════════════════════════════
# Module-trap guard (OFI 74b135e9) — explicit, not assumed.
# ═══════════════════════════════════════════════════════════════════════════


def test_module_trap_p6_is_the_module_the_workflow_executes():
    """The workflow's step callables belong to p6 — the module this file
    patches and calls throughout. (Upstream also proves a flat-import twin
    module is distinct; the package layout has no such twin.)
    """
    assert p6 is sys.modules["bytedigger_engine.workflows.phase_6_review"], (
        "p6 must be the sys.modules entry the engine resolves at runtime"
    )
    workflow = p6.phase_6_review_workflow()
    step_execs = {s.name: s.execute for s in workflow.steps}
    assert step_execs["commit_fix_code"] is p6._commit_fix_code, (
        "commit_fix_code step must execute p6._commit_fix_code — the same "
        "object this file patches/calls"
    )
    assert step_execs["verify_fix_typecheck"] is p6._verify_fix_typecheck, (
        "verify_fix_typecheck step must execute p6._verify_fix_typecheck"
    )
    assert step_execs["aggregate_review_findings"] is p6._aggregate_review_findings, (
        "aggregate_review_findings step must execute p6._aggregate_review_findings"
    )


# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════


def _init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@hal.test"],
        check=True, capture_output=True, cwd=str(repo_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "HAL Test"],
        check=True, capture_output=True, cwd=str(repo_path),
    )


def _git_commit(repo_path: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True, cwd=str(repo_path))
    subprocess.run(
        ["git", "commit", "-m", message], check=True, capture_output=True, cwd=str(repo_path),
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True, cwd=str(repo_path),
    )
    return result.stdout.strip()


def _mypy_available() -> bool:
    return shutil.which("mypy") is not None


_GUARD_MODULE_SRC = (
    'GRADE_RANK: dict[str, int] = {"none": 0, "examined": 1, "verified": 2}\n'
    "\n"
    "\n"
    "def process_claim(claim: dict) -> None:\n"
    '    grade = claim.get("grade")\n'
    "    if grade and GRADE_RANK.get(grade, 0) > GRADE_RANK[\"examined\"]:\n"
    "        pass\n"
)
_REGRESSED_MODULE_SRC = (
    'GRADE_RANK: dict[str, int] = {"none": 0, "examined": 1, "verified": 2}\n'
    "\n"
    "\n"
    "def process_claim(claim: dict) -> None:\n"
    '    grade = claim.get("grade")\n'
    "    if GRADE_RANK.get(grade, 0) > GRADE_RANK[\"examined\"]:\n"
    "        pass\n"
)


def _make_regression_repo(tmp_path: Path) -> "tuple[Path, str, str]":
    """git repo with commit1=guarded (mypy-clean), commit2=fix removing the
    guard (mypy-dirty: arg-type on dict.get). Returns (repo, baseline_sha, fix_sha).
    Matches the spec's Live-baseline probe exactly (§1b)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    gate_py = repo / "src" / "gate.py"
    gate_py.parent.mkdir()
    gate_py.write_text(_GUARD_MODULE_SRC)
    baseline_sha = _git_commit(repo, "baseline: typecheck-gated guard")
    gate_py.write_text(_REGRESSED_MODULE_SRC)
    fix_sha = _git_commit(repo, "build: fix cycle 1")
    return repo, baseline_sha, fix_sha


def _make_ctx(git_cwd: Path, **extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"git_cwd": str(git_cwd), **extra},
        question="GH1591 fix gate boundary test",
        session_id="test-gh1591",
        persona="hal", framework=None, domain=None,
    )


def _write_role_file(
    reviews_dir: Path, slug: str, *, blocks: list[tuple[str, str, str]], selfcount: int | None,
) -> None:
    """Copies the convention from test_phase_6_reviewer_suspect_rate_D3492E45.py."""
    reviews_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {slug} Review", ""]
    for severity, title, evidence in blocks:
        lines.append(f"### SEVERITY: {severity} — {title}")
        lines.append(f"> {evidence}")
        lines.append("Confidence: HIGH")
        lines.append("Description: test description")
        lines.append("")
    lines.append("VERDICT: PARTIAL")
    if selfcount is not None:
        lines.append(f"<!-- role-findings-count: {selfcount} -->")
    (reviews_dir / f"role-{slug}.md").write_text("\n".join(lines), encoding="utf-8")


class _SpyGitRead:
    """Recording spy matching GitReadPort.__call__ — mirrors the technique in
    tests/test_D5D6A364_git_read_routing_slice2.py (_SpyGitRead), copied here
    rather than imported so this RED file's lifecycle stays independent of
    that sibling file's.

    Records ALL calls in self.calls. check-ignore calls (from
    _filter_gitignored_paths) are answered specially (nothing ignored) so
    execution reaches the rev-parse Point unobstructed; every other call
    (rev-parse, diff --cached --quiet, etc.) gets the configured self.result.
    """

    def __init__(self, result: GitResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
        self.calls.append((args, cwd, timeout))
        if args[:1] == ["check-ignore"]:
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return self.result


# ═══════════════════════════════════════════════════════════════════════════
# C1 — the boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestC1FixBoundary:
    # ── AC1: production data shape (no pre_fix_sha) misses a real regression ──

    @pytest.mark.skipif(not _mypy_available(), reason="mypy not on PATH — cannot run real typecheck AC1")
    def test_ac1_production_shape_catches_real_regression(self, tmp_path, monkeypatch):
        """§1l real side-effect: real git repo, real mypy, real worktree.
        prev.data carries ONLY {cycle, fix_commit_sha} — the actual production
        shape _commit_fix_code emits today (no pre_fix_sha key). Must still
        return status='error', E_POST_FIX_TYPECHECK_REGRESSION.

        Fails today: resolve_pre_phase_sha(git_cwd) returns HEAD == fix_sha,
        git_diff_files(HEAD) is empty, step skips ok/no_python_scope
        (§1b live baseline in the spec — verified rc=1 on the committed tree).
        """
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
        repo, baseline_sha, fix_sha = _make_regression_repo(tmp_path)

        ctx = _make_ctx(repo)
        prev = StepResult(status="ok", data={"cycle": 1, "fix_commit_sha": fix_sha}, duration_ms=0, step_name="commit_fix_code")

        result = p6._verify_fix_typecheck(ctx, prev)

        assert result.status == "error", (
            f"AC1: production data shape must still catch the regression; got "
            f"status={result.status!r} data={result.data!r} error={result.error!r}"
        )
        assert result.error_code == "E_POST_FIX_TYPECHECK_REGRESSION", (
            f"AC1: expected E_POST_FIX_TYPECHECK_REGRESSION, got {result.error_code!r}"
        )

    # ── AC2: _commit_fix_code publishes fix_boundary_sha ───────────────────────

    def test_ac2_commit_fix_code_publishes_fix_boundary_sha(self, tmp_path, monkeypatch):
        """§1l real side-effect: real git repo, real commit, production prev.data
        shape (no pre_fix_sha at all). The ok-return of _commit_fix_code must
        carry data['fix_boundary_sha'] as a 40-hex SHA, equal to the real
        pre-fix HEAD, and equal to integrity/pre-fix-ref.txt's contents.

        Fails today: _commit_fix_code returns data={**prev.data, "fix_commit_sha": fix_sha}
        — fix_boundary_sha does not exist as a concept yet, so the key is
        never added to the returned dict.
        """
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        mod_py = repo / "src" / "mod.py"
        mod_py.parent.mkdir()
        mod_py.write_text("def f(x: int) -> int:\n    return x\n")
        pre_fix_head_sha = _git_commit(repo, "baseline")

        # Simulate the fix worker editing the file WITHOUT committing.
        mod_py.write_text("def f(x: int) -> int:\n    return x\n\nFOO = 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(repo, scratchpad_dir=str(scratchpad))
        # §1l production shape: prev.data carries NO pre_fix_sha — the engine
        # never supplies it (this is the actual bug). _commit_fix_code must
        # fall back to resolve_pre_phase_sha(git_cwd) internally (HEAD is
        # still the baseline commit — the fix edit above is uncommitted).
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1,
                "worker_written_paths": ["src/mod.py"],
                "manifest_source": "harness_tool_record",
            },
            duration_ms=0, step_name="invoke_fix_llm",
        )

        result = p6._commit_fix_code(ctx, prev)

        assert result.status == "ok", f"AC2: expected ok, got {result.status!r} err={result.error!r}"
        returned_boundary = result.data.get("fix_boundary_sha")
        assert returned_boundary is not None, (
            "AC2: data['fix_boundary_sha'] absent from _commit_fix_code's ok return — "
            "GREEN (C1 item 1/1b) not landed"
        )
        assert isinstance(returned_boundary, str) and len(returned_boundary) == 40, (
            f"AC2: expected a 40-hex SHA, got {returned_boundary!r}"
        )
        assert returned_boundary == pre_fix_head_sha, (
            f"AC2: data['fix_boundary_sha'] must equal the real pre-fix HEAD "
            f"{pre_fix_head_sha!r}; got {returned_boundary!r}"
        )
        integrity_ref = scratchpad / "integrity" / "pre-fix-ref.txt"
        assert integrity_ref.is_file(), f"AC2: {integrity_ref} was not written"
        assert returned_boundary == integrity_ref.read_text().strip(), (
            "AC2: data['fix_boundary_sha'] must be byte-equal to integrity/pre-fix-ref.txt"
        )

    # ── AC3: stale injected pre_fix_sha (resume/retry shape) still recovers ───

    @pytest.mark.skipif(not _mypy_available(), reason="mypy not on PATH — cannot run real typecheck AC3")
    def test_ac3_stale_injected_pre_fix_sha_recovers_via_fix_commit_parent(self, tmp_path, monkeypatch):
        """Resume/retry shape: prev.data['pre_fix_sha'] is explicitly set to the
        STALE post-fix HEAD (== fix_commit_sha). Step must recover the boundary
        to <fix_commit_sha>~1 and still return E_POST_FIX_TYPECHECK_REGRESSION,
        emitting post_fix_typecheck_boundary_degenerate{recovered: True}.

        Fails today: no degenerate-boundary detection exists; the stale
        pre_fix_sha is used as-is, the diff against itself is empty, step
        skips ok/no_python_scope instead of recovering+erroring.
        """
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
        repo, baseline_sha, fix_sha = _make_regression_repo(tmp_path)

        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        ctx = _make_ctx(repo)
        prev = StepResult(
            status="ok",
            data={"cycle": 1, "fix_commit_sha": fix_sha, "pre_fix_sha": fix_sha},
            duration_ms=0, step_name="commit_fix_code",
        )

        result = p6._verify_fix_typecheck(ctx, prev)

        assert result.status == "error", (
            f"AC3: stale boundary must recover and still catch the regression; got "
            f"status={result.status!r} data={result.data!r}"
        )
        assert result.error_code == "E_POST_FIX_TYPECHECK_REGRESSION", (
            f"AC3: expected E_POST_FIX_TYPECHECK_REGRESSION, got {result.error_code!r}"
        )
        degenerate_events = [e for e in captured if e["type"] == "post_fix_typecheck_boundary_degenerate"]
        assert len(degenerate_events) == 1, (
            f"AC3: expected exactly 1 post_fix_typecheck_boundary_degenerate event; "
            f"got {len(degenerate_events)}. All events: {[e['type'] for e in captured]}"
        )
        assert degenerate_events[0]["payload"].get("recovered") is True, (
            f"AC3: expected recovered=True; got {degenerate_events[0]['payload']!r}"
        )

    # ── AC4: no fix commit — boundary==HEAD is legitimate (regression guard) ──

    def test_ac4_no_fix_commit_boundary_equals_head_is_legitimate(self, tmp_path, monkeypatch):
        """No fix_commit_sha in prev.data at all (nothing was fixed):
        boundary==HEAD is the LEGITIMATE case — ok + post_fix_typecheck_skipped
        {reason: no_python_scope}, and NEVER E_POST_FIX_TYPECHECK_NO_BOUNDARY.

        Must PASS TODAY — this is the pre-existing no-op behaviour C1 item 3
        explicitly preserves.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "README.md").write_text("hello\n")
        _git_commit(repo, "only commit")

        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        ctx = _make_ctx(repo)
        prev = StepResult(status="ok", data={"cycle": 1}, duration_ms=0, step_name="commit_fix_code")

        result = p6._verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", f"AC4: expected ok, got {result.status!r} err={result.error!r}"
        assert result.error_code != "E_POST_FIX_TYPECHECK_NO_BOUNDARY", (
            "AC4: no fix commit ever landed — must never surface "
            "E_POST_FIX_TYPECHECK_NO_BOUNDARY"
        )
        skip_events = [e for e in captured if e["type"] == "post_fix_typecheck_skipped"]
        assert len(skip_events) == 1, f"AC4: expected 1 post_fix_typecheck_skipped; got {len(skip_events)}"
        assert skip_events[0]["payload"].get("reason") == "no_python_scope", (
            f"AC4: expected reason='no_python_scope'; got {skip_events[0]['payload']!r}"
        )

    # ── AC5: degenerate boundary whose recovery target has no parent ──────────

    def test_ac5_degenerate_boundary_with_unresolvable_parent_errors(self, tmp_path, monkeypatch):
        """Repo with exactly ONE commit — fix_commit_sha IS that commit, so
        <fix_commit_sha>~1 cannot resolve. Boundary is degenerate (HEAD ==
        fix_commit_sha) and recovery fails => status='error',
        error_code='E_POST_FIX_TYPECHECK_NO_BOUNDARY'.

        Fails today: no degenerate-boundary/no-parent handling exists at all;
        the step falls through to ok/no_python_scope instead.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        (repo / "README.md").write_text("only commit\n")
        only_sha = _git_commit(repo, "initial and only commit")

        ctx = _make_ctx(repo)
        prev = StepResult(status="ok", data={"cycle": 1, "fix_commit_sha": only_sha}, duration_ms=0, step_name="commit_fix_code")

        result = p6._verify_fix_typecheck(ctx, prev)

        assert result.status == "error", (
            f"AC5: unresolvable recovery target must error; got status={result.status!r} "
            f"data={result.data!r}"
        )
        assert result.error_code == "E_POST_FIX_TYPECHECK_NO_BOUNDARY", (
            f"AC5: expected E_POST_FIX_TYPECHECK_NO_BOUNDARY, got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"AC5: E_POST_FIX_TYPECHECK_NO_BOUNDARY must be recoverable=True per contract; "
            f"got {result.recoverable!r}"
        )

    # ── AC11: producer never reads its own output — inherited fix_boundary_sha
    # ── is OVERWRITTEN, never honoured ─────────────────────────────────────────

    def test_ac11_fix_boundary_sha_is_overwritten_never_inherited(self, tmp_path, monkeypatch):
        """Construction B (C1 item 1b). Cycle-2 shape: prev.data carries a
        STALE fix_boundary_sha (cycle 1's published boundary) and no
        pre_fix_sha. _commit_fix_code must OVERWRITE it — the ok-return
        data['fix_boundary_sha'] is the boundary THIS call resolved (falls
        back to resolve_pre_phase_sha since pre_fix_sha absent), never the
        inherited stale value. Proves the producer does not read its own
        prior output — staleness is structurally impossible, not merely
        avoided by a rule.

        Fails today: fix_boundary_sha does not exist as a concept in
        production; the key is absent from the ok-return entirely.
        """
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        mod_py = repo / "src" / "mod.py"
        mod_py.parent.mkdir()
        mod_py.write_text("def f(x: int) -> int:\n    return x\n")
        baseline_sha = _git_commit(repo, "baseline")  # the REAL boundary this call must resolve

        # Cycle 2's fix worker edits the file WITHOUT committing.
        mod_py.write_text("def f(x: int) -> int:\n    return x\n\nCYCLE2 = 1\n")

        stale_fix_boundary_sha = "e" * 40  # fabricated — cycle 1's stale published boundary

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(repo, scratchpad_dir=str(scratchpad))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 2,
                "fix_boundary_sha": stale_fix_boundary_sha,  # inherited — must be ignored/overwritten
                "worker_written_paths": ["src/mod.py"],
                "manifest_source": "harness_tool_record",
            },
            duration_ms=0, step_name="invoke_fix_llm",
        )

        result = p6._commit_fix_code(ctx, prev)

        assert result.status == "ok", f"AC11: expected ok, got {result.status!r} err={result.error!r}"
        returned = result.data.get("fix_boundary_sha")
        assert returned is not None, (
            "AC11: data['fix_boundary_sha'] absent from _commit_fix_code's ok return — "
            "GREEN (C1 item 1/1b) not landed"
        )
        assert returned != stale_fix_boundary_sha, (
            f"AC11: _commit_fix_code must OVERWRITE the inherited fix_boundary_sha "
            f"(stale value {stale_fix_boundary_sha!r} survived into the return — the "
            f"producer is reading its own prior output, reintroducing staleness)"
        )
        assert returned == baseline_sha, (
            f"AC11: published fix_boundary_sha must be the boundary THIS call resolved "
            f"({baseline_sha!r}, via resolve_pre_phase_sha fallback since pre_fix_sha was "
            f"absent); got {returned!r}"
        )

    # ── AC12: zero regression surface — inherited pre_fix_sha honoured verbatim,
    # ── no extra rev-parse through the git_read seam (the load-bearing test) ───

    def test_ac12_inherited_pre_fix_sha_honoured_verbatim_no_extra_rev_parse(
        self, tmp_path, monkeypatch
    ):
        """C1 item 1/1b, load-bearing regression guard. An inherited
        prev.data['pre_fix_sha'] that is NOT current HEAD, with no
        fix_boundary_sha key, must be honoured VERBATIM: used for the diff,
        written to pre-fix-ref.txt, and republished as fix_boundary_sha —
        with NO additional 'rev-parse HEAD' call through the
        git_port.git_read seam beyond the one _commit_fix_code already makes
        post-commit.

        Pins the five tests the REJECTED round-2 equality rule broke
        (test_D5D6A364_git_read_routing_slice2 ac1/ac2 — exactly 1 rev-parse;
        the two test_phase_6_step10_commit_fix_code cases and
        test_phase_6_commit_fix_code_pre_ref_early_7B6A9AD1 — injected SHA
        reaches pre-fix-ref.txt verbatim; test_gh947_ac4 — moving the
        boundary must never silence a live fix_surface_violation guard).
        Mirrors the recording-spy technique from
        tests/test_D5D6A364_git_read_routing_slice2.py so the "exactly 1
        rev-parse" pin is a REAL collaborator-seam assertion, not vacuous.

        The verbatim-usage + single-rev-parse behaviour ALREADY holds today
        (construction B changes nothing about the read side) — but
        fix_boundary_sha does not exist yet, so this test still FAILS today
        on that one new assertion.
        """
        git_cwd_str = str(os.path.realpath(str(tmp_path / "repo")))  # §1j macOS-safe
        (tmp_path / "repo").mkdir(parents=True, exist_ok=True)

        stale_but_verbatim_sha = "d" * 40  # NOT current HEAD — honoured as-is regardless
        post_commit_head_sha = "c" * 40  # what the one legitimate rev-parse returns

        spy = _SpyGitRead(GitResult(returncode=0, stdout=post_commit_head_sha + "\n", stderr="", timed_out=False))

        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: None)
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        monkeypatch.setattr(
            p6, "_git_op_with_lock_retry",
            lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
        )
        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")

        src_dir = tmp_path / "repo" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "foo.py").write_text("")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        ctx = _make_ctx(Path(git_cwd_str), scratchpad_dir=str(scratchpad))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1,
                "pre_fix_sha": stale_but_verbatim_sha,  # inherited — NOT current HEAD
                "worker_written_paths": ["src/foo.py"],
                "manifest_source": "harness_tool_record",
            },
            duration_ms=0, step_name="invoke_fix_llm",
        )

        try:
            set_default_git_read_factory(lambda: spy)
            result = p6._commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        revparse_calls = [c for c in spy.calls if c[0] == ["rev-parse", "HEAD"]]
        assert len(revparse_calls) == 1, (
            f"AC12: expected exactly 1 rev-parse call through the git_read seam "
            f"(the round-2 equality rule added a second and broke this pin); "
            f"got {len(revparse_calls)}. All spy calls: {[c[0] for c in spy.calls]!r}"
        )

        assert result.status == "ok", f"AC12: expected ok, got {result.status!r} err={result.error!r}"
        integrity_ref = scratchpad / "integrity" / "pre-fix-ref.txt"
        assert integrity_ref.is_file(), f"AC12: {integrity_ref} was not written"
        assert integrity_ref.read_text().strip() == stale_but_verbatim_sha, (
            f"AC12: the inherited pre_fix_sha must be honoured verbatim in "
            f"pre-fix-ref.txt; got {integrity_ref.read_text().strip()!r}"
        )
        assert result.data.get("fix_boundary_sha") == stale_but_verbatim_sha, (
            f"AC12: the inherited pre_fix_sha must be republished verbatim as "
            f"fix_boundary_sha; got {result.data.get('fix_boundary_sha')!r}"
        )

    # ── AC17: pre-resolution skips DROP any inherited fix_boundary_sha ────────
    # (mutation-testing gap M7: deleting the `.pop("fix_boundary_sha", None)`
    # from either pre-resolution return survived all 17 prior tests.)

    def test_ac17_synthetic_env_skip_drops_inherited_fix_boundary_sha(self, tmp_path, monkeypatch):
        """Covers the _is_synthetic_test_env(...) True pre-resolution return
        (workflows/phase_6_review.py ~:4232-4247). A cycle-2 prev.data
        carrying an inherited (stale) fix_boundary_sha must NOT survive this
        skip — this call attested nothing, so a cycle-2 consumer must never
        read cycle-1's boundary through it.

        cfg has NO "git_cwd" key (only current_worktree_path) — per
        _is_synthetic_test_env's logic (~:3829), only the literal
        cfg["git_cwd"] key exempts the probe; current_worktree_path still
        resolves git_cwd for the probe itself, which fails against a plain
        non-repo directory, so the branch fires.

        Would FAIL without the `.pop`: 'fix_boundary_sha' would remain in
        result.data (forwarded verbatim via the **(prev.data or {}) spread),
        equal to the stale inherited value instead of being absent.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        non_git_dir = tmp_path / "not_a_repo"
        non_git_dir.mkdir()
        ctx = WorkflowContext(
            tenant_id="hal", scope=None, db_path=None,
            org_config={"current_worktree_path": str(non_git_dir)},
            question="AC17 synthetic env", session_id="test-ac17-synthetic",
            persona="hal", framework=None, domain=None,
        )
        stale_sha = "f" * 40
        prev = StepResult(
            status="ok",
            data={"cycle": 2, "fix_boundary_sha": stale_sha},
            duration_ms=0, step_name="invoke_fix_llm",
        )

        result = p6._commit_fix_code(ctx, prev)

        assert result.status == "ok", f"AC17: expected ok, got {result.status!r} err={result.error!r}"
        assert "fix_boundary_sha" not in (result.data or {}), (
            f"AC17: pre-resolution skip (synthetic env) must DROP any inherited "
            f"fix_boundary_sha, not forward it; got data={result.data!r}"
        )
        mode_skipped = [e for e in captured if e["type"] == "project_mode_skipped"]
        assert len(mode_skipped) == 1, (
            f"AC17 setup sanity: expected the synthetic-env branch (project_mode_skipped) "
            f"to fire — distinguishes this branch from the resolve_pre_phase_sha-raise one; "
            f"got events {[e['type'] for e in captured]!r}"
        )

    def test_ac17_no_git_repo_fallback_skip_drops_inherited_fix_boundary_sha(self, tmp_path, monkeypatch):
        """Covers the OTHER pre-resolution return
        (workflows/phase_6_review.py ~:4260-4279): explicit cfg["git_cwd"]
        pointing at a real, existing, NON-repo directory —
        _is_synthetic_test_env returns False (cfg["git_cwd"] is set,
        exempting the probe) but resolve_pre_phase_sha(git_cwd) raises
        RuntimeError (not a git repo), caught by the fallback except-branch.
        A cycle-2 inherited fix_boundary_sha must still be DROPPED.

        Would FAIL without the `.pop`: 'fix_boundary_sha' would remain in
        result.data (forwarded verbatim via the **(prev.data or {}) spread),
        equal to the stale inherited value instead of being absent.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        non_git_dir = tmp_path / "not_a_repo"
        non_git_dir.mkdir()
        ctx = _make_ctx(non_git_dir)  # sets cfg["git_cwd"] explicitly — exempts the synthetic-env probe
        stale_sha = "f" * 40
        prev = StepResult(
            status="ok",
            data={"cycle": 2, "fix_boundary_sha": stale_sha},
            duration_ms=0, step_name="invoke_fix_llm",
        )

        result = p6._commit_fix_code(ctx, prev)

        assert result.status == "ok", f"AC17: expected ok, got {result.status!r} err={result.error!r}"
        assert "fix_boundary_sha" not in (result.data or {}), (
            f"AC17: pre-resolution skip (resolve_pre_phase_sha raise) must DROP any "
            f"inherited fix_boundary_sha, not forward it; got data={result.data!r}"
        )
        mode_skipped = [e for e in captured if e["type"] == "project_mode_skipped"]
        assert len(mode_skipped) == 0, (
            "AC17 setup sanity: project_mode_skipped must NOT fire on this branch "
            "(that would mean the synthetic-env branch fired instead of this one)"
        )
        skip_events = [e for e in captured if e["type"] == "fix_commit_skipped"]
        assert len(skip_events) == 1 and skip_events[0]["payload"].get("reason") == "no_git_repo", (
            f"AC17 setup sanity: expected 1 fix_commit_skipped{{reason:no_git_repo}} "
            f"from the resolve_pre_phase_sha-raise branch; got {captured!r}"
        )

    # ── AC16: consumer precedence — fix_boundary_sha wins over a decoy pre_fix_sha

    @pytest.mark.skipif(not _mypy_available(), reason="mypy not on PATH — cannot run real typecheck AC16")
    def test_ac16_verify_fix_typecheck_prefers_fix_boundary_sha_over_decoy(
        self, tmp_path, monkeypatch
    ):
        """C1 item 1b precedence: fix_boundary_sha > pre_fix_sha >
        resolve_pre_phase_sha. Real repo + real mypy: prev.data carries BOTH
        keys, disagreeing — fix_boundary_sha is the TRUE pre-fix SHA,
        pre_fix_sha is a decoy equal to post-fix HEAD (degenerate/wrong).
        _verify_fix_typecheck must measure against fix_boundary_sha and
        catch the real regression.

        Fails today: _verify_fix_typecheck only ever reads
        prev.data['pre_fix_sha'] (fix_boundary_sha does not exist as a
        concept); with the decoy equal to HEAD the diff is empty and the
        step skips ok/no_python_scope instead of erroring.
        """
        monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
        repo, baseline_sha, fix_sha = _make_regression_repo(tmp_path)

        ctx = _make_ctx(repo)
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1,
                "fix_boundary_sha": baseline_sha,  # TRUE pre-fix boundary — must win
                "pre_fix_sha": fix_sha,  # decoy — equals post-fix HEAD, degenerate/wrong
            },
            duration_ms=0, step_name="commit_fix_code",
        )

        result = p6._verify_fix_typecheck(ctx, prev)

        assert result.status == "error", (
            f"AC16: fix_boundary_sha must take precedence and catch the real regression; "
            f"got status={result.status!r} data={result.data!r} error={result.error!r}"
        )
        assert result.error_code == "E_POST_FIX_TYPECHECK_REGRESSION", (
            f"AC16: expected E_POST_FIX_TYPECHECK_REGRESSION, got {result.error_code!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC13 (§1n) — error-code registry
# ═══════════════════════════════════════════════════════════════════════════


def test_ac13_no_boundary_error_code_is_registered_and_registry_is_clean():
    """E_POST_FIX_TYPECHECK_NO_BOUNDARY is a key of error_codes.ERROR_CODES,
    and error_codes.check(<engine_root>) returns no unregistered and no dead
    codes.

    Fails today (key absent): harvest_codes excludes tests/, so today's
    check(engine_root) is expected to already return (set(), set()) — the
    registry only starts drifting once GREEN puts the literal in production
    code without registering it here first.
    """
    from bytedigger_engine import error_codes  # noqa: PLC0415 — bare import, engine_py root is on sys.path

    assert "E_POST_FIX_TYPECHECK_NO_BOUNDARY" in error_codes.ERROR_CODES, (
        "AC13: E_POST_FIX_TYPECHECK_NO_BOUNDARY missing from error_codes.ERROR_CODES "
        "— GREEN (C1 item 4b) not landed"
    )

    engine_root = Path(error_codes.__file__).resolve().parent
    unregistered, dead = error_codes.check(engine_root)
    assert unregistered == set(), f"AC13: unregistered codes found: {unregistered!r}"
    assert dead == set(), f"AC13: dead codes found: {dead!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC14 — blast radius pinned: _commit_fix_tests' tail scan uses the real boundary
# ═══════════════════════════════════════════════════════════════════════════


def test_ac14_autocommit_fix_tail_scan_uses_published_boundary_not_post_fix_head(tmp_path, monkeypatch):
    """C1 item 4c. Real repo. HAL_AUTHORED_BOUNDARY_GATE enabled. Chain
    _commit_fix_code -> _commit_fix_tests with a dirty tail file left over
    (not in either manifest) so _autocommit_fix_tail runs its
    authored_boundary.scan_boundary call. The recorded base_sha for the TAIL
    scan must equal the boundary _commit_fix_code published (the real
    pre-fix baseline commit) — NOT post-fix HEAD.

    Module-trap discipline: patches authored_boundary.scan_boundary on the
    SAME `lib.authored_boundary` object p6's own `authored_boundary` name is
    bound to (p6.authored_boundary), so the patch is visible to
    _autocommit_fix_tail's real global lookup.

    Fails today: _commit_fix_code never publishes fix_boundary_sha (the
    concept doesn't exist), so _commit_fix_tests falls back to
    resolve_pre_phase_sha(git_cwd) == HEAD AT THAT POINT — which by then is
    already the fix-code commit, i.e. post-fix HEAD, not the real pre-fix
    baseline.
    """
    assert hasattr(p6.authored_boundary, "scan_boundary"), (
        "AC14 setup sanity: p6.authored_boundary must be the real lib.authored_boundary "
        "module — the module-trap discipline requires patching THIS object"
    )

    monkeypatch.setenv("ROLLOUT_CHECK_EMIT_TREE_SCAN", "0")
    monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "1")

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    mod_py = repo / "src" / "mod.py"
    mod_py.parent.mkdir()
    mod_py.write_text("def f(x: int) -> int:\n    return x\n")
    test_py = repo / "tests" / "test_mod.py"
    test_py.parent.mkdir()
    test_py.write_text("def test_f():\n    assert True\n")
    tail_md = repo / "docs" / "notes.md"
    tail_md.parent.mkdir()
    tail_md.write_text("notes\n")
    baseline_sha = _git_commit(repo, "baseline")

    # Fix worker edits: prod file (fix-code manifest), test file (fix-test
    # manifest), and an UNRELATED tail file left dirty in neither manifest.
    mod_py.write_text("def f(x: int) -> int:\n    return x\n\nFOO = 1\n")
    test_py.write_text("def test_f():\n    assert True\n\ndef test_g():\n    assert True\n")
    tail_md.write_text("notes\nmore notes\n")

    calls: list[dict] = []

    def _fake_scan_boundary(boundary, *, base_sha, paths, git_cwd, is_test_path, **kw):
        calls.append({"boundary": boundary, "base_sha": base_sha, "paths": list(paths)})
        return p6.authored_boundary.BoundaryScanResult(suppression_hits=[], tampered_tests=[])

    monkeypatch.setattr(p6.authored_boundary, "scan_boundary", _fake_scan_boundary)

    ctx = _make_ctx(repo)
    prev0 = StepResult(
        status="ok",
        data={
            "cycle": 1,
            "worker_written_paths": ["src/mod.py", "tests/test_mod.py"],
            "manifest_source": "harness_tool_record",
        },
        duration_ms=0, step_name="invoke_fix_llm",
    )

    result1 = p6._commit_fix_code(ctx, prev0)
    assert result1.status == "ok", f"AC14 setup: commit_fix_code must succeed: {result1.error!r}"

    result2 = p6._commit_fix_tests(ctx, result1)
    assert result2.status == "ok", f"AC14 setup: commit_fix_tests must succeed: {result2.error!r}"

    tail_calls = [c for c in calls if "docs/notes.md" in c["paths"]]
    assert len(tail_calls) == 1, (
        f"AC14 setup: expected exactly 1 authored_boundary.scan_boundary call for the "
        f"tail (docs/notes.md); got {len(tail_calls)}. All calls: {calls!r}"
    )
    assert tail_calls[0]["base_sha"] == baseline_sha, (
        f"AC14: tail scan's base_sha must equal the real pre-fix boundary "
        f"{baseline_sha!r} (published by _commit_fix_code) — got "
        f"{tail_calls[0]['base_sha']!r} (blast-radius: still measuring against post-fix HEAD)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# C2 — the suspect rate acquires force
# ═══════════════════════════════════════════════════════════════════════════


class TestC2SuspectRateForce:
    # ── AC6: aggregator forwards suspect_rate + threshold_exceeded ────────────

    def test_ac6_aggregate_forwards_suspect_rate_and_threshold_exceeded(self, tmp_path, monkeypatch):
        """3 suspect / 0 verified findings -> data['suspect_rate']==1.0,
        data['suspect_rate_threshold_exceeded'] is True, and (min-N: total 3
        >= 3) data['suspect_withhold'] is True, taken from the SAME
        computation that emits reviewer_suspect_rate.

        Fails today: none of the three keys exist in the returned data dict.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text("def a():\n    pass\n", encoding="utf-8")
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("HIGH", "sus-1", f"{target}:1: TOTALLY_FABRICATED_LINE_1"),
                ("MEDIUM", "sus-2", f"{target}:2: TOTALLY_FABRICATED_LINE_2"),
                ("LOW", "sus-3", f"{target}:3: TOTALLY_FABRICATED_LINE_3"),
            ],
            selfcount=3,
        )

        ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

        result = p6._aggregate_review_findings(ctx, prev)

        assert result.status == "ok", f"AC6: unexpected status {result.status!r}: {result.error!r}"
        rate_events = [e for e in captured if e["type"] == "reviewer_suspect_rate"]
        assert len(rate_events) == 1, "AC6 setup sanity: expected 1 reviewer_suspect_rate event"
        assert rate_events[0]["payload"]["rate"] == 1.0, "AC6 setup sanity: expected computed rate==1.0"

        assert result.data.get("suspect_rate") == 1.0, (
            f"AC6: data['suspect_rate'] must equal the reviewer_suspect_rate rate (1.0); "
            f"got {result.data.get('suspect_rate')!r}"
        )
        assert result.data.get("suspect_rate_threshold_exceeded") is True, (
            f"AC6: data['suspect_rate_threshold_exceeded'] must be True; "
            f"got {result.data.get('suspect_rate_threshold_exceeded')!r}"
        )
        assert result.data.get("suspect_withhold") is True, (
            f"AC6: data['suspect_withhold'] must be True (threshold_exceeded and total 3 >= "
            f"min-N 3); got {result.data.get('suspect_withhold')!r}"
        )

    # ── AC7: _persist_fix_feed withholds suspect findings when flag is True ───

    def test_ac7_persist_fix_feed_withholds_suspect_findings_when_flag_true(self, tmp_path, monkeypatch):
        """1 verified + 2 suspect, suspect_withhold=True: fix doc contains
        the verified block, contains NEITHER suspect block's body, and contains
        a withheld notice; fix_feed_suspect_withheld emitted with
        withheld_count==2.

        Fails today (assert-time, §D1CF5FDF): _persist_fix_feed has no
        suspect_withhold parameter at all yet.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        sig = inspect.signature(p6._persist_fix_feed)
        assert "suspect_withhold" in sig.parameters, (
            f"AC7: _persist_fix_feed has no suspect_withhold parameter yet "
            f"— GREEN (C2 item 6) not landed. signature={sig}"
        )

        verified = [{"block": "### SEVERITY: HIGH — real-issue\n> some/path.py:1: code\nConfidence: HIGH\n"}]
        suspect = [
            {"block": "### SEVERITY: MEDIUM — suspect-one\n> nope.py:1: fabricated-one\nConfidence: LOW\n"},
            {"block": "### SEVERITY: LOW — suspect-two\n> nope.py:2: fabricated-two\nConfidence: LOW\n"},
        ]
        doc_path = tmp_path / "reviews" / "review.md"
        doc_path.parent.mkdir(parents=True)

        result = p6._persist_fix_feed(doc_path, "", verified, suspect, "PARTIAL", suspect_withhold=True)

        assert not isinstance(result, StepResult), f"AC7: unexpected error return: {result!r}"
        content = result.read_text(encoding="utf-8")

        assert "real-issue" in content, "AC7: verified block must still be rendered"
        assert "suspect-one" not in content, "AC7: suspect block 1 body must be withheld"
        assert "suspect-two" not in content, "AC7: suspect block 2 body must be withheld"
        assert "withheld" in content.lower(), "AC7: fix doc must contain a withheld notice"

        withheld_events = [e for e in captured if e["type"] == "fix_feed_suspect_withheld"]
        assert len(withheld_events) == 1, (
            f"AC7: expected exactly 1 fix_feed_suspect_withheld event; got {len(withheld_events)}"
        )
        assert withheld_events[0]["payload"].get("withheld_count") == 2, (
            f"AC7: expected withheld_count==2; got {withheld_events[0]['payload']!r}"
        )

    # ── AC8: flag False → byte-identical to today's _render_fix_doc output ───

    def test_ac8_persist_fix_feed_flag_false_is_byte_identical_to_render_fix_doc(self, tmp_path, monkeypatch):
        """Regression guard on CA50885D fail-OPEN: with the flag False/absent,
        _persist_fix_feed's written bytes must equal
        _render_fix_doc(verified, verdict, suspect) — unchanged today.

        Fails today (assert-time, §D1CF5FDF): the suspect_withhold kwarg
        does not exist on _persist_fix_feed yet.
        """
        sig = inspect.signature(p6._persist_fix_feed)
        assert "suspect_withhold" in sig.parameters, (
            f"AC8: _persist_fix_feed has no suspect_withhold parameter yet "
            f"— GREEN (C2 item 7) not landed. signature={sig}"
        )

        verified = [{"block": "### SEVERITY: HIGH — real-issue\n> some/path.py:1: code\nConfidence: HIGH\n"}]
        suspect = [{"block": "### SEVERITY: MEDIUM — suspect-one\n> nope.py:1: fabricated-one\nConfidence: LOW\n"}]
        doc_path = tmp_path / "reviews" / "review.md"
        doc_path.parent.mkdir(parents=True)

        result = p6._persist_fix_feed(doc_path, "", verified, suspect, "PARTIAL", suspect_withhold=False)

        assert not isinstance(result, StepResult), f"AC8: unexpected error return: {result!r}"
        actual = result.read_text(encoding="utf-8")
        expected = p6._render_fix_doc(verified, "PARTIAL", suspect)
        assert actual == expected, (
            "AC8: flag=False output must be byte-identical to _render_fix_doc "
            f"(CA50885D fail-OPEN preserved)\n--- actual ---\n{actual!r}\n--- expected ---\n{expected!r}"
        )

    # ── AC9: end-to-end — review doc keeps the header, fix doc does not ───────

    def test_ac9_end_to_end_review_keeps_header_fix_doc_withholds(self, tmp_path, monkeypatch):
        """All findings suspect: the composite review artifact still contains
        SUSPECT_FINDINGS_SECTION_HEADER (Opus satisfaction keeps its
        Read-and-judge path); the fix doc, chained through
        _write_review_artifact, does not.

        Fails today: _write_review_artifact never threads a
        suspect_withhold flag into _persist_fix_feed, so the fix doc
        still contains the suspect blocks.
        """
        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text("def a():\n    pass\n", encoding="utf-8")
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("HIGH", "sus-1", f"{target}:1: TOTALLY_FABRICATED_LINE_1"),
                ("MEDIUM", "sus-2", f"{target}:2: TOTALLY_FABRICATED_LINE_2"),
                ("LOW", "sus-3", f"{target}:3: TOTALLY_FABRICATED_LINE_3"),
            ],
            selfcount=3,
        )

        agg_ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        agg_prev = StepResult(
            status="ok",
            data={
                "doc_path": str(tmp_path / "reviews" / "composite-review.md"),
                "spec_path": "n/a", "red_log_path": "n/a", "green_log_path": "n/a",
            },
            duration_ms=0, step_name="x",
        )
        agg_result = p6._aggregate_review_findings(agg_ctx, agg_prev)
        assert agg_result.status == "ok", f"AC9 setup: aggregation must succeed: {agg_result.error!r}"

        write_ctx = types.SimpleNamespace(org_config={}, question="q")
        write_result = p6._write_review_artifact(write_ctx, agg_result)
        assert write_result.status == "ok", f"AC9 setup: write_review_artifact must succeed: {write_result.error!r}"

        review_content = Path(write_result.data["review_doc_path"]).read_text(encoding="utf-8")
        assert p6.SUSPECT_FINDINGS_SECTION_HEADER in review_content, (
            "AC9: composite review artifact must still render the suspect section"
        )

        fix_content = Path(write_result.data["review_fix_doc_path"]).read_text(encoding="utf-8")
        assert "## Suspect Findings (LOW CONFIDENCE)" not in fix_content, (
            "AC9: fix doc must NOT contain the suspect section when all findings are suspect "
            "and the rate exceeds threshold"
        )

    # ── AC15: min-N — 2-finding sample never withholds ────────────────────────

    def test_ac15_min_n_two_finding_sample_never_withholds(self, tmp_path, monkeypatch):
        """5b (min-N). 1 verified + 1 suspect (total 2, rate 0.5 > 0.4 threshold):
        data['suspect_rate_threshold_exceeded'] is True (pure observability,
        unchanged) but data['suspect_withhold'] is False (total 2 < min-N 3) —
        withholding never fires on a 2-finding sample. End-to-end, the fix doc
        still renders the suspect block.

        Fails today: neither suspect_rate_threshold_exceeded nor
        suspect_withhold exist in the returned data dict at all.
        """
        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text(
            "def verified_line():\n    pass\n",
            encoding="utf-8",
        )
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("HIGH", "ver-1", f"{target}:1: def verified_line():"),
                ("MEDIUM", "sus-1", f"{target}:2: TOTALLY_FABRICATED_LINE"),
            ],
            selfcount=2,
        )

        agg_ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        agg_prev = StepResult(
            status="ok",
            data={
                "doc_path": str(tmp_path / "reviews" / "composite-review.md"),
                "spec_path": "n/a", "red_log_path": "n/a", "green_log_path": "n/a",
            },
            duration_ms=0, step_name="x",
        )
        agg_result = p6._aggregate_review_findings(agg_ctx, agg_prev)
        assert agg_result.status == "ok", f"AC15 setup: aggregation must succeed: {agg_result.error!r}"

        assert agg_result.data.get("suspect_rate_threshold_exceeded") is True, (
            f"AC15: data['suspect_rate_threshold_exceeded'] must be True (0.5 > 0.4); "
            f"got {agg_result.data.get('suspect_rate_threshold_exceeded')!r}"
        )
        assert agg_result.data.get("suspect_withhold") is False, (
            f"AC15: data['suspect_withhold'] must be False — min-N (total 2 < 3) never "
            f"withholds even when the rate exceeds threshold; "
            f"got {agg_result.data.get('suspect_withhold')!r}"
        )

        write_ctx = types.SimpleNamespace(org_config={}, question="q")
        write_result = p6._write_review_artifact(write_ctx, agg_result)
        assert write_result.status == "ok", f"AC15 setup: write_review_artifact must succeed: {write_result.error!r}"
        fix_content = Path(write_result.data["review_fix_doc_path"]).read_text(encoding="utf-8")
        assert "## Suspect Findings (LOW CONFIDENCE)" in fix_content, (
            "AC15: fix doc must still render the suspect block — min-N sample must never "
            "be withheld"
        )

    # ── AC18: the withholding decision must be visible in the composite ───────
    # ── review artifact and the review_findings_audit event, not only the ─────
    # ── fix doc and fix_feed_suspect_withheld ──────────────────────────────────

    _WITHHOLD_MARKER = "SUSPECT FINDINGS WITHHELD FROM FIX WORKER"

    def test_ac18_aggregated_content_states_suspect_withheld_with_rate_threshold_count(
        self, tmp_path, monkeypatch
    ):
        """3 suspect / 0 verified (the AC6 rate-1.0 shape, reused): the
        returned data['aggregated_content'] — what a human and the
        satisfaction evaluator (:2184) read — must STATE that suspect
        findings were withheld from the fix worker, via a stable marker
        line pinned by EXACT equality (round-5 gate finding 1: a substring
        check admits an additive hedge like '... (MUTED)' or '... advisory
        only' — the enforcement notice must not be dilutable). The
        withheld= field is the POST-dedup suspect count (round-5 gate
        finding 2: what was actually withheld is len(suspect_findings),
        not the pre-dedup rate-source count — they happen to coincide in
        THIS no-duplicate fixture, see the sibling dedup test for the case
        where they diverge). The review_findings_audit event payload must
        also carry suspect_withhold=True alongside its existing keys.

        Fails today: production still prints the OLD field name/value
        (`count={suspect_count}`, pre-dedup) at phase_6_review.py:1822-1825
        — the exact-equality line will never match `withheld=`.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text("def a():\n    pass\n", encoding="utf-8")
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("HIGH", "sus-1", f"{target}:1: TOTALLY_FABRICATED_LINE_1"),
                ("MEDIUM", "sus-2", f"{target}:2: TOTALLY_FABRICATED_LINE_2"),
                ("LOW", "sus-3", f"{target}:3: TOTALLY_FABRICATED_LINE_3"),
            ],
            selfcount=3,
        )

        ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

        result = p6._aggregate_review_findings(ctx, prev)

        assert result.status == "ok", f"AC18: unexpected status {result.status!r}: {result.error!r}"
        assert result.data.get("suspect_withhold") is True, (
            "AC18 setup sanity: expected suspect_withhold=True (rate 1.0 > 0.4, total 3 >= 3)"
        )
        rate = result.data.get("suspect_rate")
        threshold = p6._REVIEWER_SUSPECT_RATE_THRESHOLD
        # No cross-role duplicates in this fixture — post-dedup == pre-dedup == 3.
        post_dedup_withheld = len(result.data.get("suspect_findings") or [])
        assert post_dedup_withheld == 3, (
            f"AC18 setup sanity: expected 3 distinct post-dedup suspect findings; "
            f"got {post_dedup_withheld}"
        )
        expected = (
            f"SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate={rate} "
            f"threshold={threshold} withheld={post_dedup_withheld}"
        )

        content = result.data.get("aggregated_content") or ""
        close_lines = [ln for ln in content.splitlines() if "WITHHELD FROM FIX WORKER" in ln]
        assert len(close_lines) == 1, (
            f"AC18: expected exactly 1 withheld-notice line in aggregated_content; "
            f"got {len(close_lines)}. Full content:\n{content}"
        )
        assert close_lines[0] == expected, (
            f"AC18: withheld-notice line must be byte-exact — no hedging suffix "
            f"('(MUTED)', 'advisory only', etc.) is permitted; "
            f"expected {expected!r}, got {close_lines[0]!r}"
        )

        audit_events = [e for e in captured if e["type"] == "review_findings_audit"]
        assert len(audit_events) == 1, (
            f"AC18 setup sanity: expected exactly 1 review_findings_audit event; "
            f"got {len(audit_events)}"
        )
        assert audit_events[0]["payload"].get("suspect_withhold") is True, (
            f"AC18: review_findings_audit payload must carry suspect_withhold=True "
            f"alongside its existing keys; got {audit_events[0]['payload']!r}"
        )

    def test_ac18_min_n_sample_never_states_withheld_and_audit_flag_is_false(
        self, tmp_path, monkeypatch
    ):
        """Negative case (the AC15 min-N shape, reused): 1 verified + 1
        suspect (rate 0.5 > 0.4, total 2 < min-N 3) — nothing is actually
        withheld, so the withheld-notice marker must be ABSENT from
        aggregated_content (a marker present here would lie about a sample
        that was never withheld), and the review_findings_audit event's
        suspect_withhold must be False.

        Fails today: review_findings_audit has no 'suspect_withhold' key at
        all yet (see the positive test's docstring for the exact code
        location) — `.get('suspect_withhold') is False` is False when the
        key is absent (None is not False), so this assertion fails too.
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text("def verified_line():\n    pass\n", encoding="utf-8")
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("HIGH", "ver-1", f"{target}:1: def verified_line():"),
                ("MEDIUM", "sus-1", f"{target}:2: TOTALLY_FABRICATED_LINE"),
            ],
            selfcount=2,
        )

        ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")

        result = p6._aggregate_review_findings(ctx, prev)

        assert result.status == "ok", f"AC18: unexpected status {result.status!r}: {result.error!r}"
        assert result.data.get("suspect_withhold") is False, (
            "AC18 setup sanity: expected suspect_withhold=False (min-N: total 2 < 3)"
        )

        content = result.data.get("aggregated_content") or ""
        assert self._WITHHOLD_MARKER not in content, (
            f"AC18: nothing was actually withheld on a min-N sample — the withheld-notice "
            f"marker {self._WITHHOLD_MARKER!r} must NOT appear in aggregated_content"
        )

        audit_events = [e for e in captured if e["type"] == "review_findings_audit"]
        assert len(audit_events) == 1, (
            f"AC18 setup sanity: expected exactly 1 review_findings_audit event; "
            f"got {len(audit_events)}"
        )
        assert audit_events[0]["payload"].get("suspect_withhold") is False, (
            f"AC18: review_findings_audit payload must carry suspect_withhold=False "
            f"on a min-N sample; got {audit_events[0]['payload']!r}"
        )

    def test_ac18_withheld_count_is_post_dedup_and_matches_fix_feed_event(
        self, tmp_path, monkeypatch
    ):
        """Round-5 gate finding 2: a cross-role duplicate suspect finding
        (same severity, same normalized title) must be counted ONCE in the
        withheld= notice — the POST-dedup number — not the pre-dedup
        suspect_count that correctly sources the RATE (C2 item 5) but
        overstates what was actually withheld. That post-dedup number must
        also equal what _persist_fix_feed's fix_feed_suspect_withheld event
        reports for the SAME run — the two artifacts (composite review, fix
        doc telemetry) must agree; a single-number assertion on either one
        alone would not catch them drifting apart.

        Fixture: role-a and role-b both report a "dup-finding" MEDIUM
        finding with a fabricated (non-matching) quote — same (severity,
        normalized title) — plus one more distinct fabricated LOW finding
        in role-a. Pre-dedup suspect_count=3 (clears min-N, rate=1.0);
        post-dedup len(suspect_findings)=2 (dup-finding collapses to one).

        Fails today: production's withheld notice prints pre-dedup
        suspect_count (phase_6_review.py:1824, `count={suspect_count}`) —
        3, not 2 — so it would disagree with fix_feed_suspect_withheld's
        withheld_count=2 even once the field is renamed, and the exact-match
        assertion fails outright today regardless (old field name/value).
        """
        captured: list[dict] = []
        monkeypatch.setattr(p6, "_emit_safe", lambda et, p, **kw: captured.append({"type": et, "payload": p}))

        target = tmp_path / "scratch" / "source.py"
        target.parent.mkdir(parents=True)
        target.write_text("def a():\n    pass\n", encoding="utf-8")
        reviews_dir = tmp_path / "scratch" / "reviews"
        _write_role_file(
            reviews_dir, "role-a",
            blocks=[
                ("MEDIUM", "dup-finding", f"{target}:1: FABRICATED_DUP_ROLE_A"),
                ("LOW", "sus-distinct", f"{target}:2: FABRICATED_DISTINCT"),
            ],
            selfcount=2,
        )
        _write_role_file(
            reviews_dir, "role-b",
            blocks=[
                ("MEDIUM", "dup-finding", f"{target}:1: FABRICATED_DUP_ROLE_B"),
            ],
            selfcount=1,
        )

        agg_ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path / "scratch")}, question="q")
        agg_prev = StepResult(
            status="ok",
            data={
                "doc_path": str(tmp_path / "reviews" / "composite-review.md"),
                "spec_path": "n/a", "red_log_path": "n/a", "green_log_path": "n/a",
            },
            duration_ms=0, step_name="x",
        )
        agg_result = p6._aggregate_review_findings(agg_ctx, agg_prev)
        assert agg_result.status == "ok", f"AC18 setup: aggregation must succeed: {agg_result.error!r}"
        assert agg_result.data.get("suspect_withhold") is True, (
            "AC18 setup sanity: expected suspect_withhold=True (pre-dedup total 3 >= 3, rate 1.0)"
        )

        post_dedup_withheld = len(agg_result.data.get("suspect_findings") or [])
        assert post_dedup_withheld == 2, (
            f"AC18 setup sanity: dedup must collapse the cross-role duplicate to 2 "
            f"distinct suspect findings; got {post_dedup_withheld}"
        )

        rate = agg_result.data.get("suspect_rate")
        threshold = p6._REVIEWER_SUSPECT_RATE_THRESHOLD
        expected = (
            f"SUSPECT FINDINGS WITHHELD FROM FIX WORKER: rate={rate} "
            f"threshold={threshold} withheld={post_dedup_withheld}"
        )
        content = agg_result.data.get("aggregated_content") or ""
        close_lines = [ln for ln in content.splitlines() if "WITHHELD FROM FIX WORKER" in ln]
        assert len(close_lines) == 1, (
            f"AC18: expected exactly 1 withheld-notice line; got {len(close_lines)}. "
            f"Full content:\n{content}"
        )
        assert close_lines[0] == expected, (
            f"AC18: withheld-notice line must report the POST-dedup count ({post_dedup_withheld}), "
            f"not pre-dedup suspect_count; expected {expected!r}, got {close_lines[0]!r}"
        )

        write_ctx = types.SimpleNamespace(org_config={}, question="q")
        write_result = p6._write_review_artifact(write_ctx, agg_result)
        assert write_result.status == "ok", f"AC18 setup: write_review_artifact must succeed: {write_result.error!r}"

        withheld_events = [e for e in captured if e["type"] == "fix_feed_suspect_withheld"]
        assert len(withheld_events) == 1, (
            f"AC18 setup sanity: expected exactly 1 fix_feed_suspect_withheld event; "
            f"got {len(withheld_events)}"
        )
        assert withheld_events[0]["payload"].get("withheld_count") == post_dedup_withheld, (
            f"AC18 setup sanity: fix_feed_suspect_withheld.withheld_count must equal the "
            f"same post-dedup number ({post_dedup_withheld}); "
            f"got {withheld_events[0]['payload']!r}"
        )
        # The cross-artifact agreement THIS test exists to pin: the composite
        # review's notice and the fix-feed telemetry must report the SAME number.
        assert post_dedup_withheld == withheld_events[0]["payload"].get("withheld_count"), (
            "AC18: composite-review withheld= and fix_feed_suspect_withheld.withheld_count "
            "must agree — they are two views of the same withholding decision"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Step ordering — regression guard
# ═══════════════════════════════════════════════════════════════════════════


def test_ac10_verify_fix_typecheck_step_order_unchanged():
    """verify_fix_typecheck stays immediately after run_pytest_post_fix and
    before build_decorr_prompt. Must PASS TODAY — no reordering in this lot.
    """
    workflow = p6.phase_6_review_workflow()
    step_names = [s.name for s in workflow.steps]

    idx_pytest = step_names.index("run_pytest_post_fix")
    idx_typecheck = step_names.index("verify_fix_typecheck")
    idx_decorr = step_names.index("build_decorr_prompt")

    assert idx_typecheck == idx_pytest + 1, (
        f"AC10: verify_fix_typecheck must be immediately after run_pytest_post_fix; "
        f"steps={step_names!r}"
    )
    assert idx_typecheck == idx_decorr - 1, (
        f"AC10: verify_fix_typecheck must be immediately before build_decorr_prompt; "
        f"steps={step_names!r}"
    )
