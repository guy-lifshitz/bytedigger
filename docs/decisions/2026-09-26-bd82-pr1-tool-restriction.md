# bd#82 PR1 — the tool allowlist is enforced on every backend, and the gate floor is checked before dispatch

Issue: #82 (P1, part 1 of 4). Class: SYSTEMATIC. Chokepoint: `llm_subprocess.invoke_llm_subprocess`.

## Problem (verified against the code)

- The default backend `agent-sdk` (`llm_subprocess.py:82`) builds `ClaudeAgentOptions` with
  `allowed_tools=...` and `permission_mode="bypassPermissions"` and never sets `tools=`
  (`lib/reference_backends/agent_sdk.py:422-437`). In the SDK, `allowed_tools` is the
  auto-approve list and `tools` is the availability list, so a judge declared `["Read"]`
  can still Write, Edit and run Bash. A `Bash(graphify-shim.sh:*)` entry under bypass
  approves any Bash command.
- `pydantic-anthropic` and `pydantic-openai` always register `write_file`, `edit_file`,
  `run_tests` and `bash`, and ignore `allowed_tools`.
- `claude-subprocess` passes `--allowed-tools` only; the available set is never narrowed.
- `_assert_hard_gate_opus` runs only inside the claude-subprocess handler
  (`llm_subprocess.py:1667`). On agent-sdk and the pydantic backends a hard gate on a
  below-floor model is dispatched.

## Contract

1. **Tool policy.** For `allowed_tools is not None`, the *available* set is the entries'
   base names (`"Bash(x:*)"` → `"Bash"`, order kept, duplicates dropped) and the *approved*
   set is the entries verbatim. `allowed_tools is None` keeps today's unrestricted behavior
   on every backend.
2. **claude-subprocess** appends `--tools <available joined by ",">` next to the existing
   `--allowed-tools` (`[]` → `--tools ""`, which disables every tool).
3. **agent-sdk** passes `tools=<available>`, `allowed_tools=<approved>` and
   `permission_mode="dontAsk"` (deny anything not pre-approved, no prompt). The old-SDK
   `TypeError` fallback keeps `tools` and `dontAsk`; it never falls back to
   `bypassPermissions` while `allowed_tools is not None`. It declares `tool_allowlist`.
4. **pydantic-\*** register only what the policy names: `Write` → `write_file`,
   `Edit` → `edit_file`, plain `Bash` → `bash` and `run_tests`. A patterned `Bash(...)`
   entry does not enable `bash`, and patterned `Write(...)`/`Edit(...)` entries do not enable
   `write_file`/`edit_file` (a pattern cannot be honored, so it fails closed). `[]` registers
   nothing. Both declare
   `tool_allowlist`.
5. **anthropic-api** is text-only. It declares the new capability `no_tools`, and the
   attestation reports enforcement `"no-tools"` (bd#10: it still must not claim
   `"runtime-allowlist"`).
6. **Refusal before dispatch.** `hard_gate=True`, `allowed_tools is not None`, and the
   resolved backend declares neither `tool_allowlist` nor `no_tools` →
   `E_TOOL_RESTRICTION_UNSUPPORTED` (recoverable=False) and a `tool_restriction_refused` event;
   the backend is not called.
   Non-gate calls on such a backend are dispatched and emit `tool_restriction_not_enforced`
   (never emitted when the backend enforces, or when `allowed_tools is None`).
   Capabilities are read from the registry, never keyed on a backend name: a stub
   registered over `claude-subprocess` without `tool_allowlist` is refused like any other.
   **claude-in-session** is serviced outside this repository, so it declares neither by
   default and its hard gates with a tool list are refused. The servicer opts in by setting
   `HAL_IN_SESSION_ENFORCES_TOOLS=1`, a declaration that it enforces the request's
   `allowed_tools`; the capability set of `claude-in-session` then includes
   `tool_allowlist`, and the attestation reports `"runtime-allowlist"`. Any other value is off.
7. **Gate floor before dispatch.** `hard_gate=True` → the floor check
   (`_assert_hard_gate_opus` on the argv the model resolves to) runs in the chokepoint for
   every backend, before the tool check and before dispatch. Same code
   `E_HARD_GATE_MODEL_DOWNGRADE`, same `hard_gate_refused` event. This includes
   claude-in-session: a below-floor in-session gate is refused before a request file is
   written (previously its post-check returned `E_MODEL_PIN_MISMATCH` after the round trip).
   A floor refusal is now returned before dispatch, so no `model_invocation_attested`
   event is written for it (it never was for in-session or reference backends).
8. **Registries.** `E_TOOL_RESTRICTION_UNSUPPORTED` is registered in `error_codes.py` and
   both `ERROR_CODES.md` copies; `HAL_IN_SESSION_ENFORCES_TOOLS` is catalogued in
   `flags_catalog.py` and reset by the test conftest; `"no-tools"` joins the closed enforcement set in
   `conformance/AUTHORSHIP_SPEC.md` (AC-C2).
9. **CI.** The pytest job installs `claude-agent-sdk==0.2.120` so the real-SDK ACs run there;
   they import it unconditionally (no skip), so a missing SDK fails the job.

## Acceptance

Test file `engine_py/tests/test_bd82_tool_restriction.py`. Real side effects: the real
installed `claude_agent_sdk` turns the backend's options into CLI argv; the pydantic
backend is run and a file write through its registered tools is attempted on disk; events
land in a real `EventLog`. Fake backends are used only to observe what the chokepoint
dispatches.

## Clause → AC

| Clause | ACs |
|---|---|
| 1 policy | AC2, AC3, AC6b, AC6d, AC9 (`[]`), AC20 |
| 2 subprocess | AC6, AC6b, AC6c |
| 3 agent-sdk | AC1, AC2, AC3, AC4, AC5, AC20 |
| 4 pydantic | AC7, AC8, AC9 (both flavors), AC9b, AC9c, AC10 |
| 5 anthropic-api | AC11 |
| 6 refusal | AC12, AC12b, AC13, AC13b, AC13c, AC14, AC14b, AC15, AC16 |
| 7 floor | AC17, AC18, AC19, AC22, AC23 |
| 8 registry | AC24 |

## Test migration (existing pins a correct GREEN changes)

- `test_2A6986ED_anthropic_api_backend.py:144` — capabilities become `frozenset({"no_tools"})`.
- `test_bd29_in_session_pin_fail_closed.py` AC6 — its sonnet-pinned gate is now refused
  pre-dispatch, so the drift it measures moves to an opus pin (same measurement,
  `E_MODEL_PIN_MISMATCH` still overwrites the guard's code — the pre-existing defect it pins);
  the new AC6b pins the pre-dispatch refusal of a sonnet in-session gate with no request written.
- `test_phase_5_integrity.py:715-732` and `test_phase_6_fix_integrity.py` (`_STUB_MODEL`) — their
  stub gates run on below-floor or unknown model names; the floor now refuses them, so they use
  `opus`.
- Stub backends that receive hard gates with a tool list (`test_phase_45_spec.py`
  `_register_stub`, `test_pipeline_recovery.py`, `test_phase_6_fix_integrity.py`,
  `test_phase_5_integrity.py`) declare `tool_allowlist`, added to any capability set they
  already declare — a stub stands in for an enforcing backend. No name-keyed exemption in the
  engine.

## Out of scope (later PRs of #82)

Fresh sessions and thread context (PR2), semantic_verifier through the chokepoint (PR3),
backend per role and effort as a capability (PR4).
