---
voice-profile: technical-en
lot: bd#10
children: [bd#28 (checker + attestation report, ex-§7), bd#29 (in-session model-pin flip, ex-§5)]
class: SYSTEMATIC
chokepoint: >
  llm_subprocess._dispatch_backend (llm_subprocess.py:971) — the single call site through which
  every model invocation on every registered backend passes. R3.1, R3.2 and R3.4 record there;
  R3.3 and R3.6 adjudicate the adapter's return there. An emission or check placed inside one
  backend is out of contract: it leaves every other adapter unmeasured, which is the 1-of-9
  gradient the parent document exists to reverse.
status: FROZEN v4
base: origin/main @ dc6f0d0
---

> **v2 (pre-gate, post-RED).** Not a rejection response — no gate has run yet. v2 takes the seven
> gaps the RED agent **refused to guess** (G1–G7, recorded in the RED file under `SPEC GAPS`) plus
> one the author found (`[bd10:4]`), because six of the eight are cases where v1 said something a
> GREEN could not implement or could implement two ways. Two of them — `[bd10:5]` and `[bd10:6]` —
> are **two of my own clauses demanding different things for one payload**, which is the
> `[G18r2:MINOR-1]` shape this lot family has now filed against itself four times. Finding them
> before the gate rather than during it is the entire reason the RED agent is instructed to stop
> and report instead of inventing. AC count rises 26 → 27 (AC-P8); §9's split criterion is unchanged. (v1 said "23"; see `[bd10:21]`.)
>
> **v3 (pre-gate, post-RED-realignment).** Three further gaps the RED refused to guess (G8–G10) and
> one it raised against its own test (`[bd10:11]`). `[bd10:12]` is the significant one and it is
> **my own §0.1 violation, inside the clause written to prevent it**: v2's AC-C3 demanded a fixture
> pair "whose only difference is absent-versus-empty" that cannot produce two verdicts, because
> nothing recorded which of the two occurred. That, and G9's homeless "report marks R3.3
> not-checked", had one root cause — the payload recorded what the adapter *reported* and never
> whether it *could observe*. One key (`observed_tools`) and one aggregation rule (`[bd10:13]`)
> close both. AC count unchanged at 27; the payload key set is now nine.
>
> **v4 (post-gate round 1, REJECTED — 5 blocking).** Every finding verified against the code by the
> orchestrator before being actioned; none taken on the gate's word. The gate's own one-line
> diagnosis is accurate and worth keeping at the top of this document: **the lot measured what it
> built and attested what it had not measured.** `[bd10:17]` (B-1) the checker could not return
> `failed` — an attestation oracle that cannot reject, inverting CL §1's P2; `[bd10:18]` (B-2) the
> export-surface heuristic false-failed a correct GREEN, and repairing B-1 would have detonated it;
> `[bd10:20]` (B-3) the supersession of 220E5F63 was declared and unreachable; `[bd10:19]` (B-4)
> R3.2 and R3.5 were attested at full width while measured at one-of-eight and at self-declaration;
> `[bd10:16]` (B-5) AC-I5 would have relocated a live production prompt — found independently by
> the author and the gate.
> B-5 forced the one real design change: **attribution is now separated from assembly**, because
> CL:98 asks that blocks *carry* an identifier and hash, not that the engine concatenate them.
> AC count 27 → 32 (AC-I6, AC-I7, AC-A4, AC-A5, AC-M6), then → **26** after the two cuts the
> dispatcher approved on that evidence: §7 → bd#28, §5's flip → bd#29. See §9 for the full table.

# Lot spec — bd#10: BD-L3, attested authorship and inputs (R3.1–R3.6; ADV-7, ADV-8, ADV-10)

`[bd10:21]` **Accounting correction, recorded rather than quietly fixed.** v1 through v3 stated "23"
then "24" ACs. The real counts, obtained by enumerating the AC identifiers in this document, are
**26** at v1 (P1-P7=7, I1-I5=5, M1-M5=5, C1-C6=6, A1-A3=3), **27** at v2 and v3, **32** at v4
before the cuts and **26** after them.
The error originated in v1's own header and was propagated by me into the RED agent's brief, the
round-1 gate's brief, and two issue comments. **No coverage was lost** — both agents enumerated the
ACs by identifier and worked the real set, and the gate's `[G22:13]` table covers all of them — so
this is an accounting defect, not a measurement one. It is recorded because `CONTRACTS_SPEC.md`
makes convergent AC accounting a checkable property of a lot spec ("accounting stays convergent at
7 = 6 here + 1 there"), and a count that is wrong for four consecutive revisions is exactly the
artifact-drift class this family keeps filing against itself. Counted mechanically from now on.

Step 4/4 of the BD-conformance order. Governing frozen text:
`SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md` @ HAL `fd35e1304`
(hereafter **CL**), §9 item 4, with §8's deferrals binding.

Base: `origin/main` @ `dc6f0d0` (bd#22 / PR #26 merged: `conformance/` package, shared
contracts, packaging).

## 0. Discipline inherited, and the three input rules that are new here

§0.1–§0.6 of `EMISSIONS_SPEC.md` and §0.1–§0.8 of `CONTRACTS_SPEC.md` bind this lot verbatim and
are not restated. What follows is only what is **new** or **sharpened** for bd#10.

### 0.1 `[hal#1373]` A one-sided assertion is indistinguishable from a vacuum

Input requirement, not derived here. Three subtypes, all of which this lot's surface actively
invites:

1. **Negative predicate.** `assert "E_MODEL_PIN_MISMATCH" not in …`, `assert result.status != "error"`
   — true before the implementation exists and true after, for opposite reasons.
2. **A fixture that does not force the branch to execute.** A model-pin test whose adapter is never
   dispatched measures the dispatch guard, not the pin comparison.
3. **An expected value taken from the artifact under test.** `assert event["prompt_sha256"] ==
   sha256(event["prompt"])` grades the emitter against itself. This lot is *full* of hashes, and
   this is the single easiest way to ship a green vacuum here.

**Normative and mechanical: every mutation must have TWO MEASURED OUTCOMES ON ONE FIXTURE.**
For each assertion, the spec (and the RED's docstring) must name a mutation of the implementation,
and the RED must show that on the *same* fixture the assertion yields a different verdict before
and after that mutation. One fixture, two measurements. A mutation that only changes the verdict
when the fixture also changes has not been measured — it has been argued.

### 0.2 A pinned constant MUST have an EXTERNAL source of truth

Input requirement. A test that pins a value against a constant this lot also authors verifies
nothing but its own copy-paste. Every pinned constant in the RED cites either a **live fixture** or
a **real file with `file:line` provenance** outside the artifact under test. For this lot:

| Pinned constant | External source |
|---|---|
| `E_MODEL_PIN_MISMATCH` | CL:99 @ `fd35e1304` (R3.3) |
| `E_INJECT_UNATTRIBUTED` | CL:98 @ `fd35e1304` (R3.2) |
| `E_CAPABILITY_ESCAPE` | CL:102 @ `fd35e1304` (R3.6) |
| requirement ids `R3.1`…`R3.6` | CL:97–102 @ `fd35e1304` |
| a real declared capability set | `workflows/phase_2_explore.py:372` on this base |
| the tool-head form with an operand | `"Bash(graphify-shim.sh:*)"`, same line |

`error_codes.ERROR_CODES` is **not** an external source for the three new codes — this lot writes
those entries, so pinning against them is subtype (3) of §0.1.

### 0.3 Mutation testing is destructive; GREEN is committed FIRST

Input requirement. The mutation procedure edits production source and reverts with `git checkout`,
which reverts the implementation together with the mutation whenever the implementation is
uncommitted. **GREEN is committed before the first mutation is applied.** Commit early for a second
reason: a branch with zero commits over `main` is reaped by a neighbouring worker.

### 0.4 Naming collision, pinned before it bites

`llm_subprocess.py:1391` already binds a local named `effective_prompt`, meaning *the prompt with
`stable_prefix` removed and hoisted into `--append-system-prompt`*. **CL's "effective prompt" is the
opposite**: the complete assembled text after injection, prefix included. This spec always means
CL's sense, and AC-P3 exists solely because hashing the `llm_subprocess` local would satisfy every
loosely-worded form of R3.1 while omitting the system-prompt bytes.

## 1. Measured baseline — TWO corpora, named

They diverge, so a delta reported without naming its corpus is incomplete. Measured on the
executing host, in an isolated worktree, at `dc6f0d0`, one at a time (no concurrent suite):

| Corpus | Definition | Measured at `dc6f0d0` |
|---|---|---|
| **pytest** | executing host's dev interpreter (`/opt/homebrew/bin/python3`, 3.14), `dbos` **present**; `python3 -m pytest tests/ -q -p no:cacheprovider --timeout=180` from `engine_py/` | **4189 passed, 6 skipped, 0 failed** (273.35 s) |
| **suite** | clean `venv` with `pip install -e "engine_py[test]"` only, `dbos` **absent** (mirrors `scripts/clean-room/readme-quickstart.manifest:21` and `.github/workflows/ci.yml:103`) | **4178 passed, 7 skipped, 0 failed** (266.84 s) |

`[bd10:1]` **The numbers carried in the lot brief (`pytest` 4172/13, `suite` 4167/18) did NOT
reproduce on this host**, and neither did the size of the divergence between them. Recorded, not
reconciled: this is the third occurrence in this lot family of an inherited measurement failing to
transfer (`EMISSIONS_SPEC.md:100-108`), and the rule stands — bases are measured, never inherited.
The drift invariant is the **property** "identical to this host's own `main` for that corpus,
`extra_bd == 0`", never a literal count.

`[bd10:1a]` **The divergence is fully attributed, because an unexplained one would make every later
delta unreadable.** Measured by diffing `--collect-only` between the two corpora: the *entire*
difference is `tests/test_gh792_native_sentinel_emit.py`, whose `pytest.importorskip("dbos")`
(that file, line 34) skips the module at collection when `dbos` is absent. Corpus *pytest* collects
its **11** tests; corpus *suite* collects none of them and reports one module-level skip instead —
4195 collected / 6 skipped versus 4184 collected + 1 module skip / 7 skipped. The brief's cause was
right and its magnitude was not: the gap is **11 tests, not 5**. No other test differs between the
corpora, so any future delta outside that one file is real in both.

Ship in **0 failed on both corpora**.

## 2. The chokepoint and the API surface, pinned here and not in the RED

`[G22:1]`'s lesson binds: where this spec describes a shape instead of pinning it, the RED invents
it and the invention becomes the interface. Everything GREEN must provide is named here.

### 2.1 Why `_dispatch_backend` and nowhere else

`_dispatch_backend` (`llm_subprocess.py:971`) is reached by every backend, built-in and registered,
via `_BACKENDS[resolved_backend]` read at call time (`:1000`, `:1014`). Two properties make it the
only defensible site, and both are load-bearing collections under `§0.1`:

- **It has two branches** — `stable_prefix` truthy (`:999`) and falsy (`:1014`). An implementation
  that records in one branch is uniform-fixture-invisible. **AC-P4.**
- **It is called twice for one `invoke_llm_subprocess` call** on the GH1169 one-shot fallback path
  (`:1279` then `:1306`). "Once per model invocation" (CL R3.1) means once per **dispatch**, not
  once per call. **AC-P5.**

### 2.2 New module — `conformance/attest.py`

| Export | Form |
|---|---|
| `EVENT_TYPE = "model_invocation_attested"` | `str` |
| `hash_text(text: str) -> str` | returns `"sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()` |
| `InjectedBlock` | frozen dataclass, exactly `source_id: str`, `content: str` |
| `assemble(prompt: str, blocks: Sequence[InjectedBlock]) -> str` | see AC-I1 |
| `capability_escapes(observed_tools, declared) -> tuple[str, ...]` | see AC-C3; returns **sorted**, deduplicated (`[bd10:6]`, G7) |
| `REQUIREMENT_LABELS` | `[bd10:23]` immutable mapping, requirement id → honesty qualifier; see AC-P7 |

`[bd10:23]` **The honesty labels live in the SEAM, not in the report, and that is what lets them
survive §7's cut.** They were B-4's repair — the lot was attesting R3.2 and R3.5 at full width while
measuring them at one-of-eight and at backend self-declaration — and B-4 is a defect of *what the
engine may claim*, which is this lot's business. bd#28's checker **reads** this mapping; it does not
author it. Exact contents pinned by AC-P7, so a lot that narrows a requirement without adding its
label fails a test rather than shipping a quiet overclaim.

### 2.3 `conformance/bd_l3.py` — MOVED TO bd#28

The checker module, `REQUIREMENTS`, `check_bd_l3`, `validate_report`, `[bd10:13]`'s verdict
aggregation and the `verdict:` label keys are bd#28's deliverable (§7). This lot exports no checker
and asserts nothing about one. `[bd10:18]`'s `__all__` requirement travels with it and additionally
binds `conformance/attest.py` here.


### 2.4 Additions to the LLM seam

- `invoke_llm_subprocess` gains keyword-only `injections: "Sequence[InjectedBlock] | None" = None`.
  `None` and `()` are **distinct and both legal**: `None` is "no injection channel used", `()` is
  "channel used, zero blocks". Neither is an error.
- `_dispatch_backend` gains keyword-only `injections` and forwards **nothing** of it to the backend:
  blocks are assembled into `prompt` before dispatch, so the `LLMBackend` protocol is unchanged.
- `StepResult.data` gains two **optional** keys, both written by the adapter, both meaning "what
  this adapter observed". Absent means "this adapter cannot observe"; **absence is never an error**
  and never a licence to substitute something the engine already knows:
  - `observed_model: str` — the model identity the adapter invoked (§5).
  - `observed_tools: Sequence[str]` — `[bd10:6]` (G2) the **distinct tool heads** the adapter saw
    in its own transcript. See below for why this, and not the raw transcript.

`[bd10:6]` **(G2) R3.6 had no input channel, so its enforcement clause was unimplementable.**
v1 pinned `capability_escapes` as a pure function and then never said how a transcript reaches
`_dispatch_backend`: §2.4 added only `observed_model`, and `_invoke_subprocess` returns
`worker_written_paths` and `mcp_server_losses` (`llm_subprocess.py:1926,1932`) but never the events.
So AC-C3's "a non-empty result yields `E_CAPABILITY_ESCAPE`" could not be written by any GREEN, and
the RED correctly covered only the pure function.

The channel is `observed_tools`, **not** the raw event list, and the choice is load-bearing rather
than cosmetic. Returning the transcript would put an unbounded event list into `StepResult.data` —
the same payload that `phase_artifacts` already truncates at a 4096-byte fence
(`EMISSIONS_SPEC.md` AC-E3d) — and would hand every adapter a transcript-capture surface, which is
the precise reason CL §8 struck ADV-9. A bounded set of distinct tool heads carries exactly the
evidence R3.6 needs and nothing else. The adapter observes; the chokepoint adjudicates; the payload
stays small. `claude-subprocess` derives it from the transcript walk it already performs
(`llm_subprocess.py:2513-2571`), which is the harness's own record and not the actor's self-report.

### 2.5 Three new error codes

`E_MODEL_PIN_MISMATCH`, `E_INJECT_UNATTRIBUTED`, `E_CAPABILITY_ESCAPE`, each registered in
`error_codes.ERROR_CODES` with a one-line trigger description. All three are returned as
`StepResult(status="error", recoverable=False)`; none is raised as an exception.

## 3. R3.1 — the effective prompt is hashed into the event log

- **AC-P1** Every dispatch **for which `telemetry_ctx.get_current_run()` is not `None`** emits
  exactly one `model_invocation_attested` event through `_emit_safe` (`llm_subprocess.py:2980`),
  with payload keys **exactly**
  `{step_name, backend, model_requested, prompt_sha256, injections, declared_capabilities,
  capability_enforcement, observed_model, observed_tools}`. Asserted by exact key-set equality, so a GREEN carrying
  extra diagnostic keys fails and a consumer's key set cannot drift silently. Where there is no
  active run context the engine emits nothing and **MUST NOT raise** (AC-P8).
  *Kills:* an emit that omits any key; an emit that adds one.

  `[bd10:4]` **The run-context guard is in the head clause because without it this AC is a Class B
  trap of my own making.** `_emit_safe` takes `run_ctx.event_log`, and `run_ctx` is
  `telemetry_ctx.get_current_run()`, which is `None` outside a run (`telemetry_ctx.py:73`). Every
  existing emit at this seam guards first (`:917-919`, `:3192`). v1's unqualified "every dispatch
  emits" false-failed a correctly-guarded GREEN, while a GREEN satisfying it literally would
  dereference `None` and crash **every** context-free call — including ones the existing suite
  already makes. Found by the author before the gate; recorded because `CONTRACTS_SPEC.md` §0.8
  counts this family's discharged-literally-defect-one-level-down instances, and the count is only
  honest if self-caught ones are in it.

  `[bd10:5]` **(G3) `observed_model` joins the key set, because two of my own clauses disagreed.**
  v2's AC-M1 required a report to mark R3.3 `not-checked` for an invocation whose adapter reported
  nothing — but a checker reads **events**, and v1's key set excluded `observed_model`, so no
  checker could learn it and that clause was unimplementable. Adding the key is the fix that makes
  R3.3 attestable from the log at all. The consumer is now bd#28 (§7), which does not change why the
  key exists: **the seam must record the observation whether or not this lot ships the reader.**
  Value:
  the adapter's reported identity, or `null` when the adapter reported none — `null` is the
  recorded third state and MUST NOT be backfilled from `model_requested` (§5, AC-M1).

  `[bd10:12]` **(G8, G9) `observed_tools` joins the key set too, and it is the same defect twice.**
  The RED found that v2's AC-C3 asked for a fixture pair "whose only difference is absent-versus-
  empty" that **cannot produce two verdicts**: an adapter reporting `[]` has escaped nothing under
  every declaration, so both forms return `ok`, and nothing anywhere recorded which of the two had
  happened. That is a one-outcome assertion — my own §0.1 violation, written into the clause that
  exists to prevent it. Separately (G9) AC-M1's "the report marks R3.3 `not-checked`" had nowhere
  to land, for the same underlying reason: the payload recorded what the adapter *reported* but
  never whether it *could observe at all*.
  One fix closes both. `observed_tools` is recorded exactly as `observed_model` is: the reported
  sequence, or `null` when the adapter reported none. `null` is now the observable that makes
  "absent" differ from "empty", and the two keys together let the report compute per-requirement
  verdicts (`[bd10:13]`) instead of asserting them.

  `[bd10:13]` **(G9) The verdict AGGREGATION moved to bd#28 with the report; the OBSERVATIONS it
  aggregates stay here.** The rule — `failed` if any invocation recorded a violation, else
  `not-checked` if no invocation carried a non-`null` observation, else `passed` — is bd#28's
  deliverable, together with its `verdict:` label keys. What this lot owes bd#28 is the evidence:
  `observed_model` and `observed_tools`, each `null` when the adapter reported nothing. Recording
  the observation rather than a verdict flag is deliberate and survives the split — a recorded
  verdict would let the emitter grade itself, which is §0.1 subtype 3 at the level of the whole
  system.


- **AC-P2** `prompt_sha256` equals `"sha256:" + sha256(assembled.encode("utf-8")).hexdigest()`,
  asserted **by equality against a digest the test recomputes from the text it supplied** — never
  from a value read back out of the event (§0.1 subtype 3). `"sha256:" + "0"*64` and any constant
  MUST fail.
  *Two outcomes, one fixture:* with the emitter intact the digest matches the test's own
  recomputation; with `hash_text` mutated to return the zero sentinel the same fixture fails.
- **AC-P3** The hash covers the **pre-hoist** text (§0.4). Fixture: one call whose `prompt`
  contains `stable_prefix` and whose `stable_prefix` is non-empty. **Both outcomes measured on that
  one fixture:** `prompt_sha256 == hash_text(full_prompt)` **and**
  `prompt_sha256 != hash_text(full_prompt.replace(stable_prefix, "", 1))`. The inequality is the
  half that kills a GREEN hashing `llm_subprocess.py:1391`'s local, and without it the AC is
  §0.1 subtype 1.
- **AC-P4** Quantified over **the two branches of `_dispatch_backend`**: one call with
  `stable_prefix` non-empty, one with it empty, both emit. A one-branch GREEN fails exactly one.
- **AC-P5** Quantified over **the dispatches of one `invoke_llm_subprocess` call**: a run driven
  down the GH1169 fallback path (`:1299-1320`) emits **two** events, the second carrying
  `backend == "claude-subprocess"`. Non-uniform by construction — the two events differ in
  `backend`, so a once-per-call GREEN and a last-dispatch-only GREEN both fail. Positive control:
  a non-falling-back call emits exactly one.
- **AC-P6** Emission cannot break execution. With an `EventLog` whose `append` raises **only** for
  `model_invocation_attested` (targeted, per `[G18:EDGE-5]` — a blanket raiser proves nothing
  in situ), `invoke_llm_subprocess` returns its normal status. A direct `event_log.append` instead
  of `_emit_safe` fails this.
- **AC-P8** `[bd10:4]` With **no** active run context, `invoke_llm_subprocess` completes with its
  normal status and emits nothing. Asserted against a recording adapter on one fixture, whose
  positive control is the **same** fixture under an active run context emitting exactly one event —
  so the two outcomes are attributable to the run context alone. *Two outcomes:* the guarded GREEN
  returns `ok` both times, emitting 0 then 1; a GREEN dereferencing `run_ctx.event_log` unguarded
  raises `AttributeError` on the first.
- **AC-P7** `[bd10:23]` **`REQUIREMENT_LABELS` contents pinned exactly** — this is where every
  narrowing this lot knows about is written down, and asserting it by exact equality is what stops a
  future lot narrowing a requirement without saying so:

  | Key | Value | Why |
  |---|---|---|
  | `R3.1` | `host-attested` | CL §8 — the engine hashes at assembly, not on the wire; ADV-9 is struck from the executable set |
  | `R3.2` | `injections-channel-only` | `[bd10:19]` — the channel is enforced; one of eight role-template inlining sites is migrated |
  | `R3.3` | `in-session-warn-only` | `[bd10:2]` — enforced at the chokepoint for reporting adapters; the in-session path still warns (bd#29) |
  | `R3.5` | `adapter-declared` | `[bd10:19]` — the backend declares its own enforcement; CL:101 wants a mechanism outside the actor's reach |
  | `R3.6` | `tool-head-only` | AC-C6 — the operand never leaves the adapter |

  Asserted by **exact mapping equality**, so both an omitted label and an invented one fail; and the
  mapping is immutable, so a caller cannot edit the record of what we claim. R3.4 carries no label
  and that is deliberate — it is recorded verbatim (AC-C1) with nothing narrowed.
  *Two outcomes, one fixture:* the pinned mapping matches; a GREEN dropping any single entry — most
  plausibly `R3.3`, the one that admits an open hole — fails the same assertion.


## 4. R3.2 and ADV-8 — attributed injection

`[bd10:16]` **(gate BLOCKING-5) ATTRIBUTION IS SEPARATED FROM ASSEMBLY, because v3 could not
migrate a real site without mutating it.** Found by the author while measuring AC-I5's blast radius
and, independently, by the round-1 gate.

`workflows/phase_2_explore.py:22` documents `role_template_path` as **"Prepended to prompt"** and
`:294-297` implements it — the role template is the first thing the model reads. v3's AC-I1 pinned
`assemble` to **append** blocks after the caller's prompt. Migrating that site therefore moved ~3 KB
of role framing from the head of a live production prompt to its tail, behind the output schema and
the out-of-role block, and `[bd10:10]`'s pinned `rstrip() + "\n\n"` content differed from the
`role.rstrip()` bytes actually in the prompt today. Neither position- nor byte-preserving, and
`tests/test_phase_2_explore.py:303` asserts the role text's **presence**, not its position, so the
existing suite would have reported a delta of zero. A correct GREEN obeying both ACs literally could
not avoid it: Class B.

**The fix is to notice that CL:98 does not ask the engine to concatenate.** R3.2 requires that every
injected block "carry a source identifier and a content hash" — an *attribution* requirement, not an
assembly one. v3 conflated the two and thereby gave the engine an opinion about placement it has no
basis to hold. Normative from v4:

- The caller places its own text, keeping whatever contract that site already documents.
- The caller **declares** each injected block through `injections`.
- `_dispatch_backend` records `{source_id, sha256}` per block **and VERIFIES each declared block's
  `content` occurs in the assembled prompt**, failing `E_INJECT_UNATTRIBUTED` when it does not.
- `assemble` remains exported for callers with no placement constraint, and AC-I1 still pins its
  order — but AC-I5 no longer depends on it.

Verification is what keeps this from being attribution-on-the-honour-system: a declaration that
corresponds to no bytes in the prompt fails closed, and R3.1's whole-prompt hash still covers the
result.

**Two declared limits, because containment is weaker than construction and saying so is the whole
posture of this lot.** A block declared once but inlined twice verifies; prompt text that
coincidentally contains a block's content verifies. Neither is detected in v1.
**Re-open criterion for both:** the first caller that needs occurrence *counts* or positional
attribution rather than presence.

**Scope boundary, stated because it is a reading and a gate is entitled to challenge it.** R3.2
governs blocks the **engine assembles on the caller's behalf** through the `injections` channel.
Prompt text a phase authors by string concatenation is not an "injected block" in this sense; it is
covered whole by R3.1's hash. The attestation says exactly this and claims nothing more.
**Re-open criterion:** the first phase that inlines file-sourced content into a prompt without
routing it through `injections` — `_maybe_role_template`
(`workflows/phase_workflows_common.py:410`) is today's live example and is migrated by AC-I5 so
that ADV-8 tests a door the pipeline actually uses rather than an unused one.

- **AC-I1** `assemble(prompt, blocks)` returns `prompt` followed by each block's `content` in list
  order, each separated by exactly `"\n\n"`. Pinned rather than described, because an unpinned
  separator makes every digest assertion untestable. Asserted by string equality against a literal
  the test composes; a reordering GREEN and a different-separator GREEN both fail.
- **AC-I2** `injections` in the payload is a list of `{source_id, sha256}` mappings, one per block,
  **in declaration order**, `sha256` per `hash_text` over that block's `content` alone.
  Quantified over the blocks of one call: **three** blocks with pairwise-distinct `source_id` and
  pairwise-distinct `content`, so a GREEN recording only the first, only the last, or a set
  collapsed by deduplication all fail. `injections` is `[]` for both `None` and `()`; the two are
  distinguished nowhere in the payload and that is deliberate — recording the distinction would
  invite a consumer to read `None` as an assertion that nothing was injected by any route.
- **AC-I3** **ADV-8.** `[bd10:8]` (G1) The offender kinds are exactly **`None`, `""`, and a
  non-`str`**. v1 also said "missing", which is unconstructible: §2.2 pins `InjectedBlock` as a
  frozen dataclass with `source_id` required, so a block with the attribute absent cannot be built,
  and a RED obeying v1 literally would have had to invent a second mapping-shaped input form. The
  three constructible kinds are the requirement.
  A block whose `source_id` is `None`, `""`, or not a `str` yields
  `StepResult(status="error", error_code="E_INJECT_UNATTRIBUTED", recoverable=False)` and **no
  dispatch occurs** — asserted positively by a recording adapter registered through
  `register_backend` (`llm_subprocess.py:2026`) whose call count is `0`, not by the absence of a
  side effect (§0.1 subtype 1).
  **Both orderings (§0.1):** one fixture with the offender **first** of three, one with it
  **last** of three. Positive control: the same three blocks all attributed → adapter called once,
  status `ok`.
  *Two outcomes, one fixture:* on the offender-last fixture, the intact GREEN returns
  `E_INJECT_UNATTRIBUTED` with adapter call count 0; a GREEN mutated to check only `blocks[0]`
  returns `ok` with call count 1.
- **AC-I4** `E_INJECT_UNATTRIBUTED` is present in `error_codes.ERROR_CODES` **and**
  `error_codes.py --check` exits 0 on the real tree — the existing drift gate
  (`tests/test_gh1067_ignored_dir_exclusion.py:120`) is the enforcement, and this AC pins that the
  lot does not break it.
- **AC-I5** `phase_2_explore`'s role-template inlining (`workflows/phase_2_explore.py:180,294`)
  routes through `injections` with `source_id` equal to the **resolved role-template path**, so the
  attributed channel is load-bearing on a real phase. Exactly one phase is migrated; the rest carry
  the re-open criterion above. Asserted end-to-end: the emitted event's `injections` carries that
  path and the digest of the file's text, recomputed by the test from the file it wrote.
- **AC-I6** `[bd10:16]` **(gate BLOCKING-5) The migrated phase's assembled prompt is BYTE-IDENTICAL
  to the pre-migration prompt.** This is the fence that makes AC-I5 a migration rather than a
  rewrite, and it is cheap: the test builds the prompt with the role template configured, captures
  the bytes the adapter receives, and compares against the same phase's output on this base with the
  same fixture. *Two outcomes, one fixture:* the declare-and-verify GREEN reproduces the bytes
  exactly; a GREEN routing the role template through `assemble` moves it to the tail and the
  comparison fails. It also converts `[bd10:16]`'s verification clause into a checked property on a
  real caller rather than a synthetic one.
- **AC-I7** `[bd10:16]` A declared block whose `content` does **not** occur in the assembled prompt
  yields `E_INJECT_UNATTRIBUTED` and no dispatch — the clause that stops declaration being an
  honour system. Positive control on the same fixture: the identical block, with its text actually
  present in the prompt, dispatches once.
  `[bd10:10]` **(G6) The path spelling and the content normalisation are pinned, because two
  plausible spellings produce two different digests and v1 chose neither.** `source_id` is
  `str(Path(role_path).expanduser())` — the same resolution `_maybe_role_template` already performs
  (`workflows/phase_2_explore.py:185`), **not** `.resolve()`, so a symlinked home does not change
  the recorded identifier. `content` is the string that function returns today, i.e.
  `rp.read_text(encoding="utf-8").rstrip() + "\n\n"` (`:188`) — the trailing normalisation is part
  of the injected bytes and therefore part of the hash. Pinning both is what stops the digest
  assertion from being satisfiable two ways.

## 5. R3.3 and ADV-7 — reported model identity versus the pin

`[bd10:2]` **SUPERSESSION WITHDRAWN — the in-session flip is bd#29, and the residual gap is
LABELLED here.** Decision recorded in bd#10 (issuecomment-5107410749).

v3 **declared** that this lot flips the non-hard-gate in-session drift path fail-closed against
agreement 220E5F63 / GH#222, and the round-1 gate proved the declaration unreachable: every R3.3
test installs an adapter and reads `StepResult.data["observed_model"]`, which `_invoke_in_session`
does not write (`llm_subprocess.py:929-938`), so a GREEN implementing the pin check only at the
chokepoint — what every other AC requires — passed all 36 items with the warn-only path intact and
`tests/test_2FDA949D_model_pin_warn.py` AC10 green and unedited.

**A declaration without a measurement is worse than an honestly labelled gap**, because it closes
the question in a reader's mind while the hole stays open. That is this family's "shield that is
green the whole time the hole is open", and it is why the resolution is not "declare harder".

Normative for this lot:
- **No production behaviour is changed on the in-session path.** `llm_subprocess.py:917-919` is
  untouched, `tests/test_2FDA949D_model_pin_warn.py` is untouched, and no AC edits it.
- R3.3 is enforced **at the chokepoint, for adapters that report** `observed_model` (AC-M2, AC-M4).
- The gap is recorded, not implied: `REQUIREMENT_LABELS["R3.3"] == "in-session-warn-only"`
  (§2.2, AC-P7). An attestation reading that label learns R3.3 is enforced where the adapter reports
  and warn-only on the in-session path, which is the true state of this tree.
- **Re-open criterion:** bd#29, which owns the flip, its own RED, and — the part that could not ride
  along here — an exposure measurement of how many paths run in-session today and what befalls them
  when a drifting step starts failing hard instead of warning.

- **AC-M1** An adapter reports the model it invoked as `StepResult.data["observed_model"]`.
  **Absence is a first-class third state, not a failure** — mirroring CL R2.1's
  rejected/accepted/indeterminate split. Absent → no comparison, no error, and the payload records
  `observed_model: null` (`[bd10:5]`). The aggregation that turns a log of such records into a
  requirement verdict is bd#28's (`[bd10:13]`); this lot is asserted on the record itself.
  *Kills:* a GREEN that substitutes `model_requested` when the adapter reports nothing — that is
  §0.1 subtype 3 promoted into production, a pin compared against itself, which can never fail.
  Asserted by a fixture whose adapter omits the key: `payload["observed_model"] is None` and the
  status is `ok`, with a foreign-family liveness control on the same engine proving the comparison
  path is reachable at all (so the `is None` half cannot pass merely because nothing ran).
  *Two outcomes, one fixture:* the faithful GREEN records `null`; a GREEN defaulting to
  `model_requested` records `"opus"` on the identical fixture.


- **AC-M2** **ADV-7.** An adapter registered via `register_backend` that reports a model of a
  **different family** from the dispatched request yields
  `StepResult(status="error", error_code="E_MODEL_PIN_MISMATCH", recoverable=False)`, carrying both
  `observed_model` and `pinned_model` in `data`. Positive control on the same fixture shape: an
  adapter reporting the **same** family returns `ok`.
- **AC-M3** `[bd10:7]` **(G4) `model_requested` in the payload is the POST-rebind dispatched model**,
  the same value the comparison uses — pinned here because v1 named the payload key and left its
  relationship to the tier rebind unstated, so a GREEN could honestly record either side of
  `:1273` and the RED could only assert it on fixtures where the two coincide. Recording the
  pre-rebind value would put a model in the log that was never invoked, which is the one thing an
  attestation may not do.
  The comparison target is likewise the **dispatched request model**, not the caller's original
  `model` argument. `llm_subprocess.py:1254-1273` deliberately rebinds `model` to a tier model when
  tier dispatch applies, and `:1270` calls the pre-rebind value `pinned_model`. Asserted with tier
  dispatch **active**: an adapter reporting the tier model MUST return `ok`. A GREEN comparing
  against the pre-rebind value false-fails every tier-dispatched step in production — this AC is
  the Class-B fence for that.
- **AC-M4** Family comparison, not raw equality, and the reason is in the tree: the in-session
  servicer maps `"opus"` to a Task model id such as `"claude-opus-4-8"`
  (`llm_subprocess.py:3074-3075`). Mismatch iff **both** families resolve via
  `lib.llm_provider._claude_model_family` and differ. An unresolvable observed family → no error,
  recorded as `not-checked` (an unrecognised token is not evidence of drift). Asserted by value
  against the external ladder at `lib/llm_provider.py:113`, not against a constant this lot writes.
- **AC-M5** The existing `model_pin_mismatch` telemetry event is still emitted on the flip
  (additivity for its existing consumers), **and** the step now ends `status == "error"`. Both on
  one fixture, so the AC cannot be discharged by either half alone.

## 6. R3.4, R3.5, R3.6 and ADV-10 — the declared capability set

- **AC-C1** `declared_capabilities` records the `allowed_tools` argument **verbatim as a list** when
  a declaration exists, and `null` when it is `None`. `[]` and `None` are distinct by value
  (`llm_subprocess.py:1143` already makes them semantically distinct) and both are asserted, on one
  fixture each, against the real declaration at `workflows/phase_2_explore.py:372` (§0.2).
  *Kills:* a GREEN normalising `None` to `[]`, which converts "no declaration" into the affirmative
  claim "no tools were granted" — the `[G2:4]` overclaim shape, on the field where it matters most.
- **AC-C2** `capability_enforcement` records **who enforces**, from the closed set
  `{"runtime-allowlist", "not-enforced"}`, derived from a new `"tool_allowlist"` token in
  `_BACKEND_CAPABILITIES` (`llm_subprocess.py:96`). `claude-subprocess` declares it (it appends
  `--allowed-tools` before spawn, `:1374-1376`); a backend that does not declare it records
  `"not-enforced"`. This is R3.5's honest form: CL:101 puts enforcement in the host contract, and
  `lib/reference_backends/anthropic_api.py:139,149` **accepts and ignores** `allowed_tools`, so an
  engine claiming enforcement uniformly would be claiming it for an adapter that has none.
  Asserted non-uniformly across **two** backends in one test, one of each value.
- **AC-C3** **ADV-10 / R3.6.** `capability_escapes(observed_tools, declared)` returns the sorted,
  deduplicated tool heads in `observed_tools` that are outside `declared`; a non-empty result
  yields `StepResult(status="error", error_code="E_CAPABILITY_ESCAPE", recoverable=False)`.
  `observed_tools` reaches the chokepoint as `StepResult.data["observed_tools"]` (§2.4,
  `[bd10:6]`); **absent means the adapter cannot observe** — no check runs, no error is possible,
  and that is a third state distinct from an empty set, exactly as `observed_model`'s absence is
  in §5. `[bd10:12]` The absent-versus-empty pair is asserted **through the payload**, where the two
  differ (`observed_tools` `null` versus `[]`) — **not** through the returned status, which is `ok`
  for both and cannot distinguish them. The verdict half of this pair left with the report (§7,
  bd#28); the payload half is the whole measurement here, and it is sufficient, because `null` and
  `[]` are two observable values on one fixture pair whose only difference is that key. That was
  the entire point of `[bd10:12]` — the observable had to exist somewhere, and the payload is the
  side of the split this lot owns.
  **The matching rule is pinned, not described** (`[G22:4]`): an observed `tool_use` block's `name`
  (e.g. `"Bash"`) is inside the declared set iff the set contains an entry whose text **before the
  first `"("`** equals that name exactly, case-sensitively. So `"Bash(graphify-shim.sh:*)"`
  (`workflows/phase_2_explore.py:372`) admits an observed `"Bash"`.
  `[bd10:14]` **(G10) Two different things, kept apart in the wording.** The **adapter** derives
  `observed_tools` by walking its own `tool_use` blocks, reusing the transcript walk of
  `_written_paths_from_events` (`llm_subprocess.py:2513-2571`) — the harness's own record of tool
  calls, not the worker's self-report, and therefore not forgeable by the actor. The **checker**
  never sees a transcript: `capability_escapes` takes the derived `Sequence[str]`. v2 described
  both sides as "the transcript blocks", which reads as stale against the pinned signature; the
  distinction is the whole point of `[bd10:6]` and is now stated once, here.
  **Quantified over the members of `observed_tools`, both orderings:** one fixture with the
  escaping head **first** of three, one with it **last** of three, so `any`/`first`/`last`
  reductions all die. Positive control over ≥2 heads all inside the set → `ok`.
- **AC-C4** `allowed_tools is None` ⇒ **no escape check runs and no error is possible** — nothing
  can be outside a set that was never declared. Distinguished from `allowed_tools == []`, where
  **any** observed tool use is an escape. One fixture each, with the *same* `observed_tools`, so
  the two outcomes are attributable to the declaration alone.
- **AC-C5** `E_CAPABILITY_ESCAPE` and `E_MODEL_PIN_MISMATCH` present in `error_codes.ERROR_CODES`,
  and `error_codes.py --check` exits 0 on the real tree (as AC-I4).
- **AC-C6** **Declared limit of v1, and it is in the attestation, not only here.** Only the tool
  **head** is observable — the adapter derives `observed_tools` from each `tool_use` block's `name`,
  and the operand never leaves the adapter; an argument-level escape *within* an
  allowed head (a `Bash` call outside `graphify-shim.sh` under the declaration above) is **NOT
  detected by v1**. Asserted as a characterisation test so the boundary is recorded rather than
  discovered, and labelled in the report. **Re-open criterion:** an adapter interface that surfaces
  the tool operand alongside the head.

## 7. MOVED OUT — the checker and the attestation report are bd#28

`[bd10:22]` **Cut by decision recorded in bd#10 (issuecomment-5107410749). The line is ownership,
not size.** bd#10 builds the seam (R3.1-R3.6 recorded and adjudicated at `_dispatch_backend`); the
checker and the report belong to the lot that brings the harness and the attestation writer.

The cut is not a concession to the round-1 verdict — it is what the verdict revealed. Both §7
blocking findings (B-1, the oracle that cannot reject; B-2, the export equality that false-fails a
correct GREEN) were defects **of the checker**, not of the seam. §3 came through the gate with zero
findings and §4-§6 close with assertions. And a level cannot be granted on this base at all:
BD-L0/L1/L2 do not exist, bd#8, bd#9 and bd#27 are open, and bd#7's attestation writer never landed.
`check_bd_l3` here would have been a report with no consumer.

**Moved to bd#28 verbatim as input requirements:** `conformance/bd_l3.py` (`REQUIREMENTS`,
`check_bd_l3`, `validate_report`), `[bd10:13]`'s verdict aggregation and its `verdict:` label keys,
ADV-9's `not_executed` status and its judge, gate findings **B-1** and **B-2** in full, and gate
edges **EDGE-1** (empty log) and **EDGE-8** (heterogeneous input filter). Nothing is discarded.

**What stays here, because the seam owns it:** the nine payload keys, including `observed_model` and
`observed_tools` — bd#28 recomputes verdicts *from* them, so the recording is this lot's obligation
and the aggregation is that one's. And the requirement labels, which move from the report into
`conformance.attest.REQUIREMENT_LABELS` (§2.2, AC-P7): the engine **records** what it can honestly
claim, the checker **reads** it. That split is why B-4's honesty labels survive the cut intact
rather than leaving with the report.

## 8. Out of scope

**The checker, the attestation report and the verdict aggregation — bd#28** (§7). **The in-session
model-pin flip and the supersession of 220E5F63 — bd#29** (§5). ADV-9 (CL §8: not executable in v1).
The BD-L0/L1/L2 checkers and their adversaries (bd#8, bd#9, bd#27). The attestation *writer*, its
report schema, and any grant of a level. Signing (CL §8). Shadowed runs
(`EMISSIONS_SPEC.md:514`) — this lot asserts nothing a shadowed run would change. Argument-level
capability escape (AC-C6). Migrating injection sites beyond AC-I5's one (`[bd10:19]`).

## 9. Split criterion — FIRED, and what it took with it

§9 was declared before round 1 rather than after round 4: *if the gate returns REJECTED with a
blocking finding in the same §-group twice, that group splits.* Round 1 returned five blocking
findings distributed §4:2, §7:2, §5:1, §6:1, **§3:0**. The literal rule had not fired — it needs the
same group twice **across rounds** — but the dispatcher cut on the evidence rather than waiting for
the second round to satisfy a formality, which is what a stop criterion is for.

**Two cuts, by ownership rather than by size:**
- **§7 → bd#28.** Both its findings were defects of the checker, not the seam; and no level is
  grantable on this base anyway.
- **§5's flip → bd#29.** A declared-but-unmeasured behaviour change cannot ride along with the lot
  that builds the seam; it needs its own RED and an exposure measurement.

**Accounting, named rather than waved at** (`[bd10:21]`; counted mechanically):

| Revision | ACs | Change |
|---|---|---|
| v1 | 26 | (stated as "23" — the error `[bd10:21]` records) |
| v2 | 27 | +AC-P8 |
| v3 | 27 | — |
| v4 pre-cut | 32 | +AC-I6, AC-I7, AC-A4, AC-A5, AC-M6 |
| **v4 final** | **26** | −AC-A1…A5 (§7 → bd#28), −AC-M6 (§5 → bd#29); AC-P7 repurposed as the label pin |

So the B-1/B-3/B-4/B-5 repairs added five ACs and the two cuts removed six: the lot is **exactly the
size of the v1 that was frozen** (26), with a materially different surface, and its remaining surface is the one §-group that passed the gate
clean plus three that close with assertions.


## 10. Process

Manual Option-D: frozen spec → RED → **independent gate in a separate window, verdict recorded as
an artifact comment** → GREEN → commit → mutation verification → full-suite delta **named per
corpus** → PR closing bd#10. GREEN does not start before an ACCEPTED verdict. Measured counts are
recorded every round; inherited numbers are re-measured, never trusted (§1).
