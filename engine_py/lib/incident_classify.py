"""Incident classification CLI — mutates classification/workaround of an
existing incident-ledger record in place (GH923 lot 2, ported).

Fail-closed (unlike lib/incident_ledger.py's fail-safe emit_incident):
errors surface as a nonzero exit code + stderr message.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.incident_ledger import resolve_incident_log_path  # noqa: E402

CLASSIFICATIONS: tuple[str, ...] = ("gate-FP", "legit", "env", "unknown")
_WORKAROUND_TRUNC_LEN = 500


def load_entries(path: Path) -> list[tuple[str, dict[str, Any] | None]]:
    """Read the ledger, split into lines, parse each as JSON where possible.

    Returns a list of (raw, record-or-None) pairs, one per line, in file
    order. raw never includes a trailing newline. Missing file raises
    FileNotFoundError.
    """
    text = Path(path).read_text(encoding="utf-8")
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]

    entries: list[tuple[str, dict[str, Any] | None]] = []
    for raw in raw_lines:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict):
            parsed = None
        entries.append((raw, parsed))
    return entries


def match_indices(
    entries: list[tuple[str, dict[str, Any] | None]],
    *,
    run_id: str,
    phase: str | None = None,
    step_name: str | None = None,
    cycle: int | None = None,
) -> list[int]:
    """Indices of parseable records matching run_id and the given filters."""
    filters = {"phase": phase, "step_name": step_name, "cycle": cycle}
    idxs: list[int] = []
    for i, (_raw, rec) in enumerate(entries):
        if rec is None:
            continue
        if rec.get("run_id") != run_id:
            continue
        matched = True
        for key, val in filters.items():
            if val is None:
                continue
            if rec.get(key) != val:
                matched = False
                break
        if matched:
            idxs.append(i)
    return idxs


def classify(
    path: Path,
    *,
    run_id: str,
    classification: str,
    workaround: str | None = None,
    phase: str | None = None,
    step_name: str | None = None,
    cycle: int | None = None,
    last: bool = False,
) -> tuple[int, str, str]:
    """Mutate the classification/workaround of a single matching ledger record."""
    try:
        entries = load_entries(path)
    except (FileNotFoundError, OSError):
        return 4, "", f"no ledger at {path}"

    idxs = match_indices(
        entries, run_id=run_id, phase=phase, step_name=step_name, cycle=cycle,
    )
    if not idxs:
        return 4, "", "no matching incident"

    if len(idxs) > 1 and not last:
        candidate_lines = []
        for idx in idxs:
            rec = entries[idx][1]
            assert rec is not None
            candidate = {
                "idx": idx,
                "ts": rec.get("ts"),
                "run_id": rec.get("run_id"),
                "phase": rec.get("phase"),
                "step_name": rec.get("step_name"),
                "cycle": rec.get("cycle"),
                "status": rec.get("status"),
                "classification": rec.get("classification"),
            }
            candidate_lines.append(json.dumps(candidate, separators=(",", ":"), default=str))
        return 3, "", "\n".join(candidate_lines)

    target_idx = idxs[-1] if last else idxs[0]
    target_rec = entries[target_idx][1]
    assert target_rec is not None

    target_rec["classification"] = classification
    if workaround is not None:
        target_rec["workaround"] = workaround[:_WORKAROUND_TRUNC_LEN]

    new_raw = json.dumps(target_rec, separators=(",", ":"), default=str)
    entries[target_idx] = (new_raw, target_rec)

    new_content = "".join(raw + "\n" for raw, _rec in entries)

    tmp_path = path.parent / (path.name + ".tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        with contextlib.suppress(OSError):  # best-effort tmp cleanup; primary failure already reported via exit 5
            tmp_path.unlink()
        return 5, "", f"rewrite failed: {exc}"

    return 0, json.dumps(target_rec, separators=(",", ":"), default=str) + "\n", ""


def list_records(
    path: Path,
    *,
    run_id: str | None = None,
    unclassified: bool = False,
) -> tuple[int, str, str]:
    """Print each parseable record, filtered by run_id and/or unclassified."""
    try:
        entries = load_entries(path)
    except (FileNotFoundError, OSError):
        return 0, "", ""

    lines = []
    for _raw, rec in entries:
        if rec is None:
            continue
        if run_id is not None and rec.get("run_id") != run_id:
            continue
        if unclassified and rec.get("classification") != "unknown":
            continue
        lines.append(json.dumps(rec, separators=(",", ":"), default=str))

    stdout = "".join(line + "\n" for line in lines)
    return 0, stdout, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--run")
    parser.add_argument("--class", dest="classification", choices=CLASSIFICATIONS)
    parser.add_argument("--phase")
    parser.add_argument("--step")
    parser.add_argument("--cycle", type=int)
    parser.add_argument("--workaround")
    parser.add_argument("--last", action="store_true")
    parser.add_argument("--unclassified", action="store_true")
    parser.add_argument("--path")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else resolve_incident_log_path()

    if args.list:
        exit_code, stdout, stderr = list_records(
            path, run_id=args.run, unclassified=args.unclassified,
        )
    else:
        if not args.run or not args.classification:
            parser.error("--run and --class are required in classify mode")
        exit_code, stdout, stderr = classify(
            path,
            run_id=args.run,
            classification=args.classification,
            workaround=args.workaround,
            phase=args.phase,
            step_name=args.step,
            cycle=args.cycle,
            last=args.last,
        )

    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return exit_code
