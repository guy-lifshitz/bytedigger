# bd#273 gate r1: spec + RED audit (ADV-8 attribution adapter operation)

- Spec: `docs/decisions/2026-10-11-bd273-adapter-attribution-op.md`
- RED: `engine_py/tests/test_bd273_adapter_attribution_op.py` (90 fail today)
- Pins: `engine_py/tests/test_bd273_adapter_attribution_op_pins.py` (18 pass today)
- Code under change: `engine_py/bytedigger_engine/conformance/harness.py` (`_guarded_value_call` l.400, `_ADAPTER_OPS` l.620, `_optional_op` l.633, `run_adversaries` else-branch l.711, `_LEVEL_OF` l.151); reused: `attest.hash_text`.
- Preflight facts: all steps ok, siblings green (taken from the facts file; nothing was re-run, this audit is read-only).

## Step 1: spec-internal consistency (literal-token drift)

These tokens match across the spec, the RED constants, the pins stubs and both CLI factory sources: `attribute_block`, `"source_id"`, `"digest"`, `shadow:`, `adapter_ops` / `--adapter-ops`, `_ADAPTER_OPS`, `_optional_op`, `_guarded_value_call`, `_attribution_of`, `_probe_attribution`, `attest.hash_text`, the `sha256:` prefix. The RED reads only `_ADAPTER_OPS["ADV-8"]` (the one private name the spec fixes); the extractor and probe names are not asserted, which is correct.

Call counts agree between spec and tests: AC2 (3, also under `only`), AC4 (2 for equal or empty digests; 3 for the proceed cases), AC5 (k calls for a failure on call k, for raise, hang and every malformed shape). Line references in the spec (`harness.py` l.711; bd271 test l.660, pins l.173/l.179; bd195 l.214, l.343, l.354) were checked and are correct.

Drift found (advisory only):
- The spec (l.93) says no test reads the wall clock or the date, which is true. The RED docstring says "No test here reads the clock" while three tests use `time.monotonic()` to bound elapsed time. Same wording point as bd#269 and bd#271.
- The spec's module-docstring amendment (ADV-8 row, rules 2 and 3) has no test. Same as g2 to g4.

## Step 1.5: rule-overlap simulation

**Dispatcher.** Adapter branch order: `_ADAPTER_ADVERSARIES` (ADV-1, ADV-2), then `_ADAPTER_OPS`, then `not_executed` (l.711). Today ADV-8 falls to l.711; after GREEN adds the row it matches `_ADAPTER_OPS` first. ADV-3 to ADV-7 and ADV-10 keep their rows; ADV-9 and unknown names still fall through. No overlap.

**`_optional_op`, in order.** off gives `not_executed`; a getattr that raises gives `shadow:indeterminate`; `None` gives `not_executed`; non-callable gives `shadow:indeterminate`; otherwise the probe, any Exception gives `shadow:indeterminate`, result prefixed `shadow:`. AC6 (absent, `None`), AC8 (off), AC5 (non-callable, interrupts) land on the branch the tests expect.

**ADV-8 probe, all branches.**
- Control 1: sentinel (raise or timeout) -> indeterminate, 1 call. Malformed (not a `Mapping`, `"digest"` missing, not exactly `str`) -> indeterminate, 1 call. Otherwise go on.
- Control 2: same sentinel and malformed rules -> indeterminate, 2 calls. Equal to control-1 digest -> indeterminate, 2 calls (an empty digest on both is the equal case). Different -> go on.
- Probe: sentinel or malformed -> indeterminate, 3 calls. Source id exact `str`, non-empty after `strip()`, and digest equal to `hash_text(probe)` -> defended; anything else well formed -> undefended.
- First-match checks: "constant digest" ends at control 2 (indeterminate, never defended or undefended by luck, AC4); "missing digest and missing source id" is malformed before the source-id judgement, so indeterminate and not undefended (AC5 row `digest-missing-and-no-source-id`); a control without a source id does not stop the run and the probe decides (AC4); wrong-but-different control digests proceed and the probe decides in both directions (AC4, two tests). All match the tests.

## Step 2: spec sections vs AC table

| Spec path | AC |
|---|---|
| Operation called, not the engine probe | AC1 |
| Control 1 / control 2 raise, hang, malformed | AC5 (12 shapes x 3 calls, raise x 3, hang x 3) |
| Equal / empty digests on controls | AC4 (two tests) |
| Control judges digests only | AC4 (three tests) |
| Probe defended / source id rules | AC2, AC3 (8 source variants) |
| Probe undefended digest rules | AC3 (10 digest variants incl. lossy ascii) |
| Extraction on the guard worker | AC5 hanging mapping x 3 |
| Interrupts propagate | AC5 (6) |
| Non-callable, session down, missing credential | AC5 |
| Absent / `None`, independence | AC6 pins and RED alone-test |
| Off | AC8 pins, AC11 off leg |
| Shadow prefix, not counted | AC7 and exact strings |
| Digest form | AC9 pins |
| Probe clock/file/provider-free | AC12 RED test |
| `adapter=None`, `__all__` | AC12 pins |

Every probe branch has an AC and every AC has a producing path. Not tested: the docstring amendment (Finding 2). No mismatch.

## Step 3: RED adequacy

**Count.** AC1 1, AC2 7, AC3 18, AC4 5, AC5 54, AC6 1, AC7 1, AC11 2, AC12 1. These sum to 90 failing, matching the preflight total. The pins file holds 18 passing tests, also matching the preflight.

**Fails at assert time, not collect time.** `conformance.*` imports are inside `_h()` / `_oracle()` and stub bodies; stubs subclass a local `_Base`. Today ADV-8 gives `not_executed` under an adapter, so every `== {"ADV-8": "shadow:..."}` fails, the call-count asserts fail on empty logs, the interrupt cases fail with "DID NOT RAISE", `_ADAPTER_OPS["ADV-8"]` fails with a KeyError inside the test body, the CLI case fails on the JSON assert. AC1 starts with an assert that passes today (`only=("ADV-8",)` with no adapter is `defended`) but a failing assert follows in the same test. Preflight confirms 0 pass, 90 fail.

**No stub-passability.** Stubs are the adapter, the input of the unit under test; the harness runs for real. Stubs report facts and judge nothing, and compute digests with `hashlib`, never with the harness. Nothing mocks `harness`.

**Asserts a correct GREEN cannot satisfy: none found.** Walked against the real guard (`_guarded_value_call(fn, arg, timeout_s, extract)`, extract on the worker, BaseException re-raised on the caller, sentinel on timeout or Exception):
- `_at(k, bad)` keys on `len(seen) == k-1`, where `seen` is the list of contents handed before the call; it is correct for k = 1, 2, 3. `seen[0]` / `seen[1]` are available on call 3.
- Call counts: a failure at call k stops the probe at k (controls stop the run; the probe is last). Equal-digest hosts stop at 2. All follow from the spec.
- `_check_args`: three `str` contents, non-empty, pairwise distinct, third non-ASCII and holding a line break. Satisfied by the spec's content rules; no constraint on the controls beyond distinctness, which matches the spec.
- `lossy-ascii` variant: removing the non-ASCII character changes the hash, so it is undefended. `stale-control` uses `seen[1]`, distinct from the probe digest.
- Hung: the stub sleeps 5 s on a daemon worker, join 0.2 s, so `elapsed < 2.5` holds because the probe stops at the first sentinel (k=3 first runs two instant controls). Hanging mapping: every read blocks 2 s; `isinstance(..., Mapping)` is an ABC check that does not call the blocking methods, and any read happens on the worker, so the run ends within 0.2 s per call; a GREEN that reads on the caller thread takes 2 s and fails `< 1.5`, as intended.
- `types.MappingProxyType` is a registered `Mapping`; `_OpaqueMapping` gets `.get` from the mixin. Both are accepted by the spec rule (`Mapping`, not `dict`).
- Interrupts: the same exception instance is re-raised across parameters; harmless.
- AC7: `_LEVEL_OF["ADV-8"] == "BD-L3"`; with ADV-8 `shadow:defended` and the others `defended`, the level loop stops at BD-L2 and `validate_attestation` names `level_claimed` (same construction as the bd271 RED, same `build_attestation` keywords).
- AC11: the CLI flags are the same set the bd267/bd269/bd271 REDs use; BD-L1 claimed, ADV-1/ADV-2 defended, exit 0. The factory file is written into a fresh temp dir with a name distinct from the pins module.
- AC12: `inspect.getsource(probe)` works on a module-level function or on a closure's inner function (as `_step_probe` would produce). The six forbidden words must stay out of the probe function only.

**Wrong-GREEN kill table.**

| Wrong GREEN | Killed by |
|---|---|
| Reuses engine `_adv_8`, ignores the answer | AC1, AC2, AC3 |
| Control skipped, or one control only | AC4 call counts, AC2 three-call log |
| Equal or empty control digests read as defended or undefended | AC4 (two tests) |
| Control without source id stops the run | AC4 |
| Control digest correctness enforced | AC4 (two proceed tests) |
| Source id not checked / truthiness / `strip` skipped / non-`str` or subclass accepted | AC3 source rows (8) |
| Digest compared case-insensitively, without prefix, or prefix-agnostic | AC3 `upper-hex`, `bare-hex`, `bare-upper-hex`, `other-prefix` |
| Digest of the content plus newline, of other text, or of a control accepted | AC1, AC3 |
| Lossy or non-UTF-8 hashing of the content | AC3 `lossy-ascii` |
| Digest of probe compared with control digests instead of the content | AC3 `stale-*` |
| `dict` required, extra keys rejected, inner spaces rejected, padded id rejected | AC2 five wrap rows |
| Truthiness / lenient reading of malformed answers | AC5 (36 rows) |
| Raise or timeout read as undefended or defended | AC5 raising, hung |
| Extraction off the worker | AC5 hanging mapping |
| `except BaseException` swallowing interrupts | AC5 interrupts (6) |
| Non-daemon worker, proceeding after a hang | AC5 hung (`_non_daemon_threads`, call count) |
| `shadow:` dropped or outcome counted | exact strings, AC7 |
| Probe called outside `only` scope | AC2 |
| Dispatch ignores `adapter_ops="off"` | AC11 off leg, AC8 pin |
| Probe reads a clock / file / provider word | AC12 RED test |

**Pins: vacuity.** They pass today because the operation does not exist and stay true after GREEN: omitted or `None` method stays `not_executed` with zero calls and nine keys; off and the unknown-value `ValueError`; `adapter=None` ignores `adapter_ops`; hash form; provider scan of the whole harness source; no interference with ADV-1 to ADV-7 and ADV-10 for a digest and an accept-all adapter (a real check this time, since ADV-7 and ADV-10 are included); pinned surface and no-adapter map. `test_ac6_an_adapter_with_the_other_operations_but_not_this_one` uses the same stubs as the bd267/bd269/bd271 pins; the sibling run is green.

**Sibling interactions.** No sibling stub defines `attribute_block`, and none uses `__getattr__`, `MagicMock` or `SimpleNamespace` adapters, so no sibling adapter can start receiving ADV-8 calls. The places that assert ADV-8 `not_executed` (bd271 test l.660, pins l.173/l.179; bd195 `_NON_ORACLE` l.214 and the no-adapter JSON l.343/l.354) all use adapters without the method and stay true. bd267 and bd269 assert only their own adversaries and ADV-3 to ADV-5. bd58 and bd63 touch the no-adapter path; bd10 tests `attest`, unchanged; bd94 does not see the probe (no file, no listing).

## Step 4: reachability (§1y)

| AC | Point (prod) | Host (enclosing function) | Test path |
|---|---|---|---|
| AC1-AC5 | new `_attribution_of`, `_probe_attribution`, via `_ADAPTER_OPS["ADV-8"]` | `_optional_op` (l.633), called from `run_adversaries` adapter branch (l.706) | `h.run_adversaries(only=("ADV-8",), adapter=stub)` and the full run |
| AC5 timeout / raise / extraction | `_guarded_value_call` worker and join (l.400) | the probe | same, `timeout_s` 0.2 / 0.5 / 5.0 |
| AC6 | `_optional_op` getattr / `None` branch | `run_adversaries` | pins, RED alone-test |
| AC7 | `_level_achieved` (l.726) | `build_attestation` / `validate_attestation` | direct calls on a real run's output |
| AC8 | `_optional_op` off branch | `run_adversaries` | pins, CLI off leg |
| AC9 | `attest.hash_text` | called by the probe | pins (direct), AC1 to AC3 indirectly |
| AC10 | unchanged ADV-1 to ADV-7, ADV-10 branches | `run_adversaries` | pins |
| AC11 | `_main` then `run_adversaries` | CLI `__main__` | subprocess `python -m bytedigger_engine.conformance.harness` |
| AC12 | module source, `__all__`, `_ADAPTER_OPS` | module | pins and the RED probe-source test |

Every side-effect AC reaches the code through a real entry point. The one private name the tests touch is `_ADAPTER_OPS`, fixed by the spec.

## Adversarial edges (not covered by the spec's AC table)

- **E1, whitespace flavours in the source id.** The spec says "non-empty after `str.strip()`". The RED tests only `""` and `"   "`. A GREEN using `strip(" ")` would pass although a tab, newline or no-break space id must be undefended. Not pinned.
- **E2, whitespace around the digest.** "No stripping" is in the spec, but no row sends `digest + " "` or `digest + "\n"` (note the AC3 row `plus-newline` hashes content-plus-newline, not a padded correct digest), nor an upper-case prefix `SHA256:`. A GREEN that strips or case-folds the prefix passes RED.
- **E3, control digests: one empty, one valid.** Different strings, so the run proceeds; the spec is silent on whether an empty control digest should stop it. Only the both-empty case (equal) is tested.
- **E4, fixed contents across runs.** The spec says the three contents are fixed strings. The RED asserts distinctness within a run only; a GREEN that generates random contents per run passes. Low risk, but a host cannot be given a stable fixture.
- **E5, probe-source scan scope.** AC12 scans the probe function only. The extractor `_attribution_of` and the content constants are outside the clock/file scan; the provider scan of the pins covers the whole file.
- **E6, same mapping object reused and mutated after return.** Extraction on the worker copies immutable `str` values at once, so no late-read hazard; not pinned.
- **E7, leaked sleeping workers.** Each hung call leaves a daemon sleeping for up to 5 s; the stub appends to its call log before sleeping, so counts do not move later. No cross-test effect beyond the daemon check.

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

1. **MINOR: "reads no clock" wording.** The RED docstring says "No test here reads the clock" while three tests use `time.monotonic()` (the spec's own sentence says wall clock or date, which holds). Reword the docstring. Not blocking.
2. **MINOR: docstring amendment untested.** The spec requires the module docstring to gain the ADV-8 row and to name this operation's shapes in rules 2 and 3, but no assert pins it (same as g2 to g4). GREEN should do it and keep provider words and any model or vendor name out of the new text, since the pinned scan lowercases the whole source.
3. **MINOR: whitespace edges (E1, E2).** Consider rows for a tab or no-break-space source id and for a correctly hashed digest padded with whitespace or with an upper-case `SHA256:` prefix. GREEN must use plain `str.strip()` on the source id and exact equality on the digest.
4. **MINOR (advisory): non-ASCII literals.** The probe content holds a non-ASCII character; GREEN may write it as an escape sequence to keep the harness source ASCII. Either way the pinned provider scan reads the file as UTF-8 and is unaffected.
5. **MINOR (advisory): vendor-name scan.** The pinned scan covers provider words only; the spec also forbids model and vendor names in the harness source, which is not machine-checked.

No MAJOR findings.

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-11-bd273-adapter-attribution-op.md sha256:c2cd3a43357f7e393d5f74f0a02b9aa78291bdf5d99e834cceacde0a685cefb1
red: engine_py/tests/test_bd273_adapter_attribution_op.py sha256:67f86d2fb4c19bf2a05b395e2e062b0792bb112b45ea2a7a1ec971259fbca996
-->
