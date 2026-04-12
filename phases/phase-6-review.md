> This is Phase 6 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 6: QUALITY REVIEW

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"6\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 6')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

Deep review using specialized reviewer agents and Opus satisfaction scoring.

**WORKER AGENT CONSTRAINTS (include in every fix-agent prompt):**
- You are a fix worker inside /build pipeline. Use Edit/Write/Bash directly.
- NEVER call Skill tool (you don't have access, attempts waste turns).
- NEVER invoke /build, /bugfix, or any slash command.
- If stuck, report what's blocking you — don't try to delegate or escalate via tools.
- **Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Do NOT emit text between tool calls — work silently, report once at the end.

**REVIEWER AGENT CONSTRAINTS (include in every reviewer agent prompt):**
- You are a reviewer inside /build pipeline. Your job is verification, not implementation.
- NEVER edit code files. You review and report — fixes are handled by fix workers.
- **VERDICT protocol** — structure EVERY check using one of two modes:
  ```
  RUNTIME (code-reviewer, silent-failure-hunter, pr-test-analyzer, security-reviewer):
  ### Check: [what you're verifying]
  **Command run:** [exact bash/test command executed]
  **Output observed:** [relevant output excerpt]
  **Result: PASS/FAIL**

  STATIC (comment-analyzer, type-design-analyzer, code-simplifier):
  ### Check: [what you're verifying]
  **Tool used:** [Read/Grep tool call with file:line]
  **Output observed:** [relevant excerpt]
  **Result: PASS/FAIL**
  ```
- Runtime reviewers: no check valid without command output. Reading code alone is NOT verification — run tests/linters/commands.
- Static reviewers: no check valid without tool evidence. Cite file:line from Read/Grep output.
- Self-monitor for "verification avoidance" — if you're asserting without evidence, STOP and actually verify.
- End your review with exactly one of: `VERDICT: PASS`, `VERDICT: FAIL`, or `VERDICT: PARTIAL`
- **Confidence threshold >=80** — only report findings with confidence >=80 on a 0-100 scale:
  - 0 = false positive, 25 = maybe, 50 = real but minor, 75 = verified important, 100 = certain
  - Below 80 → do NOT report. This eliminates noise and ensures every finding matters.
- **Scope: bugs and logic, NOT cosmetic cleanup.** Dead imports, stale comments, naming, formatting — these are Boy Scout Rule items handled by Phase 5 GREEN worker (free — context already loaded). Phase 6 reviewers focus on what workers CANNOT see from inside: cross-file logic errors, security vulnerabilities, coverage gaps, spec violations, silent failures. Do NOT report LOW/COSMETIC findings that a worker could have fixed inline.
- **Purpose statement** (orchestrator adds one line per reviewer):
  - code-reviewer: "This review will determine if code meets quality standards — focus on bugs, logic errors, and convention violations."
  - silent-failure-hunter: "This review will catch swallowed errors — focus on catch blocks, fallbacks, and missing error propagation."
  - pr-test-analyzer: "This review will verify test adequacy — focus on coverage gaps, missing edge cases, and assertion quality."
  - comment-analyzer: "This review will catch stale comments — focus on accuracy vs actual code behavior."
  - type-design-analyzer: "This review will validate type safety — focus on invariant enforcement and encapsulation."
  - code-simplifier: "This review will find simplification opportunities — focus on unnecessary complexity and dead code."
  - security-reviewer: "This review will catch exploitable vulnerabilities — focus on auth bypass, injection, and data exposure."

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`, then end with `VERDICT: PASS/FAIL/PARTIAL` as the absolute final line. Do NOT emit text between tool calls — work silently, report once at the end.

**criticalSystemReminder (MANDATORY — include at BOTH start AND end of every reviewer prompt):**
> CRITICAL: This is a VERIFICATION-ONLY task. You CANNOT edit code files. Every check MUST have evidence (command output or tool output). You MUST end your review with exactly `VERDICT: PASS`, `VERDICT: FAIL`, or `VERDICT: PARTIAL`. No verdict = review is invalid and will be discarded.

**Why sandwich:** Long reviews cause context drift. Reviewers start editing files, skip running commands, or forget the verdict. Repeating the constraint at the end keeps it active even after 20+ tool calls.

## Entry Gate (MANDATORY)

Before starting Phase 6, orchestrator MUST read `build-state.yaml` and verify:
- `phase_5_implement: complete` exists
- `opus_validation: pass` exists
If either is missing → STOP, Phase 5 is incomplete.

## Review Agent Policy by Complexity

| Complexity | Review Agents | Satisfaction |
|-----------|---------------|-------------|
| SIMPLE | 3: code-reviewer, silent-failure-hunter, pr-test-analyzer | 1x Opus, 3 dim, >=80% |
| SIMPLE + HIGH security | 4: above + security-reviewer | 1x Opus, 3 dim, >=80% |
| FEATURE | 6: all reviewer agents | 1x Opus, 5 dim, >=85% |
| FEATURE + HIGH security | 7: above + security-reviewer | 1x Opus, 5 dim, >=85% |
| COMPLEX | 6: all reviewer agents | 3x Opus voting, 5 dim, >=90% |
| COMPLEX + HIGH security | 7: above + security-reviewer | 3x Opus voting, 5 dim, >=90% |

Reviewer counts and thresholds are configurable via `bytedigger.json`.

## Re-Anchoring (reviewers self-read)

**Do NOT inject file contents into reviewer prompts.** Pass only file paths. Every review agent MUST self-re-anchor:

Orchestrator includes this block in every review agent prompt:
```
BEFORE reviewing, read these files yourself:
1. Read `build-state.yaml` — get files_modified list and build context
2. Read `build-spec.md` — understand what was supposed to be built
3. Run `git diff --stat` — see all actual changes
4. Read each modified file yourself — do NOT trust summaries
Only then begin your review. Compare implementation against spec.
```

**Why:** Reviewers who read code themselves catch issues that summaries hide.

## Post-Review Gate (MANDATORY)

After ALL reviewers complete, verify in `build-state.yaml` that `phase_6_findings_skipped == 0`. Any skipped findings → **PIPELINE STOPS**.

Write `post_review_gate: pass` + `semantic_skip_check: pass` to `build-state.yaml` after verification.

**Phase 7 gate BLOCKS without `post_review_gate: pass`.** Do not proceed to Phase 7 until this passes.

## Scratchpad Persistence (MANDATORY)

**Every reviewer agent MUST write its findings to `{scratchpad_dir}/reviews/{agent-name}.md` before returning.**

File format:
- Filename: `reviews/{agent-name}.md` (e.g., `reviews/code-reviewer.md`, `reviews/silent-failure-hunter.md`, `reviews/pr-test-analyzer.md`)
- Content: VERDICT protocol format — each check with command evidence, then final verdict
- Only findings with confidence >=80 included

Example — RUNTIME reviewer (code-reviewer):
```
# Code Reviewer

### Check: Input validation on auth token
**Command run:** grep -n "jwt\|token" src/auth.ts
**Output observed:** Line 42: const token = req.headers.authorization (no validation)
**Result: FAIL**
Confidence: 95 | Severity: CRITICAL — User-supplied JWT not validated before decode

### Check: Secret management
**Command run:** npx tsc --noEmit 2>&1 | grep "secret"
**Output observed:** src/auth.ts:89 — hardcoded secret detected
**Result: FAIL**
Confidence: 100 | Severity: CRITICAL — Must load from env

VERDICT: FAIL
```

Example — STATIC reviewer (comment-analyzer):
```
# Comment Analyzer

### Check: JSDoc accuracy on parseToken()
**Tool used:** Read src/auth.ts:38-45
**Output observed:** JSDoc says "returns null on failure" but function throws TokenError
**Result: FAIL**
Confidence: 92 | Severity: MEDIUM — Comment contradicts actual behavior

VERDICT: PARTIAL
```

**VERDICT rules (single source of truth):**
- Every `### Check:` MUST have evidence: `**Command run:**` (runtime) or `**Tool used:**` (static)
- `**Result: PASS/FAIL**` per check
- Final line MUST be exactly `VERDICT: PASS`, `VERDICT: FAIL`, or `VERDICT: PARTIAL`
- PASS = zero MEDIUM+ findings. PARTIAL = MEDIUM findings only (no CRITICAL/HIGH). FAIL = any CRITICAL or HIGH finding. Note: LOW/COSMETIC are not reported by Phase 6 reviewers.

**On pipeline resume** (`/build continue` / Phase 6 restart), orchestrator:
1. Reads all existing `reviews/*.md` files from scratchpad
2. Compares against `phase_6_reviewers_expected`
3. Only re-launches reviewers with missing scratchpad files
4. Skips reviewers with existing findings (no duplication)

**Why:** Without scratchpad persistence, all findings are lost on pipeline resume. Reviewers must re-run from scratch, wasting compute and losing context.

## Reviewer Agent Limits

All review agents MUST include **maxTurns: 30** to prevent infinite loops.

## Step 1: Determine Reviewer Count (MANDATORY)

**Read `complexity` from `build-state.yaml`** — do NOT guess or assume. Then launch the EXACT agents listed:

### SIMPLE → 3 agents (ALL mandatory):
1. **code-reviewer** — quality, conventions, Boy Scout Rule compliance
2. **silent-failure-hunter** — error handling, swallowed exceptions
3. **pr-test-analyzer** — test coverage quality, missing edge cases

### FEATURE / COMPLEX → 6 agents (ALL mandatory):
1. **code-reviewer** — quality, conventions, Boy Scout Rule compliance
2. **silent-failure-hunter** — error handling, swallowed exceptions
3. **pr-test-analyzer** — test coverage quality, missing edge cases
4. **comment-analyzer** — comment accuracy, staleness
5. **type-design-analyzer** — type invariants, encapsulation
6. **code-simplifier** — simplification opportunities

### Security Reviewer (conditional, if `security_classification: HIGH`):
7. **security-reviewer** — OWASP Top 10 compliance, injection vectors, auth bypass paths, secret exposure, security best practices

**Launch ALL agents for your tier in parallel** (`run_in_background: true`). If `security_classification: HIGH` in `build-state.yaml`, also launch security-reviewer.

**ENFORCEMENT**: After all agents return, COUNT them. Calculate expected count:
- SIMPLE: 3 base agents
- SIMPLE + HIGH security: 4 (3 base + security-reviewer)
- FEATURE: 6 agents
- FEATURE + HIGH security: 7 (6 base + security-reviewer)
- COMPLEX: 6 agents
- COMPLEX + HIGH security: 7 (6 base + security-reviewer)

Log in `build-state.yaml`:
```yaml
phase_6_reviewers_launched: <N>
phase_6_reviewers_expected: <3|4|6|7>
security_review_enabled: <true|false>
```
If launched != expected → STOP and fix before proceeding to Step 2.

**No excuses to skip agents within your tier. No partial reviews.**

### DevOps Additional Reviewers (if profile=devops)

7. **InfraSec** (Sonnet) — encryption, IAM, network security, secrets
8. **OpsSafety** (Sonnet) — blast radius, rollback, idempotency, HA
9. **QualityCost** (Haiku) — right-sizing, auto-scaling, cost optimization

**Fallback**: If specialized review agents unavailable, use 2 Haiku Task agents.

## Step 2: Fix ALL Findings (ZERO EXCEPTIONS — GATE ENFORCED)

**Every finding gets fixed. No triage. No "fix later". No exceptions.**
**This is enforced by a programmatic gate — skipped findings BLOCK the pipeline.**

### Forbidden responses to findings:

| What orchestrator says | Why it's wrong |
|------------------------|----------------|
| "3 minor notes (acceptable)" | Nothing is "acceptable" — fix it |
| "pre-existing issue, not from this PR" | **Boy Scout Rule** — you touched the file, you fix it. You also fix issues in code you didn't write but found during review. |
| "low severity, skip" | If it passed confidence >=80 and reviewer reported it, fix it. |
| "cosmetic only" | Phase 6 reviewers don't report cosmetics (Phase 5 Boy Scout handles those). If it's here, it's MEDIUM+. |
| "will address in follow-up" | No. Fix now. There is no follow-up. |
| "not related to this change" | If you see it, you fix it. Boy Scout Rule applies to ALL code you encounter. |

### Boy Scout Rule — EXPANDED SCOPE

You fix **everything you find**, not just what you changed:
- Code you wrote in this build → fix obviously
- Code you didn't write but reviewed → fix it
- Pre-existing issues in files you touched → fix them
- Issues in adjacent files you read during exploration → fix them
- The codebase must be **cleaner after every build**, not just "not worse"

### Process:

1. Collect ALL issues from ALL review agents — create a single list. Reviewers only report MEDIUM+ findings (LOW/COSMETIC handled by Phase 5 Boy Scout). Every finding that made it through the confidence >=80 filter is real and MUST be fixed.
2. Launch Task agent to fix **EVERY** finding on the list (CRITICAL + HIGH + MEDIUM). Agent prompt MUST include:
   - **"Fix ALL findings. Every finding passed confidence >=80 and severity >=MEDIUM filters — they are all real issues. Report each fix with file:line."**
   - **TEST INTEGRITY: If fixing a finding causes tests to fail, fix the CODE — never adjust test assertions to match broken behavior. Tests verify spec behavior. The only valid reason to change a test is if the SPEC changed."**
3. **Orchestrator validates:** If agent reports "fixed N of M" where N < M → spawn **fresh fix agent** with remaining findings:
   - **Try SendMessage first** (agent may still be alive for same-phase continuation)
   - **If SendMessage fails** (agent already completed) → spawn fresh Task agent with full context
   - Either way, prompt MUST include the actual remaining findings:
   ```
   You are a fix agent in Phase 6 Review. [N] of [M] findings were fixed. [M-N] remain.
   REMAINING FINDINGS (from review agents):
   [paste the specific unfixed findings with file:line and description]
   FIX ALL of them. Every finding passed confidence >=80 and is MEDIUM+ severity. Report each fix as file:line: what you did.
   ```
   **KEY:** The orchestrator MUST paste the actual remaining findings. A generic "fix remaining" without listing WHAT to fix causes agent to lose track.
3. Re-run affected review agents to verify fixes
4. Max fix-review cycles: **2**
5. Still issues after 2 cycles: SUPERVISED → ask user. AUTONOMOUS → proceed with warnings logged.
6. Log in `build-state.yaml`:
   ```yaml
   phase_6_findings_total: <N>
   phase_6_findings_fixed: <N>
   phase_6_findings_skipped: 0
   ```

### PROGRAMMATIC GATE (runs after Step 2, before Step 3):
Verify `phase_6_findings_skipped == 0` in `build-state.yaml`.
**Gate exits non-zero if `phase_6_findings_skipped > 0`.**
**If gate fails → PIPELINE STOPS. Cannot proceed to satisfaction scoring.**

## Step 3: Satisfaction Scoring (Opus — HARD GATE)

### SIMPLE — Single Opus Evaluator (3 dimensions)

Dimensions: Spec compliance, Test quality, Code quality
Threshold: >=80%

### FEATURE — Single Opus Evaluator (5 dimensions)

Dimensions: Spec compliance, Test quality, Code quality, Completeness, Boy Scout compliance
Threshold: >=85%

### COMPLEX — 3-Agent Majority Vote (5 dimensions)

1. Launch 3 Opus agents in parallel, each with different focus:
   - **Evaluator A**: spec compliance + completeness
   - **Evaluator B**: test quality + edge case coverage
   - **Evaluator C**: code quality + maintainability + Boy Scout
2. Each returns: Score (0-100%), Verdict (PASS/FAIL), Top concerns
3. Final verdict: **Majority vote** (2/3 PASS = PASS)
4. Final score: **Median** of 3 scores
5. Split decisions: log ALL concerns, use strictest interpretation
6. Threshold: >=90%

Thresholds are configurable via `bytedigger.json` → `satisfaction_thresholds`.

## Step 4: Checkpoint

1. **On satisfaction >= threshold**: write `review_complete: pass` to `build-state.yaml` (mandatory checkpoint for Phase 7)
2. If satisfaction >= threshold: proceed to Phase 7
3. If < threshold AND iterations < 2: fix gaps, re-review
4. If < threshold after 2 iterations: STOP pipeline

**SUPERVISED**: Present scores + findings, ask: fix now / fix later / proceed

## Zero Corner-Cutting Policy

Reviewers MUST reject if ANY of these are true:
- Test coverage gaps with severity >= 5/10 exist and are marked "acceptable"
- Known issues are deferred without a blocking ticket
- "Good enough for now" or "acceptable for MVP" appears as justification
- Boy Scout Rule violations: touched files left dirtier than found

DONE_WITH_CONCERNS is for severity 1-4 issues ONLY.
Severity 5+ = BLOCKED until fixed.

## State Update Protocol (MANDATORY)

**During and after this phase**, orchestrator MUST:

1. **On entry** — update `build-state.yaml`:
   ```yaml
   phase_6_review: in_progress
   ```
2. **On completion** — update `build-state.yaml`:
   ```yaml
   phase_6_review: complete
   review_complete: pass
   review_satisfaction: <PCT>%
   review_issues_found: <N>
   ```

**This is a BLOCKER** — do not proceed to Phase 7 without completing state updates.

## Exit Criteria

- [ ] No CRITICAL/HIGH unresolved issues
- [ ] Satisfaction >= tiered threshold
- [ ] All review agents completed
- [ ] Boy Scout Rule verified
- [ ] `build-state.yaml` updated with phase_6 + review_complete
