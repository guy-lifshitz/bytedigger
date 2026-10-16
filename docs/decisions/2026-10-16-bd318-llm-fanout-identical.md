---
red_tests:
  - engine_py/tests/test_bd318_llm_fanout.py
sibling_tests:
  - engine_py/tests/test_bd150_class_i_inventory.py
  - engine_py/tests/test_bd206_class_m_sites.py
  - engine_py/tests/test_bd89_p3b1b_ii_aggregation_helper.py
  - engine_py/tests/test_bd290_flag_unregistered_warn.py
paths:
  - engine_py/bytedigger_engine/lib/llm_fanout.py
  - engine_py/bytedigger_engine/conformance/class_i_lint.py
  - engine_py/bytedigger_engine/flags_catalog.py
  - CHANGELOG.md
---
# bd#318: a fan-out of identical model calls cannot be built (structural), and is a finding (insurance)

Refs #318. Base: origin/main @ e027c49. Tier 2.

## §1-PREFLIGHT

- **Class:** SYSTEMATIC. Any place that dispatches N >= 2 model calls concurrently as "independent reviewers/evaluators" while the N jobs carry one prompt and one input. No lint sees it.
- **Chokepoint (this is the fix):** ONE helper, `lib/llm_fanout.py::run_distinct_llm_jobs`. It takes a list of `FanoutJob(role, prompt, input_text)` and raises `IdenticalFanoutError` BEFORE any model call when two jobs compose to the same normalized invoked prompt (§Change A). An identical fan-out cannot be assembled through it. The role label alone never distinguishes jobs (the #318 bug is "3 evaluators with different focus", same prompt).
- **Insurance (second layer):** `class_i_lint.fanout_findings(root)` flags a model call (`invoke_llm_subprocess`) handed to a pool/thread dispatcher (`submit`, `map`, `starmap`, `apply_async`, `run_in_executor`, `Thread(target=...)`, `run_with_current_run`) outside the helper module. This is the owner's rule: never call the model in a pool bypassing the helper.
- **Measured on e027c49:** `workflows/phase_6_review.py:3229-3248` (`_run_satisfaction_evaluators_parallel`) submits `invoke_llm_subprocess` n times with the same prompt. Reproduces. It is NOT touched here; lot #317 removes it. The helper and the rule are built so that after #317 the real tree has 0 findings.
- **Owner's last comment (16.10) is binding:** prevent structurally first; the lint is insurance.

## Change

A. New `lib/llm_fanout.py` (stdlib + `llm_subprocess` + `telemetry_ctx`):
- `FanoutJob(role: str, prompt: str, input_text: str = "")`, frozen dataclass.
- `IdenticalFanoutError(ValueError)`; message names the colliding job indices and roles.
- `composed(job)` = `prompt` when `input_text` is empty, else `prompt + "\n\n" + input_text`: this is exactly the invoked prompt. Identity key = `" ".join(composed.split()).casefold()` (whitespace runs collapsed, case folded).
- `assert_distinct(jobs)`: empty list, a job with a blank (whitespace-only) `role`, or a job whose composed prompt is whitespace-only -> `ValueError`; two jobs with equal identity key -> `IdenticalFanoutError` (so `prompt="P\n\nIN"` vs `prompt="P", input_text="IN"`, trailing whitespace and case changes all collide). One job is allowed (no fan-out).
- `run_distinct_llm_jobs(jobs, *, model, timeout_sec, extra_data, **invoke_kwargs) -> list[StepResult]`: calls `assert_distinct` first (a rejected list makes zero model calls); then `ThreadPoolExecutor(max_workers=len(jobs))`, carrying the parent's `telemetry_ctx` run into the workers (as the current satisfaction helper does); the invoked prompt is `composed(job)`, the workers really run concurrently (not serially); results are returned in job order. It resolves `invoke_llm_subprocess` through its own module attribute so tests can substitute it.

B. `conformance/class_i_lint.py`: add `fanout_findings(root) -> list[str]` and `main(argv=None) -> int`. Scope = the existing `_SCAN_DIRS`; the file `lib/llm_fanout.py` is exempt. A dispatcher call is a call whose callee last segment is one of `submit`, `map`, `starmap`, `apply_async`, `run_in_executor`, `to_thread`, `Thread`, `Process`, `run_with_current_run`. It is a finding when ANY reference to `invoke_llm_subprocess` (bare name, import alias, or `<module>.invoke_llm_subprocess` attribute) occurs anywhere in the subtree of its arguments, at any positional index or keyword, including inside a `lambda` or `functools.partial(...)`. This includes a direct, non-pooled `run_with_current_run(ctx, invoke_llm_subprocess, ...)` (a dispatcher form). The message is `"<rel>:<line>: pooled model call bypasses lib/llm_fanout: invoke_llm_subprocess via <dispatcher>"`, anchored at the dispatching call line. A direct call `invoke_llm_subprocess(...)`, including inside a loop or a retry, is never a finding. The exemption is the exact relpath `lib/llm_fanout.py`, not a basename. `check()` is unchanged (so the bd#150 AC10 pin stays green). `main(argv=None)`: `argv[0]` (optional) is the tree root, default the `bytedigger_engine` directory (parent of `conformance/`); inventory = module-level `load_inventory()`; it prints one problem per line to stdout, `check()` problems then `fanout_findings` (only when the switch is on), returns 1 if any line was printed, else 0.

C. Switch `HAL_LLM_FANOUT_LINT` (`kind: gate`, default on, `=0` (exactly the string `0`; unset or any other value = on) turns the rule off: `fanout_findings` returns `[]`, `main` reports exactly the previous `check()` set), registered in `flags_catalog.py` with `module: conformance/class_i_lint.py`.

## Acceptance criteria

- AC1: `run_distinct_llm_jobs` with 3 jobs differing in prompt: 3 model calls, results in job order, each call got its own prompt.
- AC2: 3 jobs with identical `(prompt, input_text)` (roles differ): `IdenticalFanoutError`, ZERO model calls.
- AC3: two jobs with the same prompt but different `input_text`: allowed, both run, invoked prompt includes the input.
- AC4: a single job runs; an empty list, a blank role, a blank prompt raise `ValueError` (not `IdenticalFanoutError`), zero calls.
- AC5: `fanout_findings` on a fixture whose `executor.submit(invoke_llm_subprocess, ...)` runs in a `ThreadPoolExecutor`: exactly one finding, carrying `workflows/x.py:<line>`; `main` on that tree returns 1.
- AC6: each exactly one finding: `pool.map`, `threading.Thread(target=...)`, an import alias (`... import invoke_llm_subprocess as call`), `llm_subprocess.invoke_llm_subprocess` attribute form, `run_in_executor`, `starmap`, `apply_async`, a lambda and a `functools.partial` wrapping the call, and the REAL shape `executor.submit(run_with_current_run, ctx, invoke_llm_subprocess, prompt=...)` (model callable at positional index 2), plus a direct `run_with_current_run(ctx, invoke_llm_subprocess, ...)`.
- AC7: a fixture with a single direct call, a direct call in a `for` loop, a retry call, and a pooled call of a non-model function: 0 findings, `main` returns 0 given an inventory-clean tree.
- AC8: `lib/llm_fanout.py` itself (pooled `invoke_llm_subprocess`) yields 0 findings; the same code at `workflows/llm_fanout.py` yields exactly one (no basename exemption).
- AC9: `HAL_LLM_FANOUT_LINT=0`: AC5's tree gives `fanout_findings == []`; `main` on a tree that has both a `check()` problem and a pooled call prints exactly the `check()` lines and returns 1; on a clean-inventory tree it returns 0. Unset, `1` and `yes` keep the rule on.
- AC10: real tree. Locate `_run_satisfaction_evaluators_parallel` in `workflows/phase_6_review.py` by AST (`None` when absent). If present: exactly one finding, inside its line range. If absent (after #317): `fanout_findings == []`. In both cases no finding exists elsewhere.
- AC12 (helper behaviour): the fake records thread ids / uses a barrier so a serial implementation fails; results come back in job order although the first job completes last; the parent's `telemetry_ctx` current run is visible inside every worker.
- AC11: `HAL_LLM_FANOUT_LINT` is in the flags catalog, `kind` gate, default on; the flag-owner/catalog lint passes.

## Out of scope

The satisfaction fan-out and `phases/phase-6-review.md` (lot #317). Wrappers that call the model on behalf of a pool (e.g. a function that wraps `invoke_llm_subprocess`) are not traced; the helper is the structural layer, the rule covers the direct-reference bypass only.
