# Lot spec — bd#7: conformance harness + oracle interface + attestation writer + BD-L0

**v10** — gate REJECTED v1 (8), v2 (4), v3 (4), v4 (5), v5 (1), v6 (1), v8 (3). Gate round 8 has **not**
returned a verdict against v9 or v10: the round-8 gate process died on a session limit mid-audit, which is
not a finding and is not evidence of soundness. v10 carries **one defect found in v9 by the lot owner, in
v9's own addition**, plus its two smaller siblings:

- `[G7:4]` **§4.3's completeness claim was falsified by an AC added in the same version.** v9 replaced v7's
  false completeness sentence with a hand-built enumeration of collections plus an invitation to falsify it —
  and omitted **the events of a scope**, which is precisely what v9's own new AC-L0-15 quantifies over. Two
  smaller omissions came with it (`written`'s entries → `[G7:MINOR-7]`, `core_manifest`'s entries). Third
  consecutive round in which the audit artifact carried the defect it audits. **The instrument is therefore
  replaced, not reworded:** the global enumeration is demoted to a convenience index, and each AC now carries
  its own quantifier-and-fixture citation, so an AC without one is unasserted and a reviewer can settle the
  question by reading the AC rather than by redoing a list. If table and AC disagree, the AC governs.
- `[G7:MINOR-7]` AC-L0-3b4's `written` entries were uniform in depth (all 300 paths under one directory), so
  a producer special-casing depth was untested in the combined case. Now asserted over root, `sub/`, and
  `sub/dir/` spellings in one phase. Not blocking — the test loops rather than reduces — but cheap.

**v9** — gate REJECTED v1 (8), v2 (4), v3 (4), v4 (5), v5 (1), v6 (1), v8 (**3**). Round-7 findings tagged
`[G7:n]`. Every one of the three blocking findings was re-verified against the code by the lot owner before
being accepted — one of them arithmetically, to the byte — and the gate's refutation of one of the owner's
own claims was checked and conceded (`engine.py:710` **is** the `except` clause; the citation was correct).

- `[G7:1]` BLOCKING, **false-fail**: AC-E10 clause 1's sanity check required a **newly created** thread,
  which the daemonising-pool design that `[G7:self-1]` had just declared admissible does not produce. Worse
  than order-dependent — AC-E3/E8/E9 all call `evaluate_guarded` with a timeout earlier in file order, so it
  false-fails a correct pooled GREEN even under `-p no:randomly`. Third instance of `[G7:self-1]`'s shape
  inside one AC: the prose contradiction was fixed and the assertion still rejected the blessed design.
- `[G7:1b]` BLOCKING, **false-RED unsatisfiable by every implementation**: AC-L0-3d3's new joint-satisfiability
  control derived its boundary path count from one pair of identifiers (114 paths → 4092 B, **4 B** of slack)
  and then re-ran that count under identifiers 11 characters longer → **4114 B, 18 B over the limit**. Truncate
  and the `written_truncated is not True` assertion fails; don't and `append` raises, `_emit` swallows, and the
  exactly-one assertion fails. Introduced by the round-7 rewrite whose purpose was to remove exactly this shape
  from this AC.
- `[G7:2]` BLOCKING, **admits a wrong GREEN**: §4.3's completeness sentence was false. The table omitted three
  quantifiers, two asserted only over uniform single-phase logs — AC-L0-6e `[G7:2a]` and AC-L0-3b5 `[G7:2b]` —
  each admitting a GREEN that publishes `level_achieved: "BD-L0"`, `conformant: true` over a phase the checker
  never verified. `[G6:1]` again, one sub-clause over, **in the very table written to prevent that**.
- `[G7:3]` The "phase→run is the last rung" claim was false: **runs of a log** sits above it. Harmless only
  because AC-L0-12d already discharges it non-uniformly; AC-L0-2d, which appears to, is uniform. Withdrawn,
  documented, and the ladder's actual top is now stated with a falsification criterion instead of an assurance.

Also in v9: six minor pins (`[G7:MINOR-1..6]`, including defining "once per run" as once per `execute()` —
the literal was false, since one `run_id` spans many phases) and seven adversarial edges promoted to
normative ACs or explicitly declared out of scope with a re-open criterion. **AC-L0-15 is the notable one**:
a populated log scoped to a `run_id` matching **nothing** made every universal clause vacuously true, so the
checker reported all three requirements `"passed"` and `conformant: true` for a run with no evidence at all —
`all([])` one scope out from the ladder rounds 4-7 chased, and the cheapest false-green left.

**v8** — gate round 7 rejected this text (3 blocking, above). v8 was not a gate response: it carried
**two defects found in v7 by the lot owner's own verification of the round-7 RED**, both in v7's own
additions, both measured on the executing host before being written down:
- `[G7:self-1]` **AC-E10 contradicted itself** — clause 1 requires every surviving worker to be
  `daemon is True`, while the same AC's prose called a stdlib `ThreadPoolExecutor` (whose workers are
  `daemon=False`) "defensible". Clause 1 stands, the aside is withdrawn, and the pooled design is admissible
  only with daemon workers. `[G6:MINOR-2]`'s shape one AC over: two of my own additions demanding
  incompatible things for one design.
- `[G7:self-2]` **AC-L0-9 clause 7 was asserted on half its stated input and its precedence rule not at
  all** — v7 says "empty **or absent** `engine_version`" and makes "`failed` dominates `not-checked`"
  normative; the round-7 RED mutated only the empty case and asserted no coexistence fixture. Spec text
  unchanged (it was already right); the RED is corrected, which is where the gap was.

**v7** — gate REJECTED v1 (8), v2 (4), v3 (4), v4 (5), v5 (1), v6 (**1**). Round-6 findings tagged `[G6:n]`.

## THE RULE THIS LOT KEPT RELEARNING `[G6:quant]`

Rounds 4, 5 and 6 each found the *same* defect one quantifier higher:

| Round | Quantifier | Defect |
|---|---|---|
| `[G4:4]` | step → phase | `all([])` vacuously true, so a zero-step phase published `git-delta` |
| `[G5:accum]` | step → phase (union) | `written` assigned the last step's delta instead of accumulating |
| `[G6:1]` | **phase → run** | `any()` indistinguishable from `all()`, so one measured phase lifted a whole run |

The mechanism was identical all three times: **the fixture was uniform, so the quantifier was untested.** One step, or two steps that behave alike; one phase, or two phases that behave alike. A uniform collection cannot tell `any` from `all` from `first` from `last`.

**Normative, and it governs every AC in this document:** any requirement quantified over a collection
(steps of a phase, phases of a run, requirements of a report, adversaries of a level, files of a freeze)
MUST be asserted with a **non-uniform** collection of **≥2** members — at least one member satisfying
and at least one violating — **plus** the positive control where all members satisfy. An AC asserted
only over a uniform collection is to be treated as unasserted, whatever its prose says.

`[G7:3]` **v7's "phase→run is the last rung" claim was false, and is withdrawn.** One collection sits
above the phases of one scoped run: **the runs of one log**. `EventLog` is explicitly safe for multiple
appending processes (`event_log.py:82-88`), so a log holds many runs, and the *scoping isolation* property
— run A's R0.3 MUST NOT be satisfied by run B's `run_identity`; run B's `phase_artifacts` MUST NOT trip
run A's exactly-one-per-phase check — is quantified over that collection. It is discharged, but by
**AC-L0-12d** (a genuinely non-uniform two-run fixture, both directions asserted), *not* by AC-L0-2d,
whose own fixture is uniform (both runs must report `"passed"`) and which is therefore unasserted on its
own by the letter of this rule. §4.3 now carries the row and the cross-reference.

Above the runs of a log there is nothing: no requirement here quantifies over multiple log files, multiple
hosts, or multiple `L0Report`s (`build_attestation_report` takes exactly one). **That** is the last rung —
one above where v7 stopped looking, which is the point: the rule is a rule about looking, and declaring a
ladder finished is how rounds 4, 5 and 6 each missed the next rung. §4.3 records the sweep.

**v6** — gate REJECTED v1 (8), v2 (4), v3 (4), v4 (5), v5 (**1**). Round-5 findings tagged `[G5:n]`.

Round 5 confirmed all five round-4 findings closed *in the RED* with equality assertions rather than
prose, confirmed the `[G2:3]`/`[G3]` propagation pattern did not recur, verified ~30 code claims
accurate, and ruled both new pins (`[G5:seam]`, `[G5:endian]`) correct calls. One blocking finding:

- `[G5:accum]` **`written` was asserted only over single-step phases.** Every `written`-content
  assertion drives a one-step workflow; the only multi-step fixture with `git_cwd` (AC-L0-3a4) writes
  nothing and asserts `write_tracking` alone. So a GREEN that **assigns** the last step's delta instead
  of **accumulating** across steps passes all 97 tests — and AC-L0-3f actively rewards it, because
  assignment satisfies "run 2 must not contain run 1's paths" with no per-run reset at all. For a real
  multi-step phase where only an early step wrote, it publishes `written: []` **with**
  `write_tracking: "git-delta"`, so `check_bd_l0` reports R0.2 `"passed"`, `labels["R0.2"]` reads
  `"writes-observed"`, and the report attests `BD-L0`/`conformant: true`. That is the affirmative claim
  "we measured the write channel and nothing was written" for a phase that wrote — AC-L0-3a's own
  normative sentence violated, and strictly worse than the ambiguous `files_touched` absence §0
  recorded. `[G4:4]` closed the per-step→phase quantifier for `write_tracking` and left the identical
  quantifier on `written` open. Multi-step phases are the normal case for the flagship consumer.

Also corrected this round: my own report claimed the RED had no `StopIteration` failures. It has two,
at the `AC-L0-2b` and `AC-L0-3a` differentials — the claim was an artifact of a grep pattern that
required a colon after the exception name, so `file:1648: StopIteration` never matched. The gate caught
it by reading the source instead of trusting the measurement. Recorded because it is the lot's own
defect class committed in the lot's own reporting: a property asserted without being measured.

**v5** — gate REJECTED v1 (8), v2 (4), v3 (4), v4 (5). Round-4 findings tagged `[G4:n]`.

Round 4 confirmed all four round-3 defects closed on their own terms and found no propagation
contradiction — the `[G2:3]`/`[G3]` regression pattern did not recur. It found five NEW instances of
the lot's one disqualifying class, through doors rounds 1–3 never opened:

- `[G4:1]` The attestation's producer identity, provenance triple, `timestamp` and `level_claimed`
  were asserted by **truthiness only**, so every one of the six arguments could be ignored and
  replaced by a constant (`run_id: "unknown"`, `timestamp: "Z"`). The spec states this rule for
  `adapter_identity` in the event log (§4.1 AC-L0-2b) and then dropped it for the same values in the
  artifact §4 designates as what the reviewer is given.
- `[G4:2]` Nothing pinned the **written file** to the built report, so a writer serialising a subset
  could omit `labels`, `conformant`, `schema`, `unsigned` and all provenance from disk — `[G:MAJOR-5]`
  and `[G:MAJOR-8]` reintroduced through the writer.
- `[G4:3]` AC-L0-9 clause 5's duplicate `phase_artifacts` event **also** lacked `write_tracking`, so
  the AC-L0-3a3 fail-closed branch satisfied both assertions and no test in the file required
  duplicate detection to exist at all — the clause was present but inert.
- `[G4:4]` AC-L0-3a's "for every step of the phase" quantifier had no negative control, and over
  **zero** steps it is vacuously true — so the spec's own wording mandated publishing
  `write_tracking: "git-delta"` for a phase the engine never scanned (`_scan_cwd` is resolved at
  `engine.py:366`, after the zero-step early return at `:355`).
- `[G4:5]` `written_digest` was asserted only by `startswith("sha256:")`, so a constant hash passed —
  the digest is the sole evidence the elided path list was ever real, so AC-F9's own "a hash that
  cannot detect anything" applied to the truncation escape hatch.

**v4** — gate REJECTED v1 (8), v2 (4), v3 (4). Round-3 findings tagged `[G3:n]`.

Round 3 confirmed all four round-2 defects closed on their own terms, but found that the
`write_tracking` fix propagated into two ACs it did not update, and the `conformant` rewrite
opened a zero-rank path. Both are the same regression class round 2 flagged.

**v3** — gate REJECTED v1 (8 blocking) and REJECTED v2 (4 blocking). Amendments closing round-1
findings are tagged `[G:...]`; round-2 findings `[G2:n]`.

Round 2 confirmed 6 of 8 round-1 defects closed, and found that two of my fixes were only
apparently closed (R0.1 probe still constant-satisfiable; `L0Report.passed`/`.violations`
ignorable at the checker→attestation seam) while two amendments introduced NEW defects of the
same class they were meant to close:
- `[G2:3]` AC-A11's positive control contradicted AC-A9, silently mandating an unstated
  `level_achieved = min(measured, claimed)` cap. **Normative fix: `level_achieved` is a measured
  fact, never influenced by `level_claimed`.**
- `[G2:4]` The R0.2 label was hard-pinned to `"writes-observed"` while the write channel is inert
  unless `org_config["git_cwd"]` is set (`engine.py:1062`) — publishing "not measured" as an
  affirmative observation. Exactly the MAJOR-5 defect class, write half.

FROZEN for this lot. Source of truth: `2026-07-26_bytedigger_conformance_levels.md` (FROZEN v1,
HAL commit `fd35e1304`). §1–§6, §9 normative. §7 out of scope.

**Base rebased** `[G5:base]`: `origin/main` @ **`073ce12`** (was `606ab58`), picking up bd#13
(`f53c5d1`, manifest/version-parity declaration registry) and bd#12 (`073ce12`, ci unwedged +
`ci-heartbeat`). Neither touches `engine_py` — verified with `git diff --stat 606ab58..origin/main --
engine_py/` (empty) — so the engine-suite baseline is unaffected. The lot touches no workflow files,
so there is no overlap with `ci-heartbeat`. bd#13 does bear on R0.3: see **AC-L0-2c**.

## 0. Measurement that drives the design (answered before designing)

Against `origin/main` @ `606ab58`:

| Req | State | Evidence |
|---|---|---|
| R0.1 append-only | mechanism yes, proof no | `event_log.py:123` `O_WRONLY\|O_APPEND\|O_CREAT`, one `os.write` ≤ 4 KiB. No test asserts prefix-immutability. |
| R0.2 phase identity (phase level) | yes | `engine.py:269`, `:289` |
| R0.2 phase identity (step level) | **no** | `step_started{step_name}` `:370`; `step_finished{step_name,status,duration_ms,error}` `:472` |
| R0.2 outcome | yes | `workflow_finished.status`, `step_finished.status` |
| R0.2 artifacts written | partial | `files_touched` `:466`, suppressed on empty delta (`:438`) — absence is ambiguous |
| R0.2 artifacts read | **no** | 0 occurrences of `files_read`/`artifacts_read` in `engine_py` |
| R0.3 engine version | **no** | `engine_version` absent repo-wide; version lives only in `pyproject.toml:10` |
| R0.3 adapter identity | partial, wrong granularity | `model` per LLM call (`llm_subprocess.py:778/1208/1422`); a run with no LLM call carries none |

Consequence: the lot **adds** identity + phase-scoped artifact records and does not rewrite
`event_log.py` or `WorkflowEngine._emit`.

## 1. Scope

Three deliverables, no adversaries (§9 step 1; ADV-1…ADV-10 belong to bd#8/#9/#10).

New bd-native package `engine_py/conformance/` — NOT added to `core_manifest.json` (bd-only;
precedent `lib/run_allowlist.py`), so HAL drift stays at §6's baseline — `extra_bd == 0`, identical to
the running host's own `main`. `[G5:MINOR-1]` v5 stated the literal `5/0/0` here while §6 retracted that
same figure as macstudio-specific: two numbers in one frozen document, so the literal is struck at the
source rather than restated. Added to
`pyproject.toml [tool.setuptools.packages.find] include` so it ships.

## 2. Oracle plugin interface — `engine_py/conformance/oracle.py`

### 2.1 Three states, unmergeable by type (§6, R2.1)

```python
class OracleOutcome(Enum):
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    INDETERMINATE = "indeterminate"
```

- **AC-O1** `bool(outcome)` MUST raise `TypeError`. Enum members are truthy by default, so
  `if outcome:` would silently read INDETERMINATE as accepted. This is the exact collapse R2.1
  exists to prevent, so it is closed at the type, not by convention.
- **AC-O2** `outcome == True` / `outcome == False` MUST raise `TypeError`. Equality against `bool`
  is the second collapse path.
- **AC-O3** No constructor from a boolean exists: `OracleOutcome` MUST NOT expose `from_bool`,
  and `OracleOutcome(True)` MUST raise `ValueError`.
- **AC-O4** The three members are distinct and `INDETERMINATE is not REJECTED`,
  `INDETERMINATE is not ACCEPTED`.
- **AC-O5** `[G:edge-7]` `OracleOutcome` MUST NOT use a mixin base: its `__mro__` MUST contain no
  type other than `OracleOutcome`, `Enum` and `object`. A `str`/`int` mixin satisfies AC-O1..O4
  while `json.dumps(outcome)` re-emits a bare truthy scalar — the collapse returns at the process
  boundary, so "closed at the type" must include the serialised form.

### 2.2 `freeze` — hash over the artifact set including membership (R1.3/R1.4)

`freeze(paths: Iterable[Path], *, root: Path) -> str` returning `"sha256:<64 hex>"`.

Algorithm: relpaths to `root`, POSIX-normalised, sorted. One SHA-256 over the byte stream

```
b"bdconf-freeze/v1\0" || u64(len(files))
  for each file in sorted order:
      u64(len(relpath_utf8)) || relpath_utf8 || u64(len(content)) || content
```

`[G5:endian]` **`u64` is big-endian** (`struct.pack("!Q", n)`). v4 left this unpinned, which the RED
round-5 agent correctly flagged: with the byte stream normative but its integer encoding undefined,
GREEN and the golden vector of AC-F12 could disagree while both matched the spec, and a published
freeze would not be reproducible from the documented format — the thing R1.3/R1.4 want it for.
Network byte order is chosen because the digest is a wire/publication format, not an in-memory one.

- **AC-F1** Adding a file to the set MUST change the hash, even when the added file is empty.
  (Length-prefixed relpaths + the leading count put membership inside the digest.)
- **AC-F2** Removing a file MUST change the hash.
- **AC-F3** Renaming a file with identical content MUST change the hash.
- **AC-F4** Changing one byte of content MUST change the hash.
- **AC-F5** Reordering the input iterable MUST NOT change the hash (canonical sort).
- **AC-F6** Two files whose concatenated names/contents alias each other MUST NOT collide —
  e.g. `{"ab": "c"}` vs `{"a": "bc"}` produce different hashes (length prefixes forbid it).
- **AC-F7** A missing or unreadable path MUST raise `OracleFreezeError`. Silently skipping an
  unreadable oracle artifact would make the freeze weaker exactly when it matters.
- **AC-F8** A duplicate relpath in the input MUST raise `OracleFreezeError`.
- **AC-F9** An empty artifact set MUST raise `OracleFreezeError` — a freeze over nothing is a
  hash that cannot detect anything.
- **AC-F10** `[G:MINOR-3]` `freeze` MUST return `"sha256:"` followed by exactly 64 lowercase hex
  characters.
- **AC-F13** `[G5:EDGE-4]` **`freeze` MUST read content as bytes.** A GREEN using
  `path.read_text().encode("utf-8")` applies universal-newline translation, so CRLF and LF spellings of
  the same file digest identically — the cross-host irreproducibility AC-F12 exists to prevent, inside
  the digest itself. AC-F12's vector contains no newline and cannot distinguish. The golden vector MUST
  therefore include a `\r\n` byte, and a CRLF-vs-LF pair MUST produce different digests.
- **AC-F14** `[G5:EDGE-5]` `[G6:MINOR-9]` **Duplicate detection is on the normalised relpath, not object
  identity — and the symlink half is asserted, not just the `./` half.** A GREEN deduping on
  `Path.resolve()` and one deduping on `relative_to(root).as_posix()` agree on `root/"./a.txt"` and differ
  exactly on a symlink beside its target, so testing only the `./` case leaves the clause that
  discriminates them unasserted. Both pairs MUST raise.
  AC-F8 passes the *same* `Path` object twice. `root/"a.txt"` vs `root/"."/"a.txt"` (and a symlink
  alongside its target) normalise to one relpath, and depending on whether GREEN dedupes before or after
  normalisation they either raise `OracleFreezeError` or silently double-count — and R1.4 makes set
  membership load-bearing. Normative: they MUST raise `OracleFreezeError`, asserted with the
  distinct-object/same-relpath pair.
- **AC-F12** `[G4:MINOR-7]` **One known-answer vector.** AC-F1..F11 are all *relational*, so any
  collision-resistant scheme satisfies them and the normative byte stream above is not actually
  pinned — a published freeze would not be reproducible against the documented format across hosts or
  versions, which is what R1.3/R1.4 want it for. Required: one golden-vector assertion over a fixed
  two-file set, with the expected digest computed from the documented stream (domain prefix, u64
  count, u64-length-prefixed relpaths and contents) in the test itself.
- **AC-F11** `[G:MINOR-4]` "Unreadable" in AC-F7 MUST include a directory and a mode-000 file:
  both MUST raise `OracleFreezeError`, not `IsADirectoryError`/`PermissionError`. A path outside
  `root` MUST also raise `OracleFreezeError`, not `ValueError`.

Declared limitation (not a defect, recorded so it is not discovered later): relpaths come from the
caller's `Path` objects, so on a case-preserving/normalising filesystem an NFC- and an NFD-spelled
name for the same file yield different digests. Fail-closed for R1.4; it only costs cross-host
reproducibility of a published freeze. Out of scope for this lot.

### 2.3 `evaluate` and the indeterminate guard

`Oracle` is a `Protocol` with `freeze` and `evaluate(state) -> OracleOutcome`.

`evaluate_guarded(oracle, state, *, timeout_s=None) -> tuple[OracleOutcome, str | None]`

- **AC-E1** An oracle raising any `Exception` MUST yield `INDETERMINATE`, never `REJECTED`.
- **AC-E2** An oracle raising `ImportError`/`SyntaxError` (load error) MUST yield `INDETERMINATE`.
- **AC-E3** A timeout MUST yield `INDETERMINATE`.
- **AC-E4** An oracle returning something that is not an `OracleOutcome` (including `True`/`False`)
  MUST yield `INDETERMINATE` — an adapter that returns a bool has not implemented the interface,
  and coercing it would reintroduce the collapse.
- **AC-E5** The reason string MUST be non-empty whenever the outcome is `INDETERMINATE`.
- **AC-E6** A clean `REJECTED`/`ACCEPTED` passes through unchanged with reason `None`.
- **AC-E7** `evaluate_guarded` MUST NOT catch `KeyboardInterrupt`/`SystemExit`.
- **AC-E8** `[G:MAJOR-4a]` **Positive control for the timeout branch.** A *fast* oracle called with
  a *finite* `timeout_s` MUST return its real outcome with reason `None`. Without this,
  `if timeout_s is not None: return INDETERMINATE` satisfies AC-E3 and every other AC — R2.1's
  "a timeout MUST NOT count as rejection" would be satisfied by never reaching a verdict at all.
- **AC-E9** `[G:edge-8]` The timeout mechanism MUST NOT be `signal`-based: `evaluate_guarded` MUST
  behave identically (AC-E3 and AC-E8 both hold) when called from a non-main thread.
- **AC-E10** `[G5:EDGE-6]` `[G6:MINOR-5]` **The abandoned oracle MUST NOT be able to hang shutdown, and
  MUST NOT accumulate.** AC-E3/AC-E9 assert only the *verdict*; nothing requires the timed-out worker to
  be reaped, and it can hang interpreter shutdown or leak one worker per call.
  v6's formulation was **unmeasurable and partly false-failing**, and is replaced: it used a 2.5 s grace
  against a 2.0 s `_SlowOracle` sleep, so a GREEN with **no reaping logic at all** passed because the
  worker died on its own inside the window; and a module-level `ThreadPoolExecutor` leaves an idle worker
  forever and would false-fail a snapshot diff. Normative instead, asserting what is
  actually achievable:
  1. Any worker still alive after the grace period MUST have `daemon is True`, so it cannot hang
     interpreter shutdown. Asserted with an oracle whose sleep is **longer** than the grace period, so the
     worker is guaranteed still alive when checked.
     `[G7:1]` **The assertion MUST identify the worker by the thread the oracle itself reports, NOT by
     diffing `threading.enumerate()` for a NEWLY created thread.** A new-thread diff false-fails the
     daemonising-pool design this AC's own `[G7:self-1]` note declares admissible: the pool reuses an
     idle-alive worker, so the diff is empty and a "sanity: a worker must have been created" assertion
     fails for a correct GREEN. This is not hypothetical or order-dependent-only — `evaluate_guarded` is
     called with a timeout by AC-E3, AC-E8, AC-E9 and by this AC's own clause 2, all of which precede this
     check in file order, so a pooled worker already exists even under `-p no:randomly`. Normative form:
     the oracle records its own `threading.current_thread()` into a caller-visible object, and the
     assertion requires **that** thread to be alive (which it provably is: the sleep outlasts the grace)
     and `daemon is True`. Equivalently admissible: assert `daemon is True` over every alive non-main
     thread. Both admit the pool and the per-call thread, and both still fail a non-daemon worker.
     `[G7:1]` is the third instance of `[G7:self-1]`'s shape inside this one AC: v7 fixed the prose
     contradiction and left an assertion that still rejected the design the corrected prose admits.
  2. Workers MUST NOT accumulate: over N repeated timed-out calls the live-thread count MUST NOT grow by N.
     This holds for both the per-call-thread and the pooled designs, and fails the leak.

  `[G7:self-1]` **Correction to my own v7 text, which was internally contradictory.** v7 called a
  module-level `ThreadPoolExecutor` "a defensible design" in the same AC whose clause 1 requires every
  surviving worker to be `daemon is True` — and a stdlib `ThreadPoolExecutor` worker is `daemon=False`
  (measured on the executing host: `ThreadPoolExecutor-0_0`, `daemon=False`; py3.14). So clause 1 rejects
  the design clause 2's prose blessed: exactly `[G6:MINOR-2]`'s shape (two of my own additions demanding
  incompatible things for one input), one AC over, and it would have trapped a GREEN that took the prose
  at its word. **Clause 1 is the correct half and stands**; the "defensible" aside is withdrawn. The
  reason is measurable, not stylistic: `concurrent.futures.thread._python_exit` **joins** pool workers at
  interpreter exit, so an abandoned unbounded oracle on a non-daemon pool hangs shutdown for its full
  duration — measured on this host at **6.77 s wall for a 6 s abandoned worker** in a script whose main
  body exits immediately, i.e. precisely the hang clause 1 exists to forbid, and unbounded for an oracle
  that never returns. Normative consequence: a pooled design is admissible **only** with daemon workers
  (a pool constructed with a daemonising `initializer`/subclass, or a per-call daemon thread); the default
  `ThreadPoolExecutor` is **not** admissible for guarded evaluation. Clause 2 remains design-agnostic —
  it is about accumulation, and both admissible designs satisfy it.

Passing AC-E1..E9 does **not** mark any adversary as executed. ADV-4 and ADV-5 have the same shape
as the `_ImportErrorOracle`/`_RaisingOracle` doubles here, but these are unit tests of a helper,
not adapters substituted into a pipeline. `[G:scope-caveat]`

## 3. Attestation writer — `engine_py/conformance/attestation.py`

### 3.0 Entry points `[G:MINOR-1]`

The frozen spec, not the RED, carries the interface:

```python
build_attestation_report(*, level_claimed: str, results: Mapping[str, str],
                         l0: "L0Report", engine_version: str,
                         adapter_identity: dict, host_identity: dict,
                         repo: str, commit: str, run_id: str) -> dict
write_attestation_report(report: dict, path: Path) -> Path
```

`l0` is the `L0Report` from §4.2 — **not** a free `l0_passed: bool` `[G:MAJOR-7b]`. The caller
cannot assert L0 by argument; it must hand over the checker's own result.

### 3.1 Schema

Schema id `bytedigger.conformance.attestation/v1`, JSON. Keys per §4 "Attestation output":
`level_claimed`, `level_achieved`, `adversaries[]`, `engine_version`, `adapter_identity`,
`host_identity`, `timestamp`; plus `labels`, `conformant`, `unsigned: true` (§8), and
`[G:MAJOR-8]` the provenance triple §8 relies on to justify shipping unsigned —
`repo`, `commit`, `run_id` — plus `[G:MAJOR-7c]` an `l0` block recording which L0 requirements
were actually evaluated (`{"R0.1": ..., "R0.2": ..., "R0.3": ...}`, values `passed`/`failed`/
`not-checked`).

Adversary registry, from §4 + §8: ADV-1..ADV-2 → BD-L1; ADV-3..ADV-6 → BD-L2;
ADV-7, ADV-8, ADV-10 → BD-L3; **ADV-9 is `declarative`** (§8) and is never required for a level.

`[G4:MINOR-6]` Each `adversaries[]` entry is `{"id": "ADV-<n>", "status": <status>}` — pinned here
because §3.0's own principle is that the frozen spec, not the RED, carries the interface.

Status vocabulary: `passed` | `failed` | `not_executed` | `declarative`. Any other status value
MUST raise `[G:MAJOR-1]` — an unrecognised status is a bug in the caller, and silently bucketing
it is the same class of defect as defaulting to passed.

- **AC-A1** An adversary absent from the supplied results MUST appear in the report with status
  `not_executed`. There MUST be no default-to-passed path.
- **AC-A2** `level_achieved` MUST be computed only from adversaries with status `passed`. One
  `not_executed` in a level's required set MUST hold the achieved level below it — asserted with
  an otherwise-all-passing result set (positive control: the same set with that adversary `passed`
  MUST achieve the level).
- **AC-A3** Empty results MUST yield `level_achieved == "BD-L0"` when the L0 checks pass, and
  every ADV-1..ADV-8/ADV-10 listed as `not_executed`.
- **AC-A4** Failing L0 checks MUST yield `level_achieved == null` (no level), never `BD-L0`.
- **AC-A5** Levels are cumulative: ADV-3..ADV-6 all `passed` with ADV-1 `not_executed` MUST NOT
  achieve BD-L2 (or BD-L1).
- **AC-A6** `conformant` MUST be `false` when `level_claimed` outranks `level_achieved`, and
  `write_attestation_report` MUST still produce the file on disk `[G:MINOR-5]` (a report is
  evidence; refusing to write hides the shortfall).
- **AC-A7** `[G:MAJOR-5]` `labels` MUST equal exactly these three, asserted by value:
  `R1.2: "adapter-observed"`, `R3.1: "host-attested"`, and the R0.2 label of AC-A7b. The first
  two are §8's. The third publishes this
  lot's own gap in the attestation — the artifact §4 designates as what the reviewer is given. Its
  absence would let a report claim `BD-L0` with no visible sign that "artifacts read" was never
  observed, which is the §8-last-paragraph failure applied to ourselves.
- **AC-A7b** `[G4:MINOR-6]` **The R0.2 label, stated normatively here** — v4 cited "AC-A7b" from both
  §3 and §4.1 while §3 had no such bullet, leaving the lot's most-cited rule defined only in prose.
  `labels["R0.2"]` MUST be derived from **`requirements["R0.2"]` specifically**:
  `"writes-observed; reads-declared-only"` when `requirements["R0.2"] == "passed"`, and
  `"writes-not-observed; reads-declared-only"` otherwise. Asserted differentially over two real runs
  (AC-L0-3a's `git_cwd`-set / `git_cwd`-absent pair).
  `[G4:MINOR-2]` It MUST NOT be derived from `l0.passed`: that mislabels a report where writes *were*
  observed but R0.1 failed. Pinned by one case with `requirements = {"R0.1": "failed",
  "R0.2": "passed", "R0.3": "passed"}` expecting `"writes-observed; reads-declared-only"`.
- **AC-A24** `[G4:MINOR-1]` **A `failed` adversary MUST be published as `failed`.** AC-A11 pins that
  `failed` does not earn a level, and AC-A1 pins absent → `not_executed`, but nothing pinned the
  rendered status, so a GREEN reporting every non-passing adversary as `not_executed` erased a
  *measured* failure from the reviewer artifact — §8's last paragraph names exactly that. Assert
  `by_id["ADV-4"] == "failed"` on AC-A11's set, plus an explicitly-supplied `not_executed` case, so
  the two are distinguishable in the report.
- **AC-A8** The report MUST carry `engine_version`, `adapter_identity`, `host_identity`, `repo`,
  `commit`, `run_id` and a UTC `timestamp` with `Z` suffix; a missing or empty value for any of
  them MUST raise rather than emit a report with an anonymous producer `[G:MAJOR-8]`.
  `[G4:1]` **These are echoes of the arguments, asserted by value, not by truthiness.** v4 asserted
  only `assert report["repo"]` etc., so a GREEN hardcoding
  `{"repo": "hal/bytedigger", "commit": "0"*40, "run_id": "unknown", "engine_version": "unknown",
  "timestamp": "Z"}` ignored all six arguments and passed all 84 tests. Required: each field MUST be
  asserted **equal to a distinctive sentinel** passed in (`run_id="run-A8-sentinel"`,
  `engine_version="9.9.9-sentinel"`, `host_identity={"host":"sentinel-host"}`, etc.);
  `report["level_claimed"]` MUST equal the argument for at least **two distinct** claims (v4 never
  pinned the echo, and `level_achieved` is claim-independent by AC-A11b, so a hardcoded claim
  round-tripped cleanly); and `timestamp` MUST be asserted by a real parse plus a freshness window
  against `datetime.now(timezone.utc)`, not `endswith("Z")` — the single character `"Z"` satisfied v4.
  This is verbatim the rule AC-L0-2b states for the event log — *"a constant `"unknown"` passes a
  non-empty check while converting an unknown into an attested value"* — applied to the artifact §4
  designates as what the reviewer is given, and to the three fields §8 relies on to justify shipping
  unsigned at all.
- **AC-A9** ADV-9 MUST appear with status `declarative` and MUST NOT be counted as `passed` when
  computing BD-L3, nor block it (§8: "BD-L3 v1 is reachable without it").
- **AC-A10** Round-trip: the written file MUST parse as JSON and re-validate against the same
  level computation, yielding the identical `level_achieved` — recomputed from the report's **own**
  `l0` block, not from a re-supplied argument `[G:MAJOR-7c]`.
  `[G4:2]` **The file MUST be the whole report: `reparsed == report`, asserted as full dict
  equality** (both the all-good and the failing round-trip). v4 asserted only that
  `level_achieved`, `l0`, `level_claimed` and `adversaries` were readable back, so a writer
  serialising a subset — `{k: report[k] for k in ("schema","level_claimed","level_achieved",
  "adversaries","l0")}` — passed AC-A10, AC-A21 and AC-L0-11 while publishing a file carrying **no
  `labels`** (hence no `"writes-not-observed; reads-declared-only"`, no `"R1.2": "adapter-observed"`,
  no `"R3.1": "host-attested"`), **no `conformant`**, and **no `repo`/`commit`/`run_id`**. That is
  `[G:MAJOR-5]` and `[G:MAJOR-8]` reintroduced at the one seam that matters: §4 designates the
  written artifact, not the in-memory dict, as what the reviewer is given, and every in-memory-only
  assertion in this file is therefore unfalsifiable about the shipped evidence.
- **AC-A11** `[G:MAJOR-1]` `[G2:3]` **A `failed` adversary MUST NOT count toward a level.** Over the
  **L1+L2 set only** (ADV-1..ADV-6 `passed`, ADV-7/ADV-8/ADV-10 absent, so the measured maximum is
  genuinely BD-L2): setting ADV-4 to `failed` MUST NOT yield `BD-L2`, and the positive control —
  the identical set with ADV-4 `passed` — MUST yield `BD-L2`. Without this, a GREEN computing
  "achieved iff every required id is *present* in `results`" — or `status != "not_executed"` —
  passes AC-A1..A10 while awarding BD-L2 to a host whose ADV-4 failed.

  `[G2:3]` v2 of this AC used the **full** executable set with `level_claimed="BD-L2"` as its
  positive control while AC-A9 used the *identical* measured set with `level_claimed="BD-L3"` and
  expected `BD-L3`. Both could only pass under `level_achieved = min(measured, claimed)` — an
  unstated cap that contradicts AC-A2 and upstream §4, where "level claimed" and "level achieved"
  are two independent facts on the same report. **Normative: `level_achieved` is a measured fact
  and is NEVER capped, floored or otherwise influenced by `level_claimed`.** `level_claimed` feeds
  `conformant` and nothing else.
- **AC-A11b** `[G2:3]` **`level_achieved` is independent of `level_claimed`.** The identical
  `results` set MUST produce the identical `level_achieved` for every one of the four valid
  `level_claimed` values. This pins the rule that the v2 contradiction was silently mandating.
- **AC-A12** `[G:MAJOR-1]` A status outside the vocabulary (e.g. `"skipped"`, `"ok"`, `True`) MUST
  raise `ValueError`, never be treated as `passed` and never be silently bucketed.
- **AC-A13** `[G:MAJOR-4b]` **Positive control for `conformant`.** A run where `level_claimed`
  equals `level_achieved` MUST yield `conformant is True`. Without it a constant `False` passes
  AC-A6 and the whole file.
- **AC-A14** `[G:MINOR-12]` The `adversaries` list MUST contain exactly the ten known ids
  ADV-1..ADV-10 — no fabricated entry, no dropped id — asserted as a set equality.
- **AC-A15** `[G:MAJOR-8]` `schema` MUST equal `"bytedigger.conformance.attestation/v1"` and
  `unsigned` MUST be `true`.
- **AC-A16** `[G:edge-9]` A `level_claimed` outside `{"BD-L0","BD-L1","BD-L2","BD-L3"}` (including
  `""`, `None`, `"BD-L4"`) MUST raise, never rank at zero and report `conformant: true`.
- **AC-A17** `[G:MAJOR-7b]` `build_attestation_report` MUST derive `l0` state from the supplied
  `L0Report`. Passing an `L0Report` whose `R0.1` is `not-checked` MUST yield
  `level_achieved is None` — a level cannot be granted while a third of it was never evaluated.
- **AC-A18** `[G2:2]` **`L0Report.passed` and `.violations` are not ignorable.** An `L0Report` with
  `requirements` all `"passed"` but `passed is False`, **or** with a non-empty `violations` list,
  MUST yield `level_achieved is None`. Each of the two signals MUST be asserted independently, and
  each MUST have a positive control (the same report with that one signal cleared reaches
  `BD-L0`). Without this, the AC-A17 rewrite left a GREEN free to read only `requirements`: a
  shadowed run (AC-L0-12) whose `violations` carry `E_SHADOWED_RUN` while `requirements` sit at
  their `"passed"` default is attested `BD-L0`. AC-A4 does not catch it because there `passed=False`
  and `requirements["R0.1"]=="failed"` agree, so nothing discriminates between the two signals.
- **AC-A22** `[G3:MAJOR-2]` **The published `l0` block needs a negative control.** `report["l0"]`
  MUST equal `dict(l0.requirements)`, asserted on the **failing** reports of AC-A4, AC-A17 and
  AC-A18 — not only on all-passing ones. v3 asserted it in three places, every one of them on an
  input where the requirements were already all `"passed"`, so the literal constant
  `{"R0.1":"passed","R0.2":"passed","R0.3":"passed"}` passed the entire file. §3.1 designates this
  block as the record of what was *actually evaluated*, and AC-A10 makes it the downstream
  recomputation source — a fabricated block would let a re-validator recompute `BD-L0` from a
  report whose own `level_achieved` is `null`. Additionally AC-A10 MUST round-trip a **failing**
  report through write → reparse → recompute, not only the all-good case.
- **AC-A23** `[G3:MAJOR-3]` **`level_achieved is None` ⇒ `conformant is False`, for every
  `level_claimed`.** Asserted in AC-A4, AC-A17 and AC-A18, each of which already builds a
  null-achieved report and v3 left silent about `conformant`. The AC-A19 rank rewrite made this
  reachable: `_RANK.get(achieved, 0)` yields `conformant: true` on a report with
  `level_achieved: null` and `level_claimed: "BD-L0"` — the "rank at zero" defect AC-A16 names for
  the claimed side, unguarded on the achieved side. It is the single headline boolean a reviewer
  reads first, so "not measured → conformant" is the worst place this class of defect can land.
- **AC-A19** `[G2:3]` `conformant` MUST be `true` when `level_achieved` **outranks** `level_claimed`
  (a host claiming less than it measured is not non-conformant). `[G2:12]`
- **AC-A20** `[G2:edge-3/4]` Input-side status hygiene: a fabricated adversary id in `results`
  (`"ADV-42"`, or a case variant `"adv-1"`) MUST raise, and `{"ADV-9": "<out-of-vocabulary>"}` MUST
  raise — AC-A9's declarative override MUST NOT pre-empt AC-A12's validation.

## 4. BD-L0 against our own host

### 4.1 Engine-side additions (minimal, additive)

- **AC-L0-1** `step_started` and `step_finished` payloads MUST carry `phase` (the workflow name),
  alongside the existing keys. Existing keys unchanged (`derive_state.py` consumers untouched).
- **AC-L0-2** A new `run_identity` event MUST be emitted once per run, as the event immediately
  following `workflow_started` **found by index of that event, not at position 0** `[G:MINOR-10]`
  (`engine.py:250-268` emits `phase_reroute_entry` first whenever `phase_reroute` is set, so
  `kinds[0]` is a fixture property, not an engine invariant). Payload: `engine_version` and
  `adapter_identity`. Closes R0.3 for runs that make no LLM call — the gap §0 found.
  `[G7:MINOR-1]` **"Once per run" is imprecise and is hereby defined: once per `execute()` call.** AC-L0-3a5
  establishes that one `run_id` legitimately spans **many** `execute()` calls (that is the flagship consumer
  shape, and why AC-L0-13 exists), so a scoped run will contain **as many `run_identity` events as it had
  phases** — AC-L0-3a5(3) drives exactly that, two `execute()` calls under one `run_id`. AC-L0-2d and AC-A28
  both presuppose the per-`execute()` reading. The literal "once per run" was therefore false, and no test
  trapped either reading, so nothing shipped wrong — but a checker author had no rule for `n > 1`.
  Normative, filling the gap rather than leaving it to GREEN's discretion:
  - The emit is once per `execute()`, immediately after that call's `workflow_started`.
  - The checker MUST accept `n >= 1` `run_identity` events in one scoped run, and MUST require **every**
    one of them to satisfy R0.3 (a per-`execute()` collection — so, per `[G6:quant]`, asserted on a
    non-uniform two-phase run where phase_b's identity is malformed: R0.3 MUST NOT be `"passed"`, with the
    uniform two-identity run as the positive control). One valid identity MUST NOT excuse a malformed
    sibling; that is `[G6:1]`'s mechanism on the identity channel.
  - Zero `run_identity` events in a non-empty scope remains R0.3 `"failed"` (AC-L0-9 clause 6).
- **AC-L0-2c** `[G5:base]` **`engine_version` provenance survives packaging, and has no placeholder.**
  The canonical version lives in `engine_py/pyproject.toml [project].version` (confirmed against
  `scripts/version_parity.py --list-declarations` on `origin/main` @ `073ce12`: six declarations, all
  **files**, canonical is that one — there is no runtime accessor in `engine_py`, so this lot is the
  first thing that needs one). But `pyproject.toml` is **build** metadata: `packages.find` ships only
  `lib*`/`workflows*`/`security*`/`scripts*`(`+conformance*`), so the file **is not present in the
  installed wheel** and a runtime read of it works in a source checkout and fails in the shipped
  package — precisely where an attestation matters most.
  Therefore, resolution order: `importlib.metadata.version("bytedigger-engine")` first (the installed
  distribution's own metadata, derived from the canonical declaration at build time, so it introduces
  **no seventh declaration** — `version_parity` keeps its registry unchanged), then the
  `pyproject.toml` read for a source checkout.
  **And it MUST fail closed: when neither source resolves, `run_identity.engine_version` MUST be
  absent/empty and `check_bd_l0` MUST report R0.3 `"not-checked"` — never a placeholder**
  (`"unknown"`, `"0.0.0"`, `"0+unknown"`). A fallback string here is the exact defect AC-L0-2b names
  for `adapter_identity`, on the requirement §0 found missing entirely, and it would be *invisible*:
  an attested report carrying `engine_version: "unknown"` looks measured. Asserted three ways: the
  source-checkout path resolves to the real canonical value (read from `pyproject.toml` in the test,
  not hardcoded, so a version bump cannot rot it); the installed-metadata path resolves when
  `importlib.metadata` provides it; and with **both** seams forced to fail, R0.3 is `"not-checked"`
  and `passed is False`, with no placeholder anywhere in the payload.
  `[G5:seam]` **Both seams are pinned so RED and GREEN cannot disagree while both match the spec.**
  Metadata side: the stdlib `importlib.metadata.version(package_meta.PACKAGE_DIST_NAME)` — a seam that
  exists today, so no new engine-internal symbol is invented for the test to patch.
  `[G5:MINOR-7]` The distribution name MUST come from `package_meta.PACKAGE_DIST_NAME`
  (`package_meta.py:16`, declared "single source of truth for the distribution name", stdlib-only leaf,
  shipped via `[tool.setuptools] py-modules`), **not** the bare literal `"bytedigger-engine"` — a second
  spelling of a name that already has a single source is exactly the drift `version_parity` exists to
  prevent, one field over.
  `[G5:MINOR-5]` **And the metadata half's resolution style is pinned too:** `version` MUST be resolved
  as a **module attribute at call time** (`import importlib.metadata` … `importlib.metadata.version(…)`),
  not bound at import (`from importlib.metadata import version`). Otherwise the RED's `monkeypatch` of
  the module attribute does not take effect and an otherwise-correct GREEN false-fails — the asymmetry
  `[G5:seam]` left when it pinned the source-checkout half precisely and the metadata half implicitly. Source-checkout side: the
  read MUST go through `Path(<engine_py>/pyproject.toml).read_text()`. Without this second pin the RED
  had to *assume* a read mechanism, and a GREEN using `tomllib.load` on an open handle would fail an
  otherwise-correct test — a coupling that looks like a defect and isn't. Per §3.0's principle, the
  spec carries the interface.
- **AC-L0-2b** `[G:MAJOR-6]` **`adapter_identity` provenance is defined, not a placeholder.**
  It MUST be `{"backend": <b>, "source": <s>}` obtained from the engine's own resolver
  `llm_subprocess._resolve_backend(kwarg, env)` (`llm_subprocess.py:608`), which resolves without
  an LLM call and returns `source ∈ {kwarg, env, default}` (the fourth value `"default-fallback"` is assigned
  later, at `llm_subprocess.py:1204` in the invoke path, NOT by the resolver `[G2:6]`). The test MUST assert
  the emitted value **tracks configuration** — set the backend via the env seam and assert both
  `backend` and `source` reflect it, then change it and assert the emitted value changes.
  `[G4:MINOR-4]` The test MUST `delenv` **all three** spellings — `HAL_RUNNER_BACKEND`,
  `BD_RUNNER_BACKEND`, `BYTEDIGGER_RUNNER_BACKEND` — because `config_provider._AliasEnviron`
  (`config_provider.py:258-288`) resolves `HAL_<X>` from the `BD_`/`BYTEDIGGER_` aliases, so a host or
  CI carrying `BD_RUNNER_BACKEND` breaks the `source == "default"` assertion for an environment
  reason, inside the very test that closes `[G:MAJOR-6]`.
  Truthiness alone is insufficient: a constant `"unknown"` passes a non-empty check while
  converting an unknown into an attested value, which is the overclaim the standard exists to
  prevent. There is no `"unknown"` fallback — resolution always yields a real backend and the
  `source` field is what says whether it was chosen or defaulted.
- **AC-L0-3** A new `phase_artifacts` event MUST be emitted at phase exit, **unconditionally**
  (including when nothing changed), carrying `{phase, written: [...], read: [...],
  write_tracking: <"git-delta"|"not-observed">, read_tracking: "declared-only"}`. `files_touched`
  suppression is left as-is; `phase_artifacts` is what makes "nothing was written" a recorded fact
  rather than an absent event.
- **AC-L0-3a** `[G2:4]` **`written: []` MUST NOT be able to mean two different things.**
  `_resolve_scan_cwd` (`engine.py:1062-1065`) returns `None` unless `org_config["git_cwd"]` is set,
  and then `git_pre is None` (`engine.py:384`) and no delta is ever computed. So on a run with no
  `git_cwd` the write channel is **inert**, and v2 of this spec required `written == []` in exactly
  that configuration — converting "not measured" into the affirmative claim "nothing was written",
  which is strictly worse than the ambiguous absence §0 recorded for `files_touched`.
  Therefore: `write_tracking` MUST be `"git-delta"` only when a delta was **actually computed** —
  i.e. the phase ran **at least one step** `[G4:4]` **and** `git_pre is not None and
  git_post is not None` for **every** step of the phase — and `"not-observed"` otherwise, asserted
  **differentially** (one run with `git_cwd` set, one without). `check_bd_l0` MUST report R0.2 as
  `"not-checked"` — not `"passed"` — when any phase
  carries `write_tracking: "not-observed"`, and `labels["R0.2"]` MUST then read
  `"writes-not-observed; reads-declared-only"` (AC-A7b).
- **AC-L0-3a4** `[G4:4]` **The "every step" quantifier is asserted, and zero steps is not vacuous
  truth.** v4 stated the quantifier and tested only single-step phases, leaving two false
  affirmatives live:
  1. **Partial delta failure.** An `any()`-shaped implementation (`"git-delta"` if *some* step
     computed a delta) publishes `"git-delta"` for a phase where a later step's `git_read` raised
     `subprocess.TimeoutExpired`/`FileNotFoundError` (`engine.py:1082`, a real runtime path) — a
     step window that was never scanned, attested as measured. Required: a **two-step** phase with
     the delta forced to fail on the **second step only** (inject through the `lib.git_port`
     `get_git_read()` seam, `git_port.py:145-157`, the suite's established pattern) MUST yield
     `write_tracking: "not-observed"` and `requirements["R0.2"] == "not-checked"`.
  2. **Zero steps.** `_execute_steps` returns at `engine.py:355-361` **before** `_scan_cwd` is
     resolved at `:366`, so a zero-step workflow scans nothing — yet `all([])` is `True`, so a
     spec-faithful literal reading of v4 *mandated* `"git-delta"`. **Normative: a phase with zero
     steps MUST carry `write_tracking: "not-observed"`**, asserted in AC-L0-3c 2/2 **with `git_cwd`
     set**, together with `requirements["R0.2"] == "not-checked"`. v4's zero-step test asserted only
     the event count, so the false affirmative shipped silently.
  This is the MAJOR-5 defect class on the write half: without it the lot's own flagship composed
  attestation (AC-L0-11) publishes `BD-L0` with `writes-observed` for a run whose write channel
  never ran.
- **AC-L0-3a2** `[G3:MAJOR-4]` **Negative control for the failure branch.** `_resolve_scan_cwd`
  returning a path does NOT mean anything was measured: `_git_changes_vs_head`
  (`engine.py:1068-1083`) returns `None` for a non-git directory (`returncode != 0`, `:1077/:1080`)
  or when git is missing/times out (`:1082`), and `engine.py:434/:436` then skip the delta
  entirely. A run with `git_cwd` pointing at a **non-repo** MUST therefore yield
  `write_tracking: "not-observed"` and `requirements["R0.2"] == "not-checked"` — not
  `"git-delta"` with `written: []`, which is the affirmative claim "nothing was written" over a
  channel that never ran, verbatim the `[G2:4]` defect through its failure branch.
- **AC-L0-3a5** `[G6:1]` **R0.2 is UNIVERSALLY quantified over the phases of the scoped run: a single
  measured phase MUST NOT lift an unmeasured one.** Every "not-observed" checker fixture in v6 mutated
  the *single* `phase_artifacts` of a single-phase log, and the only two-phase fixture gave **both**
  phases `git-delta`. So `any(pa.write_tracking == "git-delta")` — and equally first-phase-only or
  last-phase-only reductions — were indistinguishable from `all(...)` across all 108 tests.
  This is the flagship shape, not a synthetic one: `EventLog` is one file per run and the consumer drives
  **one `run_id` across many phases** (each phase is its own `execute()` — which is why AC-L0-13 exists
  at all), and most phases run with no `org_config["git_cwd"]`, so their write channel is inert
  (`engine.py:1062-1065` → `git_pre is None` at `:384`). Under `any()`, one `git_cwd` phase yields
  `requirements["R0.2"] == "passed"` → `labels["R0.2"] == "writes-observed; reads-declared-only"` →
  all-passed `l0` → `level_achieved: "BD-L0"` → `conformant: true`: the affirmative claim that we measured
  the write channel, for a run whose phases mostly never observed it. `[G2:4]`/`[G5:accum]` one level up,
  in the artifact §4 designates as the reviewer's and §9 requires us to publish about ourselves.
  Required, per `[G6:quant]`, on a **non-uniform two-phase** fixture (phase_a `git-delta`, phase_b
  `not-observed`, both retaining their step events):
  1. `requirements["R0.2"] == "not-checked"` and `passed is False`; positive control — the uniform
     all-`git-delta` two-phase fixture → `"passed"`.
  2. The same `L0Report` carried into the reviewer artifact: build, **write**, reparse, and assert
     `labels["R0.2"] == "writes-not-observed; reads-declared-only"`, `l0["R0.2"] == "not-checked"`,
     `level_achieved is None`, `conformant is False`.
  3. End-to-end against our own host: two `execute()` calls on one engine under **one** `run_id`, phase A
     with `git_cwd` on a real repo and phase B without, then `check_bd_l0(log.read_all(), run_id=…)` MUST
     report `"not-checked"`.
  4. The **other three** R0.2 clauses are quantified identically and MUST be asserted by mutating
     **phase_b only** on the same two-phase fixture: `phase` stripped from phase_b's step events, phase_b's
     `workflow_finished` removed, phase_b's `status` stripped — each MUST fail. v6 asserted all three only
     on single-phase logs, so `any()` survived in each.
- **AC-L0-3a3** `[G3:MINOR-5]` A `phase_artifacts` event with **no** `write_tracking` key at all
  MUST be treated as `"not-observed"` (fail-closed), not silently accepted. `[G4:EDGE-2]` The same
  fail-close MUST apply to an **unrecognised** token: `write_tracking` of `"observed"`, `"git_delta"`
  or `""` MUST yield `requirements["R0.2"] == "not-checked"`, not `"passed"`. The checker's input is
  an arbitrary event list, so `if wt != "not-observed": passed` renders any unknown spelling — a
  future engine's or a forged one — as a measured pass. Only the exact token `"git-delta"` counts.
- **AC-L0-3e2** `[G4:EDGE-4]` **"Unconditionally" includes the crash path.** A step raising, or the
  `RuntimeError` at `engine.py:679`, propagates out of `execute()`; without a `try/finally` around the
  emit there is no artifact record at all, so AC-L0-3's "unconditionally at phase exit" is unasserted
  on the one exit v4 never drove. This is not a false-green (the log also lacks `workflow_finished`, so
  R0.2 fails honestly) — but the claim must either hold or be narrowed. **Normative: it holds** —
  asserted with a step that raises, `pytest.raises` around `execute`, and exactly one
  `phase_artifacts` in the log afterwards.
- **AC-L0-3e** `[G2:edge-7]` `phase_artifacts` MUST also be emitted when the workflow ends
  `error`/`escalate` (`engine.py:674`, `:682`), not only on the `ok` path — asserted with a
  failing workflow. A failed run is exactly when the artifact record matters most.
- **AC-L0-3f** `[G2:edge-1]` Whatever carries the per-step written paths from `_execute_steps` to
  the `execute()` emit MUST be reset per run, as `engine.py:237-243` already does for
  `_same_cycle_retries`. Asserted by two sequential `execute()` calls on **one** engine instance:
  run 2's `phase_artifacts.written` MUST NOT contain run 1's paths.
- **AC-L0-3g** `[G2:11]` Inverse control for truncation: a small artifact list MUST NOT set
  `written_truncated`, and AC-L0-3d's payload assertions MUST be whole-dict-shape, so an
  always-truncating GREEN cannot pass.
- **AC-L0-3b** `[G:MAJOR-2]` **`written` MUST be non-empty when something was written.** A step
  that creates a file MUST produce a `phase_artifacts` whose `written` contains that path (reusing
  the git-delta already computed at `engine.py:434-470`). Without this positive control the entire
  payload is satisfiable by the constant `{"phase": n, "written": [], "read": [],
  "read_tracking": "declared-only"}`, which passes AC-L0-3/7/9/10 while recording nothing — and
  "nothing was written" is only information if "something was written" is distinguishable.
- **AC-L0-3b2** `[G5:accum]` **`written` is the UNION of every step's delta for the phase, never the
  last step's.** v5 asserted `written` content only over one-step workflows, so `self._phase_written =
  paths` (assign) and `self._phase_written.update(paths)` (accumulate) were indistinguishable — and
  AC-L0-3f rewards the assign form, since assignment satisfies the per-run reset requirement without any
  reset. Asserted with a **≥2-step** phase and `git_cwd` on a real repo, both halves required:
  1. **Union.** Step 1 writes `early.txt`, step 2 writes `late.txt` ⇒ `set(written) == {"early.txt",
     "late.txt"}` and `write_tracking == "git-delta"`.
  2. **The discriminating half.** Step 1 writes `early.txt`, step 2 writes **nothing** ⇒ `written` MUST
     still contain `early.txt`. A last-step-only implementation yields `written: []` alongside
     `write_tracking: "git-delta"` — R0.2 attested `"passed"`, `labels["R0.2"]` reading
     `"writes-observed"`, `level_achieved: "BD-L0"`, `conformant: true` — the `[G2:4]` defect reopened at
     the accumulation seam, and the one the flagship multi-step consumer hits on every phase.
- **AC-L0-3b4** `[G6:EDGE-4]` **The relpath spelling is asserted for NESTED paths.** Every write fixture
  writes to the repo root, so `written` and `written_digest` are only ever exercised on bare filenames. A
  GREEN emitting absolute paths, `./`-prefixed paths, or OS-separator paths for files in subdirectories
  passes all 108 tests while making AC-L0-3d's digest non-reproducible for any real phase that writes into
  a subtree — and subtree writes are the normal case. Normative: `written` entries are **POSIX relpaths
  against the scan root**, asserted with a step writing `sub/dir/nested.txt` (exact string equality, and
  the digest recomputed over that spelling).
- **AC-L0-3b5** `[G6:EDGE-3]` **An artifact record MUST actually carry its artifact fields.** AC-L0-3a3
  fail-closes only on the `write_tracking` key/token, so a scoped `phase_artifacts` of
  `{"phase": "wf", "write_tracking": "git-delta"}` — no `written`, no `read`, no `read_tracking` — yields
  R0.2 `"passed"`. Given the checker's declared threat model (an arbitrary, possibly forged event list),
  the fields carrying the actual artifact references MUST be required present and list-typed, or R0.2 is
  `"not-checked"`.
  `[G7:2b]` **Validation is PER RECORD and MUST be asserted non-uniformly, per `[G6:quant]`.** v7 asserted
  both halves by mutating the **single** `phase_artifacts` of a single-phase log, so validating the shape
  once against `records[0]` / `next(...)` — the first-phase-only reduction `[G6:1]` named — passed both.
  A forged two-phase log whose phase_a record is well-formed and whose phase_b record is
  `{"phase": "phase_b", "write_tracking": "git-delta"}` is then granted R0.2 `"passed"`, which is precisely
  the input this AC's own threat model ("an arbitrary, possibly forged event list") exists to reject.
  Required: the malformed record on **phase_b of the two-phase fixture**, both for the missing-fields half
  and the wrong-type half, with the well-formed two-phase log as the positive control.
- **AC-L0-3b6** `[G6:EDGE-5]` **An orphan `phase_artifacts` MUST NOT lift the write half.** An artifact
  record for a phase with no `workflow_started` in the scoped run is currently undefined, so a forged log
  can add a `git-delta` record for a phase that never ran. Normative: a `phase_artifacts` whose `phase` has
  no `workflow_started` in the scoped run is an R0.2 violation (`"failed"`).
- **AC-L0-2d** `[G6:EDGE-1]` **`run_identity` is once per RUN, asserted per run.** `execute()` resets
  per-run state at `engine.py:237-243`; a GREEN guarding the identity emit with an instance flag it forgets
  to reset passes AC-L0-2 and everything else, because the only two-runs-on-one-engine fixture (AC-L0-3f)
  never feeds its log to `check_bd_l0`. Run 2 then carries no identity and R0.3 fails for a conformant
  engine — AC-L0-3f's own defect class applied to identity. Asserted with two `execute()` calls on one
  engine under **different** `run_id`s, checking **each** run: both MUST report R0.3 `"passed"`.
- **AC-L0-3b3** `[G5:EDGE-1]` **Accumulation survives the validation-retry recursion.** `_execute_steps`
  is re-entered recursively at `engine.py:640-645` with `start_step=red_index`, and the outer frame then
  `return retry_result` at `:657` without running its own tail. AC-L0-3c drives that path but **without**
  `git_cwd`, so neither `written` nor `write_tracking` is asserted across the recursion. A GREEN scoping
  the accumulator to one `_execute_steps` frame, or re-initialising it on re-entry, loses the pre-retry
  steps' paths — and can publish `git-delta` for a re-entered window whose git read failed. Distinct
  seam from AC-L0-3b2. Asserted with the retry fixture, `git_cwd` set, and the pre-retry step writing a
  file that MUST appear in the final `phase_artifacts.written`.
- **AC-L0-3c** `[G:MINOR-7]` The emit Host is `WorkflowEngine.execute`, **not** `_execute_steps`,
  which is re-entered recursively on the validation-retry path and returns early for a zero-step
  workflow. Asserted by two tests: a workflow that takes the retry path MUST still yield exactly
  one `phase_artifacts`; a zero-step workflow MUST still yield one.
- **AC-L0-3d** `[G:edge-2]` **An oversize artifact list MUST NOT vanish.** `EventLog.append` raises
  `EventLogLineTooLarge` above 4096 bytes (`event_log.py:116`) and `_emit` swallows it
  (`engine.py:710`), so a phase that writes many files would silently lose its record — the
  control fails precisely as the evidence grows, an inverted control. When the payload would
  exceed the limit, `phase_artifacts` MUST instead carry `written_truncated: true`,
  `written_count: <n>`, `written_digest: "sha256:…"` over the full sorted path list, and a bounded
  `written` sample. Asserted with a step writing enough paths to exceed 4096 bytes: the event MUST
  be present, `written_count` MUST equal the real count, and `check_bd_l0` MUST pass.
  `[G4:5]` **The digest is a measured value, not a shape.** Its canonical form is normative here so
  the test can recompute it: `"sha256:" + sha256("\n".join(sorted(relpaths)).encode("utf-8"))`,
  lowercase hex, over the **full** path list in the same relpath spelling `written` uses. The test
  MUST assert **equality** against a digest it computes itself from the real path set — v4 asserted
  only `startswith("sha256:")`, so `"sha256:" + "0"*64` passed. In the truncated case the digest is
  the only evidence the elided list was ever real (`written` is a bounded sample by design), so a
  constant turns the escape hatch AC-L0-3d created into AC-F9's "a hash that cannot detect anything".
  `[G4:MINOR-5]` The payload assertions here MUST be whole-dict-shape, as AC-L0-3g requires, not
  key-by-key `payload.get(...)` probes.
- **AC-L0-3d2** `[G4:EDGE-6]` **The truncation threshold is asserted at its boundary.** AC-L0-3d uses
  ~13 KB and AC-L0-3g one path, so nothing exercises a payload *just* over or *just* under 4096
  bytes. The size predicate MUST be computed over the **serialised event as `EventLog.append` sees
  it** — envelope (`ts`, `run_id`, `event_type`, `payload`) included, ~60 bytes — not over the
  payload alone. Asserted with two runs straddling the limit: the just-under run MUST NOT set
  `written_truncated` and MUST list every path; the just-over run MUST truncate. An off-by-one that
  measures the payload alone re-opens the swallowed-`EventLogLineTooLarge` hole exactly at the
  boundary, which is where an inverted control does its damage.
- **AC-L0-4** These emits MUST go through `_emit`, so a detached event log, the shadow-event
  path and the never-raise contract all keep working unchanged. (Pre-passing at RED time — a
  forward guard, declared as such `[G:MINOR-2]`. Its discriminating power appears after GREEN: a
  direct `self._event_log.append(...)` lets the exception escape `execute()` and fails this test,
  whereas `_emit` swallows it at `engine.py:710`.)

`[G4:MINOR-8]` **What `requirements["R0.2"] == "passed"` actually means, recorded so the token is not
over-read.** The write half fail-closes to `"not-checked"` when unmeasured (AC-L0-3a), but the **read**
half is never observed at all (§5), and the vocabulary `passed|failed|not-checked` cannot express
"partially evaluated". So `R0.2: "passed"` means *identity + outcome + the write half* — reads are
declared-only, uniformly, and that is what `labels["R0.2"]` publishes. The asymmetry is deliberate:
the read gap is uniform and declared, whereas an unmeasured write channel is a per-run accident. Note
the consequence: **adding an engine-side read seam silently changes the meaning of the same token**,
and since AC-A10 makes the `l0` block the downstream recomputation source, that change must come with
a schema revision, not just a new emit.

`read_tracking: "declared-only"` is a published gap in the §8 style: the engine has no central
read seam (`io_utils` exposes only `atomic_write`), so v1 records reads that steps declare and
says so rather than implying full coverage. Re-open criterion: an engine-side read seam.

`[G:MAJOR-5]` The gap is published in **both** artifacts, and the attestation is the one that
counts — §4 designates it, not the log, as what the reviewer is given. The event log carries
`read_tracking: "declared-only"`; the attestation carries the `labels["R0.2"]` value that
AC-A7b computes from what was actually observed (NOT a constant — see `[G2:4]` below) `[G3:MINOR-2]`. v1 of this spec promised the gap would appear in
the attestation and then fixed `labels` to exactly two entries, which fenced the fix out — our own
host would have emitted `level_achieved: "BD-L0"` with the read half never observed and nothing
saying so. That is §8's last paragraph applied to ourselves, and it is the one defect this lot
cannot ship.

`[G2:4]` **The same applies to the write half, and v2 got it wrong.** The R0.2 label is not a
constant: it MUST be `"writes-observed; reads-declared-only"` only when every phase carried
`write_tracking: "git-delta"`, and `"writes-not-observed; reads-declared-only"` when any phase
carried `"not-observed"` (AC-A7b, AC-L0-3a). v2 hard-pinned the optimistic string while the
composed attestation of AC-L0-11 runs without `git_cwd` — i.e. our own flagship artifact would
have published `writes-observed` for a run whose write channel was inert. Identical defect class,
opposite half of the same requirement.

### 4.2 Checker — `engine_py/conformance/bd_l0.py`

```python
check_bd_l0(events: list[dict], *, run_id: str, writer: type | None = None) -> L0Report

@dataclass(frozen=True)                      # [G2:10] constructor pinned in the spec, not the RED
class L0Report:
    passed: bool
    violations: list[str]                    # each entry starts with the requirement id it violates
    requirements: dict[str, str]             # "R0.1"/"R0.2"/"R0.3" -> passed | failed | not-checked
```

`[G2:9]` Separator vocabularies differ by design and both are pinned: adversary status uses
`not_executed` (underscore); L0 requirement values use `not-checked` (hyphen). Do not unify them.

`[G2:13]` **AC-A21** `write_attestation_report` MUST return the `Path` it wrote, and MUST NOT
leave a partial file on disk when serialisation fails (a non-JSON-serialisable `adapter_identity`
or `host_identity` must raise with no file, or an intact prior file, remaining) `[G2:edge-10]`.

**AC-A25** `[G5:EDGE-9]` **A missing parent directory MUST NOT silently lose the report.** All AC-A21
blocks write into an already-created directory, leaving it undefined whether the writer creates parents
or raises. §4 designates the written file as what the reviewer is given, so a `FileNotFoundError` in CI
publishes nothing. Normative: `write_attestation_report` MUST create missing parents and write, asserted
against a nested path whose parent does not exist.

**AC-A26** `[G5:EDGE-10]` **`L0Report` immutability is asserted, not only declared.** §4.2 pins
`@dataclass(frozen=True)`, but nothing in the RED tests it, so a mutable `L0Report` — which
`build_attestation_report` could rewrite between reading `.requirements` and publishing the `l0` block —
passes. Asserted by requiring an attribute assignment to raise `FrozenInstanceError`.

**AC-A27** `[G5:EDGE-8]` **`timestamp` UTC-ness MUST be host-independent.** AC-A8 parses
`ts[:-1] + "+00:00"` against a freshness window, so `datetime.now().isoformat() + "Z"` — naive local time
mislabelled as UTC — passes on a UTC host and fails elsewhere by the local offset, making the suite's
verdict depend on the runner's zone. Asserted by comparing the report's timestamp against an independent
UTC reading (`time.time()`) as well as `datetime.now(timezone.utc)`.
`[G6:MINOR-6]` **The UTC-host blind spot is closable without a new seam, so it MUST be closed** rather than
declared: naive `datetime.now()` resolves through `time.localtime`, so setting `TZ` to a large fixed offset
(`monkeypatch.setenv("TZ", "Etc/GMT-14")` + `time.tzset()`, restored in teardown) makes a naive-local
implementation diverge by 14 h on any POSIX host — including a UTC-zone runner. v6 declared the limitation
where three lines remove it, and a declared-but-avoidable blind spot is still an unmeasured claim.

**AC-A28** `[G6:MINOR-8]` **Version resolution MUST NOT be memoised.** AC-L0-2c's three tests demand three
different resolutions from one process, so an `@lru_cache` or a module-level `_ENGINE_VERSION = _resolve()`
makes their pass/fail depend on test order — and this suite runs `pytest-randomly` (the RED measurements
need `-p no:randomly` precisely because ordering is randomised by default). `[G5:MINOR-5]`'s "not bound at
import" implies it; stated explicitly: resolution happens **per `run_identity` emit** and is not cached
across runs. Asserted by resolving twice in one process with the seam changed in between.

`run_id` is required `[G:edge-4]`: `EventLog` is explicitly safe for multiple appending processes
(`event_log.py:84-88`), so a flat unscoped list from two interleaved runs yields two
`phase_artifacts` per phase name and would fail "exactly one" for reasons that are not a
conformance defect. The checker scopes to one run.

- **AC-L0-5** `[G2:5]` (pre-passing at RED time — a regression shield over already-correct
  behaviour, declared per §0's "mechanism yes, proof no") R0.1 behavioural: append N events, snapshot the file bytes, append M more — the
  first snapshot MUST be a byte-exact prefix of the final file, and the file MUST have grown.
- **AC-L0-6** `[G2:5]` (pre-passing at RED time — regression shield, declared) R0.1 structural: `EventLog.append` MUST open with `O_APPEND` and MUST NOT use
  `O_TRUNC`, asserted by intercepting the real `os.open` flags, not by reading source text.
- **AC-L0-6b** `[G:MAJOR-7a]` **R0.1 MUST be a branch inside `check_bd_l0`.** When `writer` is
  supplied, the checker MUST itself probe it (the AC-L0-6 flag interception, runnable at check
  time) and set `requirements["R0.1"]`. When `writer` is omitted, `requirements["R0.1"]` MUST be
  `"not-checked"` and `passed` MUST be `False` — fail-closed, because a level cannot be claimed
  while a third of it was never evaluated. Without this branch §4.2's own "one finding per
  violated requirement" is false for R0.1, and `l0_passed` reaches the attestation on the strength
  of two out-of-band unit tests.
- **AC-L0-6b2** `[G3:MAJOR-1]` **`"not-checked"` fail-closes for ALL THREE requirements, not just
  R0.1.** Normative: `L0Report.passed` MUST be `False` and `level_achieved` MUST be `None` whenever
  ANY `requirements[r] != "passed"`. AC-L0-6b stated this for R0.1 only; v3 then made R0.2
  reachably `"not-checked"` (AC-L0-3a) without saying what that does to the level, leaving the
  RED satisfiable only by a GREEN that grants BD-L0 over an unmeasured write channel. AC-A17's own
  rationale — "a level cannot be granted while a third of it was never evaluated" — applies
  identically to R0.2 and R0.3.
- **AC-L0-6e** `[G5:EDGE-2]` **The checker MUST NOT grant R0.2 over a phase with zero step events.**
  "Every `step_started`/`step_finished` carries `phase`" is vacuously true over an empty step set, so a
  forged or future-engine log of `workflow_started` + `phase_artifacts{write_tracking:"git-delta"}` +
  `workflow_finished{status}` — no step events at all — yields R0.2 `"passed"`. AC-L0-3a4 closes this for
  what *our* engine emits; this closes it inside the checker, whose declared threat model (AC-L0-3a3) is
  explicitly "an arbitrary event list… a future engine's or a forged one." This is `[G4:4]`'s vacuous-
  `all()` one level up. Normative: a scoped run with a `phase_artifacts` but no step events for that
  phase MUST yield R0.2 `"not-checked"`, not `"passed"`.
  `[G7:2a]` **This clause is quantified PER PHASE and MUST be asserted non-uniformly, per `[G6:quant]`.**
  v7 asserted it over a **single-phase** log only, where `any(len(steps_of(p)) > 0 for p in phases)` is
  indistinguishable from the per-phase reduction — so the cheapest wrong GREEN, checking the step-event
  precondition once against the whole scoped run instead of once per phase, passed. On the flagship
  non-uniform fixture (phase_a `git-delta` with its step events, phase_b `git-delta` with its step events
  **removed**) that GREEN yields R0.2 `"passed"` → `labels["R0.2"] == "writes-observed; reads-declared-only"`
  → all-passed `l0` → `level_achieved: "BD-L0"` → `conformant: true`, for a run one of whose phases the
  checker never verified emitted a single step. Verbatim `[G6:1]`, one sub-clause over. Required: the
  two-phase fixture with **phase_b's step events dropped** MUST yield `"not-checked"`, with the uniform
  two-phase log as the all-satisfy positive control.
- **AC-L0-6f** `[G5:EDGE-7]` The probe's own scratch directory MUST be **removed**, not merely located
  outside the caller's cwd. AC-L0-6d asserts only the first half of §4.2's "creates and removes", and the
  probe runs ~20× per suite run. Asserted by capturing the probe's temp root and requiring it absent
  after `check_bd_l0` returns.
  `[G6:MINOR-4]` **The mechanism is pinned, per §3.0:** the probe MUST obtain its scratch directory via
  `tempfile.mkdtemp` or `tempfile.TemporaryDirectory` (the latter routes through the former, so one spy
  catches both). Without this pin the RED has to spy a mechanism it only assumes, and an equally correct
  probe using `os.mkdir(Path(tempfile.gettempdir())/name)` + `shutil.rmtree`, or `mkstemp`, false-fails the
  capture — the same `[G5:seam]` asymmetry, one AC over.
- **AC-L0-6d** `[G4:EDGE-3]` **The R0.1 probe MUST NOT write outside a path it owns.** Nothing in v4
  constrained *where* the probe writes, so `writer(Path("events.jsonl"))` would write into the
  caller's cwd during a read-only conformance check — and with an `O_TRUNC` writer of the AC-L0-6c
  shape it would truncate a real file at that path. The probe runs ~20× in this suite alone. Required:
  the probe MUST use a temporary directory it creates and removes, asserted by running `check_bd_l0`
  with `writer=EventLog` from a cwd whose contents are snapshotted before and after and MUST be
  unchanged.
- **AC-L0-6c** `[G2:1]` **Negative control for the R0.1 probe — three inputs, not two.** A writer
  whose `append` opens with `O_TRUNC`, and a writer that opens **without** `O_APPEND`, MUST each
  yield `requirements["R0.1"] == "failed"`, an `R0.1`-named entry in `violations`, and
  `passed is False`. v2 exercised only `writer=EventLog` → `"passed"` and `writer` omitted →
  `"not-checked"`, so `requirements["R0.1"] = "passed" if writer is not None else "not-checked"`
  satisfied every AC in the file — the checker would have attested R0.1 on the strength of
  `writer is not None`, leaving AC-L0-6's real flag interception as exactly the out-of-band unit
  test round 1 rejected. This is the lot's own defect class inside the lot's own checker.
- **AC-L0-7** R0.2: every `workflow_started` MUST have a matching `workflow_finished`; that
  `workflow_finished` MUST carry a `status`; every `step_started`/`step_finished` MUST carry
  `phase`; every phase MUST have exactly one `phase_artifacts`.
- **AC-L0-8** R0.3: the log MUST contain a `run_identity` with non-empty `engine_version` and
  non-empty `adapter_identity`.
- **AC-L0-9** `[G:MAJOR-3]` **One negative control per clause** — eight, not three. Each of the
  following mutations of an otherwise-valid log MUST make `check_bd_l0` fail, and MUST name the
  right requirement in `violations`:
  1. `phase` key stripped from a step event → R0.2
  2. `workflow_finished` removed → R0.2
  3. `status` key stripped from `workflow_finished` → R0.2
  4. `phase_artifacts` removed → R0.2
  5. a **second** `phase_artifacts` added for the same phase → R0.2 (the failure mode AC-L0-3c
     predicts on real retry runs; v1 tested only "missing"). `[G4:3]` **The duplicate event MUST
     differ from the baseline by duplication and nothing else** — i.e. it MUST carry
     `write_tracking: "git-delta"` like the fixture it duplicates. v4's injected duplicate omitted
     `write_tracking` entirely, so the AC-L0-3a3 fail-closed branch fired first and satisfied both
     assertions; combined with AC-L0-13 (which tests only a *missing* event) and AC-L0-12d (which
     asserts the *absence* of a duplicate violation), **no test in the file required duplicate
     detection to exist**. A retry-path double emit — precisely what AC-L0-3c predicts on real runs —
     was then attested `R0.2: "passed"`: the checker publishing a requirement it never checked.
     Round 1 rejected this clause for testing only "missing" (`[G:MAJOR-3]`); v4 made it present but
     inert.
  6. `run_identity` removed → R0.3
  7. `run_identity` present but `engine_version` empty → R0.3
  8. `run_identity` present but `adapter_identity` empty → R0.3

  Plus: an empty log MUST fail. As written in v1, AC-L0-7 and AC-L0-8 were the same assertion over
  the same input, and a checker implementing only three clauses passed both.
  `[G5:MINOR-4]` **A structural breach is `"failed"`, not `"not-checked"`.** Normative: when the log is
  present but malformed (mutations **1-6 and 8** above `[G6:MINOR-2]`), `requirements[r]` MUST be `"failed"`;
  `"not-checked"` is reserved for "the channel was never observed" (AC-L0-3a, AC-L0-6b). v5 asserted only
  `passed is False` plus a violation string on clauses 1-8, so a GREEN could render every structural
  breach as `"not-checked"` — and since §3.1's schema carries `l0` but **not** `violations`, the
  reviewer-facing artifact would then read "we did not measure the write channel" for a host whose log
  was demonstrably malformed. That is AC-A24's defect class with the polarity reversed: a *measured
  failure* published as *never measured*. It fails closed either way, which is why it is a correctness
  requirement on the published record rather than a level-granting bug. Pinned on at least the clause-1
  (R0.2) and clause-6 (R0.3) mutations.
  `[G6:MINOR-2]` **Clause 7 is excluded, and the precedence rule is stated.** v6 said "any of the eight",
  which swept in clause 7 (`run_identity` present, `engine_version` empty) — the *same observable input*
  AC-L0-2c requires to be `"not-checked"`. Two of my own additions demanding different tokens for one
  input; a spec-literal GREEN was trapped. Normative:
  - Clause 7's empty **or absent** `engine_version` is AC-L0-2c's unresolved-version case ⇒
    `requirements["R0.3"] == "not-checked"`. An unresolvable version is an unobserved channel, not a
    malformed log.
  - **`"failed"` dominates `"not-checked"`** when a structural breach and an unmeasured channel coexist for
    the same requirement. This is also what reconciles clause 1 with AC-L0-6e: step events present but
    missing `phase` is a breach (`"failed"`), whereas *no* step events at all is unobserved
    (`"not-checked"`).
- **AC-L0-10** End-to-end: running a real `WorkflowEngine` workflow with an attached `EventLog`
  MUST produce a log that `check_bd_l0` passes — L0 measured against our own host, not a fixture.
- **AC-L0-11** `[G:MAJOR-7b]` **Composed path.** One test MUST run a real engine workflow, feed the
  resulting log to `check_bd_l0`, feed that `L0Report` to `build_attestation_report`, write it,
  reparse it, and assert `level_achieved == "BD-L0"` with `l0.requirements` recording all three
  requirements as `passed`. Upstream §9 requires our own host to run the suite and publish its
  attestation from step 1 onward; nothing in v1 connected the three stages.
- **AC-L0-10/11 amendment** `[G3:MAJOR-1]` Both MUST drive the engine with a **real git repo via
  `org_config["git_cwd"]`**, so all three requirements are genuinely measured and the all-`"passed"`
  assertions are honest. v3 ran them on `_make_ctx()` with no `git_cwd` — byte-identically the
  configuration AC-L0-3a defines as `"not-observed"` — so the flagship composed artifact asserted
  `l0 == {all passed}` for a run whose write channel never ran.
- **AC-L0-11b** `[G3:MAJOR-1]` **The unmeasured composed path, asserted explicitly.** The same
  end-to-end path **without** `git_cwd` MUST yield `requirements["R0.2"] == "not-checked"`,
  `report["l0"]["R0.2"] == "not-checked"`, `level_achieved is None`, `conformant is False`, and
  `labels["R0.2"] == "writes-not-observed; reads-declared-only"`. This is the pair that makes
  "not measured" observably different from "passed" in the reviewer-facing artifact.
  `[G4:2]` **Asserted from the written file, not in memory.** This test MUST call
  `write_attestation_report` and re-assert all five values from the **reparsed** file. v4 asserted
  them on the in-memory dict and never wrote it, so the lot's own flagship "not measured ≠ passed"
  pair said nothing about the artifact a reviewer actually receives — which is the only place the
  distinction has any effect.
- **AC-L0-3d3** `[G5:EDGE-3]` **The truncation predicate MUST leave headroom for the shadow envelope.**
  `_emit`'s shadow branch (`engine.py:701-707`) adds `shadowed_event` and `provenance` to the payload, so
  a `phase_artifacts` the predicate measured as just-under 4096 exceeds the limit once shadowed and is
  swallowed at `:710` — the inverted control AC-L0-3d exists to close, reopened on the shadow path. Every
  shadow fixture uses tiny payloads, so nothing exercises it.
  `[G6:MINOR-3]` **Normative, worded to exclude the reading that breaks AC-L0-3d2:** the predicate measures
  **the exact event `EventLog.append` will receive** — shadow wrap included **iff** the shadow branch
  (`engine.py:699-700`) applies to this emit, excluded when it does not. "Leave headroom" MUST NOT be read
  as an *unconditional* reserve: reserving the measured ~64-byte shadow overhead on every emit truncates
  AC-L0-3d2's just-under (~4079-byte) unshadowed payload and false-fails a conservative, otherwise-correct
  GREEN. This AC and AC-L0-3d2's just-under control are to be read together; they are jointly satisfiable
  only by measuring the actual serialised form per emit. Asserted with a shadowed run at the boundary.
  `[G7:1b]` **The boundary path count is a function of the identifiers, so the unshadowed control MUST be
  driven under identifiers the boundary was computed FROM.** v7's RED derived `n_boundary` from
  `run_id="run-l0-3d3-shadow"` / `phase="wf-l0-3d3-shadow"` (measured: `n_boundary == 114`, unshadowed
  serialised size **4092 B**, only **4 B** of slack under the limit) and then re-ran that same path count
  under `f"{run_id}-unshadowed"` / `f"{phase}-unshadowed"` — `+22 B` — so the event the control actually
  appends is **4114 B, 18 B OVER the 4096 limit**. That made the control **unsatisfiable for every
  implementation**: a GREEN that truncates fails the `written_truncated is not True` assertion, and a GREEN
  that does not truncate raises `EventLogLineTooLarge`, which `_emit` swallows at `engine.py:710`, so no
  event is written and the `exactly one phase_artifacts` assertion fails instead. No third branch exists —
  a false-RED that survives a fully correct GREEN, introduced by the round-7 rewrite whose whole purpose
  was to remove this shape from this AC. Normative: either give the unshadowed control identifiers of
  **identical length**, or **recompute the boundary per run from the identifiers actually used** — the
  latter is preferred, being robust to any future rename. The 4-byte slack is the lesson: with this AC's
  envelope, an 11-character identifier change is three times the entire margin.
- **AC-L0-12** `[G:edge-3]` A shadowed run (`HAL_ENGINE_SHADOW_EMITS` on, non-authoritative
  execution, every event mangled to `SHADOW_EVENT_TYPE` at `engine.py:699-708`) MUST be reported
  as a distinct `E_SHADOWED_RUN` violation, not as a generic pile of R0.2/R0.3 failures. A
  shadowed log is out of BD-L0's scope by construction and must say so rather than look like a
  non-conformant host.
- **AC-L0-12b** `[G2:2]` A shadowed run's `requirements` MUST be `"not-checked"` for **all three**
  requirements, and `passed` MUST be `False`. v2 pinned only `violations` and left `requirements`
  at whatever default the GREEN chose — a `"passed"` default plus AC-A17 reading only
  `requirements` attests a shadowed run as `BD-L0`. Pinning both halves is what makes AC-A18's
  independent-signal rule reachable from a real run rather than only from a synthetic report.
- **AC-L0-12c** `[G2:edge-2]` A log containing shadow events **and** a real authoritative run under
  the same `run_id` MUST still surface the real run's genuine R0.2/R0.3 violations; the shadow
  branch MUST NOT mask them.
- **AC-L0-12d** `[G3:MINOR-4]` **`run_id` scoping is functionally asserted.** Every v3 checker test
  passed a `run_id` matching every event, so a GREEN ignoring `run_id` passed all 77 tests while
  the `[G:edge-4]` scenario it exists for produces a false PASS. Required: a log holding run A
  (missing `run_identity`) and run B (complete) under **different** `run_id`s — checking run A MUST
  fail R0.3, i.e. B's identity must not satisfy A, and B's `phase_artifacts` for a same-named phase
  must not trip A's "exactly one per phase". `[G4:MINOR-3]` The duplicate-guard half MUST be asserted
  as `not any(v.startswith("R0.2") for v in report_a.violations)` — run A is R0.2-clean by
  construction — not by matching violation prose (`"second" in v.lower()`), which passes whenever a
  GREEN words its message differently even if the guard *did* wrongly trip.
- **AC-L0-12e** `[G4:EDGE-5]` **Two `phase_artifacts` for one phase under the SAME `run_id`.** This is
  the scenario `run_id` scoping exists for (`event_log.py:82-88`: multiple appending processes), yet
  v4 left the checker's behaviour on it undefined — AC-L0-12d covers only *different* `run_id`s.
  **Normative: the checker cannot distinguish an interleaved co-writer from a real double emit, so it
  MUST fail-closed and report it as the AC-L0-9-clause-5 R0.2 violation.** Recorded so the choice is
  a decision rather than an accident.
- **AC-L0-13** `[G2:edge-8]` "Exactly one `phase_artifacts` **per phase**" MUST be asserted with a
  run containing **two** phases. Every v2 fixture had a single `workflow_started`/`workflow_finished`
  pair, so a checker keyed on "exactly one per run" passed every test.
- **AC-L0-14** `[G2:8]` The checker MUST enforce the AC-L0-2b **shape** of `adapter_identity` — a
  mapping with non-empty `backend` and `source` — not merely non-emptiness. The v2 fixture used a
  bare string, which pinned the checker to a weaker contract than the engine emits.
- **AC-L0-15** `[G7:EDGE-1]` **An EMPTY SCOPE MUST NOT vacuously pass.** This is the cheapest false-green
  left in the checker, and the one `all([])` shape this lot had not yet closed. AC-L0-9's vacuity guard is
  `check_bd_l0([], ...)` — the *empty log*. Nothing exercised a **populated** log scoped to a `run_id`
  matching **nothing**. The natural implementation, `scoped = [e for e in events if e["run_id"] == run_id]`
  guarded by `if not events: fail`, then evaluates every universal R0.2/R0.3 clause over an empty `scoped`
  set — vacuously true — and reports all three requirements `"passed"`, `level_achieved: "BD-L0"`,
  `conformant: true` for a run that produced **no evidence whatsoever**. Rounds 4-7 chased `all([])` down
  the collection ladder; this is the same defect one scope out, over the event set itself. Normative: a
  scope containing no events for the requested `run_id` MUST yield `passed is False` with all three
  requirements `"not-checked"` (nothing was observed, so nothing was measured — this is not a structural
  breach), and MUST NOT grant a level. Asserted with a log holding a complete, *passing* run under a
  different `run_id`, so the fixture also proves the checker is not merely reading the whole log.
- **AC-L0-3d4** `[G7:EDGE-3]` **The TRUNCATED payload MUST itself fit under the limit.** AC-L0-3d bounds the
  sample by count, not by bytes: every fixture uses many short paths, so sample + digest always fit. A phase
  writing one pathological path (a single ~4200-character filename, legal on most filesystems in aggregate
  path terms and trivially forgeable in a supplied log) produces a *truncated* payload that **still**
  exceeds 4096, so `append` raises `EventLogLineTooLarge`, `_emit` swallows it at `engine.py:710`, and the
  record vanishes entirely — the inverted control AC-L0-3d exists to prevent, reopened through AC-L0-3d's
  own escape hatch. Normative: the truncation predicate MUST bound the **serialised** payload, dropping or
  eliding sample entries until it fits (the digest, being fixed-width, is what survives); an emitted
  `phase_artifacts` MUST NEVER exceed the limit. Asserted with a single oversized path and by requiring
  exactly one `phase_artifacts` with `written_truncated: true` to be present in the log.
- **AC-L0-3d5** `[G7:EDGE-4]` **The limit is `> 4096`, not `>= 4096`.** `event_log.py:116` rejects only
  strictly-greater, so a 4096-byte line is **legal**. AC-L0-3d2's search finds the largest count whose
  predicted size is `<= 4096` and lands at 4078 bytes for its identifiers — 18 short — so a GREEN using
  `>= 4096` truncates a payload that would have fit exactly and still passes AC-L0-3d2. Normative: a payload
  serialising to **exactly** 4096 bytes MUST NOT be truncated. Asserted by constructing that exact size
  (pad a path name to hit it precisely) and requiring `written_truncated is not True` plus a successfully
  appended event. The boundary must be hit exactly, not approached.
- **AC-L0-6g** `[G7:EDGE-6]` **A `writer` whose `append` RAISES MUST NOT be rendered as R0.1 `"passed"`.**
  AC-L0-6c supplies writers with wrong `os.open` flags; none that raises. `check_bd_l0` is documented as a
  read-only check that runs ~20× per suite, so an unhandled exception from a caller-supplied `writer` makes
  the checker a crash surface — and the reflex fix, a bare `except Exception: pass` around the probe, leaves
  R0.1 at its default and publishes `"passed"` for a probe that never ran. Both failure modes are
  unacceptable and the AC pins both directions: `check_bd_l0` MUST NOT propagate the exception, **and** MUST
  render R0.1 `"not-checked"` (with a violation naming R0.1) — never `"passed"`. Asserted with a writer
  whose `append` raises unconditionally.
- **AC-L0-3b7** `[G7:EDGE-7]` **A `phase_artifacts` preceding its own `workflow_started` is defined, not
  left to GREEN.** AC-L0-3b6 defines the *absent* `workflow_started` case as `"failed"`; a record appearing
  **before** the `workflow_started` for the same phase in the scoped run is undefined, so a streaming
  implementation calls it an orphan (`"failed"`) and a pre-indexing one calls it valid. Neither is a
  false-green, which is exactly why it must be decided now rather than becoming a round-8 finding.
  Normative: the checker indexes the whole scoped run before reducing — **event order within a scope carries
  no meaning beyond AC-L0-2's "immediately follows `workflow_started`" positional rule** — so a
  `phase_artifacts` whose phase HAS a `workflow_started` anywhere in the scope is valid regardless of
  relative order. Asserted with the record moved ahead of its `workflow_started`: R0.2 MUST still be
  `"passed"`.

`[G7:EDGE-5]` **Single-threaded `execute()` is now declared, not assumed.** AC-L0-3b3 forces the write
accumulator onto the engine **instance** (a per-frame accumulator loses the pre-retry write), and AC-L0-3f
asserts reset only across **sequential** `execute()` calls. Two threads calling `execute()` on one engine
instance would interleave into one accumulator, so run B's `phase_artifacts.written` could carry run A's
paths — AC-L0-3f's defect through the door AC-L0-3b3 was required to open. `EventLog` supports concurrent
*appenders* (`event_log.py:82-88`), which is what invites the reading. Normative for this lot: **one
`WorkflowEngine` instance is single-threaded with respect to `execute()`**; concurrent `execute()` on one
instance is unsupported and out of scope. Declared rather than asserted because the fix (thread-local or
per-run accumulator keyed by `run_id`) is a design change this lot does not otherwise need, and an undeclared
assumption is what this lot keeps getting rejected for. **Re-open criterion:** the first consumer that calls
`execute()` concurrently on a shared instance.

`[G7:EDGE-2]` **Recorded, deliberately not fixed: git's `core.quotePath` defeats the `written` spelling for
non-ASCII names.** With the default `core.quotePath=true`, `git ls-files --others --exclude-standard`
(`engine.py:1079`) emits a non-ASCII or control-character filename as a C-quoted, backslash-escaped token
(`"sub/\303\251.txt"`). AC-L0-3b4 pins POSIX-relpath spelling over ASCII names only, so `written` would carry
the escaped spelling and `written_digest` would not be reproducible from the real path set — AC-L0-3d's "the
digest is the sole evidence the elided list was real" defeated by a filename. Not fixed here because the
correct fix (pass `-z`, or set `core.quotePath=false`, and decide whether the digest is over bytes or
`str`) changes the delta-reading seam that AC-L0-3a4 pins, and this lot's write channel is measured against
ASCII fixtures throughout. **Re-open criterion:** the first non-ASCII path in a measured repo, or any use of
`written_digest` as a cross-host comparison key. One fixture with a non-ASCII name settles whether the
engine unquotes or the spec narrows.

### 4.2b Round-7 minor pins

Each closes a MUST that no test currently discriminates, or a token left unpinned at one level while pinned
at another. None admits a wrong GREEN on its own; all four are cheap, and unasserted normative text is what
this lot has been rejected for six times.

- **`[G7:MINOR-3]` The distribution name MUST be asserted against `package_meta.PACKAGE_DIST_NAME`, not the
  bare literal.** `[G5:MINOR-7]` makes it normative that the name come from that constant "**not** the bare
  literal `"bytedigger-engine"`", yet both tests touching it assert the literal — and since
  `PACKAGE_DIST_NAME == "bytedigger-engine"` (`package_meta.py:16`) the assertion **cannot discriminate**,
  so the anti-drift MUST has zero coverage. Assert equality against the imported constant, so a rename that
  updates one and not the other fails.
- **`[G7:MINOR-4]` The key set of a SUCCESSFUL UNTRUNCATED `git-delta` payload MUST be pinned.** AC-L0-3's
  whole-dict equality covers only the `not-observed` 5-key case; AC-L0-3d's exact-key-set assertion covers
  only the truncated 8-key case. Nothing pins the untruncated success case, so a GREEN that **always** emits
  `written_count`/`written_digest` passes everything — while AC-L0-3g's docstring claims to be the
  whole-dict-shape control and asserts two keys. Normative: the untruncated `git-delta` payload's key set is
  exactly the five of AC-L0-3 (`phase`, `written`, `read`, `write_tracking`, `read_tracking`); the
  truncation keys appear **iff** truncation occurred. Asserted by exact key-set equality.
- **`[G7:MINOR-6]` The phase-level R0.2 token MUST be pinned, not just the quantifier.** AC-L0-3a5(4)
  asserts `passed is False` plus an R0.2 violation when phase_b's `phase` key is stripped, but not
  `requirements["R0.2"] == "failed"` — which AC-L0-9 clause 1 pins for the **identical** mutation on a
  single-phase log. The quantifier is asserted, the token is not, so a GREEN rendering the phase-level
  breach as `"not-checked"` passes here and fails clause 1 only by luck of which fixture it meets.
  Normative: the clause-1 token rule applies per phase, and `[G6:MINOR-2]`'s precedence (`"failed"`
  dominates `"not-checked"`) resolves it.
- **`[G7:MINOR-2]` Recorded policy, deliberately kept:** a present-but-forged `phase_artifacts` (missing
  fields, wrong types, unrecognised token — AC-L0-3a3, AC-L0-3b5) renders R0.2 `"not-checked"`, while
  `[G5:MINOR-4]` renders a *structural* breach `"failed"` and AC-L0-3b6 renders the orphan record
  `"failed"`. The scoping sentence limits `[G5:MINOR-4]` to AC-L0-9 clauses 1-6/8, so there is **no trap** —
  but the consequence is real and is accepted knowingly: because §3.1's schema carries `l0` and **not**
  `violations`, the reviewer-facing artifact will read "we did not measure the write channel" for a log that
  was demonstrably forged. That is AC-A24's defect with the polarity reversed. It is kept because
  fail-closed-to-unmeasured is the safe direction for a *level grant* (it can never grant BD-L0), and
  because the alternative — calling a forged record a measured failure — asserts we measured something we
  did not. **Re-open criterion:** the first version of §3.1's schema that carries `violations`, at which
  point the distinction becomes visible to the reviewer and should be made.

## 4.3 Quantifier sweep `[G6:quant]`

Every quantified requirement in this spec, audited against the `[G6:quant]` rule.

`[G7:2]` **v7's version of this table was itself an instance of the defect it audits.** It carried 13 rows
and the sentence "No quantifier in this spec is now asserted only over a uniform collection" — and it
**omitted three quantifiers**, two of which were in fact asserted only uniformly and admitted a wrong GREEN
(AC-L0-6e, AC-L0-3b5), the third being the rung above phase→run (scoping over the runs of a log). A
completeness claim over a hand-built list is only as good as the enumeration, and round 8 would have
trusted this sentence. The three missing rows are added below and marked `[G7]`. The audit's *claims about
the tests it cites* were accurate in all 13 original rows — the defect was omission, not misstatement.

| Quantifier | Collection | Non-uniform ≥2 fixture | Positive control |
|---|---|---|---|
| `write_tracking` per step | steps of a phase | AC-L0-3a4: step 2's delta forced to fail | AC-L0-3a git half |
| `written` union | steps of a phase | AC-L0-3b2(2): step 1 writes, step 2 does not | AC-L0-3b2(1) |
| `written` across recursion | frames of a phase | AC-L0-3b3: pre-retry step writes | AC-L0-3b2(1) |
| zero-step vacuity | steps (empty) | AC-L0-3c 2/2 with `git_cwd` | AC-L0-3a git half |
| **`write_tracking` per phase** | **phases of a run** | **AC-L0-3a5(1): phase_b `not-observed`** | **uniform two-phase** |
| **`phase` attribution per phase** | **phases of a run** | **AC-L0-3a5(4): phase_b's stripped** | **uniform two-phase** |
| **outcome per phase** | **phases of a run** | **AC-L0-3a5(4): phase_b's removed** | **uniform two-phase** |
| exactly one `phase_artifacts` | phases of a run | AC-L0-13 (phase_b's dropped), AC-L0-9 cl.5 | AC-L0-13 baseline |
| adversary status → level | required adversary set | AC-A11: ADV-4 `failed` among passed | AC-A11 control |
| `requirements` → level | the three requirements | AC-L0-6b2: each non-`passed` in turn | AC-L0-11 |
| `l0.passed` / `.violations` | the two signals | AC-A18: each independently | both cleared |
| file membership | files of a freeze | AC-F1/F2/F3/F6 | AC-F5 reorder |
| `labels["R0.2"]` | phases of a run | AC-L0-3a5(2) via `requirements` | AC-A7 |
| `[G7]` **zero-step precondition per phase** | **phases of a run** | **AC-L0-6e `[G7:2a]`: phase_b's step events dropped** | **uniform two-phase** |
| `[G7]` **artifact-field validity per record** | **phases of a run** | **AC-L0-3b5 `[G7:2b]`: phase_b's record malformed** | **well-formed two-phase** |
| `[G7]` **scoping isolation per run** | **runs of a log** | **AC-L0-12d: run A lacks identity, run B complete** | **AC-L0-12d's `report_b`** |

Rows in **bold** are new in v7 (rows 5-7) or v9 (the three `[G7]` rows). Rows 5-7 are what `[G6:1]`
required; the `[G7]` rows are what `[G7:2]` required.

Two standing cross-references, so the next round does not mistake either for a gap:
- **AC-L0-2d is uniform by design and that is acceptable only because AC-L0-12d exists.** AC-L0-2d asserts
  both runs report R0.3 `"passed"`, which kills the wrong GREEN it targets (an instance-level
  "already emitted" flag never reset per run leaves run 2 without identity). It does **not** kill an
  unscoped checker that credits run A's `run_identity` to run B — AC-L0-12d does, on the non-uniform
  fixture. Neither AC is redundant; removing 12d would silently un-assert the collection.
- **Row 4 (zero-step vacuity) has no non-uniform member by construction** — the collection is empty. Its
  discrimination comes entirely from the positive control plus AC-L0-6e's per-phase row above.

| `[G7:MINOR-7]` **relpath spelling per entry** | **entries of `written`** | **AC-L0-3b4: root / `sub/` / `sub/dir/` in one phase** | **all three spellings exact** |
| `[G7:4]` **universal clauses per event set** | **events of a scope** | **AC-L0-15: scope matching nothing, log holds a passing run** | **same log under its real `run_id`** |
| `[G7:4]` manifest membership | entries of `core_manifest.json` | AC-P2 (absence; no violating member possible) | AC-P1's include list |

### The completeness claim, and why the instrument changed `[G7:4]`

v9 stated completeness as a hand-built enumeration of collections, with an explicit invitation to falsify
it. **It was falsified within the same version, by an AC added in that same version:** AC-L0-15 quantifies
every universal R0.2/R0.3 clause over *the events of a scope* — that is its entire subject, `all([])` one
scope out — and neither the collection nor the row was present. Two further gaps came with it (`written`'s
entries, `core_manifest`'s entries), all three now rows above.

That is the **third consecutive round in which the audit artifact carried the defect it audits**: v7's table
claimed completeness while omitting three quantifiers (`[G7:2]`), and v9's replacement — written expressly to
fix that, with a falsification invitation attached — omitted one from its own version. The missing rows are
trivial. The pattern is the finding, and it says the instrument is wrong, not the wording: **a global
enumeration is a claim no reviewer can check without redoing it, and its author is the person least able to
see what they left out.** Restating it more humbly does not fix that; three rounds of evidence say so.

So the burden is inverted, from one global claim to a **per-AC obligation that is checkable where it is
written**:

> **Normative `[G7:4]`.** Any AC whose requirement is universally quantified — any AC whose text turns on
> *every*, *each*, *all*, *per phase*, *per step*, *per record*, *per run*, or an implicit reduction over a
> collection — MUST name its collection and cite its `[G6:quant]` fixture **in its own text**. An AC that
> does not is **unasserted**, exactly as an AC asserted only over a uniform collection is unasserted. §4.3 is
> then a convenience index over those per-AC citations, not the authority: if the table and an AC disagree,
> **the AC governs**, and a missing row is a table bug rather than a silent hole in coverage.

The difference is that a reviewer auditing one AC can now settle the question by reading that AC, and a new
AC arrives with its quantifier obligation attached rather than needing an editor to remember to extend a
global list. The rows above remain as the index; they are no longer the claim.

Nothing in this design quantifies over multiple log files, multiple hosts, or multiple `L0Report`s
(`build_attestation_report` takes exactly one). That statement is now a **narrow negative about three
specific things I checked**, not a completeness claim about a list — the ladder's top per `[G7:3]`, stated
where it can be checked rather than trusted.

## 5. Out of scope

ADV-1…ADV-10 and everything they prove. Read instrumentation. Signing. §7.

`[G6:EDGE-2]` **Recorded, deliberately not fixed in this lot: the attestation's producer identity is not
bound to the evidence it attests.** `build_attestation_report` takes `engine_version`, `run_id` and `commit`
as free arguments beside an `L0Report` derived from some log, and nothing cross-checks that they describe the
same run — a caller may publish `run_id: "run-X"` over an `l0` measured from `run-Y`. AC-A8 pins the *echo*,
never the *binding*. v1 has no runner CLI, so there is no component whose job this is yet; the fields are
fed by whoever calls the writer. **Re-open criterion:** the first conformance-runner entry point, which
should derive all three from the same event log it checks rather than accept them as arguments. Written down
because an unbound provenance triple is exactly the kind of gap that reads as measured once it is published,
and §8's justification for shipping unsigned rests on that triple.

## 6. Non-regression

- **AC-P1** `[G:MINOR-9]` `pyproject.toml [tool.setuptools.packages.find] include` (line 104,
  currently `["lib*","workflows*","security*","scripts*"]`) MUST gain `"conformance*"`, asserted
  by a test reading the manifest — a wheel shipped without the package would fail silently.
- **AC-P2** `[G4:EDGE-1]` (**pre-passing at RED time — an absence-shield, declared as such**
  `[G5:MINOR-6]`; its discriminating power is post-GREEN: a GREEN that adds the entry fails it)
  **The manifest exclusion is a test, not an ops note.** §1 makes "`conformance/`
  is NOT in `core_manifest.json`" a load-bearing design claim — it is what keeps `extra_bd` at zero —
  yet nothing stopped GREEN from adding it, and the only check was a manual `bd-drift-check.py` run.
  Assert that `engine_py/core_manifest.json` contains no `conformance` entry. AC-P1 asserts the
  opposite direction (the `pyproject` include) and does not cover this.
- `bd-drift-check.py` MUST still report its pre-lot baseline with `extra_bd == 0` — **measured on the
  running host, not inherited.** The `5 drift / 0 / 0 / 31 skipped / 490 identical` figure in the
  handoff is macstudio's `~/.claude` state; the same commit measures `7 / 0 / 0 / 31 / 488` on the
  laptop, and `main` and `lot-bd7` measure byte-identically there. The invariant is "identical to this
  host's own `main` baseline, `extra_bd == 0`", not a literal count.
- Full engine suite delta against the `main` baseline MUST be zero new failures. Baseline is
  **host-measured**: `0 failed / 4134 passed / 6 skipped` on this laptop at `606ab58` (the handoff's
  `4 failed / 4131 / 5` was macstudio-environment-specific — all four of those tests pass here, so
  there is no pre-existing failure to hide a regression behind).
