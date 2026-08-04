"""Pure state derivation from event log.

Stage 1c: replays a list of events (from `event_log.EventLog.read_all()`)
into a derived state dict. No mutation, no I/O — engine writes events,
readers (status CLI, hooks, dashboards) call `replay()` to get the
current view.

Event types this derivation understands (engine.py emits these):
    workflow_started   payload: {workflow_name}
    step_started       payload: {step_name}
    step_finished      payload: {step_name, status, duration_ms, error?}
    workflow_finished  payload: {status}

Unknown event types are recorded but ignored for state shape — forward
compat with new event kinds added later.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from bytedigger_engine.event_log import default_log_path
from bytedigger_engine.execution_provenance import SHADOW_EVENT_TYPE


class StepState(TypedDict, total=False):
    step_name: str
    status: str           # "running" | "ok" | "error" | "skip"
    started_at: str
    finished_at: str
    duration_ms: int
    error: str


class RunState(TypedDict, total=False):
    run_id: str
    workflow_name: str
    status: str           # "running" | "ok" | "error"
    started_at: str
    finished_at: str
    steps: list[StepState]


class DerivedState(TypedDict, total=False):
    runs: dict[str, RunState]
    last_event_ts: str | None
    unknown_events: int
    subprocess_summary: dict[str, Any] | None  # decree 2026-04-26 cat A
    files_touched_summary: dict[str, Any] | None  # decree 2026-04-26 cat B
    shadow_events: int  # GH700: count of engine_shadow_emit events (only when > 0)


def replay(events: list[dict[str, Any]]) -> DerivedState:
    """Fold an event list into the current derived state."""
    runs: dict[str, RunState] = {}
    unknown = 0
    last_ts: str | None = None

    # Decree 2026-04-26 aggregation buckets
    sp_count = 0
    sp_total_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    sp_total_cost = 0.0
    sp_by_model: dict[str, dict] = {}
    sp_by_phase: dict[str, dict] = {}
    sp_pending_model: dict[int, str | None] = {}  # pid → model from spawned event
    sp_pending_phase: dict[int, str | None] = {}

    ft_by_phase: dict[str, list[str]] = {}
    ft_total = 0
    ft_added = 0
    ft_modified = 0
    ft_deleted = 0
    ft_any = False
    shadow_count = 0

    for evt in events:
        last_ts = evt.get("ts", last_ts)
        run_id = evt.get("run_id", "ad-hoc")
        kind = evt.get("event_type")
        payload = evt.get("payload", {}) or {}

        run = runs.setdefault(run_id, {"run_id": run_id, "steps": []})

        if kind == "workflow_started":
            run["workflow_name"] = payload.get("workflow_name", "")
            run["started_at"] = evt.get("ts", "")
            run["status"] = "running"
        elif kind == "step_started":
            run["steps"].append(
                {
                    "step_name": payload.get("step_name", ""),
                    "status": "running",
                    "started_at": evt.get("ts", ""),
                }
            )
        elif kind == "step_finished":
            step_name = payload.get("step_name", "")
            step = _find_open_step(run, step_name)
            if step is None:
                step = {"step_name": step_name, "started_at": evt.get("ts", "")}
                run["steps"].append(step)
            step["status"] = payload.get("status", "ok")
            step["finished_at"] = evt.get("ts", "")
            if "duration_ms" in payload:
                step["duration_ms"] = payload["duration_ms"]
            if payload.get("error"):
                step["error"] = payload["error"]
        elif kind == "workflow_finished":
            run["status"] = payload.get("status", "ok")
            run["finished_at"] = evt.get("ts", "")
        elif kind == "subprocess_spawned":
            pid = payload.get("pid")
            if isinstance(pid, int):
                sp_pending_model[pid] = payload.get("model")
                sp_pending_phase[pid] = payload.get("phase")
        elif kind == "subprocess_exited":
            sp_count += 1
            tokens = payload.get("tokens") or {}
            for k_in, k_agg in (
                ("input", "input"),
                ("output", "output"),
                ("cache_read", "cache_read"),
                ("cache_write", "cache_write"),
            ):
                v = tokens.get(k_in)
                if isinstance(v, int):
                    sp_total_tokens[k_agg] += v
            cost = payload.get("cost_usd")
            if isinstance(cost, (int, float)):
                sp_total_cost += float(cost)
            pid = payload.get("pid")
            model = sp_pending_model.pop(pid, None) if isinstance(pid, int) else None
            phase = sp_pending_phase.pop(pid, None) if isinstance(pid, int) else payload.get("phase")
            phase = phase or payload.get("phase")
            if model:
                m = sp_by_model.setdefault(model, {"count": 0, "cost_usd": 0.0})
                m["count"] += 1
                if isinstance(cost, (int, float)):
                    m["cost_usd"] += float(cost)
            if phase:
                p = sp_by_phase.setdefault(phase, {"count": 0, "cost_usd": 0.0})
                p["count"] += 1
                if isinstance(cost, (int, float)):
                    p["cost_usd"] += float(cost)
        elif kind == "runner_result_consumed":
            # F5787804: fold in-session backend cost/tokens into the same accumulators
            # as subprocess_exited so headline total_cost_usd is backend-agnostic.
            sp_count += 1
            cost = payload.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                sp_total_cost += float(cost)
            # Flat token keys (vs subprocess's nested "tokens" dict)
            ti = payload.get("tokens_in")
            to = payload.get("tokens_out")
            if isinstance(ti, int) and not isinstance(ti, bool):
                sp_total_tokens["input"] += ti
            if isinstance(to, int) and not isinstance(to, bool):
                sp_total_tokens["output"] += to
            # by_model / by_phase keyed directly on flat payload fields
            model = payload.get("model")
            phase = payload.get("phase")
            if model:
                m = sp_by_model.setdefault(model, {"count": 0, "cost_usd": 0.0})
                m["count"] += 1
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                    m["cost_usd"] += float(cost)
            if phase:
                p = sp_by_phase.setdefault(phase, {"count": 0, "cost_usd": 0.0})
                p["count"] += 1
                if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                    p["cost_usd"] += float(cost)
        elif kind == "files_touched":
            ft_any = True
            phase = payload.get("phase") or ""
            paths = payload.get("paths") or []
            if phase:
                ft_by_phase.setdefault(phase, []).extend(paths)
            ft_total += len(paths)
            by_status = payload.get("by_status") or {}
            ft_added += len(by_status.get("A", []))
            ft_modified += len(by_status.get("M", []))
            ft_deleted += len(by_status.get("D", []))
        elif kind == SHADOW_EVENT_TYPE:
            # GH700 §2.4: a shadowed (non-authoritative) execution event.
            # Never touches run["status"] / run["steps"] — a DBOS-recovery
            # zombie's completion must not overwrite a live run's state.
            shadow_count += 1
        else:
            unknown += 1

    sp_summary: dict[str, Any] | None = None
    if sp_count > 0:
        sp_summary = {
            "count": sp_count,
            "total_tokens": sp_total_tokens,
            "total_cost_usd": round(sp_total_cost, 6),
            "by_model": sp_by_model,
            "by_phase": sp_by_phase,
        }

    ft_summary: dict[str, Any] | None = None
    if ft_any:
        # Dedupe per-phase paths
        for k in list(ft_by_phase):
            ft_by_phase[k] = list(dict.fromkeys(ft_by_phase[k]))
        ft_summary = {
            "by_phase": ft_by_phase,
            "total_files": ft_total,
            "total_files_added": ft_added,
            "total_files_modified": ft_modified,
            "total_files_deleted": ft_deleted,
        }

    out: DerivedState = {
        "runs": runs,
        "last_event_ts": last_ts,
        "unknown_events": unknown,
    }
    # Decree 2026-04-26: only include summaries when telemetry events present
    # (preserves backward-compat for callers that asserted the legacy 3-key shape).
    if sp_summary is not None:
        out["subprocess_summary"] = sp_summary
    if ft_summary is not None:
        out["files_touched_summary"] = ft_summary
    if shadow_count > 0:
        out["shadow_events"] = shadow_count
    return out


def _find_open_step(run: RunState, step_name: str) -> StepState | None:
    """Return the most recent step with this name still in 'running' state, or None."""
    for step in reversed(run.get("steps", [])):
        if step.get("step_name") == step_name and step.get("status") == "running":
            return step
    return None


def query_run_events(
    events_or_path: list[dict[str, Any]] | str | Path | None = None,
    *,
    run_id: str | None = None,
    phase: str | None = None,
    step: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    """Filter events by run_id / phase / step / event_type (all AND'd).

    Source resolution for ``events_or_path``:
        - None        → load from ``event_log.default_log_path()``
        - str / Path  → JSONL path; missing file yields ``[]`` (no raise)
        - list[dict]  → use as-is

    Filter semantics:
        ``phase`` matches when ``payload.workflow_name == phase`` OR
        ``payload.phase == phase`` — covers both workflow_started/finished
        (which carry workflow_name) and subprocess/files_touched events
        (which carry payload.phase). Each phase invocation has its own
        run_id, so cross-phase queries against the shared build-events
        log filter on phase rather than run_id.

        ``step`` matches ``payload.step_name``.

    Returns events in original log order. Wired into phase_7_synthesize
    so the synthesizer can read per-step durations + cost without
    grepping the JSONL by hand (agreement 9AB90CA0).
    """
    events = _load_events(events_or_path)
    if not (run_id or phase or step or event_type):
        return events

    out: list[dict[str, Any]] = []
    for evt in events:
        if run_id is not None and evt.get("run_id") != run_id:
            continue
        if event_type is not None and evt.get("event_type") != event_type:
            continue
        payload = evt.get("payload") or {}
        if phase is not None and payload.get("workflow_name") != phase and payload.get("phase") != phase:
            continue
        if step is not None and payload.get("step_name") != step:
            continue
        out.append(evt)
    return out


def _load_events(source: list[dict[str, Any]] | str | Path | None) -> list[dict[str, Any]]:
    if source is None:
        path = default_log_path()
    elif isinstance(source, (str, Path)):
        path = Path(source)
    else:
        return list(source)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            events.append(json.loads(stripped))
    return events
