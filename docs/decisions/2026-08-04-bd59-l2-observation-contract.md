# bd#59 — an exposure measurement stopped enforcement: the observations are not produced

**Class:** an observation with no producer. #59 was filed as "a verdict with no consequence"
(a code is registered but affects nothing). The exposure measurement, which the issue itself
declared mandatory BEFORE the RED, showed the defect is **one level deeper**: five of the six
BD-L2 requirements lack not only a consequence but the **observation** — nobody emits the events
`bd_l2` reads. Enforcement built today would be inert on
**6/6** requirements.

**Chokepoint:** `conformance/bd_l2._EVENT_FOR` — the contract "requirement → the event
carrying its observation". Today it references names that do not exist in production.

---

## §1. §1b live base — both corpora, on `1a82f8c`

| corpus | result |
|---|---|
| **pytest** | **5428 passed / 47 skipped / 1 xfailed / 0 failed**, 352 s |
| **clean-room suite** | **5341 passed / 69 skipped / 1 xfailed / 0 failed**, 204 s, `PASS` |

## §2. EXPOSURE MEASUREMENT — the issue's mandatory condition, met as a number

| requirement | the event `bd_l2` waits for | production emitters | payload matches |
|---|---|---|---|
| R2.1 | `red_test_outcome` | **1** (`phase_5_implement.py:2635`) | **NO** — no `counted_as` key, and the discriminator rests on precisely that |
| R2.2 | `oracle_vacuity_scan` | **0** | — |
| R2.3 | `acceptance_criteria_declared` | **0** | — |
| R2.4 | `gate_decision` | **0** — in the tree that is a **payload key** (`"gate_decision": "fail-closed"`), not an event type | — |
| R2.5 | `known_reds_ledger_scan` | **0** | — |
| R2.6 | `baseline_delta_gate_verdict` | **1** (`_baseline_delta.py:92`) | **NO** — `verdict`/`new_fails`/`enforced` are emitted, while `scoped_result`/`full_suite_delta` are read |

**The measurement's conclusion: on a real log `bd_l2` issues NOT A SINGLE verdict.** All six
requirements are permanently `not-checked`.

**A control (the same check on a neighbour):** `bd_l3` reads `model_invocation_attested` — the
emitter is real (`llm_subprocess.py`), and the keys (`observed_model`,
`declared_capabilities`, `observed_tools`) are real. So the measurement method is sound, and the
difference between L2 and L3 is genuine, not an artefact of the instrument.

## §3. ★★★THIS IS AGAINST MY OWN bd#9

I wrote `_EVENT_FOR` from names **I invented myself in the fixtures** of the bd#9 RED. All
sixteen ACs passed because every fixture was synthetic: the checker
adjudicated exactly the shape I was feeding it. The negative legs there are honest
— they prove the function can say NO — but **no AC bound `bd_l2`
to real production emission**. That is the §1l anchor the lot did not have.

The form of the error is the same one I have been catching in others all day and have caught twice in
myself (a port against the wrong corpus, `no tests ran`, a `diff` against an empty file): **the instrument measured a corpus
that does not exist, and took silence for health.**

## §4. The consequence for #59 — enforcement is NOT BUILT, and that is an answer, not an excuse

Issue #59 demands a measurement BEFORE the RED and forbids an estimate in place of a number. The number is in, and it
says: a verdict's consequence is meaningless while the verdict does not exist. Building a
refusal now would mean adding a production path that never fires — that is,
reproducing the class declared as the subject of #59, one layer down.

**So the lot does what unblocks #59 and does not do enforcement:**
it brings the observation contract into line with what is actually emitted and installs a gate
that will not let the divergence return.

## §5. Scope

**Edited**
- `conformance/bd_l2.py` — `_EVENT_FOR` and the R2.1/R2.6 predicates are retargeted onto
  the **real** event types and the **real** payload keys.
- Requirements with no producer (R2.2, R2.3, R2.4, R2.5) receive a
  **declared** status "awaiting a producer" — as `"R3.3": "in-session-warn-only"`
  was an honest label before bd#29. The silence here is itself the defect.

**New**
- `tests/test_bd59_l2_observation_contract.py` — the RED, including a **gate against recurrence**.

**§1v — NOT in scope**
- Writing the missing producers (`oracle_vacuity_scan` and the rest) — that is an edit to
  production phases, its own subject and its own exposure. Filed as a separate issue.
- Enforcement itself (#59 in its original framing) — it stays open and is unblocked by
  the producers.
- `bd_l3`, `harness`, `oracle`, `attest` — untouched.

## §6. §1a Sibling audit

`test_bd9_l2_falsifiable_oracle.py` (**direct risk** — its fixtures are the source
of the divergence), `test_bd58_adversary_harness.py` (the ADV-4/5/6 probes feed `bd_l2`
synthetics), `test_bd28_bd_l3_checker.py`, `test_bd8_l1_oracle.py`,
`test_bd27_oracle.py`, `test_bd22_contracts.py`, `test_contracts.py`. Existence
to be checked by a run.

## §7. Acceptance criteria

- **AC1 (THE GATE AGAINST RECURRENCE, the main one).** Every event type in `bd_l2._EVENT_FOR`
  declared observable must have a **producer in production code**
  (`bytedigger_engine/**`, outside `conformance/`). A requirement with no producer must
  be **declared** in the registry of the awaiting rather than stay silent. This is the AC whose absence
  let the defect through in bd#9.
- **AC2 (R2.1 on a real payload).** An event of the shape emitted by
  `phase_5_implement.py:2635` (**without** `counted_as`) must be adjudicated: a run
  that collected zero tests is not a refusal.
- **AC3 (NEGATIVE, R2.1).** A real payload with `n_failed>=1` ⇒ `passed`, not
  `failed`. Otherwise a "fix" fails everything indiscriminately.
- **AC4 (R2.6 on a real payload).** An event of the `_baseline_delta.py:92` shape (`verdict`,
  `new_fails`, `enforced`) is adjudicated; the absence of a full-suite delta is not
  passed off as its presence.
- **AC5 (NEGATIVE, R2.6).** A real payload with a computed delta ⇒ `passed`.
- **AC6 (a declared gap, both sides).** Requirements with no producer yield
  `not-checked` **and** are listed in the declared registry of the awaiting; a requirement that
  has acquired a producer must disappear from the registry.
- **AC7 (surface = `__all__`).** B-2, the form is preserved.
- **AC8 (the neighbours are not broken).** `bd_l2` continues to adjudicate the synthetic shapes
  of bd#9/bd#58 where they coincide with the real ones; the divergences are declared.

## §8. What the PR does NOT claim

- **It does not claim BD-L2 is observable on a real log.** After the lot, R2.1 and R2.6
  are observable; the other four are declared as awaiting a producer.
- It does not build enforcement (#59 stays open).
- It does not rewrite bd#9 as a lot: its negative legs are correct, what was missing was the §1l anchor,
  and that anchor is added here.
