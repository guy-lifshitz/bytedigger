# Changelog

All notable changes to ByteDigger are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning: the plugin (`.claude-plugin/plugin.json`), the npm pointer package
(`npm/`), and the Python engine (`engine_py/pyproject.toml`) all version together
as `0.1.x` until the engine API stabilizes. The historical `v1.0.0` tag predates
the Python engine and refers to the original bash plugin (see Pre-history).

## [Unreleased]

### Changed — BREAKING

- **`phase_5_implement` now refuses to run unless the acceptance criteria it is about to implement
  were recorded first.** Run it with `--event-log PATH`, where `PATH` is the same log the spec
  phase (`phase_45_spec` or `phase_45_spec_lite`) wrote to earlier in the build. Without that, the
  phase stops before it starts and reports `E_ORACLE_UNFROZEN` with exit code 1.

  **Who this affects.** Only callers that invoke `python3 run.py --workflow phase_5_implement`
  directly *without* `--event-log`. If you drive builds through the supplied driver, nothing
  changes — it already passes `--event-log` on every phase. Measured before shipping: no such
  caller exists in this repository, and no production build path omits the flag.

  **What to do.** Add `--event-log` (and a `--run-id` shared with the spec phase) to the
  invocation, so the run has a log to look the recorded criteria up in:

  ```
  python3 run.py --workflow phase_45_spec   --run-id BUILD1 --event-log .bytedigger/events.jsonl ...
  python3 run.py --workflow phase_5_implement --run-id BUILD1 --event-log .bytedigger/events.jsonl ...
  ```

  **Why it is not optional.** The point of the check is that the criteria cannot be edited by the
  actor being judged: they are hashed when the spec phase ends and re-checked before and after the
  implementation runs, so an implementation that rewrote its own acceptance criteria mid-flight is
  refused rather than accepted. A run with no log has nowhere to have recorded them, so it cannot
  be distinguished from one whose criteria were removed — and treating "nothing was recorded" as
  "nothing changed" would make the check announce a pass it never performed. Other workflows are
  unaffected and still run with or without a log. (#8)

### Fixed

- **The install-platform scan no longer reads local build residue as a shipping platform.**
  `scan_domain` walked the tree without asking git about a file's status, so leftovers from a
  local package build (measured: `packaging/pypi-pointer/bytedigger.egg-info/`, untracked and
  git-ignored) failed the platform-registry guard — green in CI's clean checkout, red on the tree
  of whoever built the package. The domain is now derived from git (`git check-ignore --stdin`)
  rather than from a list of build-artifact directory names, so the next packaging format does
  not reproduce it. In a tree without git (unpacked sdist, exported archive) the scan fails open:
  the full declared domain, exactly as before. (#33)

## [0.1.2] — 2026-07-29

### Fixed

- **`pip install bytedigger` now prints and installs a command that works.** The npm wrapper
  advertised an install form that did not resolve, and the same broken form was repeated in the
  PyPI pointer README and in `package_meta.install_hint`. All install forms now come from one
  canonical dictionary (`scripts/install_forms.py`), and the set of places allowed to state one is
  an enforced registry — a new site that hand-writes a form fails the suite. (#20, #25)
- **Pointer pin follows the canonical version.** `bytedigger` pinned `bytedigger-engine` to a
  literal that could drift from the engine's actual version; the pin is now derived and checked by
  `version_parity.py`. This is the defect that made `bytedigger 0.1.0` hand out a stale engine. (#19)
- **Three phases and two checks no longer report a pass they never earned** — a gate that could not
  reach its subject reported success instead of refusing. (#5)
- **CI on `main` unwedged**, and a silent-skip path made loud. (#14, #15)

### Added

- **Conformance package (`BD-L2`)** — shared contracts and packaging for falsifiable conformance
  checks. (#26)
- **Attested authorship and inputs (`BD-L3`)** — attestation is emitted if and only if a dispatch
  actually happened. (#31)
- **Engine conformance emissions** — `phase`, `run_identity`, `phase_artifacts`. (#23)
- **Event-log path override, worktree in-use veto, and Red-cell shape lint**, ported from upstream. (#16)

### Changed

- Clean-room verification runs on `ubuntu-latest` instead of a self-hosted docker label. (#6)

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
