# Configuration

ByteDigger has two configuration surfaces, and they do not overlap:

1. **`bytedigger.json`** (repo root) — the **plugin layer** config. Read by the
   Claude Code plugin's gate scripts (`scripts/build-gate.sh`,
   `scripts/ts/build-phase-gate.ts`, `scripts/gate-dispatcher.sh`,
   `scripts/learning-store.sh`) and referenced by the phase prompt docs
   (`commands/build.md`, `phases/*.md`).
2. **The Python engine (`engine_py/`)** — reads **environment variables** (via
   `engine_py/config_provider.py`) and the **`org_config` dict** passed in the
   run context. The engine does **not** read `bytedigger.json`; the only
   engine-side reference to that file is the `config-json` check in
   `bytedigger doctor` (`engine_py/doctor.py`), which validates it as JSON if
   one exists in the cwd.

## bytedigger.json (plugin layer)

Path resolution (`scripts/ts/lib/config-reader.ts`): `BYTEDIGGER_CONFIG` env
var (absolute path) > `$CLAUDE_PLUGIN_ROOT/bytedigger.json` > repo root. The
bash gates and `scripts/learning-store.sh` extract keys with `python3`.

| Key | Type | Default | Consumed by |
|---|---|---|---|
| `validation_model` | string | `"opus"` | Phase prompts (`commands/build.md`, `phases/phase-4-architect.md`) — model for architecture/validation agents |
| `agent_model` | string | `"sonnet"` | Phase prompts (`commands/build.md`, `phases/phase-5-implement.md`) — model for code-gen (GREEN) workers |
| `exploration_model` | string | `"haiku"` | Documented in `docs/plugin.md`; no script reads it today (declared expectation for Phase 2 explorers) |
| `satisfaction_thresholds` | object | `{SIMPLE:80, FEATURE:85, COMPLEX:90}` | `phases/phase-6-review.md` — per-tier satisfaction score floors |
| `reviewers` | object | `{"mode":"auto"}` | `scripts/ts/build-phase-gate.ts` — reviewer selection mode: `"toolkit"` / `"generic"` / `"auto"` |
| `simple_reviewers` | int | `3` | Both gate backends; declared expectation (Phase 6 roster is fixed per tier today) |
| `feature_reviewers` | int | `6` | ditto |
| `complex_reviewers` | int | `6` | ditto |
| `gates_enabled` | bool | `true` | Both gate backends — `false` disables phase-gate enforcement entirely |
| `gate_backend` | string | `"bash"` | `scripts/gate-dispatcher.sh` — `"bash"` / `"ts"` (bun, fail-closed if missing) / `"shadow"` (run both, bash verdict wins). Env `GATE_BACKEND` overrides |
| `tdd_mandatory` | bool | `true` | Both gate backends — enforce RED-before-GREEN checkpoints |
| `worktree_auto` | bool | `true` | Reserved — no script reads it yet; worktrees are driven by `--worktree` / FEATURE+-on-main rule |
| `constitution_path` | string | `"./constitution.md"` | Phase 0.5 injection (also mirrored engine-side: `engine_py/workflows/phase_05_inject.py` reads `org_config["constitution_path"]`) |
| `omitProjectContext` | bool | `false` | `scripts/ts/build-phase-gate.ts`, `phases/phase-2-explore.md`, engine `phase_2_explore.py` — skip CLAUDE.md/project context in Explorer prompts |
| `logging` | bool | `false` | Reserved — no script reads it; event emission is `observability.enabled` |
| `learning` | object | `{backend:"file", max_inject:10, max_stored:200, storage_path:".bytedigger/learnings"}` | `scripts/learning-store.sh` — learning backend (`file` / `sqlite` / `none`), injection and storage caps |
| `observability` | object | `{enabled:true}` | `scripts/ts/build-phase-gate.ts` — controls event emission |
| `activeWorkInjection` | bool | `true` | Parsed by `build-phase-gate.ts`; prompt-injection wiring pending (`scripts/ts/lib/memory-reader.ts`) |

See [plugin.md](plugin.md#configuration) for the narrative version of the
plugin flags.

## Model pinning (engine)

The engine resolves the model per LLM step through `_resolve_model`
(`engine_py/workflows/phase_workflows_common.py`):

```
org_config["<step>_model"]  >  org_config["model"]  >  built-in role default
```

Per-step override keys read by the workflows: `spec_model`, `red_model`,
`green_model`, `validation_model`, `review_model` (plus
`review_model_retry`), `fix_model`, `integrity_model`, `fix_integrity_model`,
`satisfaction_model`, `architect_model`, `clarify_model`, `discovery_model`,
`explore_model`, `synthesizer_model`.

Example `org_config` fragment — pin validation to opus, everything else to
sonnet:

```json
{
  "model": "sonnet",
  "validation_model": "opus"
}
```

Built-in defaults come from model **roles** in `config/models.json` (relative
to the engine root, `engine_py/lib/model_config.py`): `primary` (RED/GREEN),
`critical` (validation gate), `fallback` (SIMPLE-tier haiku), `spec_writer`.
Role values may be chains (ordered fallback lists); entries listed in
`claude.unavailable` or in env `HAL_MODEL_UNAVAILABLE` (comma-separated) are
skipped. The hard validation gate additionally enforces a model floor:
`HAL_GATE_MODEL_FLOOR` env > `models.json` `claude.gate_floor` > provider
default (`engine_py/llm_subprocess.py::_resolve_gate_floor`).

## Environment variables and the BD_ alias layer

Engine env reads go through `engine_py/config_provider.py`. Every `HAL_<X>`
variable accepts two aliases: **`BD_<X>`** and **`BYTEDIGGER_<X>`**.
Precedence: `HAL_<X>` (if set) > `BD_<X>` > `BYTEDIGGER_<X>`.
`config_provider.env_mapping()` returns a read-only environ view that
synthesizes a `HAL_<X>` entry for every `BD_`/`BYTEDIGGER_` key, so
subprocess overlays see the aliases materialized; `bytedigger doctor`'s
`env-alias` check reports how many entries the mapping resolves.

Commonly used variables (spell any of them `BD_*` if you prefer):

| Variable | Purpose |
|---|---|
| `BD_RUNNER_BACKEND` (`HAL_RUNNER_BACKEND`) | Select the LLM backend (see [backends.md](backends.md)) |
| `BD_BUILD_PYTHON` (`HAL_BUILD_PYTHON`) | Interpreter/venv the engine subprocess runs in — backend extras must be installed there, not necessarily in your shell's venv |
| `BD_DIR` (`HAL_DIR`) | Engine install-root override used to derive default paths |
| `BD_GATE_MODEL_FLOOR` (`HAL_GATE_MODEL_FLOOR`) | Minimum model for the hard validation gate |
| `BD_MODEL_UNAVAILABLE` (`HAL_MODEL_UNAVAILABLE`) | Comma-separated model names/families to skip in role chains |
| `BD_DBOS_DB_PATH` (`HAL_DBOS_DB_PATH`) | Durable-state sqlite path override |
| `BD_ENGINE_DURABLE_BACKEND` (`HAL_ENGINE_DURABLE_BACKEND`) | `native` (default) or `dbos` |

Engine artifacts for a foreign project land under **`.bytedigger/`** in the
project cwd (`config_provider.foreign_state_dirname()`): `events.jsonl`
(event log), `reject-reasons.jsonl`, `build-rework-log.jsonl`, `build-runs/`,
`incidents.jsonl`, `dbos.sqlite`, `memory.db`.

The full catalog of engine env flags — 80+ entries with kind, default, and
owning module — is `engine_py/flags_catalog.py`.

## Runner backends

Backend selection and per-backend setup (deps, auth env vars, model aliases)
are documented in [backends.md](backends.md). Resolution order: per-call
`backend=` kwarg > `HAL_RUNNER_BACKEND` (or `BD_RUNNER_BACKEND`) env >
default `claude-subprocess`. Known names come from the `_BACKENDS` registry
in `engine_py/llm_subprocess.py`; selecting a reference backend whose
dependencies are missing fails with `E_LLM_BACKEND_UNKNOWN` plus an install
hint:

| Backend | Install hint |
|---|---|
| `agent-sdk` | `pip install claude-agent-sdk` |
| `anthropic-api` | stdlib-only; needs a package build that bundles `lib.reference_backends` |
| `pydantic-openai` | `pip install "bytedigger-engine[agentic-pydantic]"` |
| `pydantic-anthropic` | `pip install "bytedigger-engine[agentic-pydantic]" anthropic` |

## Troubleshooting

Run the offline self-check:

```bash
bytedigger doctor          # npm wrapper
bytedigger-engine doctor   # pip entry point
```

It runs 13 checks (`engine_py/doctor.py`): Python version, engine imports,
gates importable, optional deps, config resolution, event-log writability,
backend registry, engine smoke run, `claude` CLI, Agent SDK import, git
runtime, `bytedigger.json` JSON validity, and env-alias materialization.
Exit code 1 if any check fails.
