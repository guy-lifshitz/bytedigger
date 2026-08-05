# bd#9 — BD-L2: falsifiable oracle + fail-closed gates (ADV-3…ADV-6)

**Class:** an oracle that cannot be falsified is not an oracle. BD-L2 is the
first level at which **a green result starts to carry information**, so
every requirement must have a presented input on which the checker says **NO**.
The failure form is the same one the bd#10 gate called B-1 and that I closed in bd#28: an oracle with
an unreachable refusal branch is not weak but **inverted**.

**Chokepoint:** the new module `conformance/bd_l2.py` — the only place where the
R2.1–R2.6 verdicts are **computed**. The facts come from log events and from ALREADY EXISTING
deterministic primitives; not one of them is rewritten.

**Frozen spec:** `SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md`
(HAL `fd35e1304`), §4 the adversary table, §8 the last paragraph, §9 the order.

---

## §1. Live base — TWO CORPORA, measured BEFORE the freeze

On `c1cc725`, on this host, from `engine_py` / from the root:

| corpus | result |
|---|---|
| **pytest** (`ci.yml`) | **5401 passed / 47 skipped / 1 xfailed / 0 failed**, 446 s |
| **clean-room suite** (`clean-room.yml`, docker) | **5314 passed / 69 skipped / 1 xfailed / 0 failed**, 239 s, verdict `PASS` |

The code-drift gate on the base: `python3 -m bytedigger_engine.error_codes --check` → `OK 230 codes`.
(Invoking it as a file rather than a module fails with `ModuleNotFoundError` — the instrument must be called with `-m`.)

## §2. Measurement: all four primitives ALREADY EXIST — the lot CONNECTS them rather than writing them anew

| requirement | adversary | primitive in the tree | status |
|---|---|---|---|
| R2.1 | — | the events `red_test_outcome` (`group`, `exit_code`, `n_passed`, `n_failed`), `red_collect_probe_check` | present |
| R2.2 | **ADV-3** | `stub_passability.scan_stub_passability` / `lint_red_file` — the AST scan for "the RED mocks its own UUT" | present |
| R2.5 | **ADV-6** | `known_reds_ledger.classify_kill_by` → `active`/`expired`/`malformed`, `ISSUE_INDEX=3`, `KILL_BY_INDEX=4` | present |
| R2.6 | — | the event `baseline_delta_gate_verdict` | present |

**Only the error codes are missing:** `E_ORACLE_VACUOUS`, `E_GATE_INDETERMINATE`,
`E_SUPPRESSION_UNBOUNDED` — measured by grepping `error_codes.py` (0 hits each);
`E_ORACLE_INDETERMINATE` and `E_ORACLE_MUTATED` already exist from bd#8.

⇒ The lot does not invent the mechanics of falsifiability. It **issues a verdict** over
existing observations and closes three holes in the code dictionary.

## §3. The interface — carried by the SPEC, not the RED (`CONTRACTS_SPEC` §1.5 / bd#7 §3.0)

The module `bytedigger_engine/conformance/bd_l2.py`.

```
__all__ = ["REQUIREMENTS", "check_bd_l2", "validate_report"]

REQUIREMENTS: tuple[str, ...] = ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")

def check_bd_l2(events: "Iterable[Mapping[str, object]]") -> L0Report: ...
def validate_report(report: L0Report) -> tuple[str, ...]: ...
```

`L0Report` is imported and **NOT extended** (the bd#22 contract, `CONTRACTS_SPEC` §2
AC-C2). `labels` carries `verdict:R2.1`…`verdict:R2.6` from `conformance.tokens`, plus
`ADV-3`…`ADV-6` with their executed outcome.

**Imports under an underscore, annotations stringly, `__all__` mandatory** — B-2 from
bd#10 and my own slip in bd#28, where the surface leaked three names
(`annotations`, `Any`, `TYPE_CHECKING`). Here that is the form from the first line.

## §4. The verdict mechanism — three branches per requirement, as in bd#28

`failed` — if at least one observation recorded a violation; else `not-checked` — if not one
carried a non-null observation; else `passed`. The report's `passed` is true only
when **every** requirement is `passed`; `not-checked` is **not** `passed`.

**The verdict is recomputed from the payload rather than read from a recorded flag** — otherwise the
emitter grades itself.

Factor by factor:

- **R2.1 (a refusal ≠ a no-op).** The observation is the `red_test_outcome` event.
  *A refusal* = `n_failed >= 1` under a normal exit code. *Indeterminacy* =
  zero tests collected (`n_passed + n_failed == 0`) or a collection-error exit code.
  **A violation** is a run that **counted** indeterminacy **as a refusal**
  (`E_ORACLE_INDETERMINATE`).
- **R2.2 (a vacuous oracle, ADV-3).** A violation is a non-empty set of
  `stub_passability` findings over the oracle's files (`E_ORACLE_VACUOUS`).
- **R2.3 (an observable effect).** The observation is the declared list of ACs; a violation is
  not a single AC bound to an observable effect of the artefact.
- **R2.4 (a gate that raised, ADV-5).** A gate that raised an exception counts as
  **failed**, never as absent (`E_GATE_INDETERMINATE`). A violation is a gate with
  a recorded exception and an outcome of "absent/skipped".
- **R2.5 (suppression, ADV-6).** A violation is a registry row with no owner
  (`ISSUE_INDEX` empty) **or** with `classify_kill_by != active`
  (`E_SUPPRESSION_UNBOUNDED`).
- **R2.6 (the delta against a declared baseline).** The observation is the
  `baseline_delta_gate_verdict` event. A violation is declaring only a scoped result with no
  full-suite delta. **This is about the GATE, not the oracle** — a direct reminder from the issue.

**§8 of the frozen spec: a level is claimed ONLY on adversaries actually
executed.** So `labels` carries each ADV's outcome, and `validate_report` rejects a
report declaring `passed` on an adversary that was not executed — "an implementation that silently
counts an unexecuted adversary as passed is itself a conformance failure".

## §5. Scope

**New files**
- `engine_py/bytedigger_engine/conformance/bd_l2.py`
- `engine_py/tests/test_bd9_l2_falsifiable_oracle.py` — the RED

**Edited**
- `engine_py/bytedigger_engine/error_codes.py` — **+3 codes**:
  `E_ORACLE_VACUOUS`, `E_GATE_INDETERMINATE`, `E_SUPPRESSION_UNBOUNDED`.

**§1v — NOT in scope**
- `stub_passability.py`, `known_reds_ledger.py`, `oracle.py` — **consumed, not
  edited**. Rewriting them would mean making two subjects into one diff.
- Wiring the L2 verdicts into the engine phases (failing a run on `E_ORACLE_VACUOUS` etc.) —
  **a separate lot**, as the bd#29 flip was separate from the bd#10 seam. Here the checker and the
  report are built; enforcement is a consequence that must be measured with its own exposure.
- `L0Report` (the bd#22 contract).
- The BD-L2 level grant: it is cumulative and requires BD-L0/L1 (bd#27 and bd#8 are in the tree, but this lot
  does not issue the grant).

## §6. §1a Sibling audit

Existence verified on `c1cc725` rather than assumed:

| file | link |
|---|---|
| `test_bd8_l1_oracle.py` | `oracle.py`, the `E_ORACLE_*` codes |
| `test_bd27_oracle.py` | the L0/L1 oracle |
| `test_bd28_bd_l3_checker.py` | the checker's form, `L0Report`, `__all__` |
| `test_bd22_contracts.py`, `test_contracts.py` | the `L0Report` contract |
| `test_bd24_quant_lint.py` | the deferred-import discipline |
| the `error_codes` tests | the code registry — **direct risk**: the registry is two-sided, and `--check` failed 5 tests in bd#48 when a code was registered with no emitter |

**The risk is named in advance:** bd#48 showed that registering a code **with no emitter** gives
`DEAD <CODE>` and fails the drift tests. The three new codes must either have an emitter in
this same diff or be registered in a form the drift gate accepts.
To be checked before the GREEN freeze.

## §7. Acceptance criteria

`conformance.*` imports go **inside test bodies**. Every leg is built by **one**
log builder: if `failed` and `passed` are assembled by different fixtures, it is the fixtures that
diverge, not the verdicts.

**A negative leg for EVERY requirement — an input on which the checker must say
NO.** That is the lot's mandate, not a decoration.

- **AC1 (R2.1, A REFUSAL).** `red_test_outcome` with `n_failed=1` ⇒ `verdict:R2.1 == passed`.
- **AC2 (R2.1, NEGATIVE).** A run that collected ZERO tests
  (`n_passed=0, n_failed=0`), counted as a refusal ⇒ `failed`, code
  `E_ORACLE_INDETERMINATE`. A no-op is not a refusal.
- **AC3 (R2.2, ADV-3, NEGATIVE).** An oracle mocking its own UUT ⇒ `failed`,
  `E_ORACLE_VACUOUS`. The input is a real findings set of the `stub_passability` shape.
- **AC4 (R2.2, positive).** An oracle with no findings ⇒ `passed`.
- **AC5 (R2.4, ADV-5, NEGATIVE).** A gate with a recorded exception and an outcome of
  "absent" ⇒ `failed`, `E_GATE_INDETERMINATE`. A gate that reached no verdict
  fails closed.
- **AC6 (R2.5, ADV-6, NEGATIVE, both halves).** A row with no owner ⇒ `failed`;
  a row with an expired `Kill-by` ⇒ `failed`; both with `E_SUPPRESSION_UNBOUNDED`. A row with
  an owner and a live date ⇒ `passed`. Three inputs, because "no owner OR
  expired" is a disjunction, and checking one half leaves the other unreachable.
- **AC7 (R2.6, NEGATIVE).** A scoped result only, with no full-suite delta ⇒
  not `passed`. A direct requirement of the issue: R2.6 is about the gate, not the oracle.
- **AC8 (R2.3).** Not a single AC bound to an observable effect ⇒ not `passed`.
- **AC9 (EDGE-1, NEGATIVE).** `check_bd_l2([])` ⇒ all verdicts `not-checked` and
  **`passed is False`**. A report over zero evidence with `passed=True` is the B-1 form.
- **AC10 (the input filter).** A violation planted in an event of ANOTHER type is
  ignored. A real log is heterogeneous, a fixture corpus is homogeneous.
- **AC11 (recomputation, not a flag).** An event carrying `"verdict": "passed"` while violating
  on the data ⇒ `failed`. Otherwise the emitter grades itself.
- **AC12 (§8 — executed adversaries only).** A report declaring `passed` on an
  adversary whose outcome is "not executed" is rejected by `validate_report`.
- **AC13 (`validate_report`, both sides).** A self-consistent one ⇒ `()`; a lying one ⇒
  a non-empty tuple. A judge that always returns `()` is green.
- **AC14 (surface = `__all__`).** B-2; the form from the first line, not after a failure.
- **AC15 (`L0Report` is not extended).** Four fields — the bd#22 contract.
- **AC16 (the codes are registered).** The three new codes are present in the `error_codes`
  registry and pass its drift gate.

## §8. The base — see §1, taken BEFORE the freeze

The numbers are in §1. Both sides of the delta will be taken by me on this same host, per corpus
separately.

## §9. What this PR does NOT claim

- **It does not issue a BD-L2 grant** — the level is cumulative and is claimed only on adversaries
  actually executed; this lot builds the checker and the report.
- It does not wire the verdicts into the engine phases (enforcement is a separate lot with its own
  exposure).
- It does not rewrite `stub_passability`, `known_reds_ledger`, `oracle` — it consumes them.
- It does not claim the quality of the criterion itself: an independent, falsifiable, but wrong
  oracle passes BD-L2 by construction (§5 of the frozen spec).
