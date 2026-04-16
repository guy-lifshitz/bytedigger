# Changelog

All notable changes to ByteDigger are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Phase 2 — Sprint A (2026-04-16)

#### Added

- **`omitProjectContext` config flag** — Explorer and Architect agents can skip CLAUDE.md injection to save 10-45K tokens per build. Default: `false` (backward compatible). Controlled via `bytedigger.json`.
- **TRIVIAL tier skip path** — `checkPhase7` gate now bypasses `review_complete` check for TRIVIAL complexity builds, enabling direct Phase 0 → edit → Phase 7/8 flow for trivial fixes.
- **State-reader hardening** (F4) — New `StateReadError` class with `Error.cause` chain distinguishes file-not-found (returns `null`) from file-unreadable (throws). TOCTOU race protection via ENOENT check. Integrated into `dispatchPhase` for primary state read.
- **Config parsing helpers** — `parseBool` for boolean fields, `parseReviewerCount` with NaN guard for numeric config values.
- **`ByteDiggerConfig` interface export** — Now exported from main build-phase-gate module for type safety in consuming code.

#### Fixed

- **Cross-platform file freshness** — Changed `birthtimeMs` → `mtimeMs` for consistent file age detection across macOS and Linux.
- **Hard-coded test paths** — Worktree path resolution now uses `import.meta.url` instead of hard-coded paths (fixes Phase 1 regression).
- **10 Phase 6 review findings** — Standardized error logging, added numeric guards, resolved all medium-priority quality improvements.

#### Tests

- 43 tests passing (0 failures). State-reader: 9 new unit tests + 2 integration scenarios.
- TRIVIAL skip path: 3 test cases confirming bypass behavior.

---

### Phase 1 (2026-04-15)

#### Added

- **TypeScript phase gate backend** (Phase 1 of ByteDigger-HALForge unification): ported HAL's TS gate engine into `scripts/ts/build-phase-gate.ts` (~824 lines) with supporting libs (`config-reader.ts`, `state-reader.ts`) and 30 TS unit tests. Bash-parity contract enforced by 26 dispatcher parity tests.
- **`gate_backend` config flag** in `bytedigger.json` — selects gate engine (`"bash"` default, `"ts"` opt-in, `"shadow"` for A/B parity validation). Fail-closed on unknown values.
- **Shadow mode** (`gate_backend: shadow`) — runs bash + ts in parallel, returns bash verdict as source of truth, logs mismatches to `.bytedigger/gate-shadow/`. Preserves HAL-side reliability fixes: mismatch-only JSONL, SQLite counters, fail-closed on missing `bun`, EAGAIN single retry.
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

