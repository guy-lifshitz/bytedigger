> This is Phase 3 of the ByteDigger /build pipeline.
> Full pipeline: commands/build.md + phases/ | Compact orchestrator reference: commands/build.md
> **Skip if SIMPLE** — SIMPLE tasks go directly from Phase 1 to Phase 5.

# Phase 3: CLARIFYING QUESTIONS

**First ACTION — Update current_phase:**
```bash
python3 -c "import re,datetime,pathlib;f=pathlib.Path('build-state.yaml');t=f.read_text();t=re.sub(r'current_phase:.*','current_phase: \"3\"',t);t=re.sub(r'last_updated:.*',f'last_updated: \"{datetime.datetime.utcnow().isoformat()}Z\"',t);f.write_text(t);print('current_phase → 3')"
```

**Scratchpad Verification:** Before proceeding, verify scratchpad exists:
```bash
SCRATCHPAD=$(python3 -c "import re,pathlib;m=re.search(r'scratchpad_dir:\s*[\"'\'']*([^\"'\''\\n]+)',pathlib.Path('build-state.yaml').read_text());print(m.group(1).strip() if m else '')")
[ -n "$SCRATCHPAD" ] && { [ -d "$SCRATCHPAD" ] || mkdir -p "$SCRATCHPAD"/{research,architecture,specs,tests,reviews}; }
```

You are an agent filling gaps and resolving ambiguities before architecture and implementation.

## What You Receive

- Feature request text
- Codebase exploration findings (from Phase 2)
- Constitution block
- Mode (AUTONOMOUS / SUPERVISED)

## What You Must Produce

1. List of ambiguities and gaps identified
2. Resolved answers (from user or documented assumptions)
3. Edge cases identified

## Actions

1. Review codebase findings + original request
2. Identify underspecified aspects: edge cases, error handling, integration points, scope

### SUPERVISED Mode
- Present questions to user, wait for answers

### AUTONOMOUS Mode
- Make assumptions, document them explicitly, proceed

## Checkpoint

```
Done: EXPLORE + CLARIFY | Assumptions: [list] | Next: Architecture
```

## Model Selection

| Complexity | Model |
|-----------|-------|
| FEATURE | Haiku |
| COMPLEX | Sonnet |

**Output Schema:** End your report with: `Scope:` / `Result:` / `Key files:` / `Files changed:` / `Issues:`. Work silently during tool use — EXCEPT in SUPERVISED mode, present questions to user interactively before the final report.

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

- [ ] All ambiguities resolved or documented as explicit assumptions
- [ ] Edge cases identified
