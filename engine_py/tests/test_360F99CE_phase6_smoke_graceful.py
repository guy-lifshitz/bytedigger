"""RED tests — phase_6_smoke graceful-skip (360F99CE).

Spec: SHARED/memory/Decisions/2026-06-24_360F99CE_phase6_smoke_graceful_spec.md

AC1: script absent → status=="skip", data["skipped"]=="smoke_script_absent"
AC2: script present but zsh absent → status=="skip", data["skipped"]=="zsh_unavailable", bounded_run never called

bd#1 amended AC1/AC2 from "ok" to "skip". The original pair encoded the
false green rather than missing it: in the upstream tree an absent script
means a stray checkout and "graceful degradation" is the right reading, but
after extraction the branch is unconditional for every outside user, and a
phase that structurally did not run must not report a pass. "skip" is a
legal status and a success exit, so the pipeline outcome is unchanged.
AC3: script + zsh present, rc0, PASS output → status==ok, parsed counts, no skipped key
AC4: script + zsh present, rc1, FAIL output → status==error, E_SMOKE_FAILED
AC5: source of phase_6_smoke.py contains "import shutil" and does NOT contain "E_SCRIPT_MISSING"
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_6_smoke import phase_6_smoke_workflow  # noqa: E402
from bytedigger_engine.workflows import phase_6_smoke as _p6s_mod  # noqa: E402

_SMOKE_REL = "USER/skills/HALForge/tests/phase-6-signal-smoke.sh"


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"current_worktree_path": str(tmp_path)},
        question="",
        session_id="test",
        persona="hal",
        framework=None,
        domain=None,
    )


def _engine() -> WorkflowEngine:
    eng = WorkflowEngine()
    eng.register("phase_6_smoke", phase_6_smoke_workflow())
    return eng


# ─── AC1 ─────────────────────────────────────────────────────────────────────

def test_ac1_script_absent_returns_graceful_skip(tmp_path):
    """AC1: empty worktree → ok + skipped==smoke_script_absent (FAILS now: returns E_SCRIPT_MISSING)."""
    eng = _engine()
    result, _ = eng.execute("phase_6_smoke", _make_ctx(tmp_path))

    assert result.status == "skip", (
        f"Expected status='skip' for graceful skip (bd#1 — 'ok' reported a "
        f"pass for a check that never ran), got {result.status!r} "
        f"(error_code={getattr(result, 'error_code', None)!r})"
    )
    assert result.data.get("skipped") == "smoke_script_absent", (
        f"Expected data['skipped']=='smoke_script_absent', got data={result.data!r}"
    )


# ─── AC2 ─────────────────────────────────────────────────────────────────────

def test_ac2_zsh_absent_returns_graceful_skip_no_bounded_run(tmp_path, monkeypatch):
    """AC2: script present but zsh absent → ok + skipped==zsh_unavailable, bounded_run not called.

    FAILS now: phase_6_smoke has no shutil.which guard and calls bounded_run unconditionally.
    """
    # Create the fake smoke script file so the script-absent branch is NOT taken.
    script_path = tmp_path / _SMOKE_REL
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/bin/zsh\necho STUB\n")

    bounded_run_calls = []

    def fake_bounded_run(*args, **kwargs):
        bounded_run_calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="STATUS=PASS\n")

    # Monkeypatch where phase_6_smoke looks up the names.
    monkeypatch.setattr(_p6s_mod, "bounded_run", fake_bounded_run)

    # Patch shutil.which on the module; after GREEN, phase_6_smoke imports shutil
    # so this attribute will exist. Today it does NOT exist → we set it anyway so
    # the monkeypatch itself doesn't error (setattr creates the attribute).
    import shutil as _shutil_real
    monkeypatch.setattr(_p6s_mod, "shutil", types.SimpleNamespace(which=lambda _: None))

    eng = _engine()
    result, _ = eng.execute("phase_6_smoke", _make_ctx(tmp_path))

    assert result.status == "skip", (
        f"Expected status='skip' for zsh-absent graceful skip (bd#1), got "
        f"{result.status!r}"
    )
    assert result.data.get("skipped") == "zsh_unavailable", (
        f"Expected data['skipped']=='zsh_unavailable', got data={result.data!r}"
    )
    assert len(bounded_run_calls) == 0, (
        f"bounded_run must not be called when zsh absent; was called {len(bounded_run_calls)} time(s)"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────────

def test_ac3_happy_path_pass_output_parsed_correctly(tmp_path, monkeypatch):
    """AC3: script + zsh present, rc0, PASS output → ok, counts parsed, no skipped key.

    May already pass (happy path unchanged), but pins byte-identical behaviour.
    """
    script_path = tmp_path / _SMOKE_REL
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/bin/zsh\necho STUB\n")

    fake_proc = types.SimpleNamespace(
        returncode=0,
        stdout="PASS:a\nPASS:b\nSTATUS=PASS\n",
    )
    monkeypatch.setattr(_p6s_mod, "bounded_run", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(_p6s_mod, "shutil", types.SimpleNamespace(which=lambda _: "/bin/zsh"))

    eng = _engine()
    result, _ = eng.execute("phase_6_smoke", _make_ctx(tmp_path))

    assert result.status == "ok", f"Expected ok, got {result.status!r}"
    assert "skipped" not in result.data, (
        f"'skipped' key must be absent from data on a successful run; data={result.data!r}"
    )
    assert result.data.get("passed") == 2, f"Expected passed==2, data={result.data!r}"
    assert result.data.get("failed") == 0, f"Expected failed==0, data={result.data!r}"
    assert result.data.get("status_line") == "PASS", f"Expected status_line==PASS, data={result.data!r}"


# ─── AC4 ─────────────────────────────────────────────────────────────────────

def test_ac4_masking_guard_real_failure_still_hard_errors(tmp_path, monkeypatch):
    """AC4: script + zsh present, rc1, FAIL output → error + E_SMOKE_FAILED (masking guard).

    Should already pass today — guards that real failures still hard-error.
    """
    script_path = tmp_path / _SMOKE_REL
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/bin/zsh\necho STUB\n")

    fake_proc = types.SimpleNamespace(
        returncode=1,
        stdout="FAIL:x\nSTATUS=FAIL\n",
    )
    monkeypatch.setattr(_p6s_mod, "bounded_run", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(_p6s_mod, "shutil", types.SimpleNamespace(which=lambda _: "/bin/zsh"))

    eng = _engine()
    result, _ = eng.execute("phase_6_smoke", _make_ctx(tmp_path))

    assert result.status == "error", f"Expected error for failed smoke, got {result.status!r}"
    assert result.error_code == "E_SMOKE_FAILED", (
        f"Expected E_SMOKE_FAILED, got {result.error_code!r}"
    )


# ─── AC5 ─────────────────────────────────────────────────────────────────────

def test_ac5_source_has_shutil_import_and_no_e_script_missing():
    """AC5: phase_6_smoke.py source contains 'import shutil' AND NOT 'E_SCRIPT_MISSING'.

    Presence of 'import shutil' is the presence-gate; absence of 'E_SCRIPT_MISSING'
    is the absence assert. FAILS now on both counts.
    """
    prod_path = HERE.parent / "bytedigger_engine" / "workflows" / "phase_6_smoke.py"
    source = prod_path.read_text()

    # Presence gate — must pass before the absence assert is meaningful.
    assert "import shutil" in source, (
        f"Expected 'import shutil' in {prod_path} but not found. "
        "GREEN must add this import."
    )

    # Absence assert — only reached if presence gate passes.
    assert "E_SCRIPT_MISSING" not in source, (
        f"'E_SCRIPT_MISSING' must be removed from {prod_path} by GREEN. "
        "Found it still present."
    )
