---
red_tests:
  - engine_py/tests/test_bd267_adapter_failure_handling_ops.py
sibling_tests:
  - engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint_pins.py
  - engine_py/tests/test_bd195_harness_adapter_seam.py
  - engine_py/tests/test_bd58_adversary_harness.py
  - engine_py/tests/test_bd63_r35_enforcement_falsifiable.py
  - engine_py/tests/test_bd94_engine_owned_paths.py
  - engine_py/tests/test_bd212_changelog_helper.py
red_pins:
  - engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#267 (group 2): host operations for ADV-4 and ADV-5 (failure handling), in shadow

**Status: r1, TIER3.** Second slice of #197 (plan in that issue; g1 = ADV-3 merged in #264). Under an adapter, ADV-4 and ADV-5 are `not_executed`, so a host has no way to show how it records a fault inside a step. This slice adds two optional adapter operations on the dispatcher g1 introduced, in the same shape. ADV-6, ADV-7, ADV-8 and ADV-10 are separate PRs (g3 to g5).

| | |
|---|---|
| **Tier** | TIER3, same size as g1: one module, one new test file (plus a pins file), a CHANGELOG line. |
| **Class** | SYSTEMATIC: adversaries the harness cannot put to the host, so the host's own fault recording is never measured. Chokepoint: `conformance/harness.py`, the only place adversaries run under an adapter (`run_adversaries`, adapter branch, via `_ADAPTER_OPS` and `_optional_op`). |
| **LLM calls added** | None. Each probe is two calls to one adapter method with fixed arguments. Nothing is judged by a model. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The probe is fixed code; the verdict is a table lookup on a closed vocabulary. No model, no heuristic.
2. **Provider-agnostic: a path degrades, it does not fail.** A host whose backend is down (a session probe, a missing token) gets `shadow:indeterminate`, never a crash. The harness source names no provider (the #195 scan still holds).
3. **Works on a subscription and on API tokens.** Nothing in the probe depends on how the host pays for a call.
4. **Shadow first, enforce later.** The outcome is `shadow:<outcome>` and counts toward no level. The flip is g6.

## Problem

`run_adversaries(adapter=...)` records ADV-4 ("an oracle that fails to load must not be counted as a rejection") and ADV-5 ("a gate that raises must be recorded as failed, never as absent") as `not_executed`. A host that records faults correctly cannot show it, and one that counts a load failure as a rejection or silently drops a raising gate is not caught. The engine's own probes for both judge the engine's own checker over a hand-built event, which says nothing about the host.

## Adapter interface (levels §6 and §9.1, amended again)

Two more optional operations, one per adversary, looked up by name. Both take one argument, `fault`, and report the step record as a mapping of raw facts. **The host does not judge; it reports how the step was recorded, and the harness judges** (the lean the #197 plan records for g2 to g4; g1 asked for a boolean because the lint is the host's own check).

| Adversary | Method | `fault` values | Returns |
|---|---|---|---|
| **ADV-4** | `record_oracle_step(fault) -> Mapping` | `None`: run a clean step. `"oracle_load_failure"`: run a step whose oracle fails to load. | a mapping with `"status"`: how the step was recorded |
| **ADV-5** | `record_gate_step(fault) -> Mapping` | `None`: run a clean step. `"gate_raises"`: run a step whose gate raises. | a mapping with `"status"`: how the step was recorded |

`status` is a `str` from one closed vocabulary (`_STEP_STATUSES`): `"passed"`, `"failed"`, `"rejected"`, `"load_failure"`, `"absent"`. `"absent"` means the step has no record at all (a dropped gate). Extra keys in the mapping are ignored. Any other shape (not a `collections.abc.Mapping`, no `"status"` key, a non-`str` value, a string outside the vocabulary) is malformed and gives `indeterminate`.

Rules 1 to 6 of g1 apply unchanged (omitted or `None` is `not_executed`; guarded call; control first; shadow; `adapter_ops`; `adapter=None` untouched). Where g1 said "non-bool answer", read "malformed answer". No new switch: `adapter_ops="off"` / `--adapter-ops off` records both as `not_executed` and never calls them, which restores the BD-L1 ceiling.

## Probes

Each probe calls its method exactly twice, control first:

1. `method(None)` (a clean step). The control must be recorded as `"passed"`. A malformed answer, a raise or a timeout is `indeterminate`. A well-formed status other than `"passed"` is also **`indeterminate`**: a host that cannot record a clean step has not shown anything about faults, and the fault call is not made. (This differs from g1, where a control answered wrongly is `undefended`. For g2 the issue fixes the control-fails outcome as `indeterminate`.)
2. `method(fault)`. ADV-4 is **defended** iff `status == "load_failure"`; ADV-5 is **defended** iff `status == "failed"`. Any other well-formed status is **undefended** (for ADV-4 `"rejected"` is the counted-as-a-rejection defect; for ADV-5 `"absent"` is the dropped-gate defect; `"passed"` is wrong for both). A malformed answer, a raise or a timeout is `indeterminate`.

The outcome is then prefixed `shadow:`. The issue's "shadow:pass" and "shadow:fail" are the harness's existing `defended` and `undefended`.

## Shape of the change (`conformance/harness.py`)

- `_guarded_bool_call` is split: a new `_guarded_value_call(fn, arg, timeout_s)` holds the daemon-worker and timeout logic and returns the raw value, or a module sentinel when the call is indeterminate (raise, timeout). `_guarded_bool_call` becomes a thin wrapper that keeps its behaviour exactly (bool or `None`). One guard, not two copies.
- `_status_of(answer)` returns the status string, or `None` when malformed.
- One probe factory, `_step_probe(fault, defended_status)`, returns the probe function for a row. Two rows are added to `_ADAPTER_OPS`: `"ADV-4": ("record_oracle_step", ...)` and `"ADV-5": ("record_gate_step", ...)`.
- `_optional_op`, `run_adversaries`, the CLI and `__all__` are unchanged. The module docstring gains the two rows and the status vocabulary.

## Acceptance criteria

RED file `engine_py/tests/test_bd267_adapter_failure_handling_ops.py` (every test fails before GREEN); pins file `..._pins.py` (passes before and after). `conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module and subclass nothing from the harness. Each AC runs for both ADV-4 and ADV-5 (parametrised over the method name, fault value and defended status) unless it says otherwise.

- **AC1 (the operation is called, not the engine probe).** An adapter that answers `{"status": "passed"}` to every call gives `shadow:undefended`, though the engine probe says `defended`.
- **AC2 (conforming).** An adapter that answers `"passed"` to `None` and the defended status to the fault (ADV-4 `"load_failure"`, ADV-5 `"failed"`) gives `shadow:defended`. The method is called exactly twice, with `None` first and the fault value second. Extra keys in the returned mapping change nothing; a `Mapping` that is not a `dict` is accepted.
- **AC3 (wrong implementations fail).** With a `"passed"` control, `shadow:undefended` for every other well-formed fault answer: for ADV-4 `"rejected"`, `"passed"`, `"failed"`, `"absent"`; for ADV-5 `"absent"`, `"passed"`, `"rejected"`, `"load_failure"`.
- **AC4 (control fails first).** A control answered with a well-formed status other than `"passed"` (`"failed"`, `"absent"`, `"rejected"`, `"load_failure"`) gives `shadow:indeterminate` after exactly one call: the fault call is not made. A host that answers `"load_failure"` (ADV-4) or `"failed"` (ADV-5) to everything is caught here, not defended.
- **AC5 (degrade, don't fail).** `shadow:indeterminate`, and `run_adversaries` returns normally, for: the method raises on the control; raises on the fault call only; returns `None`, `"passed"`, `[]`, `{}`, `{"status": None}`, `{"status": 1}`, `{"status": "bogus"}`, `{"state": "passed"}` on the control; returns a malformed answer on the fault call only (control good); sleeps 5 s against `timeout_s=0.2` (the run returns in under 2.5 s and every live non-main thread is a daemon); the attribute is a non-callable value; a `subscription-session` adapter whose session probe raises `ConnectionError`; an `api-token` adapter whose missing token raises a custom `Exception` subclass. `KeyboardInterrupt` and `SystemExit` from the method propagate (`pytest.raises`).
- **AC6 (omitted stays `not_executed`; pin).** An adapter with only `freeze`/`evaluate`, and one with the method set to `None`, gives `not_executed` with zero calls to the method. The two operations are independent: an adapter with only `record_oracle_step` gives ADV-4 a `shadow:` outcome and ADV-5 `not_executed`, and the reverse. A full run still returns exactly the nine `ADVERSARIES` keys with ADV-1/ADV-2 as before.
- **AC7 (shadow is not counted).** A conforming adapter with both operations, run under `run_adversaries(adapter=...)`, has ADV-1 = ADV-2 = `defended` and ADV-4 = ADV-5 = `shadow:defended`. `build_attestation` with `level_claimed="BD-L2"`, ADV-4 and ADV-5 taken from that run and every other adversary `defended`, gives `level_achieved == "BD-L1"`, and `validate_attestation` returns a complaint naming `level_claimed`.
- **AC8 (kill switch; pin).** With `adapter_ops="off"` a conforming adapter gets zero calls to either method and ADV-4 = ADV-5 = `not_executed`; ADV-1/ADV-2 equal the `"shadow"` run. An unknown value still raises `ValueError`. `adapter=None` with `adapter_ops="off"` equals the all-`defended` mapping.
- **AC9 (scoped run).** `only=("ADV-4",)` under a conforming adapter calls `record_oracle_step` twice and `freeze`, `evaluate` and `record_gate_step` zero times; `only=("ADV-5",)` likewise.
- **AC10 (no interference; pin).** For an adapter that has both operations and `lint_vacuity`, ADV-1, ADV-2 and ADV-3 equal the outcomes of the same adapter without the two new methods, for a digest adapter and for an accept-all adapter.
- **AC11 (CLI end to end, subprocess).** A conforming adapter with both operations, `--level-claimed BD-L1`: exit 0, JSON `adversaries["ADV-4"] == "shadow:defended"` and `["ADV-5"] == "shadow:defended"`, `level_achieved == "BD-L1"`. The same with `--adapter-ops off`: exit 0 and both `not_executed`.
- **AC12 (pinned, must still pass).** `__all__` is unchanged; the harness source names no provider (the #195 scan); `run_adversaries()` and `run_adversaries(adapter=None)` are byte-identical to today; the g1 ADV-3 behaviour is unchanged (its test files stay green untouched).

## Not in scope

- ADV-6 (g3), ADV-7 and ADV-10 (g4), ADV-8 (g5), ADV-11, and the shadow-to-counted flip (g6).
- Any change to `oracle.py`, `bd_l2`, `bd_l3`, `attest`, `tokens`, the CLI or `__all__`.
- The engine's own ADV-4 and ADV-5 probes (`_adv_4`, `_adv_5`): untouched, they still run when `adapter=None`.
- The frozen levels document kept outside this repository: this file and the module docstring carry the amended interface text.

## Files and checks

Touched: `harness.py`, `CHANGELOG.md`, the two new test files, this spec and its gate report. The `bd#94` tree-scan lint skips `conformance/` and the change adds no tree-enumerating call; `tree_scan_inventory.json` stays as is (`test_bd94_engine_owned_paths.py` in the sibling run proves it). The change adds no `E_...` code, so `error_codes.py` and both `ERROR_CODES.md` copies stay untouched; preflight runs the error-code drift check (bd#243 lineage) over the diff to confirm. The harness source must not gain a provider word (`anthropic`, `claude`, `openai`, `subscription`, `api-token`, `api_key`), including in comments and docstrings.

## Sibling audit (§1a)

- g1 files `test_bd197_adapter_vacuity_lint.py` and `..._pins.py`: the dispatcher and `_guarded_bool_call` are shared. Every g1 AC is re-run unmodified; the `_guarded_value_call` split must leave `shadow:` outcomes for ADV-3 identical (AC10, AC12 above).
- `test_bd195_harness_adapter_seam.py`: `_NON_ORACLE` expects ADV-4 and ADV-5 `not_executed` for an adapter without the operations. That stays true (AC6). Its no-adapter attestation JSON is unchanged.
- `test_bd58_adversary_harness.py` (surface equals `__all__`) and `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`): no-adapter path untouched.
- `test_bd212_changelog_helper.py`: the CHANGELOG line must satisfy the entry lint (bold lead, one bullet, in `[Unreleased]`).
- Other conformance tests under `engine_py/tests/` that import `conformance.harness` were found by `grep run_adversaries`; the list above is that set.
