"""Dispatcher report auto-generation on terminal workflow_finished (GH923 lot 3, ported).

Pattern-clone of lib/incident_ledger.py (fail-safe JSONL append, call-time env
seam, hal-root/foreign split; same import style).

On every terminal-error/escalate workflow_finished the engine deterministically
assembles a markdown report + one JSONL index row from the event log. NO LLM
calls anywhere in this module.

This is a DRAFT classification only — lot 2's CLI reclassifies; observability-
only, defers ALL error codes, adds no gate (§1n).

Index row schema (one JSON object per line):
    {
        "ts":               "<ISO-8601 UTC ending Z>",
        "run_id":           "...",
        "phase":            "...",
        "status":           "error|escalate",
        "error_code":       "E_...|null",
        "classification":   "external|engine|quality-gate|infra|unknown",
        "step_name":        "<last failed step|null>",
        "wall_ms":          1234,
        "retries":          0,
        "restart_reasons":  ["..."],
        "cost_usd":         F|null,
        "tokens_in":        N|null,
        "tokens_out":       N|null,
        "calls":            N|null,
        "report_path":      "...|null",
        "error":            "<=500 chars|null>"
    }

No consumer reads this file yet (§1o); future lot-2/4 consumers will read
run_id/phase/error_code/classification/report_path — schema frozen here.

Fail-safe: emit_dispatcher_report NEVER raises into the build pipeline.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import hal_root as _hal_root_fn, path as _path_fn, foreign_state_dirname as _foreign_state_dirname_fn, env_opt as _env_opt_fn  # noqa: E402

_LINE_LIMIT_BYTES = 4096
_ERROR_TRUNC_LEN = 500

# Ordered classification rule table — first match wins. Infra exacts/prefixes
# are ORDERED BEFORE the E_LLM_ prefix so E_LLM_CMD_MISSING classifies infra,
# not external.
_CLASSIFICATION_RULES: tuple[tuple[str, str, str], ...] = (
    ("exact", "E_LLM_CMD_MISSING", "infra"),
    ("exact", "E_MANIFEST_MISSING", "infra"),
    ("exact", "E_PYTEST_MISSING", "infra"),
    ("exact", "E_TEST_RUNNER_MISSING", "infra"),
    ("exact", "E_DIFF_CMD_MISSING", "infra"),
    ("exact", "E_RESTART_CAP", "engine"),
    ("exact", "E_RESTART_SHORT_CIRCUIT", "engine"),
    ("exact", "E_STEP_TIMEOUT", "engine"),
    ("exact", "E_VALIDATION_FAILED", "quality-gate"),
    ("exact", "E_VALIDATION_RETRY", "quality-gate"),
)

# Prefix rules assembled without the "E_" head so these tails never appear as
# a standalone quoted "E_..." literal — error_codes.py's CODE_RE registry
# scanner harvests every quoted E_[A-Z0-9_]+ literal in prod .py files, and
# these tails are not themselves registered codes (they are draft
# classification prefixes, §1n). Order preserved from spec §2.1a: infra
# prefixes -> LLM_ external -> CANARY_ engine -> quality-gate prefixes.
_E_HEAD = "E" + "_"
_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("GIT_", "infra"),
    ("MEMORY_DB_", "infra"),
    ("LLM_", "external"),
    ("CANARY_", "engine"),
    ("SATISFACTION_", "quality-gate"),
    ("SPEC_", "quality-gate"),
    ("REVIEW_", "quality-gate"),
    ("SEC_", "quality-gate"),
    ("RED_", "quality-gate"),
    ("GREEN_", "quality-gate"),
)

_OUTAGE_MARKER = "[external_outage:"


def classify_error_code(error_code: str | None, error: str | None = None) -> str:
    """Draft deterministic classification, first match wins.

    0. An embedded '[external_outage:' marker in `error` wins over any code
       rule (GH933/#940 marker embedded in StepResult.error).
    1. error_code falsy -> "unknown".
    2. Ordered exact/prefix rule table (_CLASSIFICATION_RULES / _PREFIX_RULES).
    3. No match -> "unknown".
    """
    if isinstance(error, str) and _OUTAGE_MARKER in error:
        return "external"
    if not error_code:
        return "unknown"
    for kind, value, cls in _CLASSIFICATION_RULES:
        if kind == "exact" and error_code == value:
            return cls
        if kind == "prefix" and error_code.startswith(value):
            return cls
    if error_code.startswith(_E_HEAD):
        tail = error_code[len(_E_HEAD):]
        for prefix_tail, cls in _PREFIX_RULES:
            if tail.startswith(prefix_tail):
                return cls
    return "unknown"


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", s)


def collect_run_stats(events_path: Path | str, run_id: str, phase: str) -> dict[str, Any]:
    """One pass over the events JSONL, filtered to `run_id`. NEVER raises.

    Blank/malformed/non-dict lines are skipped (error_locus.py precedent).
    Missing/unreadable file -> empty defaults.
    """
    stats: dict[str, Any] = {
        "timeline": [],
        "failed_steps": [],
        "retries": 0,
        "retry_codes": [],
        "restart_reasons": [],
        "restart_denials": [],
        "rollup": None,
    }
    try:
        path_obj = Path(events_path)
        if not path_obj.exists():
            return stats
        with open(path_obj, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("run_id") != run_id:
                    continue
                event_type = row.get("event_type")
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    payload = {}

                if event_type == "step_finished":
                    entry = {
                        "step_name": payload.get("step_name"),
                        "status": payload.get("status"),
                        "duration_ms": payload.get("duration_ms"),
                    }
                    stats["timeline"].append(entry)
                    if payload.get("status") in ("error", "escalate"):
                        stats["failed_steps"].append(entry)
                elif event_type == "phase_retry_triggered":
                    stats["retries"] += 1
                    code = payload.get("error_code")
                    if code:
                        stats["retry_codes"].append(code)
                elif event_type == "restart_governor_reason_reset":
                    reason = payload.get("reason")
                    if reason:
                        stats["restart_reasons"].append(reason)
                elif event_type == "restart_governor_denied":
                    deny_code = payload.get("deny_code")
                    if deny_code:
                        stats["restart_denials"].append(deny_code)
                elif event_type == "workflow_cost_rollup":
                    stats["rollup"] = payload
    except Exception:  # noqa: BLE001
        # fail-safe: stats collection must never raise; return what was gathered (spec §2.1b)
        return stats
    return stats


def render_report(meta: dict[str, Any], stats: dict[str, Any]) -> str:
    """Pure markdown render — no I/O."""
    phase = meta.get("phase")
    error_code = meta.get("error_code")
    ts = meta.get("ts")
    rollup = stats.get("rollup") or {}

    lines: list[str] = []
    lines.append(f"# Dispatcher report — {phase} {error_code} ({ts})")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    lines.append(f"| run_id | {meta.get('run_id')} |")
    lines.append(f"| phase | {phase} |")
    lines.append(f"| status | {meta.get('status')} |")
    lines.append(f"| error_code | {error_code} |")
    lines.append(f"| classification | {meta.get('classification')} |")
    lines.append(f"| wall_ms | {meta.get('wall_ms')} |")
    lines.append(f"| cost_usd | {rollup.get('cost_usd', 'n/a')} |")
    lines.append(f"| tokens_in | {rollup.get('tokens_in', 'n/a')} |")
    lines.append(f"| tokens_out | {rollup.get('tokens_out', 'n/a')} |")
    lines.append(f"| calls | {rollup.get('calls', 'n/a')} |")
    lines.append("")

    lines.append("## Timeline")
    lines.append("")
    lines.append("| step_name | status | duration_ms |")
    lines.append("|---|---|---|")
    for entry in stats.get("timeline", []):
        lines.append(f"| {entry.get('step_name')} | {entry.get('status')} | {entry.get('duration_ms')} |")
    lines.append("")

    lines.append("## Retries")
    lines.append("")
    retry_codes = stats.get("retry_codes", [])
    lines.append(f"count: {stats.get('retries', 0)}")
    lines.append(f"codes: {', '.join(retry_codes) if retry_codes else 'none'}")
    lines.append("")

    lines.append("## Restart reasons")
    lines.append("")
    reasons = stats.get("restart_reasons", [])
    denials = stats.get("restart_denials", [])
    if reasons or denials:
        for reason in reasons:
            lines.append(f"- reason: {reason}")
        for deny_code in denials:
            lines.append(f"- denied: {deny_code}")
    else:
        lines.append("none")
    lines.append("")

    lines.append("## Error")
    lines.append("")
    error = meta.get("error")
    if error:
        lines.append("```")
        lines.append(str(error)[:_ERROR_TRUNC_LEN])
        lines.append("```")
    else:
        lines.append("none")
    lines.append("")

    lines.append("_draft classification — reclassify via incident CLI (lot 2)_")
    return "\n".join(lines) + "\n"


def default_dispatcher_index_path() -> Path:
    """Resolve the default dispatcher-report index path lazily based on cwd.

    Dogfood (cwd inside install root) -> canonical central dispatcher-reports
    index; foreign project -> cwd-relative
    foreign_state_dirname()/dispatcher-reports.jsonl. Resolver errors ->
    foreign fallback.
    """
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            from config_provider import dispatcher_reports_relpath as _dispatcher_reports_relpath_fn  # noqa: PLC0415

            return hal_dir / _dispatcher_reports_relpath_fn()
    except (OSError, ValueError, RuntimeError):
        # resolver failure -> foreign fallback (fail-safe, spec §2.1d)
        return Path.cwd() / _foreign_state_dirname_fn() / "dispatcher-reports.jsonl"
    return Path.cwd() / _foreign_state_dirname_fn() / "dispatcher-reports.jsonl"


def resolve_dispatcher_index_path() -> Path:
    """Call-time env-seam resolver for the dispatcher-report index path.

    Returns Path(BD_DISPATCHER_REPORTS_LOG) if the env var is set and
    non-empty, else default_dispatcher_index_path(). Resolved at CALL time
    (GH497-D1 parity).
    """
    return _path_fn("BD_DISPATCHER_REPORTS_LOG", default_dispatcher_index_path())


def emit_dispatcher_report(
    *,
    run_id: str | None,
    phase: str,
    status: str,
    error_code: str | None,
    error: str | None,
    wall_ms: int,
    events_path: Path | str | None = None,
    report_dir: Path | str | None = None,
    index_path: Path | str | None = None,
) -> None:
    """Assemble a dispatcher markdown report + append one JSONL index row.

    Fail-safe: the entire body is wrapped in try/except — this helper must
    NEVER raise into the build pipeline.

    run_id falsy (None or "") -> suppressed entirely, before any other work.
    """
    try:
        if not run_id:
            return

        classification = classify_error_code(error_code, error)
        if events_path:
            stats = collect_run_stats(events_path, run_id, phase)
        else:
            stats = {
                "timeline": [], "failed_steps": [], "retries": 0, "retry_codes": [],
                "restart_reasons": [], "restart_denials": [], "rollup": None,
            }

        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        failed_steps = stats.get("failed_steps") or []
        step_name = failed_steps[-1].get("step_name") if failed_steps else None

        # ── (4) markdown report — INNER try/except: a md-write failure must
        # NOT lose the index row (§2.1d, gate r1 F1).
        report_path: str | None = None
        try:
            env_dir_raw = _env_opt_fn("BD_DISPATCHER_REPORT_DIR")
            if report_dir is not None:
                target_dir = Path(report_dir)
            elif env_dir_raw:
                target_dir = Path(env_dir_raw)
            elif events_path:
                target_dir = Path(events_path).parent / "dispatcher"
            else:
                target_dir = None

            if target_dir is not None:
                os.makedirs(target_dir, exist_ok=True)
                ts_compact = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                fname = f"{_sanitize(phase)}-{_sanitize(error_code or 'NONE')}-{ts_compact}.md"
                dest = Path(target_dir) / fname
                meta = {
                    "run_id": run_id, "phase": phase, "status": status,
                    "error_code": error_code, "classification": classification,
                    "wall_ms": wall_ms, "ts": ts, "error": error,
                }
                dest.write_text(render_report(meta, stats), encoding="utf-8")
                report_path = str(Path(dest).resolve())
        except Exception:  # noqa: BLE001
            report_path = None

        rollup = stats.get("rollup") or {}
        truncated_error = error if error is None else str(error)[:_ERROR_TRUNC_LEN]

        record = {
            "ts": ts,
            "run_id": run_id,
            "phase": phase,
            "status": status,
            "error_code": error_code,
            "classification": classification,
            "step_name": step_name,
            "wall_ms": wall_ms,
            "retries": stats.get("retries", 0),
            "restart_reasons": stats.get("restart_reasons", []),
            "cost_usd": rollup.get("cost_usd"),
            "tokens_in": rollup.get("tokens_in"),
            "tokens_out": rollup.get("tokens_out"),
            "calls": rollup.get("calls"),
            "report_path": report_path,
            "error": truncated_error,
        }
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        encoded = line.encode("utf-8")

        if len(encoded) > _LINE_LIMIT_BYTES:
            return

        resolved_path: Path = Path(index_path) if index_path is not None else resolve_dispatcher_index_path()

        parent = Path(resolved_path).parent
        os.makedirs(parent, exist_ok=True)

        fd = os.open(str(resolved_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, encoded)
        os.close(fd)
    except Exception:  # noqa: BLE001
        return
