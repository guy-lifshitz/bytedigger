"""Append-only event log for the workflow engine.

Stage 1c (PIVOT 2026-04-25): replaces mutable `build-state.yaml` (multiple
racing writers — root cause of /build chaos) with a single append-only
JSONL file. The engine is the sole writer; readers (hooks, status CLI)
derive state by replay.

Format: one JSON object per line. Required keys:
    ts        — ISO 8601 UTC timestamp (millisecond precision, "Z" suffix)
    run_id    — workflow run identifier (caller-provided; "ad-hoc" if None)
    event_type — short snake_case kind (e.g. "step_started", "step_finished")
    payload   — event-specific dict (free-form, must be JSON-serialisable)

Atomicity: O_APPEND + single line write < PIPE_BUF (4 KiB) is atomic on POSIX,
so no tmp+rename dance is needed for individual event appends. Readers may
see partial files mid-write only across full lines — never inside a JSON
object — because each `append()` writes the line in one syscall.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from config_provider import hal_root as _hal_root_fn  # noqa: E402
from config_provider import event_log_relpath, resolve_event_log_hal_dir  # noqa: E402

# structural: engine_py → build → cli → SYSTEM → host install root
def _resolve_hal_dir(file_path: Path) -> Path:
    resolved = file_path.resolve()
    parents = list(resolved.parents)
    if len(parents) >= 5:
        return parents[4]
    return parents[-1] if parents else resolved


def default_log_path() -> Path:
    """Resolve the default event log path lazily based on cwd.

    HAL dogfood (cwd inside the HAL install root): canonical central state log
    so HAL's own builds keep using the central log. Foreign projects: writes to
    cwd-relative .hal-build/events.jsonl so HAL state is not polluted and the
    canary contract (build-compact.md: .hal-build/events.jsonl) is honoured.

    HOME unresolvable: treat cwd as foreign (safe default — same logic as
    phase_05_inject._cwd_inside_hal_dir, agreement 349FE371).
    """
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            return hal_dir / event_log_relpath()
    except (OSError, ValueError, RuntimeError):
        pass
    return Path.cwd() / ".hal-build/events.jsonl"


def __getattr__(name: str) -> Any:
    """PEP-562 lazy module attributes (GH444 D5): HAL_DIR / DEFAULT_LOG_PATH
    are computed on access instead of bound at import time (no caching —
    recompute per access is acceptable and matches provider-swap test needs).
    """
    if name == "HAL_DIR":
        return resolve_event_log_hal_dir(lambda: _resolve_hal_dir(Path(__file__)))
    if name == "DEFAULT_LOG_PATH":
        return resolve_event_log_hal_dir(lambda: _resolve_hal_dir(Path(__file__))) / event_log_relpath()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_LINE_LIMIT_BYTES = 4096  # POSIX PIPE_BUF lower bound — beyond this, atomic-append guarantee no longer holds


class EventLogLineTooLarge(ValueError):
    """Raised when a serialised event exceeds the atomic-append size guarantee."""


class EventLog:
    """Append-only JSONL event log.

    Single-writer assumption: callers must coordinate so only one process
    writes per run_id at a time. Multiple processes appending to the same
    file is safe (O_APPEND), but conflicting state semantics are caller's
    responsibility.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = default_log_path()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one event. Returns the full event dict that was written."""
        if not event_type or not isinstance(event_type, str):
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")

        event = {
            "ts": _utcnow_iso(),
            "run_id": run_id or "ad-hoc",
            "event_type": event_type,
            "payload": payload,
        }
        line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > _LINE_LIMIT_BYTES:
            raise EventLogLineTooLarge(
                f"event line is {len(encoded)} bytes; atomic append guaranteed only up to {_LINE_LIMIT_BYTES}"
            )

        # O_APPEND + one write() < PIPE_BUF = atomic on POSIX. os.write avoids
        # the buffered-IO layer and gives us a single syscall per event.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)
        return event

    def read_all(self) -> list[dict[str, Any]]:
        """Read all events. Skips blank lines; raises on malformed JSON."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    events.append(json.loads(stripped))
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"{self.path}:{lineno}: malformed JSON in event log: {e.msg}"
                    ) from e
        return events


def _utcnow_iso() -> str:
    """ISO 8601 UTC with millisecond precision, 'Z' suffix."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
