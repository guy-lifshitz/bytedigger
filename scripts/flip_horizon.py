#!/usr/bin/env python3
"""flip_horizon.py -- self-expiring guard for dated rollout horizons.

A `flip-by:` / `kill-by:` / `retire-by:` token (`kind:YYYY-MM-DD`) in a
`flags_catalog.FLAGS[*]["description"]` is a promise; once its date passes the
flag must either be flipped (token removed) or carry a time-boxed entry in
`scripts/flip_horizon_ledger.json`. Registry: `FLAGS` (single source) + the
ledger. Spec: docs/decisions/2026-10-03-bd-flip-horizon-guard.md.

Usage:
    flip_horizon.py --check [--today YYYY-MM-DD]

Exit codes:
    0  -- clean
    1  -- one problem line per (flag, code) on stdout
    2  -- unreadable ledger JSON / not an object of objects (stderr), or
          argparse usage error
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

MAX_DAYS = 45

LEDGER_PATH = Path(__file__).resolve().parent / "flip_horizon_ledger.json"
ENGINE_PY = Path(__file__).resolve().parent.parent / "engine_py"

# Near-miss superset: kind (any case), optional ':' and spaces, date-looking.
_CANDIDATE_RE = re.compile(
    r"(flip|kill|retire)-by:?[ \t]*\d{4}-\d{1,2}-\d{1,2}", re.IGNORECASE
)
_CANONICAL_RE = re.compile(r"(flip|kill|retire)-by:(\d{4}-\d{2}-\d{2})")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def default_today() -> datetime.date:
    """UTC calendar date; the only clock read in this module."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def _parse_date(value: object) -> datetime.date | None:
    if not isinstance(value, str) or not _ISO_RE.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _scan(flags: dict) -> tuple[list[tuple[str, str, str]], set[str]]:
    """Return (canonical tokens, flags having a near-miss), never raising."""
    tokens: list[tuple[str, str, str]] = []
    bad: set[str] = set()
    for flag, meta in flags.items():
        desc = meta.get("description") if isinstance(meta, dict) else None
        if not isinstance(desc, str):
            continue
        for m in _CANDIDATE_RE.finditer(desc):
            c = _CANONICAL_RE.fullmatch(m.group(0))
            if c is None:
                bad.add(flag)
            else:
                tokens.append((flag, c.group(0).split(":", 1)[0], c.group(2)))
    return tokens, bad


def find_tokens(flags: dict) -> list[tuple[str, str, str]]:
    """Every canonical `(flip|kill|retire)-by:<YYYY-MM-DD>` as (flag, kind, date_str)."""
    return _scan(flags)[0]


def check(flags: dict, ledger: dict, today: datetime.date) -> list[str]:
    tokens, bad = _scan(flags)
    overdue: set[str] = set()
    bad_flags = set(bad)
    for flag, _kind, ds in tokens:
        d = _parse_date(ds)
        if d is None:
            bad_flags.add(flag)
        elif d < today:
            overdue.add(flag)

    problems: list[str] = []
    for flag in flags:
        if flag in bad_flags:
            problems.append(f"BAD_TOKEN {flag}: malformed flip/kill/retire-by token")
        if flag in overdue and flag not in ledger:
            problems.append(f"UNCOVERED {flag}: overdue horizon token, no ledger entry")

    limit = today + datetime.timedelta(days=MAX_DAYS)
    order = [f for f in flags if f in ledger] + [f for f in ledger if f not in flags]
    for flag in order:
        entry = ledger[flag]
        until = _parse_date(entry.get("until"))
        if until is None:
            problems.append(f"BAD_DATE {flag}: ledger until is not a real ISO date")
        elif until < today:
            problems.append(f"LAPSED {flag}: ledger until {until.isoformat()} passed")
        elif until > limit:
            problems.append(
                f"TOO_FAR {flag}: ledger until {until.isoformat()} is more than "
                f"{MAX_DAYS} days out"
            )
        for key in ("reason", "ref"):
            v = entry.get(key)
            if not (isinstance(v, str) and v.strip()):
                problems.append(f"NO_REASON {flag}: ledger entry missing non-blank reason/ref")
                break
        if flag not in overdue:
            problems.append(f"STALE {flag}: no overdue horizon token, delete the ledger entry")
    return problems


def _load_flags() -> dict:
    try:
        import bytedigger_engine  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(ENGINE_PY))
    from bytedigger_engine.flags_catalog import FLAGS

    return FLAGS


def _load_ledger(path: Path) -> dict:
    """Missing file -> {}. Raises ValueError on unreadable / wrong-shape JSON."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read ledger {path}: {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"ledger {path} is not valid JSON: {e}")
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError(f"ledger {path} must be a JSON object of objects")
    return data


def _iso_date_arg(value: str) -> datetime.date:
    d = _parse_date(value)
    if d is None:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}")
    return d


def main(argv=None, ledger_path=None, flags=None) -> int:
    parser = argparse.ArgumentParser(description="Guard dated rollout horizons in the flag catalog.")
    parser.add_argument("--check", action="store_true", help="Run the check (default).")
    parser.add_argument("--today", type=_iso_date_arg, default=None, help="Override today (YYYY-MM-DD).")
    args = parser.parse_args(argv)

    today = args.today if args.today is not None else default_today()
    path = Path(ledger_path) if ledger_path is not None else LEDGER_PATH
    try:
        ledger = _load_ledger(path)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if flags is None:
        flags = _load_flags()

    problems = check(flags, ledger, today)
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
