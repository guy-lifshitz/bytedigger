---
red_tests:
  - engine_py/tests/test_bd197_adapter_vacuity_lint.py
sibling_tests:
  - engine_py/tests/test_bd195_harness_adapter_seam.py
  - engine_py/tests/test_bd58_adversary_harness.py
  - engine_py/tests/test_bd63_r35_enforcement_falsifiable.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#197 (group 1): a host vacuity-lint operation for ADV-3, in shadow

**Status: r1, TIER3.** First slice of #197, which follows #195 (the host-adapter seam). Under an adapter, only ADV-1 and ADV-2 run today, so a host attestation tops out at BD-L1. This slice adds the first optional adapter operation, for ADV-3, and fixes the shape every later operation reuses. ADV-4..ADV-8 and ADV-10 are separate PRs.

| | |
|---|---|
| **Tier** | TIER3. One module, one new test file, a CHANGELOG line. |
| **Class** | SYSTEMATIC: an adversary the harness cannot put to the host, so the host's own check is never measured. Chokepoint: `conformance/harness.py`, the only place adversaries run under an adapter (`run_adversaries`, adapter branch). |
| **LLM calls added** | None. The probe writes two fixed files and calls one adapter method. Nothing is judged by a model. |

## Problem

`run_adversaries(adapter=...)` records ADV-3 ("an oracle that mocks its own unit under test must be detected as vacuous") as `not_executed`. A host that has a vacuity lint has no way to be measured on it, and the attestation says nothing about that lint.

Running the engine's own lint and filing the result under the adapter is the defect #195 already rejected, so the host's lint has to be called.

## Adapter interface (levels §6 and §9.1, amended)

The adapter interface stays two required operations, plus **optional operations, one per adversary group**. An optional operation is looked up on the adapter by name.

| Adversary | Method | Contract | Defended when |
|---|---|---|---|
| ADV-1, ADV-2 | `freeze`, `evaluate` | required, unchanged (#195) | unchanged |
| **ADV-3** | `lint_vacuity(path: Path) -> bool` | `True` iff the host's lint flags the RED test file at `path` as vacuous (it mocks its own unit under test) | flags the vacuous file, does not flag the control |

Rules for every optional operation (the template for later groups):

1. **Omitted is `not_executed`.** The attribute is absent or `None`: the outcome is `not_executed`, the adapter is not called, and the level loses the adversary, as today. It never silently passes.
2. **Guarded like `freeze`.** The call runs on a daemon worker with `timeout_s`. A raise, a timeout, a non-callable attribute or a malformed return gives `indeterminate`, never a crash and never `defended`. `KeyboardInterrupt` and `SystemExit` propagate.
3. **A control comes first.** Every probe has a control the host must answer correctly, so an answer that is always yes or always no is `undefended`.
4. **Shadow.** The outcome is reported as `shadow:<outcome>` (for example `shadow:defended`). It is not `defended`, so it does not count toward a level; `build_attestation` and `validate_attestation` need no change for that.
5. **One setting.** `run_adversaries(..., adapter_ops="shadow" | "off")`, default `"shadow"`. `"off"` records every optional operation as `not_executed` and never calls it, which restores today's BD-L1 ceiling. Any other value raises `ValueError`. The CLI takes `--adapter-ops {shadow,off}`; an invalid value is a usage error (exit 2).
6. **`adapter=None` is untouched.** The setting has no effect there.

The probe code is a small registry keyed by adversary name (method name plus a probe function), and one dispatcher that applies rules 1 to 5. Later groups add a row and a probe.

## ADV-3 probe

The harness writes two files into a fresh temporary directory:

- **vacuous**: the existing `_VACUOUS_RED` (patches `mypkg.subject.compute_digest`, then asserts on the patched name).
- **control**: a RED file that patches a collaborator (`mypkg.subject.read_clock`) and asserts on the real `compute_digest("a")`.

Steps, in order:

1. `lint_vacuity(control)`. `True` is **undefended** (a lint that flags everything defends nothing). A non-`bool` is **indeterminate**.
2. `lint_vacuity(vacuous)`. `True` is **defended**, `False` is **undefended**, a non-`bool` is **indeterminate**.

The adapter is called exactly twice. The outcome is then prefixed `shadow:`.

## Acceptance criteria (test file `engine_py/tests/test_bd197_adapter_vacuity_lint.py`)

`conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module, and a conforming one wraps the engine's real `stub_passability.lint_red_file`. They subclass nothing from the harness. The CLI tests use a factory in a temporary module on `sys.path`.

- **AC1 (operation is called).** An adapter with `freeze`/`evaluate` and a `lint_vacuity` that always answers `False` gives ADV-3 = `shadow:undefended`, though the engine probe says `defended`.
- **AC2 (conforming).** An adapter whose `lint_vacuity` runs `stub_passability.lint_red_file(path).has_violation()` gives ADV-3 = `shadow:defended`. The method is called exactly twice with existing `.py` files under a temporary directory: the first path's text has no `patch("mypkg.subject.compute_digest")`, the second path's text has it.
- **AC3 (wrong implementations fail).** `shadow:undefended` for each of: always `True`; always `False`; a naive lint that returns `"patch(" in text`.
- **AC4 (degrade, don't fail).** `shadow:indeterminate`, and `run_adversaries` returns normally, for: `lint_vacuity` raises; returns `None`, `"yes"` or `1`; sleeps 5 s against `timeout_s=0.2` (the run returns in under 2.5 s and every live non-main thread is a daemon); `lint_vacuity` is a non-callable value; a `subscription-session` adapter whose session probe raises `ConnectionError`; an `api-token` adapter whose missing token raises a custom `Exception` subclass. `KeyboardInterrupt` and `SystemExit` from `lint_vacuity` propagate (`pytest.raises`).
- **AC5 (omitted stays `not_executed`).** An adapter with only `freeze`/`evaluate`, and one with `lint_vacuity = None`, gives ADV-3 = `not_executed` with zero calls to any method for ADV-3, and a full run still returns exactly the nine `ADVERSARIES` keys with ADV-1/ADV-2 as before.
- **AC6 (shadow is not counted).** A conforming adapter run under `run_adversaries(adapter=...)` has ADV-1 = ADV-2 = `defended` and ADV-3 = `shadow:defended`. `build_attestation` with `level_claimed="BD-L2"` and every other adversary `defended` gives `level_achieved == "BD-L1"`, and `validate_attestation` returns a complaint naming `level_claimed`.
- **AC7 (kill switch).** With `adapter_ops="off"` a conforming adapter gets zero `lint_vacuity` calls and ADV-3 = `not_executed`; ADV-1/ADV-2 are the same as with `"shadow"`. An unknown value raises `ValueError`. `adapter=None` with `adapter_ops="off"` equals the all-`defended` mapping.
- **AC8 (scoped run).** `only=("ADV-3",)` under a conforming adapter calls `lint_vacuity` twice and `freeze`/`evaluate` zero times.
- **AC9 (no interference).** For an adapter that has `lint_vacuity`, ADV-1 and ADV-2 equal the outcomes of the same adapter without it, for a digest adapter and for an accept-all adapter.
- **AC10 (CLI end-to-end, subprocess).** A conforming adapter with the operation, `--level-claimed BD-L1`: exit 0, JSON `adversaries["ADV-3"] == "shadow:defended"`, `level_achieved == "BD-L1"`. The same with `--adapter-ops off`: exit 0 and ADV-3 = `not_executed`. `--adapter-ops bogus`: exit 2 with nothing on stdout.
- **AC11 (pinned, must still pass).** `__all__` is unchanged (the #195 surface test); the harness source names no provider (the #195 provider-agnostic test); `run_adversaries()` with no adapter is byte-identical to today.

## Not in scope

- ADV-4..ADV-8, ADV-10 and ADV-11 operations. Each is a later PR using the table and rules above.
- Counting an operation toward a level (the shadow-to-enforce flip), and any change to `oracle.py`, `bd_l2`, `bd_l3`, `attest` or `tokens`.
- The frozen levels document kept outside this repository: this file and the module docstring carry the amended interface text.

## Sibling audit

`test_bd195_harness_adapter_seam.py` (stubs without the operation must stay `not_executed`, `__all__`, provider-agnostic scan), `test_bd58_adversary_harness.py` (surface equals `__all__`), `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`).
