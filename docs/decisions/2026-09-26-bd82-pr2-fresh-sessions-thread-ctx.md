# bd#82 PR2 — gates and reviews start fresh sessions; step context crosses thread pools

Issue: #82 (part 2 of 4). Class: SYSTEMATIC. Chokepoint: `llm_subprocess.invoke_llm_subprocess`
→ `_dispatch_backend`. Stacked on PR1 (#96). Ports HAL PR #1898 (step context in thread pools).

## Problem (verified against the code)

- **Warm resume.** agent-sdk keeps a per-process session cache keyed `run_id:<step_name before
  the first ".">` (`lib/reference_backends/agent_sdk.py:63-68`) and resumes it on the next
  call. Callers cannot ask for a fresh session.
  - Hard gates (validation, integrity, plan-review, satisfaction) resume the transcript of their
    previous call. That includes the phase 4.5 REVISE→repoll loop (`phase_45_spec.py:3854`).
  - Phase 4.5 and phase 6 both call `invoke_review_llm`, so the phase 6 review can resume the
    phase 4.5 spec-review session.
  - A judge that reads its own earlier verdict is not an independent judge.
- **Thread context.** `telemetry_ctx` keeps the step context in a `threading.local`.
  `_run_satisfaction_evaluators_parallel` (`phase_6_review.py:2770`) submits
  `invoke_llm_subprocess` to a `ThreadPoolExecutor` without that context. Each worker therefore
  sees `run_ctx=None`, which means:
  - no run telemetry and no attestation;
  - no tier;
  - `claude-in-session` fails with `E_LLM_RUN_ID_MISSING`.

  The three evaluators also share one step name.

## Contract

1. **`fresh_session`.** `invoke_llm_subprocess(..., fresh_session: bool = False)`. The effective
   value is `fresh_session or hard_gate`, so every hard gate is fresh.
   - It is passed only to backends that declare the new capability `warm_resume`. Other backends
     never receive a kwarg they did not declare, so third-party backends keep working.
   - The chokepoint resolves the value and forwards it on every dispatch: both `stable_prefix`
     branches and the GH1169 hang fallback. Production gates pass a `stable_prefix`, so both
     branches matter.
   - The rule is resolved inside `_dispatch_backend`, so no dispatch path can drop it.
   - agent-sdk also treats a hard gate as fresh itself, so a registration that omits
     `warm_resume` cannot make a gate resume.
   - agent-sdk declares `warm_resume`. With a fresh session it neither reads nor writes the
     session cache, and it never invalidates it, even when the call fails. There is no resume,
     and the worker's own warm session survives.
2. **Non-gate judges are fresh.** Phase 6 `_invoke_review_llm` and the decorrelated verifier
   `_invoke_decorr_llm` pass `fresh_session=True`.
3. **`telemetry_ctx.run_with_current_run(prev, fn, /, *args, **kwargs)`**
   (port of HAL #1898).
   - The parent captures `get_current_run()` at submit time. The wrapper runs in the worker: it
     clears the slot, re-publishes `prev` through `set_current_run_from`, runs `fn`, and always
     clears the slot in `finally`.
   - HAL's `ctx_step_name` override is left out; nothing here needs it.
   - `prev is None` leaves the slot empty. `prev` and `fn` are positional-only.
   - `contextvars` was rejected: pool threads start with an empty context.
4. **The satisfaction pool uses the wrapper.** Every evaluator keeps the registered step name
   `invoke_satisfaction_llm`, and the telemetry slot keeps the parent's step name.
   - HAL gave each evaluator its own name (`_eval{i}`) so the three would not share a warm
     session. Here every gate is fresh (clause 1), so a per-evaluator name would isolate
     nothing.
   - It would also split the step's name: all-fail results and attestations would carry
     `_eval0`, while telemetry and refusals would carry the registered name.
5. **Enforcement pin.** In `workflows/`:
   - every `.submit(` call passes `telemetry_ctx.run_with_current_run` first, with a
     non-literal `prev`;
   - no `Thread`, `Timer`, executor or pool `.map`, `run_in_executor` or `to_thread` call
     exists.

## Acceptance

Test file `engine_py/tests/test_bd82_fresh_sessions_thread_ctx.py`.

- Fresh sessions run the real agent-sdk backend through the chokepoint. Only the SDK `query` is
  replaced, and it records the `resume` each call asked for.
- Thread context runs real pool threads and the real `_run_satisfaction_evaluators_parallel`.
  Events land in a real `EventLog`.
- §1ab coverage:
  - fresh entry (cycle 1);
  - in-phase retry (`set_current_run_from`, cycle 2);
  - resume from a non-main thread.

| Clause | ACs |
|---|---|
| 1 | F1 (resume leg), F2, F2b, F2c, F3, F4, F4b, F5, F6b, F6d, F7, F8 |
| 2 | F6, F6c |
| 3 | T1, T3, T3b, T4, T5, T7 |
| 4 | T6 |
| 5 | T9 |

## Accepted residuals (follow-ups)

- **Warm resume is still the default for non-gate calls.** A new non-gate judge must ask for
  `fresh_session=True`. Making resume opt-in for writer roles would close this class of bug;
  it belongs with the per-role policy of PR4.
- **Event sinks see concurrent emits.** Evaluator threads now emit into the run's event sink at
  the same time. The default `EventLog` is safe here (one `O_APPEND` write per event), but the
  `EventSink` seam makes no thread-safety promise.

## Sibling tests (must stay green)

`test_gh705_satisfaction_stable_prefix`, `test_phase_6_satisfaction_multi_evaluator`,
`test_ccbb65dc_straggler_watchdog`, `test_gh1157_agent_sdk_retry`,
`test_gh1169_agent_sdk_hang_recovery`, `test_bd71_agent_sdk_observations`,
`test_gh379_decorr_verify`, `test_subprocess_telemetry`, `test_gh497_telemetry_hygiene`.

## Out of scope (later PRs of #82)

semantic_verifier through the chokepoint (PR3); backend per role and effort as a capability (PR4).
