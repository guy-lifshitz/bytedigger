# bd#102: the semantic verifier stops on pause-lane and fatal codes instead of tagging them UNVERIFIED

**Status: FROZEN r2** (r1 gate: REJECTED, 1 MAJOR + 4 MINOR, all addressed below) · **Tier:** 2 (engine `.py`, Option D: spec, RED, Opus gate, GREEN) · **Class:** BUG (follow-up of bd#82 part 3, #98)
**Base:** `origin/main` `43d3b49`.
**Chokepoint:** `verify_findings_semantic` in `engine_py/bytedigger_engine/lib/plugins/anti_hallucination/semantic_verifier.py`. It is the only caller of `_invoke_verifier_agent`.

## 1. Problem (measured on `43d3b49`)

- `_invoke_verifier_agent` (`semantic_verifier.py:209-218`) maps every non-timeout chokepoint error to the string `UNVERIFIED:\nreason: agent_error <code> ...`. The `verify_findings_semantic` loop (`:470-516`) tags the finding and continues; the step returns `status="ok"` (`:604`).
- `E_LLM_SPEND_LIMIT` is the pause-lane code (`lib/env_limit.py:31`, `PAUSE_LANE_ERROR_CODES`). `engine.py:392` and `run.py:433` remap a terminal `status="error"` with that code to the PAUSED lane. The verifier step never returns it, so the lane cannot engage and up to `MAX_SEMANTIC_VERIFY_FINDINGS` (20) doomed calls are spent.
- `E_CAPABILITY_ESCAPE` (`llm_subprocess.py:1419`, `recoverable=False`: a read-only verifier used a write tool), `E_LLM_BACKEND_UNKNOWN` (`:2004`) and `E_LLM_RUN_ID_MISSING` (`:787`) end as tagged findings in an ok step. Since #98 they are logged only.
- The step is not `skip_on_error` (`phase_6_review.py:6008`), so a returned error stops the phase. The `_verify_findings_semantic` wrapper (`:2396`) passes the result through.
- No flag restores or controls this behaviour (`grep -i semantic flags_catalog.py`: no match).

## 2. Design

- **op1, stop set.** Module constant `_STOP_ERROR_CODES = PAUSE_LANE_ERROR_CODES | {"E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING"}` (import `PAUSE_LANE_ERROR_CODES` from `lib/env_limit.py`).
- **op2, signal.** New module-private exception `_VerifierStop(Exception)` with attribute `result` (the chokepoint `StepResult`). `_invoke_verifier_agent` raises it when `result.status != "ok"`, `result.error_code in _STOP_ERROR_CODES` and the kill switch (op5) is on. The existing log line is kept. Every other path of `_invoke_verifier_agent` is unchanged and still returns `str` (timeouts, other codes, empty response, OSError).
- **op3, loop.** `verify_findings_semantic` catches `_VerifierStop` around both verifier calls (haiku and the opus escalation). On catch it makes no further verifier call, does NOT rewrite the review doc (the file stays byte-identical, so a paused run re-enters cleanly), emits event `phase_6_semantic_verify_stopped` (best-effort, like the other emits) with `step_name`, `error_code`, `findings_total` (= `len(findings)` including overflow findings), `model_haiku_calls`, `model_opus_calls`, `semantic_unverified_reasons` (same name as op4), `duration_ms`, and returns `exc.result` unchanged: same `status`, `error_code`, `error`, `data`, `recoverable`, `step_name`.
- **op4, tally.** The loop keeps `unverified_reasons: dict[str, int]`, counted at the same place as `counters["semantic_unverified"]` (so the sum of its values equals `semantic_unverified`). The key is the code token when the parsed reason starts with `agent_error ` (for example `E_LLM_EXIT`), `overflow` for an overflow tag, else the parsed `reason` value (`agent_timeout`, `empty_response`, `no_verdict_marker`, `malformed_output`, `cannot_decide`, ...), `unknown` when absent. Keys come from the parsed reason, which a model reply can influence; the tally is telemetry only, the stop decision reads the chokepoint result. It is added as `semantic_unverified_reasons` to the ok-return `data` and to the `phase_6_semantic_verify_complete` event. With no UNVERIFIED finding (including no findings, or only already-low-trust skipped ones) it is `{}`. The FAIL-skip return and the no-aggregated-section return are untouched.
- **op5, kill switch.** New gate flag `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`, kind `gate`, default `"1"`, module `lib/plugins/anti_hallucination/semantic_verifier.py`, read with `config_provider.get_config().gate_enabled(...)` at call time. `=0` restores the previous behaviour exactly: no raise, the finding is tagged UNVERIFIED, the step is ok (the tally still counts the code). Catalog entry carries `owner: "guy-lifshitz"` and `provenance: "introduced: bd#102 - ..."`.
- **op6, docs.** One bullet in the existing `### Fixed` block of CHANGELOG `[Unreleased]` (a second `### Fixed` block is rejected by bd#212's duplicate-block lint). The module adds no `SHARED/` string (`test_gh499_oss_string_residue.py` reads its source). The new emit logs in its `except`, like the others.

Out of scope: other callers of the chokepoint; changing which codes are pause-lane; retry or resume logic; any other `agent_error` code (they stay tag-and-continue); `docs/configuration.md`.

## 3. Acceptance criteria. RED file: `engine_py/tests/test_bd102_semantic_verifier_stop_codes.py`

Fixtures: a real review doc on `tmp_path` with N `### SEVERITY: HIGH` findings under `## Aggregated Findings`, each with a `> path:line: quote` line. The model seam is the chokepoint backend registered through `register_backend("claude-subprocess", ...)` (as `test_bd82_semantic_verifier_chokepoint.py::_spy`), returning a scripted `StepResult` per call; no patch of `_invoke_verifier_agent` in the stop ACs. EventLog on `tmp_path`.

- **AC1** 20 findings, backend returns `E_LLM_SPEND_LIMIT` (`recoverable=True`, `data={"pausable": True}`) on call 1. The step returns `status="error"`, `error_code="E_LLM_SPEND_LIMIT"`, `data == {"pausable": True}` (intact), and the backend was called exactly once.
- **AC2** Same for each of `E_CAPABILITY_ESCAPE` (`recoverable=False`, `data={"capability_escapes": ["Write"]}`), `E_LLM_BACKEND_UNKNOWN`, `E_LLM_RUN_ID_MISSING` (parametrized): error code, `data`, `recoverable` and `error` text equal those the backend returned; one call.
- **AC3** On stop the review doc bytes are identical before and after, and the event log holds one `phase_6_semantic_verify_stopped` row with the code and `findings_total == 20`, and no `phase_6_semantic_verify_complete` row.
- **AC4** Stop mid-loop: calls 1-2 return REFUTED/REPRODUCED text, call 3 returns `E_LLM_SPEND_LIMIT`: the step is the error, calls == 3, doc bytes unchanged (no partial rewrite), stopped-row `model_haiku_calls == 3`.
- **AC5** The stop also fires on the opus escalation: call 1 returns `UNVERIFIED:\nreason: cannot_decide`, the escalation call returns `E_LLM_SPEND_LIMIT`: the step is the error, calls == 2.
- **AC6** A non-stop chokepoint error (`E_LLM_EXIT`) on every call, 3 findings: step `ok`, `semantic_unverified == 3`, 3 calls, `semantic_unverified_reasons == {"E_LLM_EXIT": 3}`; the doc has 3 `[UNVERIFIED]` tags (unchanged behaviour).
- **AC7** Kill switch: `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL=0` and `E_LLM_SPEND_LIMIT` on all 3 findings: step `ok`, 3 calls, `semantic_unverified == 3`, `semantic_unverified_reasons == {"E_LLM_SPEND_LIMIT": 3}`. With the variable unset or `1` AC1 holds.
- **AC8** Tally: findings produce a timeout, an empty response, a `no_verdict_marker` text and one overflow (21 findings): `semantic_unverified_reasons == {"agent_timeout": 1, "empty_response": 1, "no_verdict_marker": 1, "overflow": 1}`; the values sum to `semantic_unverified`; the same dict is in the `phase_6_semantic_verify_complete` row.
- **AC9** All REPRODUCED: `semantic_unverified_reasons == {}` in data and event.
- **AC10** `_invoke_verifier_agent` called directly with a stop-code backend result raises `_VerifierStop` whose `.result.error_code` is that code; with `E_LLM_TIMEOUT` it still returns `"UNVERIFIED:\nreason: agent_timeout\n"`; with the switch off it returns the `agent_error <code>` string.
- **AC11** `_STOP_ERROR_CODES` equals `{"E_LLM_SPEND_LIMIT", "E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING"}` and contains `env_limit.PAUSE_LANE_ERROR_CODES`.
- **AC12** Flag: `flags_catalog.FLAGS["HAL_SEMANTIC_VERIFY_STOP_ON_FATAL"]` has kind `gate`, default `"1"`, a non-blank `owner`, and a `provenance` starting with `introduced:`; `scripts/flag_owner_lint.py` exits 0 on the live catalog.
- **AC13** (GUARD) The FAIL-skip path and the no-aggregated-section path return `ok` with no `semantic_unverified_reasons` key and no verifier call. Existing `test_semantic_verifier_*`, `test_bd82_semantic_verifier_chokepoint.py`, `test_phase_6_mass_unverified_5F9817F6.py` and `test_bd212_changelog_helper.py` stay green.
- **AC14** (GUARD) CHANGELOG `[Unreleased]` mentions `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL` in its `Fixed` block, read through the shared helper (`from helpers.changelog import read_changelog, require_entry`, as `test_bd101_fresh_default.py` does); the test carries no `Unreleased`+`.index(` line and no top-section pin (bd#212 AC9).
- **AC15** The `phase_6_semantic_verify_stopped` row carries `semantic_unverified_reasons` (a dict, for example `{"E_LLM_EXIT": 1}` after one tagged finding before the stop), `model_opus_calls` and `duration_ms`; `findings_total == 21` with 21 findings (overflow counted). With zero findings the complete row has `semantic_unverified_reasons == {}`.

## 4. op <-> AC map

op1: AC11 · op2: AC1, AC2, AC10 · op3: AC1-AC5 · op4: AC6, AC8, AC9, AC13 · op5: AC7, AC10, AC12 · op6: AC14 · op3/op4 event fields: AC15.

## 5. Adversarial edges

1. A stop code from the opus escalation after a haiku `cannot_decide` (AC5).
2. A stop after partial progress must not leave a half-rewritten doc (AC4): the rewrite happens only after the loop.
3. `E_CAPABILITY_ESCAPE` carries `recoverable=False`; the result is returned as is, so the engine does not retry it.
4. The `agent_error` reason is truncated to 200 chars and sanitised; the tally key is the first token after `agent_error `, so a long or injected message cannot create unbounded keys (AC6 pins the code-only key).
5. With the flag off the stop codes are still tallied by code, so the loss stays visible.
