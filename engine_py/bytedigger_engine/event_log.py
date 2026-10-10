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
import re
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bytedigger_engine.config_provider import hal_root as _hal_root_fn  # noqa: E402
from bytedigger_engine.config_provider import event_log_relpath, resolve_event_log_hal_dir  # noqa: E402
from bytedigger_engine.config_provider import event_log_path_override as _event_log_path_override_fn  # noqa: E402
from bytedigger_engine.config_provider import foreign_state_dirname as _foreign_state_dirname_fn  # noqa: E402
from bytedigger_engine.config_provider import run_log_root as _run_log_root_fn  # noqa: E402
from bytedigger_engine.config_provider import run_log_retention_days as _run_log_retention_days_fn  # noqa: E402

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
    cwd-relative foreign_state_dirname()/events.jsonl so HAL state is not
    polluted and the canary contract is honoured.

    HOME unresolvable: treat cwd as foreign (safe default — same logic as
    phase_05_inject._cwd_inside_hal_dir, agreement 349FE371).

    GH1309: an explicit HAL_EVENT_LOG_PATH wins over both. Batch workers point
    it outside their own worktree (`<log_dir>/events-<issue>.jsonl`) so a
    `worktree remove --force` — which on 2026-07-27 destroyed two live ppba
    worktrees — can no longer take the diagnostics with the directory.

    Scope of the override, stated exactly: every reader that resolves through
    THIS function follows it. Two writers/readers do not, by construction —
    drive-engine.ts pins `integration_canary_events_path` to a literal relative
    path, and subagent-start-track.ts resolves HAL_EVENTS_LOG_PATH against the
    HAL dir. The canary keeps working because of that explicit pin, not because
    this override reaches it.
    """
    override = _event_log_path_override_fn()
    if override:
        return Path(override).expanduser()
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            return hal_dir / event_log_relpath()
    except (OSError, ValueError, RuntimeError):
        pass
    return Path.cwd() / _foreign_state_dirname_fn() / "events.jsonl"


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def run_scoped_log_path(run_id: str, root: Path | None = None) -> Path:
    """bd#93: `<root>/<run_id>/events.jsonl` — the run's own home, independent of cwd.

    `root` defaults to the provider's run_log_root(). Pure path math, no filesystem
    I/O. ValueError on an unsafe run id (no separators/leading dot/`..`, <=128
    chars) or a root that is not absolute after expanduser (never cwd-dependent).
    """
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError(f"unsafe run id for a run-scoped event log: {run_id!r}")
    base = Path(root if root is not None else _run_log_root_fn()).expanduser()
    if not base.is_absolute():
        raise ValueError(f"run log root must be absolute: {str(base)!r}")
    return base / run_id / "events.jsonl"


_RETENTION_REPORT_CAP = 20


def _newest_mtime(directory: Path) -> float:
    """Newest modification time of `directory` and its direct children, symlinks not followed."""
    newest = os.lstat(directory).st_mtime
    for child in directory.iterdir():
        try:
            newest = max(newest, os.lstat(child).st_mtime)
        except OSError:
            continue  # vanished or unreadable child: ignored
    return newest


def run_retention_dry_run(
    run_id: str | None = None,
    root: Path | None = None,
    now: float | None = None,
) -> list[Path]:
    """bd#260: find run directories older than the retention age; report, never act.

    Candidates are real directories directly under `root` with a valid run id, other
    than `run_id` (the run that is starting), whose newest modification time (the
    directory and its direct children) is strictly older than the threshold. Writes
    a capped summary to stderr when there are any. This release deletes nothing.
    A retention age of 0 disables it. Never raises: a failure of the whole routine
    is one `run retention skipped` line and `[]`; a bad entry is skipped alone.
    """
    try:
        days = _run_log_retention_days_fn()
        if days < 0:
            raise ValueError(f"retention days must not be negative: {days}")
        if days == 0:
            return []
        base = Path(root if root is not None else _run_log_root_fn()).expanduser()
        if not base.is_absolute():
            raise ValueError(f"run log root must be absolute: {str(base)!r}")
        try:
            base_mode = base.stat().st_mode
        except FileNotFoundError:
            return []
        if not stat.S_ISDIR(base_mode):
            raise ValueError(f"run log root is not a directory: {str(base)!r}")
        real_base = base.resolve()
        current = float(now) if now is not None else time.time()
        threshold = days * 86400.0
        found: list[Path] = []
        for entry in sorted(base.iterdir(), key=lambda p: p.name):
            try:
                name = entry.name
                if name == run_id or not _RUN_ID_RE.fullmatch(name) or ".." in name:
                    continue
                if not stat.S_ISDIR(os.lstat(entry).st_mode):
                    continue  # symlinks and regular files are never candidates
                if entry.resolve().parent != real_base:
                    continue
                if current - _newest_mtime(entry) > threshold:
                    found.append(entry)
            except OSError:
                continue
    except Exception as e:  # noqa: BLE001 - the routine must never stop a run
        sys.stderr.write(f"run retention skipped: {e}\n")
        return []
    if found:
        lines = [
            f"run retention (dry-run): {len(found)} run directories older than {days} days "
            "would be removed; nothing deleted"
        ]
        lines.extend(p.name for p in found[:_RETENTION_REPORT_CAP])
        if len(found) > _RETENTION_REPORT_CAP:
            lines.append(f"... and {len(found) - _RETENTION_REPORT_CAP} more")
        sys.stderr.write("\n".join(lines) + "\n")
    return found


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
