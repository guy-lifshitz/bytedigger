"""graph_source — discovery source resolver for graphify-aware builds (DA48BEAC).

Exposes ``resolve_graph_or_grep(path)`` which consults ``graphify check-update``
and returns the canonical source token:

  "graph"  — graph is current; callers should use graph-based discovery.
  "grep"   — graph is stale; callers fall back to grep-based discovery.
             Exactly ONE ``graph_stale`` telemetry event is emitted on this path.

Frozen literals (DF-7):
  module:  graph_source
  fn:      resolve_graph_or_grep
  returns: "graph" | "grep"
  event:   graph_stale
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.lib.bounded_spawn import bounded_run

logger = logging.getLogger(__name__)


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry via active run context; swallow all errors.
    Mirrors the same helper pattern used in phase_1_discovery.py."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, exc)


def _graph_paths(repo_root: str) -> tuple[Path, Path]:
    """Return (scan_root, graph_json_abs) for the engine_py graph.

    scan_root      = <repo_root>/SYSTEM/cli/build
    graph_json_abs = <scan_root>/graphify-out/graph.json
    """
    scan_root = Path(repo_root) / "SYSTEM" / "cli" / "build"
    graph_json = scan_root / "graphify-out" / "graph.json"
    return (scan_root, graph_json)


def _run_graphify_update(scan_root: str) -> bool:
    """Run ``graphify update <scan_root> --no-cluster`` (deterministic, no LLM).

    Return True iff returncode == 0. Catch FileNotFoundError/OSError/TimeoutExpired
    → return False. Use a bounded timeout (120 s).
    """
    try:
        result = subprocess.run(
            ["graphify", "update", scan_root, "--no-cluster"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def ensure_graph(repo_root: str) -> str:
    """Ensure a usable graph exists for the engine_py scan-root, building it on demand.

    Returns "graph" (graph available for graph-first) or "grep" (genuine fallback).

    Behaviour:
      - Pin GRAPHIFY_OUT (absolute) so the later env-inherited ``graphify query``
        resolves to THIS graph.json regardless of subprocess cwd.
      - graph.json present  → return "graph" (no rebuild, no emit).
      - graph.json absent   → emit graph_stale + ``graphify update`` → present? "graph" : "grep".
      - graphify binary absent (FileNotFoundError) → graph_stale emitted (absence) + "grep".
    """
    scan_root, graph_json = _graph_paths(repo_root)
    os.environ["GRAPHIFY_OUT"] = str(scan_root / "graphify-out")
    if graph_json.exists():
        return "graph"
    _emit_safe("graph_stale", {"path": str(scan_root)})
    if _run_graphify_update(str(scan_root)) and graph_json.exists():
        return "graph"
    return "grep"


def _check_update_needs_update(path: str) -> bool:
    """Run ``graphify check-update <path>`` and return True if needs_update."""
    result = bounded_run(
        ["graphify", "check-update", path],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
        return bool(data.get("needs_update", False))
    except (json.JSONDecodeError, AttributeError):
        # If we can't parse the output, treat as dirty (safe fallback).
        return True


def resolve_graph_or_grep(path: str) -> str:
    """Return ``"graph"`` if the graphify graph is current for *path*, else ``"grep"``.

    On the dirty path emits exactly ONE ``graph_stale`` event to the active
    telemetry run context (DF-7 frozen literal).  On the clean path emits nothing.

    §1aa named sourceable helper — satisfies §1y Point→Host→Test-path trace:
      Point = the two return branches below.
      Host  = this function (importable from graph_source).
      Test  = RED test patches subprocess.run to inject clean/dirty JSON output.
    """
    dirty = _check_update_needs_update(path)
    if dirty:
        _emit_safe("graph_stale", {"path": path})
        return "grep"
    return "graph"
