# bd#163 — gate round cap per tier/complexity (frozen spec, 1 page)

Class: SYSTEMATIC (one cap, many retry sites). Chokepoint: `WorkflowEngine._execute_steps` retry check (`engine.py` ~699-705) — the only place that compares `cycle_count` to `_MAX_VALIDATION_CYCLES`.

## Design
New module `engine_py/bytedigger_engine/lib/gate_round_cap.py` (pure, no LLM, no provider/Anthropic/Jev branch):
- `DEFAULT_CAP = 2` (= today's `_MAX_VALIDATION_CYCLES`); `HARD_MAX = 6` (= `_GATE_BUDGET_HARD_BACKSTOP`).
- `resolve_gate_round_cap(label, table_raw) -> GateCap(cap:int, source:str, label:str|None, warnings:list[dict])`.
  - `table_raw`: dict or JSON string `{"<TIER>": int}` (HAL parity: `{"MICRO":1,"TIER2":3,"TIER3":3,"OPTION_D":3}` is a valid host table, AC5).
  - label match is case-insensitive on the key; no label or no matching key -> `DEFAULT_CAP`, source `"default"`, no warning when table is absent/None/empty.
  - Table present but malformed (not dict/JSON, or the matched value is not an int, is bool, <1, or >HARD_MAX) -> `DEFAULT_CAP`, source `"fallback"`, one warning `{"event":"gate_round_cap_table_invalid","reason":...}`. Never raises, never unlimited. (`0` is invalid: bd has no "gate off" tier.)
  - valid match -> source `"table"`.
- Table source, in order: `org_config["gate_round_caps"]`; else host provider `get_config().gate_round_caps()` via `getattr` (off-Protocol method; a provider without it, or one that raises, degrades to "no table" with a `gate_round_cap_provider_unavailable` warning — provider-down path); else env `BD_GATE_ROUND_CAPS` (JSON). `_DefaultConfigProvider.gate_round_caps()` returns the env value or None.
- Label source: `org_config["gate_tier"]`, else `org_config["complexity"]`, else env `BD_GATE_TIER`.

Engine: `_resolve_gate_cap(context, run_id)` caches per run_id (`self._gate_caps`), emits the warnings once per run_id (`gate_round_cap_resolved` event carries cap/source/label). The retry check uses `cap` instead of `self._MAX_VALIDATION_CYCLES`; class attrs `_MAX_VALIDATION_CYCLES=2` and `_GATE_BUDGET_HARD_BACKSTOP=6` stay (default + hard max; `test_engine_cap_consistency` keeps passing). `gate_budget_ok` clause is unchanged except it never exceeds `HARD_MAX`.

Over cap (retry wanted, `cycle_count >= cap`, no `gate_budget_ok`): emit `gate_round_cap_exceeded {phase, step_name, cycle, cap, source, label, exit:"host_escalation"}`. If `source == "table"` the returned result is a copy of the step result with `error_code="E_GATE_ROUND_CAP"`, `recoverable=False`, `data` + `{"gate_round_cap": cap, "escalation": "host_decision_required"}` (declared legitimate exit: the host decides, the event records `exit: host_escalation`). If source is `default`/`fallback` the legacy result is returned unchanged (AC1: existing runs unchanged).

`E_GATE_ROUND_CAP` is added to `error_codes.py`, `ERROR_CODES.md` and `lib/task_resume.py::_STOP_CODES` (no auto-resume, cf. bd#85).

Idempotence (AC3): the cap compares the `cycle_count` carried in step data; the engine holds no round counter, so an in-phase retry, a resume with the same run-id, and a replay each see the same `cycle_count` and the same decision. The resolve warning is emitted once per run_id per engine instance.

## AC -> test (file `engine_py/tests/test_bd163_gate_round_cap.py`)
1. default (no table, any label) -> cap 2, source default, engine retries at cycle_count 1, stops at 2 with the LEGACY error code (no E_GATE_ROUND_CAP).
2. table `{"MICRO":1,"TIER2":3}` + label MICRO -> retry denied at cycle_count 1 with `E_GATE_ROUND_CAP`, event `gate_round_cap_exceeded`, `recoverable False`; label TIER2 -> retry allowed at cycle_count 2, denied at 3. `E_GATE_ROUND_CAP` in `ERROR_CODES.md`, `error_codes.py` and `_STOP_CODES`; `plan_resume` returns `stop` for it.
3. same run through fresh engine / in-phase retry / second engine with the same run_id & replayed `cycle_count` -> identical decision, resolve warning count unchanged (one per run_id per engine).
4. malformed table (`"not json"`, `{"MICRO":"x"}`, `{"MICRO":0}`, `{"MICRO":99}`, `{"MICRO":true}`) -> cap 2, source fallback, one `gate_round_cap_table_invalid`, no exception; provider whose `gate_round_caps()` raises -> default + `gate_round_cap_provider_unavailable`.
5. HAL table `{"MICRO":1,"TIER2":3,"TIER3":3,"OPTION_D":3}` resolves to 1/3/3/3 through `org_config["gate_round_caps"]` AND through a fake provider `gate_round_caps()` AND env `BD_GATE_ROUND_CAPS`.
Design constraints: no LLM call anywhere on the path (test patches the LLM subprocess to fail); provider-down path covered (AC4); both backends: the check reads no backend config (test runs with backend env set to `claude-subprocess` and `anthropic-api`, same decisions).

## Files in scope
engine_py/bytedigger_engine/{lib/gate_round_cap.py (new), engine.py, config_provider.py, error_codes.py, ERROR_CODES.md, lib/task_resume.py}; engine_py/tests/test_bd163_gate_round_cap.py (new); docs (CHANGELOG line). NOT in scope: workflow-level `MAX_REVIEW_CYCLES` constants in phase_45_spec/phase_6_review (their consistency test stays at 2).
