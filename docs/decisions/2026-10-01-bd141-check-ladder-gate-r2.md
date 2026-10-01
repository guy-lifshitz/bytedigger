# bd#141 item 3 — check ladder — validation gate r2

**Spec:** `docs/decisions/2026-10-01-bd141-check-ladder.md` (r2)
**RED:** `engine_py/tests/test_bd141_check_ladder.py` (rev2; orchestrator run: 26 failed, 5 passed — the 5 passing are the L16 no-op shields `str/none/list/empty-dict/no-classifier-cmd`; `L16[absent]` fails today on its pairing check)
**r1:** `docs/decisions/2026-10-01-bd141-check-ladder-gate-r1.md` (findings 1–11)
**Context read:** `workflows/phase_5_implement.py` `_invoke_validation_llm` (l. 6604–6668), imports (l. 109–182: `_emit_safe` from `phase_workflows_common`, `invoke_llm_subprocess` module-level, both patchable), `telemetry_ctx.emit_safe` signature.

## Part 1 — r1 findings

| r1 | Resolution in r2 | Status |
|---|---|---|
| 1 MAJOR kwargs invariance | `_assert_same_as_baseline`: same kwarg names, every non-`extra_data` kwarg equal, `extra_data` equal minus `prescreen`; used in L17 (both params), L17b(a)/(b), L18 (both params); L16 asserts `calls[0] == base` (full dict). | resolved. Residual: L16 does not pin `model == _default_validation_model()` / `gate_label == "validation"` in the baseline itself (only matters for a GREEN that changes the no-config path too; siblings `test_llm_subprocess_allowed_tools`, `test_gh705_callsite_stable_prefix` cover part). MINOR 3. |
| 2 MAJOR outcome independence | op4.1 pins `from bytedigger_engine import check_ladder` + attribute call + "Phase 5 ignores the verdict's outcome"; L17b(a) stub returns `reject/script`, asserts the stub was called by attribute with keyword args, one Opus call with baseline kwargs, `result.status == "ok"`. | resolved for the call. Residual: the returned result's data is not pinned (fail-closed direction only). MINOR 2. |
| 3 MAJOR `classifier_cmd` owner | op2 raises `ValueError` on non-list / empty / non-str element; op1 maps the same input to `error`/`rc None`, never raises. L9 (`[]`, `"python"`, `["a", 1]`), L11, L18 `not-a-list`. | resolved. `"python"` as a str would spawn under a naive op1 (rc 1) — the RED's `rc is None` assert catches it. |
| 4a non-dict config | L16 `"x"`, `None`, `[]`, `{}`, `{"timeout_s": 5}`. | resolved |
| 4b op2 ValueError -> rc 2 | L13 `--threshold 1.5`, `--timeout-s 0`, bad-severity findings file. | resolved |
| 4c op4.5 ValueError path | L18 `valid-cmd-timeout-0`, plus "not spawned" side-effect check. | resolved |
| 4d enforce+MAJOR `classifier` value | op2.4 + op1.1 define the `skipped` shape; L7 asserts the full dict. | resolved |
| 4e exact event key set | `_EVENT_KEYS` asserted in L17, L17b(a), L17b(b), L18. | resolved |
| 5 lint `errors` | L19 checks `report["errors"]`. | resolved |
| 6 `HAL_` / vendor | L19. | resolved |
| 7 non-finite | op1.6 `math.isfinite`; L9 `conf_nan`, `cost inf`. | resolved |
| 8 grandchild / latency | §4 declared. | resolved (declared) |
| 9 off-shape + config-error values | L9 off all-`None`; op4.5 values; `_assert_config_error`. | resolved |
| 10 resume replay | §4 declared. | resolved (declared) |
| 11 `timeout_s=True` | op2 "not bool"; L11. | resolved |

All 11 r1 findings resolved or declared.

## Part 2 — full re-audit

### Step 1 — spec-internal consistency (literal tokens)

| Token | Spec | RED | Status |
|---|---|---|---|
| `check_ladder`, `prescreen`, `run_classifier`, `RUNGS`/`OUTCOMES`/`MODES` | §2, L1 | L1–L11 | consistent |
| `DEFAULT_THRESHOLD = 0.9`, `DEFAULT_TIMEOUT_S = 30` | §2, op2 signature, op4.1 | not referenced | consistent; unpinned (MINOR 5) |
| status literals `off/skipped/ok/error/timeout`, `config-error` | op1, op2.4, op4.5 | L7, L9, L10, L17b, L18 | consistent |
| `prescreen_verdict`, `extra_data["prescreen"]` | op4.2/4.3 | L16–L18 | consistent |
| event key set (10 keys) | op4.2 | `_EVENT_KEYS` | identical |
| op4.1 call form `check_ladder.prescreen(findings=[], gate_input=..., classifier_cmd=..., mode="shadow", timeout_s=...)` | op4.1 | L17b(a) stub takes `**kw` only, asserts `findings`, `gate_input`, `classifier_cmd`, `mode` | consistent (keyword form is spec-pinned, so the `**kw` stub is fair) |
| journal hash | op3 "hex of the UTF-8 bytes" (of the decoded text) | L14 "sha256 of the file bytes" | wording drift; identical for the `\n`-only fixture, diverge only on CRLF (adversarial 6, MINOR 6) |
| op2.4 bullet order | the `classifier` bullet sits between branch 2 and the final `else` | — | readability only; simulated below, no semantic conflict |

No name drift.

### Step 1.5 — rule-overlap simulation

op2 validation runs first (before step 1). Outcome dispatcher: B1 `majors` -> reject/script; B2 enforce ∧ ok ∧ reject ∧ conf ≥ thr -> reject/classifier; B3 -> escalate/None. Classifier-field dispatcher: op1 ran -> op1 result; cmd `None` -> `off`; enforce ∧ MAJOR ∧ configured -> `skipped`.

| Case | First match | Expected | OK |
|---|---|---|---|
| shadow, MAJOR, cmd ok-reject | B1; classifier spawned -> op1 result | L3, L7 | yes |
| enforce, MAJOR, cmd configured | B1; `skipped` | L3, L7 | yes |
| enforce, MAJOR, cmd `None` | B1; `off` and `skipped` both read as applicable — op1.1 "decides not to call a **configured** classifier" resolves to `off` | no AC | adversarial 2 |
| enforce, MINOR only, cmd ok-reject 0.9 thr 0.9 | B2 (majors empty, classifier called) | L5 analogue | yes |
| enforce, none, ok-reject 0.89 | B3 | L5 | yes |
| enforce, none, ok-pass 1.0 | B3 — no approve branch exists | L2 | yes |
| enforce, none, `label "approve"` | op1 -> `error` -> B3 | L2/L9 | yes |
| shadow, none, ok-reject 1.0 | B3 | L6 | yes |
| enforce, none, error/timeout | B3 | L10 | yes |
| conf NaN | op1 -> `error` -> B3 | L9 | yes |

op1 dispatcher (malformed -> error/None; None -> off; OSError -> error; timeout; rc≠0; parse) against L9: all cases land as asserted. `ok` asserts `rc == 0`, which op1 does not state (MINOR 4).

op4 dispatcher (not dict / no `classifier_cmd` -> no-op; call; any exception -> `config-error`; else event + key):

| Config | First match | Expected | OK |
|---|---|---|---|
| absent, `"x"`, `None`, `[]`, `{}`, `{"timeout_s": 5}` | no-op | L16 | yes |
| `{"classifier_cmd": "not-a-list"}` | call -> op2 `ValueError` -> config-error | L18 | yes |
| `{"classifier_cmd": [ok], "timeout_s": 0}` | call -> op2 `ValueError` (before spawn) -> config-error | L18 | yes, provided validation precedes the spawn (op2 lists it before "Steps"; adversarial 7) |
| valid, `"mode": "enforce"` | call with `mode="shadow"` -> escalate | L17 | yes |
| stub returns reject | event + key; Opus call unchanged | L17b(a) | yes |
| stub raises | config-error | L17b(b) | yes |
| `{"classifier_cmd": None}` | ambiguous: "no `classifier_cmd`" (no-op) vs a configured key with value `None` (op2 -> `off`, event emitted) | no AC | adversarial 1 |

### Step 2 — §2 vs §3

| §2 branch | AC / RED | Gap |
|---|---|---|
| op1.1–op1.8 incl. malformed cmd | L9, L9_first_line | none (`reasons` list with non-str element -> `[]` untested, MINOR 7) |
| op2 validation | L11 | none |
| op2 call decision, outcome, classifier field | L2–L8, L10 | none |
| op3 rc 0 / one line | L12 | none |
| op3 rc 2 (usage + op2 ValueError) | L13 | none |
| op3 `--log` create/append/redact | L14 | none |
| op3 journal I/O non-fatal | L15 | none |
| op4.1 attribute call, outcome ignored | L17b(a) | none for the call; result passthrough partial (MINOR 2) |
| op4.1 `timeout_s` default `DEFAULT_TIMEOUT_S` | none | MINOR 5 |
| op4.2 event keys | L17, L17b, L18 | values for (a) partial — see ruling |
| op4.3 key without reasons | L17 | none |
| op4.4 kwargs invariance, enforce ignored | L17, L17b, L18 | none |
| op4.5 config-error (ValueError + any exception) | L17b(b), L18 | none |
| op4.6 silent no-op | L16 | none |
| §2 hygiene | L19 | none |

Every terminal/error branch has an AC; every AC post-condition has a producing branch. No MAJOR gap.

### Step 3 — RED adequacy

- Collect-time: top-level imports are stdlib + pytest; UUT, `contracts`, `phase_5_implement` imported lazily. L17b calls `_baseline` (works today) then `_cl()` (fails at call time). Assert-time failure (§1q). OK.
- No UUT mocking: classifiers are real executables with spawn/stdin side-effect files; phase 5 patches only collaborators. L17b substitutes `check_ladder.prescreen`, which is a collaborator of the UUT `_invoke_validation_llm`, not the UUT itself — legitimate (§1l).
- Every AC has at least one assertion that fails today. L16: 5 params pass today by design (regression shields); they redden on an unconditional key, an event on a non-dict / no-cmd config, a kwarg change, or a `KeyError` on missing config. `L16[absent]` fails today. Acceptable.
- Timing: L2 has ≤ 5 × 1 s pre-staged timeouts; L9/L10 one each; well under `--timeout 20000`.
- L12 equality runs only on classifier-off paths (`ms == 0`), so it is deterministic.
- Spec-violating GREENs that still pass: MINOR 2 (result data rewritten after the call), MINOR 3 (a global kwarg change on both paths). Neither is an approve bypass.

### Step 4 — reachability (§1y)

| AC | Point | Host | Test path |
|---|---|---|---|
| L7–L10 | `subprocess.run(cmd, input=..., timeout=...)` | `run_classifier` <- `prescreen` | real fake executables, side-effect files |
| L12–L15 | stdout print / `O_APPEND` write | `check_ladder` `__main__` | `python -m bytedigger_engine.check_ladder` subprocess |
| L16–L18 | `check_ladder.prescreen(...)` attribute call, `_emit_safe("prescreen_verdict", ...)`, `invoke_llm_subprocess(...)` | `_invoke_validation_llm` (l. 6604) | `_drive` patching `p5.invoke_llm_subprocess`, `p5._emit_safe`, and (L17b) `check_ladder.prescreen` |
| L19 | manifest / mypy list / lint | repo files | file read + `core-boundary-lint.py --json` |

All traced. `org_config` is host ctx JSON, so op4 is prod-reachable.

### Safety direction

- **Pre-screen never approves.** `OUTCOMES` has no approve member (L1); the full matrix (L2) lands in `OUTCOMES`; a classifier `label "approve"` is `error` (L9) and falls to B3 escalate. No code path yields an approve. Pinned.
- **Phase-5 gate call unchanged and independent of outcome.** The Opus call runs exactly once with baseline kwargs for: real shadow verdict (L17), enforce in config (L17), stubbed `reject/script` (L17b(a)), stub raising (L17b(b)), bad config (L18). The verdict cannot skip or reshape the call. Residual (MINOR 2): after the call, the returned `StepResult.data` is not pinned; only `status == "ok"` is. Rewriting it on a reject would be fail-closed (stricter), not a bypass.

### Ruling — op4.2 payload values for a stubbed reject verdict (L17b a)

The event payload is a **pure projection of the verdict that `prescreen` returned**, never re-derived and never defaulted from the config:
`outcome = v["outcome"]`, `rung = v["rung"]`, `mode = v["mode"]`, `classifier_status = v["classifier"]["status"]`, `classifier_label = v["classifier"]["label"]`, `classifier_confidence = v["classifier"]["confidence"]`, `classifier_ms = v["classifier"]["ms"]`, `cost_usd = v["classifier"]["cost_usd"]`, `cycle = prev.data.get("cycle", 1)`, `phase = 5`. The literals of op4.5 apply only on the config-error path.
For the L17b(a) stub this gives exactly:
`{"outcome": "reject", "rung": "script", "mode": "shadow", "classifier_status": "off", "classifier_label": None, "classifier_confidence": None, "classifier_ms": 0, "cost_usd": None, "cycle": 1, "phase": 5}`,
and `extra_data["prescreen"]` = the stub verdict with `classifier.reasons` removed (the key is dropped even when its value is `None`).
The current RED (key set + `outcome == "reject"`) is consistent with this ruling; asserting the full dict is advisory (MINOR 1), not blocking.

## Adversarial edges (not covered by any §3 AC)

1. **Present-but-null key.** `org_config["prescreen"] = {"classifier_cmd": None}`: op4.6 "dict without `classifier_cmd`" can be read as key-absent only (then op2 gets `None` -> `off`, one event emitted, key added) or as falsy (silent no-op). Spec should pick one; recommended: treat `None` as "no `classifier_cmd`" (no-op), add to L16.
2. **Off/skipped overlap.** enforce + MAJOR + `classifier_cmd=None`: op1.1's "configured" resolves it to `off`, but no AC pins it.
3. **Post-call override.** A GREEN that, on `outcome == "reject"`, rewrites `result.data["raw_response"]` after the Opus call passes L17b(a) (only `status` is checked). Fail-closed, but contradicts "the gate stays final".
4. **Double event.** An exception raised after the normal `prescreen_verdict` event was emitted (e.g. while building `extra_data["prescreen"]`) would yield a second, `config-error`, event. op4.5 should say "exactly one event per call" — build the payload and key before emitting.
5. **Findings text in the journal.** The journal drops `reasons` but keeps `verdict.findings[*].detail`, which a script rung may fill with quoted spec text. Not reachable from phase 5 (findings `[]`); declare in §4 or redact `detail` like `reasons`.
6. **CRLF gate input.** op3 hashes the UTF-8 encoding of text read in text mode; L14 compares to the raw file bytes. With `\r\n` input these differ (universal-newline translation). Spec should say which (recommend: raw file bytes, read the file once as bytes and decode).
7. **Validate-before-spawn.** `timeout_s=0` with a valid cmd: a GREEN that spawns before validating would make `subprocess.run(timeout=0)` race the child's side-effect write. L18 asserts not spawned; op2 should state "validation runs before any spawn".
8. **`reasons` with non-str elements** (`[1, "a"]`) -> `[]` per op1.6; untested.

## VERDICT

VERDICT: APPROVED

No MAJOR finding. All r1 findings resolved or declared. Advisory findings below may be folded into a spec r2.1 / RED rev3 before GREEN at the orchestrator's discretion; none blocks GREEN. Any RED change must land before GREEN starts (tests are read-only for GREEN, §1s).

### Findings

1. MINOR — L17b(a) event values: assert the full payload dict from the ruling above, and `extra_data["prescreen"] == {**stub, "classifier": {k: v for k, v in stub["classifier"].items() if k != "reasons"}}`. Spec op4.2: add one sentence stating the projection rule.
2. MINOR — result passthrough: in L17b(a) and L17 assert `result.data["raw_response"] == "VERDICT: PASS\n"` (or `result is` the fake's returned object), so the verdict cannot rewrite the gate's answer after the call (adversarial 3).
3. MINOR — baseline anchor: in L16 also assert `base["model"] == p5._default_validation_model()`, `base["gate_label"] == "validation"`, `base["allowed_tools"] == ["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"]`, `base["timeout_sec"] == p5._resolve_validation_timeout_sec({"complexity": "SIMPLE"})`.
4. MINOR — op1.6: state `rc = 0` for exit-0 results (`ok` and parse-`error`); L9 already asserts it for `ok`.
5. MINOR — L1: pin `DEFAULT_THRESHOLD == 0.9`, `DEFAULT_TIMEOUT_S == 30`; L17b(a): assert `called[0]["timeout_s"] == cl.DEFAULT_TIMEOUT_S` when the config has no `timeout_s` (op4.1 default).
6. MINOR — op3 vs L14 hash wording (adversarial 6): align on raw file bytes.
7. MINOR — spec op4.6 `classifier_cmd: None` (adversarial 1), op4.5 one-event guarantee (adversarial 4), op2 validate-before-spawn (adversarial 7), §4 findings-detail in the journal (adversarial 5): one sentence each; optional L16 param `{"classifier_cmd": None}` and L9 case `reasons=[1, "a"]` -> `[]`.
