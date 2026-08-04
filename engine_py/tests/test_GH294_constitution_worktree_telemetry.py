"""RED tests for GH294 Ship A — constitution worktree-aware detection + resolver telemetry.

Spec: SHARED/memory/Decisions/2026-07-05_2F277499_gh294_constitution_shipA_spec.md
Agreement: 2F277499-60C3-4A5B-930C-5F42EA3F2BE2  Lane: Option-D  Class: SYSTEMATIC

§1q/D1CF5FDF: `_resolve_constitution` does not exist yet at RED time. NO
top-level import of it — every reference is deferred inside test bodies via
getattr() presence-gates that FAIL the assertion (never break collection).
This file collects cleanly under conftest's sys.path singleton (no
module-level sys.path mutation here).

AC1 — real git worktree outside fake HOME/.claude, whose --git-common-dir
      resolves under fake HOME/.claude → _cwd_inside_hal_dir() True.
AC2 — non-repo path outside home_root → False (regression twin of GH338A AC7).
AC3 — git absent/failing (subprocess.run raises FileNotFoundError) on outside
      path → False, no exception.
AC4 — physically-inside path → True WITHOUT invoking git (subprocess.run
      raises AssertionError if called at all).
AC5 — _resolve_constitution module attr exists; returns (None,"none") on
      empty cfg in foreign cwd, and (path,"explicit") for a valid explicit cfg.
AC6 — _discover_constitution w/ explicit cfg emits resolver_constitution_resolved
      source="explicit", value=resolved path.
AC7 — _discover_constitution w/ foreign cwd + no files emits same event
      source="none", value="".
AC8 — StepResult regression: explicit-path case data/status/step_name shape
      byte-identical to today.

Expected RED (pre-GREEN): AC1, AC3, AC4, AC5, AC6, AC7, AC8 FAIL.
AC2 is a pure regression of existing behavior and may PASS pre-GREEN
(spec §4 note) — declared, not a bug in this RED file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine.workflows import phase_05_inject
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import WorkflowContext
from bytedigger_engine.event_log import EventLog


def _make_ctx(org_config: dict | None = None) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config or {},
        question="test GH294 shipA",
        session_id="test-GH294",
        persona="hal",
        framework=None,
        domain=None,
    )


@pytest.fixture(autouse=True)
def _reset_telemetry_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _monkeypatch_home(monkeypatch, tmp_path) -> Path:
    """Crib of GH338A AC7 fixture pattern: relocate HOME so provider
    home_root() follows it deterministically."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    fake_hal = fake_home / ".claude"
    fake_hal.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    if hasattr(Path.home, "cache_clear"):
        Path.home.cache_clear()
    return fake_hal


# ─── AC1 ────────────────────────────────────────────────────────────────────

def test_ac1_git_worktree_outside_home_resolves_inside_via_git_common_dir(monkeypatch, tmp_path):
    """AC1: a real git worktree checked out outside fake HOME/.claude, whose
    --git-common-dir resolves under fake HOME/.claude, is classified
    HAL-internal (True) by the §2.1 fallback.

    Pre-GREEN FAILS: fallback does not exist yet — physical check alone
    returns False for a path outside home_root().
    """
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    fake_hal = _monkeypatch_home(monkeypatch, tmp_path)

    repo = fake_hal / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=str(repo), check=True,
    )

    worktree_dir = tmp_path / "outside_worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(worktree_dir)],
        cwd=str(repo), check=True,
    )

    assert not worktree_dir.resolve().is_relative_to(fake_hal.resolve()), (
        "fixture bug: worktree must be physically outside fake HAL home"
    )

    result = phase_05_inject._cwd_inside_hal_dir(worktree_dir)
    assert result is True, (
        f"AC1: expected True (git-common-dir fallback under fake HOME/.claude), got {result!r}"
    )


# ─── AC2 ────────────────────────────────────────────────────────────────────

def test_ac2_non_repo_path_outside_home_root_is_false(monkeypatch, tmp_path):
    """AC2: regression twin of GH338A AC7 — plain non-repo tmp_path outside
    home_root() → False. May PASS pre-GREEN (pure existing behavior)."""
    _monkeypatch_home(monkeypatch, tmp_path)

    outside = tmp_path / "plain_outside"
    outside.mkdir()

    result = phase_05_inject._cwd_inside_hal_dir(outside)
    assert result is False, f"AC2: expected False, got {result!r}"


# ─── AC3 ────────────────────────────────────────────────────────────────────

def test_ac3_git_failure_on_outside_path_folds_to_false_no_exception(monkeypatch, tmp_path):
    """AC3: git absent/failing (subprocess.run raises FileNotFoundError) on an
    outside path → False, no exception propagates. Forcing-fn: the fallback
    MUST actually attempt git (call count >=1) — a pure physical-check-only
    implementation would return False without ever invoking git.

    Pre-GREEN FAILS: prod code does not call subprocess at all yet — call
    count stays 0, failing the invocation assertion (anti-vacuity: proves the
    §2.1 fallback branch was reached, not just that False falls out for free).
    """
    outside = tmp_path / "outside"
    outside.mkdir()

    calls = []

    def _raise_missing(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise_missing)

    try:
        result = phase_05_inject._cwd_inside_hal_dir(outside)
    except FileNotFoundError:
        pytest.fail("AC3: FileNotFoundError from git must be caught, not propagated")

    assert result is False, f"AC3: expected False, got {result!r}"
    assert len(calls) >= 1, (
        "AC3: expected the §2.1 git-common-dir fallback to invoke subprocess.run "
        "at least once for an outside path; got 0 calls (fallback not implemented)"
    )


# ─── AC4 ────────────────────────────────────────────────────────────────────

def test_ac4_physical_inside_short_circuits_without_invoking_git(monkeypatch, tmp_path):
    """AC4: a path physically inside home_root() returns True WITHOUT ever
    invoking subprocess.run (anti-vacuity ordering probe — §1l).

    Pre-GREEN: prod code today does not import/call subprocess at all inside
    _cwd_inside_hal_dir, so this monkeypatch is inert and the test currently
    only asserts True (which already passes today via the physical check).
    Declared expected-PASS is NOT claimed here per spec — see report.
    """
    fake_hal = _monkeypatch_home(monkeypatch, tmp_path)
    inside = fake_hal / "inside_subdir"
    inside.mkdir()

    def _boom(*args, **kwargs):
        raise AssertionError("git must not be invoked for a physically-inside path")

    monkeypatch.setattr(subprocess, "run", _boom)

    result = phase_05_inject._cwd_inside_hal_dir(inside)
    assert result is True, f"AC4: expected True via physical short-circuit, got {result!r}"


# ─── AC5 ────────────────────────────────────────────────────────────────────

def test_ac5_resolve_constitution_exists_and_returns_none_and_explicit(monkeypatch, tmp_path):
    """AC5: `_resolve_constitution` is a module attr on phase_05_inject and
    returns (None,"none") on empty cfg in a foreign cwd, and
    ("<path>","explicit") when cfg has a valid constitution_path.

    Pre-GREEN FAILS: attribute does not exist — presence-gate assertion fails
    (not an AttributeError at collection time; defer via getattr()).
    """
    assert hasattr(phase_05_inject, "_resolve_constitution"), (
        "AC5: phase_05_inject._resolve_constitution does not exist yet (pre-GREEN)"
    )
    _resolve_constitution = phase_05_inject._resolve_constitution

    outside = tmp_path / "foreign_cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("HOME", str(tmp_path / "unrelated_home"))
    (tmp_path / "unrelated_home").mkdir()
    if hasattr(Path.home, "cache_clear"):
        Path.home.cache_clear()

    result_none = _resolve_constitution({})
    assert result_none == (None, "none"), f"AC5: expected (None,'none'), got {result_none!r}"

    const_file = tmp_path / "my_constitution.md"
    const_file.write_text("# constitution")
    result_explicit = _resolve_constitution({"constitution_path": str(const_file)})
    assert result_explicit == (str(const_file.resolve()), "explicit"), (
        f"AC5: expected ({str(const_file.resolve())!r},'explicit'), got {result_explicit!r}"
    )


# ─── AC6 ────────────────────────────────────────────────────────────────────

def test_ac6_discover_constitution_explicit_emits_resolver_event(tmp_path):
    """AC6: _discover_constitution with an explicit cfg path emits a single
    resolver_constitution_resolved event with source="explicit", value=the
    resolved path (telemetry_ctx capture, pattern of test_emit_resolver_966E571B.py).

    Pre-GREEN FAILS: no emit call exists in _discover_constitution yet → 0
    matching events.
    """
    const_file = tmp_path / "constitution.md"
    const_file.write_text("# constitution")

    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="ac6-run", step_name="t")

    ctx = _make_ctx(org_config={"constitution_path": str(const_file)})
    result = phase_05_inject._discover_constitution(ctx, None)

    events = log.read_all()
    resolver_events = [
        e for e in events if e.get("event_type") == "resolver_constitution_resolved"
    ]
    assert len(resolver_events) == 1, (
        f"AC6: expected exactly 1 resolver event, got {len(resolver_events)} "
        f"(total events: {len(events)})"
    )
    row = resolver_events[0]
    assert row["payload"]["source"] == "explicit", (
        f"AC6: expected source='explicit', got {row['payload']['source']!r}"
    )
    assert row["payload"]["value"] == str(const_file.resolve()), (
        f"AC6: expected value={str(const_file.resolve())!r}, got {row['payload']['value']!r}"
    )
    assert result.data["constitution_path"] == str(const_file.resolve())


# ─── AC7 ────────────────────────────────────────────────────────────────────

def test_ac7_discover_constitution_none_source_emits_empty_value(monkeypatch, tmp_path):
    """AC7: _discover_constitution resolving to none (foreign cwd, no files)
    emits the same event with source="none", value="".

    Pre-GREEN FAILS: no emit call exists yet → 0 matching events.
    """
    foreign_cwd = tmp_path / "foreign_project"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    unrelated_home = tmp_path / "unrelated_home2"
    unrelated_home.mkdir()
    monkeypatch.setenv("HOME", str(unrelated_home))
    if hasattr(Path.home, "cache_clear"):
        Path.home.cache_clear()

    log = EventLog(tmp_path / "events2.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="ac7-run", step_name="t")

    ctx = _make_ctx(org_config={})
    result = phase_05_inject._discover_constitution(ctx, None)

    events = log.read_all()
    resolver_events = [
        e for e in events if e.get("event_type") == "resolver_constitution_resolved"
    ]
    assert len(resolver_events) == 1, (
        f"AC7: expected exactly 1 resolver event, got {len(resolver_events)} "
        f"(total events: {len(events)})"
    )
    row = resolver_events[0]
    assert row["payload"]["source"] == "none", (
        f"AC7: expected source='none', got {row['payload']['source']!r}"
    )
    assert row["payload"]["value"] == "", (
        f"AC7: expected value='', got {row['payload']['value']!r}"
    )
    assert result.data["source"] == "none"
    assert result.data["constitution_path"] is None


# ─── AC8 ────────────────────────────────────────────────────────────────────

def test_ac8_step_result_shape_regression_explicit_case(tmp_path):
    """AC8: StepResult regression — for the explicit-path case, returned
    `data` == {"constitution_path": <resolved>, "source": "explicit"} and
    status=="ok", step_name=="discover_constitution" (consumer shape lock,
    §1o — _write_injection_files reads constitution_path/source) — AND the
    §2.2 single-chokepoint telemetry call fired exactly once for this call.

    Pre-GREEN FAILS: no emit call exists in _discover_constitution yet — the
    resolver-event count assertion (0 vs expected 1) fails even though the
    StepResult shape itself already matches today's behavior.
    """
    const_file = tmp_path / "constitution.md"
    const_file.write_text("# constitution")

    log = EventLog(tmp_path / "events_ac8.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="ac8-run", step_name="t")

    ctx = _make_ctx(org_config={"constitution_path": str(const_file)})
    result = phase_05_inject._discover_constitution(ctx, None)

    assert result.status == "ok"
    assert result.step_name == "discover_constitution"
    assert result.data == {
        "constitution_path": str(const_file.resolve()),
        "source": "explicit",
    }

    events = log.read_all()
    resolver_events = [
        e for e in events if e.get("event_type") == "resolver_constitution_resolved"
    ]
    assert len(resolver_events) == 1, (
        f"AC8: expected exactly 1 resolver_constitution_resolved event (single "
        f"chokepoint telemetry), got {len(resolver_events)}"
    )
