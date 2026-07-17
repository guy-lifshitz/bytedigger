"""Reject-reason capture for the HAL build pipeline (EECA708D).

Appends one structured JSONL line per REVISE/FAIL rejection so downstream
triage scripts can identify high-frequency spec/review axes.

Schema (one JSON object per line in the reject-reasons.jsonl log):
    {
        "ts":          "<ISO-8601 UTC ending Z>",
        "build_id":    "<run_id | null>",
        "phase":       "phase_45_spec | phase_6_review",
        "reason_code": "<str>",
        "axes":        ["§1w", ...],
        "detail":      { ... }
    }

Fail-safe: the entire body of emit_reject_reason is wrapped in try/except so
this helper can NEVER raise into the build pipeline.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config_provider import hal_root as _hal_root_fn, path as _path_fn, reject_log_relpath, foreign_state_dirname as _foreign_state_dirname_fn  # noqa: E402


def default_reject_log_path() -> Path:
    """Resolve the default reject-log path lazily based on cwd.

    Mirrors event_log.default_log_path(): HAL dogfood (cwd inside HAL install
    root) → canonical central HAL reject log; foreign project → cwd-relative
    foreign_state_dirname()/reject-reasons.jsonl (no HAL-state pollution).
    HOME/cwd unresolvable → treat cwd as foreign (safe default).
    """
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            return hal_dir / reject_log_relpath()
    except (OSError, ValueError, RuntimeError):
        pass
    return Path.cwd() / _foreign_state_dirname_fn() / "reject-reasons.jsonl"


REJECT_LOG_PATH = default_reject_log_path()


def resolve_reject_log_path() -> Path:
    """GH497 D1: call-time env-seam resolver for the reject-reasons log path.

    Returns Path(HAL_REJECT_LOG) if the env var is set and non-empty, else
    default_reject_log_path(). Resolved at CALL time (unlike REJECT_LOG_PATH,
    which freezes at import time before pytest env isolation can act).
    """
    return _path_fn("HAL_REJECT_LOG", default_reject_log_path())

# Matches §-rule tokens like §1w, §1o, §2.3, §3.
# Pattern: § + optional space + digit + optional 1-3 lowercase letters + optional .<digits>
_AXIS_RE = re.compile(r"§\s?\d[a-z]{0,3}(?:\.\d+)?")

_LINE_LIMIT_BYTES = 4096


def _extract_axes(text: object) -> list[str]:
    """Return deduplicated list of §-rule tokens found in text.

    Preserves first-seen order; strips internal spaces ("§ 1w" -> "§1w").
    Returns [] for None or empty input.
    """
    if not text:
        return []
    if not isinstance(text, str):
        return []
    seen: dict[str, None] = {}
    for match in _AXIS_RE.findall(text):
        normalised = match.replace(" ", "")
        if normalised not in seen:
            seen[normalised] = None
    return list(seen)


def emit_reject_reason(
    phase: str,
    reason_code: str,
    axes: list[str],
    detail: object,
    *,
    run_id: str | None = None,
    path: Path | None = None,
) -> None:
    """Append one reject-reason record to the JSONL log.

    Fail-safe: any exception is silently swallowed — this helper must NEVER
    raise into the build pipeline (mirrors _emit_safe pattern).

    build_id обязателен (GH497 D1): if no run_id is supplied and no active
    telemetry_ctx run is set, build_id resolves to None and the write is
    suppressed entirely (no record) — no more build_id=null prod-log rows.
    """
    try:
        build_id: object = run_id
        if build_id is None:
            try:
                import telemetry_ctx  # lazy import — module may be absent in test env
                run_ctx = telemetry_ctx.get_current_run()
                build_id = run_ctx.run_id if run_ctx is not None else None
            except Exception:  # noqa: BLE001
                build_id = None

        if build_id is None:
            return

        resolved_path: Path = path if path is not None else resolve_reject_log_path()

        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "ts": ts,
            "build_id": build_id,
            "phase": phase,
            "reason_code": reason_code,
            "axes": axes,
            "detail": detail,
        }
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        encoded = line.encode("utf-8")

        # Cap at 4096 bytes: skip oversized lines (do NOT write truncated invalid JSON)
        if len(encoded) > _LINE_LIMIT_BYTES:
            return

        parent = Path(resolved_path).parent
        os.makedirs(parent, exist_ok=True)

        fd = os.open(str(resolved_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, encoded)
        os.close(fd)
    except Exception:  # noqa: BLE001
        return


def record_plan_review_reject(
    findings: str | None,
    cycle: int,
    n_unresolved: int,
    *,
    findings_source: str = "unknown",
    path: Path | None = None,
) -> None:
    """Record a PLAN_REVIEW_REVISE rejection from phase_45_spec."""
    emit_reject_reason(
        "phase_45_spec",
        "PLAN_REVIEW_REVISE",
        _extract_axes(findings),
        {"cycle": cycle, "n_unresolved": n_unresolved, "findings_source": findings_source},
        path=path,
    )


def record_satisfaction_reject(
    reason_code: str,
    error_msg: str | None,
    score: object,
    threshold: object,
    *,
    axes_text: str | None = None,
    path: Path | None = None,
) -> None:
    """Record a satisfaction-FAIL rejection from phase_6_review.

    axes_text (GH497 D2): the model-output text containing §-rule citations
    (RULE-AXES: ...). Falls back to error_msg (which rarely contains
    §-tokens) when not supplied.
    """
    emit_reject_reason(
        "phase_6_review",
        reason_code,
        _extract_axes(axes_text if axes_text is not None else error_msg),
        {"score": score, "threshold": threshold},
        path=path,
    )
