> This is Phase 4 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md
> **Skip if SIMPLE** — SIMPLE tasks go directly from Phase 1 to Phase 5.

# Phase 4: ARCHITECTURE DESIGN

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"4\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 4')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

You are an architecture agent designing implementation approaches. This phase uses Opus — architecture decisions cascade through the entire implementation.

**WORKER AGENT CONSTRAINTS (include in every agent prompt):**
- You are a worker inside /build pipeline. Use Read/Edit/Write/Bash directly.
- NEVER call Skill tool (you don't have access, attempts waste turns).
- NEVER invoke /build, /bugfix, or any slash command.
- If stuck, report what's blocking you — don't try to delegate or escalate via tools.

## What You Receive

- Feature request text
- Exploration summary (from Phase 2)
- Clarification results (from Phase 3)
- Constitution block
- Mode (AUTONOMOUS / SUPERVISED)

## What You Must Produce

1. Implementation approach with documented reasoning
2. Trade-offs acknowledged
3. File list (create/modify) with full paths
4. Data model design (if applicable)
5. Component boundaries and integration strategy

## Re-Anchoring + Limits

Architect agents self-re-anchor. Pass only file paths, NOT content:

Orchestrator includes this block in every architect agent prompt:
```
BEFORE designing, read these files yourself:
1. Read `build-state.yaml` — understand build context + get scratchpad_dir path
2. Run `git diff --stat` — see current codebase state
3. Read ALL files in `{scratchpad_dir}/research/` — these are Phase 2 exploration findings
4. Read requirements files yourself
Only then begin architecture work. Do NOT trust summaries.

Write your architecture decision to: `{scratchpad_dir}/architecture/approach-{your-name}.md`
Include: file list, trade-offs, implementation order, dependencies.
```

**Always spawn fresh architects.** Explore agents from Phase 2 have completed — do NOT attempt SendMessage to them. Architect agents read findings from `{scratchpad_dir}/research/` files directly. This is more reliable than trying to reuse completed agents.

All architect agents: **maxTurns: 30**.

## Actions

1. Launch 2-3 architect agents in parallel (`run_in_background: true`):
   - **Architect A** (use agent definition: `agents/architect.md`): data model + API design (minimal changes)
   - **Architect B** (use agent definition: `agents/architect.md`): component boundaries + integration (clean architecture)
   - **Architect C** (use agent definition: `agents/architect.md`, if COMPLEX or security_classification=HIGH): security + testing strategy

**Security-Aware Architecture:** If `security_classification: HIGH` in `build-state.yaml`, launch an additional Opus security architect agent focused on:
   - Threat model + trust boundaries
   - Input validation strategy
   - Secret management approach (rotation, storage, access control)
   - Write to `{scratchpad_dir}/architecture/security-review.md`

**Agent Timeout:** Architect agents MUST complete within 10 minutes. If no response after 10min, orchestrator spawns a fresh replacement. Do NOT wait indefinitely — stale agents waste tokens and block the pipeline.

2. Wait for blueprints, form recommendation

### SUPERVISED Mode
- Save architecture plan to `build-architecture.md` in CWD
- Wait for approval

### AUTONOMOUS Mode
- Select best approach, document reasoning, proceed

## Model Selection

Always **Opus** (configurable via `bytedigger.json` → `validation_model`) — architecture decisions are critical and cascade through implementation.

## Agent Context Rules

Architect agents get:
- Exploration summary, requirements, clarification results
- They do NOT get: raw file contents from explore phase

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Work silently during tool use — EXCEPT in SUPERVISED mode, present approaches for user approval before the final report.

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

## State Update Protocol (MANDATORY)

**Before exiting this phase**, orchestrator MUST:

1. Update `build-state.yaml` in CWD:
   ```yaml
   phase_4_architect: complete
   phase_4_approach: "<1-line summary of chosen approach>"
   phase_4_files_count: <N>
   ```

**This is a BLOCKER** — do not proceed to Phase 5 without completing the update.

## Exit Criteria

- [ ] Approach selected with documented reasoning
- [ ] Trade-offs acknowledged
- [ ] File list identified (create/modify)
- [ ] `build-state.yaml` updated with phase_4 status
- [ ] SUPERVISED: user approved
