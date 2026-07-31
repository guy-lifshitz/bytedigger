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
  FROZEN v7 (post-gate round 4). The gate rejected v6 on ONE finding, type (b): E26's `[bd8:1a]`
  half was applied and its §9 half was not, so the frontmatter and `[bd8:1a]` both pointed at a
  §9 limit (ic) that did not exist while §9 (ib) still carried the struck "in its own namespace"
  clause. v7 applies E30-E35: (ic) is published with the measured producer disagreement and its
  two consequences (a reused scratchpad freezes documents this run did not author; a concurrent
  build writing the same specs/ is indistinguishable from ADV-2); AC-17's exception mapping is
  corrected (FileNotFoundError lands on E_FILE_NOT_FOUND at run.py:230-232, not E_RUNNER); §3's
  freeze payload enumeration completed; `[bd8:12]`'s numeral fixed; plus two optional
  clarifications — §9 records that the lookup order and log integrity are host/availability
  properties, not level properties, and `[bd8:1b]` states the truncation carve-out inline. The
  gate's round-4 sweep found NO GREEN that passes the 31 failing tests while falsifying §9's
  claim. HISTORY: v6 (post-gate round 3). Rounds 1-3 of the Opus gate rejected v3 (9 blocking), v4 (7) and
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
source.** The freeze records `written_crosscheck: [...]` copied VERBATIM — except that a
payload carrying `written_truncated: true` contributes its truncation MARKER — from the oracle phase's
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
Freeze emits `oracle_frozen {phase, run_id, member_count, digest, members:[{path, digest}],
scope, scope_digest, written_crosscheck}` — the last three per `[bd8:2b]` and `[bd8:1b]`, and all
of them asserted by AC-1; verify reads the LAST `oracle_frozen`-or-`oracle_amended` event **in the event log this
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
  Neither may surface as a runner-level code. The natural implementation of `[bd8:2b]` —
  `iterdir()` over the scope and `read_bytes()` over the members — raises `FileNotFoundError`,
  which `run.py:230-232` maps to `E_FILE_NOT_FOUND` and rc 2, or `PermissionError` /
  `IsADirectoryError`, which `run.py:233-240` maps to `E_RUNNER` and rc 2. Both land BELOW
  `governor_record_result` (`run.py:207-223`), so the restart governor, `--status` and
  `derive_state` see a runner failure and never an oracle refusal. An adversary who mutates a
  member AND makes it
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
30 test files under `engine_py/tests/` reference `run.py`; three are load-bearing for this change
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
  PRODUCED in the namespace the caller scoped rather than what the engine OBSERVED. bd#36 is the
  lot that would make those the same fact. (ic) That namespace is only as narrow as the CALLER made
  it, and the producers were measured to DISAGREE: `dogfood/run_oss_driver.py:185` supplies one
  `<artifacts_dir>/scratchpad` with no run component, `SYSTEM/cli/build/build-cli.ts:30` passes the
  caller's `--scratchpad-dir` string through unchanged, and
  `SYSTEM/cli/build/batch-build-regressions.test.sh:205` pins a repo-rooted `<repo>/.hal-build`
  (`[bd8:1a]`). `scratchpad_dir` is therefore build-scoped at best and may be repo-scoped, and two
  consequences are DECLARED rather than claimed away: a scratchpad reused across builds freezes
  documents THIS run did not author, and a CONCURRENT build writing into the same
  `<scratchpad_dir>/specs` during this build's implementing phase is byte-for-byte
  indistinguishable from ADV-2 and will raise `E_ORACLE_MUTATED` on a legitimate build. "What this
  phase produced" and "the contents of this namespace" are the same fact exactly when the caller
  scoped the scratchpad per build; where it did not, this limit is what BD-L1 does not cover. (ii) Editing the review document during
  implementation is `E_ORACLE_MUTATED`, deliberately (`[bd8:2a]`). (iii) An implementing-phase
  invocation with no `--event-log` fails closed (AC-15); a caller that wants BD-L1 must supply a
  log.

- **Not BD-L1 properties, stated so they are not read into the claim (CL §5).** `[bd8:9]`'s
  lookup ORDER and the integrity of the event log itself are availability and host properties, not
  level properties: a log shared by two builds can cost a legitimate build an
  `E_ORACLE_UNFROZEN`, and an actor who can write the log can FORGE an `oracle_frozen` matching a
  mutated tree — a strictly stronger capability than mutating the oracle, which `[bd8:8]`'s
  single-store design does not defend against and this level does not claim to.

It licenses nothing about oracle quality (BD-L2) and nothing about authorship attestation beyond
what bd#10 already measured.

---
---

# ⎯⎯ SECOND FROZEN SPEC IN THIS FILE ⎯⎯

**`engine_py/conformance/oracle.py` is shared by two lots**, so this file carries **two independent
frozen specs**. They collided by **file name only** — the symbol sets are disjoint and the modules
complement each other rather than compete.

| | Lot | Subject | Symbols |
|---|---|---|---|
| **§ above** | **bd#8** (BD-L1) | oracle freeze-and-verify | `OracleRefusal`, `is_oracle_workflow`, `is_implementing_workflow`, `doc_dir`, `list_members`, `member_digest`, `compute_digest`, `compute_scope`, `compute_scope_digest`, `crosscheck_from_payload`, `build_freeze_payload`, `build_amendment_payload`, event constants |
| **§ below** | **bd#38** (L3, child of bd#27) | three-state outcome + guarded evaluation | `OracleOutcome`, `Oracle` (Protocol), `evaluate_guarded` |

Neither spec is amended by the other, and neither document was edited to accommodate the merge:
bd#8's text above is byte-identical to what it carried on `main`, and bd#38's below is byte-identical
to what it carried on its branch. **Nothing was dropped from either side.**

Note for both lots: bd#38's `AC-E9` parses this module with `ast` and asserts it imports `signal` in
no form. That assertion now covers **bd#8's code as well** — re-measured on the merged file, not
inherited. bd#8 imports `hashlib`, `json`, `pathlib`, `typing` and `__future__`; the assertion holds.

# bd#27 (L3) — Oracle plugin interface + guarded evaluation

FROZEN spec for lot **bd#27**. Carries exactly **15 ACs**: `AC-O1`..`AC-O5` (the three-state
outcome type) and `AC-E1`..`AC-E10` (`evaluate_guarded`). Scope is one new module,
`engine_py/conformance/oracle.py`.

Process: manual Option-D — frozen spec → RED → gate (`hal-gate-agent`, Opus) → GREEN.
**GREEN does not start before an ACCEPTED verdict.**

---

## 0. Provenance, and what re-resolving the citation found

AC text is preserved verbatim on branch **`lot-bd7`**, `engine_py/conformance/SPEC.md`. That branch
is the carrier and **must not be deleted**.

**Citation re-resolved on this base** (`git show lot-bd7:engine_py/conformance/SPEC.md`, 1456 lines):

| Anchor | Carrier line |
|---|---|
| `## 2. Oracle plugin interface — engine_py/conformance/oracle.py` | 244 |
| `### 2.1 Three states, unmergeable by type` | 246 |
| `AC-O1` … `AC-O5` | 255 … 268 |
| `### 2.2 freeze` (**not this lot** — `AC-F1..F14`) | 269 |
| `### 2.3 evaluate and the indeterminate guard` | 332 |
| `AC-E1` … `AC-E10` | 338 … 353 |
| `AC-E10`'s **resolved normative form** (`[G8:2]`) | **395–400** |
| `[G7:self-1]` withdrawal of the `ThreadPoolExecutor` aside | 402–416 |
| §2 ends (`## 3.` begins) | 421 |

**Finding — the inherited citation range is short.** The lot brief and issue #27 cite
"§2 … lines 244–380". Line 380 lands **inside** `[G8:2]`'s preamble, one line into the two-defect
list. The **resolved normative form of `AC-E10`** — the single sentence this lot is instructed not to
re-derive — lives at **395–400**, and the measured `[G7:self-1]` withdrawal that makes a default
`ThreadPoolExecutor` inadmissible lives at **402–416**. Both fall outside the cited range. Reading to
the §2/§3 boundary (421) rather than to the cited endpoint is what recovered them. Recorded because
"measurements and line citations are base-relative and do not transfer" cuts both ways: the range was
not merely offset, it was **truncated before the operative clause**.

Nothing else in §2 is in scope. In particular **`AC-F1`..`AC-F14` (carrier 269–331, module-level
`freeze()`) are NOT this lot** — they are named here only so their absence is not later read as a gap.

---

## 1. Base, host, and measurement

Measured on the executing host at this lot's base, **before** RED.

- Base: `lot-27` @ `08b8413`, branched from `origin/main`. Working tree clean.
- Host: macOS 26.5.1, 18 cores. CPython **3.14.6**.
- Suite invocation, taken from `.github/workflows/ci.yml:103` (job `defaults.run.working-directory:
  engine_py`): `python -m pytest tests/ -q -p no:cacheprovider --timeout=120`.
- Installed pytest plugins on this host: **`pytest-xdist`, `pytest-timeout`** only.

**`pytest-randomly` is NOT installed on this base.** The carrier spec reasons repeatedly about "the
suite's default `pytest-randomly`" (carrier 386–387) and about `-p no:randomly`. That premise is
base-relative and **does not transfer to this host**. It does not relax anything: `AC-E10`'s resolved
form is order-independent by construction, which is the property that matters, and this lot asserts
order-independence rather than inheriting it from a plugin's absence.

### 1.1 Contention control (hal#1353)

Checked before and after the baseline run, matched on command **prefix**: no `Runner.Worker`, no
competing `pytest` / `python -m pytest` process. Recorded in §7.

### 1.2 Baseline

Recorded in §7 with the post-run contention re-check.

**Drift invariant.** Ship in **0 failed**, and the drift property is *"identical to this host's own
`main` at `extra_bd == 0`"* — never a literal count. `engine_py/conformance/` is bd-native and is
**not** in `core_manifest.json` (precedent `lib/run_allowlist.py`), so HAL drift stays at baseline.
`conformance*` is **already** in `[tool.setuptools.packages.find] include`
(`engine_py/pyproject.toml:104`, shipped by L2) — **this lot changes no packaging.**

### 1.3 Thread-spawn audit — re-verified on THIS base, not inherited

L1's gate flagged that `AC-E10`'s assertion quantifies over the **process**, so a foreign non-daemon
thread owned by pytest or another fixture would false-fail it. L1 audited `engine_py` and found none
live. **That conclusion was re-derived here rather than inherited**, and it reproduces:

| Site (this base) | Thread | Daemon |
|---|---|---|
| `llm_subprocess.py:609` | `llm-straggler-watchdog` | `daemon=True` |
| `llm_subprocess.py:2515` | `llm-stream-feeder` | `daemon=True` |
| `llm_subprocess.py:2518` | `llm-stream-reader` | `daemon=True` |
| `lib/reference_backends/pydantic_anthropic.py:174` | anonymous | `daemon=True` |
| `lib/reference_backends/pydantic_openai.py:616` | anonymous | `daemon=True` |
| `workflows/phase_6_review.py:2675` | `ThreadPoolExecutor(max_workers=n)` | non-daemon, but **context-managed** — `with` calls `shutdown(wait=True)`, so no worker survives the block |

No thread-spawning site in `engine_py/tests/conftest.py`, `engine_py/tests/helpers/`, or the repo-root
`conftest.py`.

**`pytest-timeout` measured, not assumed.** `--timeout=120` is in the CI command, and `pytest-timeout`
spawns a `threading.Timer` under its `thread` method. On this host SIGALRM is available, so it selects
the **signal** method and spawns nothing. Probed directly under the exact CI flags: alive non-main
threads `[]`, non-daemon `[]`.

**Declared limitation (measured hazard, deliberately not designed around).** `pytest-xdist` is
installed. This lot's suite invocation does not use `-n`, and neither does CI. `AC-E10`'s
process-quantified assertion is scoped to that invocation. Running the suite under `-n` is out of
scope for this lot's verdict.

**Self-inflicted instance of the same hazard, closed by construction:** `AC-E9` runs its assertions on
a helper thread. That helper MUST be `daemon=True` **and** joined before its test returns, or it
becomes exactly the foreign non-daemon thread that false-fails `AC-E10`. Pinned in `AC-E9`'s fixture
below.

---

## 2. Frozen interface

```python
# engine_py/conformance/oracle.py

class OracleOutcome(Enum):
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    INDETERMINATE = "indeterminate"

class Oracle(Protocol):
    def freeze(self, paths: Iterable[Path], *, root: Path) -> str: ...
    def evaluate(self, state) -> OracleOutcome: ...

def evaluate_guarded(
    oracle: Oracle, state, *, timeout_s: float | None = None
) -> tuple[OracleOutcome, str | None]: ...
```

The `Oracle` Protocol is declared because `evaluate_guarded` is typed against it. **No AC in this lot
covers the Protocol itself**, and the module-level `freeze()` function of carrier §2.2 is **not
implemented here** (`AC-F1..F14`, another lot).

**Feasibility pre-checked before freezing** (CPython 3.14.6, this host). `AC-O1`..`AC-O5` are
simultaneously satisfiable with `Enum` internals intact — a spec that cannot be implemented is a
Class B defect, so this was verified rather than assumed:

| Probe | Result |
|---|---|
| `bool(I)` | `TypeError` ✓ |
| `I == True` / `True == I` / `I != True` | `TypeError` ✓ (both operand orders) |
| `I == REJECTED` / `I == I` | `False` / `True` ✓ (comparison still works) |
| `I == object()` / `I == "indeterminate"` | `False` ✓ (not over-broad) |
| `len({R,A,I})` / `{R: 1}[R]` | `3` / `1` ✓ (hashable; `__hash__` must be restored explicitly) |
| `OracleOutcome(True)` / `(1)` | `ValueError` ✓ |
| `OracleOutcome("rejected")` | member ✓ (constructor still functional) |
| `{c.__name__ for c in __mro__}` | `{"OracleOutcome","Enum","object"}` ✓ |
| `json.dumps(I)` | `TypeError` ✓ |

Note the trap this surfaced: defining `__eq__` sets `__hash__ = None`, which breaks `Enum` set/dict
use. GREEN must restore it; the RED asserts set and dict-key usage so the trap cannot ship silently.

---

## 3. The 15 acceptance criteria

AC text in **bold quote** is verbatim from the carrier. Everything after it is this lot's fixture
design and its `[G22:13]` kill analysis.

### 3.1 `OracleOutcome` — three states unmergeable by type

#### AC-O1
> **`bool(outcome)` MUST raise `TypeError`.** Enum members are truthy by default, so `if outcome:`
> would silently read INDETERMINATE as accepted.

**Fixture.** For **each of the three members** (per-member quantifier — a `__bool__` defined only on
the member the author was thinking about is the standing "each individual member omitted" candidate):
`pytest.raises(TypeError)` around `bool(m)`, and around an `if m:` truthiness context.

**Vacuity guard.** `pytest.raises(TypeError)` alone is vacuous if the object never constructed. Paired
with `isinstance(m, OracleOutcome)` and `m.value == <literal>` asserted **first**, so the guarded path
is proven entered on a real member.

#### AC-O2
> **`outcome == True` / `outcome == False` MUST raise `TypeError`.** Equality against `bool` is the
> second collapse path.

**Fixture.** Three members × {`True`, `False`} × **both operand orders** (`m == True` and `True == m`)
— the standing "operand bound to the wrong neighbour" / "both directions" candidate. Plus `m != True`.

**Positive control, and it is the load-bearing half.** An implementation that raises `TypeError` for
*every* operand satisfies the AC's letter and destroys the type. So, asserted alongside:
`m == m` is `True`; `m == other_member` is `False`; `m == object()` is `False`; `m == "rejected"` is
`False`; `{R, A, I}` has 3 elements; `{R: 1}[R] == 1`. The collection is non-uniform by construction —
bool operands raise, non-bool operands compare normally.

#### AC-O3
> **No constructor from a boolean exists: `OracleOutcome` MUST NOT expose `from_bool`, and
> `OracleOutcome(True)` MUST raise `ValueError`.**

**Fixture.** (a) The named absence: `from_bool` not an attribute. (b) "and friends" made measurable
rather than rhetorical — a structural sweep: no attribute of `OracleOutcome` whose name contains
`bool` case-insensitively (the standing "case-folding where verbatim text is pinned" candidate, applied
in the direction that widens the net). (c) `OracleOutcome(True)` and `OracleOutcome(False)` raise
`ValueError`; also `OracleOutcome(1)` and `OracleOutcome(0)`, since `True == 1`.

**Positive control.** `OracleOutcome("rejected") is OracleOutcome.REJECTED` — without it, "the
constructor raises for everything" passes vacuously.

#### AC-O4
> **The three members are distinct and `INDETERMINATE is not REJECTED`, `INDETERMINATE is not
> ACCEPTED`.**

**Fixture.** All three pairwise `is not`; `len(OracleOutcome) == 3`; and whole-collection equality on
both names and values: `{m.name for m in OracleOutcome} == {"REJECTED","ACCEPTED","INDETERMINATE"}`,
`{m.value for m in OracleOutcome} == {"rejected","accepted","indeterminate"}` (exact, lowercase —
case-folding candidate).

**Kill.** Enum **aliasing** is the live candidate: `INDETERMINATE = "rejected"` makes the two members
the *same object*, silently. `len == 3` plus pairwise identity plus the value-set equality kill it.

#### AC-O5
> **`OracleOutcome` MUST NOT use a mixin base: its `__mro__` MUST contain no type other than
> `OracleOutcome`, `Enum` and `object`.** A `str`/`int` mixin satisfies AC-O1..O4 while
> `json.dumps(outcome)` re-emits a bare truthy scalar.

**Fixture.** Set-equality exactly as mandated:
`{c.__name__ for c in OracleOutcome.__mro__} == {"OracleOutcome", "Enum", "object"}`.
This is exhaustive whole-collection equality and admits no `any`/`all`/`first`/`last` reduction, so it
carries no `[G7:4]` quantifier obligation (`[G8:1]` clause (b)).

**Declared corollary assertion.** The AC's *rationale* names the process boundary, so it is asserted:
`json.dumps(OracleOutcome.INDETERMINATE)` raises `TypeError`. This is a **live** assertion, not
decoration — it is exactly what a `str` mixin flips (a `str`-mixin member serialises to `"rejected"`).
Declared here so the gate can rule on it rather than discover it.

**Kills.** `(str, Enum)` → mro gains `str`. `(int, Enum)` / `IntEnum` → gains `int`. `StrEnum` → gains
`str` and `ReprEnum`. A plain non-Enum class with three constants → mro is `{OracleOutcome, object}`.
All four differ from the pinned set.

### 3.2 `evaluate_guarded`

#### AC-E1
> **An oracle raising any `Exception` MUST yield `INDETERMINATE`, never `REJECTED`.**

**Fixture.** A **non-uniform** set of raising doubles — `ValueError`, `RuntimeError`,
`ZeroDivisionError`, and a lot-local `Exception` subclass — each asserted `outcome is INDETERMINATE`,
`outcome is not REJECTED`, `outcome is not ACCEPTED`, reason non-empty (AC-E5 per-path).

**Kills.** `except ValueError:` only, or any single-type catch → the other doubles escape as errors.
Mapping exception → `REJECTED` → the `is not REJECTED` assert. The "any" quantifier is over exception
*types*, and four dissimilar types is the non-uniform collection.

#### AC-E2
> **An oracle raising `ImportError`/`SyntaxError` (load error) MUST yield `INDETERMINATE`.**

**Fixture.** Two doubles, one each, asserted separately (not one representative — "each individual
member of a defaulted set omitted"). Same four assertions as AC-E1.

#### AC-E3
> **A timeout MUST yield `INDETERMINATE`.**

**Fixture.** A slow double sleeping well beyond a small finite `timeout_s`. Asserts `INDETERMINATE`,
`is not REJECTED`, `is not ACCEPTED`, reason non-empty.

**Added live assertion — wall-clock bound.** `evaluate_guarded` must **return** in materially less
than the oracle's sleep. Without it, an implementation that joins the worker unconditionally satisfies
every outcome assertion while defeating the entire point of a timeout, and nothing in the AC set
notices. This assertion can fail (a joining implementation fails it), so it is not decoration. Bound
measured on this host, §7.

#### AC-E4
> **An oracle returning something that is not an `OracleOutcome` (including `True`/`False`) MUST yield
> `INDETERMINATE`** — an adapter that returns a bool has not implemented the interface, and coercing it
> would reintroduce the collapse.

**Fixture — the enumeration is the point.** Doubles returning, each asserted independently:
`True`, `False`, `None`, `0`, `1`, `"rejected"`, `"accepted"`, an object exposing `.value ==
"rejected"` and `.name == "REJECTED"`, and a **foreign `Enum`** with identical member names and values.

**Kills.** Truthiness coercion (`True`→ACCEPTED, `False`→REJECTED) — killed by `True`/`False`.
`OracleOutcome(r)` value-coercion — killed by `"rejected"`, which such an implementation would happily
convert into a real `REJECTED`. Duck-typing on `.value`/`.name` — killed by the lookalike object and
the foreign `Enum`. A `type(r) is OracleOutcome` check and an `isinstance` check are both admissible
and both survive.

#### AC-E5
> **The reason string MUST be non-empty whenever the outcome is `INDETERMINATE`.**

**Fixture.** Asserted **in every INDETERMINATE-producing path** — exception (AC-E1), load error
(AC-E2), timeout (AC-E3), non-outcome return (AC-E4) — as `isinstance(reason, str)` and
`reason.strip() != ""`. Asserting it once would be the standing "first-only" reduction; the collection
here is *the set of paths*, and it is non-uniform because a plausible implementation sets a reason on
the exception path (where an exception message is at hand) and leaves `""` or `None` on the timeout
and non-outcome paths, where it must be authored.

**Declared limitation — presence-only survives, deliberately.** A constant reason (`"x"`) satisfies
this AC. "Presence-only" is on the standing candidate list, and the kill would be to require the
reasons of the four paths to be mutually distinguishable. **This lot does not add that requirement**:
the AC text is frozen verbatim and `[G22:13]` is explicit that *rewriting the requirement does not
close the gap*. Recorded as a known-admitted implementation, not as an oversight, so the gate rules on
a stated position rather than finding an unstated hole.

#### AC-E6
> **A clean `REJECTED`/`ACCEPTED` passes through unchanged with reason `None`.**

**Fixture.** **Both** members (non-uniform — one member only is "first-only"), `timeout_s=None`:
returned outcome `is` the exact member, and `reason is None` (identity, not falsiness — `""` is a
distinct and plausible wrong answer).

**Kill worth naming.** An implementation that over-reads AC-E1's "never `REJECTED`" as "never *return*
`REJECTED`" collapses the clean-rejection path to `INDETERMINATE`. Only the `REJECTED` half of this
fixture catches it — which is why both members are required, and why an `ACCEPTED`-only fixture would
have been the defect.

#### AC-E7
> **`evaluate_guarded` MUST NOT catch `KeyboardInterrupt`/`SystemExit`.**

**Fixture.** Two doubles, one raising each, asserted to propagate out of `evaluate_guarded`.

**Asserted under BOTH `timeout_s=None` and a finite `timeout_s`.** This is the kill that a
single-path fixture misses. `KeyboardInterrupt`/`SystemExit` derive from `BaseException`, not
`Exception`, so they do not propagate out of a worker thread on their own — an implementation that
runs inline when `timeout_s is None` and threaded otherwise passes an `timeout_s=None`-only fixture
while **silently swallowing** the interrupt on the guarded path, which is the path the AC is about.
Both parameterisations are required; GREEN must re-raise non-`Exception` `BaseException`s in the
caller's thread.

**Vacuity guard (mandatory here).** `pytest.raises(KeyboardInterrupt)` is vacuous if the fixture would
raise regardless. Each double **records into a caller-visible list immediately before raising**, and
the test asserts that record is present — evidence the guarded path was *entered*.

#### AC-E8
> **Positive control for the timeout branch.** A *fast* oracle called with a *finite* `timeout_s` MUST
> return its real outcome with reason `None`. Without this, `if timeout_s is not None: return
> INDETERMINATE` satisfies AC-E3 and every other AC — R2.1's "a timeout MUST NOT count as rejection"
> would be satisfied by never reaching a verdict at all.

**Fixture.** Fast doubles returning **both** `ACCEPTED` and `REJECTED`, each with a finite `timeout_s`:
outcome `is` the exact member, `reason is None`. Both members, because a
`timeout_s is not None → ACCEPTED` implementation passes an `ACCEPTED`-only positive control.

#### AC-E9
> **The timeout mechanism MUST NOT be `signal`-based: `evaluate_guarded` MUST behave identically
> (AC-E3 and AC-E8 both hold) when called from a non-main thread.**

**Fixture — normative half.** Re-run AC-E3's timeout case and AC-E8's fast-finite case **from a
non-main thread**; both must hold identically. Assertion failures on the helper thread are captured
and **re-raised on the main thread** (a bare `assert` in a thread is otherwise invisible to pytest —
an assertion that cannot fail).

**Harness discipline — the helper thread is itself the hazard.** The helper MUST be `daemon=True` and
**joined** before the test returns. A non-daemon, unjoined helper is precisely the foreign non-daemon
thread that false-fails `AC-E10` (§1.3), and it would be self-inflicted.

**Fixture — seam half, and the seam is pinned three ways.** Naming `signal` is not enough:
- **attribute path** — `signal.setitimer`, `signal.alarm`, `signal.signal`;
- **moment of resolution** — a monkeypatched `signal.<name>` is only reached if the implementation
  resolves the attribute **at call time** (`import signal; signal.alarm(...)`). A
  `from signal import alarm` binds at **import time** and slips straight past the patch;
- **normalisation** — none is relied upon, deliberately.

Because the resolution-moment hole cannot be closed by patching alone, the decisive assertion is
**source-level and exact**: an AST parse of `oracle.py` asserting it imports `signal` in no form —
neither `import signal` nor `from signal import ...`. That admits no resolution-moment or
normalisation ambiguity, and it can fail (a signal-based GREEN fails it).

**Record and delegate, never substitute.** Where `signal` *is* wrapped to observe calls, the wrapper
**calls through** to the real function. `pytest-timeout` runs the suite on the signal mechanism
(§1.3); replacing `signal.signal` with a raiser or a no-op would sabotage a primitive this harness
itself runs on — bd#22's session-killing defect. Observation only.

#### AC-E10
> **The abandoned oracle MUST NOT be able to hang shutdown, and MUST NOT accumulate.**
>
> **Normative form, discharging both** (carrier 395–400): after the grace period, **every alive
> non-main thread MUST have `daemon is True`.** The oracle's own recorded
> `threading.current_thread()` remains the liveness anchor (it is provably alive — its sleep outlasts
> the grace), but the daemon predicate is quantified over **all** alive non-main threads, not over
> that one. No thread counting.

This form is **inherited, not re-derived.** It is the settled output of four rounds, each of whose
Class B defects was introduced by the previous round's fix: v6 unmeasurable (grace > sleep, so
*no reaping logic at all* passed); `[G7:self-1]` self-contradictory (clause 1 required daemon workers
while the prose blessed a stdlib `ThreadPoolExecutor`, whose workers are `daemon=False` and which
`concurrent.futures.thread._python_exit` **joins** — a measured 6.77 s shutdown delay); `[G7:1]`
false-fail (demanded a *newly created* thread, which a pool reusing an idle worker never produces);
`[G8:2]` false-fail again (counted **threads**, so a cold pool spawning exactly N made `N < N` false,
and it passed under fixed order only because earlier tests left idle workers).

**Fixture.**
1. An oracle double that, as the **first statements of its body**, appends
   `threading.current_thread()` to a caller-visible holder and **sets a `threading.Event`** — then
   sleeps **longer than the grace period**, so the worker is guaranteed alive at check time.
2. `evaluate_guarded(..., timeout_s=<small>)` → `INDETERMINATE` (and reason non-empty).
3. **`event.wait(timeout=<generous>)` and assert it was set, before touching the holder.** This closes
   the inherited thread-startup race: `evaluate_guarded` returns on the timeout, and a thread start
   slower than the timeout under load would otherwise leave the holder empty and `assert holder` would
   fail for a correct GREEN. `assert holder` alone is order- and load-dependent — the exact defect
   class that broke this AC twice.
4. Sleep the grace period.
5. Assert the anchor thread `is_alive()` — the liveness anchor, provably alive because its sleep
   outlasts the grace.
6. Assert `offenders == []`, where `offenders` is **every alive non-main thread whose `daemon` is not
   `True`**. The failure message lists offender names, so a future foreign-thread false-fail is
   diagnosable on sight rather than by re-derivation.

**No counting anywhere.** Carrier clause 2 (accumulation by thread count) is **replaced**, not
dropped, by `[G8:2]`. Stated explicitly so the gate does not read its absence as a coverage gap.

**Quantifier obligation, discharged — and it is what makes the assertion non-vacuous.** The collection
is *alive non-main threads*; the §4.3 row requires a non-uniform fixture (daemon worker + non-daemon
auxiliary) and an all-daemon positive control. Step 6 is the positive control. The **non-uniform**
member is discharged by a companion test that spawns a deliberate non-daemon thread and asserts the
offender helper **reports it** (then joins it before returning). Without that companion, `offenders ==
[]` is an assertion with no demonstrated ability to fail — invisible in the pass/fail count and
misclassifiable as an inherited gap, which is exactly the failure mode the coverage-diff rule names
for **added** assertions.

**Order-independence.** No step reads a baseline captured before another test, counts threads, or
diffs `threading.enumerate()` for a newly created thread. The verdict depends only on threads alive
*at that moment*, and §1.3 establishes the process contains no non-daemon foreign thread.

---

## 4. `[G22:13]` simulation table — plausible implementations, and what kills each

The deliverable. A requirement describes what must hold; a fixture must exclude what an implementer
might plausibly do **instead**. Different enumerations.

### 4.1 `OracleOutcome`

| # | Plausible implementation | Killed by |
|---|---|---|
| 1 | Plain `Enum`, no `__bool__` | AC-O1 (members are truthy by default) |
| 2 | `__bool__` on one member / only `INDETERMINATE` | AC-O1, asserted per member |
| 3 | `__bool__` raises, `__eq__` untouched | AC-O2 (`m == True` returns `False` instead of raising) |
| 4 | `__eq__` raises `TypeError` for **every** operand | AC-O2 positive control (`m == m`, `m == object()`) |
| 5 | `__eq__` overridden, `__hash__` left `None` (the default consequence) | AC-O2 set/dict-key controls |
| 6 | `__eq__` handles `True` but not `False`, or not the reflected order | AC-O2 (3 × 2 operands × 2 orders) |
| 7 | `from_bool` classmethod provided "for adapters" | AC-O3 (a) |
| 8 | Same, renamed `fromBool` / `from_boolean` / `of_bool` | AC-O3 (b) case-insensitive `bool` sweep |
| 9 | `_missing_` maps `True`→`ACCEPTED` | AC-O3 (c) `OracleOutcome(True)` must raise `ValueError` |
| 10 | Constructor hardened to raise for everything | AC-O3 positive control `OracleOutcome("rejected")` |
| 11 | `INDETERMINATE` aliased to an existing value | AC-O4 `len == 3` + pairwise `is not` + value-set equality |
| 12 | Uppercase / mixed-case values | AC-O4 exact lowercase value-set equality |
| 13 | `class OracleOutcome(str, Enum)` | AC-O5 mro set equality; corollary `json.dumps` |
| 14 | `IntEnum` / `(int, Enum)` | AC-O5 mro set equality |
| 15 | `StrEnum` (3.11+) | AC-O5 mro set equality (gains `str`, `ReprEnum`) |
| 16 | Not an `Enum` at all — a class with three constants | AC-O5 mro set equality (`{OracleOutcome, object}`) |

### 4.2 `evaluate_guarded`

| # | Plausible implementation | Killed by |
|---|---|---|
| 17 | `except ValueError` / any single exception type | AC-E1's four dissimilar types |
| 18 | Exception → `REJECTED` ("it failed, so it's a fail") | AC-E1 `is not REJECTED` |
| 19 | Handles `ImportError`, lets `SyntaxError` through | AC-E2 asserted per type |
| 20 | Coerces a truthy/falsey return (`True`→`ACCEPTED`) | AC-E4 `True`/`False` |
| 21 | Coerces via `OracleOutcome(r)` | AC-E4 `"rejected"` / `"accepted"` |
| 22 | Duck-types on `.value` / `.name` | AC-E4 lookalike object + foreign `Enum` |
| 23 | Treats `None` as "no verdict → `ACCEPTED`" | AC-E4 `None` |
| 24 | Reason authored only on the exception path | AC-E5 asserted in all four paths |
| 25 | Reason `""` / `None` where `INDETERMINATE` | AC-E5 `strip() != ""` |
| 26 | Reason `None` replaced by `""` on the clean path | AC-E6 `reason is None` (identity, not falsiness) |
| 27 | Clean `REJECTED` collapsed to `INDETERMINATE` (over-reading AC-E1) | AC-E6 `REJECTED` half |
| 28 | `except BaseException` | AC-E7 both doubles |
| 29 | Inline when `timeout_s is None`, threaded otherwise, thread path swallows `BaseException` | AC-E7 **asserted under a finite `timeout_s` too** |
| 30 | `if timeout_s is not None: return INDETERMINATE` | AC-E8 |
| 31 | `timeout_s is not None → ACCEPTED` | AC-E8 **both** members |
| 32 | `signal.alarm` / `SIGALRM` timeout | AC-E9 off-main-thread (raises `ValueError`) + AST no-`signal` |
| 33 | Signal-based with the off-thread `ValueError` caught and timeout silently skipped | AC-E9 off-main-thread AC-E3 half (no timeout → real outcome, not `INDETERMINATE`) |
| 34 | `from signal import alarm` (import-time binding, evades a `signal.alarm` monkeypatch) | AC-E9 AST check covers both import forms |
| 35 | Joins the worker unconditionally (no timeout escape) | AC-E3 wall-clock bound |
| 36 | **Concurrency primitive:** per-call **daemon** thread | *admissible — must pass* |
| 37 | **Concurrency primitive:** default `ThreadPoolExecutor` (workers `daemon=False`) | AC-E10 offender list (the anchor is a non-daemon pool worker) |
| 38 | **Concurrency primitive:** daemonising pool (`initializer`/subclass) | *admissible — must pass* |
| 39 | **Concurrency primitive:** raw `_thread.start_new_thread` | *admissible — the resulting `_DummyThread` reports `daemon=True` and does not block interpreter exit; recorded so a later round does not read its survival as a gap* |
| 40 | Daemon worker **plus** a non-daemon watchdog joining the abandoned worker | AC-E10 — quantified over **all** alive non-main threads, not the anchor alone (`[G8:2]` Class A) |
| 41 | No reaping logic whatsoever, worker dies inside the grace | AC-E10 — oracle sleep **exceeds** the grace, so the worker is alive at check time (kills v6's unmeasurable form) |
| 42 | **Reduction:** thread **count** instead of thread **property** | not used by any fixture; the companion offender-helper test pins the property |

### 4.3 Assertions ADDED by this lot, run through the coverage-diff rule

An added assertion that cannot fail produces no deletion in the diff, is invisible in the pass/fail
count, and would misclassify as a pre-existing gap. Each is shown able to fail:

| Added assertion | Can fail because |
|---|---|
| AC-O5 `json.dumps` raises `TypeError` | a `str`-mixin member serialises to `"rejected"` (cand. 13) |
| AC-O2 set / dict-key controls | `__eq__` without `__hash__` makes members unhashable (cand. 5) |
| AC-O3 case-insensitive `bool` attribute sweep | fires on `fromBool` / `of_bool` (cand. 8) |
| AC-E3 wall-clock bound | a joining implementation exceeds it (cand. 35) |
| AC-E7 finite-`timeout_s` parameterisation | the split-path implementation swallows the interrupt (cand. 29) |
| AC-E9 AST no-`signal` check | any `signal`-based implementation (cand. 32, 34) |
| AC-E10 companion offender-helper test | it spawns a real non-daemon thread and requires the predicate to report it — it fails if the predicate is ever narrowed to the recorded anchor alone (`[G8:2]` Class A) or to a thread count. It is **independent of `oracle.py`** and therefore a declared pre-passing shield (§5.1), not a test of the AC |

This lot **modifies or removes no existing fixture** — `oracle.py` and its tests are new — so the
deletion half of the coverage-diff rule has no subject here. Stated rather than omitted.

---

## 5. Pre-passing shields — declared

**Exactly one**, and its declaration is a correction to this spec's first frozen version.

### 5.1 The shield

`TestEvaluateGuardedAbandonedWorker::test_ac_e10_offender_helper_reports_a_non_daemon_thread`
— **passes today, by design and necessarily.**

It never touches the unit under test. It spawns a deliberate non-daemon thread and requires the RED's
own predicate `_non_daemon_alive_non_main_threads()` to report it, then joins the thread. Its subject
is *the fixture's ability to fail*, not `oracle.py`, so no implementation of `oracle.py` can change
its verdict and it cannot fail for the reason the other 36 do.

It is nonetheless required, not decorative: it is the **non-uniform member** discharging AC-E10's
quantifier obligation (§3.2, §4.3 row `[G8:2]`). Without it, `offenders == []` is an added assertion
with no demonstrated ability to fail — invisible in the pass/fail count, producing no diff deletion,
and misclassifiable as an inherited gap.

### 5.2 The correction, recorded rather than quietly fixed

This spec's first frozen version (commit `e80b45d`) stated **"There are none."** That was **false**,
and the RED run falsified it immediately: `36 failed, 1 passed`. The claim was written from the
premise "every AC needs the import, therefore every test needs the import", which does not hold for a
test whose subject is the fixture rather than the AC — precisely the meta-test the same spec had
already mandated two sections earlier. The two sections were written from different premises and
never reconciled.

Recorded in full because *"pre-passing tests MUST be declared as shields; an undeclared one is a
finding"* is a non-negotiable of this lot, and because the defect shape is this lot's own inherited
one: **an audit artifact carrying the defect it audits** (carrier `[G7:2]`/`[G7:4]`, three consecutive
rounds). The count was not measured before being asserted — the same class as the carrier's own
"a property asserted without being measured" (carrier 155–159).

The other **36** tests fail today, all at `ModuleNotFoundError: No module named 'conformance.oracle'`
— verified uniform, not assumed. This includes the absence-shaped assertions (AC-O3's `from_bool`
sweep, AC-E9's AST check), which still need the import and so cannot pre-pass.

**Deferred-import discipline (§1q), inherited from L2's RED.** Every `conformance.*` symbol is
imported **inside the test body**, never at module level, so **collection stays clean** and the
failures are real test failures rather than a collection error. No stub module is added: a stub would
be a passability surface (`engine_py/stub_passability.py` exists to detect exactly that class), and
the deferred-import idiom achieves clean collection without one.

---

## 6. Declared limitations

1. **AC-E5 admits a constant reason** (§3.2 AC-E4/E5) — the AC text is frozen and is not rewritten.
2. **`-n` / `pytest-xdist` is out of scope** for AC-E10's process-quantified assertion (§1.3).
   Neither this lot's invocation nor CI uses it.
3. **`Oracle` Protocol is uncovered by any AC** — declared for typing only (§2).
4. **`AC-F1..F14` / module-level `freeze()` are not this lot** (§0).

---

## 7. Baseline, contention control, and measured constants

All figures measured on the executing host (macOS 26.5.1, 18 cores, CPython 3.14.6) at this lot's
base `lot-27` @ `08b8413`, **before** RED. They are host- and base-relative and do not transfer.

### 7.1 Baseline

Command (CI's, `.github/workflows/ci.yml:103`), run from `engine_py/`:

```
python3 -m pytest tests/ -q -p no:cacheprovider --timeout=120
```

**`4227 passed, 6 skipped, 0 failed`** in 292.28 s (6 warnings, all pre-existing:
3 × `PytestCollectionWarning` on `lib/plugins/disk_truth/test_runner.py:27`, 3 × `SyntaxWarning` on an
invalid escape in `tests/test_phase_6_FEB64BA8_backtick_strip.py:282`).

Contention control (hal#1353), matched on command **prefix**, both sides of the run:

| | `Runner.Worker` | competing `pytest` / `python -m pytest` |
|---|---|---|
| before | none | none |
| after | none | none |

**Ship bar: 0 failed, and the suite identical to this host's own `main` at `extra_bd == 0`** — the
property, never the literal 4227.

### 7.2 Constants, and why each is safe

| Constant | Value | Measured justification |
|---|---|---|
| `TIMEOUT_S` | `0.1 s` | guarded call returns at **median 0.1077 s, max 0.1103 s** over 20 runs against a 6 s oracle |
| `ORACLE_SLEEP_S` (AC-E10) | `6.0 s` | must exceed the grace; margin **5.0 s** |
| `GRACE_S` (AC-E10) | `1.0 s` | sleep − grace = **5.0 s**, so the worker is provably alive at check time — this is the inverted relation whose v6 form (grace 2.5 s > sleep 2.0 s) let *no reaping logic at all* pass |
| `EVENT_WAIT_S` (AC-E10) | `10.0 s` | thread-start latency measured at **median 0.024 ms, p99 0.036 ms, max 0.053 ms** (200 samples, unloaded). The `Event` is retained regardless: the inherited hazard is a **loaded** host exceeding the 0.1 s timeout, which this unloaded measurement cannot rule out, and 10 s is ~190 000× the measured max |
| AC-E3 wall-clock bound | `2.0 s` | **1.89 s** above the measured max return, **4.0 s** below the oracle's sleep — so it cannot flake, and a joining implementation (cand. 35) fails it by ~4 s |

**Added suite cost of AC-E10:** ≈1.1 s of real waiting (grace + timeout). The abandoned worker lingers
for the remaining ≈5 s as a **daemon** thread, which is precisely the property under test and cannot
delay interpreter exit.

---

**FROZEN.** Committed before RED. Any change after this point is a spec round and is reported as one.
