# bd#271 gate r1: spec + RED audit (ADV-7/ADV-10 attestation adapter operation)

- Spec: `docs/decisions/2026-10-10-bd271-adapter-attestation-op.md`
- RED: `engine_py/tests/test_bd271_adapter_attestation_op.py` (155 fail today)
- Pins: `engine_py/tests/test_bd271_adapter_attestation_op_pins.py` (18 pass today)
- Code under change: `engine_py/bytedigger_engine/conformance/harness.py` (`_guarded_value_call` l.387, `_optional_op` l.537, `_LEVEL_OF` l.135, level loop l.634); reused rule: `attest.capability_escapes` (`attest.py` l.85)
- Preflight facts: all steps ok, siblings green (taken from the brief; nothing was re-run, this audit is read-only).

## Step 1: spec-internal consistency (literal-token drift)

These tokens match across the spec, the RED constants, the pins stubs and both CLI factory sources: `attest_step`, `"model_drift"`, `"tool_escape"`, `model_requested`, `model_observed`, `tools_declared`, `tools_observed`, `shadow:`, `adapter_ops` / `--adapter-ops`, `_ADAPTER_OPS`, `_optional_op`, `_guarded_value_call`, `_model_family_of`, `_models_of`, `_tools_of`, `_probe_model_family`, `_probe_tool_set`. The RED reads `_model_family_of` and `_ADAPTER_OPS` (the two private names the spec itself fixes); the extractor and probe names are not asserted, which is correct (they are implementation detail).

Call counts agree between the spec and the tests: AC2 (2 per single-adversary run, 4 in order `None, "model_drift", None, "tool_escape"` in a full run), AC3 (2), AC4 (1; in a full run `[None, None]` when both controls mismatch and `[None, None, ESCAPE]` / `[None, DRIFT, None]` for the mixed cases), AC5 hung (1 on a control hang, 2 on a fault hang).

Drift found (advisory only):
- The spec (l.99) and the RED docstring say no test reads the clock. The RED calls `time.monotonic()` in four timeout tests (bounding elapsed time) and the CLI tests do not pass `--timestamp`. The spec's own wording is "wall clock or the date", which is true; the RED docstring is the wider sentence. Same wording issue as bd#269 Finding 1.
- The spec l.72 says the escape rule returns "the sorted observed tool heads that are not among the declared entries' heads". The real `capability_escapes` strips the head only from the declared entries; an observed tool is compared as written (`tool not in heads`). So an observed `"Bash(ls)"` against declared `"Bash(x:*)"` is an escape. The prose and the code differ on this point and no test pins it. See Finding 2.
- The spec's module-docstring amendment (rules 2 and 3, ADV-7/ADV-10 row) has no test. See Finding 3.

## Step 1.5: rule-overlap simulation

**Dispatcher.** Order in `run_adversaries` (adapter branch): `_ADAPTER_ADVERSARIES` (ADV-1, ADV-2), then `_ADAPTER_OPS`, then `not_executed`. Today ADV-7 and ADV-10 fall to `not_executed`; after GREEN adds two rows they match `_ADAPTER_OPS` first. ADV-3..ADV-6 keep their own rows; ADV-8 still falls through. No overlap. Both rows name the same method, which is fine: `_optional_op` looks the method up per adversary and each probe makes its own calls.

**`_optional_op`, in order.** off gives `not_executed`; getattr that raises gives `shadow:indeterminate`; `None` gives `not_executed`; non-callable gives `shadow:indeterminate`; otherwise the probe, any Exception gives `shadow:indeterminate`, result prefixed `shadow:`. AC6 absent / `None`, AC8 off, AC5 non-callable and AC5 interrupts all land on the branch the tests expect.

**ADV-7 probe, all branches.**
- Control: sentinel (raise or timeout) -> indeterminate, 1 call. Malformed (not a `Mapping`, key missing, value not exactly a non-empty `str`) -> indeterminate, 1 call. No family on either side -> indeterminate, 1 call. Mismatch -> indeterminate, 1 call (AC4). Same family -> go on.
- Fault: sentinel / malformed / no family -> indeterminate, 2 calls. Mismatch -> defended. Same family -> undefended.
- First-match checks: "reports the same model for every step" is control same-family, fault same-family: undefended (AC1). "Reports a drift for every step" is control mismatch: indeterminate after one call, not defended (AC4). "Same family, other tier, on the control" is not a mismatch, so it reaches the fault call and ends defended when the fault is a real drift (AC4 tier test). A malformed fault answer is checked before the family comparison, so it cannot be read as "same family" and end undefended.

**ADV-10 probe, all branches.** Same shape with the escape rule. Control: raise / timeout / malformed / escape -> indeterminate (1 call); no escape -> go on. Fault: sentinel / malformed -> indeterminate (2 calls); escape -> defended; no escape -> undefended. "Observed equal to declared" ends undefended; "observed `Bash` against declared `Bash(x:*)`" is no escape, so ends undefended on a fault call (AC3 head-match test); `bash` against `Bash(x:*)` is an escape and ends defended (AC3 case test). Both match the tests.

**Independence in a full run.** The four calls are sequential and each probe reads only its own keys, so a malformed or mismatching answer for one adversary cannot change the other's outcome; the call logs `[None, None, ESCAPE]`, `[None, DRIFT, None]` follow from that.

## Step 2: spec sections vs AC table

| Spec path | AC |
|---|---|
| ADV-7 control raise / timeout | AC5 raising control, hung control (1 call) |
| ADV-7 control malformed | AC5 malformed control (19 shapes, incl. whole-answer shapes, str subclass, no family) |
| ADV-7 control mismatch | AC4 (one call, every-step mismatch, one-sided mismatch) |
| ADV-7 control same family other tier | AC4 tier test |
| ADV-7 fault raise / timeout / malformed / no family | AC5 raising fault, hung fault (2 calls), malformed fault |
| ADV-7 fault mismatch / same family | AC2 / AC3 (observed == requested, 4 same-family ids) |
| ADV-10 control (all branches) | AC4, AC5 (17 malformed shapes) |
| ADV-10 fault (all branches) | AC2, AC3, AC5 |
| Escape rule (case, head match) | AC3 two tests |
| Empty tool sets well formed | AC2 |
| Extraction on the guard worker | AC5 hanging mapping (4), hanging list (4) |
| Interrupts propagate | AC5 (8) |
| Non-callable, session down, missing credential | AC5 |
| Absent / `None` method, independence | AC6 pins + RED alone-test |
| Off | AC8 pins, AC11 off leg |
| Shadow prefix / not counted | AC7, exact strings throughout |
| Family rule | AC9 (12 rows) |
| Probe source clock/file/provider-free | AC12 RED test |
| `adapter=None`, `__all__`, provider scan | AC12 pins |
| Probes distinct | AC12 `is not` test |

Every probe branch has an AC and every AC has a producing path. Not tested: the docstring amendment (Finding 3), and the observed-tool-with-operand case (Finding 2). No mismatch.

## Step 3: RED adequacy

**Count.** AC1 2, AC2 15 (2 + 1 + 10 + 2), AC3 9 (1 + 4 + 1 + 1 + 1 + 1), AC4 6, AC5 104 (72 malformed control/fault + 2 + 4 raising + 4 hung + 2 non-callable + 2 session + 2 credential + 4 hanging mapping + 4 hanging list + 8 interrupts), AC6 1, AC7 1, AC9 12, AC11 2, AC12 3. Total 155, which matches. Pins: AC6 5 (2 + 1 + 1 + 1), AC8 7, AC10 2, CLI usage 1, AC12 3. Total 18, which matches.

**Fails at assert time, not collect time.** `conformance.*` imports are inside `_h()` / `_oracle()` and the stub bodies. Today ADV-7 and ADV-10 give `not_executed`, so every `== {adv: "shadow:..."}` assert fails; the call-log asserts fail on empty logs; the interrupt cases fail with "DID NOT RAISE"; `_model_family_of` and `_ADAPTER_OPS["ADV-7"]` fail with AttributeError / KeyError inside the test body; the CLI cases fail on the JSON assert. One half of AC1 (`run_adversaries(only=(adv,))` is `defended` with no adapter) passes today but sits in front of a failing assert in the same test, so the test as a whole fails. I found no test among the 155 that passes today.

**No stub-passability.** The stubs are the adapter, the input of the unit under test; the harness runs for real. Stubs subclass a local `_Base` and judge nothing: they report facts. Nothing mocks `harness`.

**Passable by a correct GREEN (unreachable asserts, call counts, timing).** I walked every assert against the spec rules and the real guard (`_guarded_value_call(fn, arg, timeout_s, extract)`, extract on the worker):
- `_facts` builds consistent facts: default declared `("Read","Bash(x:*)")`, used `("Read","Bash")`, so a control has no escape, and `escape_tool="Write"` is outside the declared heads. `requested="alpha-large-2"`, drift observed `"beta-small-1"`. Every `drift=` / `escape=` override matches its test's intent (checked the mixed full-run cases: `drift=True if f is None else None` gives a control mismatch for ADV-7 only, `[None, None, ESCAPE]`).
- Call counts: a control-stop makes 1 call, a fault-stage result makes 2; the full-run lists are `[None, DRIFT, None, ESCAPE]`, `[None, None]`, `[None, None, ESCAPE]`, `[None, DRIFT, None]`. All follow from "one probe after another, each stopping at its first sentinel or control failure". The spec requires ADV-7 to run before ADV-10 (dict order of `ADVERSARIES`), which the order assert relies on; `ADVERSARIES` lists ADV-7 before ADV-10.
- Malformed shapes: `none`, `bare-true`, `bare-string`, `list`, `empty-dict` are applied to the dict, so each is a whole-answer mutation; all are malformed by the spec rule. `_sub_item` builds a list whose first item is a `str` subclass (rejected by "exactly `str`"). The "no family" rows (`4o-mini`, `-x`) are indeterminate on either side and at either call. The `requested-*` fault rows keep the control good, so the call log is `[None, fault]`.
- Hung cases: the stub sleeps 5 s on a daemon worker, the guard joins 0.2 s, so `elapsed < 2.5` holds as long as the probe stops at the first sentinel. A fault-stage hang first runs a good control inside its own 0.2 s window, which is ample for a dict return.
- Hanging mapping / list: extraction must run on the worker, otherwise `2 s > 1.5 s` and the test fails; a correct GREEN passes. Note that `_HangingMapping` blocks `[]`, `.get` and `in` alike, so any extraction style is cut. For `_HangingList`: an extractor using `isinstance(v, (list, tuple))` iterates the subclass on the worker and times out; one that checks `type(v) in (list, tuple)` rejects it at once as malformed and also returns `indeterminate` in time. Both pass; only the first exercises the worker. Advisory (Finding 4).
- AC7: `_LEVEL_OF` puts ADV-7 and ADV-10 in BD-L3 and the rest in BD-L1/BD-L2, so with the two shadow outcomes and everything else `defended` the loop grants BD-L2 and stops; `validate_attestation` complains about `level_claimed`. Correct GREEN passes.
- AC9: pure function; `"Ωmega-1"` and `"1alpha"` need an ASCII-only letter run (a plain `str.isalpha` would fail the Ω row, as intended). `"   "` strips to empty, `None`.
- AC11: the CLI flags exist (same set used by bd#269's RED); only `freeze`/`evaluate`/`attest_step` are present, BD-L1 claimed with ADV-1/ADV-2 defended, exit 0.
- AC12: `inspect.getsource` works on a module-level function; the six forbidden source words must stay out of the probe functions only (the module-level `_tempfile` import is outside them). The provider words test runs on `src.lower()`, so a docstring in a probe that says "API" is fine but `api_key`/`claude` is not.

No unreachable assert, wrong call count or impossible timing bound found.

**Wrong-GREEN kill table.**

| Wrong GREEN | Killed by |
|---|---|
| Reuses the engine `_adv_7` / `_adv_10` or ignores the answer | AC1 (`shadow:undefended` expected), AC2, AC3 |
| Control skipped | AC4 (`faults == [None]`), AC2 call logs |
| Mismatch/escape on the control read as defended | AC4 (every-step host) |
| Control mismatch continues to the fault call | AC4 call-log asserts |
| Same-family tier/version/case change read as drift | AC3 four same-family ids, AC4 tier test |
| Family from a vendor/tier list or the whole id | AC9 rows, AC3 |
| Family not lowercased / not stripped / non-ASCII letters accepted | AC9 rows, AC3 `ALPHA-large-2`, `" alpha_x"` |
| Escape matched case-insensitively | AC3 `bash` test |
| Escape matched on exact string rather than head | AC3 head-match test, AC4 control `Bash` test |
| Declared / observed swapped | AC2 and AC3 (the two sets are not symmetric) |
| Empty tool sets rejected | AC2 empty-set test |
| `str`, `set`, `None`, non-`str` item accepted as a tool list | AC5 declared/observed shapes, both calls |
| `str` subclass accepted (model or tool item) | AC5 `*-str-subclass*` rows |
| Non-`Mapping` accepted, or `dict` required | AC5 whole-answer rows; AC2 mappingproxy / opaque mapping |
| One adversary reading the other's keys | AC5 two key-isolation tests, full-run isolation |
| Truthiness / lenient reading of malformed answers | AC5 (all 72 malformed rows) |
| Raise / timeout read as undefended or defended | AC5 raising, hung |
| Extraction off the worker | AC5 hanging mapping, hanging list |
| `except BaseException` swallowing interrupts | AC5 interrupts (8) |
| Non-daemon worker or proceeding after a control hang | AC5 hung (`_non_daemon_threads`, call count) |
| `shadow:` dropped or outcome counted | exact strings, AC7 |
| Calls the method outside `only` scope, or wrong order | AC2 call logs |
| Dispatch ignores `adapter_ops="off"` | AC11 off leg, AC8 pin |
| Probe reads a clock / file / provider word | AC12 RED test |
| One shared probe for both adversaries | AC12 `is not` test |

**Pins: vacuity.** They pass today because the operation does not exist and stay true after GREEN: omitted/`None` method stays `not_executed` with zero calls, off, independence in both directions, ADV-8 stays `not_executed`, no interference with ADV-1..ADV-6 for a digest and an accept-all adapter, the pinned surface and the no-adapter map. `test_ac6_an_adapter_with_the_other_operations_but_not_this_one` needs ADV-3..ADV-6 to return a `shadow:` string from the `_Lint`, `_Steps`, `_Ledger` stubs; those are the same stubs the bd#267 and bd#269 pins use and the sibling run is green. AC10 is weak by design (ADV-7/ADV-10 are not in the compared set); it shows only that adding the rows leaves ADV-1..ADV-6 alone.

**Clock.** No test reads `datetime`, `time.time` or `date.today`. Four `time.monotonic()` pairs bound elapsed time.

## Step 4: reachability (§1y)

| AC | Point (prod) | Host (enclosing function) | Test path |
|---|---|---|---|
| AC1-AC5 | new `_probe_model_family`, `_probe_tool_set`, `_models_of`, `_tools_of`, `_model_family_of`, via `_ADAPTER_OPS["ADV-7"]` / `["ADV-10"]` | `_optional_op` (l.537), called from `run_adversaries` adapter branch | `h.run_adversaries(only=(adv,), adapter=stub)` and the full run |
| AC5 timeout / raise / extraction | `_guarded_value_call` worker and join (l.387) | the probes | same, `timeout_s` 0.2 / 0.5 / 5.0 |
| AC3 escape rule | `attest.capability_escapes` (l.85) | `_probe_tool_set` | same |
| AC6 | `_optional_op` getattr / `None` branch | `run_adversaries` | pins, RED alone-test |
| AC7 | level loop (l.634) | `build_attestation` / `validate_attestation` | direct calls on a real run's output |
| AC8 | `_optional_op` off branch | `run_adversaries` | pins, CLI off leg |
| AC9 | `_model_family_of` | called directly (spec names it) and by `_models_of` | `h._model_family_of(...)` |
| AC10 | unchanged ADV-1..ADV-6 branches | `run_adversaries` | pins |
| AC11 | `_main` then `run_adversaries` | CLI `__main__` | subprocess `python -m bytedigger_engine.conformance.harness` |
| AC12 | module source, `__all__`, `_ADAPTER_OPS` | module | pins and the RED probe-source test |

Every side-effect AC reaches the code through a real entry point. The private names touched by tests are `_model_family_of` and `_ADAPTER_OPS`, both fixed by the spec.

Existing-behaviour contradiction check: `test_bd195_harness_adapter_seam.py` `_NON_ORACLE` expects ADV-7/ADV-10 `not_executed` only for an adapter with no `attest_step`; still true. The bd197, bd267 and bd269 files assert only their own adversaries plus the no-adapter map. No sibling pins `_ADAPTER_OPS` keys.

## Adversarial edges (not covered by the spec's AC table)

- **E1, observed tool with an operand.** Observed `"Bash(ls)"` against declared `"Bash(x:*)"` is an escape under the real `capability_escapes` (observed is compared as written), though the spec's sentence reads "observed tool heads". Not pinned. If GREEN strips the observed head it would disagree with the engine rule the spec says it reuses. See Finding 2.
- **E2, fault-call requested model differs from the control's.** The fault answer is judged on its own two ids; nothing relates it to the control's ids. A host that changes `model_requested` between calls is not caught or flagged. Out of scope (the harness judges one answer at a time); noted so nobody assumes it is measured.
- **E3, attribute read raises.** `attest_step` as a property that raises gives `shadow:indeterminate` through shared g1 code. Unpinned for this row; code unchanged.
- **E4, off before non-callable.** `adapter_ops="off"` with a non-callable attribute gives `not_executed`; guaranteed by the shared order, not pinned.
- **E5, bool or `int` items in a tool list.** The list-holding-a-non-`str` row uses `5`; a `bool` or `None` item takes the same branch. Low risk.
- **E6, same mapping object reused and mutated by the host after return.** Extraction (copy to tuples) runs on the worker immediately, so no late-read hazard. Not pinned.
- **E7, duplicate tool names / ordering.** `capability_escapes` dedupes and sorts; only emptiness matters here. No effect on the outcome.
- **E8, hanging worker count.** Each timed-out call leaves a daemon thread asleep for its remaining 2 to 5 s; tests only assert none is non-daemon. No leak between tests beyond that.

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

1. **MINOR: "reads no clock" wording.** The RED docstring says "No test here reads the clock" while four tests use `time.monotonic()` (the spec's own sentence says wall clock or date, which holds). Reword the docstring to "no test reads the wall clock or the date". Not blocking.
2. **MINOR: observed-tool head rule is described loosely and untested.** The spec says the rule returns "the sorted observed tool heads that are not among the declared entries' heads"; the real `attest.capability_escapes` strips only the declared entries and compares each observed tool as written. GREEN should call `attest.capability_escapes` (as the spec says) rather than re-implement it; a re-implementation that strips observed operands would change behaviour for `"Bash(ls)"`. Either reword the sentence ("observed tools that are not equal to any declared head") or add an observed-operand row. Not blocking.
3. **MINOR: docstring amendment untested.** The spec requires the module docstring to gain the ADV-7/ADV-10 row and to name this operation's shapes in rules 2 and 3, but no assert pins it (same as bd#267 Finding 2 and bd#269 Finding 2). GREEN should do it; keep provider words and any model or vendor name out of the new text, since the pinned provider scan lowercases the whole source.
4. **MINOR (advisory): hanging-list tests do not distinguish extraction style.** A GREEN that accepts only exact `list`/`tuple` rejects `_HangingList` as malformed and passes in time without exercising worker-side iteration. The hanging-mapping tests do exercise it. The spec says "list or tuple"; if subclasses should be accepted, a row with a well-formed `list` subclass would pin that.
5. **MINOR (advisory): AC9 vendor-name scan.** The pinned scan covers provider words only; the spec also forbids model and vendor names in the harness source, which is not machine-checked. The probe-source test lowercases only the six provider words.

No MAJOR findings.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd271-adapter-attestation-op.md sha256:62f8b37a3425822807b172560b4028adec4dd9ade87ab15f5f1f272b3aaafb5d
red: engine_py/tests/test_bd271_adapter_attestation_op.py sha256:2794a998e69878dab4c1d590dc1cb4e5c8605b6d45e7a86b5a2511c39fe31867
-->
