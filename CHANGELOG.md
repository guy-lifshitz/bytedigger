# Changelog

All notable changes to ByteDigger are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning: the plugin (`.claude-plugin/plugin.json`), the npm pointer package
(`npm/`), and the Python engine (`engine_py/pyproject.toml`) all version together
as `0.1.x` until the engine API stabilizes. The historical `v1.0.0` tag predates
the Python engine and refers to the original bash plugin (see Pre-history).

## [Unreleased]

## [0.1.1] — 2026-07-27

### Added

- **PyPI pointer package built from the repo** (`packaging/pypi-pointer/`). The `bytedigger`
  name was previously published from an untracked working copy, and its pin drifted: `bytedigger
  0.1.0` required `bytedigger-engine==0.1.0` while the engine had moved to `0.1.1`, so
  `pip install bytedigger` handed out a stale engine. The source now lives in the repo and its
  version is a sixth declaration checked by `scripts/version_parity.py`.

### Added

- **Spec-writer rule 9** — NEW-symbol citation-form ban in the spec-writer prompt: a symbol that does not exist yet may not be cited in path:line form (ported from HAL GH934). (#44)
- **Agent-SDK stderr-tail capture** — LLM subprocess failures now carry a stderr tail and are classified as external-outage vs build-fault (ported from HAL GH933). (#45)
- **Starter `constitution.md`** — shipped in the repo root so the `constitution_path` config default resolves out of the box. (#43)
- Digger-1983-style promo card in docs. (#37)

### Changed

- README rewritten around the software-factory thesis: value-first hero, 6-axis comparison table, shift-left security, deterministic-first economics. (#28, #33, #34, #36, #41, #42)
- `docs/article.md` rewritten for the engine era — verified specs, killed review loop, economics. (#27, #35)
- Dependencies bumped across the board (TypeScript 7.0.2, bun-types 1.3.14, DBOS 2.27.0). (#40)

## [0.1.0] — 2026-07-16

First release built around the **Python engine** (`engine_py/`) — a deterministic
state machine that drives the whole pipeline (research → spec → failing tests →
implementation → review) with TDD at the core and LLM agents as replaceable
workers. Matches the `0.1.0` version of `bytedigger-engine` (PyPI /
`engine_py/pyproject.toml`) and the `bytedigger` npm pointer package.

### Added

- **Engine core** — strict manifest-driven extraction of the engine from its upstream host: state machine, event log, verdict gates, deterministic lints (stub-passability, cite-verify, scope allowlist), crash-resume from success sentinels. (#15)
- **Test suite** — 321 hermetic pytest tests imported with the engine (no DBOS dependency in the test lane). (#21)
- **Product wrapper** — engine README, keyless verified-TDD demo (`examples/verified-tdd-run/`), custom-backend example (`examples/library/custom_backend.py`), backend docs. (#22, #23)
- **Security extraction** — OWASP ASVS-derived secure-codegen defaults, semgrep + gitleaks gate assets shipped in the package, security policy docs, path-closure test. (#24)
- **CONTRIBUTING.md** and the npm pointer package. (#25)
- Python 3.9 test-collection compatibility; quickstart pip note. (#32)

### Changed

- The Claude Code plugin (`/build`) now fronts the Python engine; the TS/bash gate scripts remain as the plugin's phase-gate layer (see Pre-history).
- Plugin and marketplace manifests aligned to `0.1.0` (previously `1.0.0`, a leftover from the pre-engine plugin).

---

## Pre-history (before the Python engine)

The project began as a Claude Code plugin with a bash phase-gate pipeline
(tagged `v1.0.0`, 2026-04-10), then grew a TypeScript gate backend. That work
now lives on as the plugin's gate layer under `scripts/`. Condensed timeline:

### Phase 2 — F7 (2026-04-16)

- Observability emit wiring: 12 wire points across `dispatchPhase`, `mainCLI`, and `checkPhase6` calling the `emit.ts` wrappers; 13 new tests (109/109 passing); dead-import and switch-arm cleanups.

### Phase 2 — Sprint B (2026-04-16)

- Post-review gate (F3): semantic-skip enforcement in `checkPhase6`; 18 forbidden phrases in `semantic-skip-phrases.json`.
- Observability events (F7): `emit.ts` JSONL event streaming to stderr.
- Active Work injection (F9): `memory-reader.ts` extracts `## Active Work` from project MEMORY.md (caps: 10 items / 500 chars; flag `activeWorkInjection`).
- Reviewers config (F10): `reviewers.mode` (toolkit/generic/auto).
- 96 tests total, satisfaction 87%.

### Phase 2 — Sprint A (2026-04-16)

- `omitProjectContext` flag (skip CLAUDE.md injection, saves 10–45K tokens/build); TRIVIAL-tier skip path; state-reader hardening (`StateReadError`, TOCTOU guard); config parsing helpers; cross-platform file-freshness fix (`mtimeMs`); 43 tests passing.

### Phase 1 (2026-04-15)

- TypeScript phase-gate backend: `scripts/ts/build-phase-gate.ts` (~824 lines) with `config-reader.ts` / `state-reader.ts`, 30 TS unit tests, 26 bash-parity tests.
- `gate_backend` config flag (`"bash"` default / `"ts"` / `"shadow"` A/B mode with mismatch logging to `.bytedigger/gate-shadow/`), `GATE_BACKEND` env override, fail-closed `scripts/gate-dispatcher.sh`.
- Security: removed a credential leak from phase artifacts; hardened `ship.sh` against command injection.
- Gate repairs: `findings_skipped` / `post_review_gate` hard-block; mandatory worktree enforcement on main/master; AUTONOMOUS pause-regression fix; 116/116 BATS tests green.

### v1.0.0 (2026-04-10)

- Initial release: phased build pipeline for AI code generation as a Claude Code plugin — bash gate enforcement hook, phase transition validation with TDD.
