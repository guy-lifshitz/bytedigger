#!/usr/bin/env python3
"""ci_main_heartbeat.py — deterministic main-liveness gate for ci.yml (bd#12).

bd#12 was not a red CI, it was a silent one: a permanently unschedulable run
held the serialising concurrency group `ci-refs/heads/main`, so every later
push to main entered `pending` and was evicted by the next one. Nothing was
red; `main` simply stopped executing, invisibly, for a day.

This check asserts the outcome rather than that cause: **in the last N days
(default 7), `main` has at least one EXECUTED run of `.github/workflows/ci.yml`**.

    executed(run) := status == "completed"
                 and conclusion in {success, failure, timed_out}
                 and jobs_total_count >= 1

All three clauses are load-bearing. The wedged run `30241562817` carried
`jobs_total_count == 3` with `conclusion == null` while `status == "queued"` —
its jobs were *created*, never *run*, so a predicate keyed only on the job
count would have reported bd#12 as healthy. `conclusion` is matched against an
allowlist, not against a `!= "cancelled"` denylist, so `skipped`,
`startup_failure` and `action_required` also read as NOT executed. A `failure`
conclusion, by contrast, counts as alive: a red main is a signal, silence is not.

The quantifier is ∃, never "the latest run is executed": after the bd#12 fix
every push to main produces a run that is `queued`/`conclusion=null` for its
first seconds, and a latest-run reading would flap red on a healthy repo.

Usage:
    ci_main_heartbeat.py [--days N] [--now ISO8601]              # live (gh api)
    ci_main_heartbeat.py --runs-json PATH [--days N] [--now ISO8601]

`--runs-json PATH` reads a captured snapshot and performs no network call and
no `gh` invocation; without it the runs are collected live via `gh api`, scoped
to `ci.yml` and asking for a page large enough to cover the window. Both modes
feed the same predicate through one code path. `--now` sets the window's right
edge (default: current UTC); the window is closed on both ends,
`now - days <= created_at <= now`. `--days` must be a non-negative integer; `0`
is a legal, empty window.

Exit codes:
    0  — at least one executed run of ci.yml on main inside the window
    1  — zero executed runs inside the window, including an empty run list
         (a repo with no runs at all is unhealthy, never vacuously healthy)
    2  — usage error, malformed or shape-invalid input, or a live-mode failure

Never a bare traceback: an uncaught exception would exit 1, which this contract
reads as "main is dead" — a crash disguised as a verdict.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW_FILE = "ci.yml"
BRANCH = "main"

# Allowlist, not a denylist: an unknown or absent conclusion is NOT executed.
EXECUTED_CONCLUSIONS = frozenset({"success", "failure", "timed_out"})

# The live query must cover the whole window; a small page reinstates the
# latest-run reading the ∃ quantifier forbids, in the only mode CI runs.
PER_PAGE = 100

REQUIRED_ENTRY_KEYS = ("path", "head_branch", "status", "created_at")


class HeartbeatError(Exception):
    """A fail-loud condition that must exit 2 with its reason named.

    Every raise site states which field or collector was wrong, so the report
    is never a traceback the contract would misread as a health verdict.
    """


def _parse_timestamp(raw: object, field: str) -> datetime:
    """Parse an ISO 8601 timestamp, normalised to UTC.

    A trailing `Z` is accepted (and required in practice — a timezone-naive
    timestamp cannot be compared against an aware one, and the resulting
    TypeError would exit 1, i.e. "main is dead").
    """
    if not isinstance(raw, str):
        raise HeartbeatError(
            f"{field}: expected an ISO 8601 timestamp string, got {type(raw).__name__}"
        )
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HeartbeatError(f"{field}: {raw!r} is not a parsable ISO 8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeartbeatError(
            f"{field}: {raw!r} is timezone-naive; an explicit offset is required "
            "(e.g. a trailing Z)"
        )
    return parsed.astimezone(timezone.utc)


def _resolve_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    return _parse_timestamp(raw, "--now")


def load_runs_document(path: Path) -> object:
    """Read a captured snapshot. Offline: no network, no `gh`."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise HeartbeatError(f"--runs-json: cannot read {path}: {exc.strerror}")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HeartbeatError(f"--runs-json: {path} is not valid JSON: {exc}")


def _gh_json(args: list[str]) -> object:
    """Run `gh` and parse its stdout as JSON. Any failure is a live-mode
    failure (exit 2), and the report always names `gh` as the collector."""
    printable = " ".join(["gh", *args])
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=120
        )
    except FileNotFoundError:
        raise HeartbeatError(f"gh: executable not found on PATH ({printable})")
    except OSError as exc:
        raise HeartbeatError(f"gh: could not run {printable}: {exc}")
    except subprocess.TimeoutExpired:
        raise HeartbeatError(f"gh: timed out after 120s ({printable})")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise HeartbeatError(f"gh: `{printable}` exited {proc.returncode}: {detail}")
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        head = proc.stdout[:200]
        raise HeartbeatError(
            f"gh: `{printable}` did not return JSON ({exc}); first bytes: {head!r}"
        )


def collect_live() -> object:
    """Fetch ci.yml's runs via `gh api`, scoped to the workflow and to main,
    asking for a page large enough to cover any sane window."""
    return _gh_json(
        [
            "api",
            "-X",
            "GET",
            f"repos/{{owner}}/{{repo}}/actions/workflows/{WORKFLOW_FILE}/runs",
            "-f",
            f"per_page={PER_PAGE}",
            "-f",
            f"branch={BRANCH}",
        ]
    )


def fill_jobs_counts(entries: list[dict]) -> None:
    """Normalise the live payload to the flat `jobs_total_count` field.

    The REST runs endpoint does NOT return a job count, so it is fetched from
    the per-run `/jobs` endpoint — but ONLY for entries that lack the flat
    field. Issuing that second call unconditionally would discard a count the
    collector already has and multiply the API traffic by the page size.
    """
    for entry in entries:
        if "jobs_total_count" in entry:
            continue
        run_id = entry.get("id")
        if run_id is None:
            raise HeartbeatError(
                "gh: a workflow-run entry carries neither jobs_total_count nor id, "
                "so its job count cannot be resolved"
            )
        payload = _gh_json(
            ["api", "-X", "GET", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs"]
        )
        total = payload.get("total_count") if isinstance(payload, dict) else None
        if isinstance(total, bool) or not isinstance(total, int):
            raise HeartbeatError(
                f"gh: /actions/runs/{run_id}/jobs returned no integer total_count, "
                f"so jobs_total_count is unknown (got {total!r})"
            )
        entry["jobs_total_count"] = total


def validate_document(payload: object) -> list[tuple[dict, datetime]]:
    """Shape-check the document and pre-parse every `created_at`.

    Shape errors are usage errors (2), never health verdicts (0/1): the whole
    point of the exit-code split is that "I could not tell" is distinguishable
    from "main is dead".
    """
    if not isinstance(payload, dict):
        raise HeartbeatError(
            "input is not a JSON object carrying a workflow_runs list (got "
            f"{type(payload).__name__})"
        )
    if "workflow_runs" not in payload:
        raise HeartbeatError("input has no workflow_runs key")
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise HeartbeatError(
            f"workflow_runs is not a list (got {type(runs).__name__})"
        )

    validated: list[tuple[dict, datetime]] = []
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict):
            raise HeartbeatError(
                f"workflow_runs[{index}] is not an object (got {type(entry).__name__})"
            )
        for key in REQUIRED_ENTRY_KEYS:
            if key not in entry:
                raise HeartbeatError(f"workflow_runs[{index}] has no {key} key")
        created = _parse_timestamp(
            entry.get("created_at"), f"workflow_runs[{index}].created_at"
        )
        validated.append((entry, created))
    return validated


def is_executed(entry: dict) -> bool:
    """The bd#12 predicate. `jobs_total_count` is validated here rather than in
    validate_document() so that only entries that already matched the workflow,
    the branch and the window are held to it."""
    if "jobs_total_count" not in entry:
        raise HeartbeatError(
            f"workflow run {entry.get('id')!r} has no jobs_total_count key"
        )
    jobs = entry.get("jobs_total_count")
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise HeartbeatError(
            f"workflow run {entry.get('id')!r}: jobs_total_count is not an integer "
            f"(got {type(jobs).__name__} {jobs!r})"
        )
    return (
        entry.get("status") == "completed"
        and entry.get("conclusion") in EXECUTED_CONCLUSIONS
        and jobs >= 1
    )


def check(days: int, now_raw: str | None, runs_json: str | None) -> int:
    if days < 0:
        raise HeartbeatError(
            f"--days must be a non-negative integer, got {days} (a negative window "
            "is inverted, which is a usage error, never a health verdict)"
        )
    now = _resolve_now(now_raw)
    left_edge = now - timedelta(days=days)

    live = runs_json is None
    if live:
        payload = collect_live()
    else:
        payload = load_runs_document(Path(runs_json))

    candidates = [
        entry
        for entry, created in validate_document(payload)
        # `path` is canonical; the free-text `name:` is not an identity.
        if entry.get("path") == WORKFLOW_PATH
        # Exact equality: a substring match would let `main-backup` vouch for main.
        and entry.get("head_branch") == BRANCH
        # Closed on both ends. `event` is deliberately not part of the filter.
        and left_edge <= created <= now
    ]
    if live:
        fill_jobs_counts(candidates)

    executed = [entry for entry in candidates if is_executed(entry)]
    window = f"{left_edge.isoformat()} .. {now.isoformat()}"

    if executed:
        print(
            f"OK: {len(executed)} executed run(s) of {WORKFLOW_PATH} on {BRANCH} "
            f"in the last {days} days ({window})"
        )
        return 0

    print(
        f"UNHEALTHY: no executed run of {WORKFLOW_PATH} on {BRANCH} in the last "
        f"{days} days ({window}); {len(candidates)} run(s) matched the workflow, "
        "the branch and the window, none of them executed"
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assert that main has at least one EXECUTED run of "
            f"{WORKFLOW_PATH} in the last N days. Exit 0 healthy, 1 unhealthy, "
            "2 usage error / shape-invalid input / live-mode failure."
        ),
    )
    parser.add_argument(
        "--runs-json",
        metavar="PATH",
        help=(
            "Read runs from a captured snapshot instead of calling gh "
            "(fully offline: no network, no gh invocation)."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window size in days, non-negative (default: 7).",
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help=(
            "Right edge of the window, timezone-aware (default: current UTC). "
            "A naive or unparsable value is a usage error."
        ),
    )
    args = parser.parse_args()

    try:
        return check(args.days, args.now, args.runs_json)
    except HeartbeatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
