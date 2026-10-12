---
red_tests: [engine_py/tests/test_bd291_in_session_observed_tools.py]
sibling_tests: [engine_py/tests/test_bd145_reserved_observation_fields.py, engine_py/tests/test_bd82_tool_restriction.py, tests/test_bd89_p3d_flag_owner_lint.py]
paths: [engine_py/bytedigger_engine/llm_subprocess.py, engine_py/bytedigger_engine/flags_catalog.py, docs/configuration.md, CHANGELOG.md]
---
# bd#291: claude-in-session reads `observed_tools` from `.res.json` for the capability escape check

**Tier:** 2 · **Class:** observation does not reach the common check (slice of #104).
**Base:** `origin/main` `74bed46`.
**Chokepoint:** the result dict built in `_invoke_in_session` (`llm_subprocess.py`, the `data` dict next to `observed_model`). `_capability_escape_refusal` in `_dispatch_backend` and `_attest_payload` already read `observed_tools` from the result and are not touched.

## 1. Problem

`_invoke_in_session` never puts `observed_tools` into its result, so `_capability_escape_refusal` always sees "nothing observed" and skips; the attestation event has `observed_tools: null` for in-session. A servicer that reports the tools it called is not heard. Host side is out of scope; this slice only accepts the field.

## 2. Design

- **op1, read.** In `_invoke_in_session`, after the `data` dict is built and before `extra_data` is merged: when the switch is on and `result_obj` has the key `observed_tools` whose value is a `list` and every item is a `str`, set `data["observed_tools"] = list(value)` (a copy; `[]` is a valid observation and is kept).
- **op2, absent or invalid.** Key absent: nothing is added, never `None` (bd#145 test `"observed_tools" not in d` stays green unedited). Key present but not a list of strings (`"Read"`, `[1]`, `["Read", 2]`, `null`, a dict): nothing is added and exactly one `in_session_observed_tools_dropped` event is emitted through `_emit_safe` with `{"backend": "claude-in-session", "reason": "not_list_of_strings", "value_type": type(value).__name__}` and the step's run id; the step does not fail. A key that is absent emits no event. With a `None` `run_ctx` or event log, no event and no failure.
- **op3, reserved field.** `observed_tools` stays in `RESERVED_OBSERVATION_FIELDS`; `extra_data` still cannot inject it. The key comes from `.res.json` only; the no-inject guarantee is the dispatcher strip of `extra_data` (bd#145), not this handler.
- **op4, effect.** With the key set, the existing check decides: an observed head outside `allowed_tools` gives `status="error"`, `error_code="E_CAPABILITY_ESCAPE"`, `data["capability_escapes"]` listing the heads outside the declaration (`["Write"]` for observed `["Read","Write"]` against `["Read"]`), and the `model_invocation_attested` event carries `observed_tools` as received. Observed inside the list: success, attestation carries the list. `allowed_tools=None`: no check, as today. No new error code.
- **op5, the switch.** New flag `HAL_IN_SESSION_OBSERVED_TOOLS`, read once per call via `config_provider.get_config().gate_enabled(...)`: on unless exactly `0`; `BD_`/`BYTEDIGGER_` aliases honoured. With `0` the field is not read: no key, no drop event, even when the field is invalid; behaviour is byte-identical to `74bed46`.
- **op6, catalog and docs.** `flags_catalog.FLAGS["HAL_IN_SESSION_OBSERVED_TOOLS"]`: kind `gate`, default `"1"`, module `llm_subprocess.py`, `owner: "guy-lifshitz"`, `provenance: "introduced: bd#291 - the claude-in-session result carried no observed_tools, so the capability escape check never ran on that path - read on by default, =0 restores the old behaviour"`, and the description carries `retire-by:2026-11-10`. The name is declared as a constant next to the other `_IN_SESSION_*` names. One row in `docs/configuration.md`; one bullet in `### Added` of CHANGELOG `[Unreleased]`.

## 3. Out of scope

Host side (servicer, protocol, docs); `enforced_tools` and `applied_effort`; `HAL_IN_SESSION_ENFORCES_TOOLS` / `HAL_IN_SESSION_APPLIES_EFFORT` and `capability_enforcement`; what a hard gate does when the field is missing.

## 4. Acceptance (reference cases)

Precondition for cases 1, 2 and 5: `HAL_IN_SESSION_ENFORCES_TOOLS=1` (so the attestation reads `runtime-allowlist`); the `BD_IN_SESSION_OBSERVED_TOOLS=0` alias behaves as case 5.

1. `allowed_tools=["Read"]`, res `observed_tools=["Read","Write"]` → error `E_CAPABILITY_ESCAPE`, `capability_escapes == ["Write"]`, attestation `observed_tools == ["Read","Write"]`.
2. Same, res `["Read"]` → ok; attestation `["Read"]`.
3. No field in res → ok; `"observed_tools" not in data`; attestation `None`; no drop event.
4. Field `"Read"` and `[1]` → ok; key absent; one drop event each.
5. `HAL_IN_SESSION_OBSERVED_TOOLS=0` with case 1 → ok, key absent, attestation `None`, no drop event.
6. `extra_data={"observed_tools": ["Write"]}` with no field in res → key absent (reserved).
7. Catalog: the flag is a `gate` with default `"1"`, owner, `introduced:` provenance and `retire-by:`; `scripts/flag_owner_lint.py` stays clean.
