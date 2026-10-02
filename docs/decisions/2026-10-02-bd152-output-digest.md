# bd#152: a per-invocation output digest in `model_invocation_attested`

**Status:** r2, APPROVED by gate r2 (`2026-10-02-bd152-gate-r2.md`; advisory MINOR-1/2 folded into RED). r1: gate r1 REJECTED: 1 MAJOR + 5 MINOR + 3 NIT, `2026-10-02-bd152-gate-r1.md`; all folded, §8) · **Tier:** 2 (one chokepoint function, one spec section, two sibling test amendments; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._dispatch_backend` → `_emit_attestation` / `_attest_payload` stays the
only producer of `model_invocation_attested` (AUTHORSHIP_SPEC §2.1). This lot adds two keys to that one payload and one
engine-owned field to the `StepResult` that same function returns. It adds no second emitter.
**Side of the seam:** engine. **Source:** bd#152; AUTHORSHIP_SPEC §4 class M, re-open criterion "the log records an output
digest per invocation (#152)".

## §1 Problem (measured on `44c8994`)

1. `_attest_payload` (`llm_subprocess.py:1284`) records nine keys: `step_name, backend, model_requested, prompt_sha256,
   injections, declared_capabilities, capability_enforcement, observed_model, observed_tools`. None of them says what the
   model returned, and nothing distinguishes two dispatches of the same `step_name` (GH1169 fallback, retries, review
   cycles) other than the event's timestamp. The in-session backend has a per-call `request_nonce` (`:799`), but it is
   backend-local, absent on every other backend and not in the attestation; it is left unlinked (§5).
2. Every backend returns the model's answer as `result.data["raw_response"]` (str): `claude-subprocess` `:2610`,
   `claude-in-session` `:999`, `anthropic-api` `reference_backends/anthropic_api.py:272`, `agent-sdk` `agent_sdk.py:694`,
   `pydantic-anthropic` `pydantic_anthropic.py:254`, `pydantic-openai` `pydantic_openai.py:770`. No shared helper reads it.
3. `_RunCtx` (`telemetry_ctx.py:20`) has no per-dispatch counter; `event_log.append` assigns no sequence id.
4. Consequence (AUTHORSHIP_SPEC §4): a class-M block cannot be declared, because `source_id =
   "invocation:<step_name>:<invocation id>"` has no invocation id to name and no logged digest to match.

## §2 Decision

1. **`invocation_id`.** `_dispatch_backend` generates one id per dispatch that reaches the backend call: `uuid.uuid4().hex`
   (32 lowercase hex). A random id, not a counter: a counter keyed by `run_id` would restart on resume in a new process and
   collide inside one run's log; nothing in the engine needs ordering from it (the log has `ts`).
2. **`output_sha256`.** `hash_text(result.data["raw_response"])` when `result.data` is a dict and that value is a `str`
   (the empty string included: an empty answer is an observation). Otherwise `null`. Same form as `prompt_sha256`
   (`"sha256:" + 64 hex`) and same function (`attest.hash_text`). Computed from the backend's `result`, after the backend
   returns, before the pin and escape checks, so a refused invocation still records what it returned (`[bd10:24]`).
3. **Payload.** AC-P1's key set becomes eleven: the nine plus `invocation_id`, `output_sha256`. Exact key-set equality is
   kept.
4. **Exposure to callers.** When the attestation event is emitted, `_dispatch_backend` returns a result whose `data`
   (when a dict) carries `data["invocation_id"]` equal to the event's `invocation_id`. The chokepoint is the **only writer**
   of that key: a value a backend (or a caller's `extra_data` merged by a backend) put there is overwritten when an event is
   emitted and removed when none is (no run context, or a run context with `event_log is None`). Invariant: `data["invocation_id"]` present ⇒ an attestation with that
   id was handed to `_emit_safe` for this dispatch. The returned `data` is a new dict; the backend's dict is not mutated **on every path** (in run, forged, no run, no event
   log). `data` that is not a dict (e.g. `None`) is returned as is. A refusal before dispatch (effort/floor, unattributed
   injection) emits nothing and carries no id.
   Pin and escape refusals build their own `StepResult`; they carry `data["invocation_id"]` too, since the event exists.
5. **Not a reserved observation field.** `RESERVED_OBSERVATION_FIELDS` names fields only a *backend* may set. `invocation_id`
   is set by the chokepoint over whatever the backend returns, so it does not join that set (bd#145/bd#167 key lists are
   unchanged).
6. **AUTHORSHIP_SPEC §3/§4 text.** §3 AC-P1 lists eleven keys and defines the two new ones. §4 class M: the re-open
   criterion is met for *recording*. Declaring class-M blocks stays deferred to a follow-up issue (§6), with the matching
   rule stated now: a class-M block's `source_id` is `"invocation:<step_name>:<invocation_id>"` naming an earlier
   attestation in the same run; its `sha256` covers the content as inlined (unchanged chunk rule); it equals that
   attestation's `output_sha256` only when the whole `raw_response` is inlined verbatim. For re-rendered findings it does
   not, and the consumer's check is "the named invocation was attested earlier in this run", not digest equality.
   The R3.2 row of §2.2's `REQUIREMENT_LABELS` table (`AUTHORSHIP_SPEC.md:371`) is updated the same way: "class-M output
   digests are recorded (#152); class-M block declarations are a follow-up (bd#206)". The label value is unchanged.

## §3 Acceptance criteria

Fixture for all: a test backend registered via `register_backend` that returns `StepResult(status="ok",
data={"raw_response": R, ...})`, dispatched through `_dispatch_backend` (or `invoke_llm_subprocess`) under a real
`telemetry_ctx` run with a **real `EventLog` on `tmp_path`**; assertions read events back from the log file (§1l side effect).

- **AC1** Exact key set: one dispatch → one event whose payload keys equal the eleven (amends bd#10 AC-P1).
- **AC2** `output_sha256 == "sha256:" + hashlib.sha256(R.encode("utf-8")).hexdigest()` computed in the test (not via
  `attest.hash_text`), for R ∈ {ASCII, Cyrillic + emoji, `""`}.
- **AC3** `output_sha256 is None` when `raw_response` is absent, is not a `str` (e.g. a dict, `None`), or `result.data` is
  not a dict. The event is still emitted with all eleven keys.
- **AC4** `invocation_id` is a 32-char lowercase hex string; two dispatches in one run (same `step_name`) yield two events
  with distinct ids.
- **AC5** The returned `result.data["invocation_id"]` equals the event's `invocation_id`; the backend's own `data` dict is
  not mutated (identity + content check).
- **AC6** A backend returning `data["invocation_id"] = "forged"` → returned value is the event's id, not `"forged"`; outside
  a run context (no event) the key is absent from the returned `data` and no exception is raised. On both, the backend's
  dict is unchanged (still `"forged"`) and `result.data is not` it.
- **AC6c** Run context set with `event_log=None` plus a forged backend id: key absent, nothing raises, backend dict unchanged.
- **AC6d** `data=None` from the backend → `result.data is None` (in AC3 `data_not_dict`). A hard-gate floor refusal in a run
  (pre-dispatch) → no attestation in the log and no `invocation_id` in `result.data`.
- **AC7** Pin-mismatch refusal (R3.3) and capability-escape refusal (R3.6): the event carries `output_sha256` of the
  backend's `raw_response`, and the refusal result's `data["invocation_id"]` equals the event's id.
- **AC8** GH1169 fallback (bd#10 AC-P5 fixture): two events, distinct `invocation_id`s, each `output_sha256` matching its own
  backend's `raw_response`; the returned result's `invocation_id` is the second event's.
- **AC9** `_emit_safe` failing (event log raising on append) does not raise out of the dispatch (AC-P6 unchanged); declared
  limit: the returned id then names an event that was never written (§5).
- **AC10** AUTHORSHIP_SPEC.md: §3 AC-P1 text lists `invocation_id` and `output_sha256`; §4 class M no longer says "The event
  log records no model output". The class-M paragraph (sliced from `**Class M,` to `**Chunk rule.**`) contains `bd#206` and
  `invocation:<step_name>:<invocation_id>`. The R3.2 row of the `REQUIREMENT_LABELS` table names `bd#206` (text assertions).
- **AC11** No new class-I inventory key needed: `class_i_lint` stays green on the tree (the change adds no read/spawn call).
  If GREEN adds one, it must add the inventory key in the same commit.

## §4 Siblings (§1a) — run scoped before and after, delta on base `44c8994`

RED amendments (the only sibling edits): `test_bd10_l3_authorship.py` (AC-P1 `ATTEST_KEYS` → eleven keys; "nine" comments at
`:87`, `:167`, `:397-400`); `test_bd167_cost_cache_billing.py:867` and `:883` (`res.data == data` → compare without
`invocation_id`, plus `invocation_id` matches `^[0-9a-f]{32}$`; at `:883` it also equals the logged attestation's id).
Scoped runs: `test_bd141_p4d_*`,
`test_bd141_p4e_*`, `test_bd147_injected_segments.py`, `test_bd150_class_i_inventory.py`, `test_bd103_effective_model_hook.py`,
`test_bd107_effort_config_fail_closed.py`, `test_bd119_role_template.py`, `test_bd82_semantic_verifier_chokepoint.py`,
`test_bd82_role_backend_effort.py`, `test_bd71_agent_sdk_observations.py`, `test_bd145_reserved_observation_fields.py`,
`test_bd167_cost_cache_billing.py`, `test_bd68_l3_observation_producers.py`, `test_gh1169*`. Any sibling asserting
`result.data == {...}` exactly after a run-context dispatch is an over-constraint to amend per-site, listed in RED's report.

## §5 Declared limits

- `invocation_id` is unique by randomness, not by construction (collision probability negligible at 2^-122 per pair).
- `_emit_safe` swallows a failed append; the returned id can then name a missing event (AC9). Same limit R3.1 already has.
- A step result replayed from a sentinel/cache carries the id of the original dispatch's attestation, not a new one.
- The in-session `request_nonce` is not linked to `invocation_id`.
- `output_sha256` covers `raw_response` only, not tool results, stream events or files the model wrote.
- `bd_l3` verdicts are unchanged; it does not yet check `output_sha256` form (follow-up).

## §6 Not in scope (§1v) — follow-up issue bd#206

Declaring class-M blocks at the carry sites (`phase_6_review` `prev.data["findings"]` / `last_findings.json`, semantic
verifier findings) needs the producing `invocation_id` threaded through `prev.data` and the findings sidecar; that is a
change to phase files outside this lot's zone. Files NOT touched: `engine.py`, `phases/*.md`, `workflows/*`,
`lib/reference_backends/*`, `conformance/bd_l3.py`, `conformance/class_i_inventory.json` (unless AC11 fires).

## §7 Scope (GREEN)

`engine_py/bytedigger_engine/llm_subprocess.py` (`_attest_payload`, `_emit_attestation`, `_dispatch_backend`),
`engine_py/bytedigger_engine/conformance/AUTHORSHIP_SPEC.md` (§3 AC-P1, §4 class M, §2.2 R3.2 row), `CHANGELOG.md` [Unreleased].
RED: `engine_py/tests/test_bd152_output_digest.py` (new) + the §4 amendments in `test_bd10_l3_authorship.py` and
`test_bd167_cost_cache_billing.py`.

## §8 r2 changes (gate r1)

MAJOR-1 bd167 siblings → §4/§7. MINOR-1 no-mutation on every path → §2.4, AC6. MINOR-2 `event_log is None` → §2.4, AC6c.
MINOR-3 non-dict data / pre-dispatch refusal → §2.4, AC6d. MINOR-4 AC10 sliced + R3.2 row → §2.6, AC10. MINOR-5 sentinel
replay → §5. NIT-1 "nine" comments → §4. NIT-2 `request_nonce` → §1.1, §5. NIT-3 tautological identity check → RED drops it.
