# bd#267 gate r1: spec + RED audit (ADV-4 / ADV-5 failure-handling adapter operations)

- Spec: `docs/decisions/2026-10-10-bd267-adapter-failure-handling-ops.md`
- RED: `engine_py/tests/test_bd267_adapter_failure_handling_ops.py` (81 fail today)
- Pins: `engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py` (18 pass today)
- Code under change: `engine_py/bytedigger_engine/conformance/harness.py` (`_ADAPTER_OPS` l.417, `_optional_op` l.424, `_guarded_bool_call` l.365, `run_adversaries` dispatcher l.493-503)
- Preflight facts: HEAD f1fa59a, syntax/tier/cite/stub/scoped/siblings all ok; all 8 sibling files green.

## Step 1: spec-internal consistency (literal-token drift)

The tokens below match everywhere: in the spec table, the probe text, the ACs, the RED `ROWS`/`_WRONG` tables, the pins stubs and the CLI factory sources:
`record_oracle_step`, `record_gate_step`, `oracle_load_failure`, `gate_raises`, `load_failure`, `failed`, `passed`, `rejected`, `absent`, `shadow:`, `adapter_ops`/`--adapter-ops`, `_STEP_STATUSES`, `_guarded_value_call`, `_status_of`, `_step_probe`. No variant spellings.

Drift found (advisory only):
- The spec header says "Status: r1, **TIER3**", but the preflight facts report `tier: TIER2`. See Finding 1.
- The spec says "Rules 1 to 6 of g1 apply unchanged" and "where g1 said 'non-bool answer', read 'malformed answer'". Module docstring rule 3 (`harness.py` l.54, "a yes there is `undefended`") contradicts the g2 control rule (a failed control is `indeterminate`). The spec only requires the docstring to gain "the two rows and the status vocabulary". See Finding 2.

## Step 1.5: rule-overlap simulation

**Dispatcher (`run_adversaries`, adapter branch).** Branch order: `name in _ADAPTER_ADVERSARIES` ("ADV-1", "ADV-2"), then `name in _ADAPTER_OPS`, then `not_executed`. After GREEN, ADV-4 and ADV-5 are not in `_ADAPTER_ADVERSARIES`, so the first branch that matches is `_ADAPTER_OPS`, which is what the spec intends. ADV-1, ADV-2 and ADV-3 still go to the same branches as before. ADV-6, ADV-7, ADV-8 and ADV-10 still fall to `not_executed`.

**`_optional_op`, in order:** `off` gives not_executed; a getattr that raises gives shadow:indeterminate; `None` gives not_executed; not callable gives shadow:indeterminate; otherwise the probe runs, an Exception there gives shadow:indeterminate, and the result is `"shadow:" + outcome`. Each AC against this order:
- AC8, off plus a conforming adapter: the first branch fires, so there are zero calls. Correct.
- AC6, method absent or None: the getattr returns None, giving not_executed with zero calls. Correct.
- AC5, non-callable `"not a function"`: falls through to the callable check, giving shadow:indeterminate. Correct.
- AC5, KeyboardInterrupt/SystemExit: the guard re-raises any non-`Exception`. The `except Exception` in `_optional_op` does not catch these, so they propagate. Correct.

**Step probe (closed vocabulary), all branches simulated:**
- Control call (C) returns:
  - indeterminate sentinel (raise or timeout): indeterminate, 1 call
  - malformed: indeterminate, 1 call
  - well-formed but not `"passed"`: indeterminate, 1 call (AC4)
  - `"passed"`: go on to the fault call
- Fault call (F) returns:
  - indeterminate sentinel: indeterminate
  - malformed: indeterminate
  - `== defended_status`: defended
  - any other well-formed status: undefended

There is no overlap. "Answers the defended status to everything" matches the control "not passed" branch first, so the result is indeterminate, not defended (AC4, second test). "Answers passed to everything" reaches the fault call and gets undefended (AC1). Both outcomes match the spec.

## Step 2: §2 (shape/probes) vs §3 (ACs) cross-check

Every terminal or error branch in the spec's probe and shape sections has a producing AC, and every AC has a producing path:

| §2 path | AC |
|---|---|
| control raise | AC5 raising `where=control` |
| control timeout | AC5 hung `where=control`, plus `args == [None]` |
| control malformed (8 shapes) | AC5 malformed control |
| control well-formed but not passed | AC4 (4 statuses, plus defended-to-everything) |
| fault raise / timeout / malformed | AC5 `where=fault`, malformed fault |
| fault == defended | AC2 |
| fault other well-formed | AC3 (4 per row) |
| interrupts propagate | AC5 interrupts |
| non-callable | AC5 |
| absent / None | AC6 (pin) |
| off (kill switch) | AC8 (pin), AC11 off leg |
| shadow prefix / not counted | AC7, every RED asserts the exact `shadow:` string |
| `_guarded_bool_call` behaviour kept | AC10/AC12 plus the g1 sibling files run unmodified |
| `adapter=None` untouched | AC8, AC12 (pins) |

The one branch with no new-row AC is "attribute read raises" (`_optional_op` l.431). It already exists as shared g1 code, the change does not touch it, and it is reached the same way for every row. It does not produce a §3 post-condition specific to this lot, so it is not a mismatch (listed under Adversarial edges, E1).

No mismatch.

## Step 3: RED adequacy

**Count.** AC1 2, AC2 6, AC3 8, AC4 10, AC5 50, AC6 1, AC7 1, AC9 2, AC11 1, for a total of 81. This matches preflight. The pins add 18: AC6 5, AC8 7, AC10 2, CLI usage 1, AC12 3.

**Fails at assert time, not collect time.** `conformance.*` imports are deferred into the test bodies (bd#24). Today every case reaches `run_adversaries`, which returns `not_executed` for ADV-4/5, so the `==` asserts fail. The interrupt cases fail with pytest's "DID NOT RAISE", because the method is never called. The CLI case runs the subprocess to completion and fails on the JSON assert. None of these fail at import or collection.

**No stub-passability.** The stubs mock the *adapter*, which is the input to the unit under test (UUT). They never mock `harness`. The harness itself is imported for real and run for real.

**Not satisfiable by a wrong GREEN.** Each wrong implementation below is killed by the tests listed:
- An always-`undefended` probe: AC2 and AC9.
- A g1-style "a failed control is undefended": AC4 (expects indeterminate).
- Skipping the control: AC4 (`calls == [method]`) and AC2 (`args == [None, fault]`).
- Fault first, or the wrong fault string per row (e.g. `oracle_load_failure` passed to the gate op): AC2, AC3 and AC9 `args`.
- Swapped defended statuses between rows: AC2 and AC3 (each row's own defended status is in the other row's wrong list).
- Accepting a bare string or `None` status, or rejecting a non-dict Mapping: AC5 malformed, and AC2 with `MappingProxyType`.
- Accepting `{"status": "bogus"}`: AC5.
- Dropping the `shadow:` prefix, or counting the outcome: the exact-string asserts and AC7.
- Swallowing interrupts with `except BaseException`: AC5 interrupts.
- A non-daemon or joined worker: AC5 hung (elapsed < 2.5 s, non-daemon list empty).
- Proceeding after a control timeout: AC5 hung `where=control` (`args == [None]`; the second call would append immediately).
- Calling freeze/evaluate or the other op in a scoped run: AC9.

**Pins: are they vacuous?** AC6, AC8 and AC10 pass today only because ADV-4/5 are not yet in `_ADAPTER_OPS`. After GREEN they are real regression shields on the `off`/absent/None branches. This is acceptable and is the stated pin role.

## Step 4: reachability (§1y)

| AC | Point (prod) | Host | Test path |
|---|---|---|---|
| AC1-AC5, AC9 | new `_step_probe(...)` closure via `_ADAPTER_OPS["ADV-4"/"ADV-5"]` | `_optional_op` (l.424), called from `run_adversaries` l.497 | `h.run_adversaries(only=(adv,), adapter=stub)` |
| AC5 timeout/raise | new `_guarded_value_call` worker/join | step probe | same, `timeout_s=0.2/0.5` |
| AC6 | `_optional_op` l.433-434 | `run_adversaries` | pins, `run_adversaries(adapter=NoOps/NoneOps)` |
| AC7 | `_level_achieved` l.524 (`== OUTCOME_DEFENDED`) | `build_attestation` / `validate_attestation` | direct calls on the run output |
| AC8 | `_optional_op` l.426 | `run_adversaries` | pins `adapter_ops="off"` |
| AC10 | `_probe_lint_vacuity` through the split `_guarded_bool_call` | `_optional_op` | pins, `only=("ADV-1","ADV-2","ADV-3")` |
| AC11 | `_main` l.646 then `run_adversaries` | CLI `__main__` | subprocess `python -m bytedigger_engine.conformance.harness` |
| AC12 | module source / `__all__` / no-adapter branch l.504 | module | pins |

Every side-effect AC is reachable from a real entry point. No AC depends on a private helper being called directly.

**Existing-behaviour contradiction check.** I grepped the other test files for `ADV-4|ADV-5|_ADAPTER_OPS|_guarded_bool_call|record_*`:
- `test_bd195_harness_adapter_seam.py` `_NON_ORACLE` (l.214, l.311, l.331) expects ADV-4/5 `not_executed` only for adapters that lack the new methods. That stays true (AC6).
- `test_bd197_*` asserts only on ADV-1/2/3 and the no-adapter nine-defended map. Unaffected.
- No test pins `_ADAPTER_OPS` keys or `_guarded_bool_call` internals.

No contradiction with existing pins.

## Adversarial edges (not covered by the spec's AC table)

- **E1, attribute read raises (new rows).** An adapter whose `record_gate_step` is a property that raises. This is shared g1 code (`_optional_op` l.431) and gives `shadow:indeterminate`, but no ADV-4/5 case pins it. Low risk because the code is unchanged.
- **E2, the malformed check runs off the guard.** The spec does not say where `_status_of` runs. If it runs on the main thread after the worker returns, a hostile `Mapping` whose `__getitem__`/`get` blocks would hang the harness past `timeout_s`. A `Mapping` whose `get` raises is caught by `_optional_op`'s `except Exception`, so it is safe. Advisory: evaluate the status inside the worker, or accept and document this.
- **E3, `str` subclass status.** `class S(str): __eq__ = lambda *_: True` passes `isinstance(v, str)`, compares equal to `"passed"` and to the defended status, and so gives `defended`. This is not a real threat model, because the host self-reports its own step record anyway. Using `type(v) is str` closes it at no cost. Advisory.
- **E4, off before non-callable.** `adapter_ops="off"` with `record_oracle_step = "x"` should be `not_executed`, because the off branch comes first. This is not pinned for the new rows, but the shared order is unchanged.
- **E5, cross-op isolation under a hang.** In a full run where `record_oracle_step` hangs, ADV-5 must still be probed and judged on its own merits. Each op gets its own guard, so this holds by construction. It is not pinned.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS

## Findings

1. **MINOR: tier label drift.** The spec header says TIER3; the preflight facts say `tier: TIER2`. Reconcile the label (or the preflight's tier source) before ship so receipts agree. It does not block, because both tiers require this gate.
2. **MINOR: docstring rule drift.** Module docstring rules 2 ("non-bool answer") and 3 ("a yes there is `undefended`") will be wrong for ADV-4/5 after GREEN. GREEN should amend those rules too, not only add the rows and the vocabulary. A test does not need to pin this, but it is the interface text the spec says the docstring carries.
3. **MINOR (advisory): E2/E3 hardening.** Run `_status_of` inside `_guarded_value_call`'s worker (or wrap it in the same guard), and test the status with `type(v) is str`. Neither is required by an AC.

No MAJOR findings.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd267-adapter-failure-handling-ops.md sha256:9f5307cae11766d530c8f7796818c4f0054e1a3fd5390bf45f587a48c4e9e37a
red: engine_py/tests/test_bd267_adapter_failure_handling_ops.py sha256:2f2ba8ddee42b0ac45e4edcc965dcebcf9ec63fc2e2cc9d353ad655db1073549
-->
