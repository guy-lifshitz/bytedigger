---
name: ByteDigger
title: ByteDigger — Feature Development Pipeline
description: Full-cycle feature development with research, architecture, TDD enforcement, and deep code review. Structured pipeline from requirements to production-ready code. USE WHEN building non-trivial features end-to-end. Invoked via /build.
---

# ByteDigger — Feature Development Pipeline

**PURPOSE:** Take a feature from requirements to production-ready code.
**PIPELINE:** CLASSIFY → EXPLORE → CLARIFY → ARCHITECT → SPEC → IMPLEMENT (TDD) → REVIEW → SYNTHESIZE

## When to Use

- `/build "add X feature"` — full pipeline, mode auto-detected
- `/build "fix bug Y"` — classifies as SIMPLE, streamlined pipeline (skip explore/architect)
- `/build "task" --supervised` — always show checkpoints
- `/build "task" --auto` — skip all human gates
- `/build "task" --pr` — SHIP Protocol after implementation (commit → branch → PR → review)
- `/build --init` — create project constitution
- `/build "task" --dry-run` — classify and show execution plan without running pipeline
- `/build "task" --atomic-commits` — enable Red/Green/Refactor commits at each TDD step (default ON for COMPLEX)
- `/build "task" --worktree` — isolate work in a git worktree (auto-enabled for COMPLEX + --pr). Main branch stays clean.
- `/build continue` — resume interrupted pipeline from last checkpoint

## Not This Skill

- Architecture research only → use a separate research tool
- Docs/config edits (<10 lines) → direct edit (Phase 0 handles this automatically)

## Complexity Routing (Phase 0)

- **TRIVIAL**: docs/config → direct edit
- **SIMPLE**: bug fix, 1-3 files → streamlined 8-agent pipeline (skip Phases 2-4, 3 review agents, 1-2 Gherkin scenarios)
- **FEATURE**: non-trivial, 1-3 files → full pipeline, AUTONOMOUS
- **COMPLEX**: 4+ files, architecture → full pipeline, SUPERVISED

## CRITICAL: Load Pipeline

**Orchestrator reads the compact reference first:**
```
Read file: commands/build.md
```
Follow it phase by phase. Do NOT improvise or skip phases.

**Per-phase instructions** for Task agents (each agent reads ONLY its phase):
```
phases/phase-0-classify.md
phases/phase-1-discovery.md
phases/phase-2-explore.md
phases/phase-3-clarify.md
phases/phase-4-architect.md
phases/phase-45-spec.md
phases/phase-5-implement.md
phases/phase-6-review.md
phases/phase-7-synthesize.md
```

**Dynamic context (loaded as attachment, not cached):**
```
templates/dynamic-context.md
```
Contains model allocation, review agent roster, satisfaction scoring — changes here don't bust prompt cache.

The compact reference is the orchestrator's operating manual. Phase files are for agents. Do NOT read any other pipeline files — compact + phases + dynamic-context is the complete set.
