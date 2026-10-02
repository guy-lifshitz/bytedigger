# bd#107: an unreadable or malformed effort config surfaces and refuses a hard gate; an unresolved family under a pin surfaces

**Status:** r2 (gate r1 REJECTED: 2 MAJOR + 6 MINOR, all addressed; see `2026-10-02-bd107-gate-r1.md`) · **Tier:** 2 (one prod file `llm_subprocess.py`, `error_codes.py` + `ERROR_CODES.md` one code, one new test file; Option D) ·
**Class:** SYSTEMATIC · **Chokepoint:** `llm_subprocess._resolve_effort` (new), the single reader of
`claude.effort` in the models config. `_load_effort` / `_load_effort_gate` become thin views of it, and
`_dispatch_backend` — the chokepoint every backend dispatch passes (bd#10 docstring) — acts on its verdict.
**Source:** bd#107 (follow-up of #82 part 4, #100).
**Not in scope (§1v):** `engine.py`, `phases/*.md` (bd#89), `_load_pricing_table` (telemetry only, already
warns), the `_invoke_subprocess` self-resolution branch (`llm_subprocess.py:1903-1907`, reached only by a
direct call that bypasses the chokepoint; it keeps using the thin loaders, unchanged).

## §1 Problem (measured on `24dbd13`)

Since #100 a gate effort pin (`claude.effort.by_model[family]`) decides whether a hard gate is dispatched
(`_dispatch_backend`, `llm_subprocess.py:1447-1455`: a backend that cannot apply it is refused with
`E_GATE_EFFORT_UNSUPPORTED`). The loaders feeding that contract fail open:

1. `_load_effort` / `_load_effort_gate` (`:211-284`) wrap everything in `except Exception: return None`.
   An unreadable file, invalid JSON, invalid UTF-8, a non-object top level, a non-dict `claude`, a
   non-dict `by_model`, or a non-str pin value all read as "no pin".
2. `_model_family` returns `None` for an unrecognized model; under a non-empty `by_model` the gate pin
   silently does not apply.

Result: a corrupt or half-written models config makes the pin vanish; the gate runs at default effort,
on a backend that may not be able to apply the pin at all, with no event and no refusal.

## §2 Design

**2.1 `_resolve_effort(model, step_name, *, hard_gate) -> _EffortResolution`** (new, module-private).
`_EffortResolution` is a frozen dataclass / NamedTuple `(effort: str | None, problem: str | None,
detail: str | None)`. Exactly one reader of the file (`config_provider.models_config_path()`, unchanged
seam). Pure apart from that one read; never raises.

"Not configured" — `problem is None`, `effort is None` (unchanged behavior, no event):
- `models_config_path()` itself raises, or the file does not exist (`FileNotFoundError`) — a host
  without a models config (bare install, API-token mode, another provider) is the normal case;
- top level has no `claude` key, or `claude` has no `effort` key, or `effort` is `None` or `""`.

`problem == "unreadable"`: the file exists but `open`/read raises any other `OSError`
(`IsADirectoryError`, `PermissionError`, ...), or the bytes are not valid UTF-8, or not valid JSON.

`problem == "malformed"`: top level not an object; `claude` not an object; `effort` neither `str` nor
`dict`; or, in a dict `effort`, a **consulted** container or entry is unusable. Only what the resolution
actually reads is judged (gate r1 F3): the gate reads `by_model` only; a worker reads `by_phase` (when
`step_name` is given) and, only if that yields nothing, `by_model`. A consulted container
present-and-not-`None` but not a dict, or a consulted entry present but not a non-empty `str`, is
`malformed`. A malformed container the resolution never reads is not a problem (a gate with a valid
`by_model` pin and `by_phase: "x"` resolves the pin, no event; a worker whose `by_phase[step]` is valid
ignores a malformed `by_model`).

`problem == "family_unresolved"`: **hard gate only, signal-only** (gate r1 F1) — `by_model` is a
non-empty dict and `_model_family(model)` is `None`. On the Claude provider this is unreachable (a
family-None model already fails the gate floor, `llm_subprocess.py:3669-3672`), so in practice it is a
non-Claude provider whose argv has no `--model`. Refusing there would make a `claude.`-namespaced pin
block another provider's gate, so it never refuses: it emits the event and the gate is dispatched at
default effort (`effort None`). For a worker it stays silent (`problem None`): a family-keyed pin not
matching another provider's model is the normal mixed-provider case.

Resolution order and values when `problem is None` are byte-identical to today: worker = legacy global
str → `by_phase[step_name]` → `by_model[family]` → `None`; gate = `by_model[family]` only (legacy str and
`by_phase` ignored, GH439). When `problem` is set, `effort is None`.

**2.2 Thin loaders keep their contract.** `_load_effort(model, step_name)` returns
`_resolve_effort(model, step_name, hard_gate=False).effort`; `_load_effort_gate(model)` returns
`_resolve_effort(model, None, hard_gate=True).effort`. Both still never raise and still return `None` on
a broken config (`test_GH439…::test_ac5`, `test_3F13371D…::test_unrecognized_model_family_returns_none`
stay green).

**2.3 `_dispatch_backend`.** Line 1447 becomes one `_resolve_effort(model, step_name,
hard_gate=hard_gate)` call (same position: after the floor and tool refusals, after tier rebinding).
If `res.problem` is set:
- emit `effort_config_invalid` via `_emit_safe` when `run_ctx` and its event log exist, payload exactly
  `{"backend", "step_name", "hard_gate", "problem", "detail"}`; log one `WARNING` per
  `(problem, detail)` per process (module-level dedup set `_WARNED_EFFORT_CONFIG_INVALID`, like `_WARNED_EFFORT_NOT_APPLIED`);
- **hard gate, `problem` in (`unreadable`, `malformed`):** return `StepResult(status="error", error_code="E_GATE_EFFORT_CONFIG_INVALID",
  recoverable=False, step_name=step_name, data=None, duration_ms=0)` with an error message naming the
  problem and the models config path. The backend is **not** called and no attestation is emitted
  (`[bd10:27]`, same as the other pre-dispatch refusals). Order inside step 1 of the chokepoint
  contract: floor → tools → **effort config** → effort capability → injections;
- **hard gate, `family_unresolved`; and any worker problem:** dispatch proceeds with `effort = None`
  (default effort): degrade, never refuse.
`recoverable=False` is intended (gate r1 F8): it matches `E_GATE_EFFORT_UNSUPPORTED`; a half-written
config is an operator fault to fix before resuming, and a gate must not silently retry into a
different effort.
When `res.problem` is None the rest of the chokepoint is unchanged (`effort = res.effort`).

**2.4 Error code.** `E_GATE_EFFORT_CONFIG_INVALID` registered in `error_codes.py` (next to
`E_GATE_EFFORT_UNSUPPORTED`) and `ERROR_CODES.md`: "llm_subprocess: a hard gate's effort pin cannot be
resolved — the models config is unreadable or malformed (bd#107)". The docstring of `_dispatch_backend` (step 1 list) names it.

**Principles.** (1) Deterministic: pure config parsing in code, no model involved. (2) Provider-agnostic:
a missing config, a non-Claude provider, or a worker on an unrecognized family never refuses; only a
hard gate whose pin is *present but unusable* refuses — the existing fail-closed gate contract (#100).
(3) Subscription and API: the chokepoint is backend-independent; AC tests run the refusal through both
a subscription-style backend (`claude-in-session` / agent-sdk shape: a registered backend declaring
`effort`) and an API-style backend (a registered backend declaring `effort`, `anthropic-api` shape),
plus a backend without the capability.

## §3 Acceptance criteria (`engine_py/tests/test_bd107_effort_config_fail_closed.py`)

Fixtures: a real models config file in `tmp_path`, `config_provider.models_config_path` monkeypatched
to it (the established seam in `test_bd82_role_backend_effort.py`), a real `EventLog`, backends
registered through `register_backend` with a call spy (the backend is the boundary, not the unit under
test). `_dispatch_backend` is reached through the same public entry the bd82 tests use.

- **AC1 (gate refusal, production side-effect).** For each config: (a) invalid JSON, (b) a directory
  at the config path, (c) invalid UTF-8 bytes, (d) top level `[]`, (e) `claude: 3`, (f) `effort: 5`,
  (g) `by_model: "opus"`, (h) `by_model: {"opus": 7}` with an opus gate model, (i) `by_model: {"opus":
  ""}` — a hard-gate dispatch returns `error_code == "E_GATE_EFFORT_CONFIG_INVALID"`,
  `recoverable is False`, the backend spy saw **zero** calls, the event log holds exactly one
  `effort_config_invalid` with the right `problem` (`unreadable` for a-c, `malformed` for d-i) and
  `hard_gate: true`, and no attestation event.
- **AC2 (family unresolved, gate: signal, never refuse).** A registered stub provider whose argv has
  no `--model` and whose `model_family` returns None; config `by_model: {"opus": "high"}` and,
  separately, `by_model: {"haiku": "low"}` (gate r1 edge 1): the hard gate is dispatched (one backend
  call, `status == "ok"`), receives `effort=None` (or no kwarg), and one `effort_config_invalid` event
  with `problem == "family_unresolved"`, `hard_gate: true` is recorded.
- **AC2b (only consulted containers are judged, gate r1 F3).** (i) Gate, opus model, config
  `{"by_phase": "x", "by_model": {"opus": "high"}}` → effort `"high"` reaches the backend, no event.
  (ii) Worker with `step_name` whose `by_phase[step]` is `"low"` and `by_model: "x"` → effort `"low"`, no
  event. (iii) Worker with `by_phase: "x"` → `malformed` event, dispatched at `effort=None`.
- **AC3 (worker degrades).** Configs a-i of AC1 (h/i: worker with no `by_phase` entry consults `by_model[family]`) on a worker dispatch: `status == "ok"`, exactly one
  backend call, the backend received `effort=None` when it declares the capability (or no `effort`
  kwarg otherwise), one `effort_config_invalid` event with `hard_gate: false`.
- **AC4 (worker, unresolved family stays silent).** `by_model: {"opus": "high"}`, a worker on an
  unrecognized model: dispatched, no `effort_config_invalid` event.
- **AC5 (not configured never refuses or signals).** Missing file; `{}`; `{"claude": {}}`;
  `{"claude": {"effort": null}}`; `effort: ""` — gate and worker both dispatch, no event.
- **AC6 (loaders keep contract).** `_load_effort` / `_load_effort_gate` return `None` and do not raise
  on every AC1 config; on a valid config they return exactly what they return on `24dbd13`
  (by_model pin, by_phase, legacy str).
- **AC7 (both modes — by backend shape, gate r1 F7).** The refusal precedes any backend call, so the
  real `claude-in-session` / `agent-sdk` / `anthropic-api` registrations are represented by registered
  backends with their capability shapes (a real backend on the base would spawn a CLI or block on the
  file protocol). AC1(a) and AC1(h) refusal holds for a backend declaring `effort` registered as
  a subscription-style backend and for one registered as an API-style backend, and for a backend
  without the capability (the config refusal precedes `E_GATE_EFFORT_UNSUPPORTED`).
- **AC8 (registry).** `"E_GATE_EFFORT_CONFIG_INVALID"` is in `error_codes` registry and in
  both `ERROR_CODES.md` files (`engine_py/ERROR_CODES.md` and `engine_py/bytedigger_engine/ERROR_CODES.md`,
  both regenerated from `error_codes.render_markdown()`); the code is not in `check(root)`'s
  `unregistered` set (the `dead` leg is dropped: the RED file's own literal makes it vacuous, gate r1 F5).
- **AC9 (§2.3 obligations, gate r1 F6).** (i) Two hard-gate dispatches on the same invalid config emit
  two events but log exactly one `WARNING` naming `effort_config_invalid`. (ii) The refusal `error`
  string contains the `problem` value and the models config path. (iii) A hard gate that fails both the
  tool restriction and the effort config returns the tool-restriction code (tools precede effort
  config).

Expected RED on `24dbd13` (measured r2: 30 failed, 24 passed; AC9(iii) is a guard too): AC1, AC2, AC2b(iii), AC3 (no event), AC7, AC8, AC9 fail; AC2b(i,ii), AC4, AC5, AC6 pass (guards). (Counts re-measured for r2 below the RED update.) AC2 uses a registered stub provider (selected via `HAL_LLM_PROVIDER`) whose `model_family` returns None: on the default provider the floor only passes models that have a family.

## §4 Sibling tests (§1a)

`test_32ED59E2_effort_lever.py`, `test_3F13371D_effort_hybrid.py`, `test_GH439_hard_gate_effort_pin.py`
(AC5 pins loader `None` on broken JSON — kept by §2.2; AC6/AC8 pin `_load_effort_gate(model)` /
`_load_effort(model, step_name)` text inside `_invoke_subprocess` — that branch is out of scope and
unchanged), `test_bd82_role_backend_effort.py` (e7/e7b: not-configured / non-pin config never refuse —
kept by §2.1), `test_GH329_anthropic_api_effort.py`, the error-code registry test.

## §5 Scope

- `engine_py/bytedigger_engine/llm_subprocess.py` (prod)
- `engine_py/bytedigger_engine/error_codes.py` (one code); `engine_py/ERROR_CODES.md` and
  `engine_py/bytedigger_engine/ERROR_CODES.md` regenerated from `render_markdown()` (gate r1 F2;
  `test_bd8_l1_oracle.py:1315-1317` pins byte-equality)
- `engine_py/tests/test_bd107_effort_config_fail_closed.py` (new, RED)
- `CHANGELOG.md` (one entry)
