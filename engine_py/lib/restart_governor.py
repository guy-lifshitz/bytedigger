"""Restart governor — waste-limiter for unbounded phase re-invocation (GH374 Class 3).

Pure-stdlib, core-clean (core-boundary lint applies): no host environment-prefixed
reads, no host path literals, no host-project string refs. Config is passed in
via `org_config`.

State file per (run_id, workflow): `<state_dir>/restart-governor-<run_id>-<workflow>.json`.

GH625: state schema v2 splits the single `starts` counter into
`crash_starts` (engine crashes — a start that never got a matching result,
classified at close time) and `gate_starts` (legitimate gate-retry
re-invocations), plus a `pending` flag marking an open (unclosed) start.
Shape: `{"schema": 2, "crash_starts": int, "gate_starts": int,
"pending": bool, "error_codes": [str|null, ...], "sc_codes": [str, ...]}`
(last 5 error_codes/sc_codes kept). `sc_codes` (GH635) tracks only
non-recoverable error codes, used by the short-circuit no-progress check
(recoverable codes are exempt, bounded by gate_cap instead).
Legacy shape `{"starts": int, "error_codes": [...]}` (no "schema" key)
transparently migrates on load.

Governor is a waste-limiter, not a security gate: corrupt/missing state
self-heals to "allow" and any internal crash fails open (never blocks a
build on its own bug).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from io_utils import atomic_write

logger = logging.getLogger(__name__)

_MAX_ERROR_CODES = 5


def _state_file(state_dir: Path, run_id: str, workflow: str) -> Path:
    return Path(state_dir) / f"restart-governor-{run_id}-{workflow}.json"


def _fresh_state() -> dict:
    return {
        "schema": 2,
        "crash_starts": 0,
        "gate_starts": 0,
        "pending": False,
        "error_codes": [],
        "sc_codes": [],
    }


def _load_state(state_dir: Path, run_id: str, workflow: str) -> dict:
    path = _state_file(state_dir, run_id, workflow)
    if not path.exists():
        return _fresh_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state file did not contain a JSON object")
        if "schema" not in data:
            # legacy {"starts": N, "error_codes": [...]} -> schema v2 migration
            legacy_starts = data.get("starts", 0)
            legacy_codes = data.get("error_codes", [])
            data = {
                "schema": 2,
                "crash_starts": legacy_starts,
                "gate_starts": 0,
                "pending": False,
                "error_codes": legacy_codes,
            }
        data.setdefault("crash_starts", 0)
        data.setdefault("gate_starts", 0)
        data.setdefault("pending", False)
        data.setdefault("error_codes", [])
        data.setdefault("sc_codes", [])
        return data
    except Exception:
        # corrupt state → self-heal (governor is a waste-limiter, not a
        # security gate; deliberately differs from Class-1 verdict_resolution)
        return _fresh_state()


def governor_check(
    state_dir: Path,
    run_id: str,
    workflow: str,
    cap: int = 3,
    extra_transient: "frozenset[str]" = frozenset(),
    gate_cap: "int | None" = None,
) -> "tuple[bool, str | None]":
    """Return (allow, deny_code). Absent/corrupt state → (True, None).

    `cap` is the crash-loop cap (legacy kwarg meaning preserved). `gate_cap`
    defaults to `cap` when not given (GH625 split budgets)."""
    state = _load_state(state_dir, run_id, workflow)
    crash_cap = cap
    effective_gate_cap = gate_cap if gate_cap is not None else cap

    crash_effective = state["crash_starts"] + (1 if state["pending"] else 0)
    if crash_effective >= crash_cap:
        return False, "E_RESTART_CAP"

    if state["gate_starts"] >= effective_gate_cap:
        return False, "E_RESTART_CAP"

    codes = state.get("sc_codes", [])
    if len(codes) >= 2:
        last_two = codes[-2:]
        code = last_two[-1]
        if (
            last_two[0] == last_two[1]
            and code is not None
            and not (code.endswith("_TIMEOUT") or code in extra_transient)
        ):
            return False, "E_RESTART_SHORT_CIRCUIT"

    return True, None


def governor_record_start(state_dir: Path, run_id: str, workflow: str) -> None:
    """Record a phase start. If a prior start was never closed (`pending`
    still True), it never got a matching result -> classify it as a crash
    now (close-time classification, GH625)."""
    state = _load_state(state_dir, run_id, workflow)
    if state.get("pending"):
        state["crash_starts"] = state.get("crash_starts", 0) + 1
    state["pending"] = True
    atomic_write(_state_file(state_dir, run_id, workflow), json.dumps(state))


def governor_record_result(
    state_dir: Path,
    run_id: str,
    workflow: str,
    error_code: "str | None",
    recoverable: bool = False,
) -> None:
    """Record a phase result. `error_code=None` (success) resets the governor.

    `recoverable` (GH635, default False = conservative/terminal) marks
    codes that are expected to retry-and-progress (e.g. a gate asking for
    another pass). Recoverable codes are appended to `error_codes` (all
    codes, for telemetry) but EXEMPT from `sc_codes` — the short-circuit
    no-progress check only fires on repeated non-recoverable codes; a
    recoverable retry loop is instead bounded by gate_cap.
    """
    path = _state_file(state_dir, run_id, workflow)
    if error_code is None:
        # success resets the governor for this phase
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    state = _load_state(state_dir, run_id, workflow)
    state["pending"] = False
    state["gate_starts"] = state.get("gate_starts", 0) + 1
    codes = state.get("error_codes", [])
    codes.append(error_code)
    state["error_codes"] = codes[-_MAX_ERROR_CODES:]
    if error_code is not None and recoverable is False:
        state["sc_codes"] = (state.get("sc_codes", []) + [error_code])[-_MAX_ERROR_CODES:]
    atomic_write(path, json.dumps(state))


def governor_record_pause(state_dir: Path, run_id: str, workflow: str) -> None:
    """Env-limit pause (GH576 C474E073): clear `pending` only, no counter
    decrement. Under close-time classification an unclosed-then-paused start
    was never counted toward either budget, so clearing `pending` IS the
    refund (GH625 — replaces the prior `starts -= 1` refund)."""
    state = _load_state(state_dir, run_id, workflow)
    state["pending"] = False
    atomic_write(_state_file(state_dir, run_id, workflow), json.dumps(state))


def governor_reset(
    state_dir: Path,
    run_id: str,
    workflow: str,
    reason: str,
    event_log_path: "str | None" = None,
) -> dict:
    """Operator escape (GH603): reset restart-governor state for (run_id,
    workflow) with a logged reason, unlinking the state file so the next
    `governor_check` allows the retry without manual file surgery.

    Returns `{"prior_starts": int, "prior_error_codes": [...]}` where
    `prior_starts` (GH625) = crash_starts + gate_starts + (1 if pending)
    over the (migrated) v2 state, preserving GH603 consumer semantics.

    `reason` must be non-empty (after stripping); blank/whitespace-only
    raises ValueError.
    """
    if not reason or not reason.strip():
        raise ValueError("governor_reset: reason must be a non-empty string")

    state = _load_state(state_dir, run_id, workflow)
    prior_crash_starts = state.get("crash_starts", 0)
    prior_gate_starts = state.get("gate_starts", 0)
    prior_starts = prior_crash_starts + prior_gate_starts + (1 if state.get("pending") else 0)
    prior_error_codes = state.get("error_codes", [])

    path = _state_file(state_dir, run_id, workflow)
    try:
        path.unlink()
    except FileNotFoundError:
        pass

    if event_log_path:
        try:
            from event_sink import get_event_sink

            get_event_sink(event_log_path).append(
                "restart_governor_reason_reset",
                {
                    "workflow": workflow,
                    "run_id": run_id,
                    "reason": reason,
                    "prior_starts": prior_starts,
                    "prior_crash_starts": prior_crash_starts,
                    "prior_gate_starts": prior_gate_starts,
                    "prior_error_codes": prior_error_codes,
                },
                run_id,
            )
        except Exception:
            logger.warning("restart_governor: failed to emit reason_reset event", exc_info=True)

    return {"prior_starts": prior_starts, "prior_error_codes": prior_error_codes}


def governor_gate(
    event_log_path: "str | None",
    run_id: str,
    workflow: str,
    org_config: "dict | None",
) -> "dict | None":
    """Single call-site helper for run.py. None = allow (start recorded).

    A StepResult-shaped dict = deny. Fails open (returns None) on any
    internal crash or unusable state_dir — a broken governor must never
    block a build.
    """
    try:
        if not event_log_path:
            return None

        cfg = (org_config or {}).get("restart_governor", {}) or {}
        if cfg.get("disable"):
            return None

        legacy_cap = cfg.get("cap", 3)
        crash_cap = cfg.get("crash_cap", legacy_cap)
        gate_retry_cap = cfg.get("gate_retry_cap", legacy_cap)
        extra_transient = frozenset(cfg.get("extra_transient", []) or [])

        state_dir = Path(event_log_path).parent

        allow, deny_code = governor_check(
            state_dir,
            run_id,
            workflow,
            cap=crash_cap,
            extra_transient=extra_transient,
            gate_cap=gate_retry_cap,
        )
        if not allow:
            state = _load_state(state_dir, run_id, workflow)
            crash_starts = state.get("crash_starts", 0)
            gate_starts = state.get("gate_starts", 0)
            pending = state.get("pending", False)
            legacy_starts = crash_starts + gate_starts + (1 if pending else 0)
            if deny_code == "E_RESTART_SHORT_CIRCUIT":
                deny_class = "short_circuit"
            elif (crash_starts + (1 if pending else 0)) >= crash_cap:
                deny_class = "crash"
            else:
                deny_class = "gate_retry"
            try:
                from event_sink import get_event_sink

                get_event_sink(event_log_path).append(
                    "restart_governor_denied",
                    {
                        "workflow": workflow,
                        "run_id": run_id,
                        "deny_code": deny_code,
                        "starts": legacy_starts,
                        "crash_starts": crash_starts,
                        "gate_starts": gate_starts,
                        "deny_class": deny_class,
                    },
                    run_id,
                )
            except Exception:
                logger.warning("restart_governor: failed to emit deny event", exc_info=True)

            return {
                "status": "error",
                "data": None,
                "duration_ms": 0,
                "step_name": workflow,
                "error": f"restart governor denied: {deny_code}",
                "error_code": deny_code,
                "recoverable": False,
            }

        governor_record_start(state_dir, run_id, workflow)
        return None
    except Exception:
        logger.warning("restart_governor: internal error, failing open", exc_info=True)
        return None
