# bd#269 gate r1: spec + RED audit (ADV-6 suppression-ledger adapter operation)

- Spec: `docs/decisions/2026-10-10-bd269-adapter-suppression-ledger-op.md`
- RED: `engine_py/tests/test_bd269_adapter_suppression_ledger_op.py` (63 fail today)
- Pins: `engine_py/tests/test_bd269_adapter_suppression_ledger_op_pins.py` (17 pass today)
- Code under change: `engine_py/bytedigger_engine/conformance/harness.py` (`_guarded_value_call` l.379, `_status_of` l.426, `_step_probe` l.436, `_ADAPTER_OPS` l.471, `_optional_op` l.481, `run_adversaries` adapter branch l.551-559)
- Preflight facts: all steps ok, siblings green (taken from the brief; I did not re-run anything, I have no Bash).

## Step 1: spec-internal consistency (literal-token drift)

These tokens match everywhere (spec text, RED constants, pins stubs, both CLI factory sources): `admit_ledger_row`, `accepted`, `issue`, `kill_by`, `as_of`, `"2026-01-01"`, `"2099-01-01"`, `"2020-01-01"`, `shadow:`, `adapter_ops` / `--adapter-ops`, `_ADAPTER_OPS`, `_optional_op`, `_guarded_value_call`, `_accepted_of`, `_probe_ledger_rows`, `_LEDGER_AS_OF`. The method name is spelled the same in the spec, the RED `METHOD` constant, the pins and the CLI factories. No variant spellings.

The three-row table (control, no owner, expired) is the same in the spec and in the RED `CONTROL` / `NO_OWNER` / `EXPIRED` constants. The date arithmetic holds: 2020-01-01 < 2026-01-01 < 2099-01-01 as ISO strings, so a host that compares strings gives the expected answers. A host that reads its own clock (today is 2026-10-10) also gives the expected answers, which is what the spec claims.

Call counts agree between the spec and the tests: AC3 (2 calls for accept-no-owner and for accept-all, 3 for accept-expired), AC4 (1 call), AC5 hung (1 / 2 / 3 depending on which call hangs), AC2 and AC9 (3 calls).

Drift found (advisory only):
- The spec (l.86) says "no test reads the clock", and the RED docstring says "No test here reads the clock". The RED does call `time.monotonic()` (l.388, l.390, l.457, l.459) to bound elapsed time in two timeout tests. That is a monotonic timer, not the wall clock and not the date, and the AC5 "returns in under 2.5 s" assert cannot be written without one. Nothing reads `datetime`, `date.today` or `time.time`. It is only a wording problem. See Finding 1.
- The spec says "The harness reads no clock". The CLI entry (`_main`, l.700-702) calls `datetime.now(...)` when `--timestamp` is not given. The probe itself reads none, and AC12 pins only the probe source, which is what matters. The sentence is wider than the fact. See Finding 1.
- The docstring rules 2 and 3 amendment (spec "Shape of the change", last bullet) has no test. Same situation as the bd#267 Finding 2. See Finding 2.

## Step 1.5: rule-overlap simulation

**Dispatcher (`run_adversaries`, adapter branch).** Order: `name in _ADAPTER_ADVERSARIES` (ADV-1, ADV-2), then `name in _ADAPTER_OPS`, then `not_executed`. ADV-6 is in neither the first set nor, today, the second, so today it falls to `not_executed`. After GREEN adds one row, ADV-6 is first matched by `_ADAPTER_OPS`. ADV-1, ADV-2, ADV-3, ADV-4 and ADV-5 take the same branches as before. ADV-7, ADV-8 and ADV-10 still fall to `not_executed`. No overlap.

**`_optional_op`, in order.** off gives not_executed; a getattr that raises gives shadow:indeterminate; `None` gives not_executed; not callable gives shadow:indeterminate; otherwise the probe runs, an Exception from it gives shadow:indeterminate, and the result is `"shadow:" + outcome`. Each relevant AC against that order:
- AC8, off plus a conforming adapter: the first branch fires, zero calls.
- AC6, method absent or `None`: getattr returns `None`, so `not_executed`, zero calls.
- AC5, non-callable string: falls to the callable check, `shadow:indeterminate`.
- AC5, KeyboardInterrupt / SystemExit: `_guarded_value_call` re-raises anything that is not an `Exception`, and `_optional_op` catches only `Exception`, so both propagate.

**Ledger probe, all branches simulated sequentially (as the spec's Probe section lays them out).**
- Control call (C) returns:
  - sentinel (raise or timeout): indeterminate, 1 call.
  - malformed (`_accepted_of` gives `None`): indeterminate, 1 call.
  - well-formed `False`: indeterminate, 1 call (AC4).
  - well-formed `True`: go on to the no-owner call.
- No-owner call (N) returns:
  - sentinel or malformed: indeterminate, 2 calls.
  - `True`: undefended, 2 calls, the expired row is not asked.
  - `False`: go on to the expired call.
- Expired call (E) returns:
  - sentinel or malformed: indeterminate, 3 calls.
  - `True`: undefended, 3 calls.
  - `False`: defended, 3 calls.

First-match check for the cases that could overlap. "Accepts everything" matches C=True, then N=True, so undefended after 2 calls (AC3, which asserts 2). "Refuses everything" matches C=False first, so indeterminate (AC4), not defended. "Refuses no-owner, accepts expired" matches C=True, N=False, E=True, so undefended after 3 calls. A malformed answer at N is checked before the `True`/`False` reading, so it cannot be mistaken for a refusal that would let the probe go on to a defended result. All outcomes match the spec.

## Step 2: spec sections vs AC table

| Spec path | AC |
|---|---|
| control raise / timeout | AC5 raising `control`, hung `control` (1 call) |
| control malformed (9 shapes) | AC5 malformed control |
| control well-formed `False` | AC4 (three tests, incl. refuse-all and extra keys) |
| no-owner `True` (short-circuit) | AC3 (2 calls, rows == [CONTROL, NO_OWNER]) |
| no-owner raise / timeout / malformed | AC5 raising, hung (2 calls), malformed |
| expired `True` | AC3 (3 calls) |
| expired `False` (defended) | AC2 |
| expired raise / timeout / malformed | AC5 raising, hung (3 calls), malformed |
| extraction on the guard worker | AC5 hanging-mapping (control and no-owner) |
| interrupts propagate | AC5 interrupts (control, expired) |
| non-callable | AC5 |
| absent / `None` | AC6 pins |
| independence of operations | AC6 RED half (alone) and pins half (all-but-ledger) |
| off (kill switch) | AC8 pins, AC11 off leg |
| shadow prefix / not counted | AC7, plus exact-string asserts everywhere |
| scoped run | AC9 |
| fresh dict per call, mutating host | AC2 (two tests) |
| clock-free probe | AC12 RED test |
| `adapter=None`, `__all__`, provider scan | AC12 pins |

Every AC has a producing path and every probe branch has an AC. Not tested: "attribute read raises" (shared g1 code, unchanged; see E1) and the expired-call hanging mapping (see E3). Neither blocks. No mismatch.

## Step 3: RED adequacy

**Count.** AC1 1, AC2 7 (1 + 1 + 4 params + 1), AC3 4, AC4 3, AC5 42 (27 malformed + 3 raising + 3 hung + 1 non-callable + 1 subscription + 1 api-token + 2 hanging mapping + 4 interrupts), AC6 1, AC7 1, AC9 1, AC11 2, AC12 1. Total 63, which matches. Pins: AC6 4 (2 + 1 + 1), AC8 7 (1 + 5 + 1), AC10 2, CLI usage 1, AC12 3. Total 17, which matches.

**Fails at assert time, not collect time.** `conformance.*` imports are inside `_h()` / `_oracle()` and the stub methods, so collection succeeds. Today ADV-6 gives `not_executed`, so every `== {"ADV-6": "shadow:..."}` assert fails; `test_ac2_each_call_gets_a_fresh_dict` fails on `len(a.objs) == 3`; the interrupt cases fail with "DID NOT RAISE"; the two CLI cases fail on the JSON assert (the off leg fails first on the `on` assert, which is fine); AC12 fails on `"ADV-6" in h._ADAPTER_OPS`. I walked all 63 and found none that passes today.

**No stub-passability.** The stubs are the adapter, which is the input of the unit under test. `harness` is imported and run for real. The stubs subclass a local `_Base`, nothing from the harness.

**Passable by a correct GREEN (point 1).** I walked every assert against a literal reading of the spec plus the existing `_guarded_value_call(fn, arg, timeout_s, extract)`:
- Row snapshots are taken by the stub before the host acts, so `rows == TABLE` holds even when the host clears its row (AC2).
- `_per_row(**answers)` keyword names match the function's parameters; `_kind` reads only the keys `issue` and `kill_by`, which the spec fixes.
- `bare-true`, `bare-string`, `list` are non-Mappings, `{}` and `{"verdict": True}` lack the key, `None` / `1` / `"yes"` are not exactly `bool`. All are malformed under the spec rule.
- The hung cases: the worker sleeps 5 s on a daemon thread; the guard joins for 0.2 s; elapsed stays well under 2.5 s as long as the probe stops at the first sentinel (the spec says it does).
- The hanging-mapping cases need `_accepted_of` to run inside the worker (spec says so). Any extraction style (`get`, `[]`, `in`) blocks there and is cut by the join; on the main thread it would take 2 s against a 1.5 s bound.
- AC7: `_LEVEL_OF` puts ADV-6 in BD-L2, so a non-`defended` ADV-6 holds the level at BD-L1, and `validate_attestation` reports a `level_claimed` complaint. A correct GREEN passes.
- AC11: the CLI flags used (`--adapter`, `--level-claimed`, `--engine-version`, `--host-identity`, `--timeout-s`, `--adapter-ops`) all exist in `_main`; with BD-L1 claimed and ADV-1/ADV-2 defended, exit 0 holds.
- AC12 probe source: `inspect.getsource` works for a module-level function and for a closure. GREEN just must keep the four forbidden words out of the probe's source including its docstring.

No unreachable assert found.

**Wrong-GREEN kill table (point 2).**

| Wrong GREEN | Killed by |
|---|---|
| Accept-all (always `defended`, or engine `_adv_6` reused) | AC1 (`shadow:undefended` expected), AC3, AC4 |
| Refuse-all host judged defended | AC4 (3 tests: `calls == [METHOD]`, indeterminate) |
| Control skipped | AC4 (`rows == [CONTROL]` could not hold), AC2 (`rows == TABLE`, 3 calls) |
| Wrong `as_of` (any other value) | AC2 `as_ofs == [AS_OF]*3`, and the judging stub itself flips: an `as_of` after 2099-01-01 refuses the control, one before 2020 accepts the expired row |
| Wrong row content / key names / row order | AC2 and AC3 `rows ==` list comparisons |
| Same row object reused or mutated | AC2 fresh-dict test (3 distinct ids, held alive by `a.objs`), AC2 clearing host (the second call would see `{}`) |
| Extraction off the guard worker | AC5 hanging mapping (control and no-owner), elapsed < 1.5 s |
| Truthiness / `isinstance(v, int)` / `"yes"` accepted | AC5 `{"accepted": 1}`, `"yes"`, `None` on all three calls |
| `bool` check via `isinstance(v, bool)` versus `type(v) is bool` | Equivalent: `bool` cannot be subclassed, so no test can tell them apart and none is needed. A weaker check (int, truthy) is killed above |
| No short-circuit when no-owner is accepted | AC3 (`calls == [METHOD]*2`, `rows == [CONTROL, NO_OWNER]`) |
| Malformed no-owner / expired answer read as a refusal | AC5 malformed no-owner and expired (a "False" reading would end `defended`) |
| Timeout or raise read as `undefended` or as a refusal | AC5 raising and hung |
| Swallowing KeyboardInterrupt / SystemExit (`except BaseException`) | AC5 interrupts (control and expired position) |
| Non-daemon or joined worker | AC5 hung (`elapsed < 2.5`, `_non_daemon_threads() == []`) |
| Proceeding after a control timeout | AC5 hung `control` (`len(calls) == 1`; the hung stub's call count would reach 2) |
| `shadow:` prefix dropped, or the outcome counted | exact-string asserts, AC7 |
| Calling `freeze`/`evaluate` for ADV-6 | AC9 |
| Probe reads a clock | AC12 RED test |
| Dispatch ignoring `adapter_ops="off"` | AC11 off leg, AC8 pin |

**Pins: vacuity.** They pass today because ADV-6 is not yet an adapter operation, and they stay true after GREEN. After GREEN they shield the `off`, absent and `None` branches, the operation independence and the pinned surface. AC10 is the weakest: ADV-6 is not in the compared name set, so it only shows the new row does not change ADV-1..5. That is its stated role. The 17 count matches and every pin is reachable by a correct GREEN (I walked the stub MROs: `Conforming`, `OnlyLedger`, `AllButLedger`, `DigestWith*`, `AcceptAllWith*`).

**Point 3, wall clock.** No test reads `datetime`, `time.time` or `date.today`. All time-dependent stubs compare the ISO strings they are handed. The only timers are two `time.monotonic()` pairs bounding elapsed time. See Finding 1 for the wording. The CLI tests do not pass `--timestamp`, so the harness stamps the attestation with the wall clock in the child process; no assert looks at it. Advisory only.

**Point 4, spec-internal consistency.** Method name, row keys, dates, `as_of` and call counts agree throughout (see Step 1).

## Step 4: reachability (§1y)

| AC | Point (prod) | Host (enclosing function) | Test path |
|---|---|---|---|
| AC1-AC5, AC9 | new `_probe_ledger_rows` and `_accepted_of`, via `_ADAPTER_OPS["ADV-6"]` | `_optional_op` (l.481), called from `run_adversaries` l.554-557 | `h.run_adversaries(only=("ADV-6",), adapter=stub)` |
| AC5 timeout / raise / extraction | `_guarded_value_call` worker and join (l.379-407) | the probe | same, `timeout_s` 0.2 / 0.5 / 5.0 |
| AC6 | `_optional_op` l.490-491 | `run_adversaries` | pins, RED alone-test |
| AC7 | `_level_achieved` l.581 | `build_attestation` / `validate_attestation` | direct calls on a real run's output |
| AC8 | `_optional_op` l.483 | `run_adversaries` | pins `adapter_ops="off"`, CLI off leg |
| AC10 | unchanged ADV-1..5 branches | `run_adversaries` | pins, two adapter families |
| AC11 | `_main` l.658-723, then `run_adversaries` | CLI `__main__` | subprocess `python -m bytedigger_engine.conformance.harness` |
| AC12 | module source, `__all__`, no-adapter branch l.561-570, `_ADAPTER_OPS` | module | pins and the RED probe-source test |

Every side-effect AC reaches the code through a real entry point. The only private-name access is `_ADAPTER_OPS` in the AC12 clock test, which the spec itself names.

Existing-behaviour contradiction check: `test_bd195_harness_adapter_seam.py` `_NON_ORACLE` expects ADV-6 `not_executed` only for adapters that lack the method; that stays true. The bd197 and bd267 files assert ADV-1..5 only, plus the no-adapter map. I found nothing that pins `_ADAPTER_OPS` keys.

## Adversarial edges (not covered by the spec's AC table)

- **E1, attribute read raises.** `admit_ledger_row` as a property that raises gives `shadow:indeterminate` through shared g1 code. Unpinned for this row; low risk, code unchanged.
- **E2, off before non-callable.** `adapter_ops="off"` with a non-callable attribute should give `not_executed`. The shared order guarantees it; not pinned for this row.
- **E3, hostile mapping on the expired call.** The hanging-mapping test covers control and no-owner only. Same code path (one guard per call), so low risk.
- **E4, worst-case time.** Each of the three calls gets its own `timeout_s`, so a host that hangs on all three would cost up to 3x only if each hang were followed by another call; as designed the probe stops at the first sentinel, so the worst case is one `timeout_s` plus the earlier fast calls. A GREEN that ignored the sentinel and kept going would cost up to 3x. Covered indirectly by the hung-call-count asserts.
- **E5, whitespace-only owner.** The probe sends `""` only. A host that accepts `"   "` is not caught. Out of scope by the spec ("empty means nobody owns the row"); noted so nobody assumes it is measured.
- **E6, a host that returns the same mapping object each time and mutates it later.** Extraction runs inside the worker immediately, so there is no late-read hazard. Not pinned; no concern.
- **E7, a host that compares the dates as `date` objects.** It would raise on a `str` argument and land in `indeterminate`. That is the correct degrade and is covered by the generic raising tests.

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

1. **MINOR: "reads no clock" wording.** The spec (l.52, l.86) and the RED docstring say no test and no harness code reads a clock. The RED uses `time.monotonic()` for elapsed bounds in two tests, and the CLI entry reads `datetime.now` for the default timestamp. Nothing reads the wall clock or date in a way that affects an assert. Reword to "no test reads the wall clock or the date; the probe reads no clock", or have the CLI tests pass `--timestamp`. Not blocking.
2. **MINOR: docstring amendment untested.** The spec requires amending the module docstring rules 2 and 3 and adding the ADV-6 row, but no assert pins it. GREEN should do it anyway (the bd#267 Finding 2 left rule 3 stale). Keep provider words (`subscription`, `claude`, `openai`, `anthropic`, `api-token`, `api_key`) out of the new text, as the pinned provider scan lowercases the whole source.
3. **MINOR (advisory): AC9 "other operations zero times".** The AC9 stub has no other operations, so that half of the spec sentence is not checked by the test. It is covered by construction (`only=("ADV-6",)` routes to one op).

No MAJOR findings.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd269-adapter-suppression-ledger-op.md sha256:5e54ed1d4f0dd4443139c28f152d88eb165a55f992407bbd9af01cdc05f3686e
red: engine_py/tests/test_bd269_adapter_suppression_ledger_op.py sha256:43bc3efc9e0abe09c6ae5e75d429597146de2961a4d8fa1615d03fd6550a038d
-->
