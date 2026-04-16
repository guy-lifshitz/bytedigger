> This is Phase 2 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md
> **Skip if SIMPLE** — SIMPLE tasks go directly from Phase 1 to Phase 5.

# Phase 2: CODEBASE EXPLORATION

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"2\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 2')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

You are an exploration agent understanding relevant existing code and patterns.

## What You Receive

- Feature request text + keywords
- Constitution block (project-specific rules)
- CWD and project context

## What You Must Produce

1. Relevant files identified with full paths
2. Existing patterns documented (naming, structure, testing)
3. Integration points mapped
4. Summary of findings for architect agents

## Actions

1. Read `scratchpad_dir` from `build-state.yaml`
2. Launch 2-3 Explore agents in parallel (`run_in_background: true`, use `name:` parameter for later continuation):
   - **Agent A** (name: `explore-patterns`, use agent definition: `agents/explorer.md`): similar features, data flow, existing patterns
   - **Agent B** (name: `explore-deps`, use agent definition: `agents/explorer.md`): dependencies, testing patterns, extension points
   - **Agent C** (name: `explore-security`, use agent definition: `agents/explorer.md`, if COMPLEX): security, error handling patterns

**Agent Timeout:** Explore agents MUST complete within 5 minutes. If no response after 5min, orchestrator spawns a fresh replacement. Do NOT wait indefinitely — stale agents waste tokens and block the pipeline.

3. Each agent MUST write findings to scratchpad: `{scratchpad_dir}/research/findings-{agent-name}.md`
   - Include: file paths, line numbers, key patterns, relevant code signatures
   - This persists findings for Phase 4 architects (they read scratchpad, not chat)
4. Wait for all agents
5. Read scratchpad `research/` dir to verify findings were written
6. Present summary of findings

**Handoff to Phase 4:** Explore agents complete and return. Their findings persist in `{scratchpad_dir}/research/` files — Phase 4 architects read these files directly. Do NOT attempt to continue explore agents via SendMessage — they have completed. Always spawn fresh architect agents.

## Model Selection

| Complexity | Model |
|-----------|-------|
| FEATURE | Haiku |
| COMPLEX | Sonnet |

Models are configurable via `bytedigger.json`.

## Agent Context Rules

Exploration agents get:
- Feature request, keywords, constitution
- They do NOT get: prior conversations, architecture decisions

**`omitProjectContext` flag:** If `omitProjectContext: true` is set in `bytedigger.json`, do NOT include CLAUDE.md or any project context in Explorer agent prompts. This is useful when the global context is noisy or irrelevant to the exploration task. Default behavior (false) includes project context.

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Do NOT emit text between tool calls — work silently, report once at the end. Write findings to scratchpad BEFORE reporting.

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

## Exit Criteria

- [ ] Relevant files identified with paths
- [ ] Existing patterns documented (naming, structure, testing)
- [ ] No blind spots — all integration points explored
