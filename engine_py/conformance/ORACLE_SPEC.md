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
status: >
  FROZEN v6 (post-gate round 3). Rounds 1-3 of the Opus gate rejected v3 (9 blocking), v4 (7) and
  v5 (4). v6 applies E23-E29: `[bd8:1b]`'s cross-check must be a REAL copy and is now forced by
  AC-1a; AC-17 covers the two verify-side I/O branches §2/§5 declared but nothing asserted, so
  neither can surface as `E_RUNNER`; §7's G1 no longer states the rule v5 revoked; `[bd8:1a]`
  states what was MEASURED about scratchpad scoping instead of claiming a guarantee no producer
  gives, with the consequence declared in §9 (ic); the unreachable scope limit (ii) is struck; and
  AC-3 no longer pins the word `scope_digest`. All four round-3 findings were type (b) —
  defects in this lot's own implementation of its spec, not a failure of the v5 foundation, which
  the gate confirmed correctly and completely executed. HISTORY: v5 (post-gate round 2). v3 was REJECTED on 9 blocking findings and v4 applied gate edits
  E1-E14 verbatim (exit verify, scope_digest, phase_45_spec_lite, sentinel carve-out, error_code
  surface, log-scoped lookup order, category tokens, AC-7 as adapter-observed). v4 was then
  REJECTED on 7 more, of which R2-MAJOR-1 is foundational: `[bd8:1]`'s premise was FALSE in
  production — the engine records no write set for the oracle documents. v5 applies D1(a) plus
  E15-E22: the set is re-cut from `phase_artifacts.written` onto `<scratchpad_dir>/specs`
  (`[bd8:1]`), `[bd8:2a]` now carries a MEASURED payload from this engine instead of an argument,
  AC-10 changes subject from "not a directory listing" to "not outside the run's namespace",
  AC-9a is STRUCK and AC-16/`[bd8:4a]` re-cut because their subject moved, and AC-9b asserts the
  §9 limits. G9-G11 recorded in §7. The long-term fix (manifest as source, not filter) is bd#36;
  this lot does not wait on it.
base: origin/main @ 2b6589f
governing: SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md (HAL fd35e1304) §3 BD-L1, §9
---

## 0. What this lot is, and what it is not

BD-L1 is the level at which the acceptance criteria stop being editable by the actor they
judge. The engine already produces the two halves separately — the oracle-authoring phase
(`phase_45_spec` / `phase_45_spec_lite`) and the implementing phase (`phase_5_implement`) are distinct `run.py`
invocations, and each phase writes its documents into its own run-scoped scratchpad. (v1-v4 said
here that `phase_artifacts` "already records which files each phase wrote"; G9 measured that this
is false for the oracle documents — see `[bd8:1]` and `[bd8:2a]`.) What is missing is the binding
between the two halves: nothing hashes the oracle at its phase exit, and nothing re-checks it
before the implementation is accepted. This lot supplies exactly that
binding and the one error code that carries its refusal.

**Not in this lot.** Whether the oracle is any *good* — that it rejects the pre-implementation
state, that it is not vacuous, that it binds an observable effect — is BD-L2 (bd#9, R2.1-R2.3).
This lot's oracle may be worthless; it may simply not be edited. Claiming otherwise is the
`§8` violation the parent document exists to prevent: the level is asserted only over the
adversaries actually executed.

## 1. The oracle artifact set

**`[bd8:1]` The set is the non-recursive listing of the oracle phase's document directory,
`<scratchpad_dir>/specs`, files only** — taken at oracle-phase exit, recorded relative to
`scratchpad_dir`.

This is a REVERSAL of v1-v4, forced by measurement (G9). Those versions took the set from
`phase_artifacts.written` and justified it by §1g: a glob would be "a second source for a fact
the engine already records". **The engine does not record it.** `self._written` is fed from
exactly one place — the git delta of `org_config["git_cwd"]` (engine.py:493; the
`worker_written_paths` manifest below it at :497-508 only NARROWS that set, and DEFERs to the
delta when absent) — while both spec workflows write their documents under
`org_config["scratchpad_dir"]` (`_resolve_scratchpad`, phase_45_spec.py:372-376, used at
:1005-1007; phase_45_spec_lite.py:373-374). Those are different trees under both measured
drivers. A first source is not a second source, so §1g does not forbid this listing — it
requires it.

**`[bd8:1a]` The namespace is the `scratchpad_dir` the caller supplied — and how tightly that is
scoped is the CALLER's property, measured, not this lot's guarantee.** No `<run_id>/`
subdirectory is interposed by the phase: `_resolve_scratchpad` takes `org_config["scratchpad_dir"]`
and only expands and resolves it (`Path(raw).expanduser().resolve()`,
phase_45_spec.py:372-377; phase_45_spec_lite.py:255-257). What the drivers put there was measured
and they DISAGREE: `dogfood/run_oss_driver.py:185` supplies `<artifacts_dir>/scratchpad` with no
run component; `SYSTEM/cli/build/build-cli.ts:30` passes the caller's `--scratchpad-dir` string
through unchanged; `SYSTEM/cli/build/batch-build-regressions.test.sh:205` pins a repo-rooted
`<repo>/.hal-build`. Only lib/project_root.py:24-31 — a REVERSE derivation helper, not a producer —
documents a `<repo>/<foreign_state_dirname()>/scratchpad/<run>` convention. So `scratchpad_dir` is
build-scoped at best and may be repo-scoped, and §9 records the consequence rather than assuming it
away. What this lot DOES guarantee is the boundary: the freeze reads the directory it is given and
never walks UP out of it, so whatever else lives beside it — a sibling run's scratchpad, the shared
scratchpad root — is not this run's oracle. AC-10 forces exactly that boundary, and it is doing
production work, not fixture work: where `scratchpad_dir` is `<repo>/.hal-build`, the event log
itself (`run_oss_driver.py:187`) sits at the scratchpad root, and AC-10 leg 1 is what keeps it out
of the frozen set.

**`[bd8:1b]` `phase_artifacts.written` is retained as a recorded CROSS-CHECK, never as the
source.** The freeze records `written_crosscheck: [...]` copied VERBATIM from the oracle phase's
own `phase_artifacts` payload for this run and phase — the actual list, whatever it holds, never a
constant and never a value derived from the member set — so the divergence that produced G9 stays
visible in the log instead of being silently designed around. When that payload carries
`written_truncated: true`, `written_crosscheck` is the truncation MARKER
(`{"truncated": true, "count": <written_count>, "digest": <written_digest>}`) and never the
binary-searched sample the payload also carries (engine.py:1329-1336), so the log never implies the
engine saw a membership it did not. A mismatch between the cross-check and the frozen members is
NOT an error at this level — under every measured driver it is empty while the members are not (see
`[bd8:2a]`) — and no AC turns it into one. It is evidence for the lot that fixes the root cause
(bd#36: `worker_written_paths` as a source rather than a filter), not a gate here. It is,
however, ASSERTED to be a real copy (AC-1a): a cross-check that can be hard-coded records nothing,
which is the `drift=0 / identical=0` non-measurement `[bd8:9]` exists to refuse.

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
    non-recursively. Under `[bd8:1]` it collapses to the single document directory
    `<scratchpad_dir>/specs`. It is derived from the frozen member paths and from nothing else —
    never from a naming convention, never from the whole worktree, never from a git delta (a
    worktree scan would flag every uncommitted file left by an earlier phase as an addition).
  * The freeze records `scope: [<reldir>, …]` and a `scope_digest`, computed at freeze time, so
    files already present but not written by the phase are inside the snapshot and do NOT read as
    additions. The construction, stated exactly because a reviewer cannot check what is not
    written down: for each `<reldir>` in `scope`, in sorted order, list the directory
    NON-RECURSIVELY and keep only REGULAR FILES — subdirectories, symlinks to directories and
    anything that is not a file are excluded, and a subdirectory appearing or disappearing
    therefore does not move the digest; the line is `<reldir>` then a NUL byte then those file
    names sorted and joined by `"\n"`; the lines are joined by `"\n"`, UTF-8 encoded;
    `scope_digest = "sha256:" + sha256(that)`. The two joins deliberately share a separator: the
    encoding is injective only because a NUL byte cannot occur in a path component, which is what
    the `\0` is for.
  * The verify recomputes BOTH. `digest` mismatch or `scope_digest` mismatch is
    `E_ORACLE_MUTATED`, and the message names which of the two and which path. A scope directory
    that no longer exists at verify time is `E_ORACLE_MUTATED` with `mutated:removed` — never an
    exception out of `main()` (`[bd8:6a]`).
  * Declared limits, stated in §9: (i) a file added in a SUBdirectory of a scope directory is not
    detected — non-recursive is the measured-safe choice, since recursion over a real `specs/`
    tree makes the scope unbounded and turns every file the implementing phase writes beneath it
    into a false `E_ORACLE_MUTATED`. This limit is published rather than papered over, and it is
    ASSERTED in both directions (AC-9b) — a limit no test measures is a claim, not a limit. v4's
    limit (ii), a frozen member at the scratchpad ROOT making the scope `"."`, is STRUCK: under
    `[bd8:1]` every member is under `specs/`, so the scope is always `["specs"]` and that case
    cannot arise.

**`[bd8:2a]` What is actually in that set — MEASURED on this engine, and the measurement is
pasted here rather than described.** `phase_45_spec` writes the spec document and the review
document and gates on the review (`write_spec_doc`, `write_review_doc`, `gate_on_review`); it
does **not** write RED tests. So the oracle frozen here is the *acceptance-criteria document*,
which is exactly what CL R1.3 names ("acceptance criteria … content-hashed at that phase's
exit") — not a test suite. The review document is inside the set, and the consequence is
deliberate and restated in the level claim: editing the review during implementation is also
`E_ORACLE_MUTATED`.

What v1-v4 asserted without measuring is WHERE those documents land. Running this engine's own
`run.py` on the production topology — the step writing into `org_config["scratchpad_dir"]` exactly
as `_resolve_scratchpad` requires, `org_config["git_cwd"]` pointing at the git repo exactly as
both drivers set it — the engine emitted:

```json
{ "phase": "phase_45_spec", "read": [], "read_tracking": "declared-only",
  "write_tracking": "git-delta", "written": [] }
```

with `specs/build-spec.md` and `specs/build-plan-review.md` both present in the scratchpad and the
repository working tree clean. **`written` is empty, and `write_tracking` is `"git-delta"`** — the
payload does not merely omit the documents, it reports a successfully-observed empty set, so
nothing downstream could distinguish it from a phase that wrote nothing. Freezing that payload
would have produced `E_ORACLE_INDETERMINATE` on every real build under `[bd8:4a]` as v4 wrote it,
and in a target repo that does not gitignore the engine's state directory it would instead have
absorbed the event log — whose bytes change between freeze and verify — giving `E_ORACLE_MUTATED`
on every implementing phase. This is why `[bd8:1]` is reversed and why `[bd8:4a]` is re-cut below.

**`[bd8:3]` Paths are recorded relative to `scratchpad_dir`** and compared as such, so a run whose
scratchpad is at a different absolute path still verifies. `scratchpad_dir` is supplied by the
caller and read verbatim (`_resolve_scratchpad`); this lot reads it, it does not re-derive it, and
it never resolves a member outside it.

**`[bd8:4]` A path in the set that cannot be read at freeze time is not a zero-byte member.**
It fails the freeze with `E_ORACLE_INDETERMINATE` — never a silent digest over a shorter set.
(This is the fail-closed shape CL R2.4 generalises; stating it here costs nothing and closes
the one way a freeze can lie.)

**`[bd8:4a]` An EMPTY OR ABSENT document directory is not an empty oracle — RE-CUT under D1(a).**
v4 attached this rule to `phase_artifacts.written`; `[bd8:2a]` measured that payload reporting
`written: []` with `write_tracking: "git-delta"` on a healthy production run, so the v4 rule would
have refused every real build. The rule was right and its subject was wrong. Re-cut onto the
source `[bd8:1]` actually uses:

  * `<scratchpad_dir>/specs` absent, unreadable, or containing no regular file → the freeze fails
    `E_ORACLE_INDETERMINATE` and emits no `oracle_frozen`. A zero-member oracle makes every
    subsequent verify pass trivially, which is the vacuous-freeze shape `[bd8:9]` forbids.
  * `org_config["scratchpad_dir"]` absent or empty on an oracle-phase run → the same
    `E_ORACLE_INDETERMINATE`. The engine's own spec workflows already treat it as REQUIRED and
    raise without it (phase_45_spec.py:375-376), so this refuses nothing that works today.
  * `written_truncated` and `write_tracking` no longer gate the freeze at all. They describe the
    `[bd8:1b]` cross-check, which is evidence and not a gate. G1's rule survives only in the
    weakened form that a truncated payload is copied into `written_crosscheck` as the truncation
    marker rather than as a sampled list, so the log never implies the engine saw a membership it
    did not.

## 2. Where the freeze and the verify live

**`[bd8:5]` Seam:** a new module `engine_py/conformance/oracle.py`, pure-stdlib, doing no I/O
at import (the bd#22 AC-C1 package invariant binds it). It exposes the digest function, the
comparison, and the two event payload builders. It does not know about `run.py`, the workflow
registry, or the CLI.

**`[bd8:6]` Wiring:** three call sites, all in `run.py main()`: freeze after a successful
oracle-phase `execute()`; an ENTRY verify before an implementing-phase `execute()`; and an EXIT
verify after that `execute()` returns, run REGARDLESS of the phase's own outcome — a phase that
failed for an unrelated reason may still have mutated the oracle, and a verify that runs only on
the success path lets that mutation survive every restart-governor retry. Both verifies are
required and they discharge different requirements. The ENTRY verify is fail-fast: it stops an
implementation from being built over an oracle that was already mutated. The EXIT verify is the
one that discharges CL R1.4 ("Before ACCEPTING the implementation phase the engine MUST
re-verify that hash") and the one that defeats ADV-1 and ADV-2 as CL §4 defines them — "rewrites
an oracle artifact DURING the implementation phase" / "adds a new file to the oracle set DURING
implementation". An entry-only verify cannot observe either adversary, because both act after it
has run. `run.py` stays thin (§1f) — it passes paths and payloads, it computes nothing.

**`[bd8:6b]` An oracle phase given no `--event-log` freezes nothing and fails nothing.** The
digest is carried by the log and nowhere else (`[bd8:8]`), so a logless oracle phase has no place
to record one; it emits no `oracle_frozen` and returns its own result untouched. This is NOT a
weakening: the implementing phase is fail-closed whether or not the oracle phase refused
(AC-15), so a logless build cannot reach a BD-L1 pass by this route. The alternative — failing the
oracle phase closed — was measured and rejected: it breaks `SYSTEM/cli/build/build-cli.ts:40` on
every spec phase in addition to every implementing phase, buying nothing the implementing-phase
rule does not already buy.

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

- **AC-1 (R1.3).** After an oracle-phase run whose document directory holds a known 3 files,
  exactly one `oracle_frozen` event exists for that `run_id`; its `member_count` is 3, its
  `members` are the three paths relative to `scratchpad_dir` in sorted order, its `digest` equals
  the `[bd8:2]` construction and its `scope_digest` the `[bd8:2b]` construction, both computed
  independently in the test (not by calling the module under test). It also carries
  `written_crosscheck` (`[bd8:1b]`), which on this engine's measured production topology is
  EMPTY while the members are not — asserted, so the divergence that produced G9 is recorded by
  the test rather than remembered by a person. The freeze-then-verify pair leaves exactly ONE
  `oracle_frozen`: only the oracle phase freezes.
- **AC-1a (`[bd8:1]`/`[bd8:1b]`, the cross-check is a measurement, not a constant).** An oracle
  phase whose step ALSO writes a file inside `org_config["git_cwd"]` — so that
  `phase_artifacts.written` is NON-empty — freezes with: `members` still exactly the
  `<scratchpad_dir>/specs` listing, the git-observed path NOT among them, and
  `written_crosscheck` equal to that phase's `phase_artifacts.written` verbatim. Two GREENs die
  here and nowhere else: one that emits a hard-coded `written_crosscheck`, and one whose member set
  unions or falls back to `phase_artifacts.written` — which in any topology where the scratchpad
  sits inside `git_cwd` absorbs the working tree, including the event log, and gives
  `E_ORACLE_MUTATED` on every implementing phase (`[bd8:2a]`).
- **AC-2 (R1.4, ADV-1, entry).** Freeze, then rewrite one byte of one member, then run the
  implementing phase → `error_code == "E_ORACLE_MUTATED"`, exit 1, and the failure names the
  offending path. The implementing phase's steps MUST NOT have run: the ENTRY verify refuses
  before `execute()` is entered, and that — not the code, which the exit verify also produces —
  is the whole of what distinguishes the two call sites. Asserted on a witness the implementing
  step writes (absent ⇒ the phase never ran) and on the absence of a `step_started` event for it.
  Without this assertion an EXIT-ONLY GREEN passes AC-2, AC-3, AC-4, AC-5 and AC-15 unchanged.
- **AC-2b (R1.4, ADV-1, AS DEFINED BY CL §4).** Freeze; then run an implementing phase whose OWN
  STEP rewrites one byte of one member — the adversary acts DURING the implementation phase, which
  is what ADV-1 is. The EXIT verify (`[bd8:6]`) → `E_ORACLE_MUTATED`, exit 1. Without this leg §9
  may not name ADV-1: an entry-only verify never observes it.
- **AC-3 (R1.4, ADV-2, entry).** Freeze, then ADD a file to a scope directory without re-entering
  the oracle phase, then run the implementing phase → `E_ORACLE_MUTATED` with the category token
  `mutated:added` (`[bd8:2b]`). This is the AC that fails if the addition case is left to the
  member digest alone. No clause pins the WORD `scope_digest` in the message: the category tokens
  already carry the distinguishing work, and a vocabulary substring pins words, not behaviour.
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
  string emits exactly one `oracle_amended` carrying the FULL `[bd8:10]` payload —
  `previous_digest` ≠ `digest`, and `scope` and `scope_digest` RECOMPUTED at amendment time — and
  no second `oracle_frozen`; a subsequent implementing phase verifies against the NEW digest AND
  the NEW `scope_digest` and PASSES (exit 0). Second leg, non-optional: an addition to a scope
  directory made AFTER the amendment is still `E_ORACLE_MUTATED`. Without it, a GREEN whose
  amendment omits the scope half — and whose verify skips the scope check when the looked-up
  event carries none — passes every other test while disabling ADV-2 for the remainder of any
  build that amends, which is the normal multi-cycle spec path. Without the first leg, a GREEN
  that refuses every re-entry satisfies AC-8a.
- **AC-8a (R1.5, unreasoned leg).** A genuine second oracle-phase `execute()` — forced by a ctx
  that differs in some other `org_config` key so the phase key differs (`[bd8:10b]`) — carrying
  no `oracle_amendment_reason`, or an empty one, fails `E_ORACLE_AMENDMENT_UNREASONED` and emits
  neither `oracle_amended` nor a second `oracle_frozen`.
- **AC-8b (`[bd8:10b]`, resume control).** Re-invoking the oracle phase with an IDENTICAL ctx and
  run_id, so the phase sentinel serves the cached success (`phase_sentinel_resumed` present, no
  new `phase_artifacts`), leaves the log unchanged: exit 0, no second `oracle_frozen`, no
  `oracle_amended`, no `E_ORACLE_AMENDMENT_UNREASONED`. TWO legs, because one confounds the
  variables: (i) the tree unchanged, and (ii) a MEMBER MUTATED directly between the two identical
  invocations — which does not touch `org_config` and so leaves the phase key identical. Leg (ii)
  is what forces the rule: the resume is still a no-op (exit 0, no amendment, one freeze) and the
  mutation surfaces at the IMPLEMENTING phase's verify as `E_ORACLE_MUTATED`, not at the resume as
  `E_ORACLE_AMENDMENT_UNREASONED`. With leg (i) alone, "resume" and "nothing changed" coincide and
  a GREEN that merely compares digests — never detecting a resume at all — satisfies AC-8, AC-8a
  and AC-8b together.
- **AC-9 (`[bd8:4]`).** A member unreadable at freeze time → `E_ORACLE_INDETERMINATE`, and NO
  `oracle_frozen` event is emitted (a freeze that half-happened is worse than none).
- **AC-9a (G1) — STRUCK under D1(a), and the strike is deliberate.** It required a
  `phase_artifacts` payload carrying `written_truncated: true` to fail the freeze. That payload is
  no longer the set source (`[bd8:1]`), so the AC has no subject: a truncated cross-check cannot
  make a freeze lie about a membership it never took from there. G1's concern survives in
  `[bd8:4a]`'s third bullet — a truncated payload is copied into `written_crosscheck` as the
  truncation MARKER, never as a sampled list, so the log never implies the engine saw a membership
  it did not. Nothing else in G1 is reachable, and an AC kept for continuity's sake would be a
  test that measures a rule the spec no longer has.
- **AC-9b (`[bd8:2b]`, scope limits — the limits in §9 are asserted, not declared).** After a
  freeze: (i) adding a file inside a SUBdirectory of a scope directory does NOT trip the verify,
  and (ii) creating a new empty subdirectory in a scope directory does NOT trip it. Together they
  pin non-recursion and files-only. A limit no test measures is a claim, not a limit.
- **AC-10 (`[bd8:1]`/`[bd8:1a]`, RE-CUT under D1(a) — the namespace boundary).** v1-v4 forced
  "the set is not a directory listing". Under `[bd8:1]` a directory listing is now the REQUIRED
  behaviour, so that forcing target is dead and this AC changes subject rather than being struck:
  the freeze must not reach OUTSIDE this run's namespace. The listing is bounded twice, and both
  bounds are forced:
  * **Forcing leg 1 — not outside the document directory.** A file placed in `scratchpad_dir`
    but OUTSIDE `<scratchpad_dir>/specs` at freeze time is not a member and is not in the scope.
    A GREEN that walks the scratchpad root fails here.
  * **Forcing leg 2 — not outside the namespace.** A file placed in a SIBLING run's scratchpad
    (`<scratchpad_root>/<other-run>/specs/...`) is not a member. A GREEN that walks UP out of the
    given `scratchpad_dir` — or that globs the shared scratchpad root — fails here. This is the
    leg that keeps "a glob" from meaning "an arbitrary glob": the listing is tied to the one
    namespace the driver scoped to this run (`[bd8:1a]`, lib/project_root.py:24-31).
  * **Control leg — the run's OWN document directory freezes normally**, with exactly the members
    that are in it. Without this, "does not go outside the namespace" is satisfied by a freeze
    that returns nothing at all.
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
  Second control leg (`[bd8:6b]`): a logless ORACLE-phase invocation is likewise untouched — exit 0,
  no `oracle_frozen`, no §5 code — so the fail-closed rule is scoped to the phase that consumes the
  freeze, not to the phase that produces it.
- **AC-16 (`[bd8:4a]`, RE-CUT under D1(a)).** An oracle phase whose `<scratchpad_dir>/specs` is
  absent, unreadable, or contains no regular file fails `E_ORACLE_INDETERMINATE` and emits no
  `oracle_frozen`; the same for an oracle-phase run carrying no `scratchpad_dir`. Control leg: a
  directory holding at least one regular file freezes normally, so the AC is not satisfiable by
  refusing every freeze. The v4 form of this AC — keyed on `phase_artifacts.written` being empty
  or `not-observed` — is STRUCK: `[bd8:2a]` measured that payload reporting exactly that on a
  healthy run, so the v4 form refused every real build.

- **AC-17 (`[bd8:6a]`, §5 — a verify-side I/O fault is a refusal, never a crash).** Two legs, and
  both assert the PARSED `error_code`, never a stdout substring:
  * (i) A frozen member that still exists but cannot be read at verify time (mode 000, or replaced
    by a directory) → `E_ORACLE_INDETERMINATE`, exit non-zero. §5 already says "freeze OR verify";
    AC-9 covers only the freeze.
  * (ii) A scope directory that no longer exists at verify time (the last member of `specs/`
    removed together with the directory) → `E_ORACLE_MUTATED` with `mutated:removed`, exit 1, per
    `[bd8:2b]`.
  Neither may surface as `E_RUNNER`. The natural implementation of `[bd8:2b]` — `iterdir()` over
  the scope and `read_bytes()` over the members — raises `FileNotFoundError` / `PermissionError` /
  `IsADirectoryError`, which `run.py:233-240` maps to `E_RUNNER` and rc 2, below
  `governor_record_result` (`run.py:207-223`). An adversary who mutates a member AND makes it
  unreadable would otherwise convert a BD-L1 refusal into a runner crash, which §9 may not claim
  ADV-1 over.

## 7. Spec gaps — resolved before freeze

- **G1 — RESOLVED in v3, then SUPERSEDED by G9 in v5.** `phase_artifacts` truncates its `written`
  list when the serialised line exceeds the log's per-line limit, replacing it with
  `written_truncated: true` + `written_count` + `written_digest` and a binary-searched sample
  (engine.py:1319-1336). v3's rule — a freeze over such a payload fails
  `E_ORACLE_INDETERMINATE` — was correct while `written` WAS the membership source. Under D1(a)
  it is not, so `written_truncated` no longer gates the freeze at all (`[bd8:4a]`, third bullet)
  and **AC-9a is STRUCK** (§6). What survives is a recording rule, not a gate: a truncated payload
  is copied into `written_crosscheck` as the truncation MARKER and never as the sample
  (`[bd8:1b]`), so the log never implies the engine saw a membership it did not.
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
- **G8 (found at gate) — SUPERSEDED by G9.** Its rule (an unobserved/empty written set is
  `E_ORACLE_INDETERMINATE`) was correct in form and wrong in subject; see `[bd8:4a]` and AC-16.
- **G9 (found at gate, round 2) — the set source. RESOLVED by D1(a): `[bd8:1]` re-cut onto the
  tree.** `phase_artifacts.written` is the git delta of `org_config["git_cwd"]`
  (engine.py:493 as the sole producer, with the `worker_written_paths` manifest at :497-508 only
  NARROWING it and DEFERring when absent; engine.py:1146-1167 for the delta itself), while both
  spec workflows write their documents under `org_config["scratchpad_dir"]`
  (phase_45_spec.py:372-376,1005-1007; phase_45_spec_lite.py:373-374). Under both measured drivers
  those are different trees (`run_oss_driver.py:113,185`) or a gitignored subtree
  (`driver-template.sh` + `.gitignore`). MEASURED on this engine and pasted into `[bd8:2a]`:
  `written: []` with `write_tracking: "git-delta"` while both documents exist in the scratchpad.
  The `<run_id>/` sub-namespace initially proposed for the re-cut was REJECTED by measurement —
  `_resolve_scratchpad` returns `scratchpad_dir` verbatim and `run_oss_driver.py:185` interposes
  no run component — so the namespace is `scratchpad_dir` itself, which
  lib/project_root.py:24-31 documents as already run-scoped (`[bd8:1a]`). The proper long-term
  fix (the `worker_written_paths` manifest as a SOURCE rather than a filter, which would restore
  the §1g argument honestly) is an `engine.py` change, out of scope per §8, and is tracked as
  bd#36. This lot does not wait on it.
- **G10 — the entry verify's observable effect.** RESOLVED by AC-2's witness assertion.
- **G11 — the logless oracle phase.** RESOLVED by `[bd8:6b]`: skipped silently, because failing it
  closed breaks `build-cli.ts:40` on every spec phase and buys nothing AC-15 does not already buy.

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
`test_engine_path_closure.py` (a new module in the import closure is exactly what it measures),
`test_bd22_contracts.py` (the package's no-I/O-at-import invariant, which `[bd8:5]` binds), and
`test_gh1067_ignored_dir_exclusion.py` (its `test_ac5_real_error_codes_check_exits_0_ok` is the
INDEPENDENT enforcement of `error_codes.py --check == 0` on the real tree, and therefore the
corroboration for AC-11's `[bd8:11]` half — AC-11 cannot reach its own `--check` assertion
pre-GREEN, because the membership half fails first).
Seven files reference the conformance package. §1a applies: the sibling audit runs
`--require-clean` before and after, and a scoped pass is not the ship gate — the full-suite
delta against a declared baseline is (this is BD-L2's R2.6 applied to this lot's own delivery).

## 9. Level claim

On GREEN this lot licenses the claim **BD-L1 over ADV-1 and ADV-2**, and only because both are
executed in the form CL §4 defines them — the adversary acts DURING the implementation phase
(AC-2b, AC-3b), not merely between the two invocations (AC-2, AC-3). An entry-only verify would
license neither, and saying so is the §8 discipline this document is bound by.

Scoped, in the terms the levels require:

- **R1.1, R1.3, R1.4 — enforced.** AC-1, AC-1a, AC-2..AC-6, AC-9, AC-9b, AC-10, AC-16, AC-17.
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
  non-recursive listing of REGULAR FILES in the directories containing frozen members
  (`[bd8:2b]`); a file added in a subdirectory of a scope directory is NOT detected, and a
  subdirectory appearing beside the members is NOT an addition. Both directions are asserted
  (AC-9b), so the limit is measured rather than declared. (ib) The oracle set is the scratchpad document
  directory, NOT the engine's recorded write set: `phase_artifacts.written` does not contain the
  spec documents on any measured driver (`[bd8:2a]`, G9), so BD-L1 here binds what the phase
  PRODUCED in its own namespace rather than what the engine OBSERVED. bd#36 is the lot that would
  make those the same fact. (ii) Editing the review document during
  implementation is `E_ORACLE_MUTATED`, deliberately (`[bd8:2a]`). (iii) An implementing-phase
  invocation with no `--event-log` fails closed (AC-15); a caller that wants BD-L1 must supply a
  log.

It licenses nothing about oracle quality (BD-L2) and nothing about authorship attestation beyond
what bd#10 already measured.
