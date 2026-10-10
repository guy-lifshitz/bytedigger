---
red_tests:
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op.py
sibling_tests:
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op_pins.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops.py
  - engine_py/tests/test_bd267_adapter_failure_handling_ops_pins.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint.py
  - engine_py/tests/test_bd197_adapter_vacuity_lint_pins.py
  - engine_py/tests/test_bd195_harness_adapter_seam.py
  - engine_py/tests/test_bd58_adversary_harness.py
  - engine_py/tests/test_bd63_r35_enforcement_falsifiable.py
  - engine_py/tests/test_bd9_l2_falsifiable_oracle.py
  - engine_py/tests/test_bd155_l2_event_type_key.py
  - engine_py/tests/test_bd94_engine_owned_paths.py
  - engine_py/tests/test_bd212_changelog_helper.py
red_pins:
  - engine_py/tests/test_bd269_adapter_suppression_ledger_op_pins.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#269 (group 3): a host operation for ADV-6 (suppression ledger), in shadow

**Status: r1, TIER2.** Third slice of #197 (plan in that issue; g1 = ADV-3 merged in #264, g2 = ADV-4/ADV-5 merged in #268). Under an adapter, ADV-6 is `not_executed`, so a host has no way to show that its ledger of tolerated failures refuses a row nobody owns or a row that has expired. This slice adds one optional adapter operation on the dispatcher g1 introduced, in the same shape. ADV-7, ADV-8 and ADV-10 are separate PRs (g4, g5).

| | |
|---|---|
| **Tier** | TIER2: one module, one new test file (plus a pins file), a CHANGELOG line. |
| **Class** | SYSTEMATIC: an adversary the harness cannot put to the host, so the host's own ledger check is never measured. Chokepoint: `conformance/harness.py`, the only place adversaries run under an adapter (`run_adversaries`, adapter branch, via `_ADAPTER_OPS` and `_optional_op`). |
| **LLM calls added** | None. Each probe is up to three calls to one adapter method with fixed arguments. Nothing is judged by a model. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The probe is fixed code; the verdict is a comparison of booleans. No model, no heuristic.
2. **Provider-agnostic: a path degrades, it does not fail.** A host whose backend is down gets `shadow:indeterminate`, never a crash. The harness source names no provider (the #195 scan still holds).
3. **Works on a subscription and on API tokens.** Nothing in the probe depends on how the host pays for a call.
4. **Shadow first, enforce later.** The outcome is `shadow:<outcome>` and counts toward no level. The flip is g6.

## Problem

`run_adversaries(adapter=...)` records ADV-6 ("a tolerated failure needs both an owner and a live expiry") as `not_executed`. A host whose ledger takes any row, or whose expiry check never fires, cannot be told apart from one that is correct. The engine's own ADV-6 probe (`_adv_6`) judges `bd_l2.check_bd_l2` over hand-built rows, which says nothing about the host.

## Adapter interface (levels §6 and §9.1, amended again)

One more optional operation, looked up by name:

`admit_ledger_row(row, as_of) -> Mapping`

- `row` is a fresh `dict` with two keys, named as in the engine's ledger (`bd_l2`, `known_reds_ledger`): `"issue"` (the owner, a `str`; empty means nobody owns the row) and `"kill_by"` (the expiry, an ISO date `str`, `YYYY-MM-DD`).
- `as_of` is the date to judge expiry against, an ISO date `str`. The harness always passes `"2026-01-01"`. **The harness reads no clock**: time is an argument, so a probe gives the same answer on any day, and a test needs no clock either. A host that ignores `as_of` and reads its own clock is still judged correctly, because the live expiry is `2099-01-01` and the past one `2020-01-01`.
- It returns a mapping with `"accepted"`: a `bool`, the host's own verdict on that row (True: the ledger takes the row as a tolerated failure; False: it refuses it). Extra keys are ignored.

**The host does not judge the probe; it reports its verdict on each row as a raw fact, and the harness judges** (the lean the #197 plan records for g2 to g4). Any other shape is malformed and gives `indeterminate`: not a `collections.abc.Mapping`, no `"accepted"` key, a value whose type is not exactly `bool` (so `1`, `"yes"` and `None` are malformed).

Rules 1 to 6 of g1 apply unchanged (omitted or `None` is `not_executed`; guarded call; control first; shadow; `adapter_ops`; `adapter=None` untouched). Where g1 said "non-bool answer", read "malformed answer". No new switch: `adapter_ops="off"` / `--adapter-ops off` records ADV-6 as `not_executed` and never calls the method, which restores the BD-L1 ceiling.

## Probe

The method is called in this order, each call with a fresh `dict` (a host that mutates its argument changes nothing for the next call):

| # | Row | Expected `accepted` |
|---|---|---|
| 1 control | `{"issue": "#1", "kill_by": "2099-01-01"}` | `True` |
| 2 no owner | `{"issue": "", "kill_by": "2099-01-01"}` | `False` |
| 3 expired | `{"issue": "#1", "kill_by": "2020-01-01"}` | `False` |

Every call is `method(row, "2026-01-01")`. Walking the table:

1. **Control.** A raise, a timeout or a malformed answer is `indeterminate`. A well-formed `accepted == False` is also **`indeterminate`** (the issue fixes this): a host that refuses a row with an owner and a live expiry has shown nothing about the two probes, which a host that refuses everything would pass. The probes are not called.
2. **No owner.** `accepted == True` is **undefended** and the expired row is not asked. A malformed answer, a raise or a timeout is `indeterminate`.
3. **Expired.** `accepted == True` is **undefended**; `False` is **defended**. A malformed answer, a raise or a timeout is `indeterminate`.

The outcome is then prefixed `shadow:`. The issue's "shadow:pass" and "shadow:fail" are the harness's existing `defended` and `undefended`.

## Shape of the change (`conformance/harness.py`)

- `_accepted_of(answer)` returns the `bool`, or `None` when malformed. It runs inside the guard's worker (the `extract` argument of `_guarded_value_call`, from g2), so a hostile mapping cannot hang the main thread past `timeout_s` (the E2 advisory of the g2 gate).
- `_probe_ledger_rows(method, timeout_s)` holds the table above. Each call goes through `_guarded_value_call` with `lambda row: method(row, _LEDGER_AS_OF)`.
- One row is added to `_ADAPTER_OPS`: `"ADV-6": ("admit_ledger_row", _probe_ledger_rows)`. `_optional_op`, `run_adversaries`, the CLI and `__all__` are unchanged.
- The module docstring gains the ADV-6 row, and its rules 2 and 3 are amended so they stay true: rule 2 ("non-bool answer") names the malformed shapes per operation, and rule 3 ("a yes there is `undefended`") states that for ADV-4, ADV-5 and ADV-6 a control answered wrongly is `indeterminate` (the g2 gate's Finding 2, which g2 left open).

## Acceptance criteria

RED file `engine_py/tests/test_bd269_adapter_suppression_ledger_op.py` (every test fails before GREEN); pins file `..._pins.py` (passes before and after). `conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module and subclass nothing from the harness. Stubs that judge dates compare the ISO strings with the `as_of` they are handed; no test reads the clock.

- **AC1 (the operation is called, not the engine probe).** An adapter that answers `{"accepted": True}` to every call gives `shadow:undefended`, though the engine probe says `defended`.
- **AC2 (conforming).** A host that judges from its arguments (accepts iff `issue.strip()` is non-empty and `kill_by >= as_of`) gives `shadow:defended`. The method is called exactly three times, with the three rows of the table in order, each `as_of == "2026-01-01"`, each row a `dict` that is a different object from the others. Extra keys in the returned mapping change nothing; a `Mapping` that is not a `dict` is accepted. A host that mutates its `row` argument (clears it) on every call still gives `shadow:defended`.
- **AC3 (wrong implementations fail).** With an accepted control: a host that also accepts the no-owner row gives `shadow:undefended` after exactly two calls (the expired row is not asked); a host that refuses the no-owner row but accepts the expired one gives `shadow:undefended` after three calls; a host that accepts every row gives `shadow:undefended`.
- **AC4 (control refused first).** A control answered `{"accepted": False}` gives `shadow:indeterminate` after exactly one call. A host that refuses every row is caught here, not defended.
- **AC5 (degrade, don't fail).** `shadow:indeterminate`, and `run_adversaries` returns normally, for: the method raises on the control; raises on the no-owner call only; raises on the expired call only; returns `None`, `True`, `"accepted"`, `[]`, `{}`, `{"accepted": None}`, `{"accepted": 1}`, `{"accepted": "yes"}`, `{"verdict": True}` on the control; returns a malformed answer on the no-owner call only, and on the expired call only (the earlier calls good); sleeps 5 s against `timeout_s=0.2` (the run returns in under 2.5 s, every live non-main thread is a daemon, and a hang on the control means exactly one call); the attribute is a non-callable value; a `subscription-session` adapter whose session probe raises `ConnectionError`; an `api-token` adapter whose missing token raises a custom `Exception` subclass; a mapping whose `get` blocks past the timeout. `KeyboardInterrupt` and `SystemExit` from the method propagate (`pytest.raises`).
- **AC6 (omitted stays `not_executed`; pin).** An adapter with only `freeze`/`evaluate`, and one with the method set to `None`, gives `not_executed` with zero calls to the method. The operations are independent: an adapter with only `admit_ledger_row` gives ADV-6 a `shadow:` outcome and ADV-3, ADV-4, ADV-5 `not_executed`, and an adapter with the other three but not this one gives ADV-6 `not_executed`. A full run still returns exactly the nine `ADVERSARIES` keys with ADV-1/ADV-2 as before.
- **AC7 (shadow is not counted).** A conforming adapter, run under `run_adversaries(adapter=...)`, has ADV-1 = ADV-2 = `defended` and ADV-6 = `shadow:defended`. `build_attestation` with `level_claimed="BD-L2"`, ADV-6 taken from that run and every other adversary `defended`, gives `level_achieved == "BD-L1"`, and `validate_attestation` returns a complaint naming `level_claimed`.
- **AC8 (kill switch; pin).** With `adapter_ops="off"` a conforming adapter gets zero calls and ADV-6 = `not_executed`; ADV-1/ADV-2 equal the `"shadow"` run. An unknown value still raises `ValueError`. `adapter=None` with `adapter_ops="off"` equals the all-`defended` mapping.
- **AC9 (scoped run).** `only=("ADV-6",)` under a conforming adapter calls `admit_ledger_row` three times and `freeze`, `evaluate` and the other operations zero times.
- **AC10 (no interference; pin).** For an adapter that has all four operations, ADV-1 to ADV-5 equal the outcomes of the same adapter without `admit_ledger_row`, for a digest adapter and for an accept-all adapter.
- **AC11 (CLI end to end, subprocess).** A conforming adapter with the operation, `--level-claimed BD-L1`: exit 0, JSON `adversaries["ADV-6"] == "shadow:defended"`, `level_achieved == "BD-L1"`. The same with `--adapter-ops off`: exit 0 and `not_executed`.
- **AC12 (pinned, must still pass).** `__all__` is unchanged; the harness source names no provider (the #195 scan); `run_adversaries()` and `run_adversaries(adapter=None)` are byte-identical to today; the g1 and g2 behaviour is unchanged (their test files stay green untouched). The probe function behind `_ADAPTER_OPS["ADV-6"]` does not read a clock: its source contains none of `datetime`, `date.today`, `time.time`, `time.monotonic`.

## Not in scope

- ADV-7 and ADV-10 (g4), ADV-8 (g5), ADV-11, and the shadow-to-counted flip (g6).
- Any change to `oracle.py`, `bd_l2`, `bd_l3`, `attest`, `tokens`, `known_reds_ledger`, the CLI or `__all__`.
- The engine's own `_adv_6`: untouched, it still runs when `adapter=None`.
- The frozen levels document kept outside this repository: this file and the module docstring carry the amended interface text.

## Files and checks

Touched: `harness.py`, `CHANGELOG.md`, the two new test files, this spec and its gate report. The probe writes no file and lists no directory, so the `bd#94` tree-scan lint (which skips `conformance/`) has nothing to see; `tree_scan_inventory.json` stays as is (`test_bd94_engine_owned_paths.py` in the sibling run proves it). The change adds no `E_...` code, so `error_codes.py` and both `ERROR_CODES.md` copies stay untouched; preflight runs the error-code drift check (bd#243 lineage) over the diff to confirm. The harness source must not gain a provider word (`anthropic`, `claude`, `openai`, `subscription`, `api-token`, `api_key`), including in comments and docstrings.

## Sibling audit (§1a)

- g1 and g2 files (`test_bd197_*`, `test_bd267_*`, each with `_pins`): the dispatcher, `_optional_op` and `_guarded_value_call` are shared; the change adds a row and a probe and edits no shared function, so every AC of theirs is re-run unmodified. Their full-run expectations for ADV-6 (`not_executed` without the operation) stay true (AC6).
- `test_bd195_harness_adapter_seam.py`: `_NON_ORACLE` (l.214) lists ADV-6 as `not_executed` for an adapter without operations; still true. Its no-adapter attestation JSON (l.342, l.353) is unchanged.
- `test_bd58_adversary_harness.py` (surface equals `__all__`) and `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`): no-adapter path untouched.
- `test_bd9_l2_falsifiable_oracle.py` (R2.5 / ADV-6 at the checker) and `test_bd155_l2_event_type_key.py`: they exercise `bd_l2` and the engine's `_adv_6`, neither changed.
- `test_bd212_changelog_helper.py`: the CHANGELOG line must satisfy the entry lint (bold lead, one bullet, in `[Unreleased]`).
- Found by `grep -rl "run_adversaries\|conformance.harness\|ADV-6" engine_py/tests`; `test_gh1123_*` matched only on the string "ADV-6" in an unrelated feature and is not a sibling.
