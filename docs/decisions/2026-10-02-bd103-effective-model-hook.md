# bd#103: the hard-gate floor checks the model a backend actually runs (`effective_model` hook)

**Status:** r2 frozen (gate r1 REJECTED, r2 APPROVED with 4 advisory MINORs: m1–m3 folded in below, m4 recorded as a residual) · **Tier:** 2 (prod `llm_subprocess.py`, `lib/reference_backends/pydantic_openai.py`; normative `conformance/AUTHORSHIP_SPEC.md` AC-M3 amended; one new test file, one sibling test rewritten; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._dispatch_backend`, the one dispatch every backend
passes. It resolves the effective model once, through a new `_effective_model(...)`, and the floor check,
the attestation and the R3.3 pin check all read that one value.
**Source:** bd#103 (follow-up of #82 part 1, #96).
**Not in scope (§1v):** `engine.py`, `phases/*.md` (bd#89); the effort lookup (`_load_effort*`, bd#107 PR,
stays keyed on the **requested** model, pinned by AC14); `_assert_in_session_model_or_downgrade` (claude-in-session, checks the
model the host reports after the fact, and has its own path); `pydantic_anthropic` and other reference
backends (none remap the model today); the `_invoke_subprocess` direct-call branch that bypasses the chokepoint.

## §1 Problem (measured on `24dbd13`)

1. `_dispatch_backend` (`llm_subprocess.py:1432-1440`) checks the hard-gate floor with
   `_assert_hard_gate_opus(_build_claude_argv(model), ...)`, so it checks the model the **caller asked for**.
2. A backend can run something else. `pydantic-openai` runs `PYDANTIC_BACKEND_DEPLOYMENT or model`
   (`pydantic_openai.py:581`, used at `:608`). #96 added an adapter-local refusal for that one case
   (`:582-596`: `hard_gate and deployment != model` → `E_HARD_GATE_MODEL_DOWNGRADE`).
3. Any other backend that remaps (a future Bedrock/Vertex/Azure adapter, a router) bypasses the floor
   silently, and the attestation (`_attest_payload`, `:1215-1251`, `model_requested`) names a model that did
   not run. That breaks the docstring invariant "the log never names a model that was not invoked".
4. The adapter-local check is also too strict. It refuses a gate whose deployment differs from `model`
   even when the deployment itself meets the floor under the active provider's rank.

## §2 Design

### 2.1 Registry
`register_backend(name, impl, *, manifest_source, capabilities=None, overwrite=False, effective_model=None)`.
- `effective_model`: `None`, or a callable `(model: str) -> str`. Anything else → `TypeError`
  (same style as the `impl` check).
- It is stored in a new single-source map `_BACKEND_EFFECTIVE_MODEL: dict[str, Callable]`. A registration
  without a hook removes any earlier hook for that name, so `overwrite=True` without the kwarg clears it.
- `reset_backends()` restores the map from a `_DEFAULT_BACKEND_EFFECTIVE_MODEL` snapshot. Built-ins have no hook.

### 2.2 Resolution: `_effective_model(resolved_backend, model) -> tuple[str | None, str | None]`
Returns `(effective, problem_detail)`. It catches `Exception` only, so `KeyboardInterrupt` / `SystemExit`
propagate and Ctrl-C is never swallowed into a degrade.
- No hook → `(model, None)`.
- The hook returns a `str` with non-whitespace content → `(that str verbatim, None)`.
- The hook raises an `Exception`, or returns a non-str, an empty str or a whitespace-only str →
  `(None, "<ExcType>: <msg>"` or `"returned <type/repr>")`.
An effective model the provider can't map to a family (for example an Azure deployment label) is not a
resolution problem. For a gate it fails the floor (family `None`). For a worker it is recorded verbatim, and
R3.3 / `bd_l3` report it as `not-checked`, which is the existing behaviour for an unrecognised token.
It is called exactly once per `_dispatch_backend` call, with the model `_dispatch_backend` received
(already post tier-rebind).

### 2.3 `_dispatch_backend`, first statement block (before the floor check)
1. `effective, problem = _effective_model(resolved_backend, model)`.
2. If `problem` is set:
   - emit `effective_model_unresolved` with `{backend, step_name, hard_gate, model, detail}` (via
     `_emit_safe`, only when `run_ctx` has an event log);
   - hard gate → return `StepResult(status="error", error_code="E_HARD_GATE_MODEL_DOWNGRADE",
     recoverable=False)`. The message names the backend and says the effective model can't be resolved.
     The backend is not called and no `model_invocation_attested` is written (`[bd10:27]`, refusals don't
     attest). Fail closed: we can't verify an unknown model. With no run context (no event log) it still
     refuses and doesn't raise.
   - not a hard gate → degrade: `effective = model` and continue (Guy principle 2: degrade, don't fail).
3. The floor check runs on `effective`: `_assert_hard_gate_opus(_build_claude_argv(effective), ...)`.
   The `hard_gate_refused` event therefore carries `observed_model == effective`.
4. The backend is **still called with `model=model`** (requested). The backend owns the remap, and the hook
   only declares what that remap produces. So the LLMBackend protocol is unchanged.
5. `_emit_attestation(..., model=effective)` and `_pin_mismatch_refusal(result, model=effective, ...)`.
   The attestation keeps exactly its nine keys; `model_requested` now carries the effective model, which is
   what AC-M3's rationale asks for ("never a model that was not invoked"). AC-M3's wording ("the
   dispatched model") predates remapping backends, so it is **amended** in `conformance/AUTHORSHIP_SPEC.md`:
   `model_requested` is the post-rebind dispatched model **as resolved by the backend's `effective_model` hook**,
   and equals the dispatched model when there is no hook. The same amendment applies to AC-M3's comparison-target
   sentence (R3.3 compares against that same resolved value). The `_attest_payload` docstring and the
   `_dispatch_backend` order-of-operations docstring (new resolve/remap step before the floor check) are updated to match.
   Without a hook, effective == model, so the output is byte-identical to today.
6. **Evidence trade-off.** With a remapping hook the nine-key attestation no longer carries the requested model.
   To keep that evidence without changing the payload shape, `_dispatch_backend` emits
   `effective_model_remapped` with `{backend, step_name, hard_gate, model, effective_model}` whenever
   `effective != model`, before the floor check, so it is also written when the gate then refuses. No event is
   written when they are equal. Like every chokepoint event it is written only when `run_ctx` has an event log;
   without one the remap still applies and nothing is emitted.

### 2.4 pydantic-openai
- New module function `_effective_deployment(model: str) -> str` that returns
  `os.environ.get("PYDANTIC_BACKEND_DEPLOYMENT") or model`. The backend body uses it at `:581` (one
  expression, two readers, no drift).
- `register()` passes `effective_model=_effective_deployment`.
- The adapter-local check `:582-596` is **removed**. The chokepoint now refuses a below-floor deployment.
  A deployment that meets the floor under the active provider's rank passes (provider-agnostic: the floor
  comes from `get_provider()`, and nothing is hardcoded to `opus` or `gpt-*`).
- **Trust boundary (stated, unchanged from base).** The floor checks the *declared* model name the hook returns,
  not the weights served behind it. An Azure deployment labelled `fable` that serves a small model passes, as
  `deployment == "opus"` already passed on base. Verifying served weights is out of scope.

### 2.5 Error codes
No new code. `E_HARD_GATE_MODEL_DOWNGRADE` already means "a gate would run on a model that is unchecked or
below the floor". Two new event types, `effective_model_unresolved` and `effective_model_remapped`.

## §3 Acceptance criteria (`engine_py/tests/test_bd103_effective_model_hook.py`)

Real `EventLog` in tmp_path, backends registered through `register_backend` with a call spy (the backend is
the boundary, not the unit under test), dispatch through the public `invoke_llm_subprocess`.
`reset_backends()` in teardown.

- **AC1** `register_backend(..., effective_model=<non-callable, non-None>)` → `TypeError`; nothing registered.
- **AC2** No hook, hard gate, `model="opus"` → the backend is called with `model="opus"`; the
  `model_invocation_attested` event on disk has `model_requested == "opus"` (regression).
- **AC3** Hook `lambda m: "sonnet"`, hard gate, `model="opus"` → `E_HARD_GATE_MODEL_DOWNGRADE`,
  `recoverable is False`, spy called 0 times, the on-disk `hard_gate_refused` event has `observed_model == "sonnet"`.
- **AC4** Hook `lambda m: "fable"` (meets the floor), hard gate, `model="opus"` → the backend is called once
  **with `model="opus"`**; the attestation has `model_requested == "fable"`.
- **AC5** A worker (no hard gate), hook `lambda m: "haiku"`, `model="opus"` → dispatched (status from the spy);
  attestation `model_requested == "haiku"`. The spy reports `observed_model="haiku"` → no `model_pin_mismatch`
  event and no `E_MODEL_PIN_MISMATCH`.
- **AC6** Hook raises `RuntimeError("boom")`:
  (a) hard gate → `E_HARD_GATE_MODEL_DOWNGRADE`, spy 0 calls, one on-disk `effective_model_unresolved` with
  keys `{backend, step_name, hard_gate, model, detail}`, `hard_gate is True`, `"RuntimeError"` in `detail`;
  the error message contains the backend name and the phrase `effective model`; **no**
  `model_invocation_attested` event on disk;
  (b) worker → spy called once, the event is present with `hard_gate is False`, attestation
  `model_requested == model`.
- **AC7** Hook returns `""`, `"   "`, `None`, or `42` (parametrized), hard gate → refused as in AC6(a), including
  no attestation; worker → as AC6(b).
- **AC8** The hook is called exactly once per dispatch, with the model `_dispatch_backend` receives (counter + arg
  capture). (a) `tier_rebind=False` → the requested model; (b) tier dispatch **active** (same fixture shape as
  AUTHORSHIP_SPEC AC-M3's test) → the hook receives the post-rebind tier model, not the caller's argument.
- **AC9** Registering `overwrite=True` without `effective_model` clears an earlier hook (AC3's setup then
  dispatches); `reset_backends()` clears hooks for runtime registrations.
- **AC10** pydantic-openai through the chokepoint (fake `pydantic_ai`, same helper as
  `test_bd82_tool_restriction._install_fake_pydantic_openai`, then `pydantic_openai.register()`):
  (a) `PYDANTIC_BACKEND_DEPLOYMENT=gpt-4o-mini`, hard gate, `model="opus"` → `E_HARD_GATE_MODEL_DOWNGRADE`
  and the fake `Agent` is never constructed;
  (b) env unset → the effective model is `"opus"`, and the gate proceeds past the floor;
  (c) `pydantic_openai._effective_deployment("opus")` returns the env value when set, else `"opus"`;
  (d) `PYDANTIC_BACKEND_DEPLOYMENT=fable` (meets the floor), hard gate, `model="opus"` → passes the chokepoint and
  the fake `Agent` is constructed (the stricter local check is gone); attestation `model_requested == "fable"`.
- **AC11** Direct call to `pydantic_openai_backend(..., hard_gate=True)` with a deployment override no longer
  returns `E_HARD_GATE_MODEL_DOWNGRADE` locally; it proceeds to the fake `Agent` (the check now lives only at
  the chokepoint).

- **AC12** A hook remaps (`"opus"` → `"fable"`, and in a second case → `"sonnet"` under a hard gate): one on-disk
  `effective_model_remapped` with keys `{backend, step_name, hard_gate, model, effective_model}`,
  `model == "opus"`; it is present in the refused `"sonnet"` case too. Hook returning the same model (and no hook):
  no such event.
- **AC13** No run context (no event log), hard gate, hook raises → returns `E_HARD_GATE_MODEL_DOWNGRADE` and
  doesn't raise; spy 0 calls.
- **AC14** Effort stays keyed on the requested model (recording stubs; the loaders are not the unit under test):
  (a) hard gate, hook `"opus"` → `"fable"` → `_load_effort_gate` is called with `"opus"`;
  (b) worker, hook `"opus"` → `"haiku"` → `_load_effort` is called with `"opus"` as its model argument.

Side-effect anchor (§1l): AC3, AC4 and AC6 assert on events read back from the on-disk event log.

## §4 Sibling tests (§1a)

- `test_bd82_tool_restriction.py::test_ac26_pydantic_openai_deployment_override_refused_for_gate` calls the
  backend directly and asserts the local refusal. It is **rewritten by RED** to go through
  `invoke_llm_subprocess(backend="pydantic-openai", ...)` (same assertion, the chokepoint path). Its contract
  moves, it is not dropped.
- Must stay green (no hook ⇒ byte-identical output; in-session out of scope): `test_llm_subprocess_23680DDA.py`,
  `test_bd29_in_session_pin_fail_closed.py`, `test_bd28_bd_l3_checker.py`, `test_bd73_r31_r32_verdicts.py`,
  `test_bd63_r35_enforcement_falsifiable.py`, `test_bd141_p4_bd_l3_cli.py`, `test_bd155_l2_event_type_key.py`,
  `test_gh426_fable_model_floor.py`, `test_68E964FB_llm_backend_registry.py`, `test_02FF48F4_model_pin_insession.py`,
  `test_2FDA949D_model_pin_warn.py`, `test_GH873_usage_property_gate.py`, `test_gh898_backend_pip_hint.py`,
  `test_register_backend_A60F1FE3.py`, `test_llm_subprocess_hard_gate.py`,
  `test_GH439_hard_gate_effort_pin.py`, `test_bd82_tool_restriction.py`, `test_bd82_role_backend_effort.py`,
  `test_GH852_pydantic_anthropic_backend.py`, `test_bd10_l3_authorship.py`, `test_bd68_l3_observation_producers.py`,
  `test_bd71_agent_sdk_observations.py`.

## §5 Scope

Prod: `engine_py/bytedigger_engine/llm_subprocess.py`, `engine_py/bytedigger_engine/lib/reference_backends/pydantic_openai.py`.
Tests: `engine_py/tests/test_bd103_effective_model_hook.py` (new), `engine_py/tests/test_bd82_tool_restriction.py` (test_ac26 only).
Normative/docs: `engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md` (AC-M3 amendment, §2.3.5),
`docs/backends.md` (the `register_backend` section documents `effective_model=`, and the `PYDANTIC_BACKEND_DEPLOYMENT`
bullet says a hard gate floor-checks the deployment name), `CHANGELOG.md` [Unreleased]. The `_attest_payload` docstring
is in the prod file above.
Merge note: bd#107 (PR pending) also edits `_dispatch_backend`. This branch rebases onto it after merge, and the
two blocks are independent (effort stays keyed on the requested model).
