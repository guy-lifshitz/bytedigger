# ByteDigger Dynamic Context
> Loaded as attachment (not cached). Changes here don't bust prompt cache.
> Static rules: commands/build.md | Per-phase details: phases/

## Model Allocation Table

| Phase | SIMPLE | FEATURE | COMPLEX |
|-------|--------|---------|---------|
| 1 Spec | Orchestrator | — | — |
| 2 Explore | — | Haiku | Sonnet |
| 3 Clarify | — | Haiku | Sonnet |
| 4 Architect | — | Opus | Opus |
| 4.5 Spec | — | Sonnet | Sonnet |
| 5.1 Red | Haiku | Sonnet | Opus |
| 5.2a Gherkin | Sonnet | Sonnet | Sonnet |
| 5.2b Validate | Opus | Opus | Opus |
| 5.3 Green | Sonnet | Sonnet | Sonnet |
| 6 Review | 3x reviewers | 6x reviewers | 6x reviewers |
| 6 Satisfaction | Opus (3D) | Opus (5D) | 3x Opus (5D) |
| 7 Synthesize | Haiku | Haiku | Haiku |

Models are configurable via `bytedigger.json`.

## Review Agent Roster

| Complexity | Agents | List |
|-----------|--------|------|
| SIMPLE | 3 | code-reviewer, silent-failure-hunter, pr-test-analyzer |
| FEATURE/COMPLEX | 6 | + comment-analyzer, type-design-analyzer, code-simplifier |
| DevOps (all) | +3 | InfraSec (Sonnet), OpsSafety (Sonnet), QualityCost (Haiku) |

Launch all parallel (`run_in_background: true`) | Log: `phase_6_reviewers_launched: <N> | phase_6_reviewers_expected: <3|4|6|7>` | If launched != expected → STOP

## Satisfaction Scoring

| Complexity | Auditors | Dimensions | Threshold |
|-----------|----------|-----------|-----------|
| SIMPLE | 1x Opus | spec compliance, test quality, code quality | >=80% |
| FEATURE | 1x Opus | + completeness, Boy Scout | >=85% |
| COMPLEX | 3x Opus (parallel) | same as FEATURE, majority vote, median | >=90% |

Each returns: Verdict (PASS/FAIL) + Score (0-100%) + Top concerns

**On satisfaction >= threshold:** Write `review_complete: pass` to build-state.yaml (mandatory for Phase 7)

Thresholds are configurable via `bytedigger.json` → `satisfaction_thresholds`.
