# bd#130 gate round 3b (narrow): is r3 F1 closed? (spec Rev 2.3 text sync, arity 4)

Auditor: hal-gate-agent. Worktree wt-n130, branch lot-130. Requested by the MGR coordinator after the lot's rounds were used up. Read-only audit; no tests run and no git commands (no Bash tool).

Scope: r3 finding F1 only, plus what Rev 2.3 changed relative to what r2 saw (the `plan_review_entry` arity and the backstop column), plus a severity call on F2. Everything else accepted in r2/r3 is carried over, not re-audited.

## What was read
- `docs/decisions/2026-10-11-bd130-autonomous-cap-policy-gate-r3.md` (F1, F2, F3 wording).
- `docs/decisions/2026-10-11-bd130-autonomous-cap-policy.md` (whole spec, current state).
- `scripts/phase-deliverables.tsv` (whole file).
- `scripts/build-gate.sh:248-444` (`expand_entry_row`, `check_deliverables`).
- `scripts/ts/build-phase-gate.ts:430-586` (`expandEntryRow`, `checkDeliverables`).
- `tests/test_bd130_autonomous_cap_policy.py:30-194, 520-570`; `tests/test_bd136_gate_counter_table_parser.py:310-365`; grep of `tests/` for `_entry`, `malformed row`, `opus_validation`.

`plan_review_entry` has no call sites as an identifier: it is a table kind. The gate sources match it by suffix (`*_entry`), so the only literal occurrences are the two table rows, the table header comment, the spec and CHANGELOG.

## F1, item by item (r3 required fixes)

| r3 requirement | Current spec | Status |
|---|---|---|
| Header says Rev 2.3 | spec:17 `**Status: DRAFT Rev 2.3**` | done |
| §2.1 says arity 4 with the backstop field | spec:42 `(arity 4: phase, kind, field, backstop; Rev 2.3)` | done |
| Row change shows the 4-field row | spec:44-45 rows change to `plan_review_entry plan_review opus_validation` | done |
| §2.1c reads the backstop from the 4th column and needs live pass rows at 5.2 and 5 | spec:53-54 `5.2 field_eq <backstop> pass` and `5 field_eq <backstop> pass`, where `<backstop>` is the 4th column | done |
| Cite bd136 A8 as the reason | spec:45 "bd136 A8 forbids deliverable-name literals in gate sources, so the name lives in the table, not in code" | done |
| State the kind-matching rule (suffix `_entry`) | spec:45 "Kind matching is by suffix: any kind ending in `_entry` takes the arity-4 path" | done (also closes the documentation half of F3) |
| Say the (c) row text interpolates the backstop name | spec:67 still quotes the text with the literal `opus_validation` | not stated explicitly; see R1 |

The spec line 23 mention of `field_eq plan_review pass` sits in §1, which is headed "measured on `aef832c`". It describes the baseline before the change, so it is not a contradiction.

## Spec vs table vs code vs tests

- Table: `scripts/phase-deliverables.tsv:6-7` are `4.5` / `5` `plan_review_entry plan_review opus_validation` (4 TAB fields). The backstop rows exist at `:9` (`5 field_eq opus_validation pass`) and `:11` (`5.2 field_eq opus_validation pass`).
- Arity 4 in both backends: `scripts/build-gate.sh:381` `*_entry) want=4 ;;` with the arity check at `:389-392`; `scripts/ts/build-phase-gate.ts:530` `else if (kind.endsWith("_entry")) want = 4;` with the check at `:536-539`. So a 3-field or 5-field row yields `deliverable table: malformed row (line n)`, as spec:45 says.
- Backstop from the row: bash passes `fields[3]` at `build-gate.sh:404` and `:438`; TS passes `f[3]` at `build-phase-gate.ts:549` and `:581`.
- Condition (c) as specified at spec:53-58: the sets are built only from `field_eq` rows with value `pass` (`build-gate.sh:395-398`, `build-phase-gate.ts:541-544`), comment lines are skipped before that (`build-gate.sh:342-344`, `build-phase-gate.ts:517`), and both phases are required (`build-gate.sh:255-257`, `build-phase-gate.ts:442`). One (c) row is emitted regardless of how many rows are absent (`build-gate.sh:289-291`, `build-phase-gate.ts:473-475`).
- (c) row text: `build-gate.sh:290` and `build-phase-gate.ts:474` interpolate `$field` and `$backstop`. With the shipped table this equals spec:67 and the RED constant `ROW_C` at `tests/test_bd130_autonomous_cap_policy.py:43` byte for byte.
- Legacy and unknown-policy behaviour (`field_eq <field> pass`, spec:45) is unchanged by the 4th column: `build-gate.sh:268-273`, `build-phase-gate.ts:454-457`.
- Tests: the AC4/AC16 helpers match table rows on the first three cells (`test_bd130_autonomous_cap_policy.py:158`, `:169`), so they are unaffected by the entry row's width and still drive the real backends through subprocess (`:105-119`).

Conclusion on F1: closed. The r3 blocker was a contradiction between the spec (arity 3, backstop "checked in code" by literal name) and the shipped table and backends (arity 4, backstop from the row). The spec now describes the shipped design, and spec, table, both backends and the tests agree. No code or test change was needed, and I found none of the F1-relevant code in a state different from what r3 described.

## F2 severity: MINOR follow-up, not a blocker

The question was whether any test fails if the arity goes back to 3. Yes, in every form the reversion can take:
- Code only reverts (`want=3` for `*_entry`), table stays at 4 fields: rows `:6-7` become malformed at phases 4.5 and 5. Every pass-path test fails, because `_assert_pass` requires rc 0 and empty stdout (`test_bd130_autonomous_cap_policy.py:133-135`).
- Table only reverts to 3 fields: same malformed-row result, same failures.
- Both revert and the backstop name returns to the gate sources as a literal: bd136 A8 fails, because `opus_validation` is in `_TABLE_STRINGS` (`test_bd136_gate_counter_table_parser.py:338-340`) and is asserted absent from both gate sources outside comments (`:343-352`).
- Both revert and the backstop check is dropped: the AC4 tests fail, since they expect the (c) row (`test_bd130_autonomous_cap_policy.py:531-536`, `ROW_C` at `:43`).

What stays unpinned is narrower than "the arity":
- no test feeds a 3-field or a 5-field `*_entry` row and expects `malformed row`. The generic arity check is pinned for `field_eq` only (`test_bd136_gate_counter_table_parser.py:534-537`). If someone exempted `*_entry` from the arity check, no test would notice.
- no test changes the 4th column to another field and checks that (c) names it.

Spec:45 now states the 3-or-5-field rule as normative text, so this gap is worth closing in a follow-up, but it does not let an arity regression through unnoticed.

## Adversarial edges (not covered by the AC table)
- A 3-field or 5-field `*_entry` row in a table copy: must be `malformed row (line n)` in both backends. Untested (F2).
- A `*_entry` row whose 4th column names a field with no live `5.2`/`5` pass rows: (c) must name that field on `pass_with_concerns` and emit nothing on `pass`. Untested (F2).
- A kind such as `foo_entry`: takes the arity-4 path by the suffix rule now written at spec:45. Untested (r3 F3, test half).

## Checklist
AC text was not changed by the Rev 2.3 sync as far as the current spec shows (§4 matches what r3 audited). Rows are carried from r3 and were not re-audited in this round.
- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS
- AC18: PASS

## Findings
1. R1 MINOR: spec:66-67 quotes the (c) row text with the literal `opus_validation` and does not say the name is interpolated from the 4th column. Spec:54 defines `<backstop>` and the shipped table makes the two identical, so there is no contradiction. Suggested wording for a later doc touch: `<field>=pass_with_concerns needs the mandatory <backstop> rows in the deliverable table`.
2. F2 MINOR (carried from r3, unchanged): add guard cases for a 3-field and a 5-field `*_entry` row and for a changed 4th column. Follow-up, not a blocker; reasoning above.
3. F3 MINOR (carried from r3): the suffix rule is now documented at spec:45; a one-line test for a non-`plan_review` `*_entry` kind is still missing.

No MAJOR finding. F1 is closed.

## Not verified
- No tests were run, so "the suite is green on this head" is not established here; that is the CI's job.
- The diff of commit 948ba35 was not inspected (no git). That it touched only the spec is the coordinator's claim; I checked the resulting tree, not the commit.
- AC1-AC18, the pause table, the doc pins and `CHANGELOG.md` were not re-audited.
- The verdict anchor is not stamped here. The orchestrator must run `verdict-anchor.py` against this file, the spec and the RED file.

This verdict is the ACCEPT outcome in the r2/r3 vocabulary.

VERDICT: APPROVED
