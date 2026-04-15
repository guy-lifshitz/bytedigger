# Changelog

All notable changes to ByteDigger are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **TypeScript phase gate backend** (Phase 1 of ByteDigger-HALForge unification): ported HAL's TS gate engine into `scripts/ts/build-phase-gate.ts` (~824 lines) with supporting libs (`config-reader.ts`, `state-reader.ts`) and 30 TS unit tests. Bash-parity contract enforced by 26 dispatcher parity tests.
- **`gate_backend` config flag** in `bytedigger.json` — selects gate engine (`"bash"` default, `"ts"` opt-in). Fail-closed on unknown values.
- **`GATE_BACKEND` environment override** — per-run override of the config flag (env wins over JSON). Useful for CI experiments and shadow comparisons.
- **`scripts/gate-dispatcher.sh`** — fail-closed dispatcher wired into `hooks/hooks.json`; preserves exact backend exit codes, WARNs to stderr on config parse failure (never silently defaults), and fails closed on missing `bun` or unknown backend.
- 10 BATS dispatcher tests + 26 ts-via-dispatcher parity tests (92/92 total in this sprint).

### Security

- Removed HAL credential leak from phase artifacts
- Hardened ship.sh command injection attack surface via argument sanitization

### Fixed

- **P1 Gates (Phase 6 checks)**: Repaired dead control flow in post-review validation; findings_skipped and post_review_gate now hard-block non-compliant states; scratchpad stale detection enforced at Phase 4 gate
- **P2 Enforcement (Worktree)**: Added mandatory worktree enforcement on main/master branches; loop bypass closure prevents re-entry during active build
- **P3 Robustness**: Simplified drain_stdin anti-pattern; removed bc dependency (replaced with bash arithmetic); post-deploy stub tests now pass
- **P4 Polish**: Resolved eight medium audit findings; unified authorship attribution to shtofadhor across all phases and scripts
- **Regression**: Ported AUTONOMOUS pause regression fix — explicit flow-mode checks at pause points (Phases 3, 4, 4.5, 7) prevent silent pauses during autonomous mode

### Changed

- Phase 0.5 field name consistency across build-state.yaml serialization
- Phase 6 hard-block validation raises errors instead of warnings for findings_skipped and post_review_gate states
- Pre-build gate now enforces worktree policy before phase execution begins

### Added

- 7 new BATS tests for gate validation and enforcement
- Test suite now 116/116 green across all phases

