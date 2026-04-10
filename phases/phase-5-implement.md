> This is Phase 5 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 5: IMPLEMENTATION (TDD)

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"5\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 5')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(python3 -c "import re,pathlib;m=re.search(r'scratchpad_dir:\s*[\"'\'']*([^\"'\''\\n]+)',pathlib.Path('build-state.yaml').read_text());print(m.group(1).strip() if m else '')")
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

Build the feature via Task agents. Never write code directly as orchestrator.

**WORKER AGENT CONSTRAINTS (include in every Task agent prompt):**
- You are a worker inside /build pipeline. Use Edit/Write/Bash directly.
- NEVER call Skill tool (you don't have access, attempts waste turns).
- NEVER invoke /build, /bugfix, or any slash command.
- If stuck, report what's blocking you — don't try to delegate or escalate via tools.
- **Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Do NOT emit text between tool calls — work silently, report once at the end. RED/GREEN workers: output the schema fields first, then end with completion marker (`RED COMPLETE — ...` / `GREEN COMPLETE — ...`) as the absolute final line.
- Exception: Opus validation agents (Step 2b) MUST show reasoning step-by-step — "work silently" does NOT apply to auditors.

## Entry Gate (MANDATORY)

Before starting Phase 5, orchestrator MUST read `build-state.yaml` and verify:
- For FEATURE/COMPLEX: `phase_4_architect: complete` exists. If missing → STOP, run Phase 4 first.
- For FEATURE/COMPLEX: `plan_review: pass` exists. If missing → STOP, run Plan-Review Gate (end of Phase 4.5) first.
- For ALL: `build-state.yaml` exists and has `complexity` field.

## TDD is MANDATORY — No Exceptions

- "SIMPLE can skip Opus validation" → NO
- "Tests are broken" → fix test setup first
- "No test framework" → set one up (Step 0)
- "Manual testing is enough" → NO. Write automated tests.
- If truly impossible (e.g., hardware-only): document WHY, get SUPERVISED approval

### TDD Excuse Rebuttal Table

| Excuse | Rule |
|--------|------|
| "Tests are slow" | Fix the design, don't skip tests |
| "Just a refactor" | Tests PROVE behavior is preserved |
| "Config/env change" | Config affects runtime. Test it. |
| "UI can't be tested" | Test the behavior, mock rendering |
| "Legacy code has no tests" | Add tests for YOUR changes |
| "Deadline pressure" | Untested bugs cost MORE time |
| "It's a prototype" | Prototypes become production |
| "External API" | Mock the boundary, test YOUR code |
| "Trivially correct" | "Obviously correct" has sneakiest bugs |
| "Test framework broken" | Fix framework first (Step 0) |
| "Will add tests later" | "Later" never comes |

**If agent produces code before failing tests exist: DELETE the code. Start over from RED.**

## Step 0 — Test Infrastructure Check

1. Detect test framework: jest.config, pytest.ini, Package.swift testTarget, etc.
2. If found: note framework, test command, test directory → proceed
3. If NOT found: create test infrastructure (Jest for TS/JS, pytest for Python, XCTest for Swift)
4. **Run existing tests BEFORE writing any new code.** If pre-existing tests fail:
   - These are **pre-existing failures**, not caused by this build
   - Fix them as part of Boy Scout Rule — do NOT ask the user, do NOT skip, do NOT wait for instructions
   - Log fixed pre-existing failures in Boy Scout Report: `PRE-EXISTING FIX: [test] — [what was wrong]`
   - If a pre-existing failure is too complex to fix (would derail the build), document it and proceed — but simple fixes (imports, config, missing fixtures) MUST be fixed immediately

## Re-Anchoring (EVERY Task agent self-reads)

**Do NOT inject spec content or state into agent prompts.** Instead, pass only file paths and IDs. Every Task agent MUST self-re-anchor as its FIRST action:

Orchestrator includes this block in every Task agent prompt:
```
BEFORE doing anything else, read these files yourself:
1. Read `build-state.yaml` — understand current build state + get scratchpad_dir path
2. Read `build-spec.md` — understand what to build
3. Read `{scratchpad_dir}/architecture/` — understand design decisions
4. Run `git diff --stat` — see what changed so far
5. Read any files listed in `files_modified` that are relevant to your task
Only then begin your work. Do NOT trust any summary — read the source.

Write test plan to: `{scratchpad_dir}/tests/test-plan.md` (Step 1 RED)
```

**WORKTREE CONTEXT (check build-state.yaml → worktree_path):**
If `worktree_path` is set, you are in a git worktree — NOT the main checkout.
- Your CWD is the worktree. ALL file edits MUST be within this CWD.
- Do NOT navigate to or edit files in the main checkout path.
- Use relative paths for test files (./tests/, ./src/) — never hardcoded absolute paths.
- First action after re-anchor: run `pwd` and confirm it matches worktree_path.

**RED→GREEN Worker Lifecycle:** RED and GREEN are **separate** worker agents.

**WORKER LIFECYCLE MATRIX (deterministic):**
| Situation | Action | Why |
|---|---|---|
| Same step, worker returned NEEDS_CONTEXT | SendMessage(to: worker_name) | Reuse context |
| Same step, worker returned DONE | Agent(name: new) | Context released |
| RED done → GREEN | Agent(name: "green-worker") | Different role, fresh context |
| Phase N done → Phase N+1 | Agent(name: new) | Cross-phase = always fresh |
| Worker not returned yet | WAIT — do not spawn duplicate | Race condition |
| Worker returned with no completion marker | Agent(name: new) with RE-ANCHOR | Previous worker truncated |

**Background (why this matters):** SendMessage to a completed agent may silently resume with empty context — always spawn fresh workers instead. Always check if `{step}_complete` exists in build-state.yaml before spawning fresh workers to ensure the previous worker has released its context.

**Flow:** RED worker completes normally (returns test names, failure count, paths) → Orchestrator captures output → Opus validation (Step 2) → Spawn fresh GREEN worker with: spec + test files + Opus validation feedback + RED output summary.

Use `name: "tdd-worker"` for RED, `name: "green-worker"` for GREEN.

**Worker Completion Markers (MANDATORY):** Every worker MUST end its response with a clear status line:
- RED: `RED COMPLETE — [N] tests written, all failing. Files: [list]`
- GREEN: `GREEN COMPLETE — all [N] tests passing. Files modified: [list]`

**Orchestrator Validation (after EVERY worker returns):**
- **Missing marker** → worker may have been truncated. Do NOT proceed. Verify: do the expected files exist? Do tests run? If incomplete → spawn fresh worker to finish.
- **Marker present but no scratchpad files** → worker returned inline but didn't write to disk. Spawn a quick Haiku agent to write the findings to scratchpad before next phase.
- **Verify file state, not just worker output.** Worker says "5 tests written" → check the test file actually exists and has 5 tests. Trust disk, not agent claims.

**Why self-read > orchestrator-injection:** The orchestrator may summarize incorrectly or pass stale context. Workers reading their own spec are immune to orchestrator context rot.

## Worker Agent Limits

All Task agents in this phase MUST include `maxTurns` to prevent infinite loops:
- Test generation agents: **maxTurns: 40**
- Implementation agents: **maxTurns: 50**
- Opus validation: **maxTurns: 10**

## Step 1 — RED (tests first) — ENFORCED BY HOOK

**Injection (MANDATORY):** When spawning the RED worker, prepend `$CONSTITUTION_BLOCK + $QUALITY_GATE_BLOCK` + **purpose statement** before all phase instructions in the agent prompt.

**Purpose statement for RED worker:**
> "This worker writes failing tests that define the feature's behavior. These tests become the acceptance gate for the GREEN implementation phase — focus on covering every acceptance criterion and edge case from the spec."

**criticalSystemReminder (include at BOTH start AND end of RED worker prompt):**
> CRITICAL: Write FAILING tests ONLY. If any test passes, the test is WRONG — fix it. You MUST end with exactly: `RED COMPLETE — [N] tests written, all failing. Files: [list]`. No completion marker = work is invalid.

1. Read all relevant files from previous phases
2. Delegate test generation to Task agent — pass full spec:
   - Every acceptance criterion → test case
   - Every behavior item → test case (edge cases, error paths)
   - Every data model relationship → test case
3. Run tests — confirm they **FAIL** (if they pass, tests are wrong)
4. **RED agent returns normally.** Output RED summary (test names, failure count, expected behavior, test file paths). Orchestrator captures this output for Opus validation and GREEN worker context. **Agent MUST end with:** `RED COMPLETE — [N] tests written, all failing. Files: [list]`
5. **Save RED output (MANDATORY):** redirect test output to file:
   ```bash
   <test-command> 2>&1 | tee build-red-output.log
   ```
   This file MUST exist and contain at least one FAIL/ERROR/FAILED. The SubagentStop hook checks for it.
   If tests unexpectedly PASS in RED phase — tests are wrong. Fix tests first.

**Atomic commit** (if enabled): `test: [desc] (RED)`

**State checkpoint (MANDATORY):** After RED output saved:
```yaml
# build-state.yaml
phase_51_red: complete
current_phase: "5.1"
```

### Model Selection

| Complexity | Model |
|-----------|-------|
| SIMPLE | Haiku |
| FEATURE | Sonnet |
| COMPLEX | Opus |

Models are configurable via `bytedigger.json`.

## Step 2 — Test Validation (Opus HARD GATE)

**MANDATORY. Cannot proceed without PASS. No exceptions.**

### Step 2a — Gherkin Generation (ALL tiers, Sonnet)

Generate `./build-tests.md` — BDD scenarios in Gherkin format (holdout artifact). **ALL tiers produce Gherkin.**

- **SIMPLE**: 1–2 scenarios covering the core acceptance criterion
- **FEATURE / COMPLEX**: full scenario suite (one scenario per acceptance criterion, plus edge cases)

**This agent is a WRITER only — no validation, no verdict.** Pass the spec and RED test output. Output Gherkin to `./build-tests.md` and stop.

**Injection (MANDATORY):** Prepend `$CONSTITUTION_BLOCK + $QUALITY_GATE_BLOCK` + **purpose statement** before Gherkin writer instructions in agent prompt.

**Purpose statement for Gherkin writer:**
> "This worker translates spec criteria into BDD scenarios. These scenarios become the holdout artifact for Opus validation — focus on one Gherkin scenario per acceptance criterion, with clear Given/When/Then structure."

**State checkpoint (MANDATORY):** After Gherkin written:
```yaml
# build-state.yaml
phase_52a_gherkin: complete
```

**Model: Sonnet**

### Step 2b — Opus Audit (ALL tiers, Opus)

**Injection (MANDATORY):** Prepend `$CONSTITUTION_BLOCK + $QUALITY_GATE_BLOCK` + **purpose statement** before Opus audit instructions in agent prompt.

**Purpose statement for Opus validator:**
> "This validation gates whether implementation can proceed. Your audit determines if tests adequately cover the spec — focus on forward/reverse mapping completeness and assertion quality, not style."

**criticalSystemReminder (include at BOTH start AND end of Opus validator prompt):**
> CRITICAL: This is a VALIDATION-ONLY task. You CANNOT write or modify test files. You MUST end with exactly `Verdict: PASS` or `Verdict: FAIL`. No verdict = validation is invalid and blocks the pipeline.

Opus 4-step audit — **validator only, does NOT write or modify tests**:
1. **Forward Map**: Gherkin → Test Code (every scenario has a test)
2. **Reverse Map**: Test Code → Gherkin (flag orphan tests)
3. **Spec Compliance**: every acceptance criterion appears in BOTH Gherkin AND test code
4. **Quality Checks**: meaningful assertions, isolation, negative tests, edge cases, readable names

Verdict: PASS or FAIL. If FAIL: fix gaps in test code (not Gherkin), re-validate.

**On PASS**: orchestrator writes to `build-state.yaml` (mandatory checkpoint):
- `opus_validation: pass`
- Opus writes `build-opus-validation.md` with `Verdict: PASS` (artifact proof)

**State checkpoint (MANDATORY):** After Opus validation:
```yaml
# build-state.yaml
phase_52_validation: complete
current_phase: "5.2"
opus_validation: pass
```

**AUTONOMOUS**: auto-fix gaps, proceed

### Model: Step 2a = **Sonnet** | Step 2b = **Opus** — never downgrade 2b

## Step 3 — GREEN (make tests pass)

**CHECKPOINT GATE**: Before proceeding, orchestrator MUST read `build-state.yaml` and verify `opus_validation: pass`. If not present → STOP. This is file-based enforcement that survives context rot.

**GREEN SPAWN (deterministic):**
1. Verify `opus_validation: pass` in build-state.yaml
2. Verify `phase_51_red: complete` in build-state.yaml
3. Both exist → Agent(name: "green-worker", prompt: "$CONSTITUTION_BLOCK + $QUALITY_GATE_BLOCK + **purpose statement** + [GREEN phase instructions] + [spec + test files + RED output summary]") — NEVER SendMessage to tdd-worker

**Purpose statement for GREEN worker:**
> "This worker implements the feature to make all RED tests pass. Your code will go through Phase 6 quality review — focus on making tests green by fixing code (never tests), and apply Boy Scout Rule to every file touched."

**criticalSystemReminder (include at BOTH start AND end of GREEN worker prompt):**
> CRITICAL: Fix CODE to make tests pass. NEVER modify test assertions to match broken behavior. Tests define correct behavior — code must conform. You MUST end with exactly: `GREEN COMPLETE — all [N] tests passing. Files modified: [list]`. No completion marker = work is invalid.

4. Missing → STOP, previous step incomplete
5. **ALL injection blocks are MANDATORY** in the GREEN worker prompt. Without them, worker may violate project conventions.

The GREEN worker has all context it needs (spec, test file paths, RED output summary, Opus validation feedback) without relying on a persistent RED agent.

**TEST INTEGRITY RULE (CRITICAL — include in EVERY worker prompt that touches tests):**
> Tests verify REAL system behavior against the spec. If a test fails, the CODE is wrong — not the test. NEVER modify test assertions to match broken behavior. NEVER weaken a test to make it pass. If a test checked for a fallback and the fallback was removed, the test should now verify that WITHOUT the fallback, the expected error/empty-state occurs — not silently pass. The only valid reason to change a test is if the SPEC changed. "Make tests green" means "fix the code so tests pass", not "change assertions so they match whatever the code does."

1. Spawn fresh GREEN worker — pass spec + validated test files + RED output summary
   - Do NOT pass `build-tests.md` (holdout)
   - Include the TEST INTEGRITY RULE above verbatim in the worker prompt
   - Include: **"Apply Boy Scout Rule to EVERY file you touch. After implementation, output a BOY SCOUT REPORT listing what you cleaned per file (dead imports, unclear names, stale comments, etc.). If report is empty — re-check, there is ALWAYS something to clean. ALL severities — low, medium, high. Never filter by severity."**
2. Agent writes code to pass tests + cleans surrounding code — by fixing CODE, not by adjusting test expectations
3. **Orchestrator checks:** If agent's response has no Boy Scout Report or report is empty → spawn a re-anchor worker with full context:
   ```
   Agent(name: "boy-scout-worker", prompt: "
   RE-ANCHOR: You are continuing Phase 5 GREEN. Code was just implemented to pass tests.
   FILES MODIFIED: [list files from previous worker's response]
   MISSING: Boy Scout Report. Check EVERY file listed above for: dead imports, unclear names, stale comments, unused vars.
   OUTPUT: Boy Scout Report with file:line per cleanup item. If truly nothing — explain why per file.
   ")
   ```
4. Run tests — confirm **PASS**

**Agent MUST end with:** `GREEN COMPLETE — all [N] tests passing. Files modified: [list]`

**Atomic commit** (if enabled): `feat: [desc] (GREEN)`

**Save GREEN output (MANDATORY):** redirect test output to file:
```bash
<test-command> 2>&1 | tee build-green-output.log
```

**State checkpoint (MANDATORY):** After all tests pass:
```yaml
# build-state.yaml
phase_53_green: complete
current_phase: "5.3"
phase_5_implement: complete
```

**Orchestrator verification (MANDATORY):** After GREEN worker agent returns:
1. Read `build-state.yaml` and verify `phase_53_green: complete` exists
2. If missing — GREEN worker failed to update state. Do NOT proceed. Re-spawn fresh GREEN worker.
3. Only after verification → proceed to Phase 5.5 (Test Integrity) or Phase 6
4. NEVER use SendMessage to completed GREEN worker — always spawn fresh agent

### Boy Scout Checklist (MANDATORY in every touched file)

- [ ] Remove dead/unused imports
- [ ] Fix unclear variable/function names
- [ ] Remove stale/outdated comments
- [ ] Add missing type annotations
- [ ] Remove unused variables/functions
- [ ] Fix inconsistent formatting
- Test coverage gaps with severity >= 5/10 are BLOCKERS, not acceptable. Fix them before marking DONE.
- 'Acceptable for MVP' is NOT a valid reason to skip quality. Every gap found MUST be addressed.
- **ALL severities get fixed — LOW included.** Never say "fixing only high/medium". There is no severity filter. Fix everything.
- **EXCLUSION: Do NOT modify test assertions, expected values, or test logic as part of Boy Scout cleanup. Test files are subject to Test Integrity rules — changing assertions to make tests pass is ASSERTION_GAMING.**

### Model: Always **Sonnet** for code generation (configurable via `bytedigger.json` → `agent_model`)

## Step 3.5 — TEST INTEGRITY DIFF GUARD (MANDATORY before Phase 6)

After GREEN completes and tests pass, orchestrator MUST verify test integrity:

1. **Collect test file diffs:** Compare test files as written in RED vs their current state after GREEN/fix cycles:
   ```bash
   git diff <red-commit-or-stash>..HEAD -- '*test*' '*spec*' '*.test.*'
   ```
   If no atomic commits: compare `build-red-output.log` test names/assertions vs current test files.

2. **If test files were modified after RED:**
   - Launch Opus reviewer (model: Opus, 10 maxTurns) with the diff
   - Opus classifies EACH test change as:
     - **SPEC_CHANGE**: spec requirement changed, test correctly updated → PASS
     - **LEGITIMATE_REFACTOR**: test structure improved without changing assertions → PASS
     - **ASSERTION_GAMING**: assertion values/expectations changed to match broken behavior → BLOCK
   - Any ASSERTION_GAMING → pipeline STOPS. Worker must fix CODE, not tests.

3. **If test files unchanged after RED:** → PASS (ideal case, skip reviewer)

4. **State checkpoint:**
   ```yaml
   test_integrity_check: pass|fail
   test_files_modified_after_red: <count>
   assertion_gaming_detected: true|false
   ```

**This gate is NON-NEGOTIABLE.** If assertion gaming is detected, the fix agent must revert test changes and fix the code instead.

## Plan-Sync (after each Task agent completes)

After EACH Task agent returns (implementation or test generation), orchestrator MUST:

1. Check if the implementation **drifted from spec** — did the agent rename functions, change API shapes, use different types than planned?
2. If drift detected:
   - Update `build-spec.md` to reflect reality (what was actually built)
   - Update any downstream agent prompts to reference new names/shapes
   - Log drift in `build-state.yaml`: `plan_drift: ["renamed X to Y", "changed API shape"]`
3. If no drift: proceed normally

**Why:** If task 1 builds `getUserById()` but spec said `fetchUser()`, tasks 2-5 will import the wrong function name. Plan-sync catches this immediately.

**For COMPLEX builds with multiple workers:** Run plan-sync between each worker completion, before dispatching next dependent worker.

## Loop Detection (max 3 fix cycles)

- If tests fail: diagnose, fix, re-run
- Same test fails 3x with same error → change approach
- Max 3 cycles total. Exhausted → STOP pipeline
- Never proceed past Phase 5 with failing tests

## Step 4 — DevOps Validation (only if profile=devops)

Skip if profile is `code`.

1. **Syntax validators**: terraform validate, hadolint, actionlint, kubectl dry-run, helm lint
   - Graceful degradation: skip missing tools, log warning
2. **Security scanning**: checkov, trivy, gitleaks, regex patterns
   - Aggregate by severity
3. CRITICAL/HIGH findings → fix (max 3 cycles)

## COMPLEX Worker Dispatch

For COMPLEX tasks ONLY, replace monolithic Phase 5 with per-task workers:

1. **Decompose** spec into logical tasks (1-3 files each, self-contained)
2. **Dispatch** fresh Task agent per task (`run_in_background: true`) with ONLY:
   - Spec SUBSET for that task
   - Test cases for that task only
   - Constitution
3. **Worker rules**: minimal context, no cross-worker visibility
4. **Conflict detection**: check if workers modified same file → merge agent if needed
5. **Integration testing**: run ALL tests together, fix failures
6. **State tracking**: update `build-state.yaml` with worker status

### Anti-Pattern: Lazy Delegation

**NEVER write prompts like "based on your findings, implement X" or "based on the research, fix it."** These phrases push synthesis onto the worker instead of doing it yourself. When dispatching workers, include:
- Exact file paths and line numbers
- What specifically to change and why
- Concrete acceptance criteria

Pass data as **file paths** (scratchpad, spec), not inline summaries. Workers read their own context — orchestrator summaries rot.

## Atomic TDD Commits — Defaults

- SIMPLE/FEATURE: OFF (extra commits add noise)
- COMPLEX: ON (provides rollback points)
- `--atomic-commits` flag overrides for any complexity

## State Update Protocol (MANDATORY)

**During and after this phase**, orchestrator MUST:

1. **On entry** — update `build-state.yaml`:
   ```yaml
   phase_5_implement: in_progress
   phase_5_started: "<ISO timestamp>"
   ```
2. **After GREEN tests pass (Step 3 complete)** — update `build-state.yaml`:
   ```yaml
   phase_5_implement: complete
   opus_validation: pass
   phase_5_files_changed: <N>
   ```
3. **On each worker completion** (COMPLEX only):
   ```yaml
   phase_5_workers_done: <N>/<total>
   ```

**This is a BLOCKER** — do not proceed to Phase 6 without completing state updates.

## Exit Criteria

- [ ] Opus cross-validation returned PASS (Step 2)
- [ ] All tests pass (exit code 0)
- [ ] All files from spec created/modified
- [ ] No lint errors or warnings
- [ ] `build-state.yaml` updated with phase_5 status + opus_validation
- [ ] (DevOps) Validators pass or gracefully skipped
- [ ] (DevOps) No CRITICAL/HIGH security findings
