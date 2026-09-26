# bd#82 PR3 — the semantic verifier calls the model through the chokepoint

Issue: #82 (part 3 of 4). Class: SYSTEMATIC. Chokepoint: `llm_subprocess.invoke_llm_subprocess`.
Stacked on PR2 (#97).

## Problem (verified against the code)

`lib/plugins/anti_hallucination/semantic_verifier.py:174-185` spawns
`claude -p --model M --max-turns 10` through `bounded_run`, which bypasses the chokepoint. As a
result, the verifier:

- has every tool available: its prompt says "Do NOT modify files", but nothing enforces that;
- ignores the configured backend;
- gets no attestation and no run telemetry;
- does not get a fresh session.

It is the only model call in the engine that does not go through `invoke_llm_subprocess`.

## Contract

1. `_invoke_verifier_agent` calls `llm_subprocess.invoke_llm_subprocess` with:
   - `prompt` = the same system and user prompt as today;
   - `model` = the same tier alias as today (`get_claude_critical()` for opus, `get_claude_fallback()`
     otherwise), still failing closed on an unaccepted alias before any call;
   - `tier_rebind=False`, a new chokepoint keyword (default `True`). It stops GH375 tier
     rebinding from replacing the alias. A configured `model_by_tier` string, even a stale
     versioned id, would otherwise bypass the alias guard, which exists because of the
     2026-06-20 runaway. It would also turn the opus escalation into a second call to the
     cheaper model;
   - `timeout_sec` = the same per-tier timeout as today;
   - `step_name="verify_findings_semantic"`, the step's own name, so spawn events, attestation
     and `effort.by_phase` agree;
   - `allowed_tools=["Read", "Grep", "Glob"]`, since the verifier reads and never writes;
   - `fresh_session=True`, since each finding is judged independently;
   - `hard_gate=False`. It is a verifier inside the review step, not a pipeline gate. A hard gate
     would refuse every haiku-tier call at the floor.
   - No `backend=` keyword: the call follows the configured backend (`HAL_RUNNER_BACKEND`), like
     every other call.
   - No `idle_timeout_sec` or `straggler_cfg`. Those would be refused on backends without a
     watchdog.
2. The result maps onto today's contract. The function still returns a raw string:
   - `status == "ok"` with a non-blank string in `data["raw_response"]` → that text;
   - `status == "ok"` with a blank or non-string `raw_response` → `UNVERIFIED` / `empty_response`.
     This covers a missing key, `None` (agent-sdk can return it), and `data=None`;
   - `error_code` `E_LLM_TIMEOUT` or `E_LLM_API_TIMEOUT` → `UNVERIFIED` / `agent_timeout`;
   - any other error → `UNVERIFIED` / `agent_error <error_code> <head of the error>`, sanitised
     and capped at 200 characters. Chokepoint errors lead with their cause and end with stream
     tails, so the head is the useful part. Every non-ok result is logged (`E_CAPABILITY_ESCAPE`
     at error level), so an UNVERIFIED tag can be traced back to its code.
     Sanitising works as today: newlines and colons are removed, so no second `reason:` can be
     injected. `error=None` gives an empty tail. A timeout on claude-in-session arrives as
     `E_LLM_NO_RESULT_EVENT` and becomes `agent_error`. Both outcomes are UNVERIFIED; only the
     reason text differs.
   - An `OSError` raised during the call maps to `agent_error <sanitised>`, as today, and is
     logged with its traceback.
3. Nothing in the module spawns `claude` itself. `bounded_run` and the literal
   `"claude", "-p"` argv are gone from it.
4. **Consequences of routing through the chokepoint, accepted.**
   - **Effort.** The configured `claude.effort` now applies, as it does to other calls.
   - **Read-only.** Without Bash the verifier cannot execute a `repro:` command. It can still
     cite one: `REPRODUCED` requires `file`, `evidence` and at least one `repro:` line, none of
     which has to have been executed.
   - **claude-in-session.** Without `HAL_IN_SESSION_ENFORCES_TOOLS=1`, the tool list is recorded
     as unenforced rather than refused, as for any worker.
   - **Hang fallback.** The GH1169 agent-sdk hang fallback can spend up to twice the timeout on
     one finding.
5. `--max-turns 10` is dropped. The chokepoint has no turn cap. The verifier is bounded by its
   timeout and, now, by a read-only tool set.

## Acceptance

Test file `engine_py/tests/test_bd82_semantic_verifier_chokepoint.py`.

- The real `claude-subprocess` path runs with `Popen` captured, and `bounded_run` is made to
  raise. The argv carries `--tools Read,Grep,Glob`, and the run emits into a real `EventLog`.
- A spy backend is used only to check dispatch arguments and to map results.

| Clause | ACs |
|---|---|
| 1 | V1, V2, V3, V7, V9 |
| 2 | V4, V5, V5b, V5c, V6 |
| 3 | V3, V8 |
| 1 (tier) | V10 |
| 5 | V3 |

## Test migration

- (Design change after `/code-review`: the round-2 gate accepted tier rebinding for the
  verifier. The review showed that rebinding can bypass the alias guard, so the verifier now
  opts out and V10 pins that.)
- `test_3C533CD8_semantic_verifier_model_pin.py` AC1–AC3 patch `bounded_run`. They now patch
  `llm_subprocess.invoke_llm_subprocess` and read its `model` keyword. The assertions are the same:
  the alias per tier, and fail-closed before any call.
- `test_semantic_verifier_W15.py::test_invoke_verifier_agent_sanitises_stderr_to_prevent_reason_collision`
  patched `subprocess.run`. It now makes the chokepoint return an error whose text carries the
  injected `\nreason:` line, and the assertion is the same.

## Follow-ups

- **Pause lane.** A spend limit (`E_LLM_SPEND_LIMIT`) or a capability escape during
  verification still ends as UNVERIFIED findings in an ok step. Before this change a spend-limit
  exit was also collapsed. Carrying pause-lane and fatal codes out of `verify_findings_semantic`
  changes that function's return contract.
- **Git snapshots.** On agent-sdk, a read-only call still takes the pre/post git snapshot for a
  manifest nobody reads.

## Out of scope

Backend per role and effort as a capability (PR4).
