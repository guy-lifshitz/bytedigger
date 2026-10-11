# bd#130 — AUTONOMOUS rules for machine caps and SUPERVISED pauses

**Status: DRAFT Rev 1** · **Class:** SYSTEMATIC (undefined behaviour at caps/pauses) · **Tier:** TIER2
**Builds on:** main `aef832c`. Deterministic only: the Phase 5 entry decision is code over `build-state.yaml`, no model call.

## §1 Problem (measured on `aef832c`)

1. `phases/phase-45-spec.md` (Plan-Review Gate) says "max 2 cycles" and nothing for "cycle 2 is still REVISE".
   The Phase 5 entry check (`scripts/phase-deliverables.tsv` rows `4.5` and `5`: `field_eq plan_review pass`,
   evaluated by `check_deliverables` in `scripts/build-gate.sh` and `checkDeliverables` in
   `scripts/ts/build-phase-gate.ts`) accepts only `plan_review: pass`. The only way forward is a manual override.
2. COMPLEX forces SUPERVISED (Phase 0) and `--auto` ("skip all human gates") does not say what replaces each
   gate. Machine caps (Plan-Review, Phase 6, test-fix, NEEDS_CONTEXT) are not human gates, so they stay undefined under `--auto`.

Out of scope: the Python engine (`phase_45_spec.py`) already ends cycle-2 REVISE with the terminal
`E_REVIEW_FAILED` (a defined stop); it is not changed.

## §2 Rule set

Policy switch `BD_AUTONOMOUS_CAP_POLICY` (read by both gate backends from the environment):
`autonomous` (default, also when unset or empty) | `legacy`. Any other value → one soft-block row
`BD_AUTONOMOUS_CAP_POLICY: unknown value '<v>' (use autonomous|legacy)` and no other new rule runs.
`legacy` restores the previous behaviour byte for byte: Phase 5 / 4.5 accept only `plan_review: pass`,
no COMPLEX check, no pause-log check.

### §2.1 New table kind `plan_review_entry` (arity 3: phase, kind, field)

`phase-deliverables.tsv` rows `4.5` and `5` change from `field_eq plan_review pass` to
`plan_review_entry plan_review`. Under `legacy` the kind behaves exactly like `field_eq <field> pass`.
Under `autonomous`, for `plan_review` value `v` (quotes stripped, as `yaml_get`):

- `v == pass` → OK (unchanged). If `mode` is `AUTONOMOUS`, `complexity` is `COMPLEX`: see §2.2.
- `v == pass_with_concerns` → OK only when ALL hold; each failed condition adds its own row:
  a. `mode` is `AUTONOMOUS` (SUPERVISED never accepts it);
  b. `plan_review_concerns` is set and not `[]` (single-line value; a block sequence reads as empty and fails);
  c. the RED Opus validation backstop is still mandatory: the deliverable table itself still holds
     `5.2 field_eq opus_validation pass` and `5 field_eq opus_validation pass` (checked in code on the loaded table);
  d. `autonomous_pauses` is set and valid (§2.3).
- anything else (missing, `approved`, `fail`, …) → row `plan_review=pass (got: <v|<missing>>)`, exactly the legacy text.

A failing `pass_with_concerns` row is the **stop with resumable state**: the gate soft-blocks, `build-state.yaml`
keeps `plan_review`, `plan_review_concerns`, `current_phase: 4.5`, and `/build continue` re-enters after the
reason is fixed. Row text for (a): `plan_review=pass_with_concerns needs mode AUTONOMOUS (got mode: <m|<missing>>)`;
(b): `plan_review_concerns has no value`; (c): `plan_review=pass_with_concerns needs the mandatory opus_validation rows in the deliverable table`.

### §2.2 COMPLEX under AUTONOMOUS

SUPERVISED COMPLEX is unchanged (no check). When `mode` is `AUTONOMOUS` and `complexity` is `COMPLEX`
(reachable only via explicit `--auto`), Phase 5 entry requires `mode_override: auto_flag_complex`, else row
`COMPLEX in AUTONOMOUS needs mode_override: auto_flag_complex (got: <v|<missing>>)`. It also requires §2.3.
This check runs inside `plan_review_entry` on phase `5` only (not 4.5) and only under `autonomous`.

### §2.3 Pause log `autonomous_pauses`

Single-line inline value: `none` or `[P1,P3,...]` (ids from `scripts/autonomous-pauses.tsv`, whitespace allowed).
Required (§2.1d, §2.2). Missing/empty → row `autonomous_pauses has no value`; any unknown id →
row `autonomous_pauses: unknown pause id '<id>'` (first unknown only).

### §2.4 Pause table `scripts/autonomous-pauses.tsv`

TAB-separated: `id, phase, supervised, autonomous, state_field`. `autonomous` is `proceed` (continue,
the named field records the concern) or `stop` (halt with resumable state, `blocked_reason` set).
`commands/build.md` carries the same table, ids and rules; a test pins that the ids/rules in both match.

| id | phase | SUPERVISED pause | AUTONOMOUS | state field |
|----|-------|------------------|-----------|-------------|
| P0 | 0 | COMPLEX forced to SUPERVISED | proceed (only with explicit `--auto`) | `mode_override` |
| P1 | 3 | clarifying questions answered by user | proceed with stated assumptions | `phase3_assumptions` |
| P2 | 4 | `build-architecture.md` approval | proceed with the recommended option | `architecture_choice` |
| P3 | 4.5 | spec shown to user for approval | proceed (Plan-Review is the check) | `plan_review` |
| P4 | 4.5 | Plan-Review cap: cycle 2 still REVISE | proceed as `pass_with_concerns` iff §2.1 holds, else stop | `plan_review_concerns` |
| P5 | 5 | `DONE_WITH_CONCERNS` shown to user | proceed, concerns logged | `agent_concerns` |
| P6 | 5 | test-fix cap (3) reached | stop | `blocked_reason` |
| P7 | 5 | `NEEDS_CONTEXT` cap (2 retries) reached | stop | `blocked_reason` |
| P8 | 5 | `BLOCKED` agent status | stop | `blocked_reason` |
| P9 | 6 | review cap (2 cycles) still has issues | proceed, warnings logged | `review_warnings` |
| P10 | 7 | summary shown, user acknowledges | proceed, report path logged | `post_deploy_report` |

Only P3/P4/P0 are enforced at Phase 5 entry by code (§2.1–§2.2); the others are rules the phase docs
reference by id and the pause log validates.

## §3 Doc changes

`commands/build.md`: table of §2.4 under the AUTONOMOUS enforcement block; Plan-Review line and Phase 5 entry line
mention `pass_with_concerns` (AUTONOMOUS only). `phases/phase-45-spec.md`: step 3 gets "cap reached" rule; `phases/phase-5-implement.md`
entry gate; `phases/phase-0-classify.md` COMPLEX line; `skills/bytedigger/SKILL.md` `--auto` line points to the table. `CHANGELOG.md` entry.

## §4 Acceptance criteria

- AC1 (`bash` and `ts` backends, same stdout): phase 5, AUTONOMOUS, `plan_review: pass_with_concerns`, concerns `[x]`, `autonomous_pauses: none` → pass.
- AC2: same with `mode: SUPERVISED` → soft block containing the (a) row.
- AC3: AUTONOMOUS + concerns missing or `[]` → soft block with the (b) row; `autonomous_pauses` missing → `autonomous_pauses has no value`; unknown id → unknown-id row.
- AC4: if the table copy lacks the `5.2 … opus_validation pass` row → (c) row (fixture table via a copied scripts dir).
- AC5: `plan_review: pass`, FEATURE or SUPERVISED, any mode → pass, no new rows (regression).
- AC6: AUTONOMOUS + COMPLEX + `plan_review: pass` without `mode_override` → §2.2 row at phase 5 (not at 4.5); with `mode_override: auto_flag_complex` and `autonomous_pauses: none` → pass. SUPERVISED + COMPLEX + `pass` → pass (separate test).
- AC7: `BD_AUTONOMOUS_CAP_POLICY=legacy`: `pass_with_concerns` → legacy row `plan_review=pass (got: pass_with_concerns)`; AUTONOMOUS COMPLEX `pass` → pass.
- AC8: unknown policy value → the unknown-value row, in both backends.
- AC9: SUPERVISED behaviour unchanged: for every state in the AC1–AC6 fixtures run with `mode: SUPERVISED`, stdout equals the stdout of `legacy` on the same state.
- AC10: bash and TS stdout identical on every AC fixture (shadow-mode byte-compare).
- AC11: `scripts/autonomous-pauses.tsv` ids/rules equal the table in `commands/build.md`; every id P0..P10 present; `autonomous` ∈ {proceed, stop}.
- AC12: malformed `autonomous-pauses.tsv` is not read by the gate in any other way than id validation: a missing file → row `autonomous pause table unreadable: <path>`.

## §5 Kill switch

`BD_AUTONOMOUS_CAP_POLICY=legacy` (§2). No other state; removing it from the env returns to `autonomous`.
