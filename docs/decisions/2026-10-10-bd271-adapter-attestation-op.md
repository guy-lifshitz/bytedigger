---
red_tests:
  - engine_py/tests/test_bd271_adapter_attestation_op.py
sibling_tests:
  - engine_py/tests/test_bd271_adapter_attestation_op_pins.py
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op.py
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op_pins.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint_pins.py
  - engine_py/tests/test_bd195_harness_adapter_seam.py
  - engine_py/tests/test_bd58_adversary_harness.py
  - engine_py/tests/test_bd63_r35_enforcement_falsifiable.py
  - engine_py/tests/test_bd10_l3_authorship.py
  - engine_py/tests/test_bd94_engine_owned_paths.py
  - engine_py/tests/test_bd212_changelog_helper.py
red_pins:
  - engine_py/tests/test_bd271_adapter_attestation_op_pins.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#271 (group 4): a host attestation operation for ADV-7 and ADV-10, in shadow

**Status: r1, TIER2.** Fourth slice of #197 (plan in that issue; g1 = ADV-3 merged in #264, g2 = ADV-4/ADV-5 in #268, g3 = ADV-6 in #270). Under an adapter, ADV-7 (a step ran on a different model family than requested) and ADV-10 (a step used a tool outside its declared set) are `not_executed`, so a host has no way to show that it notices either. Both read the same attestation payload, so this slice adds **one** optional adapter operation and one registry row and one probe per adversary, on the dispatcher g1 introduced, in the same shape. ADV-8 and the shadow-to-counted flip are g5 and g6.

| | |
|---|---|
| **Tier** | TIER2: one module, one new test file (plus a pins file), a CHANGELOG line. |
| **Class** | SYSTEMATIC: two adversaries the harness cannot put to the host, so the host's model and tool attestation is never measured. Chokepoint: `conformance/harness.py`, the only place adversaries run under an adapter (`run_adversaries`, adapter branch, via `_ADAPTER_OPS` and `_optional_op`). |
| **LLM calls added** | None. Each probe is two calls to one adapter method with a fixed argument. The verdict is a string comparison and a set difference. Nothing is judged by a model. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The probes are fixed code. "Model family" is a string rule (below), not a lookup table and not a model.
2. **Provider-agnostic: a path degrades, it does not fail.** The harness names no model, vendor or provider (the #195 scan still holds); a model id it cannot read a family from makes the probe `shadow:indeterminate`, never a crash and never a guess. A host whose backend is down gets `shadow:indeterminate`.
3. **Works on a subscription and on API tokens.** Nothing in the probes depends on how the host pays for a call; the host reports whatever model id it uses.
4. **Shadow first, enforce later.** The outcome is `shadow:<outcome>` and counts toward no level. The flip is g6.

## Problem

`run_adversaries(adapter=...)` records ADV-7 and ADV-10 as `not_executed`. A host that records the model it asked for as the model it got, or never compares the tools used to the tools declared, cannot be told apart from one that is correct. The engine's own probes (`_adv_7`, `_adv_10`) judge `bd_l3.check_bd_l3` over hand-built events, which says nothing about the host.

## Adapter interface (levels §6 and §9.1, amended again)

One more optional operation, looked up by name:

`attest_step(fault) -> Mapping`

- `fault` is `None` (an ordinary step), `"model_drift"` (the host runs a step in which the model that served it is of a different family than the one it asked for) or `"tool_escape"` (a step that uses one tool outside the set it declared). The host chooses its own model ids and tool names: the harness cannot know which are valid for a host and invents none.
- It returns a mapping, the host's attestation of that step as raw facts:

| key | value |
|---|---|
| `"model_requested"` | non-empty `str`: the model the step asked for |
| `"model_observed"` | non-empty `str`: the model that served it |
| `"tools_declared"` | `list` or `tuple` of `str`: the tools the step declared |
| `"tools_observed"` | `list` or `tuple` of `str`: the tools it used |

Extra keys are ignored. The names are the harness's own; they map onto the engine's attestation payload (`model_requested`, `observed_model`, `declared_capabilities`, `observed_tools`), and the host does the mapping.

**The host does not say whether the step was a mismatch; it reports the facts, and the harness judges.** A host defends by reporting the facts truthfully: a host that records the observed model as the requested one, or the observed tools as the declared ones, shows no mismatch to the harness and is `undefended`.

Rules 1 to 6 of g1 apply unchanged (omitted or `None` is `not_executed`; guarded call; control first; shadow; `adapter_ops`; `adapter=None` untouched). "Malformed answer" for this operation is defined per adversary below. No new switch: `adapter_ops="off"` / `--adapter-ops off` records ADV-7 and ADV-10 as `not_executed` and never calls the method, which restores the BD-L1 ceiling.

## How the harness judges

**Model family** (`_model_family_of(model_id)`): strip the id, take its leading run of ASCII letters, lowercase it. `"alpha-large-2"`, `"Alpha-small-1"` and `" ALPHA_x"` are all family `alpha`; `"4o-mini"`, `"-alpha"`, `""` have none. Two ids are *the same family* when both have a family and the families are equal, and a *mismatch* when both have one and they differ. No list of vendors, no tier names, no version parsing: a tier or version change inside one family is not a mismatch, as in the engine's own R3.3. An id with no family cannot be judged.

**Tool escape**: the engine's own rule, `attest.capability_escapes(observed, declared)`: the sorted observed tool heads that are not among the declared entries' heads (the text before the first `"("`). Non-empty is an escape.

## Probes

Each adversary has its own probe, and each calls the method twice with a fresh argument. A full run therefore calls the method four times (control, `"model_drift"`, control, `"tool_escape"`); `only=("ADV-7",)` or `only=("ADV-10",)` calls it twice.

**ADV-7 (model family).** Reads only `model_requested` and `model_observed`.

1. **Control**, `attest_step(None)`. A raise, a timeout or a malformed answer (not a `Mapping`; either key missing; either value not exactly a non-empty `str`) is `indeterminate`. Ids that cannot be judged (no family on either side) are `indeterminate`. A mismatch is **`indeterminate`** (the issue fixes this): a host that reports a drift for an ordinary step has shown nothing about the probe, which a host that reports a drift for every step would pass. The fault call is not made.
2. **Fault**, `attest_step("model_drift")`. A malformed answer, a raise, a timeout, or ids that cannot be judged is `indeterminate`. A mismatch is **defended**; the same family is **undefended**.

**ADV-10 (tool set).** Reads only `tools_declared` and `tools_observed`.

1. **Control**, `attest_step(None)`. A raise, a timeout or a malformed answer (not a `Mapping`; either key missing; either value not a `list` or `tuple` whose items are all exactly `str`) is `indeterminate`. An escape is **`indeterminate`**, for the reason above. The fault call is not made.
2. **Fault**, `attest_step("tool_escape")`. A malformed answer, a raise or a timeout is `indeterminate`. An escape is **defended**; no escape is **undefended**.

The outcome is then prefixed `shadow:`. The issue's "shadow:pass" and "shadow:fail" are the harness's existing `defended` and `undefended`. An empty `tools_declared` and an empty `tools_observed` are well formed (nothing declared, nothing used).

## Shape of the change (`conformance/harness.py`)

- `_model_family_of(model_id)` and two extractors, `_models_of(answer)` and `_tools_of(answer)`, each returning a tuple of validated values or `None` when malformed. They run inside the guard's worker (the `extract` argument of `_guarded_value_call`, from g2), so a hostile mapping or sequence cannot hang the main thread past `timeout_s`, and the tools are copied to tuples there.
- `_probe_model_family(method, timeout_s)` and `_probe_tool_set(method, timeout_s)`: the two tables above, each call through `_guarded_value_call(method, fault, timeout_s, extract)`.
- Two rows are added to `_ADAPTER_OPS`, with the same method name: `"ADV-7": ("attest_step", _probe_model_family)` and `"ADV-10": ("attest_step", _probe_tool_set)`. `_optional_op`, `run_adversaries`, the CLI and `__all__` are unchanged.
- The module docstring gains the ADV-7/ADV-10 row, and its rule 2 ("malformed answer") and rule 3 ("control") name this operation's shapes, so they stay true.

## Acceptance criteria

RED file `engine_py/tests/test_bd271_adapter_attestation_op.py` (every test fails before GREEN); pins file `..._pins.py` (passes before and after). `conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module and subclass nothing from the harness. No test reads the wall clock or the date (a monotonic timer bounds the timeout tests).

- **AC1 (the operation is called, not the engine probe).** An adapter that always reports the same model and the same tools for every fault gives ADV-7 and ADV-10 `shadow:undefended`, though the engine probes say `defended`.
- **AC2 (conforming).** A host that reports `model_drift` as a different-family observed model and `tool_escape` as an extra observed tool gives `shadow:defended` for both. With `only=("ADV-7",)` the method is called exactly twice, with `None` then `"model_drift"`; with `only=("ADV-10",)` twice, with `None` then `"tool_escape"`; a full run calls it four times in the order `None, "model_drift", None, "tool_escape"`. Extra keys, a `Mapping` that is not a `dict`, tuple or list tool sets, and empty tool sets change nothing.
- **AC3 (wrong implementations fail).** A host that reports the requested model as the observed one on `model_drift` gives ADV-7 `shadow:undefended` (after two calls); one that reports a same-family observed id (a tier or version change, a different case) does too; a host that reports the observed tools equal to the declared ones on `tool_escape` gives ADV-10 `shadow:undefended`; a tool that differs from a declared one only by case (`"bash"` against `"Bash(x:*)"`) is an escape; a tool whose head matches a declared entry (`"Bash"` against `"Bash(x:*)"`) is not. The two adversaries are judged independently: a host that is right for one and wrong for the other gets `defended` and `undefended` respectively.
- **AC4 (control first).** A host that reports a mismatch for the ordinary step gives `shadow:indeterminate` after exactly one call: a family mismatch for ADV-7, an escape for ADV-10. A host that reports a mismatch for every step is caught here, not defended. A control with the same family but a different tier or version is not a mismatch and proceeds to the fault call.
- **AC5 (degrade, don't fail).** `shadow:indeterminate`, and `run_adversaries` returns normally, for: the method raises on the control; raises on the fault call only; returns `None`, `True`, a string, `[]`, `{}`, a mapping missing one of the keys, or a model value that is not a `str`, is empty, or is a `str` subclass, or a tool value that is a `str` (not a list), a set, a list holding a non-`str`, on the control; the same malformed answers on the fault call only (the control good); ids with no family (`"4o"`, `"-x"`) on either side of the control or of the fault call; sleeps 5 s against `timeout_s=0.2` (the run returns in under 2.5 s, every live non-main thread is a daemon, and a hang on the control means exactly one call); the attribute is a non-callable value; a host whose session probe raises `ConnectionError`; a host whose missing credential raises a custom `Exception` subclass; a mapping whose reads block past the timeout, and a tool sequence whose iteration blocks. A malformed answer for one adversary's keys does not affect the other's outcome. `KeyboardInterrupt` and `SystemExit` from the method propagate (`pytest.raises`).
- **AC6 (omitted stays `not_executed`; pin).** An adapter with only `freeze`/`evaluate`, and one with `attest_step = None`, gives ADV-7 and ADV-10 `not_executed` with zero calls to the method, and the full run still returns exactly the nine `ADVERSARIES` keys with ADV-1/ADV-2 as before. The operations are independent: an adapter with only `attest_step` gives ADV-7 and ADV-10 a `shadow:` outcome and ADV-3 to ADV-6 `not_executed`; an adapter with the other four operations but not this one gives ADV-7 and ADV-10 `not_executed`. ADV-8 stays `not_executed` in every case.
- **AC7 (shadow is not counted).** A conforming adapter has ADV-1 = ADV-2 = `defended` and ADV-7 = ADV-10 = `shadow:defended`. `build_attestation` with `level_claimed="BD-L3"`, ADV-7 and ADV-10 taken from that run and every other adversary `defended`, gives `level_achieved == "BD-L2"`, and `validate_attestation` returns a complaint naming `level_claimed`.
- **AC8 (kill switch; pin).** With `adapter_ops="off"` a conforming adapter gets zero calls and ADV-7 = ADV-10 = `not_executed`; ADV-1/ADV-2 equal the `"shadow"` run. An unknown value still raises `ValueError`. `adapter=None` with `adapter_ops="off"` equals the all-`defended` mapping.
- **AC9 (family rule).** `_model_family_of` is a pure function of its argument: `"alpha-large-2"`, `"Alpha-small-1"` and `" ALPHA_x"` give `"alpha"`; `"4o-mini"`, `"-alpha"`, `""` and a non-ASCII leading letter give `None`. The harness source contains no model or vendor name (the #195 scan, AC12).
- **AC10 (no interference; pin).** For an adapter that has all five operations, ADV-1 to ADV-6 equal the outcomes of the same adapter without `attest_step`, for a digest adapter and for an accept-all adapter.
- **AC11 (CLI end to end, subprocess).** A conforming adapter with the operation, `--level-claimed BD-L1`: exit 0, JSON `adversaries["ADV-7"] == adversaries["ADV-10"] == "shadow:defended"`, `level_achieved == "BD-L1"`. The same with `--adapter-ops off`: exit 0 and `not_executed` for both.
- **AC12 (pinned, must still pass).** `__all__` is unchanged; the harness source names no provider (the #195 scan); `run_adversaries()` and `run_adversaries(adapter=None)` are byte-identical to today; the g1, g2 and g3 behaviour is unchanged (their test files stay green untouched). The two probe functions behind `_ADAPTER_OPS` read no clock and no file: their source contains none of `datetime`, `date.today`, `time.time`, `time.monotonic`, `open(`, `tempfile`.

## Not in scope

- ADV-8 (g5), ADV-11 and the shadow-to-counted flip (g6).
- Any change to `oracle.py`, `bd_l2`, `bd_l3`, `attest`, `tokens`, `llm_provider`, the CLI or `__all__`. The engine's own `_adv_7`, `_adv_10` and the provider-backed family ladder in `bd_l3` are untouched; they still run when `adapter=None`.
- Proving that a host is honest about what it reports. The harness can tell a host that surfaces a mismatch from one that hides it only through what the host chooses to report; that limit is the one every adapter operation has, and counting the result is g6's decision.
- The frozen levels document kept outside this repository: this file and the module docstring carry the amended interface text.

## Files and checks

Touched: `harness.py`, `CHANGELOG.md`, the two new test files, this spec and its gate report. The probes write no file and list no directory, so the `bd#94` tree-scan lint (which skips `conformance/`) has nothing to see; `tree_scan_inventory.json` stays as is (`test_bd94_engine_owned_paths.py` in the sibling run proves it). The change adds no `E_...` code, so `error_codes.py` and both `ERROR_CODES.md` copies stay untouched; preflight runs the error-code drift check (bd#243 lineage) over the diff to confirm. The harness source must not gain a provider word (`anthropic`, `claude`, `openai`, `subscription`, `api-token`, `api_key`), including in comments and docstrings, nor a model or vendor name.

## Sibling audit (§1a)

- g1, g2, g3 files (`test_bd197_*`, `test_bd267_*`, `test_bd269_*`, each with `_pins`): the dispatcher, `_optional_op` and `_guarded_value_call` are shared; the change adds two rows and two probes and edits no shared function, so every AC of theirs is re-run unmodified. Their full-run expectations for ADV-7 and ADV-10 are none (they assert only their own adversaries, ADV-1/ADV-2 and the nine keys); the pins `test_ac12_no_adapter_run_is_unchanged` stays true.
- `test_bd195_harness_adapter_seam.py`: `_NON_ORACLE` (l.214) lists ADV-7 and ADV-10 as `not_executed` for an adapter without operations; still true because that stub has no `attest_step`. Its no-adapter attestation JSON (l.342, l.353) is unchanged.
- `test_bd58_adversary_harness.py` (surface equals `__all__`) and `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`): no-adapter path untouched.
- `test_bd10_l3_authorship.py` (R3.3 and R3.6 at the checker, and the engine's `_adv_7`/`_adv_10`): `bd_l3` and `attest` are not changed.
- `test_bd212_changelog_helper.py`: the CHANGELOG line must satisfy the entry lint (bold lead, one bullet, in `[Unreleased]`).
- Found by `grep -rl "run_adversaries\|conformance.harness\|ADV-7\|ADV-10" engine_py/tests`; `test_gh1123_*` matched only on an unrelated string and is not a sibling.
