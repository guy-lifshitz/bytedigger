# ByteDigger

Everybody lies. Coding agents are no exception: they'll mock the very unit you asked them to test, then report green with a straight face. You can keep paying a review loop to catch them lap after lap -- or you can catch the lie before the code exists. That's the whole idea here. The spec compiles into checks a machine can run, failing tests survive a hostile audit before anyone writes the implementation, and the expensive model gets spent once -- on the build, not on the do-overs.

> [Shrinking the Human in the Loop](docs/article.md) -- the story of how this pipeline came to be.

## TL;DR

AI agents don't fail at writing code, they fail at verifying it -- and they game their own tests. ByteDigger enforces the loop that prevents this: spec freezes, failing tests land and face a hostile audit, only then does implementation start, with deterministic lints (no LLM judgment) rejecting mocked-out tests, weakened assertions, and out-of-scope writes. Problems get removed before the code exists; there is no generate-review-fix loop to babysit. `pip install -e engine_py`, run the [keyless demo](examples/verified-tdd-run/), point it at your own LLM in [20 lines](examples/library/custom_backend.py). Also ships as a Claude Code plugin (`/build`).

## The problem it solves

Most agentic coding failures are not capability failures. They are verification failures. Ask an agent for a feature and it will write the tests, write the code, run the tests, and report green -- and somewhere in that loop it weakened an assertion, mocked the unit under test, or passed a scoped suite while breaking the full one. Not out of malice; the writer and the reviewer share the same blind spots.

ByteDigger treats those as first-class failure modes. A state machine enforces the loop, and the checks that matter are cheap deterministic code, not more LLM judgment:

```
spec (frozen) -> RED (failing tests) -> gate (adversarial audit) -> GREEN (implementation) -> verify
                     ^                        |
                     +------- REJECT ---------+
```

The spec freezes before any test exists. The engine checks for itself that RED tests fail. An independent gate audits the spec and the tests for stub-passable assertions, missing forcing functions, and scope drift -- and rejects back rather than rubber-stamping. During GREEN the tests are read-only. Every step appends to an event log, so a killed run resumes instead of starting over.

## Security first, shift left

The standard way to make AI code trustworthy is a loop bolted on after generation: generate, review, fix, review again. The loop is expensive, takes forever to converge, and its reviewer shares the writer's blind spots -- both are models, often the same model.

ByteDigger moves the checks to before the code exists. The spec freezes first, with an AC table and a file allowlist, so scope drift dies at write time. Failing tests come next and face a hostile audit before a single line of implementation -- a vacuous test gets rejected while rejecting it is still cheap. Secure-codegen rules ride inside the generation prompt itself, and a semgrep gate lints the tests as they land, so security guidance operates at codegen time, hours before any reviewer would have seen the diff. Reviewers still run at the end; the design goal is that they find nothing.

## Not another agent harness

Plenty of good projects wrap an LLM in a loop with tools and let it run -- SWE-agent, OpenHands, Aider, the IDE agents. They compete on generation: better prompts, better context, better tool use. And they accept the agent's own test run as the acceptance signal -- the very signal agents game.

ByteDigger competes on verification. It builds the acceptance signal outside the agent: a spec that compiles to mechanical checks, tests audited by an adversarial gate before implementation exists, lints that are deterministic code rather than another model's opinion, an event log the worker can't rewrite. Generation quality is the backend's problem -- plug in whichever model you like. This engine owns the part everyone else outsources to hope: proving the green is real.

And "proving" is literal. A reviewer's finding has to cite path, line, and quote; the verifier checks every citation against the actual file on disk, demotes a fabricated finding to an Unverified section, and recomputes the verdict without it. A disk-truth layer cross-checks RED and GREEN claims against the real git diff and the real test-runner subprocess output, not the agent's transcript. And before any pass/fail delta counts, the test command runs five times and must produce identical counts every run -- a flaky suite doesn't get to vote.

## What's in the box

The core is a sequential workflow engine (`engine_py/`) with phases registered as workflows: research, spec, implement (the RED/gate/GREEN loop), review, synthesize. State comes from replaying an append-only JSONL event log -- there is no mutable state file to drift or race.

Around the loop sits a set of deterministic anti-gaming lints, run as code:

- stub-passability: rejects a RED test file that mocks its own unit under test
- test-integrity diff guard: classifies post-RED test edits, hard-fails on assertion gaming
- scope-inverse: flags implementation writes outside the spec's file allowlist
- spec-cite: every path:line citation in the spec verified against the real repository before freeze
- net-new delta: the RED-to-GREEN improvement must be net-new passes, not reshuffled counts
- token-consistency, presence-triad, re-entry AC, suite-safety, forbidden-import -- each one a codified post-mortem

When a gate fails, a cheap model fronts the FAIL branch to draft the repair; the gate re-runs afterwards and stays the correctness authority. A restart governor caps re-invocation (and knows a crash-start from a gate-start), and every subprocess goes through one spawn chokepoint with a mandatory timeout, so an unbounded hang is impossible by construction.

The spec itself has a machine-readable half. Alongside the prose, an `AC-checks` yaml block maps each acceptance criterion to a mechanical check from a closed registry -- file-contains, command-exit-code and friends -- validated at spec-freeze time and executed as code, so "done" is the AC table passing rather than anyone's judgment. A criterion that can't live without judgment has to declare itself as one (`llm_rubric`), which keeps the escape hatch visible instead of ambient. Specs stop being documentation that drifts; they compile.

The engine installs with zero runtime dependencies and no LLM vendor baked in. Anthropic, Azure OpenAI, and Claude Code backends ship as references; plugging in your own is [about 20 lines](examples/library/custom_backend.py).

## Under the hood

The pipeline sketch above is five boxes; the engine behind it is 27 workflow modules, phase 0 research through phase 8 post-deploy -- including a spec-lite lane for small tasks, a DevOps pipeline, integrity and smoke phases, and a review fastpath for simple changes. When the changed files include a Dockerfile, Kubernetes manifest, Terraform, or CI config, phase 0.6 detects the artifact type and routes the build into a fail-closed security scan whose allowlist waivers carry expiry dates.

Durability is structural, not best-effort. The engine writes phase and step sentinels on success only, so a crashed build resumes mid-pipeline and a sticky error can never replay as progress; completed model calls are not paid for twice. An optional DBOS backend adds durable run listing and status. Cost and token rollups per run, phase, and cycle come straight from the event log, and every REVISE or FAIL reason lands in durable JSONL ledgers -- a reject log and a spec-defect ledger with a bounded reroute budget for specs that turn out defective.

The engine also learns. Phase 7 synthesizes categorized learnings from each build into a pluggable store: markdown files under `.bytedigger/learnings/` by default, and a reference SQLite shell backend with a documented schema shows how to plug in your own. Before implementation, an injection step assembles a context folder: matched learnings from whichever store you wired in, a project constitution discovered by precedence, quality-gate rules, security rules, active-work context. Lessons from build N are in the prompt for build N+1.

The engine even gates changes to itself: a commit-msg hook blocks any commit touching engine production code unless it co-stages an APPROVED audit document, and self-attested LLM verdicts get an anchor-hash and AC-parity cross-check against the on-disk files before they count.

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

Phase 6 runs a parallel reviewer panel (code review, silent-failure hunting, test coverage, and a security reviewer on high-risk changes) modeled on Anthropic's pr-review-toolkit; set `reviewers.mode: "toolkit"` to use the toolkit agents themselves when installed. See [examples/claude-code-skill/](examples/claude-code-skill/) for a manual per-project install and [docs/plugin.md](docs/plugin.md) for the full plugin reference (configuration flags, complexity routing, reviewer modes). The plugin predates the Python engine and is the layer we still drive day to day; the engine is the extracted, host-independent core of it.

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

The engine runs LLM-generated code -- that's the GREEN phase doing its job. Run it in a container or a throwaway worktree; the deterministic gates keep the model honest, they do not contain it. Keys come in through env vars only and never land in the event log. [docs/security.md](docs/security.md) spells out the threat model; [SECURITY.md](SECURITY.md) has the private reporting channel for anything exploitable.

## Non-goals

Multi-agent orchestration frameworks are well covered elsewhere. This project stays narrow on purpose: one sequential engine, one event log, and a growing set of deterministic gates that keep generated code honest.

## License

MIT -- see [LICENSE](LICENSE).
