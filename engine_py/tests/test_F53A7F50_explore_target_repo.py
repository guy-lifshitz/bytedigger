"""RED tests for F53A7F50 — phase_2_explore graphs the target repo, not HAL_DIR.

UUT: phase_2_explore._invoke_explore_llm.
Secondary UUT: phase_2_explore.resolve_project_root (AC6 — existence gate).

Pre-GREEN predict:
  AC1: FAIL — captured ensure_graph arg == str(HAL_DIR), not str(tmp_path/"A")
  AC2: FAIL — same root cause (HAL_DIR hardcoded, not read from cfg["hal_root"])
  AC3: FAIL — AttributeError: module 'phase_2_explore' has no attribute
               'resolve_project_root' (real resolver not yet bound) OR arg==HAL_DIR
  AC4: FAIL — "HAL_DIR" still present in phase_2_explore source (L69 + L346)
  AC5: PASS today — ensure_graph return already threads into extra_data["graph_source"]
       (verified at L346-356 of current prod); guard for post-GREEN contract.
  AC6: FAIL — phase_2_explore has no attribute 'resolve_project_root' today.

§1q: conftest-import-time singleton provides ENGINE_ROOT + workflows on sys.path.
     No sys.path.insert here (81F97F3D gate).
D1CF5FDF: phase module imported via importlib.import_module inside test bodies so
     missing-attribute failures occur at assert time, not collection time.
§1l stub-passability: ensure_graph patched (collaborator, not UUT). The UUT is the
     resolve_project_root-call + arg-threading wiring that does NOT exist yet.
§1i/emit-note: resolve_project_root calls emit_resolver_resolved from project_root's
     namespace. Patched in every test that lets the real resolver run (AC3) to
     prevent disk writes. AC1/AC2/AC5 patch ensure_graph before resolver call site
     is reached; emit is never invoked there today (HAL_DIR hardcoded path runs
     instead). After GREEN they will invoke the resolver — AC3 explicitly patches it.
§1v: only test_F53A7F50_explore_target_repo.py created; phase_2_explore.py untouched.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py inserts ENGINE_ROOT + ENGINE_ROOT/workflows at import time.
# No sys.path.insert here (§1q / 81F97F3D gate).

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402  # available via conftest path

# ---------------------------------------------------------------------------
# Shared sentinel
# ---------------------------------------------------------------------------

_SENTINEL = "F53A7F50_SENTINEL"  # returned by patched ensure_graph


# ---------------------------------------------------------------------------
# Shared helpers (mirroring EEFC4611 conventions)
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path: Path, org_config: dict | None = None) -> WorkflowContext:
    """Build a WorkflowContext with the given org_config (or empty dict)."""
    (tmp_path / "injection").mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config if org_config is not None else {"scratchpad_dir": str(tmp_path)},
        question="test question for F53A7F50 target-repo graph",
        session_id="test-F53A7F50",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_prev_explore(tmp_path: Path) -> StepResult:
    """Build a non-skipped prev StepResult that passes _invoke_explore_llm validation.

    Must NOT have data["skipped"] truthy (passthrough_if_skipped guard).
    Mirrors EEFC4611 L71-84.
    """
    return StepResult(
        status="ok",
        data={
            "prompt": "test explore prompt",
            "doc_path": str(tmp_path / "exploration.md"),
            "complexity": "FEATURE",
        },
        duration_ms=0,
        step_name="build_explore_prompt",
    )


def _mock_invoke_ok() -> MagicMock:
    """Return a mock invoke_llm_subprocess that returns a valid StepResult."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock",
    ))


# ---------------------------------------------------------------------------
# AC1: ensure_graph receives resolve_project_root(cfg) root, not HAL_DIR.
#      cfg["hal_root"] = str(tmp_path/"A") → captured arg == str(resolved tmp_path/"A")
#
# §1y Point→Host→Test:
#   Point  = L346 (after GREEN): graph_src = ensure_graph(str(_project_root))
#            where _project_root comes from resolve_project_root(cfg)
#   Host   = _invoke_explore_llm in phase_2_explore
#   Test   = patch ensure_graph capture-arg; patch invoke_llm_subprocess; assert arg
#
# Pre-GREEN FAIL: captured arg == str(HAL_DIR), not str(tmp_path/"A").
# ---------------------------------------------------------------------------

def test_ac1_ensure_graph_receives_cfg_hal_root_F53A7F50(tmp_path):
    """AC1: _invoke_explore_llm passes resolve_project_root(cfg) root to ensure_graph.

    With cfg["hal_root"]=<tmpA>, captured ensure_graph arg must equal
    str((tmp_path/"A").resolve()).

    Pre-GREEN FAIL: arg equals str(HAL_DIR) (hardcoded), not cfg["hal_root"].
    """
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    target_dir = tmp_path / "A"
    target_dir.mkdir(parents=True, exist_ok=True)
    expected_root = str(target_dir.resolve())

    org_config = {
        "hal_root": str(target_dir),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_explore(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL

    with patch("bytedigger_engine.workflows.phase_2_explore.ensure_graph", side_effect=fake_ensure_graph), \
         patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

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
# AC2: HAL byte-identical — cfg["hal_root"]=<haldir> → captured arg == str(<haldir>)
#      Confirms precedence #1 preserves HAL-build behaviour after the repoint.
#
# Pre-GREEN FAIL: same root cause — arg is str(HAL_DIR) but the comparison value
#   is a tmp dir path, so they are DIFFERENT strings → assertion fails.
#   (Note: HAL_DIR itself is a real path; tmp_path/"hal" is a fresh tmp dir
#   — we must assert that the CAPTURED arg equals what cfg["hal_root"] resolves to,
#   not what HAL_DIR resolves to, so the test FAILS pre-GREEN when arg==HAL_DIR.)
# ---------------------------------------------------------------------------

def test_ac2_hal_byte_identical_via_cfg_hal_root_F53A7F50(tmp_path):
    """AC2: HAL byte-identical — cfg["hal_root"] value is returned unchanged.

    Simulates a HAL build: cfg["hal_root"]=<hal_sim> → ensure_graph arg == resolved path.
    Uses a tmp directory (not the real HAL dir) to keep the test hermetic.

    Pre-GREEN FAIL: captured arg is str(HAL_DIR) ≠ str(tmp_path/"hal").resolve().
    """
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    hal_sim = tmp_path / "hal"
    hal_sim.mkdir(parents=True, exist_ok=True)
    expected_root = str(hal_sim.resolve())

    org_config = {
        "hal_root": str(hal_sim),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_explore(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL

    with patch("bytedigger_engine.workflows.phase_2_explore.ensure_graph", side_effect=fake_ensure_graph), \
         patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    assert len(ensure_graph_calls) == 1, (
        f"AC2 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1."
    )
    assert ensure_graph_calls[0] == expected_root, (
        f"AC2 FAIL: ensure_graph arg == {ensure_graph_calls[0]!r}, "
        f"expected {expected_root!r} (cfg['hal_root'] precedence #1).\n"
        "GREEN must use resolve_project_root(cfg) which returns cfg['hal_root'] first."
    )


# ---------------------------------------------------------------------------
# AC3: Decouple proof — cfg has NO hal_root; cfg["git_cwd"]=<tmpRepo>;
#      real resolve_project_root runs (NOT stubbed); assert captured arg == str(tmpRepo)
#      AND "/.claude" not in captured arg.
#
# §1i: singleton-resource: resolve_project_root calls emit_resolver_resolved (disk write).
#      patch project_root.emit_resolver_resolved to no-op.
# D1CF5FDF: import bytedigger_engine.lib.project_root inside test body (deferred) so missing-module is
#      assert-time, not collect-time.
#
# Pre-GREEN FAIL: AttributeError — phase_2_explore has no attribute 'resolve_project_root'
#   OR (if somehow reachable) arg == str(HAL_DIR) which contains "/.claude".
# ---------------------------------------------------------------------------

def test_ac3_decouple_proof_git_cwd_not_hal_dir_F53A7F50(tmp_path):
    """AC3: Foreign-repo decouple — cfg["git_cwd"]=<tmpRepo>, no hal_root.

    Real resolve_project_root runs (not stubbed). Captured ensure_graph arg must:
      (a) == str(tmpRepo.resolve())
      (b) not contain '/.claude'

    Pre-GREEN FAIL: AttributeError (resolve_project_root not yet in phase_2_explore)
    OR arg equals str(HAL_DIR) which contains '/.claude'.
    """
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    # Import project_root inside test body (D1CF5FDF: deferred import, not collect-time).
    from bytedigger_engine.lib import project_root as _project_root_mod  # noqa: PLC0415

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    expected_root = str(repo_dir.resolve())

    org_config = {
        # No "hal_root" key — forces precedence #2 (git_cwd).
        "git_cwd": str(repo_dir),
        "scratchpad_dir": str(tmp_path),
    }
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_explore(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls: list[str] = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL

    # Neutralize emit_resolver_resolved from project_root's namespace (§1i/emit-note).
    with patch.object(_project_root_mod, "emit_resolver_resolved", lambda *a, **k: None), \
         patch("bytedigger_engine.workflows.phase_2_explore.ensure_graph", side_effect=fake_ensure_graph), \
         patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    assert len(ensure_graph_calls) == 1, (
        f"AC3 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1."
    )
    captured = ensure_graph_calls[0]
    assert captured == expected_root, (
        f"AC3 FAIL: ensure_graph arg == {captured!r}, "
        f"expected {expected_root!r} (cfg['git_cwd']).\n"
        "GREEN must use resolve_project_root(cfg) — not HAL_DIR — for foreign repos."
    )
    assert "/.claude" not in captured, (
        f"AC3 FAIL: captured arg {captured!r} contains '/.claude' — "
        "still resolving to HAL dir. GREEN must decouple from event_log.HAL_DIR."
    )


# ---------------------------------------------------------------------------
# AC4: Source grep — "HAL_DIR" has 0 occurrences in phase_2_explore.py after GREEN.
#
# §1l anti-pattern B4D83B40: assert behavior via source text only when the AC
#   explicitly is a source-deletion check (the spec says "grep source → 0 occurrences").
#   This is a structural AC per §3, not a behavior-proxy.
#
# Pre-GREEN FAIL: "HAL_DIR" found at L69 (import) and L346 (usage) → count >= 2.
# ---------------------------------------------------------------------------

def test_ac4_hal_dir_removed_from_source_F53A7F50():
    """AC4: phase_2_explore.py must contain 0 occurrences of 'HAL_DIR' after GREEN.

    Pre-GREEN FAIL: 'HAL_DIR' present at L69 (import) + L346 (ensure_graph call).
    """
    from bytedigger_engine.workflows import phase_2_explore  # noqa: PLC0415
    source_path = Path(phase_2_explore.__file__)
    source_text = source_path.read_text(encoding="utf-8")
    occurrences = source_text.count("HAL_DIR")
    assert occurrences == 0, (
        f"AC4 FAIL: 'HAL_DIR' found {occurrences} time(s) in {source_path}.\n"
        "GREEN must remove the `from bytedigger_engine.event_log import HAL_DIR` import (L69) "
        "and the `ensure_graph(str(HAL_DIR))` call (L346)."
    )


# ---------------------------------------------------------------------------
# AC5: extra_data["graph_source"] threading preserved — ensure_graph's return value
#      must be forwarded into extra_data["graph_source"].
#
# Mirroring EEFC4611 AC9 pattern (L182-227). This AC is already satisfied by
# current prod (L346-356 thread graph_src into extra_data) — expected to PASS today.
# Guard contract: must remain green post-GREEN.
#
# Pre-GREEN: PASS (current prod already threads graph_src at L352-355).
# ---------------------------------------------------------------------------

def test_ac5_graph_source_threaded_into_extra_data_F53A7F50(tmp_path):
    """AC5: ensure_graph return is forwarded into extra_data["graph_source"].

    patch ensure_graph→sentinel; patch invoke_llm_subprocess to capture kwargs;
    assert extra_data["graph_source"] == sentinel.

    Expected to PASS today (threading already present in prod). Guard for post-GREEN.
    """
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    org_config = {"scratchpad_dir": str(tmp_path)}
    ctx = _make_ctx(tmp_path, org_config=org_config)
    prev = _ok_prev_explore(tmp_path)

    mock_invoke = _mock_invoke_ok()

    def fake_ensure_graph(repo_root: str) -> str:
        return _SENTINEL

    with patch("bytedigger_engine.workflows.phase_2_explore.ensure_graph", side_effect=fake_ensure_graph), \
         patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "AC5 FAIL: invoke_llm_subprocess was never called."
    )
    _, kwargs = mock_invoke.call_args_list[0]
    extra_data = kwargs.get("extra_data", {})
    assert "graph_source" in extra_data, (
        f"AC5 FAIL: extra_data missing 'graph_source' key.\n"
        f"Current extra_data keys: {list(extra_data.keys())}"
    )
    assert extra_data["graph_source"] == _SENTINEL, (
        f"AC5 FAIL: extra_data['graph_source'] == {extra_data['graph_source']!r}, "
        f"expected sentinel {_SENTINEL!r}."
    )


# ---------------------------------------------------------------------------
# AC6: resolve_project_root bound in phase_2_explore namespace (patchable post-GREEN).
#
# Pre-GREEN FAIL: AttributeError — resolve_project_root not imported into phase_2_explore.
# ---------------------------------------------------------------------------

def test_ac6_resolve_project_root_in_namespace_F53A7F50():
    """AC6: phase_2_explore.resolve_project_root exists (imported, patchable).

    Pre-GREEN FAIL: hasattr returns False — symbol not yet bound in module namespace.
    GREEN must add `from bytedigger_engine.lib.project_root import resolve_project_root` to phase_2_explore.py.
    """
    from bytedigger_engine.workflows import phase_2_explore  # noqa: PLC0415
    assert hasattr(phase_2_explore, "resolve_project_root"), (
        "AC6 FAIL: phase_2_explore.resolve_project_root does not exist.\n"
        "GREEN must replace `from bytedigger_engine.event_log import HAL_DIR` with "
        "`from bytedigger_engine.lib.project_root import resolve_project_root` (L69)."
    )
