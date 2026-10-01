# bd#141 item 4(e): the subprocess backend reports the model it invoked (R3.3 producer)

**Status:** r3 (gate r2 APPROVED; r3 applies its MINOR advisories F-r2-2/F-r2-4 as two extra AC3b tests and a dead-constant removal. r1 REJECTED: 2 MAJOR + 6 MINOR. See `2026-10-02-bd141-p4e-gate-r{1,2}.md`) · **Tier:** 2 (one engine prod `.py`, one conformance registry line, Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._pin_mismatch_refusal` (already the single R3.3
check for every backend, called from `_dispatch_backend`). This lot adds no new check. It adds the
missing **producer** on the claude-subprocess (`claude -p` stream-json) path, so the existing chokepoint stops being inert there.
**Side of the seam (decision 2026-07-26 §7.4, "model-identity verification ... belongs on the LLM
seam"):** engine. The host contract (R3.3: "the adapter MUST report") is satisfied by the engine's own
`claude -p` adapter. HAL has no host-side implementation of this control, so there is nothing to thin
out on the HAL side and no host-controls registry entry is needed.
**Source:** bd#141 item 4(e); bd#10 (chokepoint), bd#68 (producer registry), bd#71 (agent-sdk producer), bd#29 (in-session flip, closed).

## §1 Problem (measured on `1fcdba0`)

1. `_pin_mismatch_refusal` (`llm_subprocess.py:1227`) returns `None` when `data["observed_model"]` is
   absent. The verdict is `not-checked`, not an error.
2. `_invoke_subprocess`, the `claude -p` stream-json path, writes `observed_tools` (`:2428`) but never
   writes `observed_model`. Producers of `observed_model` on `1fcdba0`: `_invoke_in_session` (`:980`) and
   `reference_backends/agent_sdk.py:684`. `grep '"observed_model"'` inside `_invoke_subprocess` gives 0 hits.
3. As a result, model-identity verification (R3.3 / ADV-7) does nothing on the headless subprocess path.
   A `--model opus` dispatch answered by a sonnet (for example, a CLI-side fallback) passes silently.
4. `bd_l3.AWAITING_PRODUCER = ("R3.3",)` (`conformance/bd_l3.py:63`) declares this gap.
   `test_bd68_l3_observation_producers.py::test_ac6` pins `_producers("observed_model") == 1`.
5. **Live baseline (gate r1 F1, measured 2026-10-02 with CLI on this host).** Command:
   `claude -p "Reply with the single word OK." --model <alias> --output-format stream-json --verbose --max-turns 1`.

   | `--model` | root `system/init` `model` | root `assistant` `message.model` (all of them) | `parent_tool_use_id` | `_claude_model_family` (dispatch / observed) |
   |---|---|---|---|---|
   | `haiku` | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` ×2 | key present, `null` | haiku / haiku |
   | `sonnet` | `claude-sonnet-5-5` | `claude-sonnet-5-5` | key present, `null` | sonnet / sonnet |
   | `opus` | `claude-opus-5-5` | `claude-opus-5-5` | key present, `null` | opus / opus |
   | `fable` | `claude-fable-5-1` | `claude-fable-5-1` | key present, `null` | fable / fable |

   In every case the init and assistant models are the same string, and the observed family equals the dispatched family. `fable`
   does not report an opus id, so `decorrelated_verifier` runs are not affected. Aliases that have no family (`opusplan`, `default`)
   resolve to `None` on the dispatch side, so the chokepoint returns not-checked (`:1246`), which is unchanged. `<synthetic>` was not
   observed in these runs. Skipping it is a defensive rule taken from the CLI's known placeholder for API-error messages, and it is
   pinned by AC2.

## §2 Design

### §2.1 Extractor `_observed_model_from_events(events) -> str | None`
A new private function in `llm_subprocess.py`, placed beside `_observed_tools_from_events`. It is
defensive in the same way: it tolerates any missing key and never raises.

- It considers **root events only** (`_is_root_stream_event`). Depth-1 subagent events are excluded on
  purpose: a subagent may legitimately run on another model, and R3.3 concerns the model the engine
  dispatched.
- **Primary source:** `message.model` of the **last** root event with `type == "assistant"`, where the
  value is a non-empty `str` that does not start with `<`. The CLI's `"<synthetic>"` error/placeholder
  messages are skipped and never override a real value. This is the model that actually answered.
- **Fallback:** the `model` of the **first** root `{"type": "system", "subtype": "init"}` event that
  yields a valid value, under the same value rules. This is what the harness selected.
- If neither source yields a value, the result is `None`. Absence is the existing first-class
  `not-checked` state (`_observed`, `[bd10:5]`). The function never substitutes the requested model.
- It returns the raw reported string (for example `"claude-sonnet-5-5"`), not a family. Family comparison
  stays in the chokepoint.

### §2.2 Producer line
In `_invoke_subprocess`'s success branch, directly after the `observed_tools` line (`:2428`), add
`data["observed_model"] = _observed_model_from_events(events or [])`. It is AFTER the `extra_data` merge,
so the name is reserved and a caller cannot shadow it. This is the same discipline as its siblings. It is
absent on every error branch, unchanged.

### §2.3 Registry drain
- `bd_l3.AWAITING_PRODUCER` becomes `()`. Its comment is updated: `observed_model` is now written by
  `_invoke_subprocess`, `_invoke_in_session` and `agent_sdk`. The four `SILENT_BACKENDS` stay declared
  silent (bd#71), because they write neither field.
- `test_bd68::test_ac5`'s stale check (`_producers >= 2` while still listed) forces this drain. It is the
  existing gate and is not modified.

## §3 Acceptance criteria

- **AC1** `_observed_model_from_events` returns the last root assistant `message.model`, given a
  transcript with init `model=A` and root assistant messages `B` then `C`: the result is `C`.
- **AC2** `"<synthetic>"` and other `<`-prefixed values, empty strings and non-str values are skipped.
  With root assistant `[C, "<synthetic>"]` the result is `C`. With only `"<synthetic>"` plus init `A`, the
  result is `A`.
- **AC3** Subagent events are excluded, **including depth-1 events that `_manifest_eligible_events` keeps**. The
  fixtures contain a root assistant event with an `Agent` `tool_use` block of id `toolu_agent1`, and the subagent event's
  `parent_tool_use_id` is `toolu_agent1`, so it is depth-1 eligible (gate r1 F2). A transcript whose only non-root
  `message.model` is on that subagent event, plus init `A`, gives `A` (the spawning root assistant event carries no
  model, or a `<`-prefixed one). With no init event it gives `None`.
- **AC3b** If there are several root init events, the first valid one wins: inits `A` then `B`, with no assistant model, gives `A`.
- **AC4** Defensive behaviour. `[]`, non-dict events, `message` missing or non-dict, and init without
  `model` all return `None` without raising.
- **AC5 (production side effect, §1l)** Run `invoke_llm_subprocess` end to end on the claude-subprocess
  backend with a fake `claude` that emits a stream-json transcript whose root assistant model is
  `claude-sonnet-*`, dispatched with `model="opus"`. The returned `StepResult` has
  `error_code == "E_MODEL_PIN_MISMATCH"` and `status == "error"`. The run's event log has a
  `model_pin_mismatch` event with `chokepoint: True` and `observed_model` equal to the reported string. The
  UUT (`_invoke_subprocess`, `_dispatch_backend`, `_pin_mismatch_refusal`) is NOT mocked. Only the
  external binary is faked.
- **AC6 (no false positive)** Same as AC5 but the transcript reports `claude-opus-*`. The `StepResult` is
  not an error, `data["observed_model"]` equals the reported string, and the `model_invocation_attested`
  event carries the same `observed_model`.
- **AC7 (absence stays not-checked)** Same as AC5 but the transcript has no model anywhere. The result is
  not an error, and `data["observed_model"] is None`. This keeps today's behaviour for streams with no model.
- **AC8 (reserved name)** `extra_data={"observed_model": "haiku"}` on an AC6 run does not shadow the
  observed value.
- **AC9 (registry)** `"R3.3" not in bd_l3.AWAITING_PRODUCER` and `_producers("observed_model") == 2`.
  The existing `test_bd68::test_ac6` is **inverted** in the RED commit (pre-freeze, declared here), and
  `test_ac5` is untouched and must pass.

## §4 Negative-test teeth (which code change turns each AC red)
AC1/AC2 go red if the extractor returns the first value instead of the last, or does not skip `<`.
AC3 goes red if the extractor walks `_manifest_eligible_events` (depth 1) instead of root (the fixtures make the
subagent event depth-1 eligible). AC3b goes red on last-init-wins. AC4 goes red if `ev.get` runs before the
`isinstance(ev, dict)` guard. AC6 goes red if the §2.2 line is removed, or if it writes the requested model.
AC5 goes red if the §2.2 line is removed. AC7 goes red if the extractor falls back to the requested
model. AC8 goes red if the line moves before the `extra_data` merge. AC9 goes red if the registry is not
drained.

## §5 Scope
- `engine_py/bytedigger_engine/llm_subprocess.py` (extractor + one producer line)
- `engine_py/bytedigger_engine/conformance/bd_l3.py` (`AWAITING_PRODUCER` + comment)
- `engine_py/tests/test_bd141_p4e_subprocess_observed_model.py` (new RED file)
- `engine_py/tests/test_bd68_l3_observation_producers.py` (`test_ac6` inverted, pre-freeze)
- `CHANGELOG.md`

§1a sibling audit: `test_bd68_*`, `test_bd71_*`, `test_bd10_l3_authorship.py`, `test_bd28_bd_l3_checker.py`,
`test_bd29_in_session_pin_fail_closed.py`, `test_02FF48F4_model_pin_insession.py`, `test_2FDA949D_*`, and
every test that drives `_invoke_subprocess` with a fake transcript carrying an assistant `message.model`
that differs in family from the dispatched model (it would now go red with `E_MODEL_PIN_MISMATCH`). The RED
author greps for these and lists them in the RED report.

## §6 Not in scope
- The legacy caller-supplied `--output-format json` path (`_communicate_legacy`). It has no event list, so it stays `not-checked`.
- The four `SILENT_BACKENDS` (`anthropic_api`, `anthropic_oauth`, `pydantic_*`). They write neither field. That is a separate lot (bd#71 family).
- Item 4(d): injection declarations in phases other than `phase_2_explore`. That is a separate PR.
- Any change to `_pin_mismatch_refusal`, `_claude_model_family`, or the in-session path.
- HAL-side changes. There is no host implementation to remove.
- The straggler synthetic-ok branch (`:2032-2053`). It returns `ok` without the reserved-name override, so `extra_data` could
  shadow `observed_model` there. The sibling fields have the same gap, so this lot does not introduce it. It is a separate fix for the whole
  sibling group (gate r1 F5). The in-session path has the same override (gate r2 F-r2-3). Both are tracked in bd#145.
- Moving the stream-json extractors onto the provider (`lib/llm_provider.py`, next to `_claude_parse_result`). The extractor sits next
  to its sibling `_observed_tools_from_events`. Moving them is a separate refactor for the whole group (gate r1 F8).

## §7 Behaviour change and risk
This is the one intended change: a headless run whose answering model differs **in family** from the
dispatched one now fails with `E_MODEL_PIN_MISMATCH` (non-recoverable), instead of passing. A
fallback-model swap mid-run that ends on the pinned family is not detected, because the last root
assistant model wins. That is accepted and documented. Same-family version drift (opus 5 vs 5.5) is not
detected, which is unchanged chokepoint semantics.
