# ByteDigger /build Pipeline — Compact Reference

**Full guide:** `phases/` directory | **Phase details:** `phases/phase-*.md`

## PHASE 0: CLASSIFY + INIT

**First — render the host companion**, before `build-state.yaml` or `build-metadata.json` is written. This file is opened with Read, so `${CLAUDE_PLUGIN_ROOT}` below is not substituted: use the absolute plugin root the loaded skill shows in its place.
```bash
BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger
```
- Exit 0: keep following the loaded skill; apply each `bd:local begin/end` block in stdout as an addition to the section it names (no block = no companion).
- Exit 3: STOP. Report stderr to the user and register nothing (no `build-state.yaml`, no `build-metadata.json`).
- Any other exit: use the core skill as loaded and print one line: `W_SKILL_COMPANION_UNAVAILABLE exit=<n> — host companion NOT applied`.

Create `build-state.yaml`: `task | complexity (PENDING) | mode | current_phase: "0" | files_modified: []`

**Classification:** TRIVIAL (<10L) → direct edit | SIMPLE (bug fix ONLY — no new functionality, clear root cause) → AUTONOMOUS | FEATURE (any new behavior/capability — DEFAULT for ambiguous) → AUTONOMOUS | COMPLEX (4+ files, architecture, cross-cutting) → SUPERVISED. **Guard: user says "feature/add/create/implement/build" → NEVER SIMPLE.**

**Issue intake + readiness (bd#117):** Parse `--issue <N>`; run `bash scripts/readiness check --stage start --json` only to read `readiness.required` (exit 3 is expected here — no spec record yet, or not yet on the `gh<N>-` branch — and is not a stop: read the JSON, continue; exit 4 → warn, continue). When `readiness.required` is true: no issue → STOP (`no_issue`) before `build-state.yaml` is written; `--issue <N>` on a branch that parses to another number → STOP (`issue_mismatch`); otherwise the build runs on branch `gh<N>-<slug>` for every tier, worktree or not.

**Start gate (only when `readiness.required` is true):** before the first write outside the scratchpad — TRIVIAL: after writing a minimal `build-spec.md` (`Task | Files | Change`), before the direct edit | SIMPLE/FEATURE/COMPLEX: after Phase 4.5. Run `bash scripts/readiness check --stage start --spec ./build-spec.md`: **0** → continue | **3** → `bash scripts/readiness post --spec ./build-spec.md`, set `current_phase: awaiting_approval` + `awaiting_stage: start`, print `Waiting for "<label>" on #<N>`, STOP — in every mode, AUTONOMOUS included | **anything else** → warn with the stderr line, continue (the ship gate still holds). Every readiness STOP prints the verdict line and, for `no_spec_record` / `label_predates_spec`, the recovery: `scripts/readiness post --spec ./build-spec.md`, then a human adds `<label>`, then `/build continue`.

**Rules:** Every phase RUNS. Code/review/test = always agent. No skipping. **SYNTHESIS RULE:** Before spawning implementation workers, orchestrator MUST read the spec (`build-spec.md`) and write prompts with exact file paths + line numbers. Never "based on findings".

**WORKER LIFECYCLE MATRIX:**
| Situation | Action | Why |
|---|---|---|
| Same step, worker returned NEEDS_CONTEXT | SendMessage(to: worker_name) | Reuse context |
| Same step, worker returned DONE | Agent(name: new) | Context released |
| RED done → GREEN | Agent(name: "green-worker") | Different role, fresh context |
| Phase N done → Phase N+1 | Agent(name: new) | Cross-phase = always fresh |
| Worker not returned yet | WAIT — do not spawn duplicate | Race condition |
| Worker returned with no completion marker | Agent(name: new) with RE-ANCHOR | Previous worker truncated |

**Decision rule:** Check build-state.yaml BEFORE spawning. If `{step}_complete` exists → previous worker done → spawn fresh. If not → worker still active or lost.

---

**AUTONOMOUS MODE ENFORCEMENT:** When mode = AUTONOMOUS, proceed through ALL phases without stopping. Do NOT pause between phases, do NOT ask for user confirmation, do NOT present intermediate results for approval. The only valid stops are: (1) a gate HARD BLOCK (exit non-zero), (2) pipeline completion, (3) unrecoverable error. **SUPERVISED mode:** pause after each phase for user review.

> **PAUSE-POINT HARD RULE:** Every pause/wait/approval point in this document is wrapped in an explicit mode check below. If you add a new pause point anywhere in the pipeline, you MUST wrap it in `if mode == AUTONOMOUS / SUPERVISED`. There are NO implicit pauses. **One named exception:** the readiness start gate (Phase 0 / 4.5) STOPs at `awaiting_approval` in every mode, AUTONOMOUS included, keyed on `readiness.required` — not on mode. When reading `mode` from build-state.yaml, strip surrounding quotes before comparing (see `yaml_parser_quote_strip` pattern: `sed "s/^['\"]//;s/['\"]$//"` or equivalent).

---

**Scratchpad:** Phase 0 creates `.bytedigger/` (in project CWD) with subdirs (research/, architecture/, specs/, tests/, reviews/). Workers write findings to scratchpad files. Path stored in `build-state.yaml` as `scratchpad_dir`. **Scratchpad Health:** Every phase verifies scratchpad_dir exists before proceeding. If missing, it recreates the directory structure automatically. Persists with worktree, survives reboots.

**Hooks:** Two PreToolUse hooks are registered in `hooks/hooks.json`. `hooks/build-state-guard.sh` (Bash) applies to every caller, the main thread included. While `build-state.yaml` has a `current_phase` other than `7` or `completed`, it rejects Bash commands that `rm` or `unlink` `build-state.yaml` or `.bytedigger-orchestrator-pid` (a protected file that Phase 0 creates and Phase 7 removes), and `rm -r .` variants. `hooks/worker-write-guard.sh` (runs `hooks/worker_write_guard.py`; Write, Edit, MultiEdit, NotebookEdit and Bash) is active while a build is active. It recognises subagents by a non-empty `agent_id` field in the hook's stdin JSON and stops them from writing `build-state.yaml`, `build-metadata.json`, `build-red-output.log`, `build-green-output.log` and `.bytedigger-orchestrator-pid`. It also confines the synthesizer to `<scratchpad>/reviews/`. See `docs/security.md` for detail. No hook blocks the orchestrator from editing code; that rule is in the prompt only.

**Outputs:** Complexity + mode | Project context | Active phases (all tiers: 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7) | Model allocation (from `bytedigger.json`) | **Immutable metadata:** Write `build-metadata.json` with complexity + mode + created_at. This file prevents complexity downgrade bypass — never modify after Phase 0. | Dependency pre-check: lock file + quick validation → `deps_checked` to build-state.yaml (soft, never blocks)

**--dry-run flag:** Display table → STOP

**Resumable:** First run `BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger` again (absolute plugin root and exit rules as in the first Phase 0 step). Then, if `build-state.yaml` exists + `current_phase != "completed"` → read, skip to next. If it does not exist, look for `.bytedigger/worktrees/*/build-state.yaml` with `current_phase` != "completed"; for each one print `Build state is in worktree <path>: cd there and run /build continue` and stop. On resume, verify scratchpad_dir exists. If missing, recreate directory structure and continue (the engine no longer reads research findings). A stale `current_phase` of 1-4 (a build started before the discovery/explore/clarify/architect phases were dropped) resumes at Phase 4.5: the gates pass 1-4 through. If `current_phase: awaiting_approval`: `awaiting_stage: start` → re-run the start gate (0 → next phase); `awaiting_stage: ship` → re-run only SHIP (7.1c), no re-implementation, then the rest of Phase 7.

**Worktree:** If `--worktree` or FEATURE+ on main → `git worktree add` then **MUST move the state files to the worktree**: `mv build-state.yaml <worktree-path>/` and `mv build-metadata.json <worktree-path>/` (move, so the main checkout keeps no stale state). Without state file, ALL gates are blind and pipeline runs unprotected.

## PHASE 4.5: SPEC GENERATION (all tiers)

There are no discovery, exploration, clarification or architecture phases. The spec writer reads the codebase itself; its repo-facts input is the deterministic facts pack. Cost scales with the tier's knobs (spec length, reviewer count, timeouts), not with a second path.

Sonnet writes `build-spec.md`: User Stories (min 2, BDD) | Files (CREATE/MODIFY) | Interfaces | Data Model | Behavior | Tests

**Plan-Review Gate (MANDATORY, every tier):** Separate Opus reviewer → SHIP or REVISE (max 2 cycles) → Write `plan_review: pass` to build-state.yaml

**Phase 5 entry blocks** on missing `plan_review: pass`

**Orchestrator flow:**
1. Read `mode` from build-state.yaml (strip quotes — `sed "s/^['\"]//;s/['\"]$//"`)
2. If mode == "AUTONOMOUS": write spec, run Plan-Review Gate (gate is always mandatory — it's automated, not interactive), write `plan_review: pass`, proceed to Phase 5 — no user wait
3. If mode == "SUPERVISED": write spec, present for review, wait for user to return, apply feedback, then run Plan-Review Gate, then proceed

## PHASE 5: IMPLEMENTATION (TDD — MANDATORY, no exceptions)

**TEST INTEGRITY (applies to ALL phases that touch tests):** Tests verify REAL behavior against spec. If a test fails → fix the CODE, not the test. NEVER adjust assertions to match broken behavior. Only change tests if the SPEC changed. Include this rule verbatim in every worker prompt that runs or fixes tests.

**Entry Gate:** Verify `plan_review: pass` (every tier)

**5.0 Test Infrastructure:** Detect framework (jest, pytest, XCTest). If missing, create. **Run existing tests first.** If pre-existing tests fail → fix them immediately as Boy Scout Rule (don't ask user, don't skip). Log as `PRE-EXISTING FIX` in Boy Scout Report.

**5.1 RED (tests first):** Generate tests (use `name: "tdd-worker"`, read `build-spec.md`) → `<test-cmd> 2>&1 | tee build-red-output.log` (MUST contain ≥1 FAIL/ERROR). **RED agent returns normally with summary** (test names, failure count, file paths) ending with `RED COMPLETE — [N] tests written, all failing. Files: [list]`. Orchestrator captures output for Opus validation + GREEN worker context. **After RED returns → agent is DONE. Do NOT SendMessage to it.** Model: SIMPLE=Haiku | FEATURE=Sonnet | COMPLEX=Opus (configurable via `bytedigger.json`)

**5.2a Gherkin (Sonnet, ALL tiers):** Generate `./build-tests.md` — BDD Gherkin scenarios. SIMPLE=1–2 scenarios, FEATURE/COMPLEX=full suite. Writer only, no validation. Write `phase_52a_gherkin: complete` to build-state.yaml.
**5.2b Validation (Opus HARD GATE, ALL tiers):** Opus 4-step audit on Gherkin vs test code: Forward Map | Reverse Map | Spec Compliance | Quality Checks → PASS (write `opus_validation: pass`) or FAIL (fix test code, re-validate). Opus is validator only — does NOT write or modify tests.

**5.2c Plan-Sync (after each Task agent):** Check drift from spec (renamed functions, API changes) → Update spec + downstream prompts → Log: `plan_drift: [...]`

**5.3 GREEN (implement):** Verify `opus_validation: pass` checkpoint exists. **Spawn fresh GREEN worker IMMEDIATELY** (`name: "green-worker"`) — NEVER attempt SendMessage to tdd-worker (it has completed). Pass full context: spec + test files + RED output summary + Opus feedback. **Boy Scout Checklist (MANDATORY every file):** Remove dead imports | Clear names | Add types | Remove unused vars | Fix formatting. Agent MUST output BOY SCOUT REPORT per file. Run tests → PASS | Model: Sonnet (configurable via `bytedigger.json` → `agent_model`). **ALL severities fixed — LOW included, no filtering.**

**5.5 TEST INTEGRITY DIFF GUARD (MANDATORY):** After GREEN, diff test files (RED vs current). If tests modified → Opus classifies each change: SPEC_CHANGE (pass) | LEGITIMATE_REFACTOR (pass) | ASSERTION_GAMING (BLOCK — revert test, fix code). Unmodified tests → auto-pass. Log: `test_integrity_check: pass|fail | assertion_gaming_detected: true|false | test_files_modified_after_red: N`

**COMPLEX Worker Dispatch:** Decompose spec (1-3 files/task) → Fresh Task agent per task (`run_in_background: true`) → Minimal context → Conflict detection → Integration tests → Max turns: tests=40, impl=50, Opus=10

**WORKTREE GUARD (if worktree_path set in build-state.yaml):**
- Workers MUST edit ONLY files inside the worktree path, NOT the main checkout
- Pass worktree context to every worker prompt: "You are working in a git worktree at [worktree_path]. Edit ONLY files at this path. The main checkout is at a DIFFERENT location — do NOT touch it."
- Tests MUST use relative paths from CWD (the worktree), not hardcoded absolute paths
- Validate: after worker spawn, first action must confirm CWD matches worktree_path

**State log:** `phase_5_implement: in_progress|complete | opus_validation: pass | phase_5_files_changed: <N> | phase_5_workers_done: <N>/<total>` (COMPLEX only)

## PHASE 6: QUALITY REVIEW

**Entry Gate:** `phase_5_implement: complete` + `opus_validation: pass` exist

**6.1 Reviewer Count (EXACT agents mandatory):**
**→ See `templates/dynamic-context.md`** for Review Agent Roster (agent config loaded as attachment, not cached).
If launched ≠ expected → STOP

**6.2 Fix ALL Findings (ZERO EXCEPTIONS):**

Boy Scout Rule EXPANDED: Fix code you wrote + code you reviewed + pre-existing in touched files + adjacent files read. Codebase MUST be cleaner.

**Forbidden:** "acceptable" | "pre-existing" | "low severity, skip" | "cosmetic" | "fix later" | "not related"

Process: Collect ALL issues (single list) → Task agent fixes EVERY finding → Re-run reviewers → Max 2 cycles → Log: `phase_6_findings_total: <N> | phase_6_findings_fixed: <N> | phase_6_findings_skipped: 0`

**Post-Review Gate:** Verify `phase_6_findings_skipped == 0` in build-state.yaml. If any findings were skipped → **PIPELINE STOPS (non-negotiable)**.

**6.3 Satisfaction (Opus HARD GATE):**
**→ See `templates/dynamic-context.md`** for Satisfaction Scoring table (thresholds, auditors, dimensions).
**On satisfaction ≥ threshold:** Write `review_complete: pass` to build-state.yaml (mandatory for Phase 7)

**State log:** `phase_6_review: in_progress|complete | review_complete: pass | review_satisfaction: <PCT>% | review_issues_found: <N>`

## PHASE 7: SYNTHESIZE

**Entry Gate:** `review_complete: pass` exists

**7.1 Summary:** The engine writes `post-deploy/post-deploy-report.md` and `reviews/learnings-raw.md` deterministically from the event log and the scratchpad docs (no model). **AUTONOMOUS:** log the report path only, do NOT stop for user review. **SUPERVISED:** present the report to the user before proceeding.

**Orchestrator flow:**
1. Read `mode` from build-state.yaml (strip quotes — `sed "s/^['\"]//;s/['\"]$//"`)
2. If mode == "AUTONOMOUS": log the report path, proceed immediately to the next step — no pause to present to user
3. If mode == "SUPERVISED": present the report to user (What was built + learnings bullets), wait for acknowledgement, then proceed

**7.1b Learning Extraction:** After the report is written, run `bash scripts/learning-store.sh extract "$SCRATCHPAD"` — persists `reviews/learnings-raw.md` to `.bytedigger/learnings/`. Writes `learnings_extracted: <N>` to build-state.yaml. Gracefully exits 0 on any error.

**7.1c SHIP (if `--pr`):** `bash scripts/ship.sh --pr --state ./build-state.yaml` — commit, push, PR. It runs `readiness check --stage ship` first (verdict + consumption), so nothing leaves the machine unapproved. It ships every commit ahead of the base plus tracked changes (no hand-maintained file list needed) and writes the PR title and body from the spec and the review fields. When `required: true` (from the Phase 0 readiness read), any non-zero exit is STOP: set `current_phase: awaiting_approval` and `awaiting_stage: ship`, keep `build-state.yaml` and `build-metadata.json`, skip 7.2, print the verdict line and — for `no_spec_record` / `label_predates_spec` — the recovery `scripts/readiness post --spec ./build-spec.md`, then a human adds `<label>`, then `/build continue`. When `required: false`: best-effort, log a warning and continue.

**7.2 State Cleanup:** Delete `build-state.yaml` | `build-tests.md` | `build-red-output.log` | `build-green-output.log` | `build-opus-validation.md` | `build-metadata.json` (on FAILED, keep build-state.yaml/build-metadata.json for `/build continue`). **Cleanup rule:** Delete only transient scratchpad subdirs (`research/`, `architecture/`, `specs/`, `tests/`, `reviews/`) — do NOT `rm -rf` the entire scratchpad dir. This preserves `.bytedigger/learnings/` for future builds.

## PHASE 8: POST-DEPLOY

Runs after SHIP. Non-blocking — errors are logged but never stop the pipeline. Invoke via `bash scripts/post-deploy.sh --cwd "$(pwd)" --state-file ./build-state.yaml`.

Steps: (1) `git fetch --prune` — prune remote refs. (2) Delete gone branches (`git branch -vv | grep ': gone]'`). (3) Remove `.bytedigger-*` temp files older than 24h. (4) Remove merged worktrees (`git worktree remove`). Writes `phase_8_cleanup: complete` to state file.

**State fields:** `phase_8_cleanup: complete`

## build-state.yaml Fields

`task | complexity (SIMPLE|FEATURE|COMPLEX) | mode (AUTONOMOUS|SUPERVISED) | current_phase | files_modified: [] | forge_run_id | started_at | last_updated | spec_path: ./build-spec.md | test_spec_path: ./build-tests.md | worktree_path | constitution_loaded: true|none | security_classification: HIGH|MEDIUM|LOW | security_patterns_found: [] | plan_review: pass | phase_5_started: "<ISO timestamp>" | phase_5_implement: complete | phase_51_red: complete | phase_52a_gherkin: complete | phase_52_validation: complete | phase_53_green: complete | opus_validation: pass | test_integrity_check: pass | assertion_gaming_detected: true/false | plan_drift: [...] | test_files_modified_after_red: <count> | phase_5_files_changed | phase_5_workers_done (COMPLEX only) | phase_6_reviewers_launched | phase_6_reviewers_expected: <3|4|6|7> | security_review_enabled: true/false | phase_6_findings_total | phase_6_findings_fixed | phase_6_findings_skipped: 0 | review_complete: pass | review_satisfaction: <PCT>% | review_issues_found | semantic_skip_check: pass|fail | semantic_skip_phrases_found: <N> | post_review_gate: pass | deps_checked: true | deps_issues: "<summary if issues found>" | deps_lock_missing: true (if lock file missing) | learnings_injected: <N> | learnings_extracted: <N> | learning_skip_reason: disabled|error|sqlite_unavailable | learning_backend: none|file|sqlite`

## Model Allocation Table
**→ See `templates/dynamic-context.md`** for full Model Allocation table (loaded as attachment, not cached).
Models are configurable via `bytedigger.json`.

## Gates — Hook Enforcement (scripts/build-gate.sh)

**Total: 9 gates across 7 phases. All enforced via SubagentStop hook.**

| Gate | Phase | Check | Type |
|------|-------|-------|------|
| Security Classification | 0 | security_classification in state | Soft |
| Plan-Review | 4.5 | plan_review: pass | Soft |
| RED Output | 5.1 | build-red-output.log exists + phase_51_red: complete | Soft |
| Opus Validation | 5.2 | opus_validation: pass | Soft |
| GREEN Output | 5.3 | build-green-output.log + phase_53_green: complete | Soft |
| Test Integrity | 5.5 | test_integrity_check + test_files_modified_after_red + Opus evidence + RED baseline + cross-validation | Soft |
| Findings Fixed | 6 | findings_total == findings_fixed | Soft |
| Semantic Skip | 6 | forbidden phrases in reviews + cross-validate vs findings_skipped | Soft |
| Post-Review | 6 | unfixed findings hard block + semantic skip content scan | Hard |

**Soft blocks:** MISSING_FIELDS → agent cannot stop until fixed (3 attempts, then circuit breaker with audit trail)
**Hard blocks:** exit 1 → pipeline stops (non-negotiable)

## Common Mistakes (Avoid)

Skip test framework setup | Tests pass in RED (tests wrong — fix first) | Missing `plan_review: pass` before Phase 5 (every tier) | Missing `opus_validation: pass` before GREEN | Boy Scout violations | Reviewer count mismatch (SIMPLE=3, FEATURE/COMPLEX=6) | Skipping findings ("acceptable"/"pre-existing") | Post-review gate failure → STOP (no workarounds)

## Worker Output Schema

All worker agents MUST end their final report with structured fields:

```
Scope: [what was examined/changed]
Result: [outcome — PASS/FAIL/findings summary]
Key files: [files read or relevant]
Files changed: [files modified, or "none"]
Issues: [problems found, or "none"]
```

Workers: Do NOT emit text between tool calls. Use tools silently, then report once at the end using this schema.

Phase-specific additions:
- Phase 5 (RED/GREEN): output schema fields, then completion marker as absolute final line (`RED COMPLETE — ...` / `GREEN COMPLETE — ...`)
- Phase 6 (Review): output schema fields, then `VERDICT: PASS/FAIL/PARTIAL` as absolute final line
- Phase 7 (Synthesize): output schema
- SUPERVISED phase 4.5: "work silently" does not suppress interactive user Q&A
- Opus validators (Step 2b): exempt from "work silently" — reasoning must be visible

## Agent Status Protocol
All agents return: `STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED`
- DONE → proceed | CONCERNS → log (AUTO) or show (SUPERVISED) | NEEDS_CONTEXT → retry (max 2) | BLOCKED → STOP

## Thresholds
| Metric | Value |
|--------|-------|
| Test fix cycles | 3 max |
| Review fix cycles | 2 max |
| Satisfaction SIMPLE | ≥80% |
| Satisfaction FEATURE | ≥85% |
| Satisfaction COMPLEX | ≥90% |
| NEEDS_CONTEXT retries | 2 max |
| Stale build-state.yaml | >24h warn |
| Stale worktree | >7 days safe to remove |
