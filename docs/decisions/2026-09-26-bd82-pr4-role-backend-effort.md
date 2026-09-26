# bd#82 PR4: backend per role; effort as a capability

Issue: #82 (part 4 of 4). Class: SYSTEMATIC. Chokepoint: `llm_subprocess.invoke_llm_subprocess`
→ `_dispatch_backend`. Stacked on PR3 (#98).

## Problem (verified against the code)

- **One backend for everything.** The backend is global: kwarg > `HAL_RUNNER_BACKEND` > default
  (`llm_subprocess._resolve_backend`), and no caller passes `backend=`. So a judge cannot run
  on a strong backend while workers run on a cheap one, which is the basis of the portability
  plan in the #82 review.
- **Effort is applied only on two paths.** The reasoning effort from the models config is
  applied only on the claude-subprocess argv (`_invoke_subprocess`) and, for non-gates, in
  anthropic-api's thinking budget.
  - agent-sdk (the default), claude-in-session and the pydantic backends drop it silently.
  - An operator's gate pin (`claude.effort.by_model[family]`, GH439) is honored only on
    claude-subprocess.

## Contract

1. **Role.**
   - `invoke_llm_subprocess(..., role: str | None = None)`. The role is `"judge"` or
     `"worker"`, and anything else raises `ValueError`.
   - It defaults to `"judge"` for a hard gate and `"worker"` otherwise.
   - The non-gate judges pass `role="judge"`: the phase 6 reviewer, the decorrelated verifier
     and the semantic verifier.
   - The role only routes the call. `hard_gate` alone decides the floor, fresh sessions and gate
     effort, so `hard_gate=True, role="worker"` is legal: the call routes as a worker but is
     still held to the gate rules.
   - A role is validated even when `backend=` is given.
2. **Backend per role.** `_resolve_backend(kwarg, env, role=None)` resolves in this order:
   - kwarg;
   - `HAL_RUNNER_BACKEND_JUDGE` / `HAL_RUNNER_BACKEND_WORKER` for that role (source `"env-role"`);
   - `HAL_RUNNER_BACKEND`;
   - the default.

   - `role=None` keeps today's resolution, so existing callers of `_resolve_backend` are
     unchanged. That includes the adapter identity at `engine.py:1341` and the phase 6 report at
     `:2037`, which keep reporting the global backend.
   - An empty or whitespace role variable falls through to `HAL_RUNNER_BACKEND`.
   - The `runner_backend_resolved` event carries the role.
   - The phase 6 in-session straggler check resolves the reviewer's backend with
     `role="judge"`.
   - Both flags are catalogued in `flags_catalog.py`. conftest clears them, including their
     `BD_`/`BYTEDIGGER_` aliases.
3. **Effort is resolved once per dispatch, inside `_dispatch_backend`.**
   - The value is `_load_effort_gate(model)` for a hard gate and `_load_effort(model, step_name)`
     otherwise.
   - It is resolved after GH375 tier rebinding, so a rebound model gets its own effort. It is
     also resolved on the GH1169 fallback dispatch.
   - It is passed as `effort=` only when it has a value, and only to backends that declare the
     new capability `effort` (the same rule as `stable_prefix`):
     - claude-subprocess: `--effort`;
     - agent-sdk: the SDK's native `ClaudeAgentOptions.effort`;
     - anthropic-api: its thinking budget, now for gates too when a gate pin is set.
   - The passed value always wins. When a backend is called directly without it:
     - claude-subprocess resolves effort itself, as today;
     - anthropic-api keeps its old rule (non-gate effort, none for a gate), so GH329 stays valid;
     - agent-sdk applies none, as today.
   - **claude-in-session**: the request JSON carries `"effort"`, or `null` when nothing
     resolves. The servicer declares that it
     applies it with `HAL_IN_SESSION_APPLIES_EFFORT=1` (exactly `"1"`, catalogued), which grants
     the `effort` capability. This mirrors `HAL_IN_SESSION_ENFORCES_TOOLS`.
4. **Effort that cannot be applied.** When effort resolves to a value and the backend does not
   declare `effort`:
   - a hard gate is refused before dispatch with `E_GATE_EFFORT_UNSUPPORTED`
     (`recoverable=False`) and a `gate_effort_refused` event. The event payload is `backend`,
     `step_name` and `effort`;
   - a worker is dispatched, and `effort_not_applied` (same payload) is emitted and logged as a
     warning.
   - **Order in `_dispatch_backend`:** model floor → tool restriction → effort → injections.
   - **Consequence, accepted by the user:** a configured gate pin (`claude.effort.by_model`)
     refuses opus gates on claude-in-session until the servicer sets
     `HAL_IN_SESSION_APPLIES_EFFORT=1`. It also refuses them on the pydantic backends.
     A legacy global string or a `by_phase`-only config is not a gate pin, so it never refuses a
     gate.

   With no effort configured nothing changes: the argv and the options are byte-identical to
   today.

## Acceptance

Test file `engine_py/tests/test_bd82_role_backend_effort.py`.

- Role routing uses two spy backends, registered under different names, selected by the role
  env vars.
- Effort uses real side effects:
  - the real claude-subprocess argv, with `Popen` captured;
  - the real agent-sdk backend's options turned into CLI argv by the real SDK transport;
  - the real anthropic-api request body;
  - events in a real `EventLog`.

| Clause | ACs |
|---|---|
| 1 | R1, R1b, R2, R3, R3b |
| 2 | R4, R4b, R5, R6, R7, R8, R8b, R9 |
| 3 | E1, E2, E2b, E2c, E3, E3b, E3c, E3d, E4, E4b, E4c, E4d, E9, E10 |
| 4 | E5, E5b, E6, E7, E7b, E8 |

## Test hermeticity

conftest points `config_provider.models_config_path` at a missing file for every test, which is
what CI sees. A developer's own models config (which may pin effort) can therefore no longer
change test outcomes. Tests that need a config patch the path themselves.

## Test migration

- `test_register_backend_A60F1FE3.py`: claude-subprocess capability-set equality gains `effort`.
- `test_2A6986ED_anthropic_api_backend.py:144`: `{"no_tools", "effort"}`. gh1082 AC18 runs that
  file.
- GH439 and 32ED59E2 pin the effort lines inside `_invoke_subprocess`. The direct-call
  resolution stays there, so they are unaffected.

## Out of scope

- **Model per role** already exists (`model_config.get_role_model`); callers pick the model.
- **Making warm resume opt-in for writer roles** (the PR2 residual) stays a follow-up.
