---
name: ByteDigger
title: ByteDigger — Feature Development Pipeline
description: Full-cycle feature development with spec, TDD enforcement, and deep code review. Structured pipeline from requirements to production-ready code. USE WHEN building non-trivial features end-to-end. Invoked via /build.
metadata:
  overridable: "project-conventions"
---

# ByteDigger — Feature Development Pipeline

**PURPOSE:** Take a feature from requirements to production-ready code.
**PIPELINE:** CLASSIFY → SPEC → IMPLEMENT (TDD) → REVIEW → SYNTHESIZE (phases 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7, the same for every tier)

## When to Use

- `/build "add X feature"` — full pipeline, mode auto-detected
- `/build "fix bug Y"` — classifies as SIMPLE, same flow with lighter knobs (3 review agents, fewer Gherkin scenarios)
- `/build "task" --supervised` — always show checkpoints
- `/build "task" --auto` — skip all human gates
- `/build "task" --pr` — SHIP Protocol after implementation (branch → stage → commit → push → PR)
- `/build --init` — create project constitution
- `/build "task" --dry-run` — classify and show execution plan without running pipeline
- `/build "task" --atomic-commits` — enable Red/Green/Refactor commits at each TDD step (default ON for COMPLEX)
- `/build "task" --worktree` — isolate work in a git worktree (auto-enabled for COMPLEX + --pr). Main branch stays clean.
- `/build "task" --issue 42` — bind the build to issue #42 (branch `gh<N>-<slug>` for every tier); required when the repo's readiness policy is on
- `/build continue` — resume interrupted pipeline from last checkpoint (also from `awaiting_approval`)

## Not This Skill

- Architecture research only → use a separate research tool
- Docs/config edits (<10 lines) → direct edit (Phase 0 handles this automatically)

## Complexity Routing (Phase 0)

- **TRIVIAL**: docs/config → direct edit
- **SIMPLE**: bug fix, 1-3 files → same flow as every tier, lighter knobs (3 review agents, 1-2 Gherkin scenarios)
- **FEATURE**: non-trivial, 1-3 files → full pipeline, AUTONOMOUS
- **COMPLEX**: 4+ files, architecture → full pipeline, SUPERVISED

## Readiness Gate (bd#117)

Opt-in per repo (`readiness.required` in `bytedigger.json` on the push target's default branch; see `docs/configuration.md`). Phase 0 parses `--issue <N>` and runs `scripts/readiness check --stage start --json` only to read `readiness.required`; exit 3 is expected here (no spec record yet, or not yet on the `gh<N>-` branch) and is not a stop — read the JSON and continue; exit 4 warns and continues. When `readiness.required` is true: no issue → STOP (`no_issue`); `--issue <N>` on a branch that parses to another number → STOP (`issue_mismatch`); otherwise the build runs on `gh<N>-<slug>`.

**Start gate** — before the first write outside the scratchpad: TRIVIAL (after a minimal `build-spec.md`, before the direct edit), SIMPLE, FEATURE and COMPLEX (after Phase 4.5). Run `scripts/readiness check --stage start --spec ./build-spec.md`: 0 continues; 3 runs `scripts/readiness post --spec ./build-spec.md`, sets `current_phase: awaiting_approval` and `awaiting_stage: start`, prints `Waiting for "<label>" on #<N>` and STOPs in every mode, AUTONOMOUS included; anything else warns and continues. The ship gate (`scripts/ship.sh --pr`, engine Phase 8) then refuses unapproved work deterministically.

## CRITICAL: Load Pipeline

**First, render the host companion** (before `build-state.yaml` or `build-metadata.json` is written, and again on `/build continue` before `build-state.yaml` is read):
```bash
BD_ROOT="${CLAUDE_PLUGIN_ROOT}"; "${BD_ROOT:-$BYTEDIGGER_HOME}/scripts/skill-companion" render --core bytedigger
```
Branch on its exit code:
- Exit 0: keep following this skill as loaded; apply each `bd:local begin/end` block in stdout as an addition to the section it names (no block = no companion, nothing to apply).
- Exit 3: STOP. Report stderr to the user and register nothing (no `build-state.yaml`, no `build-metadata.json`).
- Any other exit: use this skill exactly as loaded and print one line: `W_SKILL_COMPANION_UNAVAILABLE exit=<n> — host companion NOT applied`.

**Orchestrator reads the compact reference first:**
```
Read file: commands/build.md
```
Follow it phase by phase. Do NOT improvise or skip phases.

**Per-phase instructions** for Task agents (each agent reads ONLY its phase):
```
phases/phase-0-classify.md
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

## Project conventions
