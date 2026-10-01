# bd#141 item 3 — check ladder — validation gate r1

**Spec:** `docs/decisions/2026-10-01-bd141-check-ladder.md` (r1)
**RED:** `engine_py/tests/test_bd141_check_ladder.py` (orchestrator run: 22 failed, 1 passed — L16)
**Context read:** `workflows/phase_5_implement.py` `_invoke_validation_llm` (l. 6604–6668) and its StepContract (l. 7809, `resume_sentinel=True`), `llm_subprocess.py` extra_data merge (l. 982, 2409), `core-boundary-lint.py` JSON shape, `core_manifest.json` / `mypy-strict-modules.txt` entry format (`close_gate.py` precedent), `run.py` org_config source (host ctx JSON, so op4 is reachable in prod).

## Step 1 — spec-internal consistency (literal tokens)

| Token | Spec | RED | Status |
|---|---|---|---|
| module `check_ladder.py`, `prescreen`, `run_classifier` | §2, op1, op2 | `_cl()`, `MODULE_FILE` | consistent |
| `RUNGS` / `OUTCOMES` / `MODES` values | §2 constants, L1 | L1 | consistent |
| event `prescreen_verdict` | op4.2, L16–L18 | L16–L18 | consistent |
| `classifier_status "config-error"` | op4.5, L18 | L18 | consistent |
| payload keys (`outcome, rung, mode, classifier_status, classifier_label, classifier_confidence, classifier_ms, cost_usd, cycle, phase`) | op4.2 | L17 checks a subset only | consistent literals, weak assertion (finding 4e) |
| CLI flags `--gate-input --findings --classifier-cmd --mode --threshold --timeout-s --log` | op3 | L12–L15 | consistent |
| journal keys `ts`, `verdict`, `gate_input_sha256`; `reasons` -> int | op3 | L14 | consistent |
| `extra_data["prescreen"]` = verdict without `classifier.reasons` | op4.3 | L17 (`"reasons" not in pre["classifier"]`) | consistent |
| `classifier_cmd` input domain | op1 "argv list non-empty or None"; op2 validation list does **not** include it; op4.5 says "bad config shape" raises | L18 expects `config-error` for `"not-a-list"` | **inconsistent** (finding 3) |

No name drift. One semantic gap: who rejects a malformed `classifier_cmd` is not stated, and the natural reading of op1+op2 does not produce L18's outcome.

## Step 1.5 — rule-overlap simulation (op2 outcome dispatcher)

Branches in order: B1 `majors` non-empty -> reject/script; B2 enforce and ok/reject/conf>=thr -> reject/classifier; B3 -> escalate/None.

| Case | First match | Expected (AC) | OK |
|---|---|---|---|
| MAJOR, shadow, classifier ok-reject 1.0 | B1 | reject/script, classifier spawned (L3, L7) | yes |
| MAJOR, enforce, classifier configured | B1 | reject/script, not spawned (L3, L7) | yes; but `verdict["classifier"]` value undefined (finding 4d) |
| MINOR only, off | B3 | escalate/None (L4) | yes |
| none, enforce, ok-reject 0.9 thr 0.9 | B2 | reject/classifier (L5) | yes |
| none, enforce, ok-reject 0.89 | B3 | escalate (L5) | yes |
| none, enforce, ok-pass 1.0 | B3 | escalate (L2) | yes — no approve branch exists |
| none, shadow, ok-reject 1.0 | B3 | escalate (L6) | yes |
| none, enforce, error/timeout | B3 | escalate (L10) | yes |
| none, enforce, ok-reject conf NaN | B3 if `>=` used | not specified | edge, see Adversarial |

op1 status dispatcher (None -> off; OSError -> error; timeout -> timeout; rc!=0 -> error; parse) simulated against L9: every case lands on the expected status, except a non-list `cmd` (str) which lands on OSError -> `error` (or, for a str naming a real binary, a real spawn) and `[]` which raises `IndexError` from `Popen` — op1.8 "never raises" is violated by an obvious implementation (finding 3).

op4 dispatcher (absent / not dict / no classifier_cmd -> no-op; else call; exception -> config-error): with `classifier_cmd="not-a-list"` and no extra validation, `prescreen` does **not** raise (op2 does not validate it), `run_classifier` returns `error`, and the event carries `classifier_status "error"`, not `"config-error"`. L18 is only passable if GREEN invents a validator the spec does not name (finding 3).

## Step 2 — §2 vs §3 cross-check

| §2 branch | AC | RED | Gap |
|---|---|---|---|
| op1.1–op1.7 | L9 | L9, L9_first_line | covered |
| op1.8 never raises (malformed cmd) | none | none | **gap** (3) |
| op2 validation | L11 | L11 | covered; `classifier_cmd` absent from list (3) |
| op2 B1–B3, shadow/enforce call decision | L3–L8, L10 | yes | covered; enforce+MAJOR `classifier` value undefined (4d) |
| op3 rc 0 / one line | L12 | yes | covered |
| op3 rc 2 usage errors | L13 | yes | covered |
| op3 rc 2 for "any op2 ValueError" | none | none — `--mode block` is caught by argparse `choices` before op2 | **gap** (4b) |
| op3 `--log` create/append/redact | L14 | yes | covered |
| op3 journal I/O error non-fatal | L15 | yes | covered |
| op4 absent / no classifier_cmd -> no-op | L16 | yes | covered |
| op4 "not a dict" -> no-op | none | none | **gap** (4a) |
| op4.2 event payload (exact keys) | L17 | subset only | **gap** (4e) |
| op4.4 "same prompt, same model, hard_gate=True" | L16/L17 | prompt + hard_gate only | **gap** (1) |
| op4.4 outcome must not affect the gate call | implied | unreachable via config (findings=[], mode forced shadow) | **gap** (2) |
| op4.5 exception swallowed -> config-error | L18 | only `"not-a-list"` | partial (3, 4c) |
| §2 "no `HAL_*` env, no vendor name" | none | none | gap, MINOR (m2) |

## Step 3 — RED adequacy

- Collect-time: all top-level imports are stdlib + pytest; the UUT and `bytedigger_engine.contracts` are imported lazily inside tests. Failures are assert/import-at-call time. OK (§1q).
- No UUT mocking: classifiers are real executables with side-effect files (L7, L8, L17); phase-5 tests patch only `invoke_llm_subprocess` and `_emit_safe` (collaborators). OK (§1l).
- Timeouts pre-staged (fake sleeps 10 s, `timeout_s=1`). OK (§1i).
- L12 equality compares CLI output to `prescreen` only on classifier-off paths (`ms=0`), so it is deterministic. OK.
- **L16 can redden** — it is not decoration. It turns red if GREEN: (i) puts `"prescreen"` into `extra_data` unconditionally; (ii) emits `prescreen_verdict` with no config; (iii) treats `{"timeout_s": 5}` (no `classifier_cmd`) as configured; (iv) changes `prompt` or `hard_gate`; (v) adds/removes any `extra_data` key; (vi) raises on missing config (e.g. `ctx.org_config["prescreen"]` KeyError). It does **not** redden on a change to `model`, `timeout_sec`, `gate_label`, `allowed_tools`, `stable_prefix`, or to any `extra_data` value — see finding 1.
- GREENs that pass every RED while violating the spec: findings 1, 2, 4a, 4b, 4e below.

## Step 4 — reachability (§1y)

| AC | Point | Host | Test path |
|---|---|---|---|
| L7/L8/L9 | `subprocess.run(cmd, input=..., timeout=...)` in `check_ladder` | `run_classifier` <- `prescreen` | real fake executable writes spawn/stdin files |
| L12–L15 | stdout print / `O_APPEND` write | `check_ladder` `__main__` CLI | `python -m bytedigger_engine.check_ladder` subprocess |
| L16–L18 | `_emit_safe("prescreen_verdict", ...)` and `invoke_llm_subprocess(...)` kwargs | `_invoke_validation_llm` (l. 6604) | `_drive` with patched module attrs `p5.invoke_llm_subprocess`, `p5._emit_safe` (both module-level names in phase_5, patchable) |
| L19 | manifest / mypy list / lint | repo files | file read + `core-boundary-lint.py --json` at repo root |

All traced. Prod reachability of op4: `org_config` comes from the host ctx JSON (`run.py` l. 94), so a host can set `prescreen`.

## Adversarial edges (not covered by any §3 AC)

1. **Latent gate bypass (regression shield).** A future script-rung reject (the §4 follow-up) reaching phase 5 — nothing pins that phase 5 ignores `outcome`. (finding 2)
2. **Decoy-fence on the Opus call.** Configured pre-screen + swapped `model` / `allowed_tools` / `timeout_sec` / `gate_label`. (finding 1)
3. **Malformed argv.** `classifier_cmd = []`, `"python"` (a str naming a real binary — spawns it with `shell=False`), `["a", 1]`. (finding 3)
4. **Non-dict config.** `org_config["prescreen"] = "x" | None | []` must be a silent no-op, not a config-error event. (finding 4a)
5. **Event-log leak.** Event payload carrying `gate_input` or `reasons` (spec text) into the event log. (finding 4e)
6. **Non-finite numbers.** `json.loads` accepts `NaN` / `Infinity`: `confidence: NaN` passes a `not (c < 0 or c > 1)` check; `cost_usd: Infinity` passes "non-negative real" and is re-serialized as non-standard JSON into events/journal. (m3)
7. **Grandchild orphan on timeout.** `subprocess.run` kills only the direct child; a wrapper classifier (shell script spawning a model client) leaves the grandchild running after `timeout`. (m4)
8. **Resume replay.** `invoke_validation_llm` has `resume_sentinel=True`; on a sentinel skip no `prescreen_verdict` is emitted, and on a retry cycle a second one is — the recall measurement must key by run+cycle. (m7)
9. **`timeout_s=True`.** bool is a "positive real" under a naive check -> 1 s timeout. (m8)

## VERDICT

VERDICT: REJECTED

### Findings

1. **MAJOR — the Opus-call invariance is not pinned (safety direction).** op4.4 says "same prompt, same model, `hard_gate=True`" and L16 says "same kwargs as before", but L16/L17/L18 assert only `prompt`, `hard_gate` and the `extra_data` key set. A GREEN that switches `model`, `timeout_sec`, `allowed_tools`, `gate_label` or `stable_prefix` whenever `prescreen` is configured passes every RED. **Fix:** in L17 (both params) and L18, first run `_drive` with no config to capture `base = calls[0]`; then assert `{k: v for k, v in kw.items() if k != "extra_data"} == {k: v for k, v in base.items() if k != "extra_data"}` and `{k: v for k, v in kw["extra_data"].items() if k != "prescreen"} == base["extra_data"]`. In L16 assert the full `extra_data` dict values (from `prev.data`) and `model == p5._default_validation_model()`, `gate_label == "validation"`.

2. **MAJOR — phase 5's independence from the pre-screen outcome is untested (latent gate bypass).** With `findings=[]` and mode forced to shadow, `escalate` is the only reachable outcome, so a GREEN with `if verdict["outcome"] == "reject": return <rejected StepResult>` (skipping or replacing the Opus call) passes every RED and goes live the day script findings are wired (§4 follow-up). **Fix:** spec op4 pins the binding (`from bytedigger_engine import check_ladder`; call `check_ladder.prescreen(...)` by module attribute). Add L17b: monkeypatch `bytedigger_engine.check_ladder.prescreen` to return a `reject`/`script` verdict, and to raise `RuntimeError` in a second variant; assert one `prescreen_verdict` event (outcome `reject` / `config-error` respectively), `invoke_llm_subprocess` called exactly once with base-identical kwargs (finding 1), and the returned result is the LLM result.

3. **MAJOR — the `classifier_cmd` validator has no owner; L18 is not derivable from the spec.** op2's `ValueError` list omits `classifier_cmd`; op1 only says "list non-empty or None". The obvious GREEN passes `"not-a-list"` to `subprocess.run` -> `FileNotFoundError` -> `status "error"` (L18 expects `config-error`); `[]` raises `IndexError` (violates op1.8 "never raises"); a str naming a real binary is spawned. **Fix:** spec op2 adds "`classifier_cmd` not `None` and not a non-empty list of str -> `ValueError`" and op1 adds "same input -> `status="error"`, `rc=None`, no spawn" (or op1 raises `ValueError` — pick one). L11 adds `classifier_cmd="x"`, `[]`, `["a", 1]`; L9 adds the op1 counterpart. L18 then follows from op4.5.

4. **MAJOR — §2 branches with no AC/test, each passable by a spec-violating GREEN.**
   a. op4 "not a dict -> no-op": add L16 cases `prescreen="x"`, `None`, `[]` -> no event, no `extra_data["prescreen"]`.
   b. op3 "any op2 `ValueError` -> rc 2": `--mode block` is caught by argparse before op2, so the mapping is never exercised; a GREEN that lets `ValueError` traceback (rc 1) passes. Add L13 cases `--threshold 1.5`, `--timeout-s 0`, findings file `[{"rule":"r","severity":"BLOCKER","detail":"d"}]`.
   c. op4.5 `ValueError` path: add L18 case `{"classifier_cmd": [..valid..], "timeout_s": 0}` -> `config-error`, gate runs.
   d. enforce + MAJOR: `verdict["classifier"]` is "the op1 result" but op1 is not called. Spec must define it (e.g. the `cmd=None` shape with `status "skipped"`, or `"off"`); L7 asserts it.
   e. op4.2 payload is enumerated; L17 checks a subset, so a GREEN adding `gate_input`/`reasons` to the event (spec text into the event log) passes. Assert `set(ev) == {"outcome","rung","mode","classifier_status","classifier_label","classifier_confidence","classifier_ms","cost_usd","cycle","phase"}` in L17 and L18.

5. MINOR — L19 lint check ignores `report["errors"]`; a lint parse/import error on `check_ladder.py` lands there, not in `violations`. Also assert `"check_ladder.py" not in json.dumps(report.get("errors", []))`.
6. MINOR (m2) — §2 "no `HAL_*` env, no vendor name in code" has no test; add to L19 `assert "HAL_" not in source` and `re.search(r"(?i)\bjev\b|typesafe", source) is None`.
7. MINOR (m3) — require finite numbers (`math.isfinite`) for `confidence` and `cost_usd`; add L9 cases `confidence NaN` -> error, `cost_usd Infinity` -> `None`.
8. MINOR (m4) — declare in §4 (or fix with `start_new_session=True` + `os.killpg` on timeout) that a timed-out classifier's grandchildren are not reaped; also declare the added gate latency (up to `timeout_s`, default 30 s, per validation cycle).
9. MINOR — L9 `off` case: also assert every field except `ms` is `None` (op1.1). L18: spec should state the other payload values on `config-error` (e.g. outcome `escalate`, rung/label/confidence/ms/cost `None`) and whether `extra_data["prescreen"]` is set.
10. MINOR (m7) — declare in §4 that sentinel resume skips the event and retries emit one per cycle; the recall measurement keys by run_id + cycle.
11. MINOR (m8) — op2 `timeout_s`: "positive real, not bool", like `threshold`; add L11 case `timeout_s=True`.
