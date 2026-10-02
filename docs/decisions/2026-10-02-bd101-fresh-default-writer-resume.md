# bd#101 — fresh session by default; warm resume only for writer steps

Revision r1.

## Problem

Warm resume is the default for every non-gate dispatch. `_dispatch_backend`
passes `fresh_session = fresh_session or hard_gate` to `warm_resume` backends,
so a non-gate judge is fresh only if its call site remembers to pass
`fresh_session=True`. Today three do: the phase 6 reviewer, the decorrelated
verifier and the semantic verifier. A new non-gate judge that forgets resumes
the `run_id:<step family>` session of an earlier cycle and reads its own prior
verdict. That is the defect #82 fixed for gates.

Chokepoint: `_dispatch_backend` in `llm_subprocess.py`, the one place every
dispatch passes through (the GH1169 fallback included).

## Change

1. **Writer table** (`llm_subprocess.py`). Add a module constant
   `_WARM_RESUME_STEPS: frozenset[str]` with the step families that may resume:
   `invoke_red_llm`, `invoke_green_llm`, `invoke_green_llm_retry`,
   `invoke_fix_llm`, `invoke_fix_llm_retry`, `invoke_spec_llm`.
   Add `_WARM_RESUME_STEP_PREFIXES: tuple[str, ...] = ("repair_",)`. Every
   directed repair goes through `lib/directed_repair.py` with a
   `repair_step_name` that starts with `repair_`.
   Add `_warm_resume_allowed(step_name: str) -> bool`. It takes the family
   `step_name.split(".")[0]`, the same family the agent-sdk session key uses,
   and returns True when the family is in the table or starts with a listed
   prefix. Anything else returns False. It does no I/O.
2. **Chokepoint rule** (`_dispatch_backend`). For a `warm_resume` backend:
   `optional["fresh_session"] = fresh_session or hard_gate or not _warm_resume_allowed(step_name)`.
   So the default is fresh, and resume is opt-in by being a listed writer.
   Backends without `warm_resume` still get no `fresh_session` keyword
   (strict-signature compatibility, unchanged).
3. **Judges are fresh by role** (`invoke_llm_subprocess`). Once
   `effective_role` is known, set `fresh_session = fresh_session or effective_role == "judge"`.
   That value goes to both `_dispatch_backend` calls (the main one and the
   GH1169 fallback). So `role="judge"` is fresh even on a listed step name.
   Role still only routes the backend otherwise. `hard_gate` alone decides the
   gate rules (unchanged).
4. **Backend default flips** (`lib/reference_backends/agent_sdk.py`). The
   `fresh_session` parameter of `agent_sdk_backend` defaults to `True`. A
   registration that omits `warm_resume` (so the dispatcher never forwards the
   keyword) is therefore always fresh. It degrades to a cold session and never
   fails. The `fresh_session or hard_gate` rule inside the backend stays.
5. **Call sites unchanged.** No file under `workflows/` or `phases/` changes.
   The three existing `fresh_session=True` and `role="judge"` call sites stay
   as they are; they are now redundant but harmless.
6. **Docs.**
   - `register_backend` docstring: `warm_resume` impls receive
     `fresh_session`, which is True unless the step is a listed writer that is
     neither a hard gate nor a judge and did not ask for fresh (bd#101).
   - `_dispatch_backend` docstring and the inline bd#82 comment say the same.
   - CHANGELOG: one bullet in the existing first `Unreleased` → `### Changed`
     section (bd119 F8 reads that section; do not add a second one). It says
     fresh is now the default, lists the writer families, and says how a new
     writer opts in (add its step family to `_WARM_RESUME_STEPS`).

## Precedence (the whole rule)

For a `warm_resume` backend, a dispatch is warm only if ALL hold: not a hard
gate; effective role is not `judge`; caller did not pass `fresh_session=True`;
the step family is listed. Otherwise fresh.

## Sibling amendments (declared)

`engine_py/tests/test_bd82_fresh_sessions_thread_ctx.py` assumes the old default:
- `test_f6b_chokepoint_resolves_fresh_for_gates`: the non-gate call uses
  step `"s"` and expects `False`. Change that call's step name to
  `"invoke_green_llm"`, keep `[True, False]`.
- `test_f4b_fresh_call_leaves_the_warm_session_alone`: the warm calls use the
  default step `invoke_review_llm`. Make the two warm calls use
  `step_name="invoke_fix_llm"` and keep the fresh calls on `invoke_review_llm`
  with `fresh_session=True`; expected `[None, None, None, "sess-1"]` stays.
  (Different key now; the test still proves a fresh call does not touch the
  cache.)
- `test_f4_fresh_session_is_not_offered_to_later_calls` and
  `test_f5_explicit_fresh_worker`: both still pass (the non-listed step is
  fresh anyway). Leave them.
Any other test that fails only because it expects a non-listed step to resume
is amended to a listed writer step, and listed in the PR body. A test that
fails for any other reason is a regression, not an amendment.

## Out of scope

- `workflows/` and `phases/` (bd#89 lot 1570 P3b2 in flight).
- `is_engine_owned_path` (#94), per-cycle artifact invalidation (#92),
  pre-GREEN gate softening (#91).
- New events or payload fields. agent-sdk already reports `warm_resumed`.
- Backend routing by role (#100) is unchanged; no `writer` role is added.
- No new file read, subprocess or git read under `bytedigger_engine`
  (bd#150 inventory unchanged).

## Acceptance (`engine_py/tests/test_bd101_fresh_default.py`)

Real agent-sdk backend through `invoke_llm_subprocess`, with only
`claude_agent_sdk.query` replaced (records `options.resume`), as in the bd#82
test's `sdk` fixture; spy backends via `register_backend` where noted.

- **AC1 unlisted non-gate is fresh.** Two calls, `step_name="invoke_new_judge"`,
  no `fresh_session`, no `role`, not a gate → resumes `[None, None]`, and
  `_SESSION_CACHE` is empty after both.
- **AC2 listed writers resume.** For each family in `_WARM_RESUME_STEPS` and for
  `"repair_spec_lint"` (parametrized): two calls → `[None, "sess-1"]`.
- **AC3 explicit fresh beats the table.** `invoke_green_llm`, warm call then
  `fresh_session=True` call → `[None, None]`.
- **AC4 judge role beats the table.** `invoke_fix_llm` with `role="judge"`,
  twice → `[None, None]`.
- **AC5 hard gate beats the table.** `invoke_green_llm`, `hard_gate=True`,
  twice → `[None, None]`.
- **AC6 dotted family.** `"invoke_green_llm.a"` then `"invoke_green_llm.b"` →
  `[None, "sess-1"]` (same family, listed).
- **AC7 chokepoint value, both branches.** Spy `warm_resume` backend,
  parametrized `stable_prefix in ("", "p")`: non-gate calls on `"s"`,
  `"invoke_green_llm"`, `"repair_x"`, and `"invoke_green_llm"` with
  `role="judge"` receive `fresh_session` `[True, False, False, True]`.
  A spy backend without `warm_resume` receives no `fresh_session` key.
- **AC8 fallback forwards the effective value.** agent-sdk registered as a spy
  that returns the GH1169 hang result (`hang_attempts=1`,
  `E_LLM_API_TIMEOUT`); claude-subprocess registered as a `warm_resume` spy.
  Non-gate `"s"` → fallback gets `fresh_session=True`; non-gate
  `"invoke_fix_llm"` → `False`; `"invoke_fix_llm"` with `role="judge"` → `True`.
- **AC9 backend default is fresh.** agent-sdk registered WITHOUT `warm_resume`;
  two non-gate calls on `invoke_green_llm` → `[None, None]`, both `ok` (degrade,
  never an error). Also `inspect.signature(agent_sdk_backend)` has
  `fresh_session` default `True`.
- **AC10 table drift lint (AST, no import of workflows).**
  - every name in `_WARM_RESUME_STEPS` appears as a `step_name="<name>"`
    keyword literal in some `invoke_llm_subprocess(...)` call under
    `bytedigger_engine/` (a stale entry fails);
  - every string literal passed as `repair_step_name=` under
    `bytedigger_engine/` starts with a prefix in `_WARM_RESUME_STEP_PREFIXES`;
  - no `invoke_llm_subprocess(...)` call with a literal `hard_gate=True` uses a
    listed step name (a gate must never be a listed writer);
  - no call with literal `role="judge"` or `fresh_session=True` uses a listed
    step name.
- **AC11 no workflow change.** `git diff --name-only origin/main...HEAD` touches
  nothing under `engine_py/bytedigger_engine/workflows/` or `phases/`
  (checked by the orchestrator before PR, not a pytest).

## Known limits

- A renamed writer step silently loses resume (cost, never correctness);
  AC10's stale-entry check catches the rename in CI.
- A dynamic `step_name` (not a literal) for a writer is invisible to AC10;
  today there is none apart from `repair_step_name`, which the prefix covers.
