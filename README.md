# ByteDigger

A verified-TDD engine for AI code generation. Frozen spec, failing tests first, an adversarial gate between the tests and the implementation, and deterministic lints that catch an agent gaming its own acceptance signal.

> [Shrinking the Human in the Loop](docs/article.md) -- the story of how this pipeline came to be.

## The problem it solves

Most agentic coding failures are not capability failures. They are verification failures. Ask an agent for a feature and it will write the tests, write the code, run the tests, and report green -- and somewhere in that loop it weakened an assertion, mocked the unit under test, or passed a scoped suite while breaking the full one. Not out of malice; the writer and the reviewer share the same blind spots.

ByteDigger treats those as first-class failure modes. A state machine enforces the loop, and the checks that matter are cheap deterministic code, not more LLM judgment:

```
spec (frozen) -> RED (failing tests) -> gate (adversarial audit) -> GREEN (implementation) -> verify
                     ^                        |
                     +------- REJECT ---------+
```

The spec freezes before any test exists. The engine independently checks that RED tests fail. An independent gate audits the spec and the tests for stub-passable assertions, missing forcing functions, and scope drift -- and rejects back rather than rubber-stamping. During GREEN the tests are read-only. Every step appends to an event log, so a killed run resumes instead of starting over.

## What's in the box

The core is a sequential workflow engine (`engine_py/`) with phases registered as workflows: research, spec, implement (the RED/gate/GREEN loop), review, synthesize. State comes from replaying an append-only JSONL event log -- there is no mutable state file to drift or race.

Around the loop sits a set of deterministic anti-gaming lints, run as code:

- stub-passability: rejects a RED test file that mocks its own unit under test
- test-integrity diff guard: classifies post-RED test edits, hard-fails on assertion gaming
- scope-inverse: flags implementation writes outside the spec's file allowlist
- spec-cite, spec-coverage, helper-extraction, suite-safety and friends

The engine installs with zero runtime dependencies and no LLM vendor baked in. Anthropic, Azure OpenAI, and Claude Code backends ship as references; plugging in your own is [about 20 lines](examples/library/custom_backend.py).

## Quickstart

Five minutes, no API key needed:

```bash
git clone https://github.com/shtofadhor/bytedigger
cd bytedigger/engine_py

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# smoke: run a workflow, replay its event log
bytedigger --workflow echo --ctx-json '{"question":"hello"}' --event-log /tmp/events.jsonl
bytedigger --derive-state /tmp/events.jsonl

# the verified-TDD loop end to end on a toy repo, keyless
python3 ../examples/verified-tdd-run/run_demo.py
```

The [verified-tdd-run example](examples/verified-tdd-run/) walks a frozen spec with an AC table through the loop against a toy repository and shows a gate rejection along the way. From there:

```bash
pip install -e ".[test]" && python3 -m pytest tests/   # 300+ hermetic tests
pip install -e ".[agentic-pydantic]"                   # real API backends
```

Backend setup (env vars, model aliases, selection) is in [docs/backends.md](docs/backends.md).

## Use with Claude Code

The same discipline is available as a Claude Code plugin -- `/build "add email verification"` classifies the task, routes it through the phase pipeline, and enforces the TDD loop with hooks between phases:

```bash
claude plugin add shtofadhor/bytedigger
```

See [examples/claude-code-skill/](examples/claude-code-skill/) for a manual per-project install and [docs/plugin.md](docs/plugin.md) for the full plugin reference (configuration flags, complexity routing, reviewer modes). The plugin predates the Python engine and is the layer we still drive day to day; the engine is the extracted, host-independent core of it.

## Bring your own LLM

Every LLM call goes through one dispatcher with a named-backend registry. `register_backend` is the public seam:

```python
from llm_subprocess import register_backend

register_backend("my-backend", my_callable, manifest_source="orchestrator_observed")
```

Reference backends in `engine_py/lib/reference_backends/` cover the Anthropic Messages API (text, stdlib-only), agentic Pydantic AI backends for OpenAI-compatible and Anthropic providers (write files, run tests), and the Claude Agent SDK. [docs/backends.md](docs/backends.md) has the full inventory. Deterministic phases and the whole test suite run without any LLM configured.

## Repository layout

| Path | What |
|------|------|
| `engine_py/` | The Python engine: state machine, event log, gates, workflows, tests |
| `examples/` | Keyless demos: [minimal run](examples/library/), [custom backend](examples/library/custom_backend.py), [verified-TDD loop](examples/verified-tdd-run/) |
| `docs/` | [Backends](docs/backends.md), [plugin reference](docs/plugin.md), [event schema](docs/events.md), [the article](docs/article.md) |
| `phases/`, `commands/`, `hooks/`, `scripts/`, `skills/` | The Claude Code plugin (orchestration layer) |

## Non-goals

Multi-agent orchestration frameworks are well covered elsewhere. This project is deliberately narrow: one sequential engine, one event log, and a growing set of deterministic gates that keep generated code honest.

## License

MIT -- see [LICENSE](LICENSE).
