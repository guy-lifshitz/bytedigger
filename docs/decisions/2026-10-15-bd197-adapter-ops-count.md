---
red_tests:
  - engine_py/tests/test_bd197_adapter_ops_count.py
sibling_tests:
  - engine_py/tests/test_bd197_adapter_ops_count_pins.py
  - engine_py/tests/test_bd273_adapter_attribution_op.py
  - engine_py/tests/test_bd273_adapter_attribution_op_pins.py
  - engine_py/tests/test_bd271_adapter_attestation_op.py
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
red_pins:
  - engine_py/tests/test_bd197_adapter_ops_count_pins.py
paths:
  - engine_py/bytedigger_engine/conformance/harness.py
  - CHANGELOG.md
---

# bd#197 (group 6): count the host adapter operations toward a level, opt-in

**Status: r1, TIER3.** Last slice of #197. Groups 1 to 5 (#264, #268, #270, #272, #274) added the optional operations for ADV-3..ADV-8 and ADV-10, all reported as `shadow:<outcome>` and never counted, so a host attestation still tops out at BD-L1. This slice adds the way to count them. It does **not** change what runs by default.

| | |
|---|---|
| **Tier** | TIER3. One module, one new test file plus a pins file, a CHANGELOG line. It touches level counting (the `adapter_ops` modes), so the sibling audit covers every earlier group. |
| **Class** | SYSTEMATIC: the host operations are measured but can never raise the level, so a host cannot be attested above BD-L1. Chokepoint: `_optional_op` in `conformance/harness.py`, the one place that turns an operation's outcome into a reported value. |
| **LLM calls added** | None. The only change is whether a deterministic outcome carries the `shadow:` prefix. |

## Decision (owner, MGR 2026-10-15)

1. **The default stays `"shadow"`.** Counting is opt-in: `run_adversaries(..., adapter_ops="count")` or CLI `--adapter-ops count`. `"off"` is unchanged.
2. **Flipping the default to `"count"` is a separate decision, taken later, after real host attestations have run in shadow.** This slice does not make that flip and does not prepare it beyond the pin below. A change of the default needs its own issue and its own spec; until then a test pins the default.
3. Because the default does not change, shadow data from real hosts is not a precondition of this slice.

## Behaviour

`adapter_ops` takes three values, any other raises `ValueError` (CLI: usage error, exit 2, nothing on stdout):

| Mode | An omitted operation | An implemented operation | Counts toward a level |
|---|---|---|---|
| `"shadow"` (default) | `not_executed` | `shadow:<outcome>`, as today | no |
| `"count"` | `not_executed` | `<outcome>`, with no prefix | `defended` only |
| `"off"` | `not_executed` | not called, `not_executed` | no |

The probes, their controls, the guard (daemon worker, `timeout_s`) and the outcome vocabulary are untouched. `"count"` changes one thing: `_optional_op` returns the probe's outcome without the `shadow:` prefix. Everything else follows from code that already exists:

- `_level_achieved` counts an adversary only when its outcome is exactly `defended`, so `defended` raises the level, and `undefended`, `indeterminate` and `not_executed` stop it. No change to `_level_achieved`, `build_attestation` or `validate_attestation`.
- A failing attribute read, a non-callable method, a raise, a timeout and a malformed answer are `indeterminate` (plain, in `count`). `indeterminate` sinks the level like `errored`; the run returns normally. The classifier or the provider failing is therefore a degraded level, never a crash and never a `defended`.
- `KeyboardInterrupt` and `SystemExit` propagate, as before.
- `adapter=None` ignores the setting, as before.
- The mode is not written into the attestation (the attestation keeps exactly its seven keys). The outcome strings tell a reader which mode ran for an operation that ran: `shadow:defended` was not counted, `defended` was. `not_executed` carries no such mark, in any mode.
- The mode is passed down as a parameter only: no module-level state, so concurrent runs with different modes cannot affect each other.
- **Trust boundary.** A counted level rests on the adapter's own reports: the probes pass their fault label in the clear, so an adapter can be written to answer them with canned values. `"count"` makes the harness count what the host reports; it does not prove the host's implementation is real. This is stated in the module docstring, and the later spec that flips the default must address it first.

The vocabulary has no `failed` outcome. The reference case in #197 ("a deliberately wrong implementation reports `failed`") is `undefended` here, which does not count and keeps the level below the adversary's level.

## Levels document text (kept outside this repository)

The frozen levels document (`2026-07-26_bytedigger_conformance_levels.md`, sections 6 and 9.1) is not in this repository. Its amendment, to be applied by whoever owns it, is:

> §6 / §9.1. The adapter has two required operations (`freeze`, `evaluate`) and optional operations, one per adversary group: `lint_vacuity` (ADV-3), `record_oracle_step` (ADV-4), `record_gate_step` (ADV-5), `admit_ledger_row` (ADV-6), `attest_step` (ADV-7 and ADV-10), `attribute_block` (ADV-8). An omitted operation is `not_executed` and the level stops below that adversary. A host attestation counts the optional operations only when the harness is run with `--adapter-ops count`; by default they are reported as `shadow:<outcome>` and the attestation tops out at BD-L1. The default is `shadow` until real host attestations have run in shadow and the owner decides otherwise.

This file and the module docstring carry the same text in the repository.

## Acceptance criteria (test file `engine_py/tests/test_bd197_adapter_ops_count.py`)

`conformance.*` imports go inside test bodies (bd#24). Stub adapters live in the test module, subclass nothing from the harness, and answer conformingly with deterministic code of their own (`stub_passability.lint_red_file` for ADV-3, `hashlib` for ADV-8, fixed tool lists for ADV-7 and ADV-10). A *full conforming stub* implements `freeze`, `evaluate` and every optional operation correctly. Subscription and API-token variants of the stub differ only in how `identity` is built (a session probe, a token read) and in how a failure is raised.

- **AC1 (count raises the level).** A full conforming stub under `run_adversaries(adapter=..., adapter_ops="count")` gives all nine `ADVERSARIES` as exactly `defended`. `build_attestation(..., level_claimed="BD-L3")` has `level_achieved == "BD-L3"` and `validate_attestation` returns no complaints. The same stub under `"shadow"` has ADV-3..ADV-8 and ADV-10 as `shadow:defended`, `level_achieved == "BD-L1"`, and `level_claimed="BD-L2"` draws a complaint naming `level_claimed`.
- **AC2 (BD-L2 without BD-L3).** A stub that implements only the ADV-3..ADV-6 operations, under `"count"`: ADV-3..ADV-6 `defended`, ADV-7, ADV-8, ADV-10 `not_executed`, `level_achieved == "BD-L2"`, and `level_claimed="BD-L3"` draws a complaint naming `level_claimed`.
- **AC3 (omitted stays absent).** Under `"count"`, a stub with only `freeze`/`evaluate` gives ADV-3..ADV-8 and ADV-10 as `not_executed` with zero calls to any optional method, and `level_achieved == "BD-L1"`. One operation set to `None`, and one operation deleted from an otherwise full stub, each give that adversary `not_executed` and cap the level below it: removing `lint_vacuity` caps at BD-L1, removing `attribute_block` caps at BD-L2.
- **AC4 (wrong implementations are not counted).** Under `"count"`, for each operation, a deliberately wrong implementation gives plain `undefended` (not `shadow:`, not `not_executed`) and `level_achieved` below that adversary's level. Wrong implementations: `lint_vacuity` always `True`; `record_gate_step` recording a raising gate as absent; `record_oracle_step` counting a failed load as a rejection; `admit_ledger_row` accepting every row; `attest_step` reporting no mismatch for a model drift; `attest_step` reporting no escape for a tool outside the declared set; `attribute_block` with a digest of a different content.
- **AC5 (degrade, don't fail).** Under `"count"`, for each operation: a raise, a sleep of 5 s against `timeout_s=0.2` (the run returns in under 2.5 s and every live non-main thread is a daemon), a malformed return, and a non-callable attribute each give plain `indeterminate`, and `run_adversaries` returns normally. `level_achieved` is below the adversary's level. A `subscription-session` stub whose session probe raises `ConnectionError`, and an `api-token` stub whose missing token raises a custom `Exception` subclass, give `indeterminate` for the operations that use them. A failing `getattr` on the adapter gives `indeterminate`. `KeyboardInterrupt` and `SystemExit` from an operation propagate (`pytest.raises`).
- **AC6 (both billing modes).** AC1 and AC4 are parametrised over the subscription-session stub and the api-token stub: the same level, the same outcomes. The harness source and the stubs name no provider the harness depends on (the #195 provider-agnostic scan stays green).
- **AC7 (default is shadow, pinned).** `inspect.signature(run_adversaries).parameters["adapter_ops"].default == "shadow"`; the CLI with no `--adapter-ops` gives `shadow:` outcomes for a full conforming stub and `level_achieved == "BD-L1"`. A change of this default fails this test, which is the guard for the decision above.
- **AC8 (kill switch).** `"off"` with a full conforming stub: zero optional-method calls, all optional adversaries `not_executed`, ADV-1/ADV-2 as under `"shadow"` and `"count"`. An unknown value (`"on"`, `"COUNT"`, `None`, `1`) raises `ValueError`.
- **AC9 (scoped run).** `only=("ADV-3",)` under `"count"` returns `{"ADV-3": "defended"}` with `lint_vacuity` called twice and `freeze`/`evaluate` zero times. `only=("ADV-7", "ADV-10")` calls `attest_step` four times.
- **AC10 (no interference).** For a stub with the optional operations, ADV-1 and ADV-2 equal the outcomes of the same stub without them, in every mode. `adapter=None` returns the same mapping for `"shadow"`, `"count"` and `"off"` and equals today's all-`defended` result.
- **AC11 (CLI end-to-end, subprocess).** A full conforming stub with `--adapter-ops count --level-claimed BD-L3`: exit 0, JSON `level_achieved == "BD-L3"`, every `adversaries` value `defended` except ADV-9 `not_executed`, and the JSON has exactly the seven attestation keys (no mode key). A deliberately wrong stub with the same flags: exit 1, a complaint on stderr naming `level_claimed`, the JSON written. `--adapter-ops bogus`: exit 2 with nothing on stdout. `--adapter-ops shadow` and `off` behave as before.
- **AC12 (pinned, must still pass).** `__all__` is unchanged; the harness source names no provider; the existing groups 1 to 5 tests and the #195 and #58 suites pass unmodified.

## Not in scope

- Changing the default to `"count"`. That is a separate owner decision, taken after real host attestations have run in shadow, with its own issue and spec.
- ADV-11 and ADV-9. ADV-9 stays declarative and `not_executed`; ADV-11 is not in the executable set, and if the levels document adds it, it becomes one more registry row.
- Any change to `_level_achieved`, `build_attestation`, `validate_attestation`, `oracle.py`, `bd_l2`, `bd_l3`, `attest` or `tokens`.
- Editing the frozen levels document. Its amendment is given above for its owner.

## Sibling audit

Every earlier group's test file and pins file (shadow prefix, `adapter_ops` validation, CLI choices) because the mode set and `_optional_op` change; `test_bd195_harness_adapter_seam.py` (omitted stays `not_executed`, `__all__`, provider-agnostic scan); `test_bd58_adversary_harness.py` (surface equals `__all__`); `test_bd63_r35_enforcement_falsifiable.py` (calls `run_adversaries()`). Any sibling that asserts the exact set of CLI choices or the exact `ValueError` text for `adapter_ops` is updated in the same change and listed in the PR.
