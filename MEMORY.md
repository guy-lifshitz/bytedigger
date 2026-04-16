# ByteDigger Memory

## Active Work

- **Phase 1 — TS gate port** ✅ DONE (2026-04-15) — Branch `phase1-ts-gates`. Commits: `35d33d6` (port HAL TS gate as primary + bash fallback via `scripts/gate-dispatcher.sh`), `815c60d` (51 Phase 6 review findings cycle 1), `dff3702` (12 residual findings cycle 2), `967afb6` (docs: `gate_backend` flag + `GATE_BACKEND` env override). Tests: 92/92 (30 TS unit + 10 dispatcher BATS + 26 bash parity + 26 ts-via-dispatcher parity). Phase 6 satisfaction 92.3% PASS. Shadow-mode 99.9% parity preserved from HAL (c33d8a93/f4feb1b2/30583611/42f72651). Agreement `ADDC1070`.

- **Phase 2 — Sprint A** ✅ DONE (2026-04-16) — PR #5 merged. Commit `1e706ed`. Features: (F1) `omitProjectContext` config flag — Explorer/Architect skip CLAUDE.md to save 10-45K tokens per build. Default: false. (F2) TRIVIAL tier skip — `checkPhase7` bypasses `review_complete` for trivial builds. (F4) State-reader hardening — `StateReadError` class + `readStateFieldOrThrow`, TOCTOU protection. Review findings: parseBool, parseReviewerCount NaN guard, birthtimeMs→mtimeMs, ByteDiggerConfig export, cross-platform consistency. Tests: 43/43. Docs updated: README (config), CHANGELOG (Phase 2 section), MEMORY. Next: flip `gate_backend` to `shadow`, then `ts` after bake.

- **Phase 2 — Sprint B** ✅ DONE (2026-04-16) — F3: Post-review gate with semantic-skip enforcement (18 forbidden phrases in semantic-skip-phrases.json, Boy Scout Rule in checkPhase6). F7: Observability events module (emit.ts, JSONL to stderr, HAL forwarding). F9: Active Work injection from MEMORY.md (10 items, 500 chars, config flag `activeWorkInjection`). F10: Reviewers config (ReviewersConfig interface, `reviewers.mode` in bytedigger.json). Tests: 96 total (53 new), 87% satisfaction. Docs: CHANGELOG (Sprint B section), README (new config flags), MEMORY updated.

- **Phase 2 — F7** ✅ DONE (2026-04-16) — Observability emit wiring: 12 wire points (dispatchPhase + mainCLI + checkPhase6) calling 5 emit.ts wrappers. Fixed dead-code gap from Sprint B. Tests: 13 new (WIRE-1..10 + WIRE-2b/11/12), 109/109 passing. Stats: ~55 adds + ~12 mods in build-phase-gate.ts, ~580 adds in test file, 9 dead-import removal (readStateFieldOrThrow, StateReadError). Review: 6 reviewers, 20 findings fixed, 3 deferred to Sprint C (agreement 94AF6D1F). Satisfaction 97%. Boy Scout: switch-arm collapse (0/1/2/3/8/default merged), comment cleanup (WP-N labels stripped, WHY-only comments). Docs: CHANGELOG (F7 section), MEMORY updated. Reference: agreement 44BE98DD.

- **Phase 2 — Sprint C** — NEXT — (F6) SQLite3 bridge integration. (F4) TOCTOU wiring (from agreement 94AF6D1F). (F8) Security classification module. Plus 4 new items from agreement 94AF6D1F: (F4b) global.missingFields metadata tracking, (F4c) narrow metadata type schema, (F7b) emitVerdict helper wrapper. Flip `gate_backend` default from `bash` → `shadow` for 7-day bake period, monitor `.bytedigger/gate-shadow/` for mismatches, then flip to `ts`. Add `@types/node` dev dep to clean up `tsc --noEmit` pre-existing `process is not defined` errors. Retire `scripts/build-phase-gate.sh` after `ts` is default + bake clean.

- **Phase 3 — LATER** — HALForge becomes a thin config layer over ByteDigger TS engine (Option D). Extract shared core into a package boundary.

## Reference

- Decision doc (HAL-side): `~/.claude/SHARED/memory/Decisions/2026-04-15_bytedigger-halforge-unification.md`
- Dispatcher: `scripts/gate-dispatcher.sh` — routes to `bash` | `ts` | `shadow`
- TS gate entry: `scripts/ts/build-phase-gate.ts` (~824 lines)
- Config flag: `bytedigger.json` → `gate_backend`; env override `GATE_BACKEND`
- Shadow logs: `.bytedigger/gate-shadow/` (mismatch JSONL + `counters.db`)
- Test matrix: `bun test` (TS) + `bats tests/build-gate.bats` (parity, dual-run via `SCRIPT` env var)
