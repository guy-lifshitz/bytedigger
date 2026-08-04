"""Stuck-agent root-cause halt report (GH1041 lot A+B).

Pattern-clone of lib/dispatcher_report.py (fail-safe, core-clean, no host env
reads at import; reuses io_utils.atomic_write, the same primitive
restart_governor.py uses).

Schema v1 (frozen, spec §2.1):
    {
        "schema": 1,
        "ts": "<ISO-8601 UTC Z>",
        "run_id": "...",
        "workflow": "<phase name>",
        "step_name": "<last failed step | null>",
        "breaker": "restart_cap | short_circuit | same_cycle_retry_cap | terminal_failure",
        "error_code": "E_... | null",
        "retry_count": <int>,
        "counters": {"crash_starts": N, "gate_starts": N, "pending": bool} | null,
        "last_error": "<= 500 chars> | null",
        "next_step": "operator_reset_restart_reason | fix_root_cause_then_reset |
                      inspect_validation_cycle | check_provider_or_config",
    }

No consumer reads this file yet (§1o) except the (out-of-scope-for-this-lot)
batch dispatcher lot C; schema frozen here.

NO LLM calls anywhere in this module.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from bytedigger_engine.io_utils import atomic_write

STUCK_REPORT_FILENAME = "stuck-report.json"

_LINE_LIMIT_BYTES = 4096
_ERROR_TRUNC_LEN = 500

_NEXT_STEP_TABLE: dict[str, str] = {
    "restart_cap": "operator_reset_restart_reason",
    "short_circuit": "fix_root_cause_then_reset",
    "same_cycle_retry_cap": "inspect_validation_cycle",
    "terminal_failure": "check_provider_or_config",
}


def build_stuck_report(
    *,
    run_id: str,
    workflow: str,
    step_name: "str | None",
    breaker: str,
    error_code: "str | None",
    retry_count: int,
    counters: "dict[str, Any] | None",
    last_error: "str | None",
) -> dict[str, Any]:
    """Pure template render — no I/O. Frozen breaker->next_step table."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    truncated_error = last_error if last_error is None else str(last_error)[:_ERROR_TRUNC_LEN]
    return {
        "schema": 1,
        "ts": ts,
        "run_id": run_id,
        "workflow": workflow,
        "step_name": step_name,
        "breaker": breaker,
        "error_code": error_code,
        "retry_count": retry_count,
        "counters": counters,
        "last_error": truncated_error,
        "next_step": _NEXT_STEP_TABLE.get(breaker),
    }


def emit_stuck_report(
    report_dir: Path,
    report: dict[str, Any],
    run_id: str,
    event_log_path: "str | None" = None,
) -> None:
    """Fail-safe wrapper: whole-file byte cap 4096; over-cap truncates
    last_error further (NEVER skip the write silently — this file IS the
    dispatcher's signal, unlike dispatcher_report's drop-the-row rule).

    NEVER raises into the pipeline (outer try/except).
    """
    try:
        payload = dict(report)
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _LINE_LIMIT_BYTES:
            last_error = payload.get("last_error")
            if isinstance(last_error, str) and last_error:
                overage = len(encoded) - _LINE_LIMIT_BYTES
                trim_to = max(0, len(last_error) - overage - 32)
                payload["last_error"] = last_error[:trim_to]
                encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            while len(encoded) > _LINE_LIMIT_BYTES and payload.get("last_error"):
                payload["last_error"] = str(payload["last_error"])[: max(0, len(str(payload["last_error"])) // 2)]
                encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        dest = Path(report_dir) / STUCK_REPORT_FILENAME
        atomic_write(dest, encoded.decode("utf-8"))

        if event_log_path:
            try:  # nosemgrep: hal-silent-except-pass -- GH1041 AC11: best-effort emit must never raise into governor outer except (deny would flip to allow)
                from bytedigger_engine.event_sink import get_event_sink

                get_event_sink(event_log_path).append(
                    "stuck_report_emitted",
                    {
                        "run_id": run_id,
                        "workflow": payload.get("workflow"),
                        "breaker": payload.get("breaker"),
                        "path": str(dest),
                    },
                    run_id,
                )
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return


def clear_stuck_report(
    report_dir: Path,
    run_id: str,
    event_log_path: "str | None" = None,
) -> bool:
    """Unlink-if-exists (FileNotFoundError-tolerant). Returns whether a file
    was removed. Emits stuck_report_cleared best-effort when removed AND
    event_log_path is provided."""
    path = Path(report_dir) / STUCK_REPORT_FILENAME
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    except Exception:  # noqa: BLE001
        return False

    if removed and event_log_path:
        try:  # nosemgrep: hal-silent-except-pass -- GH1041 AC11: best-effort emit must never raise into governor outer except (deny would flip to allow)
            from bytedigger_engine.event_sink import get_event_sink

            get_event_sink(event_log_path).append(
                "stuck_report_cleared",
                {"run_id": run_id, "path": str(path)},
                run_id,
            )
        except Exception:  # noqa: BLE001
            pass

    return removed
