"""RED tests for GH961 — dirty-tree guard before RED gate / validation entry.

Spec: SHARED/memory/Decisions/2026-07-17_GH961_dirty_tree_guard_spec.md

Covers §3 AC1-15 (v2: AC14 allowlist scoping, AC15 allowlist-unavailable
fail-open). `lib/dirty_tree_guard.py` and the two phase_5 guard blocks
do NOT exist yet — every test below fails today because either (a) the
`dirty_tree_guard` module cannot be imported (presence-gate pytest.fail),
or (b) the wiring assertion (E_RED_WORKTREE_DIRTY / event / kill-switch)
has nothing to produce it.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.lib import git_port as real_git_port  # noqa: E402  — real infra module, not the UUT


# ─── presence-gate helper (§1q / D1CF5FDF — no top-level import of missing module) ──


def _import_dtg():
    """Import lib.dirty_tree_guard, failing the calling test (not collection) if absent."""
    try:
        from bytedigger_engine.lib import dirty_tree_guard  # type: ignore
    except ImportError as e:
        pytest.fail(f"lib.dirty_tree_guard not implemented yet (GH961 GREEN pending): {e}")
    return dirty_tree_guard


def _import_phase5():
    from bytedigger_engine.workflows import phase_5_implement  # type: ignore
    return phase_5_implement


def _gitresult(stdout: str, returncode: int = 0) -> real_git_port.GitResult:
    return real_git_port.GitResult(
        returncode=returncode, stdout=stdout, stderr="", timed_out=False,
    )


def _make_ctx(git_cwd: str) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": git_cwd},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_red_prev(red_test_paths, cycle: int = 1, spec_path: str | None = None) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": spec_path if spec_path is not None else "scratchpad/spec.md",
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _write_spec_with_files(tmp_path: Path, files: list) -> str:
    """Write a minimal frozen spec with a `## Files` section (run_allowlist
    grammar) listing `files`, return its path as a string."""
    lines = ["# fixture spec\n", "\n## Files\n"]
    for f in files:
        lines.append(f"- {f}\n")
    p = tmp_path / "spec.md"
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


def _write_spec_with_fallback_scope_header(tmp_path: Path, files: list) -> str:
    """Write a frozen spec using the FALLBACK 'files in scope' heading grammar
    (`_FILES_IN_SCOPE_HEADER_RE` in lib/run_allowlist.py — a markdown heading
    whose text ends in 'files in scope', e.g. '## §5 — Files in scope'), the
    form real frozen specs use (no `## Files` section present)."""
    lines = ["# fixture spec\n", "\n## §5 — Files in scope\n"]
    for f in files:
        lines.append(f"- `{f}`\n")
    p = tmp_path / "spec-fallback.md"
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)


def _write_spec_without_files(tmp_path: Path) -> str:
    """Spec with no `## Files` section at all — allowlist parse returns None."""
    p = tmp_path / "spec_no_files.md"
    p.write_text("# fixture spec\n\nNo files section here.\n", encoding="utf-8")
    return str(p)


def _patch_emit(monkeypatch, module) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        module,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# AC1-5 — lib.dirty_tree_guard.dirty_prod_paths (unit-level)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDirtyProdPathsUnit:
    def test_ac1_modified_prod_path_included(self, monkeypatch) -> None:
        dtg = _import_dtg()
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        result = dtg.dirty_prod_paths("/fake/cwd", [], ["engine.py"])
        assert result == (["engine.py"], None), f"expected clean single-prod-path result, got {result}"

    def test_ac2_test_segment_and_red_path_excluded(self, monkeypatch) -> None:
        dtg = _import_dtg()
        porcelain = (
            "M  tests/test_x.py\n"
            "?? a/__tests__/y.test.ts\n"
            "M  red1.py\n"
        )
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult(porcelain),
        )
        violations, err = dtg.dirty_prod_paths("/fake/cwd", ["red1.py"], ["red1.py", "engine.py"])
        assert violations == [], (
            f"tests/ + __tests__/ + red_test_paths member must all be excluded, got {violations}"
        )
        assert err is None

    def test_ac3_rename_uses_new_path(self, monkeypatch) -> None:
        dtg = _import_dtg()
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("R  old.py -> new.py\n"),
        )
        violations, err = dtg.dirty_prod_paths("/fake/cwd", [], ["new.py"])
        assert violations == ["new.py"], f"rename must resolve to the NEW path, got {violations}"
        assert err is None

    def test_ac4_untracked_prod_file_included(self, monkeypatch) -> None:
        dtg = _import_dtg()
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("?? newmod.py\n"),
        )
        violations, err = dtg.dirty_prod_paths("/fake/cwd", [], ["newmod.py"])
        assert violations == ["newmod.py"], f"untracked prod file must be included, got {violations}"

    def test_ac5_git_failure_is_fail_open(self, monkeypatch) -> None:
        dtg = _import_dtg()

        def _boom(*a, **kw):
            raise RuntimeError("boom — simulated git failure")

        monkeypatch.setattr(dtg.git_port, "git_read", _boom)
        violations, err = dtg.dirty_prod_paths("/fake/cwd", [], ["engine.py"])
        assert violations == [], f"fail-open must return [] violations on git exception, got {violations}"
        assert isinstance(err, str) and err, f"fail-open must return a non-empty error string, got {err!r}"

        # rc != 0 path must fail open identically.
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("", returncode=128),
        )
        violations2, err2 = dtg.dirty_prod_paths("/fake/cwd", [], ["engine.py"])
        assert violations2 == [], f"fail-open must return [] on rc!=0, got {violations2}"
        assert isinstance(err2, str) and err2, f"fail-open must return a non-empty error string on rc!=0, got {err2!r}"


class TestDirtyProdPathsAllowlistScoping:
    """v2 AC14 — allowlist scoping + normalization."""

    def test_ac14_dirty_path_outside_allowlist_excluded(self, monkeypatch) -> None:
        dtg = _import_dtg()
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  SHARED/state/x.txt\n"),
        )
        result = dtg.dirty_prod_paths("/fake/cwd", [], ["engine.py"])
        assert result == ([], None), (
            f"dirty path outside the spec allowlist must be excluded (false-positive-machine fix), got {result}"
        )

    def test_ac14_dirty_path_inside_allowlist_included_and_normalized(self, monkeypatch) -> None:
        dtg = _import_dtg()
        monkeypatch.setattr(
            dtg.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  ./engine.py\n"),
        )
        result = dtg.dirty_prod_paths("/fake/cwd", [], ["engine.py"])
        assert result == (["engine.py"], None), (
            f"'./'-prefixed porcelain path must normalize-match a bare allowlist entry, got {result}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6-12 — phase_5_implement wiring
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase5RedGateWiring:
    def test_ac6_dirty_prod_blocks_red_gate(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()  # presence gate — fails here if lib.dirty_tree_guard missing
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        result = p5._verify_red_fails_mechanically(ctx, prev)

        assert result.status == "error", f"dirty prod tree must hard-stop RED gate, got status={result.status}"
        assert result.error_code == "E_RED_WORKTREE_DIRTY", (
            f"expected E_RED_WORKTREE_DIRTY, got {result.error_code!r}"
        )
        assert result.recoverable is False, f"guard must be non-recoverable, got {result.recoverable}"
        assert "engine.py" in (result.error or ""), f"error message must name the dirty path, got {result.error!r}"
        assert "HAL_DIRTY_TREE_GUARD" in (result.error or ""), (
            f"error message must mention the escape env var, got {result.error!r}"
        )

    def test_ac16_fallback_scope_header_grammar_blocks_red_gate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """v2 AC16 — real frozen specs use the FALLBACK 'files in scope'
        heading grammar (`## §5 — Files in scope`), not `## Files`. A GREEN
        that only implements the `## Files` branch of parse_spec_files_allowlist
        would pass AC6 but stay vacuous on every real build."""
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_fallback_scope_header(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        result = p5._verify_red_fails_mechanically(ctx, prev)

        assert result.status == "error", (
            f"fallback-grammar spec allowlist must still block a dirty prod tree, got status={result.status}"
        )
        assert result.error_code == "E_RED_WORKTREE_DIRTY", (
            f"expected E_RED_WORKTREE_DIRTY via fallback-grammar allowlist, got {result.error_code!r}"
        )
        assert "engine.py" in (result.error or ""), (
            f"error message must name the dirty path resolved via fallback grammar, got {result.error!r}"
        )

    def test_ac7_dirty_prod_emits_blocked_event(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        captured = _patch_emit(monkeypatch, p5)
        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        p5._verify_red_fails_mechanically(ctx, prev)

        blocked = [e for e in captured if e["type"] == "red_dirty_tree_blocked"]
        assert len(blocked) >= 1, f"expected red_dirty_tree_blocked event, captured={captured}"
        assert blocked[0]["payload"].get("paths"), f"event payload must carry non-empty paths, got {blocked[0]['payload']}"

    def test_ac8_clean_tree_reaches_test_plan_stage(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult(""),
        )
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, "clean tree must reach the test-plan stage (no dirty-tree short-circuit)"
        blocked = [e for e in captured if e["type"] == "red_dirty_tree_blocked"]
        assert blocked == [], f"clean tree must not emit red_dirty_tree_blocked, got {blocked}"

    def test_ac9_test_segment_and_red_paths_dirty_on_retry_passes(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        # Only test-segment / red_test_paths dirty — legit cycle>=2 RED retry (§1ab b).
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  tests/test_x.py\n"),
        )
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], cycle=2, spec_path=spec_path)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, "test-segment-only dirt on a cycle-2 retry must pass the guard"
        blocked = [e for e in captured if e["type"] == "red_dirty_tree_blocked"]
        assert blocked == [], f"cycle-2 retry with only test dirt must not emit blocked event, got {blocked}"

    def test_ac10_kill_switch_bypasses_guard(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setenv("HAL_DIRTY_TREE_GUARD", "0")

        call_count = {"n": 0}

        def _counting_git_read(*a, **kw):
            call_count["n"] += 1
            return _gitresult("M  engine.py\n")

        monkeypatch.setattr(p5.dirty_tree_guard.git_port, "git_read", _counting_git_read)
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, "kill-switch must let a dirty prod tree through"
        assert call_count["n"] == 0, f"kill-switch must skip the git call entirely, got {call_count['n']} calls"
        assert captured == [], f"kill-switch must emit nothing, got {captured}"


class TestPhase5ValidationEntryWiring:
    def test_ac11_dirty_prod_blocks_validation_before_prompt_assembly(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )

        def _assembly_marker(*a, **kw):
            raise AssertionError("_read_first_block must NOT run when the guard should block")

        monkeypatch.setattr(p5, "_read_first_block", _assembly_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        result = p5._build_validation_prompt(ctx, prev)

        assert result.status == "error", f"dirty prod tree must block validation entry, got status={result.status}"
        assert result.error_code == "E_RED_WORKTREE_DIRTY", (
            f"expected E_RED_WORKTREE_DIRTY at validation entry, got {result.error_code!r}"
        )

    def test_ac12_fail_open_on_git_error_at_red_gate(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()

        def _boom(*a, **kw):
            raise RuntimeError("boom — simulated git failure")

        monkeypatch.setattr(p5.dirty_tree_guard.git_port, "git_read", _boom)
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_with_files(tmp_path, ["engine.py"])
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, "fail-open must let the run proceed on a git error"
        err_events = [e for e in captured if e["type"] == "red_dirty_tree_guard_error"]
        assert len(err_events) >= 1, f"fail-open must emit red_dirty_tree_guard_error, captured={captured}"


class TestPhase5AllowlistUnavailableWiring:
    """v2 AC15 — spec without a `## Files` block (or missing spec_path) must NOT
    be treated as an unbounded allowlist; guard fails open with
    'spec_allowlist_unavailable' and the run proceeds."""

    def test_ac15_no_files_section_fails_open_with_reason(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        spec_path = _write_spec_without_files(tmp_path)
        prev = _make_red_prev(["tests/test_x.py"], spec_path=spec_path)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, (
            "spec without a ## Files section must NOT block RED gate — allowlist-unavailable is fail-open"
        )
        blocked = [e for e in captured if e["type"] == "red_dirty_tree_blocked"]
        assert blocked == [], f"allowlist-unavailable must never block, got {blocked}"
        err_events = [e for e in captured if e["type"] == "red_dirty_tree_guard_error"]
        assert len(err_events) >= 1, f"expected red_dirty_tree_guard_error, captured={captured}"
        assert any(
            "spec_allowlist_unavailable" in str(e["payload"].get("error", "")) for e in err_events
        ), f"guard-error payload must name 'spec_allowlist_unavailable', got {err_events}"

    def test_ac15_missing_spec_path_fails_open_with_reason(self, tmp_path: Path, monkeypatch) -> None:
        _import_dtg()
        p5 = _import_phase5()
        monkeypatch.setattr(
            p5.dirty_tree_guard.git_port, "git_read",
            lambda *a, **kw: _gitresult("M  engine.py\n"),
        )
        captured = _patch_emit(monkeypatch, p5)

        marker = {"reached": False}

        def _plan_marker(paths, git_cwd=None):
            marker["reached"] = True
            raise _PlanReachedMarker("test-plan stage reached")

        monkeypatch.setattr(p5, "_infer_test_command_for_paths", _plan_marker)

        ctx = _make_ctx(str(tmp_path))
        prev = _make_red_prev(["tests/test_x.py"], spec_path=None)

        with pytest.raises(_PlanReachedMarker):
            p5._verify_red_fails_mechanically(ctx, prev)

        assert marker["reached"] is True, "missing spec_path must fail open, not block"
        err_events = [e for e in captured if e["type"] == "red_dirty_tree_guard_error"]
        assert any(
            "spec_allowlist_unavailable" in str(e["payload"].get("error", "")) for e in err_events
        ), f"expected 'spec_allowlist_unavailable' guard-error, captured={captured}"


class _PlanReachedMarker(Exception):
    """Marker raised by the stubbed _infer_test_command_for_paths to prove the
    guard let execution reach the test-plan stage (downstream infra, not the UUT)."""


# ═══════════════════════════════════════════════════════════════════════════════
# AC13 — registrations
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistrations:
    def test_ac13_error_code_registered(self) -> None:
        from bytedigger_engine import error_codes  # type: ignore
        codes = getattr(error_codes, "ERROR_CODES", None) or getattr(error_codes, "ERROR_CODE_REGISTRY", None)
        assert codes is not None, "could not locate the error-code registry dict in error_codes module"
        assert "E_RED_WORKTREE_DIRTY" in codes, (
            f"E_RED_WORKTREE_DIRTY missing from error_codes registry; keys sample={list(codes)[:5]}"
        )

    def test_ac13_flag_registered_default_on(self) -> None:
        from bytedigger_engine import flags_catalog  # type: ignore
        flags = getattr(flags_catalog, "FLAGS", None) or getattr(flags_catalog, "FLAGS_CATALOG", None)
        assert flags is not None, "could not locate the flags registry dict in flags_catalog module"
        assert "HAL_DIRTY_TREE_GUARD" in flags, (
            f"HAL_DIRTY_TREE_GUARD missing from flags_catalog registry; keys sample={list(flags)[:5]}"
        )
        entry = flags["HAL_DIRTY_TREE_GUARD"]
        assert str(entry.get("default")) == "1", f"HAL_DIRTY_TREE_GUARD must default ON ('1'), got {entry.get('default')!r}"
