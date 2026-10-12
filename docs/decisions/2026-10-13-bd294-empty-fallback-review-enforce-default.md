---
red_tests:
  - engine_py/tests/test_s4_m10_review_fallback_nonempty.py
red_pins:
  - tests/test_bd89_p3d_flag_owner_lint.py
sibling_tests:
  - engine_py/tests/test_bd_flip_horizon_guard.py
  - tests/test_bd89_p3d_flag_owner_lint.py
  - engine_py/tests/test_GH1399_advisory_format_terminal.py
  - engine_py/tests/test_phase_6_stdout_fallback_verdict_4E0BAC38.py
  - engine_py/tests/test_phase_6_review_return_discipline_CF838E6F.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_6_review.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - CHANGELOG.md
---
# bd#294: empty-fallback review check is on by default; HAL_REVIEW_EMPTY_FALLBACK_ENFORCE becomes a permanent off-switch

Slice of #89. Refs #294, #89, #290. Base: origin/main @ cd5d04d.

## 1. Problem

`HAL_REVIEW_EMPTY_FALLBACK_ENFORCE` (default `"0"`, description `flip-by:2026-10-17`, introduced by
#236 on 2026-10-03) is a shadow rollout with a date and no decision. Two things happen on its date:

- `scripts/flip_horizon.py --check` goes red. Measured with `--today` on the real catalog and the real
  ledger: clean on 2026-10-14 and 2026-10-17; first red day **2026-10-18** (exit 1, line
  `UNCOVERED HAL_REVIEW_EMPTY_FALLBACK_ENFORCE: overdue horizon token, no ledger entry`). The
  2026-11-04 in #290 is the first red day of the *ledger* entries for the other flags, not of this one.
- The code enforces by itself: `_write_review_artifact` computes `_expired = today > "2026-10-17"` and
  `_enforce = flag or _expired`. So from 2026-10-18 the terminal `E_REVIEW_EMPTY_FALLBACK` fires with no
  flag set and nobody having decided anything. Doing nothing is not "stay in shadow"; it is an
  undecided flip tied to a calendar.

The decision therefore is not "flip or not": it is "flip on purpose, with a setting that undoes it",
or fix detection if the shadow data shows false positives.

## 2. Measurement (spec stage, 2026-10-13)

Scope: every `*event*.jsonl` run log reachable from `~/.claude`, `~/Projects`, `~/.hal-worktrees`,
`~/ph14`, `~/.bytedigger`, `~/.hal-build`, continuity ledgers excluded: **796 files**, the engine
`SHARED/state/build-events.jsonl` (33 117 lines, 2026-09-09 .. 2026-10-12) included.

| Quantity | Value |
|---|---|
| `review_empty_fallback` shadow events | **0** |
| `review_writer_return_source` events (phase 6 review writes) | 7 228: 7 217 `aggregated_content`, 11 `stdout` |
| of those, dated on/after 2026-10-03 (check shipped) | **0** |
| `stdout` fallback verdicts (all before the check shipped) | 11: 5 SUSPECT, 5 FAIL, 1 PARTIAL |
| distinct SUSPECT episodes (two pairs are one event copied into a sibling run log) | 3: bodies of 225, 247, 492 bytes, `had_structured_block=false` |

Reading:

- The check has never executed in any available run: there is no shadow event because there is no
  phase-6 review write after the ship date in these logs. "No false positives" is true but is the
  absence of data, not a clean sample. The spec says so; it does not claim more.
- The only population the check would have touched are the 3 pre-ship SUSPECT episodes. Their review
  bodies were not retained (those run directories hold only the state documents, no
  `build-review.md`), so whether any was a real review wrongly called empty can be neither shown nor
  refuted from logs. All three are at most 492 bytes and had no structured block; the logs say
  nothing more about their content.
- The known false-positive shapes (canonical `### SEVERITY:` blocks without the JSON directive,
  aggregator-suspect findings, a structured empty block) are pinned by guard cells AC2, AC2b, AC3,
  AC5 of the S4/M10 suite and hold in both modes.

Decision (issue criteria: "no false positives found, no events at all and the unit cases cover the
known shapes"): make the terminal behaviour the default. No detection change. Risk accepted and
bounded: the terminal is unrecoverable for the run, so the evidence file `build-review.rejected.md`
is always written, the event names the reason, and the off-switch below is a setting.

## 3. Change

1. `workflows/phase_6_review.py`, `_write_review_artifact`: `_enforce = get_config().gate_enabled(_EMPTY_FALLBACK_FLAG)`.
   Enabled unless the variable is exactly `"0"`; `"1"` and unset enforce; `BD_` / `BYTEDIGGER_`
   aliases work through the provider (a set `HAL_` name wins). Remove `_EMPTY_FALLBACK_FLAG_OWNER`,
   `_EMPTY_FALLBACK_FLAG_EXPIRES`, `_utc_today` and the `_expired` computation. The
   `review_empty_fallback` event keeps `phase`, `reason`, `bytes`, `mode`, and `flag` becomes
   `{"name": "HAL_REVIEW_EMPTY_FALLBACK_ENFORCE", "switched_off": <bool>}`; `owner`, `expires`,
   `expired` are dropped. `switched_off` is `True` exactly when the check ran in shadow because of
   the setting (this is the "event log records that the check was switched off" line of the issue).
   Behaviour with the switch at `0` is unchanged from today's shadow: event, rejected copy, step ok.
2. `flags_catalog.py`: `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE` becomes `kind: gate`, `default: "1"`, a
   description of a permanent kill switch with no `flip-by` / `kill-by` / `retire-by` token, `owner`
   kept, `provenance` starting `introduced:` and extended with this decision. The name is kept so an
   environment that already exports `=1` keeps working.
3. `scripts/flip_horizon_ledger.json`: no entry is added or extended (nothing to extend: the token
   is gone from the description).
4. `CHANGELOG.md`: one line under Unreleased/Changed, English, `bd#294` only.

No prompt change. No change to how fallback text is parsed. The other overdue flags are out of scope.

Kill switch: `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE=0` (or `BD_...` / `BYTEDIGGER_...`). Reverting the
PR is not a switch and the gate does not accept it as one.

## 4. Tests changed or removed (every test that pinned the shadow default)

All in `engine_py/tests/test_s4_m10_review_fallback_nonempty.py`, changed on purpose because the
default they pinned is the thing being decided:

| Test / fixture | Pinned | Now |
|---|---|---|
| `_hermetic_flag_and_clock` (autouse) | flag unset = shadow, date pinned inside window | flag and both aliases unset = enforce; no clock |
| `any_mode` | `flag_off` = unset, `flag_on` = `"1"` | `switched_off` = `"0"`, `default_on` = unset, `flag_one` = `"1"` |
| `enforce` | `setenv "1"` | unset (the default); `"1"` is its own cell |
| `_assert_closed` | `flag.owner` non-empty, `flag.expires == "2026-10-17"` | `flag.name` and `flag.switched_off is False` |
| `_assert_shadow` | `flag.owner`, `flag.expires`, `flag.expired is False` | `flag.switched_off is True` |
| `test_ac8_shadow_*` (3) | shadow when unset | shadow only when the variable is `"0"` (kill switch) |
| `test_ac9_on_expiry_day_flag_unset_is_still_shadow` | shadow until the date | deleted (no date) |
| `test_ac9_after_expiry_flag_unset_enforces_and_marks_expired` | enforce by date | deleted; replaced by AC1 (enforce with nothing set) |
| `test_ac9_after_expiry_flag_set_enforces` | enforce by date + flag | deleted; flag `"1"` covered by AC11 |
| `test_ac9_flag_set_before_expiry_enforces_not_expired` | flag `"1"`, `expired is False` | replaced by AC11 |
| `test_ac6b_flag_registered_in_catalog` | kind `flag`, default `"0"`, `flip-by` date in description | kind `gate`, default `"1"`, no dated token (AC6b below) |

Not changed: AC1/1b/4/4b/7/10 enforce cells (they already assert the terminal, now reached with no
environment), AC2/2b/3/4-keep/5 guards (run in all three modes), AC6 error-code and registry cells.
Static check of siblings: 25 phase-6 test files that call the review writer or the fallback path
(list in the PR body) pass 616/616 both with the variable unset and with it set to `1` on the base
tree; the full suite is CI's.

## 5. Acceptance criteria

- AC1. Real `_write_review_artifact`, no `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE` / `BD_` / `BYTEDIGGER_`
  variable, raw review output `Looks good to me.` (and the empty / whitespace / `VERDICT: PASS` /
  plain-list cells): `status == "error"`, `error_code == "E_REVIEW_EMPTY_FALLBACK"`,
  `recoverable is False`, `build-review.rejected.md` holds the raw bytes, `build-review.md` and the
  fix doc are absent, event `mode == "enforce"`, `flag.switched_off is False`.
- AC2. Same inputs with `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE=0`: `status == "ok"`, doc and fix doc
  written, rejected copy written with the raw bytes, one event with `mode == "shadow"` and
  `flag.switched_off is True`.
- AC3. Canonical `### SEVERITY: HIGH` blocks with `> path:line:` quotes and no JSON directive:
  `status == "ok"`, no `review_empty_fallback` event, in the default, `"1"` and `"0"` modes.
- AC4. `BD_REVIEW_EMPTY_FALLBACK_ENFORCE=0` with the `HAL_` name unset switches the check off
  (as AC2); `BYTEDIGGER_REVIEW_EMPTY_FALLBACK_ENFORCE=0` the same; `HAL_...=1` together with
  `BD_...=0` enforces (the `HAL_` name wins).
- AC5. Catalog entry: `kind == "gate"`, `default == "1"`, description has no
  `flip-by:` / `kill-by:` / `retire-by:` token and contains no ISO date, `owner` and a
  `provenance` starting `introduced:` are non-blank (AC6b).
- AC6. `python3 scripts/flip_horizon.py --check --today 2026-10-18` and `--today 2026-11-04` do not
  name `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE`; the ledger has no such key.
- AC7. `phase_6_review.py` has no `_utc_today`, `_EMPTY_FALLBACK_FLAG_EXPIRES` or
  `_EMPTY_FALLBACK_FLAG_OWNER` (behavioural: `hasattr(p6, "_utc_today")` is false; the result of
  AC1 does not depend on any date seam).
- AC8. Existing guards unchanged and green: `tests/test_bd89_p3d_flag_owner_lint.py`,
  `engine_py/tests/test_bd_flip_horizon_guard.py`, the GH1399 class registry
  (`E_REVIEW_EMPTY_FALLBACK` stays `terminal`).
- AC11. `HAL_REVIEW_EMPTY_FALLBACK_ENFORCE=1`: AC1 outcome, `flag.switched_off is False`.

## 6. Test plan

Edit `engine_py/tests/test_s4_m10_review_fallback_nonempty.py` per section 4 and add AC4, AC6, AC7,
AC11 cells there (real step, no mock of the unit under test; the existing `_Sink` event-log harness
and the `_resolve_backend` pin are kept). AC6 shells out to `scripts/flip_horizon.py` with
`sys.executable`, no import at module load. Local runs are targeted (this file and the siblings in
the front matter); the full suite and base-to-HEAD delta are CI's.
