---
red_tests:
  - engine_py/tests/test_bd109_adapter_identity_roles.py
sibling_tests:
  - engine_py/tests/test_bd18_emissions.py
  - engine_py/tests/test_bd82_role_backend_effort.py
  - engine_py/tests/test_bd8_l1_oracle.py
  - engine_py/tests/test_engine.py
  - engine_py/tests/test_event_log_replay_e2e.py
paths:
  - engine_py/bytedigger_engine/engine.py
  - engine_py/bytedigger_engine/conformance/EMISSIONS_SPEC.md
  - CHANGELOG.md
---
# bd#109: the run identity records the backend each role routes to

**Status: r1, TIER2.** Follow-up to #82 part 4 (#100).
**Class:** SYSTEMATIC: a record that names one value where the engine resolves several. Per-role routing made the backend a function of the role, and the one record meant to say "which adapter produced this run" still answers for no role at all. Any new routed role would repeat the gap unless the record is built from the same list the router validates.
**Chokepoint:** `engine._resolve_adapter_identity`, the one function that builds `run_identity.adapter_identity` (called from the single `run_identity` emit in `WorkflowEngine.execute`). The per-role resolution stays in `llm_subprocess._resolve_backend`; this function adds no resolution rule of its own.
**Side of the seam:** engine. **Owner:** bd. **Flag / expiry:** none (see Kill switch).

| | |
|---|---|
| **LLM calls added** | None. An environment read and a pure lookup, twice per `execute()`. No process, no network, no credential read. |

## Principles (Guy's, for bd)

1. **Deterministic first.** The identity is computed from the environment by the same pure function the calls use. No model, no probe of the backend.
2. **Provider-agnostic: a path degrades, it does not fail.** A role that cannot be resolved is left out of `roles` and logged; `execute()` carries on and the run identity is still emitted (AC8).
3. **Works on a subscription and on API tokens.** The function reads only the `HAL_RUNNER_BACKEND*` selectors, never a credential, and does not check that a named backend is registered or usable (AC9 runs the same case under three credential environments).

## Problem (measured on 43e8556)

`engine.py:1457` `_resolve_adapter_identity` calls `llm_subprocess._resolve_backend(None, _config_env_mapping())` with no role (l.1462), so the `run_identity` event (`engine.py:354`, once per `execute()`) names the global backend. The engine routes two roles (`llm_subprocess._ROLES = ("judge", "worker")`, l.160; `invoke_llm_subprocess` resolves `_resolve_backend(backend, env, role=effective_role)` at l.2007 and refuses any other role at l.2000). With `HAL_RUNNER_BACKEND_WORKER=claude-in-session`, `HAL_RUNNER_BACKEND_JUDGE=anthropic-api` and no `HAL_RUNNER_BACKEND`, the identity records `agent-sdk / default` although no call ran on agent-sdk. The accurate per-call record, `runner_backend_resolved` (carries the role since #100), has to be re-assembled by anyone who reads the log to learn which adapter produced a run. The #100 spec lists this as a known limit.

Consumers of `run_identity.adapter_identity` in the tree (audited): only `test_bd18_emissions.py::test_ac_e2b_...` reads it, by the keys `backend` and `source`, never by whole-mapping equality. `test_bd8_l1_oracle.py` (ac7) counts `run_identity` events and their order. The attestation identity in `conformance/harness.py` is a different thing (taken from the host adapter) and is not touched. No conformance oracle code reads the inner mapping, so no closed schema needs an edit.

## Design (ADDITIVE, READ-ONLY)

`_resolve_adapter_identity` returns the mapping it returns today, with one more key appended last:

```
{"backend": B, "source": S,                       # exactly as today
 "roles": {"judge":  {"backend": ..., "source": ...},
           "worker": {"backend": ..., "source": ...}}}
```

1. **Top level unchanged.** `backend` and `source` come from `_resolve_backend(None, env)` with no role, as today; they are the first two keys, in that order, and `roles` is appended after them. AC-E2b stays in force as written.
2. **One entry per routed role**, for each role in `llm_subprocess._ROLES`, read **at call time** from the module attribute (single source, §1g: a role added to the router appears in the identity with no edit here, and the identity cannot name a role the router refuses). Order is the order of `_ROLES`.
3. **Same seam as the calls.** Each entry is `_resolve_backend(None, env, role=role)`, where `env` is the one mapping returned by `config_provider.env_mapping()`, read once per call of the function (one snapshot for the top level and every role). `kwarg` is `None`: the engine passes no `backend=` kwarg at any call site, so `source` for a role entry is one of `"env-role"`, `"env"`, `"default"`. `"kwarg"` cannot occur here.
4. **The identity records the resolution, not the validity.** A role variable naming an unregistered backend is recorded as named; the failure belongs to the call (`E_LLM_BACKEND_UNKNOWN`). The `default-fallback` rebind in `invoke_llm_subprocess` (an unregistered default `agent-sdk` becomes `claude-subprocess`) is a call-site rule, not part of `_resolve_backend`; the top-level pair has never applied it (EMISSIONS_SPEC AC-E2b) and neither do the role entries. Known limit, unchanged.
5. **Degrade, do not fail.** The resolution of each role is guarded on its own. If it raises, that role is omitted from `roles`, one `logger.warning` names the role and the exception type, and the loop goes on. `roles` is always present as a mapping (empty when every role failed). The top-level resolution is not guarded: it is exactly today's code path and its behaviour is not changed by this lot.
6. **No side effect.** The function emits no event, calls no backend and does not touch the environment. Resolving the identity never produces a `runner_backend_resolved` event; only a call does.
7. The return annotation becomes `dict[str, Any]`; the docstring says what `roles` is.

### Kill switch
None added (issue). The change adds a key to one event payload and never influences which backend runs. Revert = revert one function.

### Out of scope (one PR, one problem)
`llm_subprocess._resolve_backend` and the routing itself, the `runner_backend_resolved` event, `conformance/harness.py` (attestation identity), the top-level pair, new roles or backends, the top-level resolution's failure behaviour, the `default-fallback` rebind, `ORACLE_SPEC.md` (its text names the payload `{engine_version, adapter_identity}`, which is unchanged at the payload level).

## EMISSIONS_SPEC change

A new **AC-E2e** directly after the existing AC-E2d (AC-E2d is taken: it is the per-`execute()` quantification, so the new entry is E2e and renumbers nothing): `adapter_identity` MAY carry a `roles` key (mapping role name to `{backend, source}`), emitted by this engine for every role in `llm_subprocess._ROLES`; the source set for a role entry is `{"env-role", "env", "default"}`; the top-level pair is AC-E2b's and does not change. AC-E2b gets one sentence pointing at AC-E2e. No other section is touched.

## Acceptance criteria

Every AC runs the real `WorkflowEngine.execute()` over a one-step workflow with a real `EventLog` and reads `run_identity` back from the log file, unless it says otherwise. Nothing patches `_resolve_adapter_identity`. The environment is set with `monkeypatch.setenv` / `delenv` on the real variables (the engine reads them live through `config_provider.env_mapping()`). The autouse conftest pin `HAL_RUNNER_BACKEND=claude-subprocess` is overridden per test.

- **AC1 (reference case)** `HAL_RUNNER_BACKEND_WORKER=claude-in-session`, `HAL_RUNNER_BACKEND_JUDGE=anthropic-api`, `HAL_RUNNER_BACKEND` unset: `adapter_identity` equals, as a whole mapping, `{"backend": "agent-sdk", "source": "default", "roles": {"judge": {"backend": "anthropic-api", "source": "env-role"}, "worker": {"backend": "claude-in-session", "source": "env-role"}}}`.
- **AC2** Only `HAL_RUNNER_BACKEND=anthropic-api`: top level `{anthropic-api, env}`; both role entries `{"backend": "anthropic-api", "source": "env"}`.
- **AC3** Nothing set: top level `{agent-sdk, default}`; both role entries repeat it, `{agent-sdk, default}`.
- **AC4 (mixed)** (a) `HAL_RUNNER_BACKEND=claude-subprocess` plus `HAL_RUNNER_BACKEND_JUDGE=anthropic-api`: judge `{anthropic-api, env-role}`, worker `{claude-subprocess, env}`, top level `{claude-subprocess, env}`. (b) Only `HAL_RUNNER_BACKEND_WORKER=claude-in-session`: worker `{claude-in-session, env-role}`, judge `{agent-sdk, default}`, top level `{agent-sdk, default}`.
- **AC5 (blank and padded)** A whitespace-only role variable falls through to the global (`{<global>, env}`); a role variable padded with spaces is recorded stripped, source `env-role`.
- **AC6 (top level byte-identical, additive only)** Across the env cases of AC1-AC4: the first two keys of the parsed `adapter_identity` are `backend` then `source`, the key list is exactly `["backend", "source", "roles"]`, and the pair equals `llm_subprocess._resolve_backend(None, <same env>)` (no role). The `run_identity` payload keys stay exactly `{engine_version, adapter_identity}`. The existing AC-E2b test is not amended and passes.
- **AC7 (same seam as the calls, cross-event)** With real `invoke_llm_subprocess(role="judge")` and `(role="worker")` calls dispatched to two registered spy backends under a run context with an event log: each call's `runner_backend_resolved` payload `{backend, source}` equals the same role's entry in the `run_identity` of an `execute()` under the same environment. Run for the per-role case (source `env-role`) and the global-only case (source `env`).
- **AC8 (degrade)** `llm_subprocess._resolve_backend` replaced by a wrapper that delegates to the real function and raises for `role == "judge"` only: `execute()` still finishes ok, exactly one `run_identity` is emitted, the top-level pair is as without the wrapper, `roles` has the worker entry and no judge entry, and one warning naming `judge` is logged. With a wrapper that raises for every role: `execute()` finishes ok, `roles == {}`, top-level pair intact.
- **AC9 (both billing modes)** The reference case under an API-key environment (`ANTHROPIC_API_KEY` set, no OAuth token), a subscription environment (`CLAUDE_CODE_OAUTH_TOKEN` set, no API key) and one with neither: identical `adapter_identity`.
- **AC10 (single source of roles)** With `llm_subprocess._ROLES` replaced by `("judge", "worker", "auditor")` and `HAL_RUNNER_BACKEND_AUDITOR=claude-in-session`: `roles` has three entries in that order and `auditor` is `{claude-in-session, env-role}`. With `_ROLES = ("worker",)`: `roles` has only `worker`. Kills a hard-coded role list.
- **AC11 (read-only)** An `execute()` with per-role variables set emits no `runner_backend_resolved` event, and the event-type sequence of the run is the same as with no per-role variable set. The environment mapping is equal before and after.
- **AC12 (live per `execute()`)** Two `execute()` calls on one engine, with the environment changed between them: each `run_identity` reflects the environment at its own call. Kills a value cached at import or at engine construction.
- **AC13 (spec text)** `EMISSIONS_SPEC.md` has an `AC-E2e` entry (after the untouched AC-E2d) that names `roles`, `judge`, `worker`, `env-role`, and the source set `env-role`, `env`, `default`; the `AC-E2b` entry points at `AC-E2e`.
- **AC14** Siblings pass unchanged (`sibling_tests`); no existing test is amended.

## Files

In scope: `engine_py/bytedigger_engine/engine.py` (`_resolve_adapter_identity` only), `engine_py/bytedigger_engine/conformance/EMISSIONS_SPEC.md`, new `engine_py/tests/test_bd109_adapter_identity_roles.py`, `CHANGELOG.md` (every behaviour lot adds its line), this spec and its gate files.
Not in scope: every other file, in particular `llm_subprocess.py`, `conformance/harness.py`, `ORACLE_SPEC.md`, `flags_catalog.py` (no flag), `error_codes.py`.
Sibling-test audit (§1a) done before this spec: `grep -rIln "adapter_identity\|run_identity\|_resolve_adapter_identity\|env-role" engine_py/tests` gives the candidate set. `test_bd18_emissions.py` AC-E2b reads `backend`/`source` by key (kept); `test_bd82_role_backend_effort.py::test_r5` pins that `_resolve_backend(None, env)` without a role is unchanged (this lot does not touch it); `test_bd8_l1_oracle.py` ac7, `test_engine.py` and `test_event_log_replay_e2e.py` pin the event order and presence of `run_identity`, not its inner mapping. The `test_bd195/197/267/269/271/273` and `test_bd58` files use `adapter_identity` of the attestation harness (a different object) and are unaffected. None asserts that `adapter_identity` has exactly two keys.

## Provenance (Guy 2026-10-11)

introduced: #100 added per-role routing and recorded the gap as a known limit; nothing is removed or simplified. The function keeps its seam; it gains the per-role view of the same seam.

## LLM stages in this step

None.
