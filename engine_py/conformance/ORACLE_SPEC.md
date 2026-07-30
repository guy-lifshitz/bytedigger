---
voice-profile: technical-en
lot: bd#8
parent: BD-conformance step 2/4 — BD-L1 (oracle freeze-and-verify)
class: SYSTEMATIC
chokepoint: >
  run.py main() — the single process boundary at which one phase (one workflow) is executed.
  The freeze (R1.3) happens there, after the oracle-authoring phase's execute() returns and
  before the process exits; the verify (R1.4) happens there, before the implementing phase's
  execute() is entered. Placing either inside WorkflowEngine._emit_phase_artifacts is out of
  contract: that method runs in a finally that is documented as unable to do I/O or raise
  (`[G18r3:EDGE-1]`, engine.py:770-786), and both the freeze and the verify must read file
  contents. Placing them in a per-workflow step is out of contract for the opposite reason:
  a step runs inside the phase it is meant to bound, so an oracle phase could rewrite its own
  frozen set after the freeze.
status: FROZEN v2 (pre-RED; v1's two gaps resolved — G1 by rule, G2 by measurement)
base: origin/main @ 2b6589f
governing: SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md (HAL fd35e1304) §3 BD-L1, §9
---

## 0. What this lot is, and what it is not

BD-L1 is the level at which the acceptance criteria stop being editable by the actor they
judge. The engine already produces the two halves separately — the oracle-authoring phase
(`phase-45-spec`) and the implementing phase (`phase-5-implement`) are distinct `run.py`
invocations, and `phase_artifacts` (bd#18) already records which files each phase wrote. What
is missing is the binding between them: nothing hashes the oracle at its phase exit, and
nothing re-checks it before the implementation is accepted. This lot supplies exactly that
binding and the one error code that carries its refusal.

**Not in this lot.** Whether the oracle is any *good* — that it rejects the pre-implementation
state, that it is not vacuous, that it binds an observable effect — is BD-L2 (bd#9, R2.1-R2.3).
This lot's oracle may be worthless; it may simply not be edited. Claiming otherwise is the
`§8` violation the parent document exists to prevent: the level is asserted only over the
adversaries actually executed.

## 1. The oracle artifact set

**`[bd8:1]` The set is the oracle-authoring phase's own written set**, taken from the
`phase_artifacts` event that phase emits (`payload.written`, already sorted, engine.py:792).
It is not a glob, not a naming convention, and not a hand-listed manifest — each of those
would be a second source for a fact the engine already records (§1g).

**`[bd8:2]` The frozen digest covers membership AND content.** For each path in the set, in
the set's sorted order, the digest input is the line `<relpath>\0<sha256-of-bytes>`; the
frozen digest is `"sha256:" + sha256(join("\n", lines))`. Content-only hashing would let ADV-2
(add a file) pass; membership-only hashing would let ADV-1 (rewrite a file) pass. CL R1.4 says
additions are mismatches, so membership is inside the hash, not beside it.

**`[bd8:2a]` What is actually in that set — measured, not assumed.** `phase-45-spec` writes the
spec document and the review document and gates on the review (`workflows/phase_45_spec.py`:
`write_spec_doc`, `write_review_doc`, `gate_on_review`); it does **not** write RED tests. So the
oracle frozen here is the *acceptance-criteria document*, which is exactly what CL R1.3 names
("acceptance criteria … content-hashed at that phase's exit") — not a test suite. The review
document is inside the set because the engine wrote it in that phase, and `[bd8:1]` takes the
engine's own record rather than a second, curated list; the consequence is deliberate and must
be stated in the level claim: editing the review during implementation is also
`E_ORACLE_MUTATED`.

**`[bd8:3]` Paths are recorded relative to the run's repository root** and compared as such,
so a run whose worktree is at a different absolute path still verifies. The engine already
resolves that root (`_resolve_scan_cwd`, engine.py); this lot reads it, it does not re-derive it.

**`[bd8:4]` A path in the set that cannot be read at freeze time is not a zero-byte member.**
It fails the freeze with `E_ORACLE_INDETERMINATE` — never a silent digest over a shorter set.
(This is the fail-closed shape CL R2.4 generalises; stating it here costs nothing and closes
the one way a freeze can lie.)

## 2. Where the freeze and the verify live

**`[bd8:5]` Seam:** a new module `engine_py/conformance/oracle.py`, pure-stdlib, doing no I/O
at import (the bd#22 AC-C1 package invariant binds it). It exposes the digest function, the
comparison, and the two event payload builders. It does not know about `run.py`, the workflow
registry, or the CLI.

**`[bd8:6]` Wiring:** exactly one call site each, both in `run.py main()`:
freeze after a successful oracle-phase `execute()`; verify before an implementing-phase
`execute()`. `run.py` stays thin (§1f) — it passes paths and payloads, it computes nothing.

**`[bd8:7]` Which phase is which is declared, not inferred from the name.** A module-level
mapping in `oracle.py` names the oracle-authoring workflow and the implementing workflow
(today `phase-45-spec` and `phase-5-implement`). A run whose workflow is neither is untouched
by this lot — no freeze, no verify, no event.

## 3. Persistence: the log is the store

**`[bd8:8]` The frozen digest is carried by the append-only event log and nowhere else.**
Freeze emits `oracle_frozen {phase, run_id, member_count, digest, members:[{path, digest}]}`;
verify reads the LAST `oracle_frozen`-or-`oracle_amended` event **in the event log this
invocation was given** and recomputes against the live tree. A sidecar file would be a second
source and a mutable one — the exact property BD-L1 exists to remove.

**`[bd8:8a]` The key is the LOG, with `run_id` as a fail-closed cross-check — measured, not
assumed** (resolves G2). `run.py --run-id` is caller-supplied and defaults to *fresh random
hex per invocation* (run.py:14,116). Stability across phases is a property of the driver, not
of the engine: HAL's `run-phase.ts` inherits one `HAL_BUILD_RUN_ID` per batch item
(run-phase.ts:57-63) and the dogfood driver threads one `--run-id` across phases
(dogfood/README.md:10) — but a driverless two-phase run gives the two phases *different* ids.
Keying the lookup on `run_id` alone would therefore make the freeze unfindable in exactly the
driverless case and turn a real mutation into `E_ORACLE_UNFROZEN`-shaped noise. Keying on the
log alone would let a *previous build's* freeze authorise this one (the stale-log class HAL
burned itself on in GH#215). So: the lookup is scoped to the given log, and if BOTH the freeze
event and this invocation carry a `run_id`, a mismatch is `E_ORACLE_UNFROZEN` — never a pass.
One shared log per build is the engine's existing convention (`--event-log
{worktree}/.hal-build/events.jsonl`).

**`[bd8:9]` Absence of a freeze event is not permission.** If the implementing phase finds no
freeze event for its `run_id`, it fails with `E_ORACLE_UNFROZEN`. "No oracle was recorded"
must not read as "the oracle is unchanged" (this is the vacuous-pass shape that made
hal#1428's `drift=0 / identical=0` a non-measurement).

## 4. Amendment (R1.5)

**`[bd8:10]` Re-entering the oracle phase re-freezes and is logged as an amendment.** A second
oracle-phase execute() in the same run emits `oracle_amended {phase, reason, previous_digest,
digest, member_count, members}` instead of a second `oracle_frozen`. `reason` is required and
non-empty; an amendment without a reason fails with `E_ORACLE_AMENDMENT_UNREASONED`. There is
no other way to change the frozen set: editing files without re-entering the phase is exactly
ADV-1 and must fail.

## 5. Error codes

Registered in `engine_py/error_codes.py` (the central registry with drift detection — a code
raised but unregistered fails its own check):

| Code | Raised when |
| --- | --- |
| `E_ORACLE_MUTATED` | verify: recomputed digest ≠ frozen digest (content changed, member added, or member removed) |
| `E_ORACLE_UNFROZEN` | verify: no freeze event for this `run_id` |
| `E_ORACLE_INDETERMINATE` | freeze or verify could not read a declared member, or the log could not be read |
| `E_ORACLE_AMENDMENT_UNREASONED` | amendment emitted with an absent/empty `reason` |

## 6. Acceptance criteria

Requirement-bound. Each AC names the requirement it discharges; ACs marked **ADV** are the
adversaries CL §8 requires to have actually run before the level may be claimed.

- **AC-1 (R1.3).** After an oracle-phase run over a known 3-file written set, exactly one
  `oracle_frozen` event exists for that `run_id`; its `member_count` is 3, its `members` are the
  three relative paths in sorted order, and its `digest` equals the `[bd8:2]` construction
  computed independently in the test (not by calling the module under test).
- **AC-2 (R1.4, ADV-1).** Freeze, then rewrite one byte of one member, then run the
  implementing phase → exit is non-zero and `E_ORACLE_MUTATED` is raised. The failure names the
  offending path.
- **AC-3 (R1.4, ADV-2).** Freeze, then ADD a file to the oracle directory without re-entering
  the oracle phase, then run the implementing phase → `E_ORACLE_MUTATED`. This is the AC that
  fails if the digest is taken over content alone.
- **AC-3a (R1.4, false-free direction).** Freeze, change nothing, run the implementing phase →
  exit 0 and no `E_ORACLE_MUTATED`. Without this, a verify that always fails passes AC-2/AC-3.
- **AC-4 (R1.4, removal).** Freeze, then delete a member → `E_ORACLE_MUTATED`, distinguished in
  the message from the mutation and addition cases.
- **AC-5 (`[bd8:9]`).** Implementing phase with no freeze event for its `run_id` →
  `E_ORACLE_UNFROZEN`, never a pass.
- **AC-6 (R1.1).** In the event log, the oracle phase's `workflow_finished` precedes the
  implementing phase's first event. Asserted over the real log of a two-invocation run, not
  over a constructed fixture.
- **AC-7 (R1.2).** The two phases are distinct invocations: their `run_identity` events carry
  distinct invocation identity, and no transcript of the authoring phase is present in the
  implementing phase's assembled prompt. **Measured, not asserted from design**: the check
  reads the `model_invocation_attested` payloads bd#10 already emits.
- **AC-8 (R1.5).** Re-entering the oracle phase with a reason emits `oracle_amended` carrying
  `previous_digest` ≠ `digest`, and a subsequent implementing phase verifies against the NEW
  digest and passes; the same amendment with an empty reason fails
  `E_ORACLE_AMENDMENT_UNREASONED`.
- **AC-9 (`[bd8:4]`).** A member unreadable at freeze time → `E_ORACLE_INDETERMINATE`, and NO
  `oracle_frozen` event is emitted (a freeze that half-happened is worse than none).
- **AC-9a (G1).** A `phase_artifacts` payload carrying `written_truncated: true` →
  `E_ORACLE_INDETERMINATE` and NO `oracle_frozen` event. Control leg: the same set under the
  untruncated payload freezes normally, so the AC cannot be satisfied by refusing every freeze.
- **AC-10 (§1g).** The oracle set used by the freeze is really taken from the phase's own
  `phase_artifacts.written` and not re-derived: a run whose written set differs from a
  same-named directory listing freezes the FORMER. Forcing form — a GREEN that globs the
  directory fails this.
- **AC-11 (registry).** Every code in §5 is present in `error_codes.py` with a one-line
  condition, and `error_codes.py --check` reports no drift.
- **AC-12 (bd#22 AC-C1).** Importing `conformance.oracle` performs no I/O, no subprocess, no
  directory scan.

## 7. Spec gaps — resolved before freeze

- **G1 — RESOLVED by rule, not by reachability.** `phase_artifacts` truncates its `written`
  list when the serialised line exceeds the log's per-line limit, replacing it with
  `written_truncated: true` + `written_count` + `written_digest` (engine.py:1320-1334).
  Whether a *spec* phase can reach that limit is unmeasured — and the answer does not change
  the rule: a freeze over a sampled set would record a digest for a membership the engine never
  saw whole, which is the vacuous-freeze shape `[bd8:9]` exists to forbid. **A freeze whose
  source payload carries `written_truncated: true` fails `E_ORACLE_INDETERMINATE` and emits no
  `oracle_frozen`.** (New AC-9a below.)
- **G2 — RESOLVED by measurement.** See `[bd8:8a]`: `run_id` stability is a driver property,
  not an engine guarantee, so the lookup is log-scoped with `run_id` as a fail-closed
  cross-check rather than as the key.

## 8. Level claim

On GREEN this lot licenses the claim **BD-L1 over ADV-1 and ADV-2** — the two adversaries in
AC-2 and AC-3, each executed. It licenses nothing about oracle quality (BD-L2) and nothing
about authorship attestation beyond what bd#10 already measured.
