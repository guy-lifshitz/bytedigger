> This is Phase 4.5 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 4.5: SPEC

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"4.5\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 4.5')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

Turn architecture into a concrete, verifiable specification. This is the contract between "what to build" and "what gets built."

**WORKER AGENT CONSTRAINTS (include in every agent prompt):**
- You are a worker inside /build pipeline. Use Read/Edit/Write/Bash directly.
- NEVER call Skill tool (you don't have access, attempts waste turns).
- NEVER invoke /build, /bugfix, or any slash command.
- If stuck, report what's blocking you — don't try to delegate or escalate via tools.

## What You Receive

- Feature request text
- Architecture decision (from Phase 4)
- Exploration findings (from Phase 2)
- Constitution block
- Mode (AUTONOMOUS / SUPERVISED)

## What You Must Produce

`build-spec.md` with 6 mandatory sections (in order):

### 1. User Stories (ordered by priority)

Minimum 2 stories. Each: independently testable, tech-agnostic (WHAT not HOW), BDD format. P1 alone = working MVP.

```
### US1 - [Title] (P1 — MVP)
[What the user can do, in plain language]
**Why P1**: [value justification]
**Acceptance**:
- Given [state], When [action], Then [outcome]
```

### 2. Files

Every file to create or modify, with full paths:
```
CREATE: src/services/UserService.ts
MODIFY: src/routes/users.ts (add PUT /users/:id endpoint)
CREATE: src/services/__tests__/UserService.test.ts
```

### 3. Interfaces

Function signatures, types, return values.

### 4. Data Model

Key entities with actual field names, types, and relationships. Use project's language format.

### 5. Behavior

Edge cases, error handling, validation rules.

### 6. Tests

What to test, expected outcomes (derived from acceptance criteria).

## After Writing Spec

- Verify against explorer findings — conflicts with existing patterns?

## SUPERVISED Mode — Spec Review

1. Write full spec to `./build-spec.md` in project CWD
2. Tell user: "Spec ready for review. Add comments, then say 'done reviewing'."
3. Wait for user to return
4. If comments found: apply feedback, update spec
5. When no unresolved comments: proceed to Phase 5

## AUTONOMOUS Mode

Write spec to `./build-spec.md`, proceed.

## Model Selection

| Complexity | Model |
|-----------|-------|
| SIMPLE | Orchestrator (inline, merged with Phase 1) |
| FEATURE | Sonnet |
| COMPLEX | Sonnet |

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Do NOT emit text between tool calls — work silently, report once at the end — EXCEPT in SUPERVISED mode, present spec for review and wait for approval before the final report.

## Agent Status Protocol

Return status footer as LAST output:
```
---
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
CONCERNS: [list concerns, only if DONE_WITH_CONCERNS]
BLOCKED_ON: [description, only if BLOCKED]
CONTEXT_NEEDED: [what's missing, only if NEEDS_CONTEXT]
---
```

## Exit Criteria (Quality Gate)

- [ ] **Concrete**: file paths, function names, types — no vague descriptions
- [ ] **Testable**: every behavior has a test case
- [ ] **Bounded**: clear IN and OUT of scope
- [ ] **Compatible**: doesn't contradict existing patterns (verified against explorer)
- [ ] **Story-driven**: every feature maps to a user story with acceptance criteria
- [ ] **Model-complete**: all entities defined with fields, types, and relationships
- [ ] SUPERVISED: user approved

## Plan-Review Gate (MANDATORY for FEATURE/COMPLEX)

**Before proceeding to Phase 5**, the spec MUST be validated by an independent reviewer.
This is the most cost-effective gate — design errors caught here save entire implementation cycles.

### How it works:

1. Launch a **separate Opus Task agent** (NOT the one that wrote the spec) with:
   - The generated spec (build-spec.md)
   - The original feature request
   - The exploration summary from Phase 2 (scratchpad research findings)
   - Instruction: "You are a spec reviewer. Find gaps, contradictions, missing edge cases, and impossible requirements. Do NOT approve by default. Write your verdict and findings to `build-plan-review.md` in CWD before returning."

2. Reviewer returns one of:
   - **SHIP** — spec is ready, proceed to Phase 5
   - **REVISE** — list specific issues to fix
   
   The reviewer MUST write `build-plan-review.md` in CWD containing verdict, key concerns checked, and gaps found.

3. On REVISE: fix issues in spec, re-run reviewer (max 2 cycles)
4. Write result to `build-state.yaml`:
   ```yaml
   plan_review: pass
   plan_review_concerns: []
   ```
5. Verify `build-plan-review.md` exists and is non-empty before proceeding. **Phase 5 gate BLOCKS if missing** (FEATURE/COMPLEX only).

6. **SIMPLE tasks skip this gate** — direct to Phase 5.

### Why separate agent:
The agent that wrote the spec has confirmation bias. A fresh agent catches what the author assumes is obvious.

**BLOCKER**: Phase 5 Entry Gate MUST verify `plan_review: pass` (for FEATURE/COMPLEX).
