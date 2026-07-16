"""Shared resolver telemetry helper (966E571B, GH795 emit seam).

Emits a `resolver_<helper>_resolved` event — either to a durable event log
(when `event_log_path` is provided) or via telemetry_ctx.emit_safe (default).
Never raises. No-op when no run context is active (same semantics as emit_safe).
"""
from __future__ import annotations

import telemetry_ctx  # type: ignore[import]


def emit_resolver_resolved(
    helper: str,
    source: str,
    value: str,
    extra: dict | None = None,
    *,
    event_log_path: str | None = None,
    run_id: str | None = None,
) -> None:
    """Emit a `resolver_<helper>_resolved` event.

    Never raises. No-op when no run context is active (same semantics as emit_safe).
    """
    event_type = f"resolver_{helper}_resolved"
    payload = {
        "helper": helper,
        "source": source,
        "value": value,
        "extra": extra or {},
    }
    try:
        if event_log_path:
            from event_sink import get_event_sink

            get_event_sink(event_log_path).append(event_type, payload, run_id)
        else:
            telemetry_ctx.emit_safe(event_type, payload)
    except Exception:  # noqa: BLE001
        pass
