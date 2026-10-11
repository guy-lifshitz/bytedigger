#!/usr/bin/env python3
"""ci_pytest_headroom.py -- warn when the pytest CI job nears its ceiling (bd#285).

Reads the job-level `timeout-minutes` of one job (default `pytest`) out of a
workflow file and compares it with the elapsed seconds of the job. When the
elapsed time is strictly above 70% of the ceiling it prints a GitHub
`::warning::` annotation. Stdlib only.

Usage:
    ci_pytest_headroom.py --duration-sec N [--workflow PATH] [--job NAME]

`--workflow` defaults to `<repo>/.github/workflows/ci.yml`, resolved from this
script's own location (not the cwd: the CI step runs in `engine_py`).

The ceiling is found by a line scan: the job block starts at the line
`  <job>:` (2-space indent) and ends at the next 2-space-indent key; the
ceiling is the first line in it with exactly 4-space indent
`timeout-minutes: <value>`. The value may be quoted and may carry a trailing
`# comment`; it must be a positive integer, otherwise it is unreadable.

Switch: env BD_CI_PYTEST_HEADROOM_WARN. Unset, or any value other than
0/false/off/no (case-insensitive, trimmed), is enabled. Disabled prints one
`skipped:` line, checked before anything else is read.

Exit codes:
    0  -- always (a check that could not run prints a `::warning::`)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SWITCH_ENV = "BD_CI_PYTEST_HEADROOM_WARN"
DISABLED_VALUES = {"0", "false", "off", "no"}
DEFAULT_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

_KEY_RE = re.compile(r"^  [A-Za-z0-9_-]+:")
_CEILING_RE = re.compile(r"^    timeout-minutes:[ \t]*(?:\"(\d+)\"|'(\d+)'|(\d+))[ \t]*(?:#.*)?$")


def read_ceiling_minutes(text: str, job: str) -> int | None:
    """Job-level timeout-minutes of `job`, or None when unreadable."""
    inside = False
    header = f"  {job}:"
    for line in text.splitlines():
        if not inside:
            if line.rstrip() == header:
                inside = True
            continue
        if _KEY_RE.match(line):
            return None
        if line.startswith("    timeout-minutes:") and not line.startswith("     "):
            m = _CEILING_RE.match(line)
            if not m:
                return None
            value = int(next(g for g in m.groups() if g is not None))
            return value if value > 0 else None
    return None


def _enabled() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip().lower() not in DISABLED_VALUES


def _could_not_run(reason: str) -> None:
    print(f"::warning::pytest headroom check could not run: {reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration-sec", type=str, default=None)
    parser.add_argument("--workflow", type=str, default=None)
    parser.add_argument("--job", type=str, default="pytest")
    args = parser.parse_args(argv)

    if not _enabled():
        print(f"skipped: pytest headroom check disabled ({SWITCH_ENV})")
        return 0

    workflow = Path(args.workflow) if args.workflow is not None else DEFAULT_WORKFLOW
    try:
        text = workflow.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        _could_not_run(f"cannot read workflow {workflow}: {exc.__class__.__name__}")
        return 0
    minutes = read_ceiling_minutes(text, args.job)
    if minutes is None:
        _could_not_run(f"no readable job-level timeout-minutes for job '{args.job}' in {workflow}")
        return 0
    ceiling = minutes * 60

    raw = args.duration_sec
    if raw is None or not re.fullmatch(r"\d+", raw.strip()):
        _could_not_run(f"--duration-sec is not a non-negative integer: {raw!r}")
        return 0
    duration = int(raw.strip())

    if duration * 10 > ceiling * 7:
        pct = duration * 100 // ceiling
        print(
            f"::warning::pytest job used {duration}s of its {ceiling}s ceiling "
            f"({pct}%, warn above 70%): raise timeout-minutes in "
            ".github/workflows/ci.yml or speed the suite"
        )
    else:
        print(f"ok: pytest job used {duration}s of its {ceiling}s ceiling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
