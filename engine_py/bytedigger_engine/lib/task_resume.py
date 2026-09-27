"""Driver seam: resume a task's failed phase under the same run id, stop when a
human is needed, and cap each task at a number of runs and dollars (bd#85).

Pure-stdlib and deterministic. The event log is the source of truth for what
each phase did; a small per-run ledger next to it counts driver runs.

Decision rules (spec: docs/decisions/2026-09-27-bd85-retry-budgets-phase-resume.md §4):

* A phase's row is its last authoritative ``workflow_finished`` row. Shadowed
  zombie rows have a different event type and are never read.
* Walking ``phases`` in order, a row is *fresh* when it comes after the fresh
  row of every earlier phase — a rerun of an earlier phase makes later rows
  stale. ``completed`` is the leading run of phases whose fresh row is ok/skip.
* The first phase not completed decides: no fresh row → resume there; a fresh
  stop-class row → stop; ``E_SPEC_DEFECT`` → reroute to phase_45_spec; any
  other fresh row (error, paused) → resume there. A stop or reroute row older
  than the latest ``task_cap_reset`` row counts as a plain error.

The ledger fails closed (a spend cap), unlike the restart governor, which is a
waste limiter and fails open.
"""
from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from bytedigger_engine.event_log import EventLog
from bytedigger_engine.io_utils import atomic_write
from bytedigger_engine.lib.cost_rollup import compute_cost_rollup

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX host: begins are not serialized
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "DEFAULT_MAX_RUNS",
    "DEFAULT_MAX_COST_USD",
    "ResumePlan",
    "TaskBegin",
    "TaskLogUnreadable",
    "plan_resume",
    "begin_task_run",
    "reset_task_runs",
]

DEFAULT_MAX_RUNS = 3
DEFAULT_MAX_COST_USD = 60.0

REROUTE_TARGET = "phase_45_spec"
_DONE_STATUSES = ("ok", "skip")
_STOP_CODES = frozenset({
    "E_RED_WORKTREE_DIRTY",
    "E_SPEC_DEFECT_BUDGET",
    # Restart-governor codes are defensive: governor denials emit no workflow_finished.
    "E_RESTART_CAP",
    "E_RESTART_SHORT_CIRCUIT",
})
_REROUTE_CODE = "E_SPEC_DEFECT"
_COST_EVENT_TYPES = ("subprocess_exited", "runner_result_consumed")
_RESET_EVENT = "task_cap_reset"


class TaskLogUnreadable(OSError):
    """The event log exists but cannot be read."""


@dataclass(frozen=True)
class ResumePlan:
    action: str  # "resume" | "done" | "stop" | "reroute"
    resume_from: str | None
    completed: list[str] = field(default_factory=list)
    last_status: str | None = None
    last_row: int | None = None


@dataclass(frozen=True)
class TaskBegin:
    allowed: bool
    action: str
    resume_from: str | None
    completed: list[str]
    runs: int
    cost_usd: float
    cost_unknown_calls: int
    error_code: str | None = None


def _read_rows(events_path: Path | str) -> list[dict[str, Any]]:
    path = Path(events_path)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TaskLogUnreadable(str(exc)) from exc
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _is_stop(status: str | None, code: str | None) -> bool:
    if status == "escalate":
        return True
    return code in _STOP_CODES


def _plan(rows: list[dict[str, Any]], run_id: str, phases: list[str]) -> ResumePlan:
    last: dict[str, tuple[int, dict[str, Any]]] = {}
    last_reset = -1
    for idx, row in enumerate(rows):
        if row.get("run_id") != run_id:
            continue
        payload = row.get("payload") or {}
        if row.get("event_type") == _RESET_EVENT:
            last_reset = idx
        elif row.get("event_type") == "workflow_finished" and payload.get("workflow_name") in phases:
            last[payload["workflow_name"]] = (idx, payload)

    completed: list[str] = []
    fresh_after = -1
    for phase in phases:
        entry = last.get(phase)
        if entry is None or entry[0] <= fresh_after:
            return ResumePlan("resume", phase, completed)
        idx, payload = entry
        status = payload.get("status")
        if status in _DONE_STATUSES:
            completed.append(phase)
            fresh_after = idx
            continue
        code = payload.get("error_code")
        overridden = idx < last_reset
        if _is_stop(status, code) and not overridden:
            return ResumePlan("stop", None, completed, status, idx)
        if code == _REROUTE_CODE and not overridden:
            return ResumePlan("reroute", REROUTE_TARGET, completed, status, idx)
        return ResumePlan("resume", phase, completed, status, idx)
    return ResumePlan("done", None, completed)


def plan_resume(events_path: Path | str, run_id: str, phases: list[str]) -> ResumePlan:
    """Decide where a driver continues the task ``run_id``. Raises
    ``TaskLogUnreadable`` when the log exists but cannot be read."""
    return _plan(_read_rows(events_path), run_id, list(phases))


def _ledger_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / f"task-runs-{run_id}.json"


@contextlib.contextmanager
def _locked(state_dir: Path, run_id: str) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(_ledger_path(state_dir, run_id).with_suffix(".lock"), "a") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _load_ledger(path: Path) -> dict[str, Any] | None:
    """The ledger, a fresh one when absent, or None when it is corrupt."""
    if not path.exists():
        return {"runs": 0, "credited_row": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("runs"), int):
        return None
    data.setdefault("credited_row", None)
    return data


def _unknown_cost_calls(rows: list[dict[str, Any]], run_id: str) -> int:
    return sum(
        1 for r in rows
        if r.get("run_id") == run_id and r.get("event_type") in _COST_EVENT_TYPES
        and (r.get("payload") or {}).get("cost_usd") is None
    )


def begin_task_run(
    state_dir: Path | str,
    run_id: str,
    events_path: Path | str,
    phases: list[str],
    max_runs: int = DEFAULT_MAX_RUNS,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
) -> TaskBegin:
    """Admit (and count) one driver run of the task, or refuse it.

    Only a ``resume`` plan is admitted and charged. The cost cap always
    applies; a paused row not credited before waives the runs cap and the
    charge once. A refusal records nothing.
    """
    state = Path(state_dir)
    ledger_path = _ledger_path(state, run_id)
    with _locked(state, run_id):
        ledger = _load_ledger(ledger_path)
        if ledger is None:
            return TaskBegin(False, "stop", None, [], 0, 0.0, 0, "E_TASK_LEDGER_CORRUPT")
        runs = ledger["runs"]
        try:
            rows = _read_rows(events_path)
        except TaskLogUnreadable:
            return TaskBegin(False, "stop", None, [], runs, 0.0, 0, "E_TASK_COST_UNREADABLE")
        plan = _plan(rows, run_id, list(phases))
        cost = float(compute_cost_rollup(events_path, run_id)["cost_usd"])
        unknown = _unknown_cost_calls(rows, run_id)

        def _result(allowed: bool, runs_now: int, code: str | None = None) -> TaskBegin:
            return TaskBegin(allowed, plan.action, plan.resume_from, plan.completed,
                             runs_now, cost, unknown, code)

        if plan.action != "resume":
            return _result(False, runs)
        if cost >= max_cost_usd:
            return _result(False, runs, "E_TASK_CAP_REACHED")
        if plan.last_status == "paused" and plan.last_row != ledger["credited_row"]:
            ledger["credited_row"] = plan.last_row
            atomic_write(ledger_path, json.dumps(ledger))
            return _result(True, runs)
        if runs >= max_runs:
            return _result(False, runs, "E_TASK_CAP_REACHED")
        ledger["runs"] = runs + 1
        atomic_write(ledger_path, json.dumps(ledger))
        return _result(True, runs + 1)


def reset_task_runs(state_dir: Path | str, run_id: str, events_path: Path | str, reason: str) -> None:
    """Operator escape: forget the task's run count and record why. The reset
    row also turns an earlier stop or reroute into a plain error."""
    state = Path(state_dir)
    with _locked(state, run_id):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(_ledger_path(state, run_id))
        EventLog(events_path).append(_RESET_EVENT, {"reason": reason}, run_id)
