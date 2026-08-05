# bd#59 — enforcement: the measurement found refusals already in place, but with no link to a verdict

**Class:** a duplicate guard. #59 was filed as "code with no emitter, a verdict with no
consequence". The exposure measurement — mandatory per the issue BEFORE the RED — showed that the
consequence **already exists for all three observable requirements**, and that building "wire the L2
verdicts into the phases" would mean placing **a second guard on an already guarded condition**.
That is exactly the defect I found in bd#29 §5a: two guards on the hard-gate path, where the
later one overwrites the earlier one's code.

**Chokepoint:** the correspondence "BD-L2 requirement → production refusal", which today does not
exist in any form. That is what gets built: `conformance/bd_l2.ENFORCEMENT`.

---

## §1. §1b live base — both corpora, `0dfa549`

| corpus | result |
|---|---|
| **pytest** | **5442 passed / 47 skipped / 1 xfailed / 0 failed**, 353 s |
| **clean-room suite** | **5355 passed / 69 skipped / 1 xfailed / 0 failed**, 209 s, `PASS` |

## §2. EXPOSURE MEASUREMENT — as a number, as the issue demands

For each **observable** requirement (R2.1, R2.2, R2.6 after bd#61):

| requirement | a production refusal exists | code | sites in `phase_5_implement` | enforcement |
|---|---|---|---|---|
| **R2.2** vacuous oracle | **YES, terminal** | `E_RED_STUB_PASSABLE`, `recoverable=False` | **6** | the gate `HAL_STUB_PASSABILITY_GATE` **default=1** — on |
| **R2.1** zero collection | **YES, but soft** | `E_RED_COLLECT_PROBE`, `recoverable=True` | **3** | `HAL_RED_COLLECT_PROBE_ENFORCE` **default=0** — a warning |
| **R2.6** delta against the baseline | **YES, but disabled** | `would_block` / `BLOCKED` in `_baseline_delta` | 0 (lives in its own module) | `HAL_BASELINE_DELTA_ENFORCE` **default=0** |

**The measurement's conclusion: the engine is not short of refusals — it is short of them being
ENABLED and of the link "verdict ⇄ refusal" being declared.**

The exposure "how many steps would fail today" per code:
- R2.2 — **already fails** (the gate is on by default); enforcement would add zero;
- R2.1 — everything that fails to collect would fail instead of warning;
- R2.6 — everything with an uncovered delta would fail.

For R2.1/R2.6 the exact number of runs is **unmeasurable on this host**: it needs a history of
real engine runs, which the repository does not have. The issue forbids an estimate in place of a
number, so the number is **not substituted** — the missing instrument is named instead.

## §3. Decisions

**D1 — we do NOT place a second guard.** Wiring an L2 verdict into a phase would give a refusal on top of
an existing refusal on the same condition. The price is known by name from bd#29 §5a: the later
guard overwrites the earlier one's code, and the caller receives the wrong cause.

**D2 — what is built is what genuinely does not exist: a DECLARED correspondence.**
`bd_l2.ENFORCEMENT` — a table "requirement → (refusal code, enforcement flag, whether it is on
by default)". Today the link exists only in the reader's head.

**D3 — a gate against inertness.** A requirement declared observable must have an
entry in `ENFORCEMENT`; the entry must name an **existing** code from the `error_codes`
registry and an **existing** flag from `flags_catalog`. A reference to a non-existent code
or flag fails the gate. That is the layer whose absence would have allowed "wiring the
verdicts" blind.

**D4 — disabled flags are DECLARED, not silently accepted.** `ENFORCEMENT` carries
`enforced_by_default`, and the `check_bd_l2` report shows a requirement whose refusal
exists but is disabled **differently from** a requirement with no refusal at all. Today they both
look the same.

**D5 — no flag is flipped by this lot.** Flipping `HAL_RED_COLLECT_PROBE_ENFORCE` or
`HAL_BASELINE_DELTA_ENFORCE` is production behaviour with an exposure that cannot be measured without
a run history (§2). That is a separate subject, and it is named rather than done silently.

## §4. Scope

**Edited:** `conformance/bd_l2.py` — `ENFORCEMENT`, `__all__`, the report labels.
**New:** `tests/test_bd59_enforcement_map.py`.
**§1v — NOT in scope:** the engine phases (there is no second guard — D1); the flag values (D5);
`stub_passability`, `known_reds_ledger`, `_baseline_delta`, `bd_l3`, `harness`.

## §5. §1a Sibling audit

`test_bd9_l2_falsifiable_oracle.py`, `test_bd59_l2_observation_contract.py`,
`test_bd61_observation_producers.py`, `test_bd58_adversary_harness.py`,
`test_bd28_bd_l3_checker.py`, the `error_codes` and `flags_catalog` tests. To be checked by a run.

## §6. Acceptance criteria

- **AC1 (THE GATE: the observable must be bound).** Every requirement outside
  `AWAITING_PRODUCER` has an entry in `ENFORCEMENT`.
- **AC2 (NEGATIVE: the entry must be real).** The refusal code from the entry
  exists in `error_codes.ERROR_CODES`; the flag exists in `flags_catalog`. A reference to
  an invented name fails the gate — that is exactly the error I made in bd#9 with the
  events and in bd#59 with the keys.
- **AC3 (NEGATIVE: the registry does not outrun reality).** A requirement standing in
  `AWAITING_PRODUCER` has **no** entry in `ENFORCEMENT` — one cannot declare a
  consequence for something one does not observe.
- **AC4 (three states are distinguishable).** The report distinguishes: the refusal is on · the refusal exists but is
  off · there is no refusal. Three different labels for three different states.
- **AC5 (NEGATIVE: a disabled refusal ≠ an enabled one).** A requirement with
  `enforced_by_default=False` has no right to look enforced.
- **AC6 (the measurement is pinned).** R2.2 is declared enforced by default; R2.1 and R2.6 are
  not. If a flag's value in the catalogue changes, the AC fails and demands a decision.
- **AC7 (surface = `__all__`).**

## §7. What the PR does NOT claim

- **It does not enable enforcement** for R2.1/R2.6 (D5) — the exposure is unmeasurable here.
- It places no second guard on any condition (D1).
- It does not make R2.3/R2.4/R2.5 observable.
