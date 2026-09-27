# bd#85 — a gate failure retries its step with findings; a driver resumes the failed phase

Issue: #85 (P4). Class: SYSTEMATIC (engine) + PROCESS (driver). Chokepoints:
`workflows/_recoverable_policy.py` (the budget matrix) and the new `lib/task_resume.py`
(the driver seam).

## Problem (verified against the code)

- **One budget for two jobs.** Every deterministic spec gate spends the `spec_retry` budget
  (`recoverable_once`). That covers completeness, scope-inverse, reentry, helper-extraction,
  coverage, lint-batch, ac-dsl, citations and the six spec-file-missing checks. The spec
  reviewer (`_gate_on_review`, which reads the policy by hand) spends the same budget. One
  lint retry leaves the reviewer with nothing, so a REVISE after any lint retry is terminal.
- **Spec lint failures are terminal.** `_verify_spec_lint` (`E_SPEC_LINT_FAIL`),
  `_verify_spec_cite_lint` (`E_SPEC_CITE_LINT_FAIL`) and `_verify_spec_preflight_batch`
  (`E_SPEC_PREFLIGHT_BATCH`) return `recoverable=False` once directed repair does not
  converge. The writer never sees the findings.
- **Satisfaction is terminal.** `_write_satisfaction_doc` and `_write_satisfaction_doc_multi`
  return `recoverable=False` for `E_SATISFACTION_BELOW_THRESHOLD` and
  `E_SATISFACTION_AC_CHECKLIST`. The evaluator's `fixes_required` never reach a fix worker.
- **No driver seam.** The phase sentinel caches a successful phase under
  `(run_id, workflow, ctx hash)`. The step sentinel caches `resume_sentinel=True` steps,
  keyed by cycle. A driver that keeps the `RUN_ID` therefore pays nothing for finished work.
  Nothing tells a driver whether to resume, stop or reroute, or when the task has spent
  enough.

## Contract

### 1. Separate budgets

`spec_retry` is replaced by two gates. Both have explicit rows for SIMPLE, FEATURE and
COMPLEX.

- **`spec_gates`**, `recoverable_once`: every deterministic spec gate. The name is not
  `spec_lint`, which is already the directed-repair label for one of these gates.
  - Every `spec_gates` retry is built by one helper in `phase_45_spec.py`. It is the only
    `gated_step_result(gate="spec_gates", ...)` call in the module.
  - When the caller passes no findings, the helper uses the error message as the findings,
    so a spec-file-missing retry still tells the writer something.
- **`spec_review`**, `recoverable_once`: `_gate_on_review`, including its hand-written
  policy read.

Other budgets and bounds:

- RED gates (`red_lint_preflight` ×2, `red_runtime`, `red_crashed`) are unchanged.
- In phase 4.5 the writer runs at most three times: the first pass, one gate retry and one
  review retry. With `HAL_SPEC_CITE_PRELINT_ENFORCE=1` the prelint's own retry adds one
  more pass. The engine backstop (6) and the durable REVISE counter are unchanged.
- The two paths that dropped `gate_attempts` now carry it: the preflight batch failure
  (which returned `data={"findings": ...}` with no `prev.data`) and the frozen fallback.

### 2. Spec gate failures retry the writer with their own findings

- **Which failures retry.** `E_SPEC_LINT_FAIL`, `E_SPEC_CITE_LINT_FAIL` and
  `E_SPEC_PREFLIGHT_BATCH` go through the helper. At the cap they are terminal with the same
  error code as today. The retry's `error` still contains the finding evidence.
- **At the cap.** The terminal result sets `invalidate_cycle_sentinels_on_fail` (and so
  does `_gate_on_review` at its cap). Without it, a driver resume would replay the cached
  writer and reviewer outputs that just failed and fail again.
- **Which stay terminal: infrastructure failures.**
  - lint: driver missing, timeout (rc 124), driver error (rc 2), rc 1 with nothing on stdout
    (a crashed driver), unexpected rc;
  - cite-lint: driver missing, timeout, rc 2, unexpected rc, and rc 1 with nothing parsed.
  - The preflight batch marks each finding `infra: True` at the source for a lint rc 2, a
    lint rc 1 with no output, a cite rc 2, and a cite rc 1 with nothing parsed. If any finding is infra, the batch is
    terminal.
  - The batch keeps its finding list under `spec_lint_findings`, a key that directed repair
    already reads, because `findings` becomes the rendered string.
- **Findings routing.**
  - The helper sets `retry_source="spec_gates"` and clears `structured_findings`.
  - On a `spec_gates` retry, `_build_spec_prompt` does not load the persisted review
    thread. It turns the gate findings (one per line) into structured findings and uses the
    same patch-in-place path as review findings, labelled "spec gate findings". The spec is
    therefore patched rather than rewritten, and fixes made for an earlier review survive a
    gate retry.
  - Without structured findings, the free-rewrite fallback uses a heading that names them
    as spec gate findings.
  - A later review REVISE builds its own data without `retry_source`, so reviewer retries
    are unchanged.
- **Lite workflow stays terminal on lint.** `phase_45_spec_lite` gives its loop body a
  wrapper around `_verify_spec_lint` that turns the retry back into today's terminal
  result. Two reasons:
  - Lite's body rebuilds its data, so `gate_attempts` never accrues there. The loop's
    ceiling exit also carries no error code.
  - Lite is slated for removal (#89).

### 3. Satisfaction FAIL runs a fix loop

A new gate, `satisfaction`, is `recoverable_twice` (cap 2) with explicit rows for all
classes.

**Which failures start the loop.** Only those that name work a fix worker can do:

- `concurring_fail`, `score_only_below_threshold` and `ac_checklist_fail` with failing AC
  entries, in the single-evaluator path. A missing checklist section is an evaluator-format
  failure and stays terminal.
- A multi-evaluator majority FAIL whose evaluators list fixes. A majority FAIL with no fixes
  listed stays terminal.
- A multi-evaluator AC-checklist override where some evaluator names failing ACs.
- A multi-evaluator run with one valid evaluator is degraded, and `E_REVIEW_DEGRADED` is
  checked first, so it stays terminal.

**Which stay terminal.** Evaluator-format failures (`no_signals`,
`structured_only_no_score`, `drift_*`) and `E_REVIEW_DEGRADED` stay terminal. A fix worker
cannot repair an evaluator's output.

**Where the loop runs.** Only in a running workflow that has a `build_fix_prompt` step.
The workflow is identified by `get_current_run().phase` and the target index is resolved
by step name. In `phase_6_review_simple_fastpath`, which has no fix step, satisfaction
stays terminal.

**What the retry carries.**

- `findings`: the evaluator's `fixes_required`, the failing AC ids, or the gate message.
- `spec_path`, `review_doc_path`, `fix_doc_path`.
- `review_fix_doc_path`: the verified-only feed, derived from `review_doc_path` when the
  chain lost it.
- `verdict`, taken from `review_verdict`.
- `fix_loop_source="satisfaction"`.
- `cycle_count`: the current cycle.
- `gate_budget_ok: True`, which lets the engine run the second fix loop past its default
  two-cycle limit (the backstop still applies).

**`build_fix_prompt` changes.**

- It accepts a plain-dict `prev`: the engine passes retry `initial_data` as a dict.
- On a satisfaction retry it adds a "SATISFACTION FINDINGS" section, cites the
  satisfaction report file, and leaves out the
  "review verdict PASS → FIX SKIPPED" early-return rule. The satisfaction prompt contains no
  FIX SKIPPED text at all, including in the structured-output instructions.
- The fix scope covers the review findings and the satisfaction findings.

**Attempts.**

- Attempts = engine cycle − 1. Nothing else in phase 6 advances the cycle, and the steps
  between `build_fix_prompt` and the gate rebuild their data.
- The cycle comes from the engine's step context. With no step context the gate fails
  closed: the budget counts as spent and the result is today's terminal one. It also emits
  `satisfaction_fix_loop_unavailable`, so a lost step context is visible.
- Satisfaction attempts restart with each process. With a driver the bound is
  (task runs) × 3 satisfaction passes.

**Terminal exit invalidates every cycle.** A terminal exit that declares
`invalidate_cycle_sentinels_on_fail` invalidates cycles 1..N, not just N (GH925). The
event keeps reporting the current cycle.

### 4. Driver seam

**Event payload.** `workflow_finished` gains `error_code` when the status is `error` or
`escalate`. Paused rows keep exactly `{workflow_name, status, wall_ms}`, which
`test_gh576_pause_lane` pins.

#### `lib/task_resume.py`, pure and deterministic

**`plan_resume(events_path, run_id, phases) -> ResumePlan`** returns `action`,
`resume_from`, `completed`, `last_status`, `last_row` and `error_code` (the deciding row's
code).

- `resume_from` is `None` for `done` and `stop`.
- `last_status` is the status of the deciding phase's fresh row, or `None` when that phase
  has no fresh row.
- `last_row` is that row's position in the log, used to consume a paused credit once.

It reads only authoritative rows for `run_id`: `workflow_finished`, `task_cap_reset`,
`restart_governor_denied`, and `phase_refused`. `run.py` writes a `phase_refused` row when
the oracle refuses a phase, before or after the phase's own `workflow_finished` row. Shadowed zombie rows are ignored, provided `HAL_ENGINE_SHADOW_EMITS`
stays on.

1. **Rows per phase.** Each phase's row is its last `workflow_finished` row.
2. **Freshness.** Walking `phases` in order, a phase's row is *fresh* when it comes after
   the fresh row of every earlier phase. A rerun of an earlier phase makes the later rows
   stale.
3. **Completed phases.** `completed` is the leading run of phases whose fresh row is `ok`
   or `skip`.
   A phase with a denial or refusal row newer than its `ok` row is not completed.
4. **The deciding phase** is the first phase not in `completed`:
   - a denial or refusal row newer than both the phase's own row and the latest reset →
     `stop`, carrying the deny or refusal code;
   - no phases left → `done`;
   - no row, or only a stale row → `resume` at that phase (this covers a fresh task and the
     state after a reroute);
   - a fresh `error` or `paused` row → `resume` at that phase;
   - a fresh stop-class row → `stop`: status `escalate`, `E_RED_WORKTREE_DIRTY`,
     `E_SPEC_DEFECT_BUDGET`, `E_RESTART_CAP`, or `E_RESTART_SHORT_CIRCUIT`. The last two are defensive, because governor
     denials emit no `workflow_finished`.
   - a fresh `E_SPEC_DEFECT` row → `reroute`, with `resume_from="phase_45_spec"`.
5. **Operator reset.** A stop-class or reroute row that comes before the latest
   `task_cap_reset` row is treated as a plain error, so the next plan is `resume`. This is
   the operator's way out of a sticky `stop`.

**`begin_task_run(state_dir, run_id, events_path, phases, max_runs, max_cost_usd) ->
TaskBegin`** returns `allowed`, `action`, `resume_from`, `completed`, `runs`, `cost_usd`,
`cost_unknown_calls`, `error_code` (why the begin was refused), and `last_status` and
`plan_error_code` (why the plan decided as it did).

- **Charging.** Only `action == "resume"` is allowed and charged. `done`, `stop` and
  `reroute` record nothing and come back with `allowed=False`.
- **Caps.** At the cap (runs ≥ `max_runs`, or known cost ≥ `max_cost_usd`) it refuses with
  `E_TASK_CAP_REACHED` and records nothing.
- **Paused runs.** When the deciding row is `paused` and that row has not already been
  credited, the new run reuses that run's slot and is not charged.
  - The ledger records the credited row's position, so a later refusal cannot make every
    begin free.
  - Order: the cost cap is checked first and always applies. A credited paused row then
    waives only the runs cap and the charge.
- **Cost.**
  - It is `cost_rollup.run_cost` over the rows already read: the same events and fields
    as `compute_cost_rollup`, plus the count of calls with no cost.
  - A missing log is a first run and costs $0.
  - A log that exists but cannot be read, or holds a malformed line, gives
    `E_TASK_COST_UNREADABLE` (read with `EventLog.read_all`, which fails on a malformed line).
  - Rows with no `cost_usd` are counted in `cost_unknown_calls`.
- **Ledger.**
  - The ledger is `task-runs-<run_id>.json` in `state_dir`, written atomically under an
    exclusive lock (`fcntl.flock` on `<ledger>.lock`), so concurrent begins cannot both be
    admitted.
  - A corrupt ledger gives `E_TASK_LEDGER_CORRUPT`. The ledger fails closed because it is a
    spend cap; the restart governor, a waste limiter, fails open.
- **What the $60 limits.** It is an admission check between runs, not a limit inside a
  run.

**`reset_task_runs(state_dir, run_id, events_path, reason)`** takes the same lock, appends
a `task_cap_reset` row with the reason to the event log, and then deletes the ledger. The
order means a reset whose row cannot be written keeps the count.

#### CLI

`run.py --task-begin PHASE[,PHASE...] --run-id ID --event-log PATH [--task-max-runs N]
[--task-max-cost-usd X]`:

- The defaults are 3 runs and $60.
- `state_dir` is `Path(--event-log).parent`, like the restart governor.
- It prints the `TaskBegin` fields plus `run_id` as one JSON line.
- Exit codes: 0 when allowed, 1 when not, 2 on a usage error (missing `--run-id` or
  `--event-log`).

`run.py --task-reset REASON --run-id ID --event-log PATH` calls `reset_task_runs`. It exits
0, or 2 on a usage error.

**How the two limits compose.** The restart governor limits re-invocations of one phase
within a run. The task cap limits driver runs of the whole task.

#### `templates/driver-resume.sh`

Usage: `driver-resume.sh RUN_ID PHASES_CSV CTX_JSON EVENT_LOG`.

- The engine command is `$BD_ENGINE_RUN` (default `python3 -m bytedigger_engine.run`).
- It calls `--task-begin` once. Any exit code other than 0 or 1, or output that is not JSON,
  ends the driver with that output.
- It warns when `cost_unknown_calls > 0`, because the dollar cap counts only known cost.
- It reads the JSON `action`:
  - `done` → exit 0;
  - `stop`, `reroute`, or not allowed → exit 1 and run nothing, printing the refusal and
    plan codes.
    - A stop prints the `--task-reset` command.
    - A reroute prints the exact command that runs `resume_from` once with
      `org_config.phase_reroute` set. After that run, the rerouted phase's row is newer than
      the defect row, so the next plan resumes the failed phase.
  - `resume` → run each phase from `resume_from` in order with `--workflow`, the same
    `--run-id`, `--ctx` and `--event-log`, stopping at the first failing phase.

## Acceptance criteria

| AC | Contract | RED |
|---|---|---|
| AC1 | explicit rows: `spec_gates`, `spec_review` = once/1; `satisfaction` = twice/2; no `spec_retry` | `test_ac1_*` |
| AC2 | review retry survives a spent `spec_gates`; review caps on `spec_review` | `test_ac2*` |
| AC3 | spec gates spend `spec_gates`, mark `retry_source`, drop stale structured findings, carry findings text; one helper | `test_ac3*` |
| AC4 | lint FAIL retries with findings; cap terminal; infra rcs terminal | `test_ac4*` |
| AC5 | cite-lint FAIL retries with findings; cap terminal; blindness and infra terminal | `test_ac5*` |
| AC6 | preflight content retries and keeps `prev.data`; any infra finding terminal | `test_ac6*` |
| AC7 | gate-retry prompt shows gate findings, not the stale thread (StepResult `prev`) | `test_ac7*` |
| AC8 | frozen fallback threads `gate_attempts` | `test_ac8*` |
| AC9 | satisfaction loop: retry target, findings, cycle hand-off, cap, fail-closed, AC checklist, multi, fast-path terminal, format failures terminal, degraded terminal | `test_ac9*` |
| AC10 | fix prompt takes a dict `prev`, shows satisfaction findings, drops the early-return rule | `test_ac10*` |
| AC11 | terminal exit invalidates every cycle's sentinels | `test_ac11*` |
| AC12 | `workflow_finished.error_code` on error; paused keeps three keys | `test_ac12*`, `test_gh576_pause_lane.py:111` |
| AC13 | `plan_resume` resume, fresh, done, last row, shadow, freshness after reroute | `test_ac13*` |
| AC14 | stop-class, reroute, paused, reset unsticks | `test_ac14*` |
| AC15 | `begin_task_run` runs and cost caps, unknown cost, paused slot, corrupt ledger, unreadable log, reset, done/stop not charged | `test_ac15*` |
| AC16 | CLI `--task-begin` / `--task-reset` | `test_ac16*` |
| AC17 | driver template | `test_ac17*` |
| AC18 | lite keeps lint terminal | `test_ac18*` |
| AC19 | review fixes: cap invalidates the writer's cache, a crashed lint is terminal, gate retries patch in place, a missing checklist or a fix-less majority FAIL is terminal, the fix prompt cites the satisfaction report, governor denials and oracle refusals stop, the stop reason is reported | `test_ac2b`, `test_ac4b`, `test_ac4d`, `test_ac6b`, `test_ac7`, `test_ac9i`, `test_ac9j`, `test_ac10`, `test_ac14f`–`test_ac14i` |

## Deliberate sibling updates

- `test_E843349F_recoverable_policy.py`: the `spec_retry` cells become the new gates.
- `test_gh625_restart_budget_split.py`: the AC17 pin moves to `spec_review`.
- `test_phase_45_spec_EECB919C.py`: seed `spec_gates`.
- `test_phase_45_spec_telemetry_D7B5BFB3.py`: seed `spec_review`.
- `test_EECA708D_reject_reason_capture.py`: the source check for the literal
  `error_code="E_SATISFACTION_BELOW_THRESHOLD"` must keep matching.
- Tests that pin terminal lint, cite-lint or satisfaction results at the first attempt are
  updated to seed a spent budget or to run with no step context.

## Deferred

- **HAL #1018 port (phase 5 resume after GREEN).**
  - What it needs: the HAL fix changes the orphan-GREEN recovery from HAL GH1626 part D.
    bytedigger has the GH483 marker seam but not that recovery
    (`_orphan_green_recovery_result`, `_resolve_orphan_green_cycle`).
  - Why not here: the fix lives in `phase_5_implement.py`, the zone of the parallel P7 lot
    (#88).
  - What happens meanwhile: resuming phase 5 over an uncommitted GREEN ends in a terminal
    `E_RED_WORKTREE_DIRTY`, which the driver seam treats as `stop`.
  - Follow-up: #110 tracks porting GH1626-D and #1018 together, after #88 merges.
- **New files in a satisfaction fix.** The GH947 surface guard deletes new files that the
  review doc does not name. A satisfaction fix that has to create a file loses it, and the
  loop then ends at the cap. The guard is left unchanged here, because widening what the fix
  worker may create is its own decision.
- **Retry telemetry inside nested re-runs.** Directed repair and lite's terminal wrapper call
  a gate that builds a retry result. Each such call emits `recoverable_gate_attempted
  outcome=retry` although no engine retry follows, so retry counts read high.
- **Fix commit label.** `_invoke_fix_llm` and `_write_fix_artifact` drop `cycle`, so a
  second fix-loop commit is labelled "fix cycle 1" again. It is cosmetic and left as is.

## Rejected

- **Keep `spec_retry` and raise its cap to 2.** Lint findings could still use up the
  reviewer's retry.
- **Thread `gate_attempts` through every phase 6 step.** Ten steps rebuild their data, and
  one missed step silently turns the cap into the engine backstop.
- **Decide resume from the phase sentinels.** They record only successes, so they cannot
  tell `stop` from `resume` from `reroute`.
