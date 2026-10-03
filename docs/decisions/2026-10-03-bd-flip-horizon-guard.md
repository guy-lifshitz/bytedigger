# bd: self-expiring guard for dated rollout horizons (flip-by / kill-by / retire-by)

**Status:** r2 spec (gate r1 REJECTED: 1 MAJOR + 5 MINOR folded; see `2026-10-03-bd-flip-horizon-guard-gate-r1.md`) · **Class:** SYSTEMATIC · **Chokepoint:** one script,
`scripts/flip_horizon.py`, run by one pytest module in the existing CI `pytest` job; the single
registry it reads is `flags_catalog.FLAGS[*]["description"]` (§1g) plus one committed ledger,
`scripts/flip_horizon_ledger.json`. **Enforcement layer (Principle C):** deterministic test in CI
(fails the PR build when a horizon passes) — no LLM, no prose matching beyond one date token.
**Source:** wave-2 principle 5 (`*_ENFORCE` flags); host issue hal#1892 (bd-drift: dated waivers
with no enforcing layer). Predecessor mechanic: `test_1846_mapping_lost_warn.py` AC9 (deleted by
#1866 with no replacement).

**Introduced because (provenance):** every `flip-by:` token in `flags_catalog.py` was written by
a warn-only rollout (hal#282, #529, #542, #561, #564, #681, #823, #863, #1338, agreements
CD666B9B / 92237C8D / C4B6B16C / CB74189B / 34E0B77B) whose completion nobody was made to
notice. Measured on `origin/main` 1e391ed, 2026-10-03: 18 horizon tokens, 16 overdue by 47–81
days, 0 flipped. What this removes: nothing (additive). Kills: none.

## §1 Problem

A date in a description string is a promise with no enforcer. Overdue rollouts sit warn-only
forever. Each flag was individually fine; together they are lost enforcement.

## §2 Decision

1. `scripts/flip_horizon.py` (stdlib only, no engine import at module load except inside the CLI):
   - `find_tokens(flags) -> list[(flag, kind, date_str)]` — every `(flip-by|kill-by|retire-by):<YYYY-MM-DD>`
     in a description. A token whose date does not parse as a real calendar date is returned with
     `kind` preserved and reported as `BAD_TOKEN` by `check` (never raises).
   - `check(flags, ledger, today) -> list[str]` — pure; one line per problem, each starting with
     the problem code and naming the flag. Codes:
     `UNCOVERED` (token date < today, flag has no ledger entry) ·
     `LAPSED` (ledger `until` < today) ·
     `TOO_FAR` (ledger `until` > today + 45 days) ·
     `NO_REASON` (ledger entry missing a non-blank `reason` or `ref`) ·
     `BAD_DATE` (ledger `until` not a real ISO date) ·
     `STALE` (ledger entry for a flag with no overdue token — the flip landed, delete the entry) ·
     `BAD_TOKEN`. One line per flag per code (two overdue tokens on one flag give one `UNCOVERED` line); several codes may fire for one flag.
     A near-miss shape — `(flip|kill|retire)-by` followed by optional `:`/spaces and a date-looking `\d{4}-\d{1,2}-\d{1,2}` that is not exactly canonical
     (`kind:YYYY-MM-DD`) — is `BAD_TOKEN`; prose such as "kill-by enforcement" is not a token. Non-string descriptions are ignored.
2. Ledger shape: `{"HAL_X": {"until": "YYYY-MM-DD", "ref": "<link/agreement>", "reason": "<why not flipped, one line>"}}`,
   one entry per line, entries in catalog order. It is the machine-readable "not enabled, and why".
3. Interfaces: `default_today() -> datetime.date` is the UTC calendar date (`datetime.now(timezone.utc).date()`), the only
   clock read; `main(argv, ledger_path=None, flags=None) -> int` uses `default_today()` when `--today` is absent, the committed
   ledger when `ledger_path` is None and the real `FLAGS` when `flags` is None. CLI: `python3 scripts/flip_horizon.py --check [--today YYYY-MM-DD]`. Exit 0 clean; 1 with one
   problem line each on stdout; 2 on unreadable ledger JSON or JSON that is not an object of objects (message on stderr). A missing ledger
   file is an empty ledger. The CLI loads `FLAGS` from the repo's `engine_py` (adds it to `sys.path`
   when the package is not importable).
4. `engine_py/tests/test_bd_flip_horizon_guard.py` runs the CLI against the real catalog + real
   ledger at the real UTC date. This is the enforcement; a later date makes CI red until someone
   flips the flag or renews the ledger entry with a reason (renewals are visible in review).
5. The committed ledger covers exactly the currently overdue set (16 flags), `until` ≤ 45 days,
   each with a reason and ref.

## §3 Acceptance checks

- **AC1** `find_tokens` returns all 3 token kinds from a synthetic catalog; ignores text without a token.
- **AC2** `check`: overdue token + no ledger entry → one `UNCOVERED <flag>` line; with a valid entry → no line.
- **AC3** `LAPSED`, `TOO_FAR` (46 days fails, 45 passes), `NO_REASON` (blank reason; missing ref), `BAD_DATE` each fire exactly once for a crafted entry.
- **AC4** `STALE`: ledger entry for a flag whose token is in the future or absent → `STALE <flag>`.
- **AC5** `flip-by:2026-13-40` → `BAD_TOKEN <flag>`, no exception.
- **AC6** CLI exit codes (synthetic `flags=` injected, so independent of the real catalog): 0 on clean fixtures, 1 on a problem, 2 on malformed ledger JSON, 0 with ledger file absent and no overdue tokens.
- **AC7 (production side-effect)** CLI on the real `FLAGS` and the committed ledger at the real today exits 0; the same command with `--today 2099-01-01` exits 1 with at least one `UNCOVERED`/`LAPSED` line (no flag is named, so follow-up flips cannot break this AC).
- **AC8** committed ledger: every entry (possibly none) has reason+ref+valid `until`; its flags ⊆ flags having a token. An empty ledger is valid.
- **AC9 (clock pin)** `default_today() == datetime.datetime.now(datetime.timezone.utc).date()`; with `default_today` monkeypatched to 2099-01-01, `main(["--check"], ledger_path=<committed>)` returns 1 whenever the real catalog still has a horizon token, else 0 (end-state safe).
- **AC10 (off-by-one)** ledger `until == today` is not LAPSED, `until == today-1` is; a token dated today-1 is `UNCOVERED`, dated today is not.
- **AC11** near-miss token shapes (`flip-by 2026-08-07`, `Flip-By:2026-08-07`, `flip-by: 2026-08-07`) → `BAD_TOKEN`; "kill-by enforcement" prose → nothing.
- **AC12** ledger JSON `[]` or `{"HAL_X": "str"}` → `main` returns 2; two overdue tokens on one flag → exactly one `UNCOVERED` line.

## §4 Edge cases

Token in a non-overdue flag → ignored. Same flag two tokens (one overdue, one future) → overdue one
needs coverage. `today` equal to the token date → not overdue (`<`, not `<=`). Ledger key not in
`FLAGS` → `STALE`.

## §5 Files in scope

New: `scripts/flip_horizon.py`, `scripts/flip_horizon_ledger.json`,
`engine_py/tests/test_bd_flip_horizon_guard.py`, this spec + gate notes.
**Files NOT in scope:** `flags_catalog.py` (no description edits here), any prod engine module,
`.github/workflows/*` (the existing pytest job already runs the test), host-repo `bd-drift.yml`
(hal#1892 half in the host repo is tracked separately).

## §6 Out of scope / follow-ups

Horizon tokens that live only in source comments (not in the catalog) are not covered; the
catalog is the registry, so comment-only tokens are a follow-up if any matter. Host-side bd-drift
`flip-by:2026-08-16` and `kill-by:2026-12-31` need a host-repo change.

## §7 Gate r1 notes

The §2.3 `sys.path` fallback branch is not exercised in CI (the wheel is installed); accepted, one line of code.
Two ledger entries (the follow-up flips) expire 2026-10-17 on purpose: CI goes red for every PR from 2026-10-18 until
the flips land or are renewed with a reason. That is the guard working, not a defect.
