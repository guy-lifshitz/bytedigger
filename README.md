# ByteDigger

<p align="center"><img src="docs/assets/bytedigger-title.png" alt="ByteDigger" width="480"></p>

The review loop exists because you can't trust the code and can't afford the laps. ByteDigger removes both reasons.

> [Shrinking the Human in the Loop](docs/article.md) -- the story of how this pipeline came to be.

## What it is

ByteDigger is a software factory: a CI that drives a Python state machine through the whole SDLC -- research, spec, failing tests, implementation, review -- with TDD at the core and agents as replaceable workers inside. The spec compiles into machine-runnable checks, so "done" is a table of checks passing, not anyone's judgment. That is what buys the autonomy: the pipeline runs unattended because the checks don't need you, not because you trust the agent. Maximally autonomous because maximally deterministic.

## Why the other factories are blind

Four familiar ways to get AI-written code, and the bill each one hands you:

- **Coding agents** -- Copilot, Cursor, Claude Code on its own -- write the tests, write the code, run the tests, report green. Green means nothing, so you re-read all 400 lines anyway.
- **Agent frameworks and harnesses** orchestrate the calls, then take the agent's word that the work is done. More code, faster, and the same re-reading bill.
- **AI review loops** add a second model to check the first. You pay tokens per lap, and the reviewer misses what the writer missed -- same training, same blind spots.
- **Software factories** loop agents at scale for throughput. The same blindness, multiplied: a backlog of merges nobody verified.

ByteDigger verifies the acceptance signal itself, with checks that run as code, not as another model's opinion:

| | typical factory | ByteDigger |
|---|---|---|
| who verifies the work | the agent; you trust its report | the engine runs the tests in a subprocess it owns and checks every claim against the real git diff |
| gamed tests | a test that mocks its own unit ships; assertions bend to match reality | deterministic lints reject both, no model involved |
| security | a scan later in CI, maybe | OWASP ASVS defaults ride in the generation prompt; a changed Dockerfile or Terraform routes into a fail-closed scan |
| crash mid-build | start over, pay again | resume from the last success sentinel |
| learning | every build starts amnesiac | learnings extracted, stored, injected into the next build |
| reviewer findings | prose, unverified | every path:line:quote citation checked against disk |

`pip install -e engine_py`, run the [keyless demo](examples/verified-tdd-run/), point it at your own LLM in [20 lines](examples/library/custom_backend.py). Also ships as a Claude Code plugin (`/build`).

## The two pillars

**Security shifts left.** The spec freezes with an AC table and a file allowlist, so scope drift dies at write time. Failing tests face a hostile audit before a single line of implementation. Secure-coding defaults distilled from OWASP ASVS 5.0 ride inside the generation prompt: allowlist validation, argument-vector subprocess calls, parameterized queries, path containment. A deterministic semgrep + gitleaks gate scans what lands; a changed Dockerfile, Kubernetes manifest, or Terraform file routes the build into a fail-closed scan and adds a CIS/OWASP/SLSA devops reviewer alongside the OWASP Top 10 one. A mypy gate holds the typing line: a change that adds new type errors does not pass. Reviewers still run at the end; the design goal is that they find nothing.

**Economics run deterministic-first.** Every check lives at the cheapest layer that can produce it -- regex, AST, a diff, a byte count -- and a model gets called only when code cannot decide. When a gate fails, a cheap model drafts the repair and the gate re-runs as the authority, so the expensive model is spent once, on the build. A crashed build resumes from its event log and never repays a completed model call. The spend is not vibes: cost and token rollups per run, phase, and cycle come straight from that log.

## The pipeline

A state machine, not a conversation. Gates fail closed; nothing proceeds on the agent's say-so.

```mermaid
flowchart LR
    SPEC["spec freeze"] --> LINTS{"deterministic lints"}
    LINTS --> RED["RED: failing tests"]
    RED --> GATE{"adversarial gate"}
    GATE -- REJECT --> RED
    GATE --> GREEN["GREEN: implement"]
    GREEN --> VERIFY{"verify: disk truth, 5 identical runs"}
    VERIFY --> LEARN["learnings -> next build"]
    CRASH["crash anywhere"] -. "replay event log" .-> RESUME["resume from last sentinel"]
```

Every step appends to a frozen event log; sentinels land on success only, so a killed build resumes mid-pipeline and never repays a completed model call.

## The checks that make it real

The mechanisms doing the heavy lifting, each one shipping as code in `engine_py/`:

- **Citation verifier** (`lib/plugins/anti_hallucination/`) -- a reviewer finding must cite path, line, and quote; the verifier checks each citation against the file on disk, demotes fabricated findings to an Unverified section, and recomputes the verdict without them.
- **Stub-passability lint** -- a RED test that imports a symbol and mocks that same symbol verifies nothing; automatic reject, no model involved.
- **Disk truth** (`lib/plugins/disk_truth/`) -- cross-checks RED and GREEN claims against the real git diff and the real test-runner subprocess output, not the agent's transcript.
- **Reproducibility gate** -- the test command runs five times; identical pass/fail counts required before the engine enforces the RED-to-GREEN delta. Flaky suites don't get to vote.
- **Durable resume** -- success-only sentinels plus an append-only event log with replay; a sticky error can never replay as progress.
- **Learning loop** -- phase 7 extracts categorized learnings from each build; an injection step feeds them, plus a project constitution and security rules, into the next build's context.

## Under the hood

The pipeline sketch above is seven boxes; the engine behind it is 27 workflow modules, phase 0 research through phase 8 post-deploy -- including a spec-lite lane for small tasks, a DevOps pipeline, integrity and smoke phases, and a review fastpath for simple changes. The engine installs with zero runtime dependencies and no LLM vendor baked in.

The spec itself has a machine-readable half. Alongside the prose, an `AC-checks` yaml block maps each acceptance criterion to a mechanical check from a closed registry -- file-contains, command-exit-code and friends -- validated at spec-freeze time and executed as code. A criterion that can't live without judgment has to declare itself as one (`llm_rubric`), which keeps the escape hatch visible instead of ambient. Specs stop being documentation that drifts; they compile.

Beyond the headline lints above, the deterministic set includes spec-cite (every path:line citation in the spec verified against the real repository before freeze), net-new delta (the RED-to-GREEN improvement must be net-new passes, not reshuffled counts), token-consistency, presence-triad, re-entry AC, suite-safety, and forbidden-import -- each one a codified post-mortem. A test-integrity diff guard classifies post-RED test edits and hard-fails on assertion gaming; a scope lint flags implementation writes outside the spec's file allowlist.

A restart governor caps re-invocation (and knows a crash-start from a gate-start), and every subprocess goes through one spawn chokepoint with a mandatory timeout, so an unbounded hang is impossible by construction. An optional DBOS backend adds durable run listing and status. Every REVISE or FAIL reason lands in durable JSONL ledgers -- a reject log and a spec-defect ledger with a bounded reroute budget for specs that turn out defective.

On the learning side, phase 7 synthesizes categorized learnings from each build into a pluggable store: markdown files under `.bytedigger/learnings/` by default, and a reference SQLite shell backend with a documented schema shows how to plug in your own. Before implementation, an injection step assembles a context folder: matched learnings from whichever store you wired in, a project constitution discovered by precedence, quality-gate rules, security rules, active-work context. Lessons from build N are in the prompt for build N+1.

The engine even gates changes to itself: a commit-msg hook blocks any commit touching engine production code unless it co-stages an APPROVED audit document, and self-attested LLM verdicts get an anchor-hash and AC-parity cross-check against the on-disk files before they count.

## Quickstart

Five minutes, no API key needed:

```bash
git clone https://github.com/guy-lifshitz/bytedigger
cd bytedigger/engine_py

python3 -m venv .venv && source .venv/bin/activate
pip install -U pip   # stock macOS 3.9 ships a pre-PEP660 pip that cannot editable-install
pip install -e .

# smoke: run a workflow, replay its event log
bytedigger-engine --workflow echo --ctx-json '{"question":"hello"}' --event-log /tmp/events.jsonl
bytedigger-engine --derive-state /tmp/events.jsonl

# the verified-TDD loop end to end on a toy repo, keyless
python3 ../examples/verified-tdd-run/run_demo.py
```

If something fails, run `bytedigger doctor` for an offline self-check of your environment.

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
