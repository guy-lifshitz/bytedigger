---
name: build
title: ByteDigger — Feature Development Pipeline
description: Full-cycle feature development with research, architecture, TDD enforcement, and deep code review. Spec → RED → gate → GREEN, structured pipeline from requirements to production-ready code. USE WHEN building non-trivial features end-to-end. Invoked via /build.
---

# ByteDigger — Feature Development Pipeline

**PURPOSE:** Take a feature from requirements to production-ready code.
**PIPELINE:** CLASSIFY → EXPLORE → CLARIFY → ARCHITECT → SPEC → IMPLEMENT (TDD) → REVIEW → SYNTHESIZE

> Drop-in copy of the plugin skill for manual installs. Set `BYTEDIGGER_HOME`
> below to your checkout path -- all pipeline files resolve relative to it.

**Checkout location (edit this):**
```
BYTEDIGGER_HOME: ~/tools/bytedigger
```

## When to Use

- `/build "add X feature"` — full pipeline, mode auto-detected
- `/build "fix bug Y"` — classifies as SIMPLE, streamlined pipeline (skip explore/architect)
- `/build "task" --supervised` — always show checkpoints
- `/build "task" --auto` — skip all human gates
- `/build "task" --pr` — SHIP Protocol after implementation (branch → stage → commit → push → PR)
- `/build continue` — resume interrupted pipeline from last checkpoint

## Not This Skill

- Architecture research only → use a separate research tool
- Docs/config edits (<10 lines) → direct edit (Phase 0 handles this automatically)

## Complexity Routing (Phase 0)

- **TRIVIAL**: docs/config → direct edit
- **SIMPLE**: bug fix, 1-3 files → streamlined pipeline (skip Phases 2-4, 3 review agents)
- **FEATURE**: non-trivial, 1-3 files → full pipeline, AUTONOMOUS
- **COMPLEX**: 4+ files, architecture → full pipeline, SUPERVISED

## TDD Discipline (non-negotiable)

Phase 5 runs spec → RED → gate → GREEN:

1. **Spec** is frozen before any test is written (Phase 4.5 output).
2. **RED** — failing tests authored from the spec; verified to FAIL before implementation.
3. **Gate** — an independent validation agent audits spec + RED for stub-passable tests, missing forcing functions, scope drift. REJECT loops back; it does not rubber-stamp.
4. **GREEN** — implementation makes the RED tests pass; tests are read-only during GREEN.

## CRITICAL: Load Pipeline

**Orchestrator reads the compact reference first:**
```
Read file: $BYTEDIGGER_HOME/commands/build.md
```
Follow it phase by phase. Do NOT improvise or skip phases.

**Per-phase instructions** for Task agents (each agent reads ONLY its phase):
```
$BYTEDIGGER_HOME/phases/phase-0-classify.md
$BYTEDIGGER_HOME/phases/phase-1-discovery.md
$BYTEDIGGER_HOME/phases/phase-2-explore.md
$BYTEDIGGER_HOME/phases/phase-3-clarify.md
$BYTEDIGGER_HOME/phases/phase-4-architect.md
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
