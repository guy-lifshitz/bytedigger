# bd#141 item 4 (b): `oracle verify` host CLI

**Status:** r1 · **Tier:** 2 (one engine prod `.py` + one new test file, Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `conformance/oracle.read_log_events` (the single reader of the
event log for freeze and verify) + `find_last_freeze`/`verify_against` (the single verdict path).
**Source:** bd#154 (bd#141 item 4(b), §7.4 portable set). Pattern: the `bd_l3` CLI of #156
(`docs/decisions/2026-10-02-bd141-p4-bd-l3-cli.md`). Pairs with a hal-v2 shadow adapter (later, not here).

## §1 Problem (measured on `2222a1f`)

1. **No host surface.** Freeze-and-verify lives in `conformance/oracle.py` and is wired only in
   `run.py` (`_oracle_entry_verify` :135, `_oracle_after_execute` :158). A host sees the verdict only
   by running `phase_5_implement` and reading the `E_ORACLE_*` refusal or the
   `oracle_frozen`/`oracle_amended` events. `oracle.py` has no `__main__`.
2. **The reader breaks its own §5 contract on two inputs.** `read_log_events` (:272) promises
   "a log that cannot be read is `E_ORACLE_INDETERMINATE`, never an escaping ValueError". Measured:
   - invalid UTF-8 → `Path.read_text` raises `UnicodeDecodeError` (a `ValueError`, not `OSError`),
     which escapes; `run.py` would map it to a non-oracle code.
   - a valid-JSON non-object line (`[1,2]`, `"x"`, `null`) is returned as an event;
     `find_last_freeze` then calls `.get` on it → `AttributeError` escapes.
   Production `EventLog.append` writes neither, but a host CLI reads whatever file it is pointed at,
   and fixing it in the reader closes it for `run.py` too (Principle B: fix at the chokepoint).
3. §1a sibling audit (tests importing `conformance.oracle`): `test_bd8_l1_oracle.py`,
   `test_bd27_oracle.py`. Constraints they pin and this change must keep: no I/O at import
   (`test_bd8 AC-12`, monkeypatched `open`/`scandir`/`listdir`/`subprocess`), no `signal` import in
   any form (`test_bd27 AC-E9`, AST), conformance is a real package (AC-12b). `oracle.py` has no
   `__all__`. No sibling feeds the reader UTF-8-invalid or non-object lines, so none should change.
4. `ORACLE_SPEC.md` (FROZEN v7) `[bd8:5]` says the seam "does not know about `run.py`, the workflow
   registry, or the CLI" — meaning `run.py`'s CLI. The new `_main` imports no `bytedigger_engine`
   module (AC12), so the seam stays below `run.py`. `ORACLE_SPEC.md` is not edited; the module
   docstring gets one paragraph naming the host CLI.

## §2 Design

**2.1 Reader (the class fix).** In `read_log_events`:
- the file is decoded strictly as UTF-8; a `UnicodeDecodeError` → `OracleRefusal(E_ORACLE_INDETERMINATE,
  "event log is not valid UTF-8: …")`;
- a non-blank line whose JSON value is not an object → `OracleRefusal(E_ORACLE_INDETERMINATE,
  "event log line N is not a JSON object: …")`.
Nothing else in the reader changes (missing file → `[]`, `OSError` → INDETERMINATE, bad JSON →
INDETERMINATE stay as they are).

**2.2 CLI.** `python -m bytedigger_engine.conformance.oracle verify --event-log P --run-id ID --scratchpad-dir D`.
All three flags required; `verify` is the only subcommand and is required.

The verdict is exactly what `run._oracle_entry_verify` decides for a `phase_5_implement` run with the
same log, run id and `org_config["scratchpad_dir"]` (no new logic):
`events = read_log_events(P)`; `frozen = find_last_freeze(events, ID)`; if `None` → `unfrozen`;
else `verify_against(frozen["payload"], D)`; an `OracleRefusal` maps its `code`.

Faithfulness consequences (deliberate, so the host shadow matches the engine): a missing `P` is
`unfrozen` (engine: no log ⇒ no freeze), a directory as `P` is `indeterminate`, a missing `D` is
`mutated` with `mutated:removed`. None of these is rc 2.

Success: exactly one line on stdout, a JSON object with exactly these keys:

| key | value |
| --- | --- |
| `outcome` | `"verified"` · `"unfrozen"` · `"mutated"` · `"indeterminate"` |
| `code` | `null` (verified) · `"E_ORACLE_UNFROZEN"` · `"E_ORACLE_MUTATED"` · `"E_ORACLE_INDETERMINATE"` — the refusal's `code` verbatim |
| `token` | `"mutated:content"` / `"mutated:added"` / `"mutated:removed"` when outcome is `mutated` (the one `TOKEN_*` the refusal message starts with), else `null` |
| `message` | the refusal message verbatim, else `null` |
| `event_type` | `event_type` of the freeze found (`"oracle_frozen"` / `"oracle_amended"`), else `null` |
| `frozen_digest` | the found payload's `digest`, else `null` |
| `current_digest` | `compute_digest(D, <frozen member paths>, when="verify")` when a freeze was found and that call does not raise; `null` otherwise (unfrozen, a member gone or unreadable, log unreadable) |
| `run_id` | the `--run-id` value echoed |

Exit **0 for any verdict**. Mapping: `E_ORACLE_UNFROZEN`→`unfrozen`, `E_ORACLE_MUTATED`→`mutated`,
`E_ORACLE_INDETERMINATE`→`indeterminate`.

Usage/input errors: exit **2**, nothing on stdout, one message on stderr starting `oracle: `.
Cases: no subcommand; unknown subcommand; any of the three flags missing; unknown flag (even with a
valid log — no `parse_known_args`); a positional argument after `verify`; a freeze event whose
`payload` is not an object, or whose payload makes `verify_against` raise anything other than
`OracleRefusal` (`KeyError`/`TypeError`/`AttributeError`) — message contains `malformed`.

Mode: read-only. Never writes or appends to `P`, never writes under `D`, creates no directory.

Module hygiene: `argparse`/`sys` imported inside `_main` (keeps AC-12 no-I/O-at-import and adds no
module attribute); `_main(argv=None) -> int`; guard `if __name__ == "__main__": sys.exit(_main())`
(the guard's own `import sys` is local to the guard block). No `signal`, no `bytedigger_engine` import.

## §3 Acceptance criteria

Fixture shape (all ACs): a `scratch/specs/` with two files, a log under a separate `logs/` dir.
"Real freeze" = `run._oracle_after_execute(args, ctx, run_id, result)` with
`args.workflow="phase_45_spec"`, `args.event_log=P`, `ctx.org_config={"scratchpad_dir": D}`,
`result.status="ok"` — the production freeze writer, no hand-built `oracle_frozen` line.
CLI = real subprocess `python -m bytedigger_engine.conformance.oracle verify …`.

- **AC1 (side-effect, §1l):** real freeze, tree untouched → rc 0, `outcome=="verified"`,
  `code is None`, `token is None`, `frozen_digest == current_digest`, `frozen_digest` equals the
  `digest` of the `oracle_frozen` event in the log, `event_type=="oracle_frozen"`, `run_id` echoed.
  Red on `2222a1f` (no `__main__`).
- **AC2 (mutated, one per token):** after a real freeze — (a) rewrite a member → `token=="mutated:content"`,
  `current_digest` non-null and `!= frozen_digest`; (b) add `specs/.new.md` → `"mutated:added"`;
  (c) delete a member → `"mutated:removed"`, `current_digest is None`. All `code=="E_ORACLE_MUTATED"`, rc 0.
- **AC3 (unfrozen):** (a) log with only a foreign event; (b) `P` does not exist (and its parent dir is
  not created); (c) real freeze under run `A`, CLI `--run-id B` → each `outcome=="unfrozen"`,
  `code=="E_ORACLE_UNFROZEN"`, `frozen_digest is None`, `current_digest is None`, `event_type is None`.
- **AC4 (indeterminate):** (a) a directory as `--event-log`; (b) a non-JSON line after a real freeze;
  (c) invalid UTF-8 bytes appended after a real freeze; (d) a `[1,2]` line after a real freeze;
  (e) a member made unreadable (`chmod 000`; skipped when running as root) → each rc 0,
  `outcome=="indeterminate"`, `code=="E_ORACLE_INDETERMINATE"`, `token is None`, non-empty `message`.
- **AC5 (reader, in-process):** `oracle.read_log_events` on (c) and (d) of AC4 raises `OracleRefusal`
  with `code=="E_ORACLE_INDETERMINATE"` (no `UnicodeDecodeError`, no `AttributeError` later in
  `find_last_freeze`).
- **AC6 (amendment):** real freeze, then a real re-entry (`_oracle_after_execute` again with a changed
  member and `org_config["oracle_amendment_reason"]="r"`) → CLI `event_type=="oracle_amended"`,
  `outcome=="verified"`, `frozen_digest` equals the amendment's `digest`, not the first freeze's.
- **AC7 (engine parity):** for each scenario of AC1, AC2(a–c), AC3(a,c), AC4(a,b): the CLI `code` equals
  the `error_code` of `run._oracle_entry_verify(args, ctx, run_id)` (`args.workflow="phase_5_implement"`)
  run on a byte copy of the same log — `None` when the engine returns `None`.
- **AC8 (key set):** stdout is one line; the object's key set is exactly
  `{outcome, code, token, message, event_type, frozen_digest, current_digest, run_id}`.
- **AC9 (usage errors):** rc 2, empty stdout, stderr starts `oracle: ` for each of: no args; `bogus`
  subcommand; `verify` missing each of the three flags (3 cases); unknown flag with a valid log;
  positional after `verify` with a valid log; a hand-written `oracle_frozen` event whose `payload` is
  `"x"` (stderr contains `malformed`); one whose payload `members` is `[{"digest": "…"}]` (no `path`,
  stderr contains `malformed`).
- **AC10 (read-only):** after AC1 and AC2(a), the log's bytes and every file under `D` (path → sha256)
  are identical before/after the CLI call.
- **AC11 (seam hygiene):** AST of `oracle.py`: no top-level `import argparse`/`import sys`, no import of
  any `bytedigger_engine` module or relative import anywhere; `vars(oracle)` has no `argparse` key; the
  module has a `_main` function.
- **AC12 (in-process):** `oracle._main(["verify", …AC1 args…])` returns `0` and prints the same JSON as
  the subprocess.

## §4 Scope

In: `engine_py/bytedigger_engine/conformance/oracle.py`; new test
`engine_py/tests/test_bd141_p4b_oracle_cli.py`; this doc; `CHANGELOG.md` one line.

## §5 Not in scope

- `ORACLE_SPEC.md` (frozen), `run.py`, `event_log.py`, `error_codes.py`/`ERROR_CODES.md` (no new code).
- The hal-v2 shadow adapter and its host-controls entry.
- `--json`-less human output, a `freeze` subcommand (a host must not freeze; only the oracle phase does).
- Entry-vs-exit distinction: entry and exit verify run the same two calls; the CLI is one verify.
