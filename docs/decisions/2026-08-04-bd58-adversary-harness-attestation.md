# bd#58 — adversary execution harness + attestation publishing

**Class:** a shield that is green because nobody ever raised it. Both checkers (`bd_l2`, `bd_l3`)
can say NO — that is proven by their negative legs. But no adversary is **ever run** against its
own host, so on a real log every verdict today is `not-checked`, and there is no publisher at
all. `not-checked` on its own is honest; it is dangerous paired with an absent
publisher — nothing goes out, and "the level was not measured" reads
as "the question is closed".

**Chokepoint:** a new module `engine_py/bytedigger_engine/conformance/harness.py` —
the **only** place where an adversary is executed, its outcome recorded, and the attestation
artefact built.

**Frozen spec:** `2026-07-26_bytedigger_conformance_levels.md` (HAL `fd35e1304`),
§4 (the adversary table + Attestation output), §8 (the last paragraph), §9 step 1.

---

## §1. §1b live base — TWO CORPORA, taken BEFORE the freeze

On `a6e7fb5`, this host:

| corpus | result |
|---|---|
| **pytest** | **5417 passed / 47 skipped / 1 xfailed / 0 failed**, 411 s |
| **clean-room suite** | **5330 passed / 69 skipped / 1 xfailed / 0 failed**, 238 s, `PASS` |

## §2. Measurement: what exists and what does not

| adversary | level | primitive in the tree | executable today |
|---|---|---|---|
| ADV-1, ADV-2 | L1 | `conformance.oracle` (`compute_digest`, `compute_scope_digest`, `OracleRefusal`) | **yes** |
| ADV-3 | L2 | `stub_passability.lint_red_file` | **yes** |
| ADV-4 | L2 | an oracle that fails to load ⇒ `E_ORACLE_INDETERMINATE`; the discriminator is in `bd_l2._r21` | **yes** |
| ADV-5 | L2 | a gate that raised; the discriminator is in `bd_l2._r24` | **yes** |
| ADV-6 | L2 | `known_reds_ledger.classify_kill_by` | **yes** |
| ADV-7, ADV-8, ADV-10 | L3 | `attest.capability_escapes`, `_model_family`, `attest.assemble` | **yes** |
| ADV-9 | L3 | — | **no, declarative per §8** |

⇒ The lot invents no adversaries. It **executes** them with existing primitives and
**publishes** the result. `bd_l2`/`bd_l3`/`report`/`tokens`/`attest` are consumed, not
edited.

## §3. The interface — carried by the SPEC (`CONTRACTS_SPEC` §1.5)

```
__all__ = ["ADVERSARIES", "OUTCOME_DEFENDED", "OUTCOME_UNDEFENDED",
           "OUTCOME_ERRORED", "run_adversaries", "build_attestation",
           "validate_attestation"]

ADVERSARIES: tuple[str, ...]          # the executable set of §8: ADV-1..ADV-8, ADV-10
def run_adversaries(only=None) -> dict[str, str]      # adversary -> outcome
def build_attestation(outcomes, *, level_claimed, engine_version,
                      adapter_identity, host_identity, timestamp) -> dict
def validate_attestation(attestation) -> tuple[str, ...]
```

Imports under an underscore, annotations stringly, `__all__` mandatory (B-2).

## §4. Outcome semantics — this is where fail-closed lives

- `defended` — the adversary was run AND the host repelled it.
- `undefended` — run, and the host did **not** repel it. Sinks the level.
- `errored` — the execution raised ⇒ counts as **NOT passed** (fail-closed, the spirit of
  R2.4: a guard that reached no verdict refuses). Never "absent".
- `not_executed` (`tokens.ADVERSARY_NOT_EXECUTED`) — not run. **Withdraws** the grant rather
  than raising it.

**The level grant is cumulative and is issued ONLY on `defended`.** Any other outcome on a
level-N adversary means N was not achieved. ADV-9 is declarative and **must
be present** in the report as `not_executed` rather than absent: silence is precisely the
form §8 forbids.

## §5. Scope

**New:** `conformance/harness.py`; `tests/test_bd58_adversary_harness.py`.
**Edited:** nothing.
**§1v — NOT in scope:** `bd_l2`, `bd_l3`, `oracle`, `stub_passability`,
`known_reds_ledger`, `attest`, `report`, `tokens` — consumed. Wiring the verdicts into
the engine phases is **issue #59**, a separate lot with its own exposure measurement.

## §6. §1a Sibling audit

`test_bd9_l2_falsifiable_oracle.py`, `test_bd28_bd_l3_checker.py`,
`test_bd8_l1_oracle.py`, `test_bd27_oracle.py`, `test_bd22_contracts.py`,
`test_contracts.py`, `test_bd24_quant_lint.py`. Existence to be checked by a run rather than
assumed: in bd#28 listing a non-existent file gave `no tests ran` and voided the
whole invocation.

## §7. Acceptance criteria

`conformance.*` imports go inside test bodies.

- **AC1 (the execution is real).** `run_adversaries()` returns an outcome for **every**
  name in `ADVERSARIES`, and at least one is `defended`, obtained by a genuine run of the
  primitive rather than by a constant.
- **AC2 (NEGATIVE — an unexecuted one withdraws the grant).** An attestation where a level's
  adversary stands at `not_executed` does **not** grant that level; `level_achieved` is below
  what was claimed.
- **AC3 (NEGATIVE — an unrepelled one sinks it).** `undefended` on a level's adversary ⇒ the level
  is not achieved.
- **AC4 (NEGATIVE — an exception = not passed).** `errored` ⇒ the level is not achieved.
  A guard whose run crashed is not "absent".
- **AC5 (NEGATIVE — a claim above the fact is rejected).** `level_claimed` above
  `level_achieved` ⇒ `validate_attestation` returns a non-empty tuple.
- **AC6 (cumulativity).** All L2 adversaries `defended`, but an L1 adversary `undefended`
  ⇒ BD-L2 is not granted.
- **AC7 (ADV-9 is present).** The report carries `ADV-9` with the value `not_executed`;
  a missing key is also a failure.
- **AC8 (the §4 schema).** The attestation carries exactly: `level_claimed`, `level_achieved`,
  `adversaries`, `engine_version`, `adapter_identity`, `host_identity`, `timestamp`.
- **AC9 (`validate_attestation` both sides).** A consistent one ⇒ `()`.
- **AC10 (surface = `__all__`).** B-2.
- **AC11 (determinism).** Two consecutive `run_adversaries()` give identical outcomes —
  a harness whose verdict jumps around is not an instrument.

## §8. The base — the numbers are in §1, taken BEFORE the freeze

Both sides of both deltas are taken by me on one host, per corpus separately.

## §9. What the PR does NOT claim

- It does not wire the verdicts into the engine phases (#59).
- It does not claim the achieved level is high: the harness publishes what it measured.
- ADV-9 stays declarative (§8), and that is declared rather than hidden.
