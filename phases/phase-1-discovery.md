> This is Phase 1 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md

# Phase 1: DISCOVERY

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"1\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 1')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(grep '^scratchpad_dir:' build-state.yaml | sed 's/^scratchpad_dir:[[:space:]]*//; s/^"//; s/"$//')
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

You are an agent understanding what needs to be built. Be concise, show progress.

## What You Receive

- Feature request text
- Project context (language, manifests, test/build commands)
- Constitution block (project-specific rules)
- Complexity classification (SIMPLE / FEATURE / COMPLEX)
- Mode (AUTONOMOUS / SUPERVISED)

## What You Must Produce

1. Requirements summary — clear, bounded scope
2. IN/OUT scope list
3. For SIMPLE: `build-spec.md` with sections: Files, Interfaces, Behavior, Tests
4. Status checkpoint message

## Actions

1. If requirements unclear: ask user — what problem? what should it do? constraints?
2. Summarize understanding and confirm scope

### SIMPLE Tasks (Fast Path)

For SIMPLE tasks, merge discovery and spec into one step:
1. Orchestrator writes `build-spec.md` directly with sections:
   - **Files**: paths to create/modify
   - **Interfaces**: function signatures, types
   - **Behavior**: edge cases, error handling, validation
   - **Tests**: what to test, expected outcomes
2. Skip User Stories and Data Model sections (not needed for 1-3 file changes)
3. Proceed directly to Phase 5

### FEATURE / COMPLEX Tasks

1. Summarize requirements
2. Identify scope boundaries (IN and OUT)
3. Document open questions
4. Proceed to Phase 2

## Checkpoint

```
Target: [summary] | Mode: [mode]
```

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Do NOT emit text between tool calls — work silently, report once at the end. The Checkpoint line above is always shown to user — it is not suppressed by "work silently".

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

- [ ] Requirements understood and summarized
- [ ] Scope bounded — what's IN and OUT
- [ ] No open questions (or answered by user)
- [ ] SIMPLE: build-spec.md written
