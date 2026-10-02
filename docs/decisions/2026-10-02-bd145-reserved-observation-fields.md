# bd#145: reserved observation fields cannot be shadowed by `extra_data`

**Status:** r3 (gate r2 APPROVED; r3 applies its three MINOR advisories. r1 REJECTED: 1 MAJOR + 5 MINOR. See `2026-10-02-bd145-gate-r{1,2}.md`) · **Tier:** 2 (one engine prod `.py`, Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._dispatch_backend`, the single call site that
hands the caller's `extra_data` to every registered backend (`_BACKENDS[resolved_backend](...)`).
**Source:** bd#145 (found by the bd#141 4(e) gate, r2 F-r2-3 / r1 F5).

## §1 Problem (measured on `083a158`)

1. `_pin_mismatch_refusal` (R3.3) and `_capability_escape_refusal` (R3.5/R3.6) read `result.data`
   (`_observed`, `:1180`). Whatever a backend leaves in `observed_model` / `observed_tools` is adjudicated
   as the adapter's report.
2. `_invoke_in_session` sets `observed_model`, `worker_written_paths`, `manifest_source` (`:975-980`) and
   THEN `data.update(extra_data)` (`:982`). A caller value wins.
3. The straggler synthetic-ok branch of `_invoke_subprocess` (`:2040-2047`) merges `extra_data` with no
   reserved-name override, so a caller can inject any observation field into an `ok` result.
4. The same defect exists outside issue's text: `reference_backends/agent_sdk.py:700`,
   `pydantic_openai.py:739`, `pydantic_anthropic.py:263` merge `{**base_data, **caller_extra}`
   (caller wins over `observed_model` / `observed_tools` / `worker_written_paths` / `manifest_source`),
   and `anthropic_api.py:278` does the same (`{**base_data, **(extra_data or {})}`, verified on `083a158`, gate r1 F2). Any third-party backend registered via `register_backend`
   receives the raw `extra_data` too.
5. Only the claude-subprocess success branch (`:2409-2433`) is safe: producer lines run after the merge.
   Pinned by `test_bd141_p4e_subprocess_observed_model.py::test_ac8` and
   `test_gh1194_mcp_server_losses.py` (`:935`).
6. §1a audit: no production caller passes any reserved name in `extra_data`
   (`grep -A12 'extra_data=' workflows/` → 0 hits). The only test callers that do (the two in item 5)
   assert the name is NOT shadowed, which this change keeps. `test_gh1194`'s warning filter gets one named edit (AC12, §4).

A per-path helper (issue's suggested fix shape) would need six call sites, one per backend plus the
straggler branch, and would not cover third-party backends. Stripping at the dispatcher closes the whole
class at one point.

## §2 Design

- New module constant `RESERVED_OBSERVATION_FIELDS: frozenset[str]` =
  `{"observed_model", "observed_tools", "worker_written_paths", "manifest_source", "mcp_server_losses"}`.
- `_dispatch_backend` passes the backend `extra_data` with those keys removed (a new dict; the caller's
  dict is never mutated). Only a `collections.abc.Mapping` is filtered (gate r2 F2): `None`, `{}` and a non-dict value pass through
  unchanged, so today's behaviour for them is kept. A reserved key is dropped whatever its value,
  including `None`. Dropped names produce one `logger.warning` record per `_dispatch_backend` call
  (the hang fallback at `:1784` dispatches twice, so it logs twice; gate r1 F3). The message starts with
  the module constant `RESERVED_DROP_LOG_PREFIX = "extra_data reserved observation fields dropped: "`
  followed by the sorted names joined with `", "` (gate r2 F1). Known limit: a list of key/value pairs is not a Mapping and passes through unchanged (no caller does this; `extra_data` is typed `dict | None`).
- Backends are unchanged. A reserved name can then only come from a backend's own producer. A path with
  no producer for a name leaves it absent, which is the existing first-class `not-checked` state.
- `invoke_llm_subprocess` docstring: "caller's keys win on collision" gains "except the reserved
  observation fields (`RESERVED_OBSERVATION_FIELDS`), which are dropped".
- Not in scope: `straggler_aborted` (a control flag, not an observation), the `straggler_data` shape,
  any backend file, `_pin_mismatch_refusal` / `_capability_escape_refusal` logic.

## §3 Acceptance criteria

All runs go through `invoke_llm_subprocess` end to end; only external seams (the in-session runner
result file / `subprocess.Popen` / a test-registered backend) are faked. `_dispatch_backend`,
`_invoke_in_session`, `_invoke_subprocess`, `_pin_mismatch_refusal` run for real.

- **AC1** `llm_subprocess.RESERVED_OBSERVATION_FIELDS` is a `frozenset` equal to exactly the five names in §2.
- **AC2 (in-session, field-by-field)** In-session success with runner `dispatched_model` = opus id,
  `model="opus"`, and `extra_data` holding a forged sentinel for each of the five names: `status == "ok"`;
  `observed_model` == the runner's `dispatched_model`; `worker_written_paths` and `manifest_source` equal
  the producer values (same as a run without `extra_data`); `observed_tools` and `mcp_server_losses` are
  absent from `data`.
- **AC3 (in-session, §1l side effect)** Runner reports a sonnet id, `model="opus"`, forged
  `extra_data={"observed_model": "claude-opus-5-5"}`: `error_code == "E_MODEL_PIN_MISMATCH"`, and the
  run's event log has one `model_pin_mismatch` event whose `observed_model` is the sonnet id.
- **AC3b (no false refusal)** Runner reports an opus id, `model="opus"`, forged
  `extra_data={"observed_model": "claude-sonnet-5-5"}`: not an error, no `model_pin_mismatch` event.
- **AC4 (straggler path)** Straggler synthetic-ok run (watchdog `aborted`) with forged sentinels for
  all five names and `model="opus"` + forged `observed_model="claude-sonnet-5-5"`: `status == "ok"`,
  `data["straggler_aborted"] is True`, none of the five names in `data`, no `model_pin_mismatch` event.
- **AC5 (straggler, tools)** Same straggler run with `allowed_tools=["Read"]` and forged
  `extra_data={"observed_tools": ["Bash"]}`: not refused by the capability-escape check.
- **AC6 (any registered backend)** A test backend registered with `register_backend` that returns
  `{"raw_response": "x", **(extra_data or {})}`: forged reserved values do not appear in `result.data`;
  with `model="opus"` and forged `observed_model="claude-sonnet-5-5"` the result is not refused.
- **AC7 (no over-strip)** Same test backend: a non-reserved key (`{"doc_path": "p"}`) reaches the
  backend and is in `result.data`.
- **AC8 (caller dict untouched)** The caller's `extra_data` dict still holds all its keys after the call.
- **AC9 (visible)** A dropped reserved name produces one `WARNING` record on the `llm_subprocess`
  logger that names it; a call with no reserved names produces none.
- **AC10 (regression lock)** The claude-subprocess success path keeps producer values for all five names
  under forged `extra_data` (already green; pins the class on the safe path).
- **AC11 (edges, gate r1 F5)** Through the AC6 test backend: `extra_data={"observed_model": None}` →
  `observed_model` not in `result.data` and one warning; `extra_data={}` and `extra_data=None` → no
  warning and the backend receives the same value; a non-dict `extra_data` (e.g. a list) is handed to the
  backend unchanged, with no warning and no exception raised by the dispatcher.
- **AC12 (sibling, gate r1 F1)** `test_gh1194_mcp_server_losses.py::test_ac17` stays green: its
  `_WarningCapture.mcp_messages` filter excludes records starting with `RESERVED_DROP_LOG_PREFIX`.

**Which change reddens each negative:** removing the strip in `_dispatch_backend` reddens AC2-AC6, AC9;
stripping in place on the caller's dict reddens AC8; stripping every key reddens AC7; filtering
non-dicts or warning on empty input reddens AC11. AC10 is a lock, not a negative: it reddens only if the
strip is removed AND a subprocess producer line is moved before the `extra_data` merge (gate r1 F4).

**Verification (gate r1 F6):** targeted run of the new file + §1a siblings
(`test_gh1194_mcp_server_losses.py`, `test_bd141_p4e_subprocess_observed_model.py`,
`test_02FF48F4_model_pin_insession.py`, `test_ccbb65dc_straggler_watchdog.py`, `test_bd71_agent_sdk_observations.py`)
locally; the base→HEAD full-suite delta is read from CI on the PR (local full suite is denied, GH2196).

## §4 Files

- Prod: `engine_py/bytedigger_engine/llm_subprocess.py` only.
- Test (new): `engine_py/tests/test_bd145_reserved_observation_fields.py`.
- Test (named edit, AC12): `engine_py/tests/test_gh1194_mcp_server_losses.py` — only the
  `_WarningCapture.mcp_messages` property (`:205-207`) gains the prefix exclusion; the literal prefix is
  read from `llm_subprocess.RESERVED_DROP_LOG_PREFIX` via `getattr(..., None)` so it collects on base.
- NOT in scope: `reference_backends/*`, `conformance/*`, any other existing test.
