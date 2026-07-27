"""git_write_at_cwd telemetry helper — GH1220 Change E / Amendment 5.3.

Emits a `git_write_at_cwd` event on every git-write-port call, unconditionally
(AC41) — the observability layer that would have caught the ambient-cwd bug
class in a day. Mirrors `lib.observability.emit_resolver.emit_resolver_resolved`
exactly: never raises (AC42/MAJOR-J) — an emit that can raise inside
`op_capture` would convert an observability nicety into an outage for every
git write in the engine.
"""
from __future__ import annotations

import logging

import telemetry_ctx

logger = logging.getLogger(__name__)


def emit_git_write_at_cwd(verb: str, cwd: "str | None") -> None:
    """Emit `git_write_at_cwd{cmd0, cwd}`. Never raises — a raising sink is
    logged at debug level rather than silently swallowed (the swallow itself
    is load-bearing, per A5.3; only the silence is the defect)."""
    payload = {"cmd0": verb, "cwd": cwd}
    try:
        telemetry_ctx.emit_safe("git_write_at_cwd", payload)
    except Exception:  # noqa: BLE001
        logger.debug("git_write_at_cwd emit failed", exc_info=True)
