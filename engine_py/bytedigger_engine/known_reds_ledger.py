"""GH1164 (agreement 5477C5BE) — THE shared sticky ledger-table parser.

One parser for `known-reds.md`-style markdown pipe tables, imported by every
consumer (`ci-known-reds-filter.py`, `baseline_delta_gate.py`). Stdlib only.

Contract (spec §1.1):
  - A line whose strip() does not start with "|" is skipped without touching
    any state — prose can never close a table.
  - Separator rows are dropped.
  - A "|"-row is a header (and is dropped) iff the next "|"-row later in the
    file (prose skipped) is a separator row.
  - Everything else is a data row, returned with its cell list unmodified.
    No cell-count filtering happens here: column-shape policy belongs to
    each caller.

GH1199 kill-by layer (agreement CD666B9B-D319-403D-8EF2-8B8870E0F834):
ONE canonical date/enforcement classifier for the ledger's Kill-by column,
shared by ci-known-reds-filter.py and baseline_delta_gate.py (§1g).
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

_SEPARATOR_CELL_RE = re.compile(r"-+")

LEDGER_COLUMNS = 6          # | Suite | Red | Scope | Issue | Kill-by | Class |
ISSUE_INDEX = 3
KILL_BY_INDEX = 4

KILL_BY_ACTIVE = "active"
KILL_BY_EXPIRED = "expired"
KILL_BY_MALFORMED = "malformed"

_KILL_BY_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def split_row(line: str) -> list[str]:
    """`"| a | b |"` -> `["a", "b"]`, cells stripped."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    """True iff there is at least one non-empty cell and all of them are `-+`."""
    non_empty = [c for c in cells if c != ""]
    if not non_empty:
        return False
    return all(_SEPARATOR_CELL_RE.fullmatch(c) for c in non_empty)


def parse_table_rows(text: str) -> list[tuple[int, list[str]]]:
    """Return `(1-based source line number, cells)` for every DATA row."""
    pipe_rows = [
        (i + 1, split_row(line))
        for i, line in enumerate(text.splitlines())
        if line.strip().startswith("|")
    ]
    separators = [is_separator_row(cells) for _, cells in pipe_rows]

    data_rows: list[tuple[int, list[str]]] = []
    for idx, (lineno, cells) in enumerate(pipe_rows):
        if separators[idx]:
            continue
        if idx + 1 < len(separators) and separators[idx + 1]:
            continue  # header: its next pipe row is a separator
        data_rows.append((lineno, cells))
    return data_rows


def classify_kill_by(cells: list[str], today: date) -> tuple[str, str]:
    """Classify a ledger row's kill-by cell against `today`.

    Order matters (§1.1): shape (len<LEDGER_COLUMNS) first, then empty cell,
    then the regex, then `date.fromisoformat`, then the `< today` comparison.
    A >6-cell row is NOT malformed-by-shape: KILL_BY_INDEX is authoritative.
    """
    if len(cells) < LEDGER_COLUMNS:
        return KILL_BY_MALFORMED, ""

    kill_by_text = cells[KILL_BY_INDEX]
    if not kill_by_text.strip():
        return KILL_BY_MALFORMED, kill_by_text
    if not _KILL_BY_DATE_RE.fullmatch(kill_by_text):
        return KILL_BY_MALFORMED, kill_by_text
    try:
        kill_by_date = date.fromisoformat(kill_by_text)
    except ValueError:
        return KILL_BY_MALFORMED, kill_by_text

    if kill_by_date < today:
        return KILL_BY_EXPIRED, kill_by_text
    return KILL_BY_ACTIVE, kill_by_text


def partition_by_kill_by(
    rows: list[tuple[int, list[str]]], today: date
) -> tuple[list[tuple[int, list[str]]], list[dict[str, object]]]:
    """Split `[(lineno, cells)]` into active rows (input order preserved) and
    inactive reports `{"line", "issue", "kill_by", "status"}`."""
    active_rows: list[tuple[int, list[str]]] = []
    inactive_reports: list[dict[str, object]] = []
    for lineno, cells in rows:
        status, kill_by_text = classify_kill_by(cells, today)
        if status == KILL_BY_ACTIVE:
            active_rows.append((lineno, cells))
            continue
        issue = cells[ISSUE_INDEX] if len(cells) >= LEDGER_COLUMNS else "?"
        inactive_reports.append(
            {
                "line": lineno,
                "issue": issue,
                "kill_by": kill_by_text,
                "status": status,
            }
        )
    return active_rows, inactive_reports


def format_inactive(report: dict[str, object], enforced: bool) -> str:
    """Single formatter for both consumers' stderr lines (§1aa)."""
    suffix = "DROPPED" if enforced else "WARN-ONLY"
    return (
        f"ledger line {report['line']}: issue {report['issue']} "
        f"kill-by {report['kill_by']} {report['status']} — "
        f"{suffix}, flip-by:2026-08-08"
    )


def resolve_today() -> date:
    """`date.today()`, overridden by HAL_KNOWN_REDS_TODAY=YYYY-MM-DD (§1i).

    Fail-closed: an EMPTY string must NOT mean "today" (MINOR-6) — only the
    variable's total absence (`raw is None`) falls back to the real date.
    """
    raw = os.environ.get("HAL_KNOWN_REDS_TODAY")
    if raw is None:              # NOT `if not raw:` — "" must not mean today
        return date.today()
    return date.fromisoformat(raw)


def kill_by_enforced() -> bool:
    """The ONE read site of HAL_KNOWN_REDS_KILL_BY_ENFORCE.

    rollout: CD666B9B-D319-403D-8EF2-8B8870E0F834 flip-by:2026-08-08
    """
    return os.environ.get("HAL_KNOWN_REDS_KILL_BY_ENFORCE") == "1"


# ─── GH1230 (agreement C4B6B16C-0E22-4286-8064-EEAFF9ECEE9A) ────────────────
# Owner-liveness: second, orthogonal deactivation axis for known-reds.md.
# Pure/offline (spec §1.1) — owner states are INJECTED by the caller, this
# module makes no network calls of any kind.

OWNER_ALIVE = "owner-alive"
OWNER_DEAD = "owner-dead"
OWNER_UNKNOWN = "owner-unknown"
OWNER_MALFORMED = "owner-malformed"

_ISSUE_REF_RE = re.compile(r"#(\d+)")


def parse_issue_ref(cells: list[str]) -> str | None:
    """`cells[ISSUE_INDEX]` iff it wholly (fullmatch) matches `#\\d+`.

    Returns the cell text VERBATIM, no normalization — `"#0641"` is
    returned as `"#0641"`, never coerced to `"#641"`.
    """
    if len(cells) <= ISSUE_INDEX:
        return None
    text = cells[ISSUE_INDEX]
    if _ISSUE_REF_RE.fullmatch(text):
        return text
    return None


def classify_owner(
    cells: list[str], owner_states: dict[str, object]
) -> tuple[str, str]:
    """Classify a ledger row's owning issue against injected `owner_states`.

    Branch order is fixed (§1.1):
      1. len(cells) < LEDGER_COLUMNS -> (OWNER_MALFORMED, "?")
      2. parse_issue_ref(cells) is None -> (OWNER_MALFORMED, raw Issue cell)
      3. ref not in owner_states -> (OWNER_UNKNOWN, ref)
      4. owner_states[ref] == "OPEN" -> (OWNER_ALIVE, ref)
      5. owner_states[ref] == "CLOSED" -> (OWNER_DEAD, ref)
      6. else -> (OWNER_UNKNOWN, ref)
    """
    if len(cells) < LEDGER_COLUMNS:
        return OWNER_MALFORMED, "?"

    ref = parse_issue_ref(cells)
    if ref is None:
        return OWNER_MALFORMED, cells[ISSUE_INDEX]

    if ref not in owner_states:
        return OWNER_UNKNOWN, ref

    state = owner_states[ref]
    if state == "OPEN":
        return OWNER_ALIVE, ref
    if state == "CLOSED":
        return OWNER_DEAD, ref
    return OWNER_UNKNOWN, ref


def partition_by_owner(
    rows: list[tuple[int, list[str]]], owner_states: dict[str, object]
) -> tuple[list[tuple[int, list[str]]], list[dict[str, object]]]:
    """Split `[(lineno, cells)]` into active rows (ALIVE ∪ UNKNOWN, input
    order preserved) and reports (DEAD ∪ MALFORMED)."""
    active_rows: list[tuple[int, list[str]]] = []
    reports: list[dict[str, object]] = []
    for lineno, cells in rows:
        status, ref = classify_owner(cells, owner_states)
        if status in (OWNER_ALIVE, OWNER_UNKNOWN):
            active_rows.append((lineno, cells))
            continue
        owner_state = owner_states.get(ref, "") if status == OWNER_DEAD else ""
        reports.append(
            {
                "line": lineno,
                "issue": ref,
                "owner_state": owner_state,
                "status": status,
            }
        )
    return active_rows, reports


def format_dead_owner(report: dict[str, object], enforced: bool) -> str:
    """Single formatter for the entry-point's stderr lines (§1aa)."""
    suffix = "BLOCKING" if enforced else "WARN-ONLY"
    return (
        f"ledger line {report['line']}: issue {report['issue']} "
        f"owner {report['owner_state']} {report['status']} — "
        f"{suffix}, flip-by:2026-08-08"
    )


def owner_enforced() -> bool:
    """The ONE read site of HAL_KNOWN_REDS_OWNER_ENFORCE.

    rollout: C4B6B16C-0E22-4286-8064-EEAFF9ECEE9A flip-by:2026-08-08
    """
    return os.environ.get("HAL_KNOWN_REDS_OWNER_ENFORCE") == "1"


# ─── GH1231: canonical Red-cell token extractor + shape lint ────────────────

RED_OK = "RED_OK"
RED_MULTI_TOKEN = "RED_MULTI_TOKEN"
RED_MALFORMED = "RED_MALFORMED"
RED_INDEX = 1

_RED_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]+")


def red_match_tokens(cells: list[str]) -> list[str]:
    """The ONE canonical Red-cell token extractor (GH1231). Verbatim port of
    the former ci-known-reds-filter.py:tokens_for_row(42-51)."""
    if len(cells) < 2:
        return []
    red_cell = cells[1]
    toks = _RED_TOKEN_RE.findall(red_cell)
    filtered = [t for t in toks if len(t) >= 5 and ("_" in t or "::" in t or "." in t)]
    if not filtered and len(red_cell.strip()) >= 12:
        # fallback (issue #904/#656): empty token list falls back to exact-full-cell substring candidate
        return [red_cell.strip()]
    return filtered


def classify_red_shape(cells: list[str]) -> tuple[str, str]:
    """GH1231 Red-cell shape lint. Branch order:
    1. len(cells) < LEDGER_COLUMNS -> (RED_MALFORMED, "?")
    2. exactly one anchor token -> (RED_OK, token)
    3. otherwise (0 or >=2 tokens) -> (RED_MULTI_TOKEN, detail)
    """
    if len(cells) < LEDGER_COLUMNS:
        return RED_MALFORMED, "?"
    tokens = red_match_tokens(cells)
    if len(tokens) == 1:
        return RED_OK, tokens[0]
    return RED_MULTI_TOKEN, ",".join(tokens)


def red_shape_enforced() -> bool:
    """The ONE read site of HAL_KNOWN_REDS_RED_SHAPE_ENFORCE.

    rollout: CB74189B-6A03-49DF-A65D-7520D528C45D flip-by:2026-08-08
    """
    return os.environ.get("HAL_KNOWN_REDS_RED_SHAPE_ENFORCE") == "1"


# ─── GH1470 (agreement 92237C8D-8B77-4294-8DC6-5B81020A86D7) ────────────────
# The ledger's `Scope` column starts being READ: the declared narrowing of a
# tolerated red becomes ENFORCED. Fourth deactivation axis of this file, built
# exactly like kill-by (GH1199), owner (GH1230) and red-shape (GH1231): ONE
# canonical classifier here, both consumers delegate to it (§1g).

SCOPE_APPLIES = "scope-applies"
SCOPE_OUT_OF_SCOPE = "scope-out-of-scope"
SCOPE_UNKNOWN = "scope-unknown"
SCOPE_MALFORMED = "scope-malformed"

_SCOPE_INDEX = 2
_SCOPE_FLIP_BY = "flip-by:2026-08-16"


@dataclass(frozen=True)
class RunContext:
    """The run context a Scope cell is compared against (§1.4).

    Everything false / lane empty is the NARROWEST context and the
    fail-closed default: an undeclared run must not silently be treated as
    the widest one — that is precisely the defect this axis fixes.
    """

    ci: bool = False
    full_run: bool = False
    xdist: bool = False
    standalone: bool = False
    lane: str = ""


def resolve_run_context() -> RunContext:
    """Read the five HAL_KNOWN_REDS_RUN_* seams (§1.4).

    `"1"` is the ONLY true value, exactly as for `kill_by_enforced()`; the
    lane is normalized with `strip().casefold()`.
    """
    return RunContext(
        ci=os.environ.get("HAL_KNOWN_REDS_RUN_CI") == "1",
        full_run=os.environ.get("HAL_KNOWN_REDS_RUN_FULL") == "1",
        xdist=os.environ.get("HAL_KNOWN_REDS_RUN_XDIST") == "1",
        standalone=os.environ.get("HAL_KNOWN_REDS_RUN_STANDALONE") == "1",
        lane=(os.environ.get("HAL_KNOWN_REDS_RUN_LANE") or "").strip().casefold(),
    )


def normalize_scope(cell: str) -> str:
    """§1.5 lookup key: strip + collapse inner whitespace + casefold."""
    return " ".join(cell.split()).casefold()


# §1.5 — the CLOSED enumeration. Every predicate is the LITERAL translation of
# its cell's wording, not one condition more. Extending the ledger's Scope
# vocabulary means editing THIS dict (and §1.5 of the spec) and nothing else.
SCOPE_VOCAB: dict[str, Callable[[RunContext], bool]] = {
    "any": lambda ctx: True,
    "full-run only": lambda ctx: ctx.full_run,
    "standalone sibling suite": lambda ctx: ctx.standalone,
    "ci engine-py xdist only":
        lambda ctx: ctx.ci and ctx.xdist and ctx.lane == "engine-py",
    "oss-engine xdist only":
        lambda ctx: ctx.xdist and ctx.lane == "oss-engine",
    "ci runners, full-run only": lambda ctx: ctx.ci and ctx.full_run,
    "ci full-run xdist only":
        lambda ctx: ctx.ci and ctx.full_run and ctx.xdist,
}


def classify_scope(cells: list[str], ctx: RunContext) -> tuple[str, str]:
    """Classify a ledger row's Scope cell against the declared run context.

    Branch order is fixed (§1.6), the same order `classify_kill_by` uses:
    shape -> normalization -> closed enumeration -> predicate.

    A >LEDGER_COLUMNS row is NOT malformed-by-shape: index 2 is authoritative,
    exactly as KILL_BY_INDEX is for `classify_kill_by`. An unrecognised value
    — the EMPTY cell included — is SCOPE_UNKNOWN and fails closed: there is no
    silent "treat it as any" fallback (§1.6, a direct requirement of the issue).
    """
    if len(cells) < LEDGER_COLUMNS:
        return SCOPE_MALFORMED, "?"

    raw = cells[_SCOPE_INDEX]
    predicate = SCOPE_VOCAB.get(normalize_scope(raw))
    if predicate is None:
        return SCOPE_UNKNOWN, raw.strip()
    if predicate(ctx):
        return SCOPE_APPLIES, raw.strip()
    return SCOPE_OUT_OF_SCOPE, raw.strip()


def partition_by_scope(
    rows: list[tuple[int, list[str]]], ctx: RunContext
) -> tuple[list[tuple[int, list[str]]], list[dict[str, object]]]:
    """Split `[(lineno, cells)]` into in-scope rows (input order preserved) and
    reports `{"line", "scope", "status"}` for everything that does NOT apply."""
    active_rows: list[tuple[int, list[str]]] = []
    reports: list[dict[str, object]] = []
    for lineno, cells in rows:
        status, detail = classify_scope(cells, ctx)
        if status == SCOPE_APPLIES:
            active_rows.append((lineno, cells))
            continue
        reports.append({"line": lineno, "scope": detail, "status": status})
    return active_rows, reports


def format_out_of_scope(report: dict[str, object], enforced: bool) -> str:
    """Single formatter for both consumers' stderr lines (§1aa).

    Deliberately NOT a copy of `format_inactive`: this axis owns its OWN line
    prefix `ledger scope line ` (rev 5). The bare `ledger`+`line` prefix
    belongs to the kill-by axis (`format_inactive`) and the red-shape axis
    (`known-reds-owner-audit.py`), and a third consumer greps that prefix
    deliberately (`__tests__/known-reds-flip-noop-23DA6B27.test.ts:127` requires
    ZERO such lines at the flip date), so the prefixes must not be shared.
    The flip-by token is printed in the warn-only variant ONLY: under
    enforcement the row is gone, there is nothing left to flip.
    """
    suffix = "DROPPED" if enforced else f"WARN-ONLY, {_SCOPE_FLIP_BY}"
    return (
        f"ledger scope line {report['line']}: scope {report['scope']} "
        f"{report['status']} — {suffix}"
    )


def format_scope_summary(
    parsed: int,
    suite_rows: int,
    active_rows: list[tuple[int, list[str]]],
    reports: list[dict[str, object]],
    enforced: bool,
    ctx: RunContext,
) -> str:
    """§1.7 one-line denominator, printed once per run that reads the ledger.

    `parsed` counts EVERY data row of the ledger; `suite_rows` counts the rows
    of this Suite that REACHED scope partitioning (i.e. already past kill-by).
    Invariant: applies + out-of-scope + unknown + malformed == suite_rows.
    """
    def _bit(value: bool) -> str:
        return "1" if value else "0"

    out_of_scope = sum(1 for r in reports if r["status"] == SCOPE_OUT_OF_SCOPE)
    unknown = sum(1 for r in reports if r["status"] == SCOPE_UNKNOWN)
    malformed = sum(1 for r in reports if r["status"] == SCOPE_MALFORMED)
    return (
        f"scope: parsed={parsed} suite_rows={suite_rows} "
        f"applies={len(active_rows)} out-of-scope={out_of_scope} "
        f"unknown={unknown} malformed={malformed} "
        f"enforced={_bit(enforced)} "
        f"ctx=ci={_bit(ctx.ci)},full={_bit(ctx.full_run)},"
        f"xdist={_bit(ctx.xdist)},standalone={_bit(ctx.standalone)},"
        f"lane={ctx.lane}"
    )


def format_disarmed_scope_warning(suite: str, suite_rows: int) -> str:
    """§1.6: "the filter is disarmed" must be distinguishable from "all green"."""
    return (
        f"scope: WARNING suite={suite} had {suite_rows} ledger row(s) but "
        f"0 mute tokens survived scope"
    )


def scope_enforced() -> bool:
    """The ONE read site of HAL_KNOWN_REDS_SCOPE_ENFORCE.

    rollout: 92237C8D-8B77-4294-8DC6-5B81020A86D7 flip-by:2026-08-16
    """
    return os.environ.get("HAL_KNOWN_REDS_SCOPE_ENFORCE") == "1"
