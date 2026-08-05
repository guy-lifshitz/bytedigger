# bd#71 — the default backend starts producing observations

**Class:** an observation discarded along the way. Not "there is no producer" (bd#68) and not
"the emitter is one-directional" (bd#61), but a third form: the evidence **passes through the code and is
thrown away**. The SDK stream-reading loop sees both the tool names and the model — and
keeps only `ResultMessage`.

**Chokepoint:** `lib/reference_backends/agent_sdk.py`, the message-reading loop
(`:405-427`) and the `base_data` assembly point (`:620-631`).

---

## §1. §1b live base — both corpora, `01ff2d4`

| corpus | result |
|---|---|
| **pytest** | **5465 passed / 47 skipped / 1 xfailed / 0 failed**, 349 s |
| **clean-room suite** | **5378 passed / 69 skipped / 1 xfailed / 0 failed**, 213 s, `PASS` |

## §2. Exposure measurement

| fact | value |
|---|---|
| `_DEFAULT_BACKEND` | **`agent-sdk`** — production's default path |
| calls `_invoke_subprocess` | **no**, its own implementation |
| writes `observed_tools` / `observed_model` | **no / no** |
| the other five reference backends | also no |

⇒ After bd#70, R3.5/R3.6 are observable only for `claude-subprocess`, and R3.3 only for
`claude-in-session`. **On the default path all three are silent.**

## §3. WORK REMOVED — declared, not narrowed silently

Checked whether the tool names are present in the SDK result. **They are in the stream, but not in the
retained result:** `AssistantMessage` carries `content` and **`model`**;
`ToolUseBlock` carries `id`, **`name`**; the loop at `:407-427` iterates over all messages and
keeps only `ResultMessage`.

**Removed:** new host instrumentation · a new data source · a change to the SDK API ·
a separate pass over the logs.
**What remains:** an accumulator inside the already existing loop.

**The `observed_model` tail is closed as a side effect:** `AssistantMessage.model` travels in the same
stream, so both observations are done by one lot. This is a **widening** of scope against
my plan, and it is declared.

## §4. Decisions

**D1 — the accumulator sits next to `result_holder`.** The same technique already used to carry a result
out of a nested coroutine (`:344`, reset at `:378`). It is reset on every
retry attempt together with it — otherwise the previous attempt's observations would leak into
the next.

**D2 — both fields go into `base_data`**, next to `worker_written_paths`, and **only on the success
path**, as with their neighbours.

**D3 — `observed_model` is taken from `AssistantMessage.model`, not from what was requested.**
Recording what was requested would mean restoring self-assessment: the pin would be compared against itself and
R3.3 could never fail.

**D4 — the gate against recurrence covers ALL registered backends**, not only the one
touched. The absence of such a gate produced three inert lots in a row; the form
"fixed where we happened to look" must not be repeated.

**D5 — the other four reference backends are not touched**, but they are **declared** in the registry
of the awaiting. They have their own streams and their own exposure.

## §5. Scope

**Edited:** `lib/reference_backends/agent_sdk.py` (the accumulator + two fields);
`conformance/bd_l3.py` (the per-backend registry of the awaiting).
**New:** `tests/test_bd71_agent_sdk_observations.py`.
**§1v — NOT in scope:** the other reference backends (D5); `_invoke_subprocess` (bd#68);
`_written_paths_from_events`; `bd_l2`; `harness`.

## §6. §1a Sibling audit

`test_gh1157_agent_sdk_retry.py` (**direct risk** — the fake SDK and the retry loop),
`test_gh1169_agent_sdk_hang_recovery.py`, `test_GH901_agent_sdk_cost_rollup.py`,
`test_gh933_agent_sdk_stderr_outage.py`, `test_bd68_l3_observation_producers.py`,
`test_bd28_bd_l3_checker.py`, `test_bd63_r35_enforcement_falsifiable.py`. By a run.

## §7. Acceptance criteria

- **AC1.** A stream with `ToolUseBlock(name="Read")` and `ToolUseBlock(name="Bash")` ⇒
  `data["observed_tools"] == ["Bash", "Read"]`.
- **AC2 (NEGATIVE).** A stream with no tool blocks ⇒ `[]`. An accumulator that always writes
  the same thing is as inert as its absence.
- **AC3.** `data["observed_model"]` is taken from `AssistantMessage.model`.
- **AC4 (NEGATIVE, D3).** The model in the stream **differs** from the requested one ⇒
  `data` receives the observed one, not the requested one. Otherwise R3.3 can never fail.
- **AC5 (the end-to-end link).** An escape visible only in the SDK stream reaches the R3.6
  verdict; model drift reaches R3.3.
- **AC6 (the retry does not leak).** The first attempt's observations are not visible in the second.
- **AC7 (THE GATE, all backends).** Every registered backend either writes both fields
  or is listed in the registry of the awaiting. Both sides: a stale entry fails it too.
- **AC8 (surface = `__all__`).**

## §8. What the PR does NOT claim

- It does not make the other four reference backends observable (D5) — they are declared.
- It does not claim the stream is complete: it records what the SDK sent.
- It does not touch the decisions about retries, timeouts and salvage.
