# bd#63 — R3.5: the enforcement claim becomes refutable

**Class:** the actor grades itself. `capability_enforcement` in the attestation comes from
`_capability_enforcement(resolved_backend)`, i.e. from a registry **the backend itself fills in** —
`register_backend(..., capabilities=...)`. A claim of enforcement issued by the party being enforced is
indistinguishable from a polite lie, and no layer today sees that
difference. The same superclass that bd#29 and bd#36 closed.

**Chokepoint:** `conformance/bd_l3` — the only place where a claim is cross-checked against an
observation. `_capability_enforcement` and `_attest_payload` are **not touched**: the claim
stays, it merely stops being irrefutable.

---

## §1. §1b live base — both corpora, `6fb06ba`

| corpus | result |
|---|---|
| **pytest** | **5449 passed / 47 skipped / 1 xfailed / 0 failed**, 348 s |
| **clean-room suite** | **5362 passed / 69 skipped / 1 xfailed / 0 failed**, 208 s, `PASS` |

## §2. Measurement — the issue's three questions closed by number

**(1) How many backends declare enforcement.** Six are registered:

| backend | `capabilities` | `capability_enforcement` |
|---|---|---|
| `claude-subprocess` | carries `tool_allowlist` | **`runtime-allowlist`** — the only claimant |
| `claude-in-session` | without it | `not-enforced` |
| `agent-sdk`, `anthropic-api`, `pydantic-anthropic`, `pydantic-openai` | `frozenset()` | `not-enforced` |

⇒ **1 of 6** declares enforcement. The edit's exposure is small and stated as a number.

**(2) What the claim can be refuted with — the instrument ALREADY exists.** The attestation payload carries
both the claim (`capability_enforcement`) and the evidence (`observed_tools`,
`declared_capabilities`) — **in one and the same event**. `attest.capability_escapes`
already counts escapes for R3.6. So an external host observation is **not required**: a backend
that declared `runtime-allowlist` and then exhibited an escape is refuted by its own
record.

**(3) Does ADV-10 rest on the same self-description — NO.** The ADV-10 probe in the harness
(`harness._adv_10`) reads `capability_escapes` and the R3.6 verdict; it does not read
`capability_enforcement`. So the BD-L3 level does not rest on self-description, and the R3.5 edit
will not collapse it. The §3 question of the issue is closed by measurement, not by argument.

## §3. Decisions

**D1 — the claim is not abolished, it becomes REFUTABLE.** An R3.5 violation is
`capability_enforcement == "runtime-allowlist"` **and** a non-empty escape set in the same
event. A claim contradicted by its own evidence is not an opinion but a
refuted assertion.

**D2 — honesty is not punished.** A backend that declared `not-enforced` does **not**
violate R3.5 on an escape: it promised nothing. Its escape is caught by R3.6. Otherwise an honest
declaration would cost more than a false one — an inversion of incentive, and that is worse than the original defect.

**D3 — no evidence ⇒ `not-checked`.** A claim with no observation is neither confirmed
nor refuted. Silently counting it as `passed` would mean restoring self-assessment.

**D4 — a new code `E_CAPABILITY_ENFORCEMENT_UNSUBSTANTIATED`**, bound in the module as
a **bare string literal**: `error_codes.CODE_RE` harvests `["']E_[A-Z0-9_]+["']`, and a
code inside a long message reads as `DEAD` (measured in bd#9). After the edit,
`python3 -m bytedigger_engine.error_codes --markdown > engine_py/ERROR_CODES.md` is mandatory
— the canonical file is exactly there (`_ENGINE_ROOT`); there are two in the tree (the bd#9 error).

**D5 — we do NOT introduce external host observation.** It would require instrumenting the
process and carry its own exposure. Today the refutation is made with evidence that is already
recorded. Named, not done.

## §4. Scope

**Edited:** `conformance/bd_l3.py` (+R3.5 in `REQUIREMENTS`, the predicate, the code),
`error_codes.py` (+1 code), `engine_py/ERROR_CODES.md` (regenerated),
`tests/test_bd28_bd_l3_checker.py` (the `REQUIREMENTS` and `__all__` pins — they grow legitimately).

**New:** `tests/test_bd63_r35_enforcement_falsifiable.py`.

**§1v — NOT in scope:** `_capability_enforcement`, `_attest_payload`, `register_backend`
— the claim stays in place; `harness` (ADV-10 does not depend on self-description — §2.3);
`bd_l2`; external host observation (D5).

## §5. §1a Sibling audit

`test_bd28_bd_l3_checker.py` (**direct risk** — it pins `REQUIREMENTS` and `__all__`),
`test_bd10_l3_authorship.py`, `test_bd58_adversary_harness.py`,
`test_bd59_enforcement_map.py`, `test_bd9_l2_falsifiable_oracle.py`, the
`error_codes` tests. To be checked by a run.

## §6. Acceptance criteria

- **AC1 (NEGATIVE, the heart of the lot).** `runtime-allowlist` declared, and in the same event
  an escape (`observed_tools=["bash"]` with `declared=["Read"]`) ⇒ `verdict:R3.5 == failed`,
  with the violation naming `E_CAPABILITY_ENFORCEMENT_UNSUBSTANTIATED`. **The input on which the
  mechanism must say NO to a self-declaring backend** — without it this is today's state
  under another name.
- **AC2 (positive).** `runtime-allowlist` declared, no escapes ⇒ `passed`.
- **AC3 (NEGATIVE, honesty is not punished).** `not-enforced` + an escape ⇒ R3.5
  **not** `failed` (R3.6 meanwhile is `failed` — the escape is not lost).
- **AC4 (no evidence).** The claim is present, `observed_tools`/`declared` are absent
  ⇒ `not-checked`, never `passed`.
- **AC5 (the measurement is pinned).** `claude-subprocess` → `runtime-allowlist`;
  `claude-in-session` and all four reference ones → `not-enforced`. Registry drift fails the AC.
- **AC6 (ADV-10 is independent).** The ADV-10 probe does not read `capability_enforcement`; the harness
  still gives 9/9 `defended`.
- **AC7 (the code is registered and not DEAD).** The code is in `ERROR_CODES`, and the drift gate is clean.
- **AC8 (surface = `__all__`, `REQUIREMENTS` grew deliberately).**

## §7. What the PR does NOT claim

- It does not introduce external host observation (D5) — the refutation is made with evidence from the same
  record.
- It does not abolish the claim and does not change `capability_enforcement`.
- It does not claim that `runtime-allowlist` is genuinely enforced: the PR distinguishes a
  **refuted** claim from an unrefuted one, it does not prove the latter.
