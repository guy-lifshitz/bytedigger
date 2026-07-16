# ByteDigger

ByteDigger is a phased, gate-enforced build pipeline for AI code generation.
TDD is mandatory, reviews are scored, and skipping is blocked: an event-sourced
RED → validate → GREEN state machine with deterministic gates that make
reward-hacking expensive instead of easy.

Most agentic coding failures aren't capability failures — they're verification
failures. Agents game their own acceptance signal: weakening assertions,
mocking the unit under test, passing a scoped suite while breaking the full
one. ByteDigger treats those as first-class failure modes and gates them with
cheap deterministic checks, not with more LLM judgment.

## What it does

- **Frozen state machine, single mutation point.** Every phase (research → spec
  → RED → validate → GREEN → review → synthesize) is a registered workflow run
  by one engine. State is derived by replaying an append-only JSONL event log —
  there is no mutable state file to drift or race.
- **RED-first, hard-gated.** A failing test is written and independently
  verified before implementation. An LLM validation gate sits between RED and
  GREEN and cannot be skipped.
- **Deterministic anti-gaming lints, run as code:**
  - stub-passability: rejects a RED that mocks its own unit under test
  - test-integrity diff guard: classifies post-RED test edits, hard-fails on
    assertion gaming
  - scope-inverse: flags implementation writes outside the spec's file allowlist
  - spec-cite / spec-coverage / helper-extraction / suite-safety and friends
- **Durable execution, no required services.** The default durable backend is
  `native` (stdlib, per-phase sentinels): a killed run resumes from its last
  completed step. An optional [DBOS](https://www.dbos.dev)-backed backend is
  available via the `[dbos]` extra.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate

pip install .              # core: no runtime dependencies
pip install ".[test]"      # + pytest, to run the suite
pip install ".[dbos]"      # optional: DBOS durable-execution backend
pip install ".[agentic-pydantic]"  # optional: provider-agnostic agentic backend
```

The core installs with **zero runtime dependencies** — stdlib plus the LLM
backend of your choice. DBOS is strictly optional: the engine defaults to the
dependency-free `native` durable backend; install `[dbos]` only if you set
`HAL_ENGINE_DURABLE_BACKEND=dbos`.

## Quickstart

```bash
# list registered workflows
bytedigger --list

# round-trip smoke: run the echo workflow with an event log
bytedigger --workflow echo --ctx-json '{"question":"hello"}' \
    --event-log /tmp/engine-events.jsonl

# derive state by replaying the log
bytedigger --derive-state /tmp/engine-events.jsonl

# test suite (from a source checkout)
python3 -m pytest tests/
```

### Project mode

The entrypoint auto-detects its context: run from an ordinary project
directory it uses neutral defaults and writes all state under
`<cwd>/.hal-build/` — nothing touches any host install. To force neutral mode
explicitly, pass `--no-hal` or set `HAL_ENGINE_NEUTRAL=1`:

```bash
# from any project checkout — artifacts land in ./.hal-build/
bytedigger --workflow echo --ctx-json '{}' --event-log .hal-build/events.jsonl

# explicit overrides (equivalent)
bytedigger --no-hal --workflow echo --ctx-json '{}'
HAL_ENGINE_NEUTRAL=1 bytedigger --workflow echo --ctx-json '{}'
```

LLM steps are subprocess-based and backend-pluggable: any CLI that accepts a
prompt on stdin and prints to stdout works. Deterministic phases and the full
test suite run without any LLM configured.

## Backends: text vs agentic

Two reference backends live in `lib/reference_backends/`:

- **`anthropic-api`** (text) — Anthropic Messages API, stdlib-only (urllib).
  For opaque-text phases (no file writes, no tool use). Requires
  `ANTHROPIC_API_KEY`.
- **`pydantic-openai`** (agentic) — **experimental** — provider-agnostic agent
  (write files, run tests) on any OpenAI-compatible provider via
  [Pydantic AI](https://ai.pydantic.dev) v2. Install with the
  `[agentic-pydantic]` extra. Requires `AZURE_OPENAI_KEY` +
  `AZURE_OPENAI_ENDPOINT` (optional `PYDANTIC_BACKEND_DEPLOYMENT`). The
  workspace root must be a git repository — the write manifest is a
  pre-state-aware git diff (ground truth, never model self-report). Bash /
  run_tests are restricted by default to an argv0 allowlist executed without a
  shell; treat the allowlist as accident protection, not a security boundary.

## Boundary

The engine core is host-decoupled by construction and CI-enforced: a
core-boundary lint (`SYSTEM/cli/build/core-boundary-lint.py`) scans the
transitive import closure of `core_manifest.json` for machine-specific paths,
environment coupling, and denylisted imports, and a flag-routing lint keeps
environment reads behind the `config_provider` seam.

## Repository layout

The engine lives at `SYSTEM/cli/build/engine_py/`, mirroring the upstream
nesting: engine code resolves sibling tooling (deterministic lints, the
security rule pack) by relative path, so the extracted tree keeps the same
shape. Packaging flattens it — `pip install .` exposes the modules top-level.
Flattening the tree itself is upstream follow-up work.

This tree is extracted from the upstream engine by
`tools/extract_from_hal.py` (manifest-driven; `pyproject.toml`, `README.md`
and `LICENSE` are owned here and never overwritten by extraction). The
pre-extraction ByteDigger Claude Code plugin (TypeScript/hooks pipeline) is
preserved under [`legacy-plugin/`](legacy-plugin/) — see its
[README](legacy-plugin/README.md) and
[the article](legacy-plugin/docs/article.md) on how the gate-enforced pipeline
came to be.

## Non-goals

Multi-agent orchestration frameworks are well covered elsewhere. This project
is deliberately narrow: one sequential engine, one event log, and a growing set
of deterministic gates that keep generated code honest.

## License

MIT — see [LICENSE](LICENSE).
