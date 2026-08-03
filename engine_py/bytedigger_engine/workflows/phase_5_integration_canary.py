"""Phase 5 Integration-Canary hard gate (agreement 06452D44).

Closes the deployment-coverage gap exposed by build forge-1777578916: the
implementation was statically correct (51 tests passed) but never fired in
production because no integration smoke ran against the real event bus.

Contract:
    A single-step workflow (`verify_integration_event`) that — when opted-in
    via `org_config.integration_canary_required = True` — reads the
    append-only events.jsonl log and HARD-FAILS the build when no event for
    the current run_id matches the required event_type (default
    `agent_spawn`) and required_payload_keys (default empty).

Inputs (via `ctx.org_config`):
    integration_canary_required             — bool. Default False (skip).
    integration_canary_event_type           — str. Default "agent_spawn".
    integration_canary_required_payload_keys — list[str]. Default [].
    integration_canary_events_path          — str|Path. Default
                                              event_log.default_log_path().
    integration_canary_run_id               — str. If absent, falls back to
                                              telemetry_ctx.get_current_run().run_id.
                                              Required when required=True.
    integration_canary_min_matches          — int. Default 1.

Error codes:
    E_CANARY_BAD_CONFIG     — required=True but run_id unresolvable.
    E_CANARY_EVENTS_MISSING — events_path doesn't exist.
    E_CANARY_NO_MATCH       — fewer than min_matches events matched.

Skip semantics:
    `required=False` short-circuits to status="ok", data={"skipped": True, ...}
    WITHOUT reading the events file. This keeps SIMPLE/iterative-debug runs
    cheap when the orchestrator hasn't requested the gate.

Malformed lines in events.jsonl are skipped silently. event_log.py guarantees
single-writer atomic append (line < PIPE_BUF), so any malformed line is
corruption — best to keep going rather than mask the missing event signal.
"""
from __future__ import annotations

import json
from pathlib import Path

from bytedigger_engine import event_log
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition


# 36269734: sidecar for canary event_type; counterpart in phase_45_spec.py
CANARY_META_RELPATH = "integration/canary-meta.json"


def _read_canary_meta_event_type(cfg: dict) -> str | None:
    """Read event_type from the spec-written sidecar at
    <scratchpad_dir>/integration/canary-meta.json.

    Returns the event_type string when the sidecar exists and is valid JSON,
    None otherwise (missing scratchpad_dir, file absent, bad JSON, any OSError).

    Agreement 36269734 — counterpart constant: CANARY_META_RELPATH.
    """
    sp = cfg.get("scratchpad_dir")
    if not sp:
        return None
    try:
        raw = (Path(sp) / CANARY_META_RELPATH).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data.get("event_type") or None
    except Exception:  # noqa: BLE001
        return None


def _resolve_run_id(cfg: dict) -> str | None:
    """org_config first (test seam), then telemetry_ctx (production)."""
    rid = cfg.get("integration_canary_run_id")
    if rid:
        return str(rid)
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is not None and getattr(run_ctx, "run_id", None):
        return str(run_ctx.run_id)
    return None


def _verify_integration_event(ctx, _prev) -> StepResult:
    cfg = ctx.org_config or {}
    required = bool(cfg.get("integration_canary_required", False))
    event_type = str(cfg.get("integration_canary_event_type") or _read_canary_meta_event_type(cfg) or "agent_spawn")
    required_payload_keys = list(cfg.get("integration_canary_required_payload_keys") or [])
    events_path_raw = cfg.get("integration_canary_events_path") or event_log.default_log_path()
    events_path = Path(events_path_raw)
    min_matches = int(cfg.get("integration_canary_min_matches") or 1)

    # BRANCH 1: NOT REQUIRED — short-circuit, do NOT touch disk.
    if not required:
        return StepResult(
            status="ok",
            data={
                "skipped": True,
                "matched_count": 0,
                "required_event_type": event_type,
                "required_run_id": None,
            },
            duration_ms=0,
            step_name="verify_integration_event",
        )

    # BRANCH 2: BAD CONFIG — required=True but run_id unresolvable.
    required_run_id = _resolve_run_id(cfg)
    if not required_run_id:
        return StepResult(
            status="error",
            data={
                "skipped": False,
                "matched_count": 0,
                "required_event_type": event_type,
            },
            duration_ms=0,
            step_name="verify_integration_event",
            error=(
                "integration_canary_run_id missing from org_config and no current "
                "run available via telemetry_ctx; cannot verify integration event"
            ),
            error_code="E_CANARY_BAD_CONFIG",
            recoverable=False,
        )

    # BRANCH 3: PATH MISSING.
    if not events_path.exists():
        return StepResult(
            status="error",
            data={
                "skipped": False,
                "matched_count": 0,
                "required_run_id": required_run_id,
                "required_event_type": event_type,
                "events_path": str(events_path),
            },
            duration_ms=0,
            step_name="verify_integration_event",
            error=f"integration canary events file not found at {events_path}",
            error_code="E_CANARY_EVENTS_MISSING",
            recoverable=False,
        )

    # BRANCH 4: READ + MATCH.
    matched_count = 0
    sample_ts: str | None = None
    try:
        with events_path.open("r", encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    # Single-writer atomic append guarantees full-line writes;
                    # malformed line = corruption, skip silently.
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("run_id") != required_run_id:
                    continue
                if event.get("event_type") != event_type:
                    continue
                payload = event.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if any(k not in payload for k in required_payload_keys):
                    continue
                matched_count += 1
                if sample_ts is None:
                    sample_ts = event.get("ts")
    except OSError as exc:
        return StepResult(
            status="error",
            data={
                "skipped": False,
                "matched_count": 0,
                "required_run_id": required_run_id,
                "required_event_type": event_type,
                "events_path": str(events_path),
            },
            duration_ms=0,
            step_name="verify_integration_event",
            error=f"could not read integration canary events file at {events_path}: {exc}",
            error_code="E_CANARY_EVENTS_MISSING",
            recoverable=False,
        )

    common = {
        "skipped": False,
        "matched_count": matched_count,
        "sample_ts": sample_ts,
        "required_run_id": required_run_id,
        "required_event_type": event_type,
        "events_path": str(events_path),
    }

    # BRANCH 5: NO MATCH / TOO FEW.
    if matched_count < min_matches:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="verify_integration_event",
            error=(
                f"integration canary failed: expected ≥{min_matches} event(s) of type "
                f"{event_type!r} for run_id {required_run_id!r}, got {matched_count}"
            ),
            error_code="E_CANARY_NO_MATCH",
            recoverable=False,
        )

    # BRANCH 6: SUCCESS.
    return StepResult(
        status="ok",
        data=common,
        duration_ms=0,
        step_name="verify_integration_event",
    )


def phase_5_integration_canary_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_5_integration_canary",
        steps=[
            StepContract(
                name="verify_integration_event",
                execute=_verify_integration_event,
            ),
        ],
    )
