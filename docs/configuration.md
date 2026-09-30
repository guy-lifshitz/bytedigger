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

## Readiness gate (`readiness`, bd#117)

Opt-in approval gate for issue-bound builds. It reads the `readiness` key of `bytedigger.json` **on the
default branch of the repository BD pushes to** (`git remote get-url --push origin`, fetched into
`refs/bd/policy`) -- never the working tree or the build branch, so a build cannot switch it off.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `readiness.required` | bool | `false` | `true` turns the gate on; absent or `false` means no `gh` call and no behaviour change |
| `readiness.label` | string | `"plan-approved"` | the label a human adds to approve the plan |
| `readiness.approvers` | list of logins | `[]` | when non-empty, only these users' label counts |
| `readiness.distinct_actor` | bool | `false` | `true` refuses a label added by the BD user itself (`self_approved`) |

How it works:

- The build is bound to issue `N` by its branch name only: `gh<N>-<slug>`, `gh<N>` or `batch/<N>`.
  `/build --issue <N>` puts the build on `gh<N>-<slug>` for every tier.
- `scripts/readiness post --spec <path>` posts the plan to the issue as a record by the BD user
  (`gh api user`) and removes the label if present. A human then adds the label.
- `scripts/readiness check --stage start|ship` decides. At `ship` (run by `scripts/ship.sh --pr` and by
  the engine's `ship_to_pr`, before anything is pushed) an approval is consumed: BD posts a
  `<!-- bd:consumed ... -->` record, re-reads the comments, and removes the label. A second branch needs
  a new record and a new approval. BD never adds the label.
- Exit codes: 0 off or approved, 2 usage, 3 not approved (`E_READINESS_NOT_APPROVED <reason> #<N>`),
  4 unavailable (`W_READINESS_UNAVAILABLE`; at `ship` the gate fails closed). `ship.sh` maps a crash or
  a missing `python3` to 4, so `ship.sh --pr` requires `python3`.
- v1 supports github.com only (`https://github.com/<o>/<n>` or `git@github.com:<o>/<n>` push URLs); any
  other push target with `required: true` is unavailable.
- Engine-driven builds (`engine_py` phases 4.5 to 5) get only the ship gate. Under `required: true`
  their Phase 8 fails `E_READINESS_NOT_APPROVED` until a record exists. Recovery:
  `scripts/readiness post --spec $SCRATCHPAD/specs/build-spec.md`, a human adds the label, resume
  Phase 8.

Residual risk: the gate is advisory against the orchestrating model. It has `git` and `gh` and can add
the label itself or skip both ship paths with a direct `git push` / `gh pr create`. `approvers` and
`distinct_actor` close only the "model adds the label, then ships through BD" path; with a shared GitHub
account they cannot tell the two apart. Approval binds the posted spec, not the code.

## Companion tuning (`tuning`, bd#117 Part B)

`scripts/companion-tune` turns what maintainers do after a BD-built PR ships into one proposed change of
your host companion (`bytedigger/companions/<core>.md`, see the companion docs) and opens a PR for a human
to review. It never merges, never pushes the default branch and never adds a label.

- `collect [--repo .] [--since 7d] [--out signals.json]` reads two kinds of signal, both by a maintainer
  (repository permission `admin` or `write`, not in `tuning.bot_logins`) inside the window: `reopened` (an
  issue that a merged BD-built PR closes) and `relabeled` (a watched label added or removed on a BD-built PR
  or on an issue it closes). The readiness label is never a signal. Comment text is never read.
- `propose --core <id> --signals signals.json [--llm-command <cmd>] [--dry-run]` asks one model call (default:
  the configured fallback model; `--llm-command` is split with `shlex` and run without a shell) for the new
  companion, commits it in a temporary worktree cut from the push target's default branch, refuses anything
  but that one file, runs the #116 checker, and only then pushes branch `bd/companion-tune-<date>-<hash>` and
  opens the PR. A run with no signals, an open tuner PR, nothing overridable, or an unchanged draft is a no-op.
- Exit codes: 0 ok or no-op, 2 usage, 3 `E_COMPANION_TUNE_REFUSED` / `E_COMPANION_TUNE_DRAFT_INVALID`, 4
  `E_COMPANION_TUNE_UNAVAILABLE` / `E_COMPANION_TUNE_TRUNCATED` (a listing hit exactly 1000 rows).

Config: the `tuning` key of `bytedigger.json` on the push target's default branch (same read as `readiness`).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tuning.bd_logins` | list of logins | `[]` | accounts that author BD-built PRs, added to the login of `gh api user` |
| `tuning.bot_logins` | list of logins | `[]` | actors that are never a maintainer |
| `tuning.watched_labels` | list of labels | `[]` | labels whose add or remove is a `relabeled` signal; empty means none |

Under a workflow `GITHUB_TOKEN`, `gh api user` returns 403, so `tuning.bd_logins` must then be non-empty
(`E_COMPANION_TUNE_UNAVAILABLE bd_logins_required`). A malformed `tuning` is `malformed_tuning`. Creating the
PR from a workflow needs the repository setting "Allow GitHub Actions to create and approve pull requests".
`examples/github-actions/companion-tune.yml` runs `collect --since 7d` and then `propose` every Monday.

Provenance markers (a marker counts only on a PR whose author is a BD login):

- `<!-- bd:built -->` is the last body line of every PR that `scripts/ship.sh` and the engine's `ship_to_pr`
  open, after any `ship_pr_body` override.
- `<!-- bd:tune signals=<id>,<id>... -->` is the last body line of a tuner PR (head `bd/companion-tune-`);
  signals named in an earlier genuine tuner PR are not proposed again. A tuner PR never carries `bd:built`.

Residual risk: issue and PR titles are written by anyone who can open an issue and reach the model; the
checker bounds where the model's text lands, not what it says. The human review of the PR is the content check.

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

## Verification skills (engine)

Phase 5 step `verify_registered_skills` runs every skill whose `SKILL.md`
frontmatter carries `metadata.verification: true` (see `commands/verify.md`).
Default roots are `skills/*/` and `.claude/skills/*/`; two `org_config` keys
extend and bound it:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `verification_skill_dirs` | list of strings | `[]` | Extra repo-relative directories scanned one level deep (`<dir>/*/SKILL.md`). An entry outside the repo is an `outside_repo` error row. CLI twin: `verify --extra-dir DIR` (repeatable). |
| `verification_timeout_sec` | number | `300` | Per-command timeout; on expiry the whole process group is killed and the skill is `timeout`. CLI twin: `verify --timeout SEC`. |

A check must not leave non-ignored files in the repo: any new or changed file that
`.gitignore` does not cover makes the skill `mutated`, which fails the step.

## Skill companions (host-local skill text)

A host can extend a ByteDigger core skill without editing it. The core
declares which of its H2 sections may be extended, in its frontmatter:

```yaml
metadata:
  overridable: "project-conventions"
```

`skills/bytedigger/SKILL.md` declares one, the empty `## Project conventions`
section. The host commits a companion at
`bytedigger/companions/<core-id>.md` in its repo root:

```markdown
---
specializes: bytedigger
---
# Our conventions

## Project conventions

Run `make lint` before every commit.
```

Phase 0 runs `scripts/skill-companion render --core bytedigger`. Each
companion section is appended to the core section with the same title,
inside `<!-- bd:local begin/end -->` markers. The companion must be committed
(working tree equal to `HEAD`), may only use sections the core declares, and
may not contain H1 headings, setext headings, unclosed fences or HTML
comments. Any violation exits 3 with one `E_SKILL_COMPANION_INVALID <reason>
<path>` line per error and the build stops. Run
`scripts/skill-companion check --core bytedigger --repo .` in your own CI to
catch this early (`--json` prints the full result).

Do not `@`-import a companion into `CLAUDE.md` or place it under a skills
directory: that loads the text without these checks.

## Role templates (engine, `org_config`)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `role_template_path` | string | unset | Path to a file whose whole content the engine inserts into its phase prompts. A missing file is skipped silently. Nothing limits what the file says. A bounded replacement is tracked in #119. |

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
