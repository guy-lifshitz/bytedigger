---
voice-profile: technical-en
lot: bd#8
parent: BD-conformance step 2/4 — BD-L1 (oracle freeze-and-verify)
class: SYSTEMATIC
chokepoint: >
  run.py main() — the single process boundary at which one phase (one workflow) is executed.
  The freeze (R1.3) happens there, after the oracle-authoring phase's execute() returns and
  before the process exits; the verify happens there TWICE (`[bd8:6]`) — an entry verify before
  the implementing phase's execute() is entered, and the exit verify that actually discharges
  R1.4 ("before ACCEPTING the implementation phase") after it returns, because CL §4's ADV-1 and
  ADV-2 both act DURING that phase and an entry-only verify cannot observe either. Placing any of
  the three inside WorkflowEngine._emit_phase_artifacts is out of
  contract: that method runs in a finally that is documented as unable to do I/O or raise
  (`[G18r3:EDGE-1]`, engine.py:770-786), and both the freeze and the verify must read file
  contents. Placing them in a per-workflow step is out of contract for the opposite reason:
  a step runs inside the phase it is meant to bound, so an oracle phase could rewrite its own
  frozen set after the freeze.
status: FROZEN v4 (post-gate; Opus gate REJECTED v3 on 9 blocking findings — v4 applies gate edits E1-E14 verbatim: the exit verify (MAJOR-1), scope_digest for the addition case (MAJOR-2), phase_45_spec_lite in the mapping (MAJOR-3), the sentinel-vs-re-entry carve-out (MAJOR-4), the error_code surface (MAJOR-5), the empty/unobserved written set (MAJOR-6), the log-scoped lookup order (MAJOR-7), category tokens (MAJOR-8), AC-7 re-cut to adapter-observed (MAJOR-9). G3-G6 resolved; G7-G8 found and resolved at gate)
base: origin/main @ 2b6589f
governing: SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md (HAL fd35e1304) §3 BD-L1, §9
---

## 0. What this lot is, and what it is not

BD-L1 is the level at which the acceptance criteria stop being editable by the actor they
judge. The engine already produces the two halves separately — the oracle-authoring phase
(`phase_45_spec` / `phase_45_spec_lite`) and the implementing phase (`phase_5_implement`) are distinct `run.py`
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
the set's sorted order, the digest input is the line `<relpath>\0<sha256-of-bytes>`, UTF-8
encoded; the frozen digest is `"sha256:" + sha256(join("\n", lines).encode("utf-8"))`. Each
member's per-member `digest` in the event payload is the bare lowercase hex sha256 of its bytes,
with no `sha256:` prefix. Membership is inside the hash, not beside it, so a member removed or
renamed changes the digest.

**`[bd8:2b]` Membership inside the hash does NOT by itself detect an ADDITION, and saying so is
the point.** Recomputing the `[bd8:2]` construction over the FROZEN member list is invariant
under a new file appearing next to it: the verify would have to be told what the CURRENT
membership is, and at verify time no new `phase_artifacts` exists. CL R1.4 nevertheless makes
additions mismatches (CL §4 ADV-2 adds a file to the oracle SET). The addition case is therefore
discharged by a SECOND, separately-named digest, not by `digest`:

  * The **oracle scope** is the set of directories that contain at least one frozen member,
    non-recursively. It is derived from the frozen member paths and from nothing else — never
    from a naming convention, never from the whole worktree, never from a git delta (a worktree
    scan would flag every uncommitted file left by an earlier phase as an oracle addition).
  * The freeze records `scope: [<reldir>, …]` and
    `scope_digest: "sha256:" + sha256(join("\n", ["<reldir>\0<sorted \n-joined basenames>"]))`
    over that scope, computed at freeze time, so files already present but not written by the
    phase (e.g. a file committed at HEAD) are inside the snapshot and do NOT read as additions.
  * The verify recomputes BOTH. `digest` mismatch or `scope_digest` mismatch is
    `E_ORACLE_MUTATED`, and the message names which of the two and which path.
  * Declared limit, stated in §9: a file added in a SUBdirectory of a scope directory is not
    detected. Non-recursive is the measured-safe choice — recursion over a real `specs/` tree
    makes the scope unbounded — and the limit is published rather than papered over.

**`[bd8:2a]` What is actually in that set — measured, not assumed.** `phase_45_spec` writes the
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

**`[bd8:4a]` A written set the engine did not observe is not an empty oracle.** `phase_artifacts`
carries `write_tracking: "not-observed"` and an empty `written` whenever no step's git delta
resolved — which includes the common case of an absent or non-git `org_config["git_cwd"]`
(engine.py:787-791 via `_resolve_scan_cwd`, engine.py:1146-1149), and that case is reachable
through a real caller (`SYSTEM/cli/build/build-cli.ts:19-34,40` supplies no `git_cwd`). A freeze
over such a payload fails `E_ORACLE_INDETERMINATE` and emits no `oracle_frozen`. An oracle-phase
payload with `write_tracking: "git-delta"` but `written: []` fails the same way: a zero-member
oracle makes every subsequent verify pass trivially, which is the vacuous-freeze shape `[bd8:9]`
exists to forbid, and it is the same class G1 closed for `written_truncated`.

## 2. Where the freeze and the verify live

**`[bd8:5]` Seam:** a new module `engine_py/conformance/oracle.py`, pure-stdlib, doing no I/O
at import (the bd#22 AC-C1 package invariant binds it). It exposes the digest function, the
comparison, and the two event payload builders. It does not know about `run.py`, the workflow
registry, or the CLI.

**`[bd8:6]` Wiring:** three call sites, all in `run.py main()`: freeze after a successful
oracle-phase `execute()`; an ENTRY verify before an implementing-phase `execute()`; and an EXIT
verify after that `execute()` returns and before `main()` reports success. Both verifies are
required and they discharge different requirements. The ENTRY verify is fail-fast: it stops an
implementation from being built over an oracle that was already mutated. The EXIT verify is the
one that discharges CL R1.4 ("Before ACCEPTING the implementation phase the engine MUST
re-verify that hash") and the one that defeats ADV-1 and ADV-2 as CL §4 defines them — "rewrites
an oracle artifact DURING the implementation phase" / "adds a new file to the oracle set DURING
implementation". An entry-only verify cannot observe either adversary, because both act after it
has run. `run.py` stays thin (§1f) — it passes paths and payloads, it computes nothing.

**`[bd8:6a]` Where a refusal surfaces.** A freeze or verify refusal is reported through the
StepResult that `run.py main()` prints: `status: "error"`, `error_code` set to the §5 code
VERBATIM, `recoverable: false`, and `main()` returning 1. It is NOT reported by raising out of
`main()`'s try block: `run.py:233-240` maps any escaping exception to `error_code: "E_RUNNER"`,
which would hide every oracle refusal from the restart governor (`run.py:216-223`), from
`--status`, and from `derive_state`. A test that only greps stdout for the code string does not
measure this; the AC asserts the parsed JSON `error_code` field.

**`[bd8:7]` Which phase is which is declared, not inferred from the name.** A module-level
mapping in `oracle.py` names the oracle-authoring workflows and the implementing workflow, under
the REGISTRY names (`workflows/__init__.py`), not the prose names: oracle-authoring =
{`phase_45_spec`, `phase_45_spec_lite`}; implementing = {`phase_5_implement`}. Both spec workflows
are in the set because both write the spec and review documents
(`workflows/phase_45_spec_lite.py:1255-1262`) and because the OSS driver's declared sequence is
`phase_05_inject → phase_1_discovery → phase_45_spec_lite → phase_5_implement`
(`dogfood/run_oss_driver.py:15,76`) — omitting the lite path would make every SIMPLE-tier build
fail `E_ORACLE_UNFROZEN` at phase 5. A run whose workflow is in neither set is untouched by this
lot — no freeze, no verify, no event. Adding a workflow to either set is a spec amendment, never
an inference.

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
freeze event in the event log it was given, it fails with `E_ORACLE_UNFROZEN`. The lookup order
is fixed and is part of the rule: filter the log's `oracle_frozen`/`oracle_amended` events to
those whose `run_id` matches this invocation's when BOTH carry one, then take the LAST survivor.
Taking the last event first and cross-checking afterwards would make a second build sharing one
log fail the first build's verify. "No oracle was recorded"
must not read as "the oracle is unchanged" (this is the vacuous-pass shape that made
hal#1428's `drift=0 / identical=0` a non-measurement).

## 4. Amendment (R1.5)

**`[bd8:10]` Re-entering the oracle phase re-freezes and is logged as an amendment.** A second
oracle-phase execute() in the same run emits `oracle_amended {phase, reason, previous_digest,
digest, member_count, members, scope, scope_digest}` instead of a second `oracle_frozen`.
`reason` is required and non-empty; an amendment without a reason fails with
`E_ORACLE_AMENDMENT_UNREASONED`. There is no other way to change the frozen set: editing files
without re-entering the phase is exactly ADV-1 and must fail.

**`[bd8:10a]` The reason's input channel is `org_config["oracle_amendment_reason"]`, supplied
through the existing `--ctx-json`.** No new `run.py` flag: `[bd8:6]` keeps run.py thin, run.py
already parses `--ctx-json` into `org_config` and already merges into it (run.py:163-168), and
the read plus the emptiness check live in `oracle.py` per `[bd8:5]`. A consequence worth stating
because a test depends on it: `org_config` is inside the phase-sentinel ctx hash
(`lib/phase_sentinel.py:78-85`), so supplying a reason changes the phase key and the re-entry is
a genuine second `execute()` rather than a served cache hit.

**`[bd8:10b]` A sentinel-RESUMED oracle phase is not a re-entry.** The native durable backend
serves a cached success for a repeated `phase_key(run_id, workflow_name, ctx)` and emits
`phase_sentinel_resumed` without running the engine (`lib/phase_sentinel.py:308-331`). No second
`execute()` occurred, no new `phase_artifacts` exists, and the tree is unchanged. Such an
invocation neither re-freezes, nor amends, nor fails: it is a no-op for this lot. Without this
rule, the ordinary resume-after-interrupt of `phase_45_spec` would fail
`E_ORACLE_AMENDMENT_UNREASONED`, destroying the property the success-only sentinel exists to
provide (#299/#603/#611). NOTE: `question` is NOT part of the phase key — the key hashes
`org_config` and `task_description` only, and `WorkflowContext` has no `task_description` field —
so varying `question` does not force a re-execute and must not be used as a harness knob.

## 5. Error codes

Registered in `engine_py/error_codes.py` (the central registry with drift detection — a code
raised but unregistered fails its own check):

| Code | Raised when |
| --- | --- |
| `E_ORACLE_MUTATED` | verify: recomputed digest ≠ frozen digest (content changed, member added, or member removed) |
| `E_ORACLE_UNFROZEN` | verify: no freeze event in this invocation's event log (including: no event log was given), or a freeze event whose `run_id` contradicts this invocation's |
| `E_ORACLE_INDETERMINATE` | freeze or verify could not read a declared member, or the log could not be read |
| `E_ORACLE_AMENDMENT_UNREASONED` | amendment emitted with an absent/empty `reason` |

## 6. Acceptance criteria

Requirement-bound. Each AC names the requirement it discharges; ACs marked **ADV** are the
adversaries CL §8 requires to have actually run before the level may be claimed.

- **AC-1 (R1.3).** After an oracle-phase run over a known 3-file written set, exactly one
  `oracle_frozen` event exists for that `run_id`; its `member_count` is 3, its `members` are the
  three relative paths in sorted order, and its `digest` equals the `[bd8:2]` construction
  computed independently in the test (not by calling the module under test).
- **AC-2 (R1.4, ADV-1, entry).** Freeze, then rewrite one byte of one member, then run the
  implementing phase → `error_code == "E_ORACLE_MUTATED"`, exit 1, and the failure names the
  offending path.
- **AC-2b (R1.4, ADV-1, AS DEFINED BY CL §4).** Freeze; then run an implementing phase whose OWN
  STEP rewrites one byte of one member — the adversary acts DURING the implementation phase, which
  is what ADV-1 is. The EXIT verify (`[bd8:6]`) → `E_ORACLE_MUTATED`, exit 1. Without this leg §9
  may not name ADV-1: an entry-only verify never observes it.
- **AC-3 (R1.4, ADV-2, entry).** Freeze, then ADD a file to a scope directory without re-entering
  the oracle phase, then run the implementing phase → `E_ORACLE_MUTATED`, and the message names
  `scope_digest` as the mismatching half (`[bd8:2b]`). This is the AC that fails if the addition
  case is left to the member digest alone.
- **AC-3b (R1.4, ADV-2, AS DEFINED BY CL §4).** Freeze; then run an implementing phase whose OWN
  STEP adds a file to a scope directory. The EXIT verify → `E_ORACLE_MUTATED`, exit 1.
- **AC-3c (scope containment, decoy fence).** Freeze; then add a file OUTSIDE every scope
  directory and rewrite an unrelated non-member file that was already dirty at freeze time; run
  the implementing phase → exit 0, no `E_ORACLE_MUTATED`. This is what stops a GREEN from
  discharging AC-3 with a whole-worktree or git-delta-vs-HEAD scan, which would fail every real
  build.
- **AC-3a (R1.4, false-free direction).** Freeze, change nothing, run the implementing phase →
  exit 0 and no `E_ORACLE_MUTATED`. Without this, a verify that always fails passes AC-2/AC-3.
- **AC-4 (R1.4, removal).** Freeze, then delete a member → `E_ORACLE_MUTATED`, distinguished in
  the message from the mutation and addition cases by a CATEGORY TOKEN, not by the path it
  happens to name: the message contains exactly one of `mutated:content`, `mutated:added`,
  `mutated:removed`. Pinning distinguishability by comparing whole messages does not measure this
  — three adversaries acting on three different paths yield three different generic messages.
- **AC-5 (`[bd8:9]`).** Implementing phase over an event log carrying no freeze →
  `E_ORACLE_UNFROZEN`, never a pass. Asserted on the parsed `error_code`, not on stdout.
- **AC-6 (R1.1).** In the event log, the oracle phase's `workflow_finished` precedes the
  implementing phase's first event. Asserted over the real log of a two-invocation run, not
  over a constructed fixture.
- **AC-7 (R1.2, ADAPTER-OBSERVED — see §9).** The two phases are two `run.py main()` invocations,
  each emitting its OWN `run_identity` immediately after its own `workflow_started`
  (engine.py:286-298): the log of a two-phase run carries exactly two `workflow_started` events
  for the two workflows and exactly two `run_identity` events, one following each. This is the
  whole of what R1.2 is enforceable on here. It is NOT asserted that the two `run_identity`
  PAYLOADS differ: that payload is `{engine_version, adapter_identity}`, both constant across
  invocations on one tree, and the only per-invocation discriminator is the envelope `run_id`,
  which `[bd8:8a]` deliberately permits to be identical across the two phases. Making R1.2
  enforceable needs a new invocation-scoped field on `run_identity` — an `engine.py` change,
  out of scope per §8, therefore a different lot. RE-OPEN CRITERION: that lot.
- **AC-8 (R1.5, reasoned leg — the false-free control for the whole amendment path).**
  Re-entering the oracle phase with `org_config["oracle_amendment_reason"]` set to a non-empty
  string emits exactly one `oracle_amended` carrying `previous_digest` ≠ `digest` and no second
  `oracle_frozen`; a subsequent implementing phase verifies against the NEW digest and PASSES
  (exit 0). Without this leg, a GREEN that refuses every re-entry satisfies AC-8a.
- **AC-8a (R1.5, unreasoned leg).** A genuine second oracle-phase `execute()` — forced by a ctx
  that differs in some other `org_config` key so the phase key differs (`[bd8:10b]`) — carrying
  no `oracle_amendment_reason`, or an empty one, fails `E_ORACLE_AMENDMENT_UNREASONED` and emits
  neither `oracle_amended` nor a second `oracle_frozen`.
- **AC-8b (`[bd8:10b]`, resume control).** Re-invoking the oracle phase with an IDENTICAL ctx and
  run_id, so the phase sentinel serves the cached success (`phase_sentinel_resumed` present, no
  new `phase_artifacts`), leaves the log unchanged: exit 0, no second `oracle_frozen`, no
  `oracle_amended`, no `E_ORACLE_AMENDMENT_UNREASONED`.
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
- **AC-13 (`[bd8:7]`).** A run whose workflow is in neither mapping set is untouched: exit 0, no
  `oracle_frozen`, no `oracle_amended`, and none of the §5 codes in the result. This is the
  blast-radius control: a GREEN that freezes on every phase satisfies AC-1..AC-12 and breaks
  every other phase in the engine.
- **AC-14 (§8 scope).** The freeze and verify seam is referenced by `run.py` and the conformance
  package and by nothing else: `engine._emit_phase_artifacts` contains no reference to it (it
  runs in a `finally` documented as unable to do I/O or raise, `[G18r3:EDGE-1]`), no module under
  `engine_py/workflows/` reaches it (a step runs inside the phase it is meant to bound), and no
  module outside `run.py` and `conformance/` imports `conformance.oracle`.
- **AC-15 (G4, `[bd8:8]`).** An implementing-phase invocation given NO `--event-log` fails
  `E_ORACLE_UNFROZEN` — fail-closed, per `[bd8:9]`'s "absence of a freeze event is not
  permission". A logless run has no log in which a freeze could exist, so it is the strongest
  case of absence, not an exemption from it. Control leg: the same invocation on a NON-mapped
  workflow with no `--event-log` is untouched (exit 0, no code), so the rule is scoped to the
  implementing phase and is not a blanket refusal of logless runs. MEASURED MIGRATION COST: zero
  in-tree callers (no `run.py` invocation of `phase_5_implement` exists in this repo), zero
  production build paths (`dogfood/driver-template.sh:143-148` always passes `--event-log`), one
  out-of-tree ad-hoc caller (`SYSTEM/cli/build/build-cli.ts:40`), which must add the flag.

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
- **G3 — RESOLVED by measurement.** The registry names are `phase_45_spec`,
  `phase_45_spec_lite`, `phase_5_implement` (workflows/__init__.py:34-36) and
  `phase_artifacts.phase` carries them verbatim (engine.py:792-793). The hyphen forms do not
  exist; `run.py --workflow` does not normalise. Underscores are normative throughout.
- **G4 — RESOLVED by rule, with the cost measured.** Fail-closed; see AC-15 for the rule and the
  measured migration cost.
- **G5 — RESOLVED by the governing document.** R1.2 is `adapter-observed` in v1; AC-7 is re-cut
  onto the only property observable without an engine.py change. See §9.
- **G6 — RESOLVED by naming the channel.** `org_config["oracle_amendment_reason"]` via the
  existing `--ctx-json`; see `[bd8:10a]`. Both amendment legs are in scope.
- **G7 (found at gate) — RESOLVED by `[bd8:2b]`.** The frozen member digest cannot detect an
  addition on its own; `scope_digest` carries that half, with its limit published in §9.
- **G8 (found at gate) — RESOLVED by `[bd8:4a]`.** An unobserved or empty written set is
  `E_ORACLE_INDETERMINATE`, never a zero-member oracle.

## 8. PREFLIGHT — scope, siblings, and the two traps measured before freeze

**In scope (files the GREEN may touch).** `engine_py/conformance/oracle.py` (new),
`engine_py/run.py` (two call sites, §2), `engine_py/error_codes.py` (four registrations),
`engine_py/ERROR_CODES.md` (regenerated, not hand-edited — it is `--markdown` output), and the
new test file. Nothing else.

**Explicitly NOT in scope (§1v).** `engine.py` — in particular `_emit_phase_artifacts` and its
`finally`; the workflow modules under `engine_py/workflows/`; `attest.py` and the rest of the
conformance package. A diff touching any of them is out of contract and should be rejected
without reading further: it means the freeze migrated back into the phase it is meant to bound.

**`[bd8:11]` Trap 1 — the error-code registry rejects codes that are registered but never
raised.** `error_codes.py --check` reports BOTH `UNREGISTERED` (raised in the tree, absent from
the dict) and **`DEAD`** (present in the dict, raised nowhere), and returns 1 on either. So the
four codes in §5 must be registered *in the same change that raises them* in production code;
a code exercised only from the test file still counts as dead. AC-11 is therefore not
satisfiable by registering the codes early — which is the shape a GREEN naturally reaches for.

**`[bd8:12]` Trap 2 — the sibling surface of `run.py` is wide and must be re-run, not assumed.**
30 test files under `engine_py/tests/` reference `run.py`; two are load-bearing for this change
and are named so the GREEN cannot claim a clean scoped pass without them:
`test_engine_path_closure.py` (a new module in the import closure is exactly what it measures)
and `test_bd22_contracts.py` (the package's no-I/O-at-import invariant, which `[bd8:5]` binds).
Seven files reference the conformance package. §1a applies: the sibling audit runs
`--require-clean` before and after, and a scoped pass is not the ship gate — the full-suite
delta against a declared baseline is (this is BD-L2's R2.6 applied to this lot's own delivery).

## 9. Level claim

On GREEN this lot licenses the claim **BD-L1 over ADV-1 and ADV-2**, and only because both are
executed in the form CL §4 defines them — the adversary acts DURING the implementation phase
(AC-2b, AC-3b), not merely between the two invocations (AC-2, AC-3). An entry-only verify would
license neither, and saying so is the §8 discipline this document is bound by.

Scoped, in the terms the levels require:

- **R1.1, R1.3, R1.4 — enforced.** AC-1..AC-6, AC-9, AC-9a, AC-10.
- **R1.2 — `adapter-observed`, NOT enforced.** Per the governing document's own §8 deferral
  ("The attestation MUST therefore label R1.2 `adapter-observed`, not `enforced`"). AC-7 asserts
  two invocations each emitting their own `run_identity`; it does NOT assert distinct invocation
  identity, because `run_identity`'s payload is `{engine_version, adapter_identity}`, constant
  across invocations on one tree, and `[bd8:8a]` deliberately permits one shared `run_id`. The
  attestation must carry the `adapter-observed` label. RE-OPEN: the lot that adds an
  invocation-scoped field to `run_identity`.
- **R1.5 — enforced for the reasoned and unreasoned amendment (AC-8, AC-8a), with the declared
  carve-out that a sentinel-resumed phase is not a re-entry (`[bd8:10b]`, AC-8b).**
- **Declared limits, published rather than papered over.** (i) The addition case is scoped to the
  non-recursive listing of the directories containing frozen members (`[bd8:2b]`); a file added
  in a subdirectory of a scope directory is NOT detected. (ii) Editing the review document during
  implementation is `E_ORACLE_MUTATED`, deliberately (`[bd8:2a]`). (iii) An implementing-phase
  invocation with no `--event-log` fails closed (AC-15); a caller that wants BD-L1 must supply a
  log.

It licenses nothing about oracle quality (BD-L2) and nothing about authorship attestation beyond
what bd#10 already measured.
