---
voice-profile: technical-en
lot: bd#10
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
> AC count 27 → 32 (AC-I6, AC-I7, AC-A4, AC-A5, AC-M6). That growth is itself evidence for the
> split recorded in §9 — see the note there.

# Lot spec — bd#10: BD-L3, attested authorship and inputs (R3.1–R3.6; ADV-7, ADV-8, ADV-10)

`[bd10:21]` **Accounting correction, recorded rather than quietly fixed.** v1 through v3 stated "23"
then "24" ACs. The real counts, obtained by enumerating the AC identifiers in this document, are
**26** at v1 (P1-P7=7, I1-I5=5, M1-M5=5, C1-C6=6, A1-A3=3), **27** at v2 and v3, and **32** at v4.
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
| `ADVERSARY_NOT_EXECUTED == "not_executed"` | `conformance/tokens.py:10` on this base |
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

### 2.3 New module — `conformance/bd_l3.py`

| Export | Form |
|---|---|
| `REQUIREMENTS = ("R3.1","R3.2","R3.3","R3.4","R3.5","R3.6")` | `tuple[str, ...]` |
| `check_bd_l3(events: Sequence[Mapping]) -> L0Report` | consumes `conformance.report.L0Report` and `conformance.tokens` |
| `validate_report(report: L0Report) -> tuple[str, ...]` | `[bd10:9]` (G5) — returns violation strings, empty when the report is well-formed. Exists because AC-A2's "a report that lists ADV-9 as executed is itself a failure" named no mechanism that could *detect* it, leaving the clause unassertable. |

`[bd10:18]` **(gate BLOCKING-2) Exhaustiveness is expressed as `__all__`, because the origin
heuristic false-fails a correct GREEN.** `[bd10:11]` said imports are "excluded by origin, not by
name", and the RED implemented that as
`getattr(value, "__module__", module.__name__) == module.__name__`. Builtin instances expose no
`__module__` — `"x".__module__` and `(1,2).__module__` both raise `AttributeError` — so the default
fires and every **imported immutable constant** counts as an export. `conformance/bd_l3.py` needs
`from conformance.tokens import REQUIREMENT_PASSED, REQUIREMENT_FAILED, …`, the vocabulary
`[bd10:13]` itself mandates, and any such import fails the equality. Class B, and it interacts
viciously with `[bd10:17]`: repairing the `failed` branch makes `REQUIREMENT_FAILED` a near-certain
import, so fixing one finding detonates the other.
Normative: both modules define `__all__` naming exactly the members of their table, and AC-A3
asserts equality against `__all__`. That is the direct expression of "exhaustive public surface" —
mechanically checkable, independent of import style, and it stops the RED enforcing an unstated
import convention as an interface contract (`[G22:1]`'s inversion).

`[bd10:11]` **The two tables above are EXHAUSTIVE public surfaces, and that is normative on GREEN.**
Raised by the RED agent against its own AC-A3, which discharges "no export grants a level" by exact
equality between each module's public names and the tables in §2.2/§2.3. That assertion is only
defensible if the tables are closed: if GREEN may add one public helper, a correct implementation
is false-failed, which is a Class B defect the RED would have written unknowingly. Normative:
`conformance/attest.py` exports exactly `EVENT_TYPE`, `hash_text`, `InjectedBlock`, `assemble`,
`capability_escapes`; `conformance/bd_l3.py` exports exactly `REQUIREMENTS`, `check_bd_l3`,
`validate_report`. Anything else GREEN needs is a module-private `_name`. Imported symbols are not
exports and are excluded by origin, not by name.

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
  AC-M1 requires the L3 report to mark R3.3 `not-checked` for an invocation whose adapter reported
  nothing — but `check_bd_l3` reads **events**, and v1's key set excluded `observed_model`, so the
  checker could not learn it and AC-M1's report half was unimplementable. Adding the key is the
  fix that makes R3.3 attestable from the log, which is the entire point of recording it. Value:
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

  `[bd10:13]` **(G9) Per-requirement verdicts live in `labels` under a `verdict:` prefix, and are
  AGGREGATES over the log, not per-invocation states.** `L0Report`'s four fields are bd#22's
  contract and this lot does not widen them (`CONTRACTS_SPEC.md` §2, AC-C2: L2 owns the carrier).
  v2's AC-M1 spoke of marking R3.3 `not-checked` "for that invocation", which a whole-log report
  cannot express at all — the honest unit is the log. Normative, for R3.3 and R3.6 alike:
  - `failed` if any invocation in the log recorded a violation of that requirement;
  - else `not-checked` if **no** invocation carried a non-`null` observation for it
    (`observed_model` for R3.3, `observed_tools` for R3.6);
  - else `passed`.
  The label keys are `"verdict:R3.3"` and `"verdict:R3.6"`, values drawn from `conformance.tokens`
  (`REQUIREMENT_PASSED`, `REQUIREMENT_FAILED`, `REQUIREMENT_NOT_CHECKED`). The `verdict:` prefix
  keeps them from colliding with the qualifier labels `"R3.1": "host-attested"` and
  `"R3.6": "tool-head-only"`, which say something different about the same requirement and must
  remain separately readable.
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
- **AC-P7** R3.1 is labelled **`host-attested`**, per CL §8: the engine hashes at assembly, not on
  the wire, so ADV-9 is not executable in v1. See AC-A2.

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

`[bd10:2]` **This AC group supersedes a live HAL decision, and says so.** Today a non-hard-gate
in-session step whose dispatched model drifts from its pin emits a `model_pin_mismatch` event and
**proceeds** (`llm_subprocess.py:917-919`; `_detect_nonhardgate_model_drift`, `:3106`), per
agreement 220E5F63 / GH#222, asserted at
`tests/test_2FDA949D_model_pin_warn.py` AC10 (`StepResult.status == "ok"`, "non-blocking warn").
CL R3.3 (CL:99) says a declared pin that mismatches **MUST fail**. Both cannot hold. This lot takes
CL as governing for the reason CL exists (§0: publishing a level we do not meet is the one failure
mode it was written to prevent), flips the path fail-closed, and **updates that one test rather
than deleting it** — the edit is confined to AC10's expected status and error code, its drift-detection
assertions untouched, and it is submitted to the gate for exactly that scrutiny. Editing an existing
test so one's own change passes is the standard way a regression is hidden; declaring it is the only
thing that separates the two.

- **AC-M1** An adapter reports the model it invoked as `StepResult.data["observed_model"]`.
  **Absence is a first-class third state, not a failure** — mirroring CL R2.1's
  rejected/accepted/indeterminate split. Absent → no comparison, no error; the payload records
  `observed_model: null` (`[bd10:5]`), and the log-level aggregate `labels["verdict:R3.3"]` reads
  `REQUIREMENT_NOT_CHECKED` (`[bd10:13]`, `conformance/tokens.py:9`) when **no** invocation in the
  log reported a model.
  *Kills:* a GREEN that substitutes `model_requested` when the adapter reports nothing — that is
  §0.1 subtype 3 promoted into production, a pin compared against itself, which can never fail.
  Asserted by a fixture whose adapter omits the key: `payload["observed_model"] is None` and the
  status is `ok`, **and** on the same fixture a GREEN mutated to default it to `model_requested`
  is caught by the verdict reading `passed` where `not-checked` is required.
  `[bd10:15]` **This bullet was stale against `[bd10:5]` and `[bd10:13]` until now** — it said
  "absent" where the payload pins `null`, and "for that invocation" where the aggregate is
  log-level. Caught by the RED agent, which followed the normative clauses rather than this
  paragraph and flagged the divergence instead of silently picking one. Recorded because a
  normative section carrying superseded wording is the artifact-drift class this lot family has
  filed against itself repeatedly (`EMISSIONS_SPEC.md:36-41`), and it is no less a defect for
  being editorial.
- **AC-M6** `[bd10:20]` **(gate BLOCKING-3) The in-session flip is MEASURED, and AC10's own
  assertions are re-asserted rather than replaced.**
  v3 declared the supersession of 220E5F63 and no AC reached the path. Every R3.3 test installs a
  `_RecordingAdapter` and reads `StepResult.data["observed_model"]`, but `_invoke_in_session` does
  not write that key — its result dict (`llm_subprocess.py:929-938`) carries `raw_response`,
  `response_bytes`, `model`, `tokens_out`, `tokens_in`, `cost_usd`, `worker_written_paths`,
  `manifest_source`. So a GREEN implementing the pin check **only** at `_dispatch_backend` — which
  is what every other AC demands — passed all 36 items with the warn-only path untouched and
  `tests/test_2FDA949D_model_pin_warn.py` AC10 still green and unedited. The spec would have
  declared a change the lot did not make, and the attestation would have reported R3.3 while the one
  production path in this tree that actually detects drift today still warned and proceeded.
  Normative:
  1. `_invoke_in_session` reports `observed_model` from `result_obj["dispatched_model"]`, so the
     in-session path reaches the same adjudication as every other adapter and the flip is a
     consequence of the chokepoint rather than a second implementation of it.
  2. A test drives `_invoke_in_session` end-to-end with a drifting dispatched model and asserts
     `status == "error"` and `E_MODEL_PIN_MISMATCH`.
  3. **The same test re-asserts AC10's drift-detection halves unchanged** — the `model_pin_mismatch`
     event is still emitted, carrying `observed_model`, `pinned_model` and `step_name`. This is the
     point: editing a live regression test to expect the opposite outcome means the change is graded
     by an artifact this lot authored, so the surviving assertions must be re-anchored in a *new*
     oracle. Declaring the edit distinguishes it from a hidden regression; it does not substitute
     for measuring it.
  4. The edit to `test_2FDA949D_model_pin_warn.py` is confined to AC10's expected status and error
     code. Its `_detect_nonhardgate_model_drift` unit assertions (AC5-AC9) are untouched.
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
  in §5. `[bd10:12]` The absent-versus-empty pair is asserted **through the payload**, where the
  two now differ (`observed_tools` `null` versus `[]`) and through the resulting
  `"verdict:R3.6"` label (`not-checked` versus `passed`) — not through the returned status, which
  is `ok` for both and cannot distinguish them.
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

## 7. Attestation — what may be claimed, and what may not

- **AC-A1** `check_bd_l3(events)` returns an `L0Report` (`conformance/report.py:14`) whose
  `requirements` is exactly `REQUIREMENTS`, and whose `labels` carry `"R3.1": "host-attested"`
  (CL §8, ADV-9 struck from the executable set) and `"R3.6": "tool-head-only"` (AC-C6).
  `[bd10:19]` **(gate BLOCKING-4) Two further qualifiers, because the lot was attesting two
  requirements at full width while measuring them far narrower.** v3 labelled its two known
  narrowings honestly and left two larger ones unlabelled, sitting in `REQUIREMENTS` and folding
  into `passed`:
  - `"R3.2": "injections-channel-only"` — AC-I5 migrates **one** site; the identical
    `read_text(...).rstrip() + "\n\n"` role-template inlining exists in **eight** files on this base
    (measured: `phase_2_explore`, `phase_3_clarify`, `phase_45_spec`, `phase_45_spec_lite`,
    `phase_5_integrity`, `phase_6_fix_integrity`, `phase_7_synthesize`, `phase_workflows_common`),
    plus `phase_05_inject`'s file-body reads. §4's re-open criterion is already satisfied seven
    times over — it even names `phase_workflows_common.py:410` as "today's live example" and then
    migrates a different file.
  - `"R3.5": "adapter-declared"` — `capability_enforcement` derives from a token the backend
    supplies **about itself** at `register_backend` time. The RED demonstrates the forgeability in
    its own fixtures: an adapter that enforces nothing is recorded `"runtime-allowlist"`. CL:101
    requires enforcement "by a mechanism outside the actor's reach"; recording a self-declaration is
    the most an engine can do, and the attestation must say so rather than imply more.
  Both asserted alongside the existing two. CL:221-224 governs this and is **not** confined to
  ADV-9: the attestation lists what was executed against what was attested. The lot knew how to be
  honest about a boundary twice and had not applied it where the gap was widest.
  Constructed with **mutable** containers per `[G22:23]`, so coercion is exercised.
- **AC-A4** `[bd10:17]` **(gate BLOCKING-1) The `failed` branch is specified in MECHANISM and
  asserted in OUTCOME. Without this the checker is an oracle that cannot reject.**
  v3 pinned a three-branch aggregation and the RED measured two of them; `REQUIREMENT_FAILED`
  (`conformance/tokens.py:8`) was not even bound in the test file, and no fixture ever handed
  `check_bd_l3` a log containing a violating invocation. Three plausible GREENs survived all 36
  items: the `failed` branch omitted; `passed=True` returned unconditionally; `violations=()`
  always. That is `[G22:18]`'s vacuity promoted into production, and it inverts CL §1's P2 — an
  oracle that cannot fail converts "unverified" into "verified" on every dashboard that reads it.
  For the artifact whose only job is to attest a level, that is the worst available defect.

  **Mechanism, which v3 never gave (the checker had no way to learn a violation had occurred).**
  `check_bd_l3` derives violations from the payload alone, recomputing the same comparisons the
  chokepoint made:
  - **R3.3 violated** by an invocation whose `observed_model` is non-`null` and whose family
    (`lib/llm_provider.py:113`) differs from `model_requested`'s.
  - **R3.6 violated** by an invocation whose `observed_tools` is non-`null`, whose
    `declared_capabilities` is non-`null`, and for which `capability_escapes` returns a non-empty
    tuple.
  Recomputation rather than a recorded verdict flag is deliberate: a flag would let the emitter
  grade itself, which is §0.1 subtype 3 at the level of the whole system.
  - **`check_bd_l3` reads only `model_invocation_attested` events** and ignores every other event
    type. Pinned because a real event log is heterogeneous and every RED fixture is homogeneous
    (gate EDGE-8), so a GREEN with no filter passes an all-attestation corpus and then
    miscounts on a real one.

  Asserted in **both directions on one fixture set**: a log with a drifted `observed_model` gives
  `labels["verdict:R3.3"] == REQUIREMENT_FAILED`, `passed is False`, and `violations` non-empty;
  the same log with the drift removed gives `passed is True` and empty `violations`. Mirrored for
  R3.6 with an escaping `observed_tools`. This is the AC that makes `passed` and `violations`
  measured at all — v3 asserted only `isinstance(report.passed, bool)`.

- **AC-A5** `[bd10:17]` **(gate EDGE-1) An empty log is `not-checked`, and `passed` is FALSE.**
  Under `[bd10:13]` a log with no invocations has no non-`null` observation, so both verdicts read
  `not-checked` — and v3 left `passed` undefined for that case, which is the purest form of
  BLOCKING-1: a report over zero evidence returning `passed=True`. Normative: `passed` is `True`
  only when **every** requirement in `REQUIREMENTS` has verdict `passed`; `not-checked` is not
  `passed`. Asserted on `check_bd_l3([])` against a populated conformant log.

- **AC-A2** ADV-9's status is `tokens.ADVERSARY_NOT_EXECUTED` and a report that lists ADV-9 as
  executed is itself a failure — CL:221-224, "an implementation that quietly counts an unexecuted
  adversary as passed is itself a conformance failure". Asserted in both directions on one fixture
  set: a conformant report passes, a report mutated to mark ADV-9 executed fails.
  `[bd10:9]` **(G5) The label key and the detecting mechanism are pinned, because v1 supplied
  neither.** The key is the literal `"ADV-9"` in `L0Report.labels`, whose value MUST be
  `tokens.ADVERSARY_NOT_EXECUTED`. "A report that lists ADV-9 as executed **is itself a failure**"
  names a *judgement*, and a judgement with no judge cannot be asserted — so §2.3's
  `validate_report(report)` is the judge: it returns a non-empty violation tuple for any report
  whose `"ADV-9"` label is absent or holds any other value, and an empty tuple otherwise. Both
  directions run through `validate_report` on one fixture set, so the AC measures a mechanism
  rather than the test's own opinion.
- **AC-A3** `[bd10:3]` **`check_bd_l3` reports requirements; it does NOT grant BD-L3.** The level is
  cumulative (CL §2) and BD-L0/L1/L2 are not implemented on this base — bd#8, bd#9 and bd#27 are
  open, and `origin/main` carries no harness, no checker and no attestation writer, only the bd#22
  carriers. A function returning "BD-L3 achieved" here would be the exact overclaim CL exists to
  prevent. Asserted: no export of this module returns a level grant, and `L0Report.passed` is
  scoped to R3.1–R3.6.
  **Re-open criterion:** the harness lot that lands the level grant consumes `check_bd_l3` as one
  input among four.

## 8. Out of scope

ADV-9 (CL §8: not executable in v1). The BD-L0/L1/L2 checkers and their adversaries (bd#8, bd#9,
bd#27). The attestation *writer* and its report schema, and the level grant (AC-A3). Signing
(CL §8). Shadowed runs (`EMISSIONS_SPEC.md:514`) — this lot asserts nothing a shadowed run would
change. Argument-level capability escape (AC-C6). Migrating injection sites beyond AC-I5's one.

## 9. Split criterion, declared before the first round rather than after the fourth

This lot is larger than the two that have succeeded in this family (L1: 24 ACs, accepted round 2;
L2: 6 ACs, accepted round 4 after a split) and carries 32 ACs across four independent surfaces.
Recorded now so narrowing is a rule rather than a concession: **if the gate returns REJECTED with a
blocking finding in the same §-group twice, that group splits into its own issue** and this lot
ships the remainder. The groups are exactly §3 (R3.1), §4 (R3.2), §5 (R3.3), §6 (R3.4–R3.6), §7
(attestation), and they share only the payload key set of AC-P1.

## 10. Process

Manual Option-D: frozen spec → RED → **independent gate in a separate window, verdict recorded as
an artifact comment** → GREEN → commit → mutation verification → full-suite delta **named per
corpus** → PR closing bd#10. GREEN does not start before an ACCEPTED verdict. Measured counts are
recorded every round; inherited numbers are re-measured, never trusted (§1).
