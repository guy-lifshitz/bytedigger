# bd#130 gate round 3: AUTONOMOUS cap policy (spec Rev 2.3 + RED incl. N1/N3/N5 cases)

Auditor: hal-gate-agent. Worktree wt-n130, branch lot-130, HEAD 7b18031. Read-only audit, no tests run (no Bash tool). Scope: spec, RED file, and the GREEN files named in the brief, read for consistency only.

## r2 minor status (N1-N5)
- N1 RESOLVED. `[p1]` is "malformed" in `_PAUSE_CASES`, and AC10 parity pins it.
- N2 RESOLVED. The block-sequence case `ac15_block_sequence` expects the "no value" row.
- N3 RESOLVED. `'"none"'` (ok) and `'""'` (no value) are in `_PAUSE_CASES`.
- N4 RESOLVED. AC17 is listed in §4. AC14 says "no further row". §2.2 says the pause row is emitted at most once. The (d) row text is quoted by reference in §2.1d.
- N5 RESOLVED. The AC18 tests (3 scenarios x 2 backends) compare `build-state.yaml` before and after the run.

## 4-step audit
- Step 1 (token drift): see F1. The spec says "arity 3" and `plan_review_entry plan_review`, and its header still says "DRAFT Rev 2". The table and both backends use arity 4. Everything else is consistent. The row texts in the spec match the RED constants and both backends.
- Step 1.5 (branch simulation): I simulated the order policy -> plan_review (pass / pwc / other) -> a, b, c, d -> §2.2 (phase 5 only) -> pause validation (once) -> COMPLEX row last. The bash `expand_entry_row` and the TS `expandEntryRow` do the same. The expected outcomes of every SC scenario match the first branch that applies.
- Step 2 (§2 vs §3): every soft-block branch has an AC. The "state unchanged" post-condition is now AC18, produced by the soft-block path. AC18 permits writes only to `gate_block_counter` / `gate_block_phase`. build-gate.sh lines 756-774 show those are the only keys the soft-block path rewrites (grep -v of those two keys, then echo). The relaxation is justified. It does not weaken the resume-state guarantee, because the test strips only lines that start with those two keys.
- Step 3 (RED adequacy): the tests drive real backends through subprocess, with no mocks, and a missing `bun` fails the test. Interpolated values vary across scenarios. RED does not pin the arity of the `plan_review_entry` row. The only RED coverage of the 4th column is indirect: `ROW_C` hard-codes `opus_validation`, and the row dropping/commenting helpers match rows with `[:3]`. See F2.
- Step 4 (reachability): rows 4.5 and 5 of `phase-deliverables.tsv` go through `check_deliverables` -> `expand_entry_row` (build-gate.sh) and `checkDeliverables` -> `expandEntryRow` (build-phase-gate.ts). The tests reach both through subprocess. No change from r2.

## Focus 1: arity-4 consistency and parity
- Table: `4.5` and `5` rows are `plan_review_entry plan_review opus_validation`. The header comment is updated.
- Bash and TS agree on:
  - the kind test (`*_entry` / `endsWith("_entry")`, after `field_eq`, `field_set`, `log_red` and `scratch_file`);
  - `want=4`;
  - the `pass52` / `pass5` sets, which are built from the whole table before any expansion, after the arity check, and from rows with value `pass` only;
  - ordering (entry rows expand in table order after the plain rows are collected);
  - all row texts.
  The backstop name is taken from the row, so the (c) text yields `opus_validation` identically in both backends.
- bd136 A8: `plan_review`, `opus_validation` and the other deliverable names no longer appear in gate code outside comments. I checked the build-gate.sh and TS code paths read above. Mandatory-ness of the backstop is now data in the table instead of a code literal. If someone edits the 4th column, (c) points at a different field. That field must itself have `5.2` and `5` `field_eq ... pass` rows, which in practice only `opus_validation` has, so the mandatory-backstop guarantee is intact for the shipped table. A test of that is missing (F2).
- Parity holds. The one divergence risk, that bash `yaml_get` and the TS `readStateField(...).trim()` differ in quote handling, was already pinned by AC10 byte-compares.
- The spec, however, does not describe this design (F1).

## Focus 2: do N1-N5 weaken any r2 criterion
No.
- AC15 gained cases only.
- AC18 is new, and its relaxation covers an existing, verified side effect.
- Pinning `[p1]` as malformed and block sequences as "no value" only tightens the spec.
- No r2 AC had its expected rows loosened.

## Focus 3: the `*_entry` suffix nit
Advisory, not a blocker. Reasons:
- The table is repo-owned and pinned by bd136 parser tests. Both `bogus_kind` and `bogus` are still "unknown kind", because neither ends in `_entry`.
- No AC depends on rejecting `x_entry`. A hostile or mistaken `foo_entry` row would apply the plan_review rules to its own `<field>` and `<field>_concerns`. That is a table-authoring error, not a runtime path.
- It is also unpinned: no RED or guard test exercises a non-`plan_review_entry` suffix kind (F3).

## Adversarial edges (not covered by the AC table)
- A `plan_review_entry` row with 3 fields (the spec's arity 3) must be a "malformed row" in both backends; with 5 fields too. Untested.
- A `foo_entry` kind: unknown-kind or plan_review semantics? Today the latter. Untested.
- A 4th column pointing at a field that has `5.2`/`5` `field_eq ... pass` rows other than `opus_validation`. The (c) text then names that field. Untested.
- A `plan_review_entry` row at a phase other than 4.5/5 (e.g. 5.2). Today it is evaluated like any phase. §2.2 stays phase-5-only because of the `phase = 5` test.
- `mode_override` or `autonomous_pauses` with CRLF line endings in `build-state.yaml`. `yaml_get` strips `\r`, but the TS reader's behaviour here is pinned only by older bd136 tests.

## Checklist
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
1. F1 MAJOR: the frozen spec does not carry the arity-4 amendment. `docs/decisions/2026-10-11-bd130-autonomous-cap-policy.md` still says:
   - header: "DRAFT Rev 2";
   - §2.1: "New table kind `plan_review_entry` (arity 3: phase, kind, field)";
   - the §2.1 row change: `plan_review_entry plan_review`;
   - §2.1c: the backstop is "checked in code" on `5.2`/`5 field_eq opus_validation pass`;
   - §1: "`field_eq plan_review pass`" as the table rows.
   Only the AC18 text shows a Rev 2.2 edit. The shipped table and both backends enforce arity 4 (`want=4`), so a table row written to the spec's arity 3 is flagged as malformed. The spec and the implementation contradict each other on an externally visible table format, and the reason for the change (bd136 A8 forbids deliverable-name literals in gate sources) is nowhere in the spec. This blocks. Required fix, doc-only:
   - bump the header to Rev 2.3;
   - change §2.1 to "arity 4: phase, kind, review field, backstop field" with rows `plan_review_entry plan_review opus_validation`;
   - rewrite §2.1c to say the (c) check reads the backstop name from the row's 4th column and requires live `field_eq <backstop> pass` rows at 5.2 and 5;
   - say that the (c) row text interpolates that name;
   - state the kind-matching rule (suffix `_entry`, see F3) and cite bd136 A8.
   No code or test change is needed for F1. The orchestrator must re-stamp the anchor after the fix (`verdict-anchor.py`).
2. F2 MINOR: add RED or guard cases for the arity: a 3-field and a 5-field `plan_review_entry` row give "malformed row (line n)" in both backends; the 4th column is the backstop named in (c) (a table copy with the column changed to a field that has no `5.2`/`5` pass rows yields (c) on pwc and nothing on `pass`).
3. F3 MINOR: the `*_entry` suffix is accepted for any kind. Advisory per Focus 3. Either narrow it to the literal `plan_review_entry` or document the suffix rule in §2.1 and add a one-line test. Not a blocker.

VERDICT: REJECT
