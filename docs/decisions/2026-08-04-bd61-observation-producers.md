# bd#61 — BD-L2 observation producers

**Class:** an absence of evidence indistinguishable from evidence of absence. The vacuity
scan **already runs in production** and emits an event — but **only on a violation**. A
clean scan writes nothing, so "checked and clean" is indistinguishable from "never
checked". While that pair is indistinguishable, the `passed` verdict is unreachable by construction, and
enforcement (#59) stays inert regardless of its quality.

**Chokepoint:** `workflows/phase_5_implement.py`, the gate branch
`HAL_STUB_PASSABILITY_GATE` (`:3062`), where `scan_stub_passability` is called and where
`red_stub_passability_violation` is emitted today (`:3079`) — the only place where the
vacuity observation arises.

---

## §1. §1b live base — both corpora, `40d9d00`

| corpus | result |
|---|---|
| **pytest** | **5435 passed / 47 skipped / 1 xfailed / 0 failed**, 397 s |
| **clean-room suite** | **5348 passed / 69 skipped / 1 xfailed / 0 failed**, 230 s, `PASS` |

## §2. MEASUREMENT — which of the four requirements is observable in the engine at all

| requirement | primitive | called in production | observation today |
|---|---|---|---|
| **R2.2** | `stub_passability.scan_stub_passability` | **YES** — `phase_5_implement.py:3070` and `:3179`, under the `HAL_STUB_PASSABILITY_GATE` gate | `red_stub_passability_violation` is emitted, **only if `stub_hits` is non-empty** |
| R2.3 | — | — | the engine does not emit AC declarations at all |
| R2.4 | — | — | not one point where a gate's caught exception would be recorded as an outcome |
| R2.5 | `known_reds_ledger` | **NO** — **zero** production imports; the registry is parsed only by CLIs outside the package. `_baseline_delta.py` attributes `ledgered` from somebody else's JSON but does not classify owner and deadline | none |

⇒ **Exactly one requirement of four is observable, and it is missing half of it: the negative half.**

**Against issue #61, which I wrote myself:** it says "R2.2 — 0 emitters". The measurement is
more precise: the emitter exists but is one-directional. The defect is not the absence of an event but that the
event is written **only on the bad outcome**. Those are different fixes, and the second is cheaper and
more honest than the first.

## §2a. FOUND WHILE EDITING: THE GATE IS DUPLICATED, AND THAT CHANGES THE VOLUME

The stub-passability block exists in the file **TWICE** — in the batch path
(`_collect_red_lint_findings`) and in the legacy sequential one. Discovered because an exact
replacement found two matches instead of one.

The consequence for the lot: **both** must emit, otherwise the observation would depend on which
path ran — that is, the defect would survive halfway and would surface
unpredictably. Both are edited.

The duplication itself (§1g, one canonical source) is **not removed here**: that is an edit to
the structure of phase 5, its own subject with its own check. Named, not hidden.

**Against myself:** the first replacement went through `replace_all` and broke the second site — there a
dependent directed-repair block followed `if stub_hits:` and was left indented under
a condition that had been removed. Caught by `ast.parse` before the run. **The form: when removing a condition, check whether a
body hangs off it.**

## §3. Decisions

**D1 — R2.2 is retargeted onto the REAL name** `red_stub_passability_violation` rather than the
invented `oracle_vacuity_scan`. bd#59 already fixed an error of the same class for
R2.1/R2.6; here it is closed for R2.2.

**D2 — the missing POSITIVE emission is added.** When the gate has run and there are
no violations, the same event is written with an empty `hits`. Without it `passed` is unreachable:
the checker cannot tell a clean scan from one that never happened.

**D3 — the kill switch stays visible.** With the gate disabled, `gate_disabled` is emitted
(it already exists) and there is no observation ⇒ `not-checked`. A disabled gate has no right to
read as "clean" — that is exactly the path by which an absent check masquerades as
a passing one.

**D4 — R2.3, R2.4, R2.5 stay in `AWAITING_PRODUCER`** with a measured reason for
each (§2). For R2.5 the reason is strong: the registry lives outside the package, and pulling it into the engine is
a separate subject with its own exposure, not a side effect of this lot.

## §4. Scope

**Edited**
- `workflows/phase_5_implement.py` — the emission on a clean scan (D2). The gate logic,
  the batch and the error codes are **untouched**.
- `conformance/bd_l2.py` — `_EVENT_FOR["R2.2"]`, the `_r22` predicate, `AWAITING_PRODUCER`.

**New**
- `tests/test_bd61_observation_producers.py` — the RED.

**§1v — NOT in scope**
- `stub_passability.py` — consumed.
- Pulling `known_reds_ledger` into the engine (R2.5) — a separate subject.
- Enforcement (#59) — still next, not this.
- `bd_l3`, `harness`, `oracle`, `attest`.

## §5. §1a Sibling audit

`test_bd9_l2_falsifiable_oracle.py` (the R2.2 fixtures — **direct risk**),
`test_bd59_l2_observation_contract.py` (the AC1 gate against recurrence — **must stay
green and must itself confirm that R2.2 has left the registry**), `test_bd58_adversary_harness.py`
(the ADV-3 probe feeds `bd_l2`), plus the `phase_5_implement` tests around the stub gate.
Existence to be checked by a run.

## §6. Acceptance criteria

- **AC1 (the positive emission exists).** A clean scan under an enabled gate emits
  an event with an empty `hits`.
- **AC2 (NEGATIVE — the violation is still emitted).** A dirty scan emits the same
  event with a non-empty `hits`, and the previous error code/batch are unchanged. Otherwise a telemetry
  "fix" silently lifts the gate.
- **AC3 (distinguishability — the heart of the lot).** `bd_l2` gives `passed` on a clean event,
  `failed` on a dirty one, and `not-checked` when the event is absent. **Three different outcomes for three
  different inputs** — before the lot the first and the third were indistinguishable.
- **AC4 (NEGATIVE — a disabled gate ≠ clean).** With `HAL_STUB_PASSABILITY_GATE=0`
  there is no observation ⇒ `not-checked`, never `passed`.
- **AC5 (the registry empties).** `R2.2` has **left** `AWAITING_PRODUCER`, and the gate against
  recurrence (`test_bd59_*::test_ac1`) confirms it rather than merely not objecting.
- **AC6 (the other three are declared).** `R2.3`, `R2.4`, `R2.5` stay in the registry and yield
  `not-checked`.
- **AC7 (surface = `__all__`).**

## §7. What the PR does NOT claim

- It does not make BD-L2 observable as a whole: after the lot **R2.1, R2.2, R2.6** are observable;
  the other three are declared as awaiting, with a measured reason.
- It does not build enforcement (#59).
- It does not change the stub-passability gate's decision — it only makes its outcome observable in both
  directions.
