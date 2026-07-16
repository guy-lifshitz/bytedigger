# LLM backends

Every LLM call in the engine goes through one function, `invoke_llm_subprocess`
(`engine_py/llm_subprocess.py`). It dispatches to a named backend from a
registry. This page covers how a backend is selected, what the shipped
reference backends need to run, and how to plug in your own.

## Selection

Resolution order, first match wins:

1. the per-call `backend=` argument to `invoke_llm_subprocess`
2. the `HAL_RUNNER_BACKEND` environment variable
3. the default, `claude-subprocess` (drives a local `claude` CLI)

```bash
HAL_RUNNER_BACKEND=anthropic-api bytedigger-engine --workflow phase_0_research ...
```

Registration is fail-closed. Every backend declares a `manifest_source` at
`register_backend()` time -- the producer of its written-files manifest that
the worker itself cannot forge (a git diff, a harness tool record, or a plain
text response for backends that never write files). A backend without one is
rejected at the capability probe with `E_LLM_BACKEND_NO_MANIFEST`, before any
work runs, rather than failing at commit time.

## Reference backends

Four reference implementations live in `engine_py/lib/reference_backends/`.
`run.py` auto-registers each one at import time when its dependencies are
present, and silently skips it when they are not -- so a core-only install
works, and `bytedigger-engine --list` always reflects what is actually usable.

| Name | Kind | Extra deps | Auth |
|---|---|---|---|
| `anthropic-api` | text | none (stdlib) | `ANTHROPIC_API_KEY` |
| `pydantic-openai` | agentic | `[agentic-pydantic]` | `AZURE_OPENAI_KEY` + `AZURE_OPENAI_ENDPOINT` |
| `pydantic-anthropic` | agentic | `[agentic-pydantic]` + `anthropic` | Claude subscription OAuth |
| `agent-sdk` | agentic | `claude-agent-sdk` | Claude Code login |

Text backends return a response string and are for opaque-text phases only
(no file writes, no tool use). Agentic backends can write files and run
tests inside the workspace; they require the workspace root to be a git
repository, because their write manifest is a pre-state-aware git diff --
ground truth, never model self-report.

### anthropic-api (text)

Anthropic Messages API over stdlib `urllib`. No third-party dependencies.

Environment:

- `ANTHROPIC_API_KEY` (required)
- `HAL_ANTHROPIC_MODEL_<ALIAS>` (optional) -- overrides the model ID a short
  alias resolves to, e.g. `HAL_ANTHROPIC_MODEL_HAIKU=claude-haiku-4-5-20251001`.
  Unknown aliases fall back to the built-in alias map, then pass through
  verbatim.

### pydantic-openai (agentic)

Provider-agnostic agent on any OpenAI-compatible endpoint, via
[Pydantic AI](https://ai.pydantic.dev) v2. Install:

```bash
pip install "bytedigger-engine[agentic-pydantic]"
```

Environment:

- `AZURE_OPENAI_KEY` (required)
- `AZURE_OPENAI_ENDPOINT` (required)
- `PYDANTIC_BACKEND_DEPLOYMENT` (optional) -- deployment name; defaults to the
  model string the engine passes in
- `AZURE_OPENAI_API_VERSION` (optional, default `2024-10-21`)
- `HAL_AGENTIC_BASH_UNRESTRICTED=1` (optional escape) -- lifts the default
  argv0 allowlist on the agent's bash / run_tests tools. The allowlist is
  accident protection, not a security boundary; leave it on unless a task
  genuinely needs arbitrary commands.

The module imports cleanly without `pydantic_ai`; only `register()` raises,
with a pip-extra hint.

### pydantic-anthropic (agentic)

Same agentic contract and tool set as `pydantic-openai` (it imports the tool
implementations rather than duplicating them), authenticated with a Claude
subscription OAuth token instead of an API key. Install:

```bash
pip install "bytedigger-engine[agentic-pydantic]" anthropic
```

Environment:

- `CLAUDE_CODE_OAUTH_CREDENTIALS` (optional) -- path to the credentials JSON;
  defaults to `~/.claude/.credentials.json` as written by Claude Code login.
  Tokens are refreshed and persisted automatically when near expiry.

### agent-sdk (agentic)

Drives the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk)
in-process. Install:

```bash
pip install claude-agent-sdk
```

Uses the local Claude Code login for auth. Environment:

- `HAL_AGENT_SDK_MAX_RESUMES` (optional, default `8`) -- cap on session
  resumes when the SDK run is interrupted.

## Bring your own

`register_backend` is the public seam. Register a callable matching the
`LLMBackend` protocol and the entire pipeline routes through it -- your API
client, a replay cache, a spy for tests:

```python
from llm_subprocess import register_backend, invoke_llm_subprocess

register_backend("mine", my_backend, manifest_source="orchestrator_observed")
result = invoke_llm_subprocess(prompt=..., model=..., timeout_sec=60,
                               step_name="step", backend="mine")
```

A complete runnable example (keyless stub, ~20 lines of backend code) is at
[`examples/library/custom_backend.py`](../examples/library/custom_backend.py).

## A note on env-var names

Configuration variables keep the `HAL_` prefix (`HAL_RUNNER_BACKEND`,
`HAL_ENGINE_NEUTRAL`, `HAL_ANTHROPIC_MODEL_*`) for compatibility with the
upstream engine this tree is extracted from. There are no `BD_` aliases
today; if they land, the `HAL_` names will keep working.
