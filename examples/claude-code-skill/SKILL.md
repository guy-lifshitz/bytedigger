---
name: build
title: ByteDigger — Feature Development Pipeline
description: Full-cycle feature development with spec, TDD enforcement, and deep code review. Spec → RED → gate → GREEN, structured pipeline from requirements to production-ready code. USE WHEN building non-trivial features end-to-end. Invoked via /build.
---

# ByteDigger — Feature Development Pipeline

**PURPOSE:** Take a feature from requirements to production-ready code.
**PIPELINE:** CLASSIFY → SPEC → IMPLEMENT (TDD) → REVIEW → SYNTHESIZE (phases 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7, the same for every tier)

> Drop-in copy of the plugin skill for manual installs. Set `BYTEDIGGER_HOME`
> below to your checkout path -- all pipeline files resolve relative to it.

**Checkout location (edit this):**
```
BYTEDIGGER_HOME: ~/tools/bytedigger
```

## When to Use

- `/build "add X feature"` — full pipeline, mode auto-detected
- `/build "fix bug Y"` — classifies as SIMPLE, same flow with lighter knobs (3 review agents)
- `/build "task" --supervised` — always show checkpoints
- `/build "task" --auto` — skip all human gates
- `/build "task" --pr` — SHIP Protocol after implementation (branch → stage → commit → push → PR)
- `/build continue` — resume interrupted pipeline from last checkpoint

## Not This Skill

- Architecture research only → use a separate research tool
- Docs/config edits (<10 lines) → direct edit (Phase 0 handles this automatically)

## Complexity Routing (Phase 0)

- **TRIVIAL**: docs/config → direct edit
- **SIMPLE**: bug fix, 1-3 files → same flow as every tier, lighter knobs (3 review agents)
- **FEATURE**: non-trivial, 1-3 files → full pipeline, AUTONOMOUS
- **COMPLEX**: 4+ files, architecture → full pipeline, SUPERVISED

## TDD Discipline (non-negotiable)

Phase 5 runs spec → RED → gate → GREEN:

1. **Spec** is frozen before any test is written (Phase 4.5 output).
2. **RED** — failing tests authored from the spec; verified to FAIL before implementation.
3. **Gate** — an independent validation agent audits spec + RED for stub-passable tests, missing forcing functions, scope drift. REJECT loops back; it does not rubber-stamp.
4. **GREEN** — implementation makes the RED tests pass; tests are read-only during GREEN.

## CRITICAL: Load Pipeline

**First, render the host companion** (before `build-state.yaml` or `build-metadata.json` is written, and again on `/build continue` before `build-state.yaml` is read). In a manual install `${CLAUDE_PLUGIN_ROOT}` is empty, so the command uses `$BYTEDIGGER_HOME` — export it in the shell with your checkout path:
```bash
BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger
```
Branch on its exit code:
- Exit 0: keep following this skill as loaded (its `$BYTEDIGGER_HOME/...` paths stay authoritative); apply each `bd:local begin/end` block in stdout as an addition to the section it names (no block = no companion).
- Exit 3: STOP. Report stderr to the user and register nothing (no `build-state.yaml`, no `build-metadata.json`).
- Any other exit: use this skill exactly as loaded and print one line: `W_SKILL_COMPANION_UNAVAILABLE exit=<n> — host companion NOT applied`.

**Orchestrator reads the compact reference first:**
```
Read file: $BYTEDIGGER_HOME/commands/build.md
```
Follow it phase by phase. Do NOT improvise or skip phases.

**Per-phase instructions** for Task agents (each agent reads ONLY its phase):
```
$BYTEDIGGER_HOME/phases/phase-0-classify.md
$BYTEDIGGER_HOME/phases/phase-45-spec.md
$BYTEDIGGER_HOME/phases/phase-5-implement.md
$BYTEDIGGER_HOME/phases/phase-6-review.md
$BYTEDIGGER_HOME/phases/phase-7-synthesize.md
```

**Dynamic context (loaded as attachment, not cached):**
```
$BYTEDIGGER_HOME/templates/dynamic-context.md
```

The compact reference is the orchestrator's operating manual. Phase files are for agents. Do NOT read any other pipeline files — compact + phases + dynamic-context is the complete set.
