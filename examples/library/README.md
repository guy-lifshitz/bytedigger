# Library usage

Drive the ByteDigger engine from plain Python -- no Claude Code, no plugin.

## Setup

```bash
cd engine_py && pip install -e .
```

Zero runtime dependencies for the core. The optional `[agentic-pydantic]` extra adds the pydantic-ai API backends.

## Examples

### `minimal_run.py` -- engine, workflow, event log

The smallest end-to-end loop, runs keyless:

```bash
python3 examples/library/minimal_run.py
```

What it shows:

1. **`WorkflowEngine`** -- construct with an event sink, register the shipped workflows (`workflows.register_all`).
2. **`WorkflowContext`** -- the feature request rides in `question`; the rest is tenancy/config plumbing.
3. **`engine.execute(name, ctx, run_id=...)`** -- returns `(StepResult, WorkflowContext)`. The example runs `echo` (deterministic, no LLM); the real phases (`phase_0_research` .. `phase_7_synthesize`) have the same call shape.
4. **Event log + replay** -- every step appends to a JSONL log; `derive_state.replay` reconstructs run state from it. That's how interrupted builds resume.

### `custom_backend.py` -- bring your own LLM

```bash
python3 examples/library/custom_backend.py
```

Registers a stub backend via `register_backend` (the public injection seam), routes a call through `invoke_llm_subprocess`, and resets. Swap the stub body for your API client and the entire pipeline uses it.

## Real API backends

```bash
pip install -e "engine_py[agentic-pydantic]"
```

| Backend | Env vars |
|---|---|
| `pydantic_openai` | `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, optional `PYDANTIC_BACKEND_DEPLOYMENT` |
| `anthropic_api` | `ANTHROPIC_API_KEY` |

`run.py` auto-registers both when the battery is installed (skips silently when not).

## CLI equivalent

Everything above is also reachable from the console script:

```bash
bytedigger-engine --list
bytedigger-engine --workflow echo --ctx-json '{"question":"hello"}' --event-log /tmp/events.jsonl
bytedigger-engine --derive-state /tmp/events.jsonl
```

See `engine_py/README.md` for the full CLI surface (durable runs, `--status`, `--no-hal` neutral mode).
