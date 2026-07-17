"""Receipts line generator for Decisions/*.md run-series docs (GH923 lot 4).

Deterministic markdown receipts block per run, assembled from the same
sources the engine already writes: the events JSONL (terminal
workflow_finished, cost rollup, retries) and the incident ledger (lot-1
schema; classification/workaround mutated by lot-2 CLI). NO LLM calls.

Fail-closed CLI library (like lib/incident_classify.py): errors surface as a
nonzero exit code + stderr message.

Spec: decision doc 2026-07-17_GH923L4_receipts_line_spec.md (host repo).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.dispatcher_report import collect_run_stats  # noqa: E402
from lib.incident_ledger import resolve_incident_log_path  # noqa: E402


def load_incidents(ledger_path: Path | str, run_id: str) -> list[dict[str, Any]]:
    """Read the incident ledger, keep rows matching run_id, in file order.

    Skips blank/malformed/non-dict rows. Missing/unreadable file -> [].
    Never raises.
    """
    try:
        path_obj = Path(ledger_path)
        if not path_obj.exists():
            return []
        result: list[dict[str, Any]] = []
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
                result.append(row)
        return result
    except Exception:  # noqa: BLE001
        return []


def collect_terminal(events_path: Path | str, run_id: str) -> dict[str, Any] | None:
    """One pass over events JSONL; return the LAST workflow_finished row for run_id.

    Skips blank/malformed/non-dict rows. No matching row / missing file / any
    error -> None. Never raises.
    """
    try:
        path_obj = Path(events_path)
        if not path_obj.exists():
            return None
        last: dict[str, Any] | None = None
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
                if row.get("event_type") != "workflow_finished":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                last = {
                    "ts": row.get("ts"),
                    "workflow_name": payload.get("workflow_name"),
                    "status": payload.get("status"),
                    "wall_ms": payload.get("wall_ms"),
                }
        return last
    except Exception:  # noqa: BLE001
        return None


def _fmtK(n: Any) -> str:
    """Format a token count: >=1000 -> "N.NK", else str(n); non-numeric -> "?"."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return "?"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def _outcome(status: Any, incidents: list[dict[str, Any]]) -> str:
    if status == "ok":
        return "✅ DONE"
    if status in ("error", "escalate"):
        last_code = None
        for inc in incidents:
            code = inc.get("error_code")
            if code:
                last_code = code
        return f"✗ FAIL @ {last_code if last_code else status}"
    return f"⏸ {status}"


def _render_economics(rollup: Any) -> str:
    if not isinstance(rollup, dict):
        return "**n/a (no rollup)**"
    cost_usd = rollup.get("cost_usd")
    cost_str = (
        f"${float(cost_usd):.2f}"
        if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool)
        else "$?"
    )
    calls = rollup.get("calls")
    calls_str = str(calls) if isinstance(calls, (int, float)) and not isinstance(calls, bool) else "?"
    tokens_str = _fmtK(rollup.get("tokens_out"))
    by_cycle = rollup.get("by_cycle")
    n_cycles = len(by_cycle) if isinstance(by_cycle, dict) else 0
    cycles_seg = f" / {n_cycles} cycles" if n_cycles else ""
    return f"**{cost_str} / {calls_str} calls / {tokens_str} tokens_out{cycles_seg}**"


def render_receipts(
    terminal: dict[str, Any], stats: dict[str, Any], incidents: list[dict[str, Any]], run_id: str,
) -> str:
    """Pure markdown render — no I/O. Returns block ending with exactly one "\\n"."""
    workflow_name = terminal.get("workflow_name") or "?"
    status = terminal.get("status")
    outcome = _outcome(status, incidents)

    lines: list[str] = []
    lines.append(f"## Run — {run_id} ({workflow_name}) — {outcome}")
    lines.append("")

    ts = terminal.get("ts") or "?"
    wall_ms = terminal.get("wall_ms")
    if isinstance(wall_ms, (int, float)) and not isinstance(wall_ms, bool):
        wall_min = wall_ms / 60000
        lines.append(f"- Date: {ts} · wall ≈ {wall_min:.1f} min")
    else:
        lines.append(f"- Date: {ts}")

    timeline = stats.get("timeline") or []
    n_total = len(timeline)
    n_ok = sum(1 for entry in timeline if entry.get("status") == "ok")
    retries = stats.get("retries", 0)
    lines.append(f"- Steps: {n_ok}/{n_total} ok · retries: {retries}")

    if incidents:
        lines.append(f"- Incidents: {len(incidents)}")
        for inc in incidents:
            phase = inc.get("phase")
            step_name = inc.get("step_name")
            code = inc.get("error_code") or "no-code"
            classification = inc.get("classification")
            workaround = inc.get("workaround")
            suffix = f" — workaround: {workaround}" if workaround else ""
            lines.append(f"  - `{phase}/{step_name}` {code} [{classification}]{suffix}")
    else:
        lines.append("- Incidents: none")

    lines.append(f"- Economics: {_render_economics(stats.get('rollup'))}")

    return "\n".join(lines) + "\n"


def generate_receipts(
    *,
    run_id: str,
    events_path: Path | str,
    ledger_path: Path | str | None = None,
    append_path: Path | str | None = None,
) -> tuple[int, str, str]:
    """Assemble a receipts block; optionally append it to a target markdown doc."""
    terminal = collect_terminal(events_path, run_id)
    if terminal is None:
        return 4, "", f"no terminal workflow_finished for run {run_id} in {events_path}"

    stats = collect_run_stats(events_path, run_id, terminal.get("workflow_name") or "")

    resolved_ledger_path: Path | str = ledger_path if ledger_path is not None else resolve_incident_log_path()
    incidents = load_incidents(resolved_ledger_path, run_id)

    block = render_receipts(terminal, stats, incidents, run_id)

    if append_path is None:
        return 0, block, ""

    target = Path(append_path)
    header_line = block.split("\n")[0]

    try:
        existing: str | None = target.read_text(encoding="utf-8") if target.exists() else None
    except OSError as exc:
        return 5, "", f"append failed: {exc}"

    if existing is not None and header_line in existing:
        return 6, "", f"receipts for run {run_id} already present in {append_path}"

    if not existing:
        suffix = block
    elif existing.endswith("\n\n"):
        suffix = block
    elif existing.endswith("\n"):
        suffix = "\n" + block
    else:
        suffix = "\n\n" + block

    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(suffix)
    except OSError as exc:
        return 5, "", f"append failed: {exc}"

    return 0, block, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--append")
    args = parser.parse_args(argv)

    exit_code, stdout, stderr = generate_receipts(
        run_id=args.run,
        events_path=args.events,
        ledger_path=args.ledger,
        append_path=args.append,
    )
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return exit_code
