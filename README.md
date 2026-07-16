# ByteDigger

A verified-TDD engine for AI code generation. Frozen spec, failing tests first, an adversarial gate between the tests and the implementation, and deterministic lints that catch an agent gaming its own acceptance signal.

> [Shrinking the Human in the Loop](docs/article.md) -- the story of how this pipeline came to be.

## TL;DR

AI agents don't fail at writing code, they fail at verifying it -- and they game their own tests. ByteDigger enforces the loop that prevents this: spec freezes, failing tests land and get adversarially audited, only then does implementation start, with deterministic lints (no LLM judgment) rejecting mocked-out tests, weakened assertions, and out-of-scope writes. Problems get removed before the code exists; there is no generate-review-fix loop to babysit. `pip install -e engine_py`, run the [keyless demo](examples/verified-tdd-run/), point it at your own LLM in [20 lines](examples/library/custom_backend.py). Also ships as a Claude Code plugin (`/build`).

## The problem it solves

Most agentic coding failures are not capability failures. They are verification failures. Ask an agent for a feature and it will write the tests, write the code, run the tests, and report green -- and somewhere in that loop it weakened an assertion, mocked the unit under test, or passed a scoped suite while breaking the full one. Not out of malice; the writer and the reviewer share the same blind spots.

ByteDigger treats those as first-class failure modes. A state machine enforces the loop, and the checks that matter are cheap deterministic code, not more LLM judgment:

```
spec (frozen) -> RED (failing tests) -> gate (adversarial audit) -> GREEN (implementation) -> verify
                     ^                        |
                     +------- REJECT ---------+
```

The spec freezes before any test exists. The engine independently checks that RED tests fail. An independent gate audits the spec and the tests for stub-passable assertions, missing forcing functions, and scope drift -- and rejects back rather than rubber-stamping. During GREEN the tests are read-only. Every step appends to an event log, so a killed run resumes instead of starting over.

## Security first, shift left

The standard way to make AI code trustworthy is a loop bolted on after generation: generate, review, fix, review again. The loop is expensive, converges slowly, and its reviewer shares the writer's blind spots -- both are models, often the same model.

ByteDigger moves the checks to before the code exists. The spec freezes first, with an AC table and a file allowlist, so scope drift dies at write time. Failing tests come next and get adversarially audited before a single line of implementation -- a vacuous test gets rejected while rejecting it is still cheap. Secure-codegen rules ride inside the generation prompt itself, and a semgrep gate lints the tests as they land, so security guidance operates at codegen time, hours before any reviewer would have seen the diff. Reviewers still run at the end; the design goal is that they find nothing.

## What's in the box

The core is a sequential workflow engine (`engine_py/`) with phases registered as workflows: research, spec, implement (the RED/gate/GREEN loop), review, synthesize. State comes from replaying an append-only JSONL event log -- there is no mutable state file to drift or race.

Around the loop sits a set of deterministic anti-gaming lints, run as code:

- stub-passability: rejects a RED test file that mocks its own unit under test
- test-integrity diff guard: classifies post-RED test edits, hard-fails on assertion gaming
- scope-inverse: flags implementation writes outside the spec's file allowlist
- spec-cite, spec-coverage, helper-extraction, suite-safety and friends

The spec itself has a machine-readable half. Alongside the prose, an `AC-checks` yaml block maps each acceptance criterion to a mechanical check from a closed registry -- file-contains, command-exit-code and friends -- validated at spec-freeze time and executed as code, so "done" is the AC table passing rather than anyone's judgment. A criterion that genuinely needs judgment has to declare itself as one (`llm_rubric`), which keeps the escape hatch visible instead of ambient. Specs stop being documentation that drifts; they compile.

The engine installs with zero runtime dependencies and no LLM vendor baked in. Anthropic, Azure OpenAI, and Claude Code backends ship as references; plugging in your own is [about 20 lines](examples/library/custom_backend.py).

## Quickstart

Five minutes, no API key needed:

```bash
git clone https://github.com/guy-lifshitz/bytedigger
cd bytedigger/engine_py

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# smoke: run a workflow, replay its event log
bytedigger-engine --workflow echo --ctx-json '{"question":"hello"}' --event-log /tmp/events.jsonl
bytedigger-engine --derive-state /tmp/events.jsonl

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
claude plugin marketplace add guy-lifshitz/bytedigger
claude plugin install bytedigger@bytedigger
```

Phase 6 runs a parallel reviewer panel (code review, silent-failure hunting, test coverage, and a security reviewer on high-risk changes) modeled on Anthropic's pr-review-toolkit; set `reviewers.mode: "toolkit"` to use the toolkit agents directly when installed. See [examples/claude-code-skill/](examples/claude-code-skill/) for a manual per-project install and [docs/plugin.md](docs/plugin.md) for the full plugin reference (configuration flags, complexity routing, reviewer modes). The plugin predates the Python engine and is the layer we still drive day to day; the engine is the extracted, host-independent core of it.

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

## Security

The engine runs LLM-generated code -- that's the GREEN phase doing its job. Run it in a container or a throwaway worktree; the deterministic gates keep the model honest, they do not contain it. Keys come in through env vars only and never land in the event log. The threat model is spelled out in [docs/security.md](docs/security.md), and [SECURITY.md](SECURITY.md) has the private reporting channel for anything exploitable.

## Non-goals

Multi-agent orchestration frameworks are well covered elsewhere. This project is deliberately narrow: one sequential engine, one event log, and a growing set of deterministic gates that keep generated code honest.

## License

MIT -- see [LICENSE](LICENSE).
