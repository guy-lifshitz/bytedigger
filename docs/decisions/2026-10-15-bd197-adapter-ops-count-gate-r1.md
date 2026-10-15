# bd#197 g6 gate r1: spec + RED audit (opt-in `adapter_ops="count"`)

- Spec: `docs/decisions/2026-10-15-bd197-adapter-ops-count.md`
- RED: `engine_py/tests/test_bd197_adapter_ops_count.py` (92 fail today)
- Pins: `engine_py/tests/test_bd197_adapter_ops_count_pins.py` (19 pass today)
- Code under change: `engine_py/bytedigger_engine/conformance/harness.py` (`_ADAPTER_OPS_MODES` l.689, `_optional_op` l.692 with its three `"shadow:"` sites l.700/l.704/l.709, `run_adversaries` validation l.755, CLI `--adapter-ops` choices l.891). Unchanged and relied on: `_level_achieved` l.785, `build_attestation` l.799, `validate_attestation` l.825, `_guarded_value_call` l.412, the seven probes l.469-l.674.
- Preflight facts: all steps ok; scoped 0 pass / 92 fail; pins 19/0; siblings bd273, bd271, bd269, bd267, bd197-g1, bd195, bd58, bd63 all green (taken from the facts file; nothing was re-run, this audit is read-only). The prescreen MISSING rows are name collisions (`_probe`, `_main`, `_run`, `_ev`, `_LEDGER_AS_OF` in unrelated modules), not callers of this harness.

## Step 1: spec-internal consistency (literal-token drift)

These tokens match across the spec, the RED, the pins and both CLI factory sources: `adapter_ops` / `--adapter-ops`, `"shadow"`, `"count"`, `"off"`, `shadow:`, `defended`, `undefended`, `indeterminate`, `not_executed`, `_optional_op`, `run_adversaries`, `build_attestation`, `validate_attestation`, `level_claimed`, the six method names (`lint_vacuity`, `record_oracle_step`, `record_gate_step`, `admit_ledger_row`, `attest_step`, `attribute_block`) and the ADV-to-method map (`OP_OF` equals `_ADAPTER_OPS`). `LEVEL_OF` in the RED equals `_LEVEL_OF` for ADV-3..ADV-10.

"All nine `ADVERSARIES`" (AC1) and "every value `defended` except ADV-9" (AC11) are consistent: `ADVERSARIES` is the nine-name executable tuple without ADV-9; the published JSON has ten keys because `build_attestation` adds ADV-9 as `not_executed`.

Drift found (advisory only):
- The AC preamble says the stubs wrap `attest.capability_escapes` and `attest.hash_text`. The RED stubs use static tool lists and `hashlib` instead (only `stub_passability.lint_red_file` is wrapped). This is the better choice (the oracle stays independent of the harness's own helper, as in bd#273) but the sentence drifts from the tests.
- The RED docstring says "No test here reads the clock"; the hang tests use `time.monotonic()`. Same wording point as g2 to g5.
- Behaviour: "The outcome strings already tell a reader which mode ran". True for `shadow:defended` vs `defended`; not true for `not_executed`, which `off`, an omitted operation in `count` and an omitted operation in `shadow` all produce. Harmless for the level (none counts), but the sentence overstates.

## Step 1.5: rule-overlap simulation

**`run_adversaries` dispatch.** Validation first (`isinstance(str)` and membership): `"count"` accepted after GREEN; `None`, `1`, `True`, `"COUNT"`, `" count"` still rejected. Then `adapter is None` -> engine probes, mode ignored (AC10). Adapter: ADV-1/ADV-2 -> `_adapter_adversary` (mode-independent, AC10); ADV-3..ADV-8, ADV-10 -> `_optional_op`; ADV-9 and unknown names -> `not_executed`. No overlap.

**`_optional_op` under `"count"`, in order.** `off`? no. getattr raises -> `indeterminate` (AC5 property test). `None` (set or absent) -> `not_executed`, no call (AC3, both forms). Non-callable -> `indeterminate` (AC5). Probe -> its outcome, plain; a probe-level Exception -> `indeterminate`. Every RED case lands on the branch it expects. A GREEN that strips only at l.709 and leaves l.700/l.704 prefixed is killed by the getattr and non-callable rows.

**`_level_achieved`.** Counts only `outcomes.get(a) == "defended"` over `ADVERSARIES`; `shadow:defended`, `indeterminate`, `undefended`, `not_executed` all stop it. ADV-9 is in neither `ADVERSARIES` nor `_LEVEL_OF`, so its forced `not_executed` never caps a level.

**Stub-by-stub against the real probes (the question whether a stub bug hides behind today's ValueError).**
- Good stubs (`GOOD_OPS`) have the same logic as the pins `Full` stub and the pins CLI `Host`, which today produce `shadow:defended` for all seven optional adversaries (pins `test_ac7_default_run_reports_shadow_outcomes_and_bd_l1` and `test_ac7_cli_without_the_flag_is_shadow_and_bd_l1` pass). The only addition is `self._gate()`, a no-op unless `gate_fail=True`. So after GREEN, `count` gives plain `defended` for all seven and BD-L3. Checked per probe: ADV-3 control not flagged, vacuous flagged; ADV-4 `passed` then `load_failure`; ADV-5 `passed` then `failed`; ADV-6 control accepted, no-owner and expired refused (as_of `2026-01-01`); ADV-7 `alpha`/`alpha` then `alpha`/`beta`; ADV-10 control `["Read","Bash"]` within declared, fault adds `Write`; ADV-8 controls differ, probe digest equals `hash_text`.
- Wrong stubs: `_wrong_lint` True on the control -> `undefended`; `_wrong_oracle` `rejected` (in vocabulary, not `load_failure`) -> `undefended`; `_wrong_gate` `absent` -> `undefended`; `_wrong_ledger` accepts the no-owner row -> `undefended`; `_wrong_model` same family on drift -> ADV-7 `undefended` (ADV-10 still `defended`, level BD-L2); `_wrong_tools` no escape -> ADV-10 `undefended` (ADV-7 `defended`, level BD-L2); `_wrong_attribute` digest of content plus newline, controls still differ -> `undefended`. None of them is an `indeterminate` in disguise, so every AC4 row is a real `undefended` assertion.
- Failure stubs: `_raiser(RuntimeError)`, `_hanger` (0.2 s join), `lambda: 42` (fails every extractor and `_guarded_bool_call`), `"not a function"`, `property(broken)` (RuntimeError escapes `getattr(..., None)` and hits the `except Exception`), `gate_fail` with `ConnectionError` / `ProviderUnavailable(Exception)` -> all `indeterminate` on the first guarded call. `KeyboardInterrupt` / `SystemExit` are boxed on the worker and re-raised on the caller, past `_optional_op`'s `except Exception`.
- AC2 `_only(...)` inherits `freeze`/`evaluate` from `_Base`, so ADV-1/ADV-2 are `defended` and ADV-3..ADV-6 alone (with the required operations) give BD-L2. AC3 omitted `attest_step` removes both ADV-7 and ADV-10 -> BD-L2; omitted `attribute_block` -> BD-L2; any L2 operation -> BD-L1.

**Spec claim (d) confirmed.** ADV-3..ADV-6 `defended` plus ADV-1/ADV-2 reach exactly BD-L2; all nine `defended` reach BD-L3 through the unchanged `_level_achieved`.

## Step 2: spec sections vs AC table

| Spec path (Behaviour / Decision) | AC |
|---|---|
| `count`, implemented op -> plain outcome, `defended` counts | AC1, AC9, AC11 |
| `count`, omitted / `None` op -> `not_executed`, not called | AC3 |
| `count`, wrong op -> plain `undefended`, level below | AC4, AC11 wrong host |
| getattr fails, non-callable, raise, timeout, malformed -> plain `indeterminate`, run returns | AC5 |
| provider/classifier failure (both billing shapes) -> degraded level | AC5 provider rows, AC6 |
| `KeyboardInterrupt` / `SystemExit` propagate | AC5 interrupts (14) |
| default `shadow`, pinned | AC7 (pins: signature, default run, CLI default) |
| `off` kill switch, unknown value -> ValueError / exit 2 | AC8, AC11 (pins) |
| `adapter=None` ignores the mode | AC10, AC12 pin |
| ADV-1/ADV-2 independent of ops and mode | AC10 |
| mode not written into the attestation | none (Finding 3) |
| module docstring / levels-document text | none (Finding 4, as g2 to g5) |

Every terminal and error path of `_optional_op` under `count` has an AC except the probe-level `except Exception` (l.707), which no adapter behaviour can reach because every adapter call is inside `_guarded_value_call` (edge E5). No AC asserts a post-condition without a producing path. No mismatch.

## Step 3: RED adequacy

**Count.** AC1 4, AC6 1, AC2 1, AC3 13, AC4 14, AC5 51 (7 x raise, hang, malformed, non-callable, getattr = 35; provider 2; interrupts 14), AC9 2, AC10 4, AC11 2. Sum 92, matching the preflight. Pins: AC7 3, AC8 10, AC11 3, AC12 3 = 19, matching.

**Fails at assert time, not collect time.** All `conformance.*` imports are in `_h()` / `_oracle()` / stub bodies; module level holds only constants, functions and a `textwrap` string. Every RED test reaches `adapter_ops="count"` (or `--adapter-ops count`) before any assertion that could fail for another reason: AC1-shadow and AC10 deliberately run `count` first; AC6 runs `adapter_identity` (passes) then `count`. Today each fails with the ValueError, the interrupt rows with an unexpected ValueError inside `pytest.raises(KeyboardInterrupt|SystemExit)`, the CLI rows on `returncode == 2`. No collection error.

**No stub-passability.** The stubs are the adapter, i.e. the input of the unit under test; the harness and its probes run for real. Nothing patches or mocks `harness`.

**Asserts a correct GREEN cannot satisfy: none found.** Hang rows are single-call (first guarded call times out), so `elapsed < 2.5` holds at about 0.2 s; workers are daemons. `validate_attestation(att) == ()` on the AC1 BD-L3 attestation holds (recomputed level equals achieved; all names present; ADV-9 present). CLI factory module names differ between RED and pins and each is written to its own `tmp_path`.

**Billing parametrisation.** AC1, AC4 and AC5-provider run over both stubs; the harness never sees a billing mode (identity dict and exception class differ only), so these rows prove provider-agnostic behaviour, which is what the harness can promise. The principle "both modes covered by tests" is met at the stub level.

**Wrong-GREEN kill table (focus b).**

| Wrong GREEN | Killed by |
|---|---|
| `shadow:` prefix stripped in every mode | pins AC7 default run + CLI default, RED AC1 shadow leg, pins AC11 explicit shadow |
| default flipped to `count` | pins AC7 signature, default run, CLI default |
| `count` added to `_ADAPTER_OPS_MODES` but not to CLI choices | AC11 RED (both) |
| prefix stripped only for `defended` (others still `shadow:`) | AC4 (plain `undefended`), AC5 (plain `indeterminate`) |
| l.700 / l.704 left as `shadow:indeterminate` | AC5 getattr and non-callable rows |
| `count` runs engine probes `_PROBES[name]()` instead of the adapter | AC4 (14), AC3 (13), AC5 |
| `None` / absent read as `indeterminate` or `defended` in count | AC3 |
| `indeterminate` or `undefended` mapped to `defended` | AC4, AC5, AC1 level asserts |
| case-folding / stripping the mode string | pins AC8 (`COUNT`, `Count`, `" count"`) |
| `except BaseException` around the count path | AC5 interrupts |
| attest_step answered once and shared by ADV-7/ADV-10 | AC9 (four calls), AC4 per-adversary rows |
| `count` changes ADV-1/ADV-2 or `adapter=None` | AC10 |
| `_level_achieved` / `build_attestation` changed to count `shadow:` | AC1 shadow leg, pins AC7, bd58 schema pin |

**Focus (a): can `count` count what must not count?** Indeterminate and shadow values cannot reach `defended`: `_optional_op` only removes a prefix, and in `shadow` the prefix is always added; `_level_achieved` is exact-string. ADV-7/ADV-10 share the method but each probe makes its own control and fault call and is judged separately (AC4 shows one `undefended` while the other stays `defended`; AC3 shows deleting the method removes both). ADV-9 is declarative: not in `_ADAPTER_OPS`, not in `ADVERSARIES`, forced to `not_executed` by `build_attestation`, never level-bearing. The residual risk is the trust model (edge E1), not a code path.

**Focus (c): siblings.** Grep over `engine_py/tests`: no sibling asserts the exact CLI choice set (`{shadow,off}`, "invalid choice", "choose from") or the `ValueError` text ("must be one of" appears only in an unrelated phase-5 test). The sibling unknown-value lists are `["enforce", "", "SHADOW", None, True]` (bd197-g1, bd267, bd269, bd271, bd273 pins) and none contains `"count"`; the CLI usage-error siblings use `"bogus"` and assert only `"adapter-ops" in r.stderr`. Every sibling runs `shadow`/`off` only. So the spec's "update in the same change" list is empty and AC12's "unmodified" holds. bd58 pins the attestation key set; bd195/bd273/bd271/bd269 AC12-style scans read probe sources that GREEN does not touch.

## Step 4: reachability (§1y)

| AC | Point (prod) | Host (enclosing function) | Test path |
|---|---|---|---|
| AC1-AC4, AC9 | `_optional_op` return (l.709) without prefix under `count` | `_optional_op`, from `run_adversaries` adapter branch (l.766) | `h.run_adversaries(adapter=stub, adapter_ops="count")`, full and `only=` |
| AC5 getattr / non-callable | `_optional_op` l.700 / l.704 | same | `only=(adv,)`, `timeout_s` 0.5 |
| AC5 raise / hang / malformed / provider / interrupts | `_guarded_value_call` worker, join, re-raise (l.412) | the seven probes | same, `timeout_s` 0.2 / 0.5 / 5.0 |
| AC1, AC2, AC4, AC5 level | `_level_achieved` (l.785) | `build_attestation` / `validate_attestation` | direct calls on real run output |
| AC7, AC8 | `run_adversaries` default and validation (l.742, l.755) | `run_adversaries` | pins, `inspect.signature`, direct calls |
| AC10 | adapter=None branch, `_adapter_adversary` | `run_adversaries` | direct calls in three modes |
| AC11 | argparse choices (l.891), `_main` -> `run_adversaries` | CLI `__main__` | subprocess `python -m bytedigger_engine.conformance.harness` |
| AC12 | `__all__`, module source | module | pins |

Every side-effect AC reaches the code through a public entry point; the tests touch no private name.

## Adversarial edges (not covered by the spec's AC table)

- **E1, canned answers keyed on the fault label reach BD-L3 (trust boundary).** The probes hand the fault in the clear (`"oracle_load_failure"`, `"gate_raises"`, `"model_drift"`, `"tool_escape"`) and the host only reports. The RED's own "full conforming" stubs for ADV-4, ADV-5, ADV-7 and ADV-10 are canned (`load_failure if fault else passed`) and reach BD-L3 under `count`. This was harmless while everything was `shadow:`; with `count` these reports become level-bearing. Inherited from the g2 to g5 frozen specs and mitigated here by keeping `shadow` the default, but nothing in this spec or the docstring says that a counted BD-L2/BD-L3 rests on the adapter's own reports.
- **E2, mode held in module state.** All tests are sequential with the mode passed per call. A GREEN that implements `count` by a module-level toggle (set on entry, reset on exit) passes every row, yet leaks `count` into a concurrent `shadow` run, or into the next run after a `KeyboardInterrupt` skips the reset. Not pinned.
- **E3, mode written into the attestation by `_main`.** The spec says the mode is not written. bd58 pins `build_attestation`'s keys, but a key added in `_main` after `build_attestation` (or a CLI-only wrapper) passes AC11.
- **E4, provider failures that are `BaseException` but not `Exception`.** `asyncio.CancelledError` (and `GeneratorExit`) from a session- or token-backed provider is re-raised by `_guarded_value_call` like `KeyboardInterrupt`, so the run crashes instead of degrading. Pre-existing, but it now sits in a level-bearing path; the "degrade, don't crash" principle is only tested with `Exception` subclasses.
- **E5, the probe-level `except Exception` (l.707) in `count`.** Not reachable from an adapter (every adapter call is guarded), so no test covers it; a GREEN that rewrites `_optional_op` and drops it is undetected. Low risk.
- **E6, `not_executed` is mode-ambiguous.** `off`, omitted-in-`count` and omitted-in-`shadow` publish the same string (see Step 1). Harmless for the level.

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

1. **MINOR: stub-wrapping sentence drifts.** The AC preamble names `attest.capability_escapes` and `attest.hash_text` as wrapped; the RED uses static lists and `hashlib`. Keep the tests; reword the spec sentence (or note it in the PR).
2. **MINOR: "reads no clock" wording.** The RED docstring says no test reads the clock; the hang rows use `time.monotonic()`. Reword to "wall clock or date". Not blocking.
3. **MINOR: "mode not in the attestation" untested (E3).** Add to `test_ac11_cli_count_full_stub_reaches_bd_l3` an assert that `set(att)` is exactly the seven §4 keys, or accept it as advisory. GREEN must not add a mode key anywhere.
4. **MINOR: docstrings untested.** Module docstring rule 4 ("never counts toward a level"), rule 5 (`"shadow" | "off"`, `--adapter-ops {shadow,off}`) and the `run_adversaries` docstring ("must be shadow or off") become false after GREEN; update them with the spec's levels text. Keep the words `subscription`, `api-token`, `api_key` and any vendor name out of that text: the pinned scan lowercases the whole harness source. The spec's amendment paragraph is safe as written.
5. **MINOR: trust boundary (E1).** Put one sentence in the module docstring and the levels text: under `count` the optional adversaries are judged on the adapter's own reports. Carry E1 into the later spec that would flip the default.
6. **MINOR: mode via parameter only (E2).** GREEN should keep passing `adapter_ops` down to `_optional_op` as it does today, with no module-level state.
7. **MINOR (advisory): `BaseException` provider failures (E4).** Out of this slice's scope (pre-existing guard rule); record an OFI to decide whether `asyncio.CancelledError` should degrade to `indeterminate` on the guard worker.
8. **MINOR (advisory): spec wording on mode visibility (E6).** "The outcome strings tell which mode ran" holds for implemented operations only.
9. **INFO: sibling audit result.** No sibling pins the CLI choice set or the ValueError text and no sibling bad-value list holds `"count"`, so no sibling edit is expected; a GREEN diff that touches a sibling test is out of scope and should be questioned.

No MAJOR findings. Requested PASS/FAIL mapping: APPROVED = PASS.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-15-bd197-adapter-ops-count.md sha256:fd2d77e1fc4535e0376d8ecd485a6ab56eca4fe83fd8340410496d859456a90c
red: engine_py/tests/test_bd197_adapter_ops_count.py sha256:7eb673138421e7f056a8df0d8a4f7649c0ecafd732677443e4327b941e62e161
-->
