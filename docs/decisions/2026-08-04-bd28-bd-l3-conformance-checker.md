# bd#28 — BD-L3 conformance checker + attestation report

**Class:** an oracle that cannot refuse (B-1 of the bd#10 round-1 gate, the
`[G22:18]` form, the inversion of P2 from CL §1). A checker whose `failed` branch is unreachable
is not a weak control but an **inverted** one: it turns an absence of evidence into
a claim of conformance.

**Chokepoint:** the new module `conformance/bd_l3.py` — the only place where the
R3.3/R3.6 verdicts are **computed**. The source of facts is the
`model_invocation_attested` events (`conformance.attest.EVENT_TYPE`) written by
the `_dispatch_backend` chokepoint (bd#10). The requirement labels are consumed from
`conformance.attest.REQUIREMENT_LABELS`, not redeclared.

---

## §1b. Live base — TWO CORPORA, measured BEFORE the freeze

The issue's discipline: the delta is taken **per corpus separately**. On `8548c39`,
on this host:

| corpus | command | result |
|---|---|---|
| **pytest** (`ci.yml`) | `python3 -m pytest tests/ -q -p no:cacheprovider --timeout=120` from `engine_py` | **5336 passed / 47 skipped / 1 xfailed / 0 failed**, 385 s |
| **clean-room suite** (`clean-room.yml`) | `bash scripts/clean-room/run.sh suite 3.11` (docker, `git archive`) | **5250 passed / 68 skipped / 1 xfailed / 0 failed**, 216 s, verdict `PASS` |

**The corpus divergence has grown:** the issue names 11 tests (`test_gh792_native_sentinel_emit.py`
behind `importorskip dbos`) on base `dc6f0d0`; today the difference is **86 passed / 21 skipped**.
On this host `dbos` is installed and in the clean room it is not — but that one file no longer
explains the difference. **Do not derive the cause by reasoning**: both sides of the delta
are taken in THEIR OWN corpus, and a number from the other corpus is not substituted into the report.

## §2. The subject is alive

`engine_py/bytedigger_engine/conformance/bd_l3.py` **does not exist** on `8548c39`
(verified with `ls`). The lot builds it from scratch; `check_bd_l3` in bd#10 would have been a report with no
consumer, which is why it was split off.

## §3. The interface — carried by the SPEC, not by the RED (§1.5 / bd#7 §3.0)

The module `bytedigger_engine/conformance/bd_l3.py`.

```
__all__ = ["REQUIREMENTS", "check_bd_l3", "validate_report"]

REQUIREMENTS: tuple[str, ...] = ("R3.3", "R3.6")

def check_bd_l3(events: "Iterable[Mapping[str, Any]]") -> L0Report: ...
def validate_report(report: L0Report) -> tuple[str, ...]: ...
```

`L0Report` is imported from `conformance.report` and is **NOT extended** — its four
fields are the bd#22 contract (`CONTRACTS_SPEC.md` §2 AC-C2).

The report's `labels` carries `verdict:R3.3` and `verdict:R3.6` with values from
`conformance.tokens` (`REQUIREMENT_PASSED` / `REQUIREMENT_FAILED` /
`REQUIREMENT_NOT_CHECKED`), plus the requirement labels from
`attest.REQUIREMENT_LABELS`.

## §4. The mechanism — fixed HERE, because bd#10's v3 did not fix it

**Aggregation, three branches, per requirement separately (`[bd10:13]`):**
1. `failed` — if **at least one** invocation recorded a violation;
2. else `not-checked` — if **none** carried a non-null observation;
3. else `passed`.

**What constitutes a violation — recomputation from the payload, NOT a recorded verdict flag.**
A recorded flag would let the emitter grade itself (subtype (3) from hal#1373 at
the level of the whole system).
- **R3.3 is violated** when `observed_model` is not `null` **and** its family differs
  from the family of `model_requested`. The family comes from `lib.llm_provider` (`model_family`).
- **R3.6 is violated** when `observed_tools` is not `null`, `declared_capabilities` is not
  `null`, and `attest.capability_escapes(observed_tools, declared_capabilities)`
  returns a non-empty tuple.

**What constitutes a "non-null observation"** (the discriminator between branch 2 and branch 3):
- for R3.3 — `observed_model` is not `null`;
- for R3.6 — `observed_tools` is not `null` **and** `declared_capabilities` is not `null`.

**The input filter.** `check_bd_l3` reads **only** events with
`type == attest.EVENT_TYPE` and ignores the rest. It is fixed separately, because
a real log is heterogeneous, while a homogeneous fixture corpus (the gate's EDGE-8) would let
a GREEN without the filter through.

**The report's `passed`** is true only when **every** requirement in `REQUIREMENTS`
has the verdict `passed`. `not-checked` is **not** `passed` (EDGE-1).

**ADV-9** is recorded as `ADVERSARY_NOT_EXECUTED`; the judge is `validate_report`
(CL:221-224): it returns a tuple of complaint strings, and an empty tuple means the report is
self-consistent.

## §5. Scope

**New files**
- `engine_py/bytedigger_engine/conformance/bd_l3.py`
- `engine_py/tests/test_bd28_bd_l3_checker.py` — the RED

**Edited** — nothing. Not one existing module changes.

**§1v — NOT in scope**
- `L0Report` (the bd#22 contract).
- `attest.py`, `tokens.py`, `report.py` — consumed, not edited.
- The BD-L3 level grant: it requires BD-L0/L1/L2 (bd#8, bd#9, bd#27 are open). This lot
  builds the checker and the report and **grants no level**.
- `_dispatch_backend` and the attestation payload — bd#10.

## §6. §1a Sibling audit

The consumers of what the lot touches (imports, does not edit):

Existence verified on `8548c39` rather than assumed:

| file | link |
|---|---|
| `test_bd10_l3_authorship.py` | `REQUIREMENT_LABELS`, the attestation payload |
| `test_bd22_contracts.py` | `L0Report`, the AC-C2 contract |
| `test_bd24_quant_lint.py` | the export-surface form, the deferred-import discipline |
| `test_contracts.py` | the contracts |

**163 tests in total, all green on the base.** `test_bd29_in_session_pin_fail_closed.py` is NOT in
the list: it arrives with my own PR bd#54, not yet merged.

**★★★Against myself:** the first run of this surface listed the non-existent bd#29
file and returned **`no tests ran`** — pytest on a missing path voids the WHOLE invocation
rather than skipping one argument. A plausible zero turned out once again to be a broken instrument,
not a finding. The form: before running a list of files, verify they exist, and read
any `no tests ran` as an instrument failure.

Run it targeted + **both** full corpora (§1b).

## §7. Acceptance criteria

`conformance.*` imports go **inside test bodies**, never at module level
(the bd#24 discipline: collection stays clean, and the RED fails on an assert/ImportError in the body).

- **AC1 (the `failed` branch, R3.3).** A log with an invocation where `observed_model="haiku"` while
  `model_requested="sonnet"` ⇒ `labels["verdict:R3.3"] == REQUIREMENT_FAILED`,
  `report.passed is False`, `violations` non-empty.
- **AC2 (the `failed` branch, R3.6).** An invocation with `observed_tools=["bash"]` and
  `declared_capabilities=["Read"]` ⇒ `verdict:R3.6 == REQUIREMENT_FAILED`,
  `passed is False`, `violations` non-empty.
- **AC3 (the `passed` branch).** An invocation with a matching family and no escapes ⇒ both
  verdicts `passed`, `report.passed is True`, `violations == ()`.
- **AC4 (the `not-checked` branch).** An invocation with `observed_model=None` and
  `observed_tools=None` ⇒ both verdicts `not-checked`.
- **AC5 (EDGE-1, NEGATIVE LEG).** `check_bd_l3([])` ⇒ both verdicts
  `not-checked` **and `report.passed is False`**. A report over zero evidence
  returning `passed=True` is the purest form of B-1.
- **AC6 (EDGE-8, NEGATIVE LEG — the input filter).** A log where the VIOLATING invocation
  sits in an event of ANOTHER type (`"runner_result_consumed"` with the same keys), while the
  `model_invocation_attested` event is clean ⇒ verdicts `passed`, not `failed`.
  It catches a GREEN without the filter, which is indistinguishable on a homogeneous corpus.
- **AC7 (NEGATIVE LEG — recomputation, not a flag).** An invocation VIOLATING by payload
  but carrying `"verdict": "passed"` ⇒ the verdict is `failed` all the same. It catches a GREEN
  that trusts the recorded flag, i.e. one that lets the emitter grade itself.
- **AC8 (B-2, the export surface).** The module's public surface equals
  `__all__`. It is asserted AGAINST `__all__` rather than computed via `__module__`:
  it is measured that instances of built-in types carry no `__module__` (`"x".__module__`
  raises `AttributeError`), so the computing form counts imported
  constants as exports — and `bd_l3.py` is obliged to import `REQUIREMENT_FAILED`,
  i.e. **fixing B-1 detonates B-2**.
- **AC9 (`validate_report` as the judge).** A self-consistent report ⇒ `()`.
  A report with `passed=True` under a `failed` verdict ⇒ a non-empty tuple of complaints.
  Both sides, otherwise a judge that always returns `()` is green.
- **AC10 (ADV-9).** `labels` carries ADV-9 with the value `ADVERSARY_NOT_EXECUTED`.
- **AC11 (`L0Report` is not extended).** The report has exactly four fields — the bd#22 contract.

**In both directions on one fixture set** (the issue's requirement): AC1/AC2 against AC3 —
the same log builder, different observations.

## §8. What this PR does NOT claim

- It does not issue a BD-L3 grant: BD-L0/L1/L2 do not exist (bd#8, bd#9, bd#27 are open).
- It changes not one existing module.
- It does not claim completeness for R3.1/R3.2/R3.5 — this lot's `REQUIREMENTS` is exactly
  `("R3.3", "R3.6")`, as the issue specifies.
