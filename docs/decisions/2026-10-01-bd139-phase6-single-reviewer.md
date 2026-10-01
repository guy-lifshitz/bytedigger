# bd#139 — phase 6 runs one reviewer; phase 5 validation rejects are logged with reasons

**Status: FROZEN r2 (gate r1 REJECT → 4 blockers addressed, see `2026-10-01-bd139-gate-r1.md`)** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** SYSTEMATIC ·
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
- New `org_config["review_fanout"]`: `"single"` (default) | `"parallel"`. Missing, `None` or
  `""` → `"single"`. The value is compared after `.strip().lower()`. Any other value →
  step error `E_REVIEW_FANOUT_INVALID` (new code, added to `engine_py/ERROR_CODES.md`).
- `_select_reviewers(complexity, artifact_type, fanout="single")` → `(table, 1)`. The table is
  one composite row. Its checklist covers the dimensions of the six roles: correctness, silent
  failures, test adequacy, type design, simplification, comments. A `devops` line is appended
  when `artifact_type` is truthy. The count stays 1.
  `fanout="parallel"` returns today's rows and counts byte-for-byte.
- In single mode, `_build_review_prompt` replaces the orchestrator ROLE line. The model reviews
  the work itself, writes **one** file `reviews/role-composite.md` in `PER_ROLE_SCHEMA_TEMPLATE`,
  then the aggregated doc. The prompt contains no "Spawn", "parallel" or "Agent tool". The quote
  instruction (`_REVIEW_STABLE_PREFIX`, `:652-661`) is unchanged. The prompt carries the composite
  table verbatim: all six dimension names, and the devops focus text (`CIS/OWASP/SLSA`) only when
  `artifact_type` is truthy.
- The other sub-agent-addressed blocks of `_build_review_prompt`
  (`engine_py/bytedigger_engine/workflows/phase_6_review.py:849-918`) get a single-mode wording. These are the 7CA211D2
  prior-findings block (`:858`, "Each dispatched Agent MUST read …") and the SECURITY ADDENDUM
  (`:913`). In single mode they address the reviewer itself, for example "Before reviewing, read the
  prior findings file …", and contain none of `dispatched Agent`, `sub-agent`, `Agent call`,
  `Spawn`. Parallel wording is unchanged.
- Stale file guard: in single mode, `_invoke_review_llm` deletes `reviews/role-composite.md`, if
  present, before invoking the LLM. A file from an earlier cycle, or from a run resumed after a
  mode switch, therefore cannot satisfy floor 1. Parallel mode is unchanged (pre-existing
  behaviour, §1v).
- Aggregator floor: `min_floor = 1 if expected_reviewers == 1 else max(2, (expected_reviewers + 1) // 2)`.
  Parallel floors stay 2/3.
- Watchdog: `_invoke_review_llm` builds `straggler_cfg` only when `reviewer_count >= 2`.
  Otherwise it passes `straggler_cfg=None`.

### op2 — validation rejects logged (`engine_py/bytedigger_engine/reject_log.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py`)
- New `record_validation_reject(verdict, cycle, findings_text, reason_code=None)` →
  `emit_reject_reason`. Like its siblings, it resolves `build_id` from `telemetry_ctx`. With no
  current run the row is suppressed (`engine_py/bytedigger_engine/reject_log.py:114` behaviour). It writes
  `phase="phase_5_implement"` and `reason_code` `VALIDATION_FAILED` | `VALIDATION_UNKNOWN` |
  `VALIDATION_SPEC_DEFECT`. `verdict` is the verdict `_gate_on_validation` itself acts on
  (`prev.data["verdict"]`). The row also carries
  `axes=_extract_axes(findings_text)`, `detail={cycle, verdict, findings_head}`. `findings_head`
  holds the titles of the first ≤5 `### SEVERITY: <CRITICAL|HIGH> — <title>` lines in the
  validation doc, ≤120 chars each.
- **Every rejected round is logged, not only the terminal one** (gate r1 B4, option a).
  `_gate_on_validation` calls it on every non-PASS outcome:
  - the retry branch `cycle < cap` (status ok, next cycle);
  - the terminal `E_VALIDATION_FAILED`;
  - the spec-defect exits `E_SPEC_DEFECT` / `E_SPEC_DEFECT_BUDGET`, with reason
    `VALIDATION_SPEC_DEFECT`.

  `detail.cycle` is the cycle that was rejected. A logging failure is swallowed and does **not**
  change the gate outcome (status, error code, next cycle).
- `reject_log` module docstring phase list (`engine_py/bytedigger_engine/reject_log.py:10`) gains `phase_5_implement`.

### Out of scope (§1v)
Clearing stale role files in parallel mode; quote filter / suspect withholding (`:1213-1218`, `:1668-1888`), satisfaction and decorr
steps, the fix loop, plan-review rounds, `PER_ROLE_SCHEMA_TEMPLATE` text, HAL `/build`
(frozen; its engine copy is not touched), the classifier instrument (lives in the lot, not
the engine).

## §3 Files in scope
Prod: `engine_py/bytedigger_engine/workflows/phase_6_review.py`, `engine_py/bytedigger_engine/lib/plugins/review_schema/canonical.py` (single-mode
framing constant), `engine_py/bytedigger_engine/reject_log.py`, `engine_py/bytedigger_engine/workflows/phase_5_implement.py` (`_gate_on_validation`
only), `engine_py/ERROR_CODES.md`.
RED (new): `engine_py/tests/test_bd139_single_reviewer.py`.
§1a siblings. The only allowed test edits pin `review_fanout="parallel"` /
`fanout="parallel"` where a test asserts the old default (3/6/+1 reviewers, floor 2/3, non-None
`straggler_cfg`, orchestrator or sub-agent prompt text).
- **Pinned:** `test_e8433b4e_aggregator_partial_floor.py`, `test_ccbb65dc_straggler_watchdog.py`,
  `test_phase_6_rubric_trim_5D0D3BD1.py`, `test_phase_6_subagent_prior_context_propagation_7ca211d2.py`.
- **To pin (gate r1 B1):** `test_bd82_role_backend_effort.py` (`:184`, `:207`) and
  `test_CF2EE8ED_in_session_cutover.py` AC3.
- **Checked, no pin needed:** `test_phase_6_reviewer_suspect_rate_D3492E45.py`,
  `test_gh1591_fix_gate_boundary.py`.
- **Tests that assert `_gate_on_validation` returns no side effect:** any that now see a row
  must set `HAL_REJECT_LOG` to tmp. Rows are only written with a current telemetry run.

Audit grep over `engine_py/tests`: `_select_reviewers`, `reviewer_count`, `expected_reviewers`,
`expected_n`, `straggler_cfg`, `straggler_abort`, `parallel pr-review-toolkit`, `Spawn `,
`dispatched Agent`, `SECURITY ADDENDUM`, `role-*.md`, `record_plan_review_reject`,
`_gate_on_validation`, `reject-reasons`.

## §4 Acceptance (RED: `engine_py/tests/test_bd139_single_reviewer.py`)
- **AC1** `_select_reviewers(c, None)` → count 1 for c ∈ {SIMPLE, FEATURE, COMPLEX}. With
  `artifact_type="dockerfile"`, count is 1 and the table contains `devops`.
- **AC2** `_select_reviewers(c, a, fanout="parallel")` equals the `2622727` output for all 6
  (c, a∈{None,"dockerfile"}) pairs (literal expected tuples in the test).
- **AC3** Single-mode review prompt: no `Spawn`, no `parallel`, no `Agent tool`. It contains
  `role-composite.md` and the `> path:line:` quote instruction. The parallel-mode prompt still
  contains `Spawn 6 parallel` for FEATURE.
- **AC3b** Single mode, with `reviews/last_findings.json` present and
  `security_classification="HIGH"`. The prompt contains the absolute `last_findings.json` path and
  `PRIOR — still present`. It contains none of `dispatched Agent`, `sub-agent`, `Agent call`,
  `Spawn`. Parallel mode with the same inputs still contains `Each dispatched Agent MUST read`.
- **AC3c** The single-mode prompt names all six dimensions: correctness, silent failures, test
  adequacy, type design, simplification, comments. With `artifact_type="dockerfile"` it contains
  `CIS/OWASP/SLSA`; without it, it does not. In both cases it names `role-composite.md` and no
  `role-devops` file.
- **AC4b** `review_fanout` ∈ {missing, `None`, `""`, `" Single "`} → single mode (count 1).
  `"PARALLEL"` → parallel.
- **AC4** `review_fanout="bogus"` → `build_review_prompt` step status error, `E_REVIEW_FANOUT_INVALID`. The code
  exists in `ERROR_CODES.md`.
- **AC5** Aggregator, expected 1: one valid `role-composite.md` → ok (today: `E_INSUFFICIENT_FANOUT`, floor 3); zero role files →
  the existing `E_NO_ROLE_FILES` (unchanged, guard). Expected 6 → floor 3 (unchanged).
- **AC6** `_invoke_review_llm` in single mode calls the LLM subprocess with
  `straggler_cfg=None`. In parallel FEATURE it passes `expected_n == 6`. Assert on the kwargs
  the real `invoke_llm_subprocess` receives (patch at the call boundary, not the UUT). The test
  pins `HAL_RUNNER_BACKEND`/`HAL_RUNNER_BACKEND_JUDGE=claude-subprocess`, so the in-session degrade
  (`:993`) cannot mask it.
- **AC6b** Single mode with a stale `reviews/role-composite.md` before `_invoke_review_llm`: a
  fake LLM that writes nothing ends `E_NO_ROLE_FILES` in the aggregator, not ok.
- **AC7 (side effect, §1l)** Run the phase 6 review steps 1–4 on a tmp scratchpad with a fake
  LLM command, a real executable script that writes `reviews/role-composite.md` with one HIGH
  finding quoting a real line of a tmp file. `reviews/build-review.md` on disk then contains
  that finding as verified. The same run with a script that writes nothing ends
  `E_NO_ROLE_FILES`. `build-review.md` contains `expected: 1` and `missing: (none)`.
- **AC8 (side effect, §1l)** `_gate_on_validation` with a FAIL validation doc and `HAL_REJECT_LOG`
  (the override `resolve_reject_log_path` honours) set to a tmp file appends exactly one JSON row to it with
  `phase=="phase_5_implement"`, `reason_code=="VALIDATION_FAILED"`, `detail.cycle`, and
  `detail.findings_head` (≤5 items, each ≤120 chars). It still returns `E_VALIDATION_FAILED`.
  UNKNOWN → `VALIDATION_UNKNOWN`. PASS writes no row.
- **AC8b (per round)** FAIL at `cycle=1 < cap` appends one row with `detail.cycle == 1`. The
  result is still status ok, with the next cycle `== 2`, exactly as without logging.
- **AC8c** A spec-defect exit (`E_SPEC_DEFECT`) appends one row with
  `reason_code == "VALIDATION_SPEC_DEFECT"`. The error code is unchanged.
- **AC8d** No current telemetry run → no row, gate outcome unchanged.
- **AC9** If the reject log is unwritable (path is a directory), `_gate_on_validation` still
  returns `E_VALIDATION_FAILED` and raises nothing. The same holds for the retry branch: status ok,
  same next cycle.

**Turns-red map (a guard that cannot redden is not a guard):** AC1 reddens if the default stays
`"parallel"`. AC2 reddens if parallel rows drift. AC5 reddens if `max(2, …)` is kept. AC6
reddens if the watchdog guard is dropped. AC3/AC3b/AC3c redden if single-mode
wording leaks sub-agent language, or drops the dimensions or the devops focus. AC4b reddens on
naive comparison. AC6b reddens if the stale-file guard is missing. AC7 reddens only on the floor or
the aggregator slug parse; delegation is AC3's job. AC8b/AC8c redden if only the terminal round is
logged. AC8/AC9 redden if the call is
missing or unguarded.

## §5 Effect (PR «Эффект»)
- **Agents per phase 6 run:** 3/6/7 → 1 (structural, from `_select_reviewers`).
- **Phase 6 cost:** token-ledger on the same task before and after (lot-1964 pins the BD arm at
  `2622727` for #2208; the after-run is separate).
- **Gate rounds rejected for script-catchable reasons:** baseline from the lot's classifier
  over HAL gate verdicts. Post-merge, the same classifier runs over `reject-reasons.jsonl` rows
  from op2.
