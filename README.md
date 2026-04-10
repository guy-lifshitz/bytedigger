# ByteDigger

> The build pipeline AI coding tools forgot to ship.

**⚠️ Alpha** — Pipeline structure and phase docs are complete. Gate enforcement hook (`scripts/build-gate.sh`) is not yet implemented. Expect rough edges. Test reports and issues welcome.

## What it does

ByteDigger is a Claude Code plugin that forces AI agents through an 8-phase build pipeline with mandatory TDD, multi-agent code review, and gate enforcement. No skipping phases. No excuses for missing tests.

## Why it exists

Every AI coding tool lets agents free-form code. You ask for a feature, the agent dumps a wall of code, maybe runs it, calls it done. There's no design phase, no spec, no review, no tests unless you beg. That works for throwaway scripts. It falls apart on anything real.

ByteDigger fixes that. It's the process discipline layer that sits between "build me X" and the actual code generation.

## Pipeline

```
  CLASSIFY ─> DISCOVER ─> EXPLORE ─> CLARIFY ─> ARCHITECT ─> SPEC ─> IMPLEMENT ─> REVIEW ─> SYNTHESIZE
     0          0.5          1          2          3           4         5           6           7
              (deps)     (codebase)  (unknowns)  (design)   (TDD)    (code+test)  (6 agents)  (report)
```

Each arrow is a gate. Gates have pass criteria. Fail a gate, you don't move forward.

## Quick start

```bash
claude plugins add shtofadhor/bytedigger
```

Then in any project:

```bash
/build "add user authentication"
```

That's it. ByteDigger classifies complexity, picks the right pipeline depth, spins up agents, and runs through every phase automatically.

## Complexity routing

| Complexity | Phases | Reviewers | Satisfaction threshold | Auto-detected when |
|-----------|--------|-----------|----------------------|-------------------|
| SIMPLE | Skip explore/architect | 3 | 80% | Bug fix, 1-2 files |
| FEATURE | Full pipeline | 6 | 85% | New capability, clear scope |
| COMPLEX | Full + Opus voting | 6 + 3 voting | 90% | 4+ files, architecture decisions |

Complexity is detected in Phase 0 based on file count, dependency graph, and whether the task touches architectural boundaries. You can override it: `/build --complexity COMPLEX "task"`.

## Key features

- **12 gate checkpoints** enforced by hooks -- agents can't skip phases, period
- **TDD mandatory** -- 12 hardcoded excuses rejected ("tests not applicable", "will add later", etc.)
- **3-6 specialized reviewers** per build: security, test coverage, type safety, simplification, correctness, edge cases
- **Semantic skip detection** -- catches reviewers rubber-stamping with "LGTM looks good"
- **Test integrity guard** -- blocks assertion gaming (empty assertions, `expect(true).toBe(true)`)
- **Resumable state** -- `/build continue` picks up from last checkpoint after crash or context limit
- **Worktree isolation** -- parallel builds run on separate git branches, no conflicts
- **Constitution system** -- project-specific rules (style guide, forbidden patterns, arch constraints) loaded into every agent
- **Observability** -- structured JSON events for every phase transition, gate pass/fail, agent spawn
- **`--dry-run` mode** -- see the full plan (phases, agents, estimated tokens) without spending anything
- **Learning interface** -- pluggable system that extracts patterns from completed builds and injects relevant learnings into future ones

## Configuration

Drop a `bytedigger.json` in your project root:

```json
{
  "validation_model": "opus",
  "agent_model": "sonnet",
  "satisfaction_thresholds": {
    "SIMPLE": 80,
    "FEATURE": 85,
    "COMPLEX": 90
  },
  "gates_enabled": true,
  "tdd_mandatory": true,
  "max_review_rounds": 3,
  "worktree_default": false
}
```

All fields are optional. Defaults are sane. Override `tdd_mandatory` at your own risk.

### Learning interface

ByteDigger can remember what worked across builds. After each completed pipeline, Phase 7 extracts patterns (what broke, what helped, anti-patterns discovered). Before the next build, Phase 0 injects relevant learnings into the agent context so it doesn't repeat mistakes.

Configure the backend in `bytedigger.json`:

```json
{
  "learning": {
    "backend": "file",
    "store_path": ".bytedigger/learnings"
  }
}
```

| Backend | Storage | Use case |
|---------|---------|----------|
| `none` | Disabled | Default. Opt-in only. |
| `file` | `.bytedigger/learnings/{category}.md` | Simple, git-trackable, human-readable |
| `sqlite` | Extension point | Not yet implemented |

The learning store script (`scripts/learning-store.sh`) supports two subcommands: `extract` (save a learning) and `inject` (retrieve relevant learnings by keyword match).

## How it compares

|  | ByteDigger | Aider | SWE-agent | Codex CLI | Devin |
|---|:-:|:-:|:-:|:-:|:-:|
| Phased pipeline | ✓ | - | - | - | partial |
| TDD enforcement | ✓ | - | - | - | - |
| Multi-agent review | ✓ | - | - | - | - |
| Gate enforcement | ✓ | - | - | - | - |
| Resumable builds | ✓ | - | - | - | ✓ |
| Worktree isolation | ✓ | - | - | - | - |
| Open source | ✓ | ✓ | ✓ | ✓ | - |

## Commands

| Command | What it does |
|---------|-------------|
| `/build "task"` | Run the full pipeline |
| `/build continue` | Resume from last checkpoint |
| `/build --dry-run "task"` | Preview plan without running |
| `/build --init` | Generate project constitution |
| `/build --worktree "task"` | Isolate build in a git worktree |
| `/build --complexity COMPLEX "task"` | Force complexity tier |

## How gates work

Each gate checks specific conditions before allowing the next phase. Examples:

- **Phase 0 gate:** Dependency check passes, complexity classified, no circular imports in target area
- **Phase 4 gate:** Test spec exists, covers happy path + at least 2 edge cases, no empty assertions
- **Phase 5 gate:** All tests pass, coverage meets threshold, no skipped tests
- **Phase 6 gate:** All reviewers score above satisfaction threshold, no unresolved security findings

If a gate fails, the pipeline loops back with specific feedback. Three failures on the same gate = pipeline stops and reports what went wrong.

## Contributing

PRs welcome. Open an issue first for anything beyond a typo fix -- I'd rather discuss the approach before you write the code.

The irony of a build-process tool accepting drive-by PRs without process would not be lost on me.

## License

MIT

---

Built by [Guy Lifshitz](https://github.com/shtofadhor). Inspired by the idea that AI agents need process discipline, not just intelligence.
