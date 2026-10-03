# ByteDigger Dynamic Context
> Loaded as attachment (not cached). Changes here don't bust prompt cache.
> Static rules: commands/build.md | Per-phase details: phases/

## Model Allocation Table

| Phase | SIMPLE | FEATURE | COMPLEX |
|-------|--------|---------|---------|
| 4.5 Spec | Sonnet | Sonnet | Sonnet |
| 5.1 Red | Haiku | Sonnet | Opus |
| 5.2a Gherkin | Sonnet | Sonnet | Sonnet |
| 5.2b Validate | Opus | Opus | Opus |
| 5.3 Green | Sonnet | Sonnet | Sonnet |
| 6 Review | 1x composite reviewer | 1x composite reviewer | 1x composite reviewer |
| 6 Satisfaction | Opus (3D) | Opus (5D) | 3x Opus (5D) |
| 7 Synthesize | Haiku | Haiku | Haiku |

Models are configurable via `bytedigger.json`.

## Review Reviewer

Phase 6 runs one composite reviewer for every tier (details: `phases/phase-6-review.md`).

## Satisfaction Scoring

| Complexity | Auditors | Dimensions | Threshold |
|-----------|----------|-----------|-----------|
| SIMPLE | 1x Opus | spec compliance, test quality, code quality | >=80% |
| FEATURE | 1x Opus | + completeness, Boy Scout | >=85% |
| COMPLEX | 3x Opus (parallel) | same as FEATURE, majority vote, median | >=90% |

Each returns: Verdict (PASS/FAIL) + Score (0-100%) + Top concerns

**On satisfaction >= threshold:** Write `review_complete: pass` to build-state.yaml (mandatory for Phase 7)

Thresholds are configurable via `bytedigger.json` → `satisfaction_thresholds`.
