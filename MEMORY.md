# ByteDigger Memory

## Active Work

- **Phase 1 — TS gate port** ✅ DONE (2026-04-15) — Branch `phase1-ts-gates`. Commits: `35d33d6` (port HAL TS gate as primary + bash fallback via `scripts/gate-dispatcher.sh`), `815c60d` (51 Phase 6 review findings cycle 1), `dff3702` (12 residual findings cycle 2), `967afb6` (docs: `gate_backend` flag + `GATE_BACKEND` env override). Tests: 92/92 (30 TS unit + 10 dispatcher BATS + 26 bash parity + 26 ts-via-dispatcher parity). Phase 6 satisfaction 92.3% PASS. Shadow-mode 99.9% parity preserved from HAL (c33d8a93/f4feb1b2/30583611/42f72651). Agreement `ADDC1070` — HAL-side decision doc: `~/.claude/SHARED/memory/Decisions/2026-04-15_bytedigger-halforge-unification.md`.

- **Phase 2 — NEXT** — Flip `gate_backend` default from `bash` → `shadow` for bake period (7 days), monitor `.bytedigger/gate-shadow/` for mismatches, then flip to `ts`. Add `@types/node` dev dep to clean up `tsc --noEmit` pre-existing `process is not defined` errors. Retire `scripts/build-phase-gate.sh` after `ts` is default + bake clean.

- **Phase 3 — LATER** — HALForge becomes a thin config layer over ByteDigger TS engine (Option D from decision doc). Extract shared core into a package boundary.

## Reference

- Decision doc (HAL-side): `~/.claude/SHARED/memory/Decisions/2026-04-15_bytedigger-halforge-unification.md`
- Dispatcher: `scripts/gate-dispatcher.sh` — routes to `bash` | `ts` | `shadow`
- TS gate entry: `scripts/ts/build-phase-gate.ts` (~824 lines)
- Config flag: `bytedigger.json` → `gate_backend`; env override `GATE_BACKEND`
- Shadow logs: `.bytedigger/gate-shadow/` (mismatch JSONL + `counters.db`)
- Test matrix: `bun test` (TS) + `bats tests/build-gate.bats` (parity, dual-run via `SCRIPT` env var)
