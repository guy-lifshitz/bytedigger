"""RED tests for E8A5ACAB — phase_1_discovery graphs TARGET repo, not HAL_DIR.

UUT: phase_1_discovery._invoke_discovery_llm.

Pre-GREEN predict:
  AC1: PASS today — cfg["hal_root"] accidentally satisfies the assertion only if
       HAL_DIR == hal_root; but we use a tmp dir so HAL_DIR != tmp → FAIL.
  AC2: FAIL — ensure_graph receives str(HAL_DIR), not resolved foreign git_cwd.
  AC3: FAIL — same root cause (HAL_DIR hardcoded; hal_root precedence not wired).
  AC4: FAIL — `from event_log import HAL_DIR` still present; `resolve_project_root`
       absent.
  AC5: PASS today — invoke_llm_subprocess return already threaded back by prod.

§1q: conftest-import-time singleton provides ENGINE_ROOT + workflows on sys.path.
     No sys.path.insert here (81F97F3D gate).
D1CF5FDF: phase module imported via importlib.import_module inside test bodies so
     a missing-attribute failure occurs at assert time, not collection time.
§1l stub-passability: ensure_graph is a collaborator (not the UUT). UUT is the
     resolve_project_root-call + arg-threading that does NOT exist yet in phase_1.
§1i: resolve_project_root emits resolver events; patched via patch.object on the
     project_root module in tests that let the real resolver run (AC2, AC3).
§1v: only test_E8A5ACAB_phase1_target_repo.py created; phase_1_discovery.py untouched.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py inserts ENGINE_ROOT + ENGINE_ROOT/workflows at import time.
# No sys.path.insert here (§1q / 81F97F3D gate).

from contracts import StepResult, WorkflowContext  # noqa: E402 — available via conftest path

# ---------------------------------------------------------------------------
# Shared sentinel
# ---------------------------------------------------------------------------

_SENTINEL_GRAPH = "E8A5ACAB_GRAPH_SENTINEL"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path: Path, org_config: dict | None = None) -> WorkflowContext:
    """Build a WorkflowContext with the given org_config (or empty dict)."""
    (tmp_path / "injection").mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config if org_config is not None else {"scratchpad_dir": str(tmp_path)},
        question="test question for E8A5ACAB target-repo graph",
        session_id="test-E8A5ACAB",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_prev_discovery(tmp_path: Path) -> StepResult:
    """Build a valid prev StepResult for _invoke_discovery_llm.

    Needs data["prompt"], data["doc_path"], data["complexity"]
    (see phase_1_discovery.py L403-413).
    """
    return StepResult(
        status="ok",
        data={
            "prompt": "test discovery prompt",
            "doc_path": str(tmp_path / "discovery.md"),
            "complexity": "FEATURE",
        },
        duration_ms=0,
        step_name="build_discovery_prompt",
    )


def _mock_invoke_ok() -> MagicMock:
    """Return a mock invoke_llm_subprocess that returns a valid StepResult."""
    sentinel_result = StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock_invoke",
    )
    return MagicMock(return_value=sentinel_result)


# ---------------------------------------------------------------------------
# AC1: cfg["hal_root"]=<tmpA>  → ensure_graph arg == str(resolved tmpA).
#
# §1y Point→Host→Test:
#   Point  = L394 (post-GREEN): graph_src = ensure_graph(str(_project_root))
#            where _project_root = resolve_project_root(cfg)[0]
#   Host   = _invoke_discovery_llm in phase_1_discovery
#   Test   = patch ensure_graph; capture positional arg; assert == resolved tmpA
#
# Pre-GREEN: FAIL — ensure_graph receives str(HAL_DIR) ≠ str(tmpA.resolve()).
# ---------------------------------------------------------------------------

def test_ac1_ensure_graph_receives_cfg_hal_root_E8A5ACAB(tmp_path):
    """AC1: _invoke_discovery_llm passes resolve_project_root(cfg) root to ensure_graph.

    With cfg["hal_root"]=<tmpA>, captured ensure_graph arg must equal
    str((tmp_path/"A").resolve()).

    Pre-GREEN FAIL: arg equals str(HAL_DIR) (hardcoded at L394), not cfg["hal_root"].
    """
    phase_1 = importlib.import_module("phase_1_discovery")

    target_dir = tmp_path / "A"
    target_dir.mkdir(parents=True, exist_ok=True)
    expected_root = str(target_dir.resolve())

    org_config = {
        "hal_root": str(target_dir),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_discovery(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL_GRAPH

    with patch("phase_1_discovery.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    assert len(ensure_graph_calls) == 1, (
        f"AC1 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1."
    )
    assert ensure_graph_calls[0] == expected_root, (
        f"AC1 FAIL: ensure_graph arg == {ensure_graph_calls[0]!r}, "
        f"expected {expected_root!r} (cfg['hal_root']).\n"
        "GREEN must replace ensure_graph(str(HAL_DIR)) with "
        "ensure_graph(str(resolve_project_root(cfg)[0]))."
    )


# ---------------------------------------------------------------------------
# AC2: cfg={"git_cwd": "/tmp/<unique-foreign>"} (no hal_root)
#      → ensure_graph arg == resolved foreign root AND != HAL_DIR.
#
# §1i: resolve_project_root emits; patch project_root.emit_resolver_resolved.
#
# Pre-GREEN: FAIL — arg == str(HAL_DIR), not foreign dir.
# ---------------------------------------------------------------------------

def test_ac2_foreign_git_cwd_not_hal_dir_E8A5ACAB(tmp_path):
    """AC2: For foreign cfg (git_cwd only), ensure_graph receives the foreign root.

    Captured arg must equal resolved git_cwd AND must NOT equal HAL_DIR.

    Pre-GREEN FAIL: arg is str(HAL_DIR) (hardcoded at L394), not foreign git_cwd.
    """
    phase_1 = importlib.import_module("phase_1_discovery")

    # Import project_root inside body (D1CF5FDF: deferred to assert time).
    import project_root as _pr_mod  # noqa: PLC0415

    foreign_dir = tmp_path / "foreignrepo"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    expected_root = str(foreign_dir.resolve())

    org_config = {
        # No "hal_root" key → resolver falls to git_cwd (precedence #2).
        "git_cwd": str(foreign_dir),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_discovery(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL_GRAPH

    # Neutralise resolver side-effect (§1i — disk write avoidance).
    with patch.object(_pr_mod, "emit_resolver_resolved", lambda *a, **k: None), \
         patch("phase_1_discovery.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    assert len(ensure_graph_calls) == 1, (
        f"AC2 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1."
    )
    captured = ensure_graph_calls[0]
    assert captured == expected_root, (
        f"AC2 FAIL: ensure_graph arg == {captured!r}, "
        f"expected {expected_root!r} (cfg['git_cwd']).\n"
        "GREEN must use resolve_project_root(cfg), not the hardcoded HAL_DIR."
    )
    # Import HAL_DIR here (inside body) to get the real value for the != check.
    from event_log import HAL_DIR as _hal_dir  # noqa: PLC0415
    assert captured != str(_hal_dir), (
        f"AC2 FAIL: ensure_graph received HAL_DIR ({str(_hal_dir)!r}) "
        "instead of the foreign git_cwd root."
    )


# ---------------------------------------------------------------------------
# AC3: BOTH hal_root and git_cwd present → arg derives from hal_root (precedence #1).
#
# §1i: patch emit_resolver_resolved on project_root module.
#
# Pre-GREEN: FAIL — arg is HAL_DIR, not hal_root tmp dir.
# ---------------------------------------------------------------------------

def test_ac3_hal_root_takes_precedence_over_git_cwd_E8A5ACAB(tmp_path):
    """AC3: Resolver precedence — hal_root beats git_cwd.

    cfg has BOTH keys; ensure_graph must receive resolved hal_root,
    not git_cwd and not HAL_DIR.

    Pre-GREEN FAIL: arg is str(HAL_DIR) (hardcoded), not the cfg hal_root tmp dir.
    """
    phase_1 = importlib.import_module("phase_1_discovery")

    import project_root as _pr_mod  # noqa: PLC0415

    hal_sim = tmp_path / "hal_sim"
    hal_sim.mkdir(parents=True, exist_ok=True)
    foreign_dir = tmp_path / "foreign_sim"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    expected_root = str(hal_sim.resolve())

    org_config = {
        "hal_root": str(hal_sim),
        "git_cwd": str(foreign_dir),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_discovery(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL_GRAPH

    with patch.object(_pr_mod, "emit_resolver_resolved", lambda *a, **k: None), \
         patch("phase_1_discovery.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    assert len(ensure_graph_calls) == 1, (
        f"AC3 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1."
    )
    captured = ensure_graph_calls[0]
    assert captured == expected_root, (
        f"AC3 FAIL: ensure_graph arg == {captured!r}, "
        f"expected hal_root {expected_root!r} (precedence #1).\n"
        "GREEN must use resolve_project_root(cfg) which returns cfg['hal_root'] first."
    )
    assert captured != str(foreign_dir.resolve()), (
        "AC3 FAIL: arg matched git_cwd, not hal_root — precedence #1 broken."
    )


# ---------------------------------------------------------------------------
# AC4: Source-grep the prod file for the import contract.
#
# Presence guards (B4D83B40 / feedback_assert_absence_red_needs_presence_gate):
#   PRESENT: "from project_root import resolve_project_root" AND "resolve_project_root("
#   ABSENT:  "from event_log import HAL_DIR"
#
# Pre-GREEN: FAIL —
#   • "from event_log import HAL_DIR" IS present (L54).
#   • "from project_root import resolve_project_root" is ABSENT (import not yet added).
# ---------------------------------------------------------------------------

def test_ac4_import_contract_in_phase1_source_E8A5ACAB():
    """AC4: phase_1_discovery.py source import contract.

    MUST CONTAIN:
      'from project_root import resolve_project_root'
      'resolve_project_root('
    MUST NOT CONTAIN:
      'from event_log import HAL_DIR'

    Pre-GREEN FAIL on absence of resolve_project_root import AND presence of HAL_DIR import.
    """
    import phase_1_discovery  # noqa: PLC0415
    source_path = Path(phase_1_discovery.__file__)
    source_text = source_path.read_text(encoding="utf-8")

    # Presence gate #1 — required import.
    assert "from project_root import resolve_project_root" in source_text, (
        f"AC4 FAIL: 'from project_root import resolve_project_root' NOT found in "
        f"{source_path}.\nGREEN must add this import."
    )

    # Presence gate #2 — required call site (guards the absence assertion below).
    assert "resolve_project_root(" in source_text, (
        f"AC4 FAIL: 'resolve_project_root(' call site NOT found in {source_path}.\n"
        "GREEN must call resolve_project_root(cfg) inside _invoke_discovery_llm."
    )

    # Absence assertion — guarded by the two presence assertions above.
    hal_dir_count = source_text.count("from event_log import HAL_DIR")
    assert hal_dir_count == 0, (
        f"AC4 FAIL: 'from event_log import HAL_DIR' found {hal_dir_count} time(s) "
        f"in {source_path}.\nGREEN must remove this host-coupled import (L54)."
    )


# ---------------------------------------------------------------------------
# AC5: _invoke_discovery_llm returns the invoke_llm_subprocess result unchanged.
#
# Mock invoke_llm_subprocess to return a sentinel StepResult; assert return value
# is that exact sentinel object.
#
# Pre-GREEN: PASS today (return is already threaded at L403). Guard for post-GREEN.
# ---------------------------------------------------------------------------

def test_ac5_returns_invoke_llm_subprocess_sentinel_E8A5ACAB(tmp_path):
    """AC5: _invoke_discovery_llm returns invoke_llm_subprocess result unchanged.

    mock invoke_llm_subprocess → sentinel StepResult; assert return value is sentinel.

    Expected to PASS today (prod already returns the subprocess result).
    Guard to ensure GREEN does not break this.
    """
    phase_1 = importlib.import_module("phase_1_discovery")

    org_config = {"scratchpad_dir": str(tmp_path)}
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_discovery(tmp_path)

    sentinel_result = StepResult(
        status="ok",
        data={"raw_response": "SENTINEL_RESPONSE", "response_bytes": 17, "command": ["sentinel"]},
        duration_ms=42,
        step_name="sentinel_step",
    )
    mock_invoke = MagicMock(return_value=sentinel_result)

    def fake_ensure_graph(repo_root: str) -> str:
        return _SENTINEL_GRAPH

    with patch("phase_1_discovery.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        result = phase_1._invoke_discovery_llm(ctx, prev)

    assert result is sentinel_result, (
        f"AC5 FAIL: _invoke_discovery_llm returned {result!r}, "
        f"expected sentinel {sentinel_result!r}.\n"
        "GREEN must not alter the return path from invoke_llm_subprocess."
    )
