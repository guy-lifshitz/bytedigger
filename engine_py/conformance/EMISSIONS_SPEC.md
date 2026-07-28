# Lot spec — bd#18: engine conformance emissions (`phase`, `run_identity`, `phase_artifacts`)

**v3.** Narrow lot split from bd#7 by decision recorded in bd#7. Base: `origin/main` @ `a2691f9`.

**Round 2 of this lot's gate: ACCEPTED, 0 blocking** — the first ACCEPTED anywhere in this lot family, after
nine rejections on bd#7 and one here. It confirmed all three round-1 repairs genuine, the reduction table
complete in both orderings, both new engine branches (`engine.py:679`, `:575`) reachable and failing for the
right reason, the two new doubles unable to leak or be defeated, every citation resolving on this base, and —
the thing that had failed five consecutive times in bd#7 — **no Class B defect introduced by the repairs**.

v3 is **not** a rejection response. It takes the accepted round's non-blocking findings *before* GREEN starts,
because four of them govern what GREEN must do and it is cheaper to state them than to discover them in
implementation:
- `[G18r2:MINOR-1]` v2 contained a **contradiction of its own** — AC-E3's exact key set forbids
  `written_truncated` when untruncated, while AC-E3d permitted "absent **or falsey**". A GREEN following the
  latter was false-failed by the former: `[G6:MINOR-2]`'s shape, self-inflicted, one clause apart. Resolved in
  favour of the key set.
- `[G18r2:MINOR-5]` The metadata seam named a **path** but not a **binding time**, so a correct GREEN written
  `from importlib.metadata import version` would be false-failed by five tests. Third instance in this family of
  the same lesson — `mkdtemp`, then `read_text`'s normalisation, now this — and it generalises: **naming a seam
  is not enough; the spec must pin whatever property the interception depends on.**
- **AC-E4..AC-E7 are new**, promoted from the accepted round's edges because each is a requirement on the log
  that **bd#8..#10 will consume**: `run_id` attribution (an invariant two tests already relied on unstated),
  emission **before** `workflow_finished` (a consumer stopping at the terminal event would never see the
  record), the in-flight exception surviving a failing emit in the `finally` (a direct emit there makes the real
  error *disappear* — worse than a status regression, because it is invisible), and neither event being emitted
  for a run that never started.

Round 1 of this lot's own gate: **REJECTED (2 blocking)**, both in the fragile family this lot knowingly
inherited (`written` accumulation and the step quantifier), both verified against the code before acceptance,
both fixed by a fixture change rather than a spec rewrite — `[G18:1]` (the step quantifier excluded `any` and
`first` but not `last`, so a last-delta GREEN passed all 38 tests) and `[G18:3]` (the reset test used two
*different* workflow names, so a phase-keyed accumulator never reset survived). Eight minors and seven edges
resolved alongside; `[G18:MINOR-1]` is a stale-citation defect of my own making, recorded at its site.

**Correction to v1's own §0 history**, since §0 is normative here and inaccurate history in a normative section
is the artifact-drift class this lot family has been bitten by repeatedly: bd#7 was rejected **nine** times, not
eight, and produced **five** Class B defects, not four. v1 said "eight" while its own `AC-E1e` cited a round-9
finding, and said "four" in the same paragraph as "bd#7's five boundary ACs". v1's §0.5 sentinel list also named
a 40-hex git-sha placeholder for a field that does not exist in this lot; the sentinel that matters
here is `"sha256:" + "0"*64`, which AC-E3d names correctly and the RED asserts.

Scope: **three additive engine emissions. No checker, no attestation, no oracle, no freeze.** bd#8..#10 depend
on exactly these three; every one of bd#7's nine rounds of gate findings lives in the checker and attestation,
which is why this lot exists separately.

## 0. What this lot inherits from bd#7, and what it refuses to inherit

bd#7 was rejected nine times in two classes, and both lessons are load-bearing here.

**Class A — "not measured" published as "passed."** Its sharpest form is `[G6:quant]`, adopted here verbatim as
**§0.1**: any requirement ranging over a collection **where the reduction is an implementation choice** MUST be
asserted with a **non-uniform** collection of ≥2 members — at least one satisfying, at least one violating —
**plus** the all-satisfy positive control. An AC asserted only over a uniform collection is unasserted, whatever
its prose says. bd#7 found this defect **five** times: four climbing *upward* one rung per round (step→phase,
step→phase union, phase→run, then inside its own audit table), and once — in round 9 — a rung **downward**, the
two step-event *kinds* asserted only as a merged set. **The ladder has two ends**, and the rule was applied
monotonically upward for four rounds by an author who had declared the ladder's top with a falsification
criterion attached. The collections in *this* lot are: **steps of a phase**, **frames of a phase** (the retry
recursion), **`execute()` calls of an engine instance**, and — downward — the **two step-event kinds** (AC-E1e)
and the **payload fields** (AC-N1, AC-E3). Nothing here ranges over phases of a run, runs of a log, or events of
a scope; those are checker collections and they left with the checker.

This lot's own round 1 then supplied a sixth instance, `[G18:1]`: a fixture non-uniform in *one ordering only*,
which excluded `any` and `first` but not `last`. So the rule needs stating more sharply than "≥2 members, one
violating": **the fixture set must exclude every reduction the implementation could have chosen**, which for an
ordered collection means both orderings, not one.

**Class B — a correct GREEN cannot pass.** bd#7 produced **five** of these, *each introduced by the fix for the
previous one*: four inside two ACs (`AC-L0-3d3`, `AC-E10`), which carried a fresh Class B defect for four
consecutive rounds, plus a fifth in `AC-L0-3d5` — a predicted byte count asserted at a point where the emitted
event necessarily differs, introduced by the very AC added to close the fence's missing face. Three concrete
prohibitions follow, and they are why this spec is short:

- **§0.2 No exact byte-boundary arithmetic.** bd#7's 4096/4097 faces and shadow-envelope interaction produced
  every one of the five Class B defects: a boundary derived for one identifier pair and reused under identifiers
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
`"0.0.0"`, `"0+unknown"`, `"sha256:" + "0"*64`) MUST fail, not pass a non-empty check. No present/absent where a
behaviour is specified. Every assertion must distinguish a correct implementation from a plausible wrong one.
This binds **every** specified value, including ones whose sibling already complies: `adapter_identity.backend`
was pinned by value in v1 and `source` was not (`[G18:MINOR-3]`), and `read`'s value was left unpinned entirely
(`[G18:MINOR-8]`) — the prohibition is per field, not per AC.

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
- **AC-E1e** `[A2]` **The two step-event KINDS are asserted independently, because the ladder has two ends.**
  bd#7's round-9 gate found a rung *below* the collection ladder its rounds 4-9 had been climbing
  (step→phase→run→log→scope, monotonically **upward**): every fixture there stripped `phase` from
  `step_started` **and** `step_finished` **together**, so the two-kind collection was never tested for
  non-uniformity, and a consumer reducing over the merged list with `any`/`first`/`last` passed all 123 tests.
  **This lot is the emitter of both events**, and they are emitted from two genuinely separate code sites —
  `engine.py:370` and `engine.py:472` — so the plausible wrong implementation is the **half-implementation**:
  `phase` added at one site and forgotten at the other. Without this AC the gap is directional and lands on us:
  L8 would learn to *catch* a non-uniform log while L1 remained able to *produce* one, discovered only after
  L1 had landed.
  Normative: `phase` is asserted **kind by kind**, each kind's events collected and required to carry it
  **separately** — never over a merged "step events" list, which is precisely the uniform collection that hid
  this in bd#7. Both wrong implementations (missing at `:370`; missing at `:472`) MUST fail.
  Note the direction: this is a **producer** requirement, so there is no strip-the-key-and-check-the-consumer
  half here — nothing in this lot consumes the log. The §4.3-style row for the *consumer* side of this
  collection belongs to L8.
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
  `[G18:MINOR-3]` **Seam named (§0.6) and `source` given a specified value.** v1 required assertion "by value
  against the configured backend" while naming **no** seam, so the RED had to guess one — it guessed right, but
  §0.6 exists precisely so a RED pins a *stated* interface rather than an assumption. The seam is
  `llm_subprocess._resolve_backend(kwarg, env)` (`llm_subprocess.py:608-630`), the engine's only backend
  selector, which returns exactly a `(backend, source)` pair and reads the environment through
  `config_provider.env_mapping()` (`:327-328`) — a live `_AliasEnviron`, so `monkeypatch.setenv` on
  `HAL_RUNNER_BACKEND` reaches it.
  And `source` MUST be asserted **by value**, not merely non-empty: it belongs to the closed set
  `{"kwarg", "env", "default", "default-fallback"}` that the module's backend-selection path produces —
  `[G18r2:MINOR-6]` `_resolve_backend` itself returns only the first three (`llm_subprocess.py:619-630`);
  `"default-fallback"` is produced at a different site, `llm_subprocess.py:1204`. v2 attributed the whole set to
  that one resolver, which is the kind of inaccuracy in a normative section this lot has already filed against
  itself twice. v1 specified only non-emptiness, so
  `source: "x"` satisfied both spec and test — the truthiness-where-a-value-is-specified defect §0.5 bans, which
  v1 avoided for `backend` and then committed for its sibling field one clause later.
- **AC-E2c** **`engine_version` provenance survives packaging, and has no placeholder.** Resolution order:
  `importlib.metadata.version("bytedigger-engine")` **first** (installed-wheel path), then a read of
  `engine_py/pyproject.toml` `[project].version` (source-checkout path).
  - `[G18r2:MINOR-5]` **The metadata seam's BINDING TIME is pinned, not just its path.** Resolution MUST reach
    `importlib.metadata.version` by **attribute lookup at call time**; a module-level
    `from importlib.metadata import version` is **out of contract**, because it binds at import and no
    `monkeypatch.setattr(importlib_metadata, "version", …)` can reach it — five tests would false-fail a GREEN
    that is otherwise correct. This is bd#7's `[G5:seam]` asymmetry for the third time in this lot family:
    `mkdtemp` (mechanism pinned, binding not), `read_text` (`[G18:MINOR-4]`, mechanism pinned, normalisation
    not), and now this. **Naming a seam is not enough — the spec must pin whatever property the test's
    interception depends on**, which for an attribute patch is late lookup, for a path comparison is
    normalisation, and for a factory is call-time resolution.
  - **Seam pins** (§0.6): the metadata half is patched at `importlib.metadata.version`; the source half MUST go
    through `Path(<engine_py>/pyproject.toml).read_text()`, and a test patching it MUST do so
    **path-conditionally**, delegating for every other path — a blanket patch breaks unrelated engine reads.
    `[G18:MINOR-4]` **The conditional MUST compare RESOLVED paths.** `Path.__eq__` compares normalised strings,
    not filesystem identity, so a `self == target` gate misses a correct GREEN that spells the file
    `Path(__file__).resolve().parent / "pyproject.toml"`, and misses it on any host where a parent of `engine_py`
    is a symlink — the conditional falls through to the real read, the injected version never appears, and a
    correct GREEN is false-failed. Required: `Path(self).resolve() == target.resolve()`. This is bd#7's
    `[G5:seam]` asymmetry one layer down: v1 pinned the *mechanism* (`read_text`) and not the *normalisation*,
    exactly as bd#7 pinned `mkdtemp` and not its binding time.
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

- **AC-E3** Exactly **one** `phase_artifacts` per phase. Payload keys **in the untruncated case** are exactly
  `phase`, `written`, `read`, `write_tracking`, `read_tracking` — `[G18r3:MINOR-2]` the qualifier is in this head
  sentence deliberately, because v3 stated the key set unconditionally here and added three truncation keys in
  AC-E3d two hundred lines away. That is the *shape* `[G18r2:MINOR-1]` was filed against — two clauses demanding
  different things for one payload — even though the substance was already consistent. A reader of this sentence
  alone must not be able to conclude the wrong thing.
  - `read_tracking` is `"declared-only"` — this lot adds **no** read instrumentation, and the label says so
    rather than leaving the absence to be read as "nothing was read."
  - `[G18:MINOR-8]` **`read` MUST be `[]`, asserted by value.** v1 required the *key* in the exact key set and
    specified nothing about its value, so a GREEN emitting `read: ["anything"]` satisfied both spec and suite.
    AC-E3b's anti-overclaim rule — no affirmative claim over a channel that never opened — applies with **more**
    force here than to `written`, because this channel provably never opened: there is no read instrumentation in
    this lot at all (§6), which is the very thing `read_tracking: "declared-only"` announces. An unasserted `read`
    lets us publish a list of files we claim to have read without ever having looked.
  - Asserted by **exact key-set equality** for the untruncated case, so a GREEN always emitting the truncation
    keys fails.
- **AC-E3a** **Emitted on every exit path**, from the `try/finally` around the step loop (`engine.py:271`) —
  **not** inside `_execute_steps`, which returns early for a zero-step workflow (`:355-361`) and returns
  `retry_result` from the recursion (`:657`). Required on: ok exit, error exit (`:674`), escalate (`:682`), and a
  **step raising** (crash path, driven with `pytest.raises`). Exactly one per phase on each, including the
  zero-step workflow.
  `[G18:EDGE-1/2]` **Two further terminal branches, both enumerated because both unwind from INSIDE the
  recursion** — the place a phase-level accumulator double-counts, and the reason the emit belongs in
  `execute()`'s `finally` rather than in `_execute_steps`:
  1. **`start_step` beyond range** ⇒ `RuntimeError` at `engine.py:679`, reachable via a retry whose
     `retry_from_step` exceeds the last index. `_execute_steps` **raises** rather than returns, so it behaves
     like the crash path but from inside a recursive frame; "exactly one across two unwinding frames" is the
     non-obvious half and MUST be asserted.
  2. **Same-cycle-retry-cap exit** at `engine.py:575`, which returns from within the retry block after
     `same_cycle_retry_capped`. It is the only exit that leaves mid-retry state.
  Both MUST yield exactly one `phase_artifacts` for the phase.
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
    reaches `engine.py:1076`/`:1079`).
    `[G18:1]` **BOTH orderings are required, and the positive control must span ≥2 steps.** Round 1 asserted
    only the fail-**late** ordering (step 1 succeeds, step 2 fails) with a **one-step** positive control. That
    excludes `any()` but **not** `last`: the wrong GREEN
    `"git-delta" if n_steps >= 1 and last_step_delta is not None else "not-observed"` — one overwritten
    variable instead of an `all` reduction — passed **all 38 tests**, because in the fail-late fixture the
    *last* delta is exactly the missing one. §0.1 says the reduction is an implementation choice and only
    *some* reductions were excluded, so the AC was unasserted for the rest. Required:
    1. **fail-early**: step 1's delta fails, step 2's succeeds ⇒ `"not-observed"`. This is the ordering that
       kills `last`, and it is the one that was missing.
    2. **fail-late**: step 1 succeeds, step 2 fails ⇒ `"not-observed"` (kills `any` and `first`).
    3. **positive control over ≥2 steps**, all deltas computed ⇒ `"git-delta"`. A one-step control cannot be
       an all-satisfy control over a collection, which is what §0.1 requires of it.
    The failure MUST be triggered off **state set inside the failing step's own body**, never off a git-call
    ordinal calibrated on a differently-shaped run — see `[G18:2]`.
  - `[G18:2]` **No boundary calibrated on one run shape may be reused on another.** Round 1 measured the
    git-call count on a **1-step** run and reused it to trigger failure in a **2-step** run. That is safe only
    while the GREEN makes fewer than four `git_read` calls after the last step, so a GREEN taking two full
    `_git_changes_vs_head` snapshots at phase exit is false-failed. It is not a byte prediction, so §0.2 is not
    violated in letter — but it is *boundary-reuse-across-shapes*, the mechanism of bd#7's first Class B
    defect, and it is removable for free. Normative: trigger seam failures off **step-body state** (a flag the
    step sets when it runs), never off a call ordinal.
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
    `[G18:3]` **The two runs MUST be the SAME registered workflow, because a phase-keyed accumulator survives
    two different ones.** Round 1 ran `"wf"` then `"wf2"`, which kills an unkeyed `self._written = set()` but
    **not** `self._written_by_phase: dict[str, set]` left out of the reset at `engine.py:237-243` — under that
    GREEN run 2 reads an empty bucket and the test passes. That shape is actively invited here: the payload is
    keyed by `phase`, and the neighbouring per-run state `self._same_cycle_retries` is itself a keyed dict
    (`:243`, `:540`). Meanwhile the real defect — **re-running the same phase on one engine**, the ordinary
    case, and exactly what AC-E2d already does three times — would report run 1's paths in run 2's `written`,
    an affirmative false claim about which files a run wrote. Required: **one registered workflow, executed
    twice under different `run_id`s**, run 2 writing nothing; run 2's `written` MUST NOT contain run 1's paths.
    This kills the keyed and unkeyed variants together. Also pin `len(phase_artifacts) == 2` for that
    same-phase pair, so "exactly one per phase" is quantified over the `execute()` calls of one instance too.
  - **Concurrency is out of scope and declared, not assumed** (bd#7 `[G7:EDGE-5]`): one `WorkflowEngine`
    instance is **single-threaded with respect to `execute()`**. Declared because the accumulator lives on the
    instance; **re-open criterion:** the first consumer calling `execute()` concurrently on a shared instance.
- **AC-E3d** **The emitted event MUST NEVER exceed the atomic-append limit, and truncation is asserted
  behaviourally** (§0.2). `event_log.py:90` sets `_LINE_LIMIT_BYTES = 4096`, `:132` tests
  `len(encoded) > _LINE_LIMIT_BYTES` and `:133` raises `EventLogLineTooLarge`; `_emit` swallows that at
  `engine.py:710`, so an oversized
  <!-- [G18:MINOR-1] These were cited as :74 and :116 in v1 — correct for bd#7's older base, stale here, where
  they point at an unrelated `except` clause and a parameter default. I re-verified engine.py's citations for
  this base and copied event_log.py's across without doing the same. Citations are base-relative; carrying them
  between lots without re-resolving them is the same class as inheriting a measurement between hosts. -->
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
  - `[G18r2:MINOR-1]` `written_truncated` MUST be **absent** when no elision occurred — **not** "absent or
    falsey", which is what v2 said and which contradicts AC-E3's exact-key-set requirement one clause away. A
    GREEN implementing v2's letter (`written_truncated: false` always) satisfied the small-list test and was
    **false-failed** by the key-set test: two of my own clauses demanding different things for one payload, the
    `[G6:MINOR-2]` shape that trapped bd#7. AC-E3's key set governs; a falsey value is out of contract.
    And when elision *did* occur, `written_truncated` MUST be `True` **by identity**, not merely truthy —
    `written_truncated: "yes"` is out of contract (§0.5, per field).
- **AC-E3e** **`written` entries are POSIX relpaths against the scan root**, asserted with a fixture spanning
  **three distinct depths** in one phase (repo root, one level, two levels) — every existing bd#7 fixture wrote at
  a single depth, so a producer special-casing depth was untested. Exact string equality per depth; absolute
  paths, `./`-prefixed paths and OS-separator spellings MUST all fail. The digest is recomputed over the full
  three-depth sorted list, which is what catches a depth-conditional bug for paths the elided sample omits.
- **AC-E4** `[G18r2:MINOR-2]` **Both new events carry the emitting `execute()` call's `run_id`.** No AC stated
  this, yet two tests already depend on it — and the whole `[G18:3]` reset repair rests on being able to tell
  run 1's `phase_artifacts` from run 2's. A GREEN cannot easily omit it (`_emit(self, event_type, payload,
  run_id)` at `engine.py:686` takes it as a **required positional**), but "hard to get wrong" is not a
  requirement, and an unstated invariant that a test silently relies on is the shape this lot family keeps
  filing. Asserted by value against the `run_id` passed to `execute()`, for both events, on a run whose
  `run_id` is distinctive. Selection of a payload **by** `run_id` in any test MUST report the observed
  `run_id`s on failure rather than raising `StopIteration`.
  `[G18r3:MINOR-3]` **It is the event ENVELOPE's `run_id`, not a payload key** (`event_log.py:124-129` builds
  `{"ts", "run_id", "event_type", "payload"}`). Stated because a GREEN that *additionally* mirrored `run_id` into
  the payload would satisfy this AC while violating AC-E3's exact key set — two of my own ACs disagreeing about
  one emit, which is the defect I have now filed against myself three times in this lot.
- **AC-E5** `[G18r2:EDGE-2]` **`phase_artifacts` is emitted BEFORE `workflow_finished`.** AC-E3a pins it to the
  `try/finally` around `engine.py:271`, which necessarily places it before `workflow_cost_rollup`
  (`:275-281`) and `workflow_finished` (`:289`) — but nothing asserted the ordering, so a GREEN emitting after
  the terminal event passed all 41 tests. **bd#8..#10 are the consumers of this log**, and a stream whose
  terminal event precedes its artifact record is exactly what a consumer assumes away: a reader that stops at
  `workflow_finished` would never see the record. Asserted by index within the scoped run: the
  `phase_artifacts` index MUST be less than the `workflow_finished` index. Required on the ok path and on at
  least one non-ok exit, since the `finally` runs on both.
- **AC-E6** `[G18r2:EDGE-3]` **A failing emit in the `finally` MUST NOT replace or swallow the in-flight
  exception.** AC-E3f covers a raising log on a *succeeding* run; AC-E3a covers a raising *step* with a working
  log. Nothing combined them, so nothing forbade the worst composition: on the crash path, a GREEN that emits
  **directly** in the `finally` — bypassing `_emit`'s `except` at `engine.py:710` — substitutes its own logging
  exception for the step's original one, and the real error **disappears**. That is strictly worse than
  AC-E3f's status regression, because a status regression is visible and a swapped exception is not. Asserted
  by driving a raising step **with** a log whose `append` raises for `phase_artifacts`, and requiring the
  **original** exception type and message to propagate (`pytest.raises` matching the step's own error, not the
  log's).
- **AC-E7** `[G18r2:EDGE-4]` **Neither new event is emitted for a run that never started.** `execute()` raises
  `KeyError` at `engine.py:233` for an unregistered workflow, **before** `workflow_started` at `:269`. A GREEN
  that resolves and emits `run_identity` ahead of the registration check publishes an identity with no
  `workflow_started` and no `phase_artifacts` — a log shape for which AC-E2's "immediately following that
  call's `workflow_started`" cannot hold, and which any consumer indexing from `workflow_started` will mis-scope.
  Asserted: after a `KeyError` from an unregistered name, the log contains **no** `run_identity` and **no**
  `phase_artifacts`.
- **AC-E3f** **Emission MUST NOT be able to break execution.** Both new emits go through `_emit`
  (`engine.py:696-711`), whose `except` at `:710` swallows logging failures. Asserted with a log whose `append`
  raises unconditionally: `execute()` MUST still complete with its normal status. A direct
  `self._event_log.append(...)` for either new emit would let the exception escape.
  `[G18:EDGE-5]` **The unconditional-raise double is too blunt on its own and MUST be joined by a targeted
  one.** A double with `path = None` short-circuits the `workflow_cost_rollup` block (`engine.py:275-281`) and
  the dispatcher/stuck-report paths (`:302-325`), so it proves swallowing only for a run whose neighbouring
  emits were skipped entirely. Required additionally: a **real** `EventLog` whose `append` raises **only** for
  the two new event types, leaving every existing emit working. That is the run that actually resembles
  production, and the only one that shows the new emits are swallowed *in situ* rather than in a stripped-down
  execution.

## 5.1 Constraints on GREEN carried from the accepted round's edges `[G18r3]`

These are **normative constraints on the implementation** whose **tests are deliberately deferred** to a
post-GREEN hardening round. I am recording that split explicitly rather than quietly: at GREEN time these two
requirements are **stated but unasserted**, so nobody may read "all 46 tests pass" as covering them. The reason
for deferring is that both need a *new composed fixture*, and composing two individually-working doubles is the
exact shape that produced all five of bd#7's Class B defects — I will not add an unaudited composition to the
RED on the way into GREEN. They get their own short RED round and their own gate, after GREEN is green.

- **`[G18r3:EDGE-1]` The `finally` body MUST NOT be able to raise, not merely MUST NOT re-raise.** AC-E6 covers a
  failing `append`, which `_emit`'s `except` at `engine.py:710` absorbs. It does **not** cover a `finally` that
  raises *before* reaching the emit — e.g. an unguarded phase-level `_git_changes_vs_head(_scan_cwd)` computed
  inside the `finally`, which reaches subprocess-backed git through `git_port` (`engine.py:1076`/`:1079`) and can
  fail on timeout or a missing binary. That exception is raised **outside** `_emit`'s protection, so it replaces
  the in-flight `_CrashError` and the original error disappears — precisely the invisible-failure mode AC-E6
  exists to prevent, arriving by the one door AC-E6 does not watch. **Normative for GREEN: everything the
  `finally` needs must either be already computed before the `try`, or be computed inside a guard of its own. No
  unguarded work in the `finally`.**
  **Re-open criterion / deferred test:** the same `_CrashError`-propagation assertion as AC-E6, on a crash-path
  run with the `git_port` factory injected to fail.
- **`[G18r3:EDGE-4]` The crash path MUST NOT overclaim the write channel.** With a raising step, `git_pre` is taken
  at `engine.py:384` but the step raises at `:421` before the post-snapshot at `:435`, so **no per-step delta is
  ever computed**. The correct emission is therefore `written: []` with `write_tracking: "not-observed"` — and
  AC-E3b's anti-overclaim rule already demands exactly that. But every AC-E3b differential fixture is
  non-raising, so the crash path is the one place where a GREEN publishing `written: []` beside `"git-delta"`
  would pass: `[G2:4]`'s overclaim shape on the single path that never tests for it. **Normative for GREEN: the
  crash path emits `"not-observed"`.**
  **Re-open criterion / deferred test:** assert `write_tracking == "not-observed"` and `written == []` on the
  existing crash-path fixture — cheap, and needs no new composition, which is why it should land first in the
  hardening round.

Also recorded, not deferred because nothing is owed: `[G18r3:EDGE-2]` a second never-started path exists (an
exception out of the reroute block at `engine.py:250-267`, which precedes `workflow_started`), so AC-E7's claim is
about a class of which it asserts one member; and `[G18r3:EDGE-3]` AC-E5's ordering has no counterpart on
exception exits, because `workflow_finished` is never emitted there — correct rather than missing.

`[G18r3:MINOR-1]` **A note on why AC-E6 and AC-E7 read as they do.** Both ACs, as worded in v3, would be
satisfied by a RED that passes *pre-GREEN*: `pytest.raises(_CrashError)` alone is vacuous because the step's
exception already propagates today, and AC-E7's negative assertions are trivially true before the emissions
exist. The RED supplies the missing halves — the observed-attempt assertion and the same-engine positive control
— so nothing is unasserted now. Normative, so a later round cannot "simplify" them back to the vacuous form:
**AC-E6 additionally requires that the attempted emit be OBSERVED (the double records the attempt), and AC-E7
additionally requires a positive control on the same engine and log.** Same family as `[G18r2:MINOR-5]`: the
load-bearing property lived in the RED and not in the spec.

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
