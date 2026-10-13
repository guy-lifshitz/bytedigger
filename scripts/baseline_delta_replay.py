#!/usr/bin/env python3
"""bd#299: replay harness for baseline_delta_gate.py (stdlib only).

Usage: baseline_delta_replay.py --cases cases.json [--out table.md]
       [--json out.json] [--base-ref HEAD]

Each case: {"id", "results", "base_fails"?, "expect"?, "seeded"?}. The driver
is run with --base-ref (default HEAD, a non-remote ref, so no network).
Exit 0 always unless cases.json is unreadable (exit 2).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = REPO / "baseline_delta_gate.py"
TABLE_HEADER = "| id | verdict | expect | false_block | caught |"


def _error_row(case, expect, seeded):
    return {
        "id": case.get("id"), "verdict": "ERROR", "delta_verdict": None,
        "new_fails": [], "blocked_by": [], "corpus_scope_reason": None,
        "expect": expect, "seeded": seeded, "false_block": False, "caught": False,
    }


def run_case(case, base_ref="HEAD"):
    expect = case.get("expect")
    seeded = bool(case.get("seeded", False))
    row = _error_row(case, expect, seeded)
    base_path = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(f"FAILED {i}" for i in (case.get("base_fails") or [])))
            if case.get("base_fails"):
                fh.write("\n")
            base_path = fh.name
        argv = [
            sys.executable, str(DRIVER), "--results", str(case.get("results")),
            "--suite", "pytest", "--require-corpus-parity",
            "--baseline", base_path, "--base-ref", base_ref,
        ]
        try:
            proc = subprocess.run(argv, cwd=str(REPO), capture_output=True,
                                  text=True, timeout=300)
        except (subprocess.TimeoutExpired, OSError):
            return row
        if proc.returncode not in (0, 4, 5):
            return row
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        try:
            out = json.loads(lines[-1]) if lines else None
        except ValueError:
            out = None
        if not isinstance(out, dict) or out.get("verdict") not in ("PASS", "FAIL", "BLOCKED"):
            return row
        row["verdict"] = out["verdict"]
        row["delta_verdict"] = out.get("delta_verdict")
        row["new_fails"] = list(out.get("new_fails") or [])
        row["blocked_by"] = list(out.get("blocked_by") or [])
        row["corpus_scope_reason"] = out.get("corpus_scope_reason")
    finally:
        if base_path:
            try:
                os.unlink(base_path)
            except OSError:
                pass
    row["false_block"] = expect == "PASS" and row["verdict"] != "PASS"
    row["caught"] = seeded and row["verdict"] == expect
    return row


def summarize(rows):
    counts = {k: sum(1 for r in rows if r.get("verdict") == k)
              for k in ("PASS", "FAIL", "BLOCKED", "ERROR")}
    false_blocks = sum(1 for r in rows if r.get("false_block"))
    seeded = [r for r in rows if r.get("seeded")]
    caught = sum(1 for r in seeded if r.get("caught"))
    return (f"PASS={counts['PASS']} FAIL={counts['FAIL']} BLOCKED={counts['BLOCKED']} "
            f"ERROR={counts['ERROR']} false_blocks={false_blocks} "
            f"seeded_caught={caught}/{len(seeded)}")


def render_table(rows):
    lines = [TABLE_HEADER, "|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {} | {} | {} | {} | {} |".format(
            r["id"], r["verdict"], r["expect"] if r["expect"] is not None else "",
            "yes" if r["false_block"] else "no",
            "yes" if r["caught"] else "no"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", dest="json_out", default=None)
    parser.add_argument("--base-ref", default="HEAD")
    args = parser.parse_args(argv)

    try:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        if not isinstance(cases, list):
            raise ValueError("cases.json must be a list")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"baseline_delta_replay: cannot read cases {args.cases}: {exc}\n")
        return 2

    rows = [run_case(c, args.base_ref) for c in cases]
    summary = summarize(rows)
    table = render_table(rows)
    if args.out:
        Path(args.out).write_text(table, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"rows": rows, "summary": summary}, indent=2), encoding="utf-8")
    sys.stdout.write(table)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
