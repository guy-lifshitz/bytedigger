# bd#139 — phase 6 runs one reviewer; phase 5 validation rejects are logged with reasons

**Status: FROZEN (RED r1 amended: AC4/5/7/8 wording)** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `_select_reviewers` (`engine_py/bytedigger_engine/workflows/phase_6_review.py:460`),
the one place the reviewer set and its size are decided; every consumer (prompt, aggregator
floor, straggler watchdog) reads its count. Second chokepoint: `reject_log.emit_reject_reason`
(`engine_py/bytedigger_engine/reject_log.py:86`), the one writer of `reject-reasons.jsonl`.
**Side of the seam (decision 2026-07-26 §7.1):** engine. No host mechanism needed.
**Source:** hal-v2#2259 item 2; audit 2026-09-26 `build-forensics.md` N1.

## §1 Problem (measured on `2622727`)

1. **Phase 6 fans out to 3–7 agents.** `_select_reviewers` returns 3 rows for SIMPLE and 6 for
   FEATURE/COMPLEX, +1 devops row when `artifact_type` is set (`engine_py/bytedigger_engine/workflows/phase_6_review.py:460-472`).
   `_build_review_prompt` tells a primary-model orchestrator to "Spawn {reviewer_count} parallel
   pr-review-toolkit sub-agent reviews via the Agent tool" (`:731-733`). Each sub-agent re-reads
   spec, diff and tests. Per the 26.09 audit, phase 6 is ~30% of engine spend, and 200 of 275
   reviewer findings never reached the fix loop (quote filter, `:1213-1218`), so the extra
   reviewers mostly add cost and not findings.
2. **N=1 breaks two consumers today.** The aggregator floor is
   `min_floor = max(2, (expected_reviewers + 1) // 2)` (`:1534`): with one reviewer it demands 2
   role files, so every run ends in `E_INSUFFICIENT_FANOUT` (`:1558`). The straggler watchdog
   arms at `n >= expected_n - 1` (`engine_py/bytedigger_engine/llm_subprocess.py:589`): with `expected_n=1` it arms at zero
   files and kills the only reviewer after `patience_sec`.
3. **Validation rejects are not logged.** `reject_log` has `record_plan_review_reject` (`:145`)
   and `record_satisfaction_reject` (`:163`). `_gate_on_validation`
   (`engine_py/bytedigger_engine/workflows/phase_5_implement.py:6797`) ends a FAIL/UNKNOWN in terminal `E_VALIDATION_FAILED` and writes
   no reason row. The share of gate rounds rejected for script-catchable reasons cannot be
   measured on engine runs.

Already true, not changed here: deterministic checks run before each model gate. Phase 5
steps 5–8 (`verify_red_fails_mechanically`, `verify_red_lint_rules`, `check_red_executable`)
precede `invoke_validation_llm`. `verify_green_passing` / `green_lint` / `green_typecheck` /
`security_lint` precede phase 6. `verify_spec_reality` and cite-lint precede plan review.

## §2 Design

### op1 — one reviewer by default (`engine_py/bytedigger_engine/workflows/phase_6_review.py`)
- New `org_config["review_fanout"]`: `"single"` (default) | `"parallel"`. Any other value →
  step error `E_REVIEW_FANOUT_INVALID` (new code, added to `engine_py/ERROR_CODES.md`).
- `_select_reviewers(complexity, artifact_type, fanout="single")` → `(table, 1)`. The table is
  one composite row. Its checklist covers the dimensions of the six roles: correctness, silent
  failures, test adequacy, type design, simplification, comments. A `devops` line is appended
  when `artifact_type` is truthy. The count stays 1.
  `fanout="parallel"` returns today's rows and counts byte-for-byte.
- In single mode, `_build_review_prompt` replaces the orchestrator ROLE line. The model reviews
  the work itself, writes **one** file `reviews/role-composite.md` in `PER_ROLE_SCHEMA_TEMPLATE`,
  then the aggregated doc. The prompt contains no "Spawn", "parallel" or "Agent tool". The quote
  instruction (`_REVIEW_STABLE_PREFIX`, `:652-661`) is unchanged.
- Aggregator floor: `min_floor = 1 if expected_reviewers == 1 else max(2, (expected_reviewers + 1) // 2)`.
  Parallel floors stay 2/3.
- Watchdog: `_invoke_review_llm` builds `straggler_cfg` only when `reviewer_count >= 2`.
  Otherwise it passes `straggler_cfg=None`.

### op2 — validation rejects logged (`engine_py/bytedigger_engine/reject_log.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py`)
- New `record_validation_reject(build_id, verdict, cycle, findings_text)` → `emit_reject_reason`
  with `phase="phase_5_implement"`, `reason_code` `VALIDATION_FAILED` | `VALIDATION_UNKNOWN`,
  `axes=_extract_axes(findings_text)`, `detail={cycle, verdict, findings_head}`. `findings_head`
  holds the titles of the first ≤5 `### SEVERITY: <CRITICAL|HIGH> — <title>` lines in the
  validation doc, ≤120 chars each.
- `_gate_on_validation` calls it before returning the terminal error. A logging failure is
  swallowed and does **not** change the gate outcome (still terminal `E_VALIDATION_FAILED`).

### Out of scope (§1v)
Quote filter / suspect withholding (`:1213-1218`, `:1668-1888`), satisfaction and decorr
steps, the fix loop, plan-review rounds, `PER_ROLE_SCHEMA_TEMPLATE` text, HAL `/build`
(frozen; its engine copy is not touched), the classifier instrument (lives in the lot, not
the engine).

## §3 Files in scope
Prod: `engine_py/bytedigger_engine/workflows/phase_6_review.py`, `engine_py/bytedigger_engine/lib/plugins/review_schema/canonical.py` (single-mode
framing constant), `engine_py/bytedigger_engine/reject_log.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py` (`_gate_on_validation`
only), `engine_py/ERROR_CODES.md`.
RED (new): `engine_py/tests/test_bd139_single_reviewer.py`.
§1a siblings — the RED phase must pin these to `review_fanout="parallel"` where they assert
3/6/+1 or floor 2/3. Those are the only allowed test edits:
`test_e8433b4e_aggregator_partial_floor.py`, `test_ccbb65dc_straggler_watchdog.py`
(`:417-419` asserts `expected_n == 6`), `test_phase_6_review_devops.py` (AC1–AC5 rows),
`test_phase_6_reviewer_suspect_rate_D3492E45.py`, `test_gh1591_fix_gate_boundary.py`. The RED
agent completes the audit with a grep over `engine_py/tests` for `_select_reviewers`,
`reviewer_count`, `expected_reviewers`, `parallel pr-review-toolkit`,
`record_plan_review_reject`, `_gate_on_validation`. It lists any extra file it finds in its
report.

## §4 Acceptance (RED: `engine_py/tests/test_bd139_single_reviewer.py`)
- **AC1** `_select_reviewers(c, None)` → count 1 for c ∈ {SIMPLE, FEATURE, COMPLEX}. With
  `artifact_type="dockerfile"`, count is 1 and the table contains `devops`.
- **AC2** `_select_reviewers(c, a, fanout="parallel")` equals the `2622727` output for all 6
  (c, a∈{None,"dockerfile"}) pairs (literal expected tuples in the test).
- **AC3** Single-mode review prompt: no `Spawn`, no `parallel`, no `Agent tool`. It contains
  `role-composite.md` and the `> path:line:` quote instruction. The parallel-mode prompt still
  contains `Spawn 6 parallel` for FEATURE.
- **AC4** `review_fanout="bogus"` → `build_review_prompt` step status error, `E_REVIEW_FANOUT_INVALID`. The code
  exists in `ERROR_CODES.md`.
- **AC5** Aggregator, expected 1: one valid `role-composite.md` → ok (today: `E_INSUFFICIENT_FANOUT`, floor 3); zero role files →
  the existing `E_NO_ROLE_FILES` (unchanged, guard). Expected 6 → floor 3 (unchanged).
- **AC6** `_invoke_review_llm` in single mode calls the LLM subprocess with
  `straggler_cfg=None`. In parallel FEATURE it passes `expected_n == 6`. Assert on the kwargs
  the real `invoke_llm_subprocess` receives (patch at the call boundary, not the UUT).
- **AC7 (side effect, §1l)** Run the phase 6 review steps 1–4 on a tmp scratchpad with a fake
  LLM command, a real executable script that writes `reviews/role-composite.md` with one HIGH
  finding quoting a real line of a tmp file. `reviews/build-review.md` on disk then contains
  that finding as verified. The same run with a script that writes nothing ends
  `E_NO_ROLE_FILES`.
- **AC8 (side effect, §1l)** `_gate_on_validation` with a FAIL validation doc and `HAL_REJECT_LOG`
  (the override `resolve_reject_log_path` honours) set to a tmp file appends exactly one JSON row to it with
  `phase=="phase_5_implement"`, `reason_code=="VALIDATION_FAILED"`, `detail.cycle`, and
  `detail.findings_head` (≤5 items, each ≤120 chars). It still returns `E_VALIDATION_FAILED`.
  UNKNOWN → `VALIDATION_UNKNOWN`. PASS writes no row.
- **AC9** If the reject log is unwritable (path is a directory), `_gate_on_validation` still
  returns `E_VALIDATION_FAILED` and raises nothing.

**Turns-red map (a guard that cannot redden is not a guard):** AC1 reddens if the default stays
`"parallel"`. AC2 reddens if parallel rows drift. AC5 reddens if `max(2, …)` is kept. AC6
reddens if the watchdog guard is dropped. AC7 reddens if single mode still delegates (no file
is written by the outer prompt contract) or the floor is wrong. AC8/AC9 redden if the call is
missing or unguarded.

## §5 Effect (PR «Эффект»)
- **Agents per phase 6 run:** 3/6/7 → 1 (structural, from `_select_reviewers`).
- **Phase 6 cost:** token-ledger on the same task before and after (lot-1964 pins the BD arm at
  `2622727` for #2208; the after-run is separate).
- **Gate rounds rejected for script-catchable reasons:** baseline from the lot's classifier
  over HAL gate verdicts. Post-merge, the same classifier runs over `reject-reasons.jsonl` rows
  from op2.
