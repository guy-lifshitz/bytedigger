# bd#158: a malformed `oracle_frozen` / `oracle_amended` row refuses `E_ORACLE_INDETERMINATE`, never escapes

**Status:** r2 (gate r1 REJECTED: 1 MAJOR + 4 MINOR, all addressed; see `2026-10-02-bd158-gate-r1.md`) · **Tier:** 2 (one prod file `conformance/oracle.py`, one new test file, two doc lines; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `conformance/oracle.py` — `find_last_freeze` (row shape) and
`verify_against` (payload shape), through one new validator `check_freeze_payload`. Every reader of a
freeze row (engine ENTRY verify, engine EXIT verify, engine re-entry amendment, bd#154 CLI) goes
through `find_last_freeze` and/or `verify_against`; none needs to change to be covered.
**Source:** bd#158, found by bd#154 gate r1 MINOR 9 (`2026-10-02-bd141-p4b-oracle-cli-gate-r1.md`).
**Not in scope (§1v):** `run.py`, `engine.py`, `phases/*.md` (bd#89), `has_sentinel_resume`,
`last_phase_artifacts`, `crosscheck_from_payload` (they never raise past `run.py` on a malformed
freeze: they read other event types or `.get` on dicts). Also out (gate r1 F5, pre-existing, not a
freeze-row shape): `RecursionError` from a deeply nested log row in `read_log_events`, and absolute /
`..` member paths (accepted today, hash outside the scratchpad; fail-open surface, not an escape).
Both are filed as a follow-up comment on bd#158 rather than widened into this lot.

## §1 Problem (measured on `24dbd13`)

`run._oracle_entry_verify` / `_oracle_after_execute` (`run.py:135-212`) catch only `OracleRefusal`.
A hand-written or corrupted freeze row raises a plain exception that escapes and is mapped by
`run.py main()` to a non-oracle code (`[bd8:6a]` forbids exactly this):

| Row / payload shape | Where it raises today | Exception |
|---|---|---|
| `payload` is a non-dict truthy value (`"x"`, `[1]`, `3`) and the row has no top-level `run_id` | `find_last_freeze` run filter `(e.get("payload") or {}).get(...)` | `AttributeError` |
| no `payload` key | `run.py` `frozen["payload"]` | `KeyError` |
| `payload` is a non-dict (with top-level `run_id`) | `verify_against` `frozen_payload.get` | `AttributeError` |
| `members` is not a list (e.g. `3`, `"ab"`, `{}`) | `verify_against` iteration | `TypeError` / wrong semantics |
| a member dict without `path`, or non-`str` `path` | `verify_against` `m["path"]` / `Path / rel` | `KeyError` / `TypeError` |
| a member that is neither `str` nor `dict` (e.g. `3`, `null`) | `Path / rel` | `TypeError` |
| `scope` is a non-empty non-list, or holds a non-`str` | `compute_scope_digest` `Path / reldir` | `TypeError` |
| payload `digest` non-`str` / member `digest` non-`str` | silently compared → `E_ORACLE_MUTATED` | wrong code (not a mutation: the record is unreadable) |
| `scope_digest` non-`str` / absent | silently compared → `E_ORACLE_MUTATED` (`mutated:added`) | wrong code |

The bd#154 CLI already reports these as rc 2 `malformed` (via its own ad-hoc `except (KeyError,
TypeError, AttributeError, ValueError)`). Engine and CLI disagree.

## §2 Design

**2.1 `class OracleMalformedFreeze(OracleRefusal)`** (new, `oracle.py`). `__init__(self, message)`
calls `super().__init__("E_ORACLE_INDETERMINATE", "malformed freeze event: " + message)`. It *is* an
`OracleRefusal`, so `run.py`'s existing `except oracle.OracleRefusal` reports it verbatim with
`error_code == "E_ORACLE_INDETERMINATE"`, `recoverable=False` — no `run.py` change.

**2.2 `check_freeze_payload(payload) -> None`** (new, pure, no I/O). Raises `OracleMalformedFreeze`
when, in this order:
1. `payload` is not a `dict`;
2. `payload.get("digest")` is not a `str`;
3. `members` (key present and not `None`) is not a `list`; any member is neither a `str` nor a
   `dict`; a `dict` member's `path` is not a `str`, or its `digest` is not a `str`;
4. `scope` (key present, not `None`, not `[]`) is not a `list`, or holds a non-`str`;
5. `payload.get("scope_digest")` is not a `str`;
6. (gate r1 F1) any member path (bare-`str` member or a `dict` member's `path`) or any `scope` entry
   contains `"\x00"` — the OS path layer rejects it with `ValueError`, which no caller catches.

Accepted unchanged: everything `build_freeze_payload` / `build_amendment_payload` emit; bare-`str`
members (legacy shape `verify_against` already accepts); `members` absent/`None`/`[]`; `scope`
absent/`None`/`[]` (recomputed, as today). Empty-string values are NOT malformed (shape only;
semantics stay with the digests). Extra keys are ignored.

§1x legacy input (gate r1 F4): an absent `scope_digest` moves from `E_ORACLE_MUTATED mutated:added` to
`E_ORACLE_INDETERMINATE`. Both refuse; nothing fails open. No shipped writer ever emitted a freeze
without `scope_digest`: `build_freeze_payload` has always set it (`oracle.py:246`) and
`build_amendment_payload` delegates to it.

**2.3 `find_last_freeze`.** (a) Run filter: the payload-side run id is read only when the payload is
a `dict` (otherwise `None`) — the filter itself never raises. (b) Before returning the selected (last)
candidate: if it has no `payload` key, raise `OracleMalformedFreeze`; otherwise run
`check_freeze_payload(row["payload"])` on it (full check, gate r1 F2). Consequence, intended and
pinned (AC3b): a re-entry amendment whose previous row is a dict but malformed further down (e.g.
`digest: 1`) refuses `E_ORACLE_INDETERMINATE` and appends no `oracle_amended` — it never records a
non-`str` `previous_digest`.
Only the **selected** row is checked: a malformed row of another run (filtered out by a differing
top-level `run_id`) or an earlier, superseded row does not refuse. Return value otherwise unchanged
(`None` when no candidate). Callers can therefore always index `frozen["payload"]` as a dict.

**2.4 `verify_against`.** First statement: `check_freeze_payload(frozen_payload)` (kept although
`find_last_freeze` already checked: `verify_against` is public and called with a bare payload).
The rest of the body is unchanged in order (removal → content → addition), but a `ValueError` or
`OSError` raised by the path layer inside it that is not already an `OracleRefusal` (gate r1 F1: e.g.
`Path.exists` re-raising `ENAMETOOLONG` on py < 3.13) is converted to
`OracleRefusal("E_ORACLE_INDETERMINATE", "verify: path layer error (<Class>: <msg>)")` — not
`OracleMalformedFreeze`: the record may be fine and the filesystem the problem. The CLI reports that as
`indeterminate` (rc 0), the engine as `E_ORACLE_INDETERMINATE`: codes agree.

**2.5 bd#154 CLI `_main` keeps its contract** (rc 2, empty stdout, stderr starts `oracle: ` and
contains `malformed`). The inner `except OracleRefusal` around `verify_against` and the outer one
must not swallow `OracleMalformedFreeze` as an `indeterminate` verdict: an `except
OracleMalformedFreeze` (before `except OracleRefusal`, both levels) writes
`oracle: <message>\n` to stderr and returns 2. The ad-hoc `(KeyError, TypeError, AttributeError,
ValueError)` clause may stay as a backstop.

**2.6 Docs.** `ERROR_CODES.md:239` and `ORACLE_SPEC.md` §5 table (`:340`): `E_ORACLE_INDETERMINATE`
also covers "a freeze/amendment row whose payload is malformed (bd#158)".

**Principles.** Purely deterministic code; no model, no provider, no network — the provider and
subscription/API axes do not apply. Fail-closed: a malformed record refuses, it never verifies.

## §3 Acceptance criteria (test file `engine_py/tests/test_bd158_oracle_malformed_freeze.py`)

Fixtures: real scratchpad + real `events.jsonl` written as JSON lines (same shape as
`EventLog.append`: `ts`, `run_id`, `event_type`, `payload`); production functions called directly.
Nothing mocked (§1l: the side-effect is the `StepResult` the real `run._oracle_entry_verify` returns).

- **AC1 (engine ENTRY, production side-effect).** For each malformed shape in §1 (at least: payload
  `"x"` with and without top-level `run_id`; no `payload` key; payload `[1]`; `members: 3`;
  `members: "ab"`; member `{"digest": "d"}`; member `{"path": 3, "digest": "d"}`; member `3`; member
  `null`; `scope: 5`; `scope: [1]`; `digest: 1`; member `digest: 1`; `scope_digest` absent;
  `scope_digest: 1`; `members: {}`; `scope: ["sp\x00ecs"]`; member path `"sp\x00ecs/a.md"`; and
  payload `"x"` / member without `path` under `event_type="oracle_amended"` (gate r1 F3)),
  `run._oracle_entry_verify(args(workflow="phase_5_implement"), ctx, RUN_ID)`
  returns a `StepResult` with `status == "error"`, `error_code == "E_ORACLE_INDETERMINATE"`,
  `recoverable is False`, and `"malformed freeze event" in error`. No exception escapes.
- **AC2 (engine EXIT).** The same for `run._oracle_after_execute(args, ctx, RUN_ID, ok_result)` on an
  implementing workflow, for ≥4 representative shapes (no payload key, payload str, member without
  path, `digest: 1`).
- **AC3 (re-entry amendment).** An oracle-phase `_oracle_after_execute` whose last freeze row has a
  non-dict payload returns `E_ORACLE_INDETERMINATE` and appends **no** `oracle_frozen` /
  `oracle_amended` event: the pre-call log bytes are a prefix of the post-call bytes, and any added
  rows are none of those two types (`run._oracle_refusal_result` legitimately appends its own
  `phase_refused` row; `run.py` is out of scope).
- **AC3b (amendment over a dict-but-malformed previous, gate r1 F2).** Previous row a well-formed
  dict except `digest: 1`, amendment reason present in ctx: oracle-phase `_oracle_after_execute`
  returns `E_ORACLE_INDETERMINATE` and appends no `oracle_amended` / `oracle_frozen` row.
- **AC4b (verify_against placement, gate r1 F2).** `oracle.verify_against(p, D)` called directly with
  each payload-level malformed shape raises `OracleMalformedFreeze`.
- **AC4c (path layer never escapes, gate r1 F1).** A freeze whose member path and scope hold a
  300-char component: `_oracle_entry_verify` returns a `StepResult` whose `error_code` starts with
  `E_ORACLE_` (version-agnostic: py < 3.13 gives INDETERMINATE via §2.4, py ≥ 3.13 may give
  MUTATED); no exception escapes. The CLI on the same input exits 0 or 2, never 1.
- **AC4 (unit, the chokepoint).** `check_freeze_payload` raises `OracleMalformedFreeze` (an
  `OracleRefusal` subclass, `.code == "E_ORACLE_INDETERMINATE"`) for each AC1 payload-level shape, and
  returns `None` for: a real `build_freeze_payload` output; a real `build_amendment_payload` output;
  bare-`str` members; `members` absent / `None` / `[]`; `scope` absent / `None` / `[]`; an extra key.
- **AC5 (find_last_freeze selection).** (i) A malformed row of **another** run (top-level
  `run_id: "other"`) followed by nothing for RUN_ID → returns `None` (no raise). (ii) A malformed row
  followed by a well-formed RUN_ID freeze → returns the well-formed row. (iii) A well-formed freeze
  followed by a malformed RUN_ID row → raises `OracleMalformedFreeze`. (iv) Payload `"x"` with no
  top-level `run_id` → raises `OracleMalformedFreeze`, not `AttributeError`.
- **AC6 (engine ↔ CLI parity).** For every AC1 shape, the bd#154 CLI
  (`python -m bytedigger_engine.conformance.oracle verify ...` as a subprocess) exits 2 with empty
  stdout and `malformed` in stderr, AND the engine returns `E_ORACLE_INDETERMINATE`.
- **AC7 (no regression on well-formed input).** A real freeze over a real scratchpad: unchanged
  → `_oracle_entry_verify` returns `None`; one member rewritten → `E_ORACLE_MUTATED` with
  `mutated:content`. (The existing suites `test_bd8_l1_oracle.py`, `test_bd141_p4b_oracle_cli.py`,
  `test_bd27_oracle.py` stay green — the ship gate, not a RED.)

Expected RED on `24dbd13` (measured, py 3.14: 107 failed, 5 passed): AC1/AC2/AC3/AC3b/AC4/AC4b/
AC5(iii,iv)/AC6 fail (escaping exception or wrong code). Green on base (guards): AC5(i), AC5(ii) with a
top-level run_id, AC7 x2, and AC4c on py >= 3.13 (where `Path.exists` swallows the error); AC4c is a
real RED on CI's stock ubuntu-latest python3 (3.12).

Known residual (gate r2 F3, not an escape): a lone-surrogate path gives `E_ORACLE_MUTATED` in the
engine but rc 2 in the CLI. Recorded as a follow-up comment on bd#158.

## §4 Sibling tests (§1a)

`tests/test_bd8_l1_oracle.py`, `tests/test_bd141_p4b_oracle_cli.py`, `tests/test_bd27_oracle.py`,
`tests/test_bd9_l2_falsifiable_oracle.py` — must stay green; none pins a malformed row to a
non-INDETERMINATE engine code (grep: only `test_bd141_p4b_oracle_cli.py:351` writes `digest: 1`, and
it pins the CLI rc 2, which §2.5 keeps).

## §5 Scope

- `engine_py/bytedigger_engine/conformance/oracle.py` (prod)
- `engine_py/tests/test_bd158_oracle_malformed_freeze.py` (new, RED)
- `engine_py/bytedigger_engine/ERROR_CODES.md`, `engine_py/bytedigger_engine/conformance/ORACLE_SPEC.md` (one line each)
