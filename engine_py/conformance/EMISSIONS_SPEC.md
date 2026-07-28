# Lot spec — bd#18: engine conformance emissions (`phase`, `run_identity`, `phase_artifacts`)

**v1.** Narrow lot split from bd#7 by decision recorded in bd#7. Base: `origin/main` @ `63af51b`.

Scope: **three additive engine emissions. No checker, no attestation, no oracle, no freeze.** bd#8..#10 depend
on exactly these three; every one of bd#7's eight rounds of gate findings lives in the checker and attestation,
which is why this lot exists separately.

## 0. What this lot inherits from bd#7, and what it refuses to inherit

bd#7 was rejected eight times in two classes, and both lessons are load-bearing here.

**Class A — "not measured" published as "passed."** Its sharpest form is `[G6:quant]`, adopted here verbatim as
**§0.1**: any requirement ranging over a collection **where the reduction is an implementation choice** MUST be
asserted with a **non-uniform** collection of ≥2 members — at least one satisfying, at least one violating —
**plus** the all-satisfy positive control. An AC asserted only over a uniform collection is unasserted, whatever
its prose says. bd#7 found this defect four times, one rung higher each round (step→phase, step→phase union,
phase→run, then inside its own audit table). The collections in *this* lot are: **steps of a phase**, **frames of
a phase** (the retry recursion), and **`execute()` calls of an engine instance**. Nothing here ranges over
phases of a run, runs of a log, or events of a scope — those are checker collections and they left with the
checker.

**Class B — a correct GREEN cannot pass.** bd#7 produced four of these, *each introduced by the fix for the
previous one*, all four inside two ACs (`AC-L0-3d3`, `AC-E10`) that carried a fresh Class B defect for four
consecutive rounds. Three concrete prohibitions follow, and they are why this spec is short:

- **§0.2 No exact byte-boundary arithmetic.** bd#7's 4096/4097 faces and shadow-envelope interaction produced
  every one of the four Class B defects: a boundary derived for one identifier pair and reused under identifiers
  22 bytes longer (4 bytes of slack, 18 bytes over), and a "unshadowed" control that was silently shadowed. This
  lot pins truncation **behaviourally** instead (**E3d**), by reading the raw serialised line back off disk. That
  formulation carries no predicted number, so the arithmetic cannot drift out from under it — and it is the only
  one of bd#7's five boundary ACs that never produced a Class B defect. The exact faces stay in bd#7.
- **§0.3 No assertion may depend on test order, thread scheduling, or host state.** bd#7's `[G8:2]` passed only
  because earlier tests happened to leave idle thread-pool workers behind. If an assertion's verdict can change
  with `-p no:randomly` versus the suite's default `pytest-randomly`, it is not a requirement.
- **§0.4 No assertion that cannot fail.** bd#7 shipped `assert sample[sample.index(x)] == x` (a tautology for any
  list), assertions against the test's own fixture rather than the produced artifact, and conditionals that
  no-opped silently. A dead assertion is worse than none: it makes a reader believe the case is covered. Every
  assertion here must state, in its test's docstring, the wrong implementation it kills.

**§0.5 Three standing prohibitions.** No truthiness where a value is specified — a sentinel (`"unknown"`,
`"0.0.0"`, `"0+unknown"`, `"0"*40`) MUST fail, not pass a non-empty check. No present/absent where a behaviour is
specified. Every assertion must distinguish a correct implementation from a plausible wrong one.

**§0.6 Mechanism pins are spec-side, not RED-guesswork** (bd#7's `[G5:seam]`). Where a test must intercept a
seam, this spec names the seam, so the RED pins a stated interface rather than an assumed one and a differently-
but-correctly-implemented GREEN is not false-failed.

## 1. Measured baseline

Bases are **measured on the executing host**, never inherited — bd#7 inherited three numbers from another host
and two did not reproduce. Recorded at RED time, re-measured before ship:

- Full `engine_py` suite on this lot's `main` base: **to be measured on this host before ship.**
- There are **no pre-existing failures to hide a regression behind**: ship in **0 failed**.
- Drift invariant is the **property** "identical to this host's own `main` at `extra_bd == 0`", not a literal
  count — bd#7 learned this when a literal `5/0/0` proved non-portable between hosts.

## 2. Additivity — the constraint that makes this lot safe

Every change is **additive**. No existing payload key changes meaning, type, or presence.

- **AC-N1** `step_started`/`step_finished` retain every key they carry today, unchanged, alongside the new
  `phase`. Asserted by comparing the full key set of a real run's step events against the pre-change key set,
  not by checking that `phase` is present.
- **AC-N2** (**pre-passing at RED time — declared**) `derive_state.py` consumers are untouched: the existing
  state-derivation tests pass unchanged, and a replay over a log carrying the new `phase` key derives the same
  state. This is the regression surface that matters — the engine's event log is consumed, not just written.
  **Declared pre-passing because it must be:** it is an *additivity shield*, and a shield over already-correct
  behaviour necessarily passes before the change. It gains its power at GREEN, where it fails any
  implementation that alters an existing payload key rather than adding beside it. bd#7's discipline applies —
  an **undeclared** pre-passing test is a finding, because a reader cannot distinguish a shield from a test
  that never measured anything; so this is declared here, and it is the **only** pre-passing test in this lot.
- **AC-N3** No new hard dependency. Version resolution uses `importlib.metadata` (stdlib) and a file read; no
  third-party import is added to the engine's import path.

## 3. Emission 1 — `phase` on step events

- **AC-E1** `step_started` (`engine.py:370`) and `step_finished` (`engine.py:472`) payloads MUST carry `phase`,
  the workflow name.
- **AC-E1b** **Quantified over the steps of a phase** (§0.1). A first-step-only or last-step-only
  implementation is the plausible wrong GREEN here, and a single-step fixture cannot see it. Asserted over a
  **multi-step** workflow: **every** `step_started` and **every** `step_finished` carries `phase`, checked per
  event, with a ≥3-step fixture so first/last/any reductions are all distinguishable.
- **AC-E1c** The value MUST equal the workflow name **by equality**, not merely be non-empty (§0.5) — a GREEN
  emitting a constant, the step name, or `""` must fail. Asserted with a workflow whose name is distinct from
  every step name in it.
- **AC-E1d** `phase` MUST be present on the step events of a **retry** frame too: `_execute_steps` is re-entered
  recursively (`engine.py:638-645`) and the outer frame then returns `retry_result` (`:657`) without running its
  own tail, so an implementation that adds `phase` only on the first pass loses it exactly where the flagship
  consumer looks. **Quantified over the frames of a phase** — non-uniform by construction: assert on events from
  both the pre-retry and post-retry frames.

## 4. Emission 2 — `run_identity`

- **AC-E2** A new `run_identity` event MUST be emitted **once per `execute()` call**, as the event immediately
  following **that call's** `workflow_started`.
  - **Located by index of `workflow_started`, not by position 0** (bd#7's `[G:MINOR-10]`):
    `engine.py:250-268` emits `phase_reroute_entry` first whenever `phase_reroute` is set, so `events[0]` is a
    fixture property, not an engine invariant. Asserted **with `phase_reroute` set**, so a position-0
    implementation fails.
  - Payload: `engine_version` and `adapter_identity`.
  - "Once per `execute()`" is the definition, stated because bd#7's "once per run" was a false literal: one
    `run_id` legitimately spans many `execute()` calls, so a log may hold many identities.
- **AC-E2b** `adapter_identity` MUST be a **mapping with non-empty `backend` and `source`** — not a bare string
  (bd#7 `[G2:8]`: a bare-string fixture pinned a weaker contract than the engine emits). Asserted by shape and by
  value against the configured backend, so a constant fails.
- **AC-E2c** **`engine_version` provenance survives packaging, and has no placeholder.** Resolution order:
  `importlib.metadata.version("bytedigger-engine")` **first** (installed-wheel path), then a read of
  `engine_py/pyproject.toml` `[project].version` (source-checkout path).
  - **Seam pins** (§0.6): the metadata half is patched at `importlib.metadata.version`; the source half MUST go
    through `Path(<engine_py>/pyproject.toml).read_text()`, and a test patching it MUST do so
    **path-conditionally**, delegating for every other path — a blanket patch breaks unrelated engine reads.
  - The distribution name MUST come from `package_meta.PACKAGE_DIST_NAME`, **not** a bare literal. Asserted
    against the imported constant, so a rename that updates one and not the other fails. (bd#7 had this
    normative and asserted the literal on both sides, where the assertion could not discriminate.)
  - **No placeholder, ever.** When neither seam resolves, `engine_version` is **absent or empty** and that is
    what is emitted. `"unknown"`, `"0.0.0"`, `"0+unknown"` MUST fail (§0.5).
  - **Failure contract, pinned beside the mechanism** (bd#7 `[G8:MINOR-4]` — pinning only the mechanism traps a
    GREEN catching a different exception family): "does not resolve" covers **any `OSError` subclass**,
    **`KeyError`** (no `[project].version`), and **any parse error** from the reader. None of them may propagate
    out of `execute()`.
  - **MUST NOT be memoised.** Resolution happens per `run_identity` emit and is not cached across runs; an
    `@lru_cache` or module-level `_VERSION = _resolve()` makes results depend on test order (§0.3), and this
    suite runs `pytest-randomly`. Asserted by resolving twice in one process with the seam changed in between.
- **AC-E2d** **Quantified over the `execute()` calls of one engine instance** (§0.1). `execute()` resets per-run
  state at `engine.py:237-243`; a GREEN guarding the emit with an instance flag it forgets to reset emits for
  run 1 only. Asserted with **two `execute()` calls on one engine** under **different** `run_id`s: **each** call's
  `workflow_started` MUST be followed by its own `run_identity`, with correct payloads in both — non-uniform via
  a third call whose seam is changed, so a cached-first-value implementation fails.

## 5. Emission 3 — `phase_artifacts`

- **AC-E3** Exactly **one** `phase_artifacts` per phase, payload keys exactly:
  `phase`, `written`, `read`, `write_tracking`, `read_tracking`.
  - `read_tracking` is `"declared-only"` — this lot adds **no** read instrumentation, and the label says so
    rather than leaving the absence to be read as "nothing was read."
  - Asserted by **exact key-set equality** for the untruncated case, so a GREEN always emitting the truncation
    keys fails.
- **AC-E3a** **Emitted on every exit path**, from the `try/finally` around the step loop (`engine.py:271`) —
  **not** inside `_execute_steps`, which returns early for a zero-step workflow (`:355-361`) and returns
  `retry_result` from the recursion (`:657`). Required on: ok exit, error exit (`:674`), escalate (`:682`), and a
  **step raising** (crash path, driven with `pytest.raises`). Exactly one per phase on each, including the
  zero-step workflow.
- **AC-E3b** **`write_tracking` never overclaims.** `"git-delta"` requires **≥1 step AND a computed delta for
  every step of the phase**; anything else is `"not-observed"`.
  - Differential: `org_config["git_cwd"]` on a real repo → `"git-delta"`; **absent** → `"not-observed"`
    (`engine.py:1062-1065` — `_resolve_scan_cwd` never falls back to ambient cwd, so `git_pre is None` at `:384`);
    a `git_cwd` pointing at a **non-git** directory → `"not-observed"`.
  - **Zero steps ⇒ `"not-observed"`**, because `_execute_steps` returns at `:355-361` **before** `_scan_cwd`
    resolves at `:366`, so the engine scanned nothing. `all([])` is `True`, so the literal "every step" reading
    would publish `"git-delta"` for a phase never looked at — bd#7's `[G4:4]`.
  - **Partial delta failure ⇒ `"not-observed"`**: `_git_changes_vs_head` returns `None` on timeout or missing git
    (`engine.py:1082`), so an `any()`-shaped implementation publishes `"git-delta"` when only *some* step was
    scanned. **Quantified over the steps of a phase**, asserted non-uniformly through the **`lib.git_port`**
    seam (§0.6; `git_port.py:145-157` resolves through `get_git_read()` at call time, so a factory injection
    reaches `engine.py:1076`/`:1079`): step 1's delta succeeds, step 2's fails ⇒ `"not-observed"`, with the
    all-succeed run as the positive control.
  - **A phase whose write channel never ran MUST NOT publish `written: []` alongside `"git-delta"`** — that is
    the affirmative claim "nothing was written" over a channel that never opened (bd#7's `[G2:4]`).
- **AC-E3c** **`written` accumulates over ALL steps of the phase**, as a union.
  - **Quantified over the steps of a phase**, non-uniform: step 1 writes `early.txt`, step 2 writes nothing —
    `written` MUST still contain `early.txt`. A last-step-only implementation yields `written: []` beside
    `"git-delta"`, which is AC-E3b's overclaim through the accumulation seam. Positive control: both steps write.
  - **Accumulation survives the retry recursion** — quantified over the **frames** of a phase: the pre-retry
    step's write MUST still be present after `_execute_steps` is re-entered at `:638-645` and the outer frame
    returns at `:657`.
  - **Reset between sequential `execute()` calls on one engine instance**: run 2's `written` MUST NOT carry run
    1's paths. The accumulator is per-run state (`:237-243`), and this is the defect an instance-level
    accumulator produces.
  - **Concurrency is out of scope and declared, not assumed** (bd#7 `[G7:EDGE-5]`): one `WorkflowEngine`
    instance is **single-threaded with respect to `execute()`**. Declared because the accumulator lives on the
    instance; **re-open criterion:** the first consumer calling `execute()` concurrently on a shared instance.
- **AC-E3d** **The emitted event MUST NEVER exceed the atomic-append limit, and truncation is asserted
  behaviourally** (§0.2). `event_log.py:74` sets `_LINE_LIMIT_BYTES = 4096` and `:116` raises
  `EventLogLineTooLarge` for `len(encoded) > 4096`; `_emit` swallows that at `engine.py:710`, so an oversized
  `phase_artifacts` **vanishes silently** — the failure mode this AC exists to prevent.
  - Asserted by driving a phase that writes **many** files, then reading the **raw serialised line back off
    disk** and requiring `len(raw_line) <= 4096`. No predicted byte count appears anywhere in this AC.
  - **A pathological single path** (one ~4200-character filename, supplied through the `lib.git_port` seam) MUST
    also produce an emitted event within the limit — a count-based bound alone does not survive it, and this is
    the case where a sample-bounding implementation silently loses the record.
  - When the list is elided, the payload carries `written_truncated: true`, `written_count` (the **true** total),
    and `written_digest`. The digest MUST be **recomputable**: `"sha256:"` + SHA-256 over the newline-joined
    **full sorted** relpath list. Asserted **by equality against a locally recomputed digest** — `"sha256:" +
    "0"*64` and any constant MUST fail (§0.5; bd#7's `[G4:5]`, a hash that cannot detect anything). In the
    elided case the digest is the **only** evidence the omitted paths existed.
  - `written_truncated` MUST be absent or falsey when no elision occurred, asserted on a small-list run.
- **AC-E3e** **`written` entries are POSIX relpaths against the scan root**, asserted with a fixture spanning
  **three distinct depths** in one phase (repo root, one level, two levels) — every existing bd#7 fixture wrote at
  a single depth, so a producer special-casing depth was untested. Exact string equality per depth; absolute
  paths, `./`-prefixed paths and OS-separator spellings MUST all fail. The digest is recomputed over the full
  three-depth sorted list, which is what catches a depth-conditional bug for paths the elided sample omits.
- **AC-E3f** **Emission MUST NOT be able to break execution.** Both new emits go through `_emit`
  (`engine.py:696-711`), whose `except` at `:710` swallows logging failures. Asserted with a log whose `append`
  raises unconditionally: `execute()` MUST still complete with its normal status. A direct
  `self._event_log.append(...)` for either new emit would let the exception escape.

## 6. Out of scope

The entire checker (`check_bd_l0`), the attestation writer and its schema, the oracle interface, `freeze`, the
BD-L0 level grant, and every AC defending against forged or arbitrary event lists. All remain in bd#7.

Read instrumentation: `read_tracking` is `"declared-only"` and AC-E3 requires that label to be emitted, so the
absence is published rather than left to be misread. Signing: out of scope, as in bd#7.

**Shadowed runs** (`HAL_ENGINE_SHADOW_EMITS`, `engine.py:699-708`) are out of scope for this lot: the shadow
branch rewrites every event type, and bd#7's attempt to pin its byte interaction produced two of the four Class B
defects. This lot asserts nothing about shadowed emission, and asserts nothing that a shadowed run would change.
Any test here that patches `is_authoritative_execution` MUST undo the patch and MUST assert the absence of
shadow events before measuring — bd#7's `[G8:3]` was exactly a patch left in place with a comment claiming
otherwise.

## 7. Process

Manual Option-D: frozen spec → RED → gate → GREEN. **GREEN does not start before an ACCEPTED verdict.** Measured
per-test counts are recorded at every round; inherited numbers are re-measured, never trusted.
